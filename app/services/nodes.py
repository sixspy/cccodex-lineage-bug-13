"""血缘节点：登记、列表、停用；节点卡片亮真实绑定来源。"""
from __future__ import annotations

from app.db import get_db
from app.services import tag_bind as tag_bind_svc
from app.services.errors import ServiceError


def list_nodes(layer: str | None = None, db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        if layer:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes WHERE layer=? ORDER BY id",
                (layer,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lineage_nodes ORDER BY id"
            ).fetchall()
        items = [dict(r) for r in rows]
        for it in items:
            up = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE downstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            down = conn.execute(
                "SELECT COUNT(*) AS c FROM lineage_edges "
                "WHERE upstream_id=? AND status='已发布'",
                (it["id"],),
            ).fetchone()["c"]
            it["upstream_count"] = int(up)
            it["downstream_count"] = int(down)
            binding = conn.execute(
                "SELECT b.*, t.tag_code FROM tag_bindings b "
                "JOIN tag_catalog t ON t.id=b.tag_id "
                "WHERE b.node_id=? AND b.state='bound'",
                (it["id"],),
            ).fetchone()
            if binding:
                it["bound"] = True
                it["binding_id"] = int(binding["id"])
                it["tag_code"] = binding["tag_code"]
                it["binding_track"] = binding["track"]
                it["binding_source"] = (
                    tag_bind_svc.SOURCE_VIRTUAL
                    if binding["track"] == tag_bind_svc.VIRTUAL
                    else tag_bind_svc.SOURCE_ACTIVE
                )
                it["virtual_bound"] = binding["track"] == tag_bind_svc.VIRTUAL
                it["binding_fingerprint"] = binding["tag_fingerprint"]
                it["node_status_snapshot"] = binding["node_status_snapshot"]
                it["binding_drift"] = (
                    binding["node_status_snapshot"] != it["status"]
                )
            else:
                it["bound"] = False
                it["binding_source"] = tag_bind_svc.SOURCE_NONE
                it["virtual_bound"] = False
                it["binding_drift"] = False
        return items


def get_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        return dict(row)


def register_node(
    node_code: str,
    table_name: str,
    layer: str,
    owner_team: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    node_code = (node_code or "").strip()
    table_name = (table_name or "").strip()
    layer = (layer or "").strip().upper()
    if not node_code or not table_name:
        raise ServiceError("节点编号与表名必填")
    if layer not in ("ODS", "DWD", "DWS", "ADS"):
        raise ServiceError("层级只许 ODS/DWD/DWS/ADS")
    with get_db(db_path) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO lineage_nodes(node_code, table_name, layer, owner_team, status, note) "
                "VALUES (?,?,?,?,'登记',?)",
                (node_code, table_name, layer, owner_team or "", note or ""),
            )
        except Exception as exc:
            raise ServiceError(f"登记失败：编号可能重复（{exc})") from exc
        return dict(
            conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )


def activate_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        if row["status"] == "停用":
            raise ServiceError("已停用节点不可直接启用", 409)
        conn.execute(
            "UPDATE lineage_nodes SET status='在用' WHERE id=?", (node_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
            ).fetchone()
        )


def retire_node(node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone()
        if not row:
            raise ServiceError("节点不存在", 404)
        live = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_edges "
            "WHERE (upstream_id=? OR downstream_id=?) AND status='已发布'",
            (node_id, node_id),
        ).fetchone()["c"]
        if int(live) > 0:
            raise ServiceError("仍有已发布边，不可停用", 409)
        conn.execute(
            "UPDATE lineage_nodes SET status='停用' WHERE id=?", (node_id,)
        )
    # 双写两轨：停用后真绑整笔转只读虚绑（独立整笔事务，失败回白话）
    demoted = tag_bind_svc.demote_to_virtual(
        node_id, operator="system", note="节点停用，真绑转只读虚绑", db_path=db_path
    )
    with get_db(db_path) as conn:
        node = dict(conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (node_id,)
        ).fetchone())
        node["demoted_to_virtual"] = bool(demoted)
        return node


def open_isolate_window(node_ids: list[int], db_path: str | None = None) -> dict:
    """隔离窗口：只做只读核对并返回真实绑定/闸门状态，绝不伪造绑定落点。

    原实现只刷芯片不写落点（半修），这里改为如实盘点；要落绑定须走
    tag_bind.commit_binding / commit_virtual_binding。
    """
    node_ids = [int(x) for x in (node_ids or [])]
    blocked: list[dict] = []
    for nid in node_ids:
        gate = tag_bind_svc.gate_for_node(nid, db_path)
        if not gate["allowed"]:
            blocked.append({"node_id": nid, "reason": gate["reason"],
                            "source": gate["source"]})
    return {
        "isolated": len(node_ids),
        "chip": tag_bind_svc.panel_chip(db_path),
        "pack_count": tag_bind_svc.board(db_path)["bound_count"],
        "blocked": blocked,
        "message": "隔离窗口只读取证，未写任何绑定落点"
        if not blocked else f"{len(blocked)} 个节点被绑定闸门拦停",
    }
