"""标签绑定双写两轨 + 凭证锁 + 解析闸门验收测试。"""
import threading

import pytest

from app.services import coverage, impact, nodes, tag_bind
from app.services.errors import ServiceError


def _tag(conn, code):
    return dict(conn.execute(
        "SELECT * FROM tag_catalog WHERE tag_code=?", (code,)
    ).fetchone())


def _node(items, code):
    return next(n for n in items if n["node_code"] == code)


# --------------------------------------------------------------------------- #
# 种子：真绑健康节点 + 停用虚绑节点
# --------------------------------------------------------------------------- #
def test_seed_has_real_binding_landing_rows(db_path):
    bindings = tag_bind.list_bindings(db_path)
    bound = [b for b in bindings if b["state"] == "bound"]
    assert len(bound) >= 2
    # 凭证表锁住绑定那一刻的节点状态与标签指纹
    for b in bound:
        assert b["tag_fingerprint"].startswith("fp_")
        assert b["credential_fingerprint"].startswith("bp_")
        assert b["node_status_snapshot"] in ("在用", "停用", "登记")
    tracks = {b["track"] for b in bound}
    assert tracks == {tag_bind.ACTIVE, tag_bind.VIRTUAL}


def test_seed_virtual_binding_locks_retired_snapshot(db_path):
    items = nodes.list_nodes(db_path=db_path)
    legacy = _node(items, "N-ADS-LEGACY")
    assert legacy["status"] == "停用"
    assert legacy["virtual_bound"] is True
    assert legacy["binding_source"] == tag_bind.SOURCE_VIRTUAL
    assert legacy["node_status_snapshot"] == "停用"
    dash = _node(items, "N-ADS-DASH")
    assert dash["binding_source"] == tag_bind.SOURCE_ACTIVE


# --------------------------------------------------------------------------- #
# 真绑：整笔提交，停用节点拒绝真绑
# --------------------------------------------------------------------------- #
def test_active_commit_writes_landing_row(db_path):
    tag = tag_bind.list_tags(db_path)[0]
    target = next(n for n in nodes.list_nodes(db_path=db_path)
                  if not n["bound"] and n["status"] != "停用")
    item = tag_bind.commit_binding(tag["id"], target["id"], "值班甲", db_path=db_path)
    assert item["track"] == tag_bind.ACTIVE
    assert item["state"] == "bound"
    assert item["source"] == tag_bind.SOURCE_ACTIVE
    # 表内须见绑定落点行
    again = tag_bind.list_bindings(db_path)
    assert any(b["id"] == item["id"] and b["node_id"] == target["id"] for b in again)
    # 台账只追加留痕
    ledger = tag_bind.ledger_rows(db_path)
    assert any(e["action"] == "bind" and e["operator"] == "值班甲" for e in ledger)


