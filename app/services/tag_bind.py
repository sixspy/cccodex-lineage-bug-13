"""采集标签绑定：凭证表锁住绑定那一刻的节点状态与标签指纹。

工程落点（双写两轨）：
- tag_catalog 是标签指纹的权威来源；
- tag_bindings 是唯一绑定落点行，一条在绑落点要么真绑(active)要么虚绑(virtual)，禁止并存；
- 现行管道只读真绑，虚绑推进只读历史轨；
- 绑定由服务在单事务（BEGIN IMMEDIATE + 唯一索引）内整笔提交，并发争绑不写两行、不盖先手；
- 标签指纹与节点现状不合即停解析管道。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.db import get_db
from app.services.errors import ServiceError

TITLE = "采集标签绑定"
ACTIVE = "active"
VIRTUAL = "virtual"
BOUND = "bound"
RELEASED = "released"

# 绑定来源（对外白话）
SOURCE_ACTIVE = "真绑·现行"
SOURCE_VIRTUAL = "虚绑·只读历史"
SOURCE_NONE = "未绑定"


# --------------------------------------------------------------------------- #
# 指纹：标签内容指纹 + 绑定行凭证指纹
# --------------------------------------------------------------------------- #
def tag_fingerprint(tag_code: str, tag_name: str, version: int) -> str:
    raw = f"{(tag_code or '').strip()}|{(tag_name or '').strip()}|{int(version)}"
    return "fp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def binding_fingerprint(tag_fp: str, node_id: int, node_status: str) -> str:
    raw = f"{tag_fp}|node={int(node_id)}|status={node_status}"
    return "bp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# 内部读取
# --------------------------------------------------------------------------- #
def _get_tag(conn, tag_id: int):
    row = conn.execute("SELECT * FROM tag_catalog WHERE id=?", (tag_id,)).fetchone()
    if not row:
        raise ServiceError("标签不存在", 404)
    return row


def _get_node(conn, node_id: int):
    row = conn.execute("SELECT * FROM lineage_nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        raise ServiceError("节点不存在", 404)
    return row


def _bound_binding(conn, node_id: int):
    return conn.execute(
        "SELECT * FROM tag_bindings WHERE node_id=? AND state=?",
        (node_id, BOUND),
    ).fetchone()


def _append_ledger(
    conn,
    action: str,
    track: str,
    tag_code: str,
    node_code: str,
    fingerprint: str,
    node_status: str,
    operator: str,
    note: str,
    ref_id: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO tag_bind_ledger(ref_id, action, track, tag_code, node_code, "
        "fingerprint, node_status, operator, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (ref_id, action, track, tag_code, node_code, fingerprint, node_status,
         operator, note),
    )
    conn.execute(
        "INSERT INTO tag_bind_events(event_type, ref_id, detail) VALUES (?,?,?)",
        (action, ref_id, f"{tag_code} -> {node_code} [{track}/{node_status}] {note}".strip()),
    )


# --------------------------------------------------------------------------- #
# 标签目录
# --------------------------------------------------------------------------- #
def list_tags(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_catalog ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def create_tag(tag_code: str, tag_name: str, db_path: str | None = None) -> dict:
    tag_code = (tag_code or "").strip()
    tag_name = (tag_name or "").strip()
    if not tag_code or not tag_name:
        raise ServiceError("标签编号与名称必填")
    fp = tag_fingerprint(tag_code, tag_name, 1)
    with get_db(db_path, immediate=True) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO tag_catalog(tag_code, tag_name, version, fingerprint, status) "
                "VALUES (?,?,?,1,'启用')",
                (tag_code, tag_name, fp),
            )
        except sqlite3.IntegrityError as exc:
            raise ServiceError(f"标签编号已存在：{tag_code}", 409) from exc
        _append_ledger(conn, "tag_create", "", tag_code, "", fp, "", "", "新建标签")
        return dict(conn.execute(
            "SELECT * FROM tag_catalog WHERE id=?", (cur.lastrowid,)
        ).fetchone())


# --------------------------------------------------------------------------- #
# 绑定：服务整笔提交，单事务加锁 + 唯一索引
# --------------------------------------------------------------------------- #
def commit_binding(
    tag_id: int,
    node_id: int,
    operator: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """真绑：把标签绑定到在用/登记节点，整笔提交。

    节点停用只许虚绑；节点已有在绑落点（真绑或虚绑）一律拒绝，禁止并存/盖写先手。
    """
    operator = (operator or "").strip() or "值班"
    note = (note or "").strip()
    with get_db(db_path, immediate=True) as conn:
        tag = _get_tag(conn, tag_id)
        if tag["status"] != "启用":
            raise ServiceError("标签已停用，不可发起真绑", 409)
        node = _get_node(conn, node_id)
        if node["status"] == "停用":
            raise ServiceError(
                "停用节点不可真绑：请走虚绑（只读历史轨），现行管道不读此绑", 409
            )
        existing = _bound_binding(conn, node_id)
        if existing:
            label = SOURCE_VIRTUAL if existing["track"] == VIRTUAL else SOURCE_ACTIVE
            raise ServiceError(
                f"节点 {node['node_code']} 已存在{label}落点，禁止并存或覆盖先手绑定", 409
            )
        tag_fp = tag["fingerprint"]
        bp = binding_fingerprint(tag_fp, node_id, node["status"])
        try:
            cur = conn.execute(
                "INSERT INTO tag_bindings(tag_id, node_id, track, state, "
                "tag_fingerprint, node_status_snapshot, operator) "
                "VALUES (?,?,?,?,?,?,?)",
                (tag_id, node_id, ACTIVE, BOUND, tag_fp, node["status"], operator),
            )
        except sqlite3.IntegrityError as exc:
            # 并发争绑：唯一索引兜底，后到者失败回白话，不写两行
            raise ServiceError("该标签/节点已被他人抢先绑定，请刷新后重试", 409) from exc
        _append_ledger(
            conn, "bind", ACTIVE, tag["tag_code"], node["node_code"], bp,
            node["status"], operator, note or "真绑落台",
        )
        row = conn.execute(
            "SELECT * FROM tag_bindings WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return _decorate(conn, dict(row))


def commit_virtual_binding(
    tag_id: int,
    node_id: int,
    operator: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """虚绑：绑定（或对停用节点补登记）到只读历史轨，不进入现行管道。"""
    operator = (operator or "").strip() or "值班"
    note = (note or "").strip()
    with get_db(db_path, immediate=True) as conn:
        tag = _get_tag(conn, tag_id)
        node = _get_node(conn, node_id)
        existing = _bound_binding(conn, node_id)
        if existing:
            label = SOURCE_VIRTUAL if existing["track"] == VIRTUAL else SOURCE_ACTIVE
            raise ServiceError(
                f"节点 {node['node_code']} 已存在{label}落点，禁止并存或覆盖先手绑定", 409
            )
        tag_fp = tag["fingerprint"]
        bp = binding_fingerprint(tag_fp, node_id, node["status"])
        try:
            cur = conn.execute(
                "INSERT INTO tag_bindings(tag_id, node_id, track, state, "
                "tag_fingerprint, node_status_snapshot, operator) "
                "VALUES (?,?,?,?,?,?,?)",
                (tag_id, node_id, VIRTUAL, BOUND, tag_fp, node["status"], operator),
            )
        except sqlite3.IntegrityError as exc:
            raise ServiceError("该标签/节点已被他人抢先绑定，请刷新后重试", 409) from exc
        _append_ledger(
            conn, "virtual_bind", VIRTUAL, tag["tag_code"], node["node_code"], bp,
            node["status"], operator, note or "虚绑入只读历史轨",
        )
        row = conn.execute(
            "SELECT * FROM tag_bindings WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return _decorate(conn, dict(row))


def release_binding(
    node_id: int,
    operator: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    """解绑：把在绑落点置为 released（保留历史），节点回到未绑定。"""
    operator = (operator or "").strip() or "值班"
    with get_db(db_path, immediate=True) as conn:
        node = _get_node(conn, node_id)
        binding = _bound_binding(conn, node_id)
        if not binding:
            raise ServiceError("节点无在绑落点，无需解绑", 409)
        tag = _get_tag(conn, binding["tag_id"])
        conn.execute(
            "UPDATE tag_bindings SET state=?, lock_version=lock_version+1, "
            "updated_at=datetime('now') WHERE id=?",
            (RELEASED, binding["id"]),
        )
        _append_ledger(
            conn, "release", binding["track"], tag["tag_code"], node["node_code"],
            binding["tag_fingerprint"], node["status"], operator, note or "解绑",
            ref_id=binding["id"],
        )
        return {"node_id": node_id, "node_code": node["node_code"], "state": RELEASED}


def demote_to_virtual(
    node_id: int,
    operator: str = "system",
    note: str = "",
    db_path: str | None = None,
) -> dict | None:
    """节点停用时：把真绑整笔转成只读虚绑（历史轨），不与真绑并存。"""
    with get_db(db_path, immediate=True) as conn:
        node = _get_node(conn, node_id)
        binding = _bound_binding(conn, node_id)
        if not binding:
            return None
        if binding["track"] == VIRTUAL:
            return _decorate(conn, dict(binding))
        tag = _get_tag(conn, binding["tag_id"])
        conn.execute(
            "UPDATE tag_bindings SET track=?, node_status_snapshot=?, "
            "lock_version=lock_version+1, updated_at=datetime('now') WHERE id=?",
            (VIRTUAL, node["status"], binding["id"]),
        )
        bp = binding_fingerprint(binding["tag_fingerprint"], node_id, node["status"])
        _append_ledger(
            conn, "demote_virtual", VIRTUAL, tag["tag_code"], node["node_code"],
            bp, node["status"], operator, note or "节点停用，真绑转只读虚绑",
            ref_id=binding["id"],
        )
        row = conn.execute(
            "SELECT * FROM tag_bindings WHERE id=?", (binding["id"],)
        ).fetchone()
        return _decorate(conn, dict(row))


# --------------------------------------------------------------------------- #
# 解析管道闸门：指纹与节点状态不合即停
# --------------------------------------------------------------------------- #
def evaluate_gate(conn, node_id: int) -> dict:
    """对单个节点做绑定闸门评估（复用同一连接，供 BFS 调用）。"""
    node = conn.execute(
        "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
    ).fetchone()
    if not node:
        return {"allowed": False, "reason": "节点不存在", "source": SOURCE_NONE,
                "binding": None, "drift": False}
    binding = _bound_binding(conn, node_id)
    if not binding:
        return {"allowed": True, "reason": "", "source": SOURCE_NONE,
                "binding": None, "drift": False}
    tag = conn.execute(
        "SELECT * FROM tag_catalog WHERE id=?", (binding["tag_id"],)
    ).fetchone()
    drift = bool(tag is None or tag["fingerprint"] != binding["tag_fingerprint"]
                 or binding["node_status_snapshot"] != node["status"])
    if binding["track"] == VIRTUAL:
        return {
            "allowed": False,
            "reason": f"节点 {node['node_code']} 为虚绑（只读历史轨），现行管道不读，已阻断",
            "source": SOURCE_VIRTUAL, "binding": dict(binding), "drift": drift,
        }
    if node["status"] == "停用":
        return {
            "allowed": False,
            "reason": f"节点 {node['node_code']} 已停用，真绑失效，停止解析",
            "source": SOURCE_ACTIVE, "binding": dict(binding), "drift": True,
        }
    if drift:
        return {
            "allowed": False,
            "reason": f"节点 {node['node_code']} 标签指纹/节点状态与绑定落点不符，停止解析",
            "source": SOURCE_ACTIVE, "binding": dict(binding), "drift": True,
        }
    return {"allowed": True, "reason": "", "source": SOURCE_ACTIVE,
            "binding": dict(binding), "drift": False}


def gate_for_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        return evaluate_gate(conn, node_id)


def assert_pipeline_allowed(node_id: int, db_path: str | None = None) -> None:
    """根节点开解析前的硬闸门：不合即抛白话错误，停解析管道。"""
    gate = gate_for_node(node_id, db_path)
    if not gate["allowed"]:
        raise ServiceError(gate["reason"], 409)


# --------------------------------------------------------------------------- #
# 视图模型
# --------------------------------------------------------------------------- #
def _decorate(conn, binding: dict) -> dict:
    tag = conn.execute(
        "SELECT tag_code, tag_name, fingerprint FROM tag_catalog WHERE id=?",
        (binding["tag_id"],),
    ).fetchone()
    node = conn.execute(
        "SELECT node_code, table_name, status FROM lineage_nodes WHERE id=?",
        (binding["node_id"],),
    ).fetchone()
    current_fp = tag["fingerprint"] if tag else ""
    drift = (
        current_fp != binding["tag_fingerprint"]
        or (node and node["status"] != binding["node_status_snapshot"])
    )
    source = SOURCE_VIRTUAL if binding["track"] == VIRTUAL else SOURCE_ACTIVE
    return {
        **binding,
        "tag_code": tag["tag_code"] if tag else "",
        "tag_name": tag["tag_name"] if tag else "",
        "node_code": node["node_code"] if node else "",
        "table_name": node["table_name"] if node else "",
        "node_status": node["status"] if node else "",
        "source": source,
        "drift": bool(drift),
        "credential_fingerprint": binding_fingerprint(
            binding["tag_fingerprint"], binding["node_id"],
            binding["node_status_snapshot"],
        ),
    }


def list_bindings(db_path: str | None = None) -> list[dict]:
    """绑定落点行（含历史 released），供台账与验收核对。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_bindings ORDER BY id DESC"
        ).fetchall()
        return [_decorate(conn, dict(r)) for r in rows]


