"""覆盖对账：节点与已发布边的孤儿/未覆盖统计，旁挂标签绑定覆盖进度。"""
from __future__ import annotations

from app.db import get_db
from app.services import tag_bind as tag_bind_svc
from app.services.errors import ServiceError


def list_reports(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM coverage_reports ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def binding_coverage_progress(db_path: str | None = None) -> dict:
    """标签绑定覆盖进度：在用节点中真绑/虚绑/未绑占比，虚绑单独计数。"""
    with get_db(db_path) as conn:
        live_nodes = conn.execute(
            "SELECT id FROM lineage_nodes WHERE status != '停用'"
        ).fetchall()
        live_ids = [int(n["id"]) for n in live_nodes]
        bound_rows = conn.execute(
            "SELECT node_id, track FROM tag_bindings WHERE state='bound'"
        ).fetchall()
        track_by_node = {int(r["node_id"]): r["track"] for r in bound_rows}
        active = [nid for nid in live_ids if track_by_node.get(nid) == tag_bind_svc.ACTIVE]
        virtual = [nid for nid in live_ids if track_by_node.get(nid) == tag_bind_svc.VIRTUAL]
        unbound = [nid for nid in live_ids if nid not in track_by_node]
        total = len(live_ids)
        pct = round(100 * len(active) / total, 1) if total else 0.0
        # 停用节点上的虚绑（只读历史轨）单独披露
        retired_virtual = int(conn.execute(
            "SELECT COUNT(*) AS c FROM tag_bindings b JOIN lineage_nodes n "
            "ON n.id=b.node_id WHERE b.state='bound' AND b.track=? AND n.status='停用'",
            (tag_bind_svc.VIRTUAL,),
        ).fetchone()["c"])
        return {
            "live_node_count": total,
            "active_bound_count": len(active),
            "virtual_bound_count": len(virtual),
            "retired_virtual_count": retired_virtual,
            "unbound_count": len(unbound),
            "coverage_pct": pct,
            "unbound_node_ids": unbound,
        }


def run_coverage(db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        nodes = conn.execute(
            "SELECT id, status FROM lineage_nodes"
        ).fetchall()
        edges = conn.execute(
            "SELECT id, upstream_id, downstream_id, status FROM lineage_edges"
        ).fetchall()
        live_nodes = [n for n in nodes if n["status"] != "停用"]
        published = [e for e in edges if e["status"] == "已发布"]
        linked = set()
        for e in published:
            linked.add(int(e["upstream_id"]))
            linked.add(int(e["downstream_id"]))
        orphan = sum(1 for n in live_nodes if int(n["id"]) not in linked)
        # draft edges that share endpoints with published = uncovered drafts
        uncovered = sum(1 for e in edges if e["status"] == "草稿")
        binding = binding_coverage_progress(db_path)
        summary = (
            f"在用节点 {len(live_nodes)}，已发布边 {len(published)}，"
            f"孤儿节点 {orphan}，草稿边 {uncovered}"
        )
        if orphan:
            summary += "；存在未挂边节点须人工核对"
        summary += (
            f"；绑定覆盖 {binding['active_bound_count']}/{binding['live_node_count']}"
            f"（{binding['coverage_pct']}%），虚绑 {binding['virtual_bound_count']}，"
            f"停用虚绑 {binding['retired_virtual_count']}，未绑 {binding['unbound_count']}"
        )
        cur = conn.execute(
            "INSERT INTO coverage_reports(summary, node_count, edge_count, "
            "orphan_count, uncovered_edges) VALUES (?,?,?,?,?)",
            (
                summary,
                len(live_nodes),
                len(published),
                orphan,
                uncovered,
            ),
        )
        row = dict(
            conn.execute(
                "SELECT * FROM coverage_reports WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )
        row["binding"] = binding
        return row


def latest_chip(db_path: str | None = None) -> str:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT orphan_count FROM coverage_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "未对账"
        return "有缺口" if int(row["orphan_count"]) > 0 else "已齐套"