def test_retired_node_rejects_active_binding(db_path):
    tag = tag_bind.list_tags(db_path)[0]
    legacy = _node(nodes.list_nodes(db_path=db_path), "N-ADS-LEGACY")
    # 先解绑虚绑腾出节点，再尝试真绑停用节点
    tag_bind.release_binding(legacy["id"], db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        tag_bind.commit_binding(tag["id"], legacy["id"], db_path=db_path)
    assert exc.value.status_code == 409
    assert "虚绑" in exc.value.message


# --------------------------------------------------------------------------- #
# 禁止并存：同一节点只许一条在绑落点
# --------------------------------------------------------------------------- #
def test_active_and_virtual_cannot_coexist(db_path):
    tags = tag_bind.list_tags(db_path)
    target = next(n for n in nodes.list_nodes(db_path=db_path)
                  if not n["bound"] and n["status"] != "停用")
    tag_bind.commit_binding(tags[0]["id"], target["id"], db_path=db_path)
    with pytest.raises(ServiceError) as exc:
        tag_bind.commit_virtual_binding(tags[1]["id"], target["id"], db_path=db_path)
    assert exc.value.status_code == 409
    assert "禁止并存" in exc.value.message
    bound = [b for b in tag_bind.list_bindings(db_path)
             if b["node_id"] == target["id"] and b["state"] == "bound"]
    assert len(bound) == 1  # 绝不写两行


# --------------------------------------------------------------------------- #
# 停用：真绑整笔转只读虚绑（双写两轨）
# --------------------------------------------------------------------------- #
def test_retire_demotes_active_to_virtual(db_path):
    # 找一个无已发布边、可停用且已真绑的节点：先造一个
    reg = nodes.register_node("N-TMP-1", "tmp_t", "DWD", db_path=db_path)
    tag = tag_bind.list_tags(db_path)[0]
    tag_bind.commit_binding(tag["id"], reg["id"], db_path=db_path)
    out = nodes.retire_node(reg["id"], db_path=db_path)
    assert out["demoted_to_virtual"] is True
    binding = next(b for b in tag_bind.list_bindings(db_path) if b["node_id"] == reg["id"])
    assert binding["track"] == tag_bind.VIRTUAL
    assert binding["state"] == "bound"
    assert binding["node_status_snapshot"] == "停用"
    # 同一落点行，没有新增第二条
    bound = [b for b in tag_bind.list_bindings(db_path)
             if b["node_id"] == reg["id"] and b["state"] == "bound"]
    assert len(bound) == 1


# --------------------------------------------------------------------------- #
# 闸门：指纹/状态不合即停解析管道
# --------------------------------------------------------------------------- #
def test_pipeline_blocked_on_virtual_root(db_path):
    legacy = _node(nodes.list_nodes(db_path=db_path), "N-ADS-LEGACY")
    gate = tag_bind.gate_for_node(legacy["id"], db_path)
    assert gate["allowed"] is False
    assert gate["source"] == tag_bind.SOURCE_VIRTUAL
    with pytest.raises(ServiceError) as exc:
        impact.start_impact(legacy["id"], db_path=db_path)
    assert exc.value.status_code == 409


def test_pipeline_runs_on_clean_active_chain(db_path):
    dwd = _node(nodes.list_nodes(db_path=db_path), "N-DWD-ORD")
    job = impact.start_impact(dwd["id"], db_path=db_path)
    assert job["status"] == "成功"
    assert job["virtual_blocked"] == 0


def test_pipeline_prunes_virtual_downstream(db_path):
    # 给退役虚绑节点挂一条已发布边，再从其上游触发，验证 BFS 阻断计数
    from app.db import get_db
    dwd = _node(nodes.list_nodes(db_path=db_path), "N-DWD-PAY")
    legacy = _node(nodes.list_nodes(db_path=db_path), "N-ADS-LEGACY")
    with get_db(db_path) as conn:
        conn.execute(
            "INSERT INTO lineage_edges(edge_code, upstream_id, downstream_id, "
            "transform_kind, status) VALUES ('E-VIRT',?,?,'直抽','已发布')",
            (dwd["id"], legacy["id"]),
        )
    job = impact.start_impact(dwd["id"], db_path=db_path)
    assert job["status"] == "已拦停"
    assert job["virtual_blocked"] >= 1
    blocked_ids = {b["node_id"] for b in job["blocked_nodes_list"]}
    assert legacy["id"] in blocked_ids
    detail = impact.job_detail(job["id"], db_path=db_path)
    hit_ids = {h["node_id"] for h in detail["hits"]}
    assert legacy["id"] not in hit_ids  # 虚绑节点不进命中


def test_fingerprint_drift_stops_pipeline(db_path):
    from app.db import get_db
    reg = nodes.register_node("N-TMP-2", "tmp_drift", "DWS", db_path=db_path)
    tag = tag_bind.list_tags(db_path)[0]
    tag_bind.commit_binding(tag["id"], reg["id"], db_path=db_path)
    # 指纹漂移：直接改标签指纹，模拟凭证与落点不一致
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE tag_catalog SET fingerprint=? WHERE id=?",
            ("fp_drifted", tag["id"]),
        )
    with pytest.raises(ServiceError) as exc:
        impact.start_impact(reg["id"], db_path=db_path)
    assert exc.value.status_code == 409
    assert "不符" in exc.value.message