def binding_map(db_path: str | None = None) -> dict[int, dict]:
    """node_id -> 在绑落点视图（无在绑则缺键）。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_bindings WHERE state=?", (BOUND,)
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            view = _decorate(conn, dict(r))
            out[int(r["node_id"])] = view
        return out


def ledger_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_bind_ledger ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def event_rows(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_bind_events ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def virtual_binding_count(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) AS c FROM tag_bindings "
            "WHERE track=? AND state=?", (VIRTUAL, BOUND)
        ).fetchone()["c"])


def active_binding_count(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) AS c FROM tag_bindings "
            "WHERE track=? AND state=?", (ACTIVE, BOUND)
        ).fetchone()["c"])


def board(db_path: str | None = None) -> dict:
    """绑定看板：来源、虚绑计数、对齐情况，全部读真实落点行。"""
    with get_db(db_path) as conn:
        bindings = [_decorate(conn, dict(r))
                    for r in conn.execute("SELECT * FROM tag_bindings ORDER BY id DESC").fetchall()]
        bound = [b for b in bindings if b["state"] == BOUND]
        virtual = [b for b in bound if b["track"] == VIRTUAL]
        active = [b for b in bound if b["track"] == ACTIVE]
        drift = [b for b in bound if b["drift"]]
        # 虚绑落在「在用」节点上才是违规；停用节点上的历史虚绑是合规终态
        live_virtual = [b for b in virtual if b["node_status"] != "停用"]
        retired_virtual = [b for b in virtual if b["node_status"] == "停用"]
        ledger = [dict(r) for r in conn.execute(
            "SELECT * FROM tag_bind_ledger ORDER BY id DESC LIMIT 20").fetchall()]
        # 绑定来源与虚绑数对齐 + 表内有绑定落点，才算过
        aligned = bool(bound) and not drift and not live_virtual
        return {
            "title": TITLE,
            "rows": bindings,
            "tags": [dict(r) for r in conn.execute(
                "SELECT * FROM tag_catalog ORDER BY id").fetchall()],
            "ledger": ledger,
            "bound_count": len(bound),
            "active_count": len(active),
            "virtual_count": len(virtual),
            "live_virtual_count": len(live_virtual),
            "retired_virtual_count": len(retired_virtual),
            "drift_count": len(drift),
            "aligned": aligned,
            "source_active": SOURCE_ACTIVE,
            "source_virtual": SOURCE_VIRTUAL,
            "source_none": SOURCE_NONE,
        }


# --------------------------------------------------------------------------- #
# 向后兼容（旧调用方平滑收敛到真实落点，不再碰 meta 伪造）
# --------------------------------------------------------------------------- #
def panel_chip(db_path: str | None = None) -> str:
    """现行管道闸门状态（替代旧 meta 芯片）。

    停用节点上的合规历史虚绑不影响「可通」；在用虚绑/漂移才拦截。
    """
    b = board(db_path)
    return "可通·真绑" if b["aligned"] else "拦截·虚绑/漂移"


def truth_flag(db_path: str | None = None) -> str:
    return "绑定已绿" if board(db_path)["aligned"] else "停用仍绑"


def drift_count(db_path: str | None = None) -> int:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tag_bindings WHERE state=?", (BOUND,)
        ).fetchall()
        n = 0
        for r in rows:
            node = conn.execute(
                "SELECT status FROM lineage_nodes WHERE id=?", (r["node_id"],)
            ).fetchone()
            tag = conn.execute(
                "SELECT fingerprint FROM tag_catalog WHERE id=?", (r["tag_id"],)
            ).fetchone()
            if not node or not tag or tag["fingerprint"] != r["tag_fingerprint"] \
                    or node["status"] != r["node_status_snapshot"]:
                n += 1
        return n


def pack_rows(db_path: str | None = None) -> list[dict]:
    """旧名保留：现在返回真实绑定落点行（不再有 decoy）。"""
    return list_bindings(db_path)


def snapshot(db_path: str | None = None) -> dict:
    b = board(db_path)
    return {
        "title": TITLE,
        "panel_chip": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "pack_count": b["bound_count"],
        "aligned": b["aligned"],
        "virtual_count": b["virtual_count"],
        "active_count": b["active_count"],
        "gate_bypass": False,
    }


def gate_allows_pipeline(node_id: int, db_path: str | None = None) -> bool:
    return gate_for_node(node_id, db_path)["allowed"]


def require_pack_or_raise(db_path: str | None = None) -> None:
    if len([b for b in list_bindings(db_path) if b["state"] == BOUND]) == 0:
        raise ServiceError(f"{TITLE}库内无落点，禁止放行", 409)


def dashboard(db_path: str | None = None) -> dict:
    b = board(db_path)
    return {
        "title": TITLE,
        "chip": panel_chip(db_path),
        "truth": truth_flag(db_path),
        "pack_count": b["bound_count"],
        "durable_count": b["bound_count"],
        "rows": b["rows"],
        "virtual_count": b["virtual_count"],
        "active_count": b["active_count"],
        "panel_pack_mismatch": not b["aligned"],
        "ok_chip": "绑定已绿",
        "bad_chip": "停用仍绑",
        "label": "标签绑定",
    }


def commit_action(payload: dict | None = None, db_path: str | None = None) -> dict:
    """旧 HTTP 入口收敛：按 payload 走真实整笔提交，绝不只画芯片。"""
    payload = payload or {}
    tag_id = int(payload.get("tag_id") or 0)
    node_id = int(payload.get("node_id") or 0)
    operator = str(payload.get("operator") or "")
    note = str(payload.get("note") or "")
    track = str(payload.get("track") or ACTIVE)
    if not tag_id or not node_id:
        raise ServiceError("tag_id 与 node_id 必填，且须落绑定落点行", 400)
    if track == VIRTUAL:
        item = commit_virtual_binding(tag_id, node_id, operator, note, db_path)
    else:
        item = commit_binding(tag_id, node_id, operator, note, db_path)
    return {
        "ok": True,
        "item": item,
        "chip": panel_chip(db_path),
        "title": TITLE,
        "message": f"{item['source']}已落绑定落点行 #{item['id']}",
        "pack_count": len(pack_rows(db_path)),
    }


def reconcile_display(db_path: str | None = None) -> dict:
    """半修入口：只重算展示，绝不伪造落点；返回真实对齐状态。"""
    snap = snapshot(db_path)
    snap["half_fixed"] = False
    snap["message"] = "展示已按真实落点重算；虚绑须解绑或转真绑，不能只改芯片"
    return snap


# 兼容旧隔离窗口调用
def record_event(note: str, db_path: str | None = None) -> dict:
    return dashboard(db_path)