# --------------------------------------------------------------------------- #
# 并发：两名值班争绑，锁保证不写两行、不盖先手
# --------------------------------------------------------------------------- #
def test_concurrent_bind_same_node_single_winner(db_path):
    tags = tag_bind.list_tags(db_path)
    target = next(n for n in nodes.list_nodes(db_path=db_path)
                  if not n["bound"] and n["status"] != "停用")
    errors: list[ServiceError] = []

    def bind(tag_id):
        try:
            tag_bind.commit_binding(tag_id, target["id"], "值班", db_path=db_path)
        except ServiceError as exc:  # 后到者必须失败回白话
            errors.append(exc)

    t1 = threading.Thread(target=bind, args=(tags[0]["id"],))
    t2 = threading.Thread(target=bind, args=(tags[1]["id"],))
    t1.start(); t2.start(); t1.join(); t2.join()

    bound = [b for b in tag_bind.list_bindings(db_path)
             if b["node_id"] == target["id"] and b["state"] == "bound"]
    assert len(bound) == 1            # 绝不写两行
    assert len(errors) == 1           # 一人被拒
    assert errors[0].status_code == 409
    assert ("抢先" in errors[0].message) or ("禁止并存" in errors[0].message)


# --------------------------------------------------------------------------- #
# 半修：只改芯片不落绑定行不算修复
# --------------------------------------------------------------------------- #
def test_half_fix_never_lands_row(db_path):
    before = len([b for b in tag_bind.list_bindings(db_path) if b["state"] == "bound"])
    out = tag_bind.reconcile_display(db_path)
    after = len([b for b in tag_bind.list_bindings(db_path) if b["state"] == "bound"])
    assert before == after
    assert out["half_fixed"] is False


# --------------------------------------------------------------------------- #
# 看板：绑定来源与虚绑数对齐 + 表内有落点才算过
# --------------------------------------------------------------------------- #
def test_board_alignment_and_virtual_count(db_path):
    board = tag_bind.board(db_path)
    assert board["bound_count"] >= 2
    assert board["virtual_count"] == tag_bind.virtual_binding_count(db_path)
    assert board["active_count"] == tag_bind.active_binding_count(db_path)
    # 种子虚绑都在停用节点（合规历史轨），整体对齐、芯片可通
    assert board["aligned"] is True
    assert board["live_virtual_count"] == 0
    assert tag_bind.panel_chip(db_path) == "可通·真绑"
    assert tag_bind.truth_flag(db_path) == "绑定已绿"
    # 覆盖进度旁挂
    progress = coverage.binding_coverage_progress(db_path)
    assert progress["live_node_count"] >= 5
    assert progress["retired_virtual_count"] >= 1
    assert progress["active_bound_count"] >= 1


def test_live_virtual_binding_breaks_alignment(db_path):
    tags = tag_bind.list_tags(db_path)
    target = next(n for n in nodes.list_nodes(db_path=db_path)
                  if not n["bound"] and n["status"] != "停用")
    tag_bind.commit_virtual_binding(tags[0]["id"], target["id"], db_path=db_path)
    board = tag_bind.board(db_path)
    assert board["live_virtual_count"] == 1
    assert board["aligned"] is False
    assert tag_bind.panel_chip(db_path) == "拦截·虚绑/漂移"
    assert tag_bind.truth_flag(db_path) == "停用仍绑"
    # 该在用虚绑节点开解析即拦停
    with pytest.raises(ServiceError):
        impact.start_impact(target["id"], db_path=db_path)


def test_commit_action_requires_real_landing(db_path):
    with pytest.raises(ServiceError) as exc:
        tag_bind.commit_action({"ref": 1}, db_path=db_path)  # 旧的只画芯片入参
    assert exc.value.status_code == 400
    assert "落点" in exc.value.message
