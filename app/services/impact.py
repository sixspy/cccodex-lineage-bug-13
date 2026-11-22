"""影响分析：从根节点沿已发布边向下展开，绑定闸门不合即停。"""
from __future__ import annotations

import json

from app.db import get_db
from app.services import tag_bind as tag_bind_svc
from app.services.errors import ServiceError


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id ORDER BY j.id DESC"
        ).fetchall()
        items = [dict(r) for r in rows]
        for it in items:
            it["blocked_nodes_list"] = json.loads(it.get("blocked_nodes") or "[]")
        return items


def job_detail(job_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT j.*, n.node_code, n.table_name FROM impact_jobs j "
            "JOIN lineage_nodes n ON n.id=j.root_node_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        if not job:
            raise ServiceError("作业不存在", 404)
        hits = conn.execute(
            "SELECT h.*, n.node_code, n.table_name, n.layer FROM impact_hits h "
            "JOIN lineage_nodes n ON n.id=h.node_id WHERE h.job_id=? ORDER BY h.hop, h.id",
            (job_id,),
        ).fetchall()
        job_dict = dict(job)
        job_dict["blocked_nodes_list"] = json.loads(job_dict.get("blocked_nodes") or "[]")
        return {"job": job_dict, "hits": [dict(x) for x in hits]}


def start_impact(root_node_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        root = conn.execute(
            "SELECT * FROM lineage_nodes WHERE id=?", (root_node_id,)
        ).fetchone()
        if not root:
            raise ServiceError("根节点不存在", 404)
        # 硬闸门：标签指纹与节点状态不合（虚绑/停用/漂移）即停解析管道
        root_gate = tag_bind_svc.evaluate_gate(conn, root_node_id)
        if not root_gate["allowed"]:
            raise ServiceError(root_gate["reason"], 409)
        if root["status"] == "停用":
            raise ServiceError("停用节点不可开影响分析", 409)
        running = conn.execute(
            "SELECT id FROM impact_jobs WHERE root_node_id=? AND status IN ('待执行','执行中')",
            (root_node_id,),
        ).fetchone()
        if running:
            raise ServiceError("该根节点已有在途影响作业", 409)
        seq = conn.execute("SELECT COUNT(*) AS c FROM impact_jobs").fetchone()["c"] + 1
        job_code = f"IMP-{seq:04d}"
        cur = conn.execute(
            "INSERT INTO impact_jobs(job_code, root_node_id, status, message, started_at) "
            "VALUES (?,?,'执行中','展开中',datetime('now'))",
            (job_code, root_node_id),
        )
        job_id = cur.lastrowid

        # BFS downstream via published edges；虚绑/漂移节点只阻断不展开
        seen = {root_node_id: 0}
        queue = [root_node_id]
        blocked: list[dict] = []
        blocked_ids: set[int] = set()
        while queue:
            cur_id = queue.pop(0)
            hop = seen[cur_id]
            conn.execute(
                "INSERT OR IGNORE INTO impact_hits(job_id, node_id, hop) VALUES (?,?,?)",
                (job_id, cur_id, hop),
            )
            kids = conn.execute(
                "SELECT downstream_id FROM lineage_edges "
                "WHERE upstream_id=? AND status='已发布'",
                (cur_id,),
            ).fetchall()
            for kid in kids:
                kid_id = int(kid["downstream_id"])
                if kid_id in seen or kid_id in blocked_ids:
                    continue
                gate = tag_bind_svc.evaluate_gate(conn, kid_id)
                if not gate["allowed"]:
                    blocked_ids.add(kid_id)
                    node = conn.execute(
                        "SELECT node_code FROM lineage_nodes WHERE id=?", (kid_id,)
                    ).fetchone()
                    blocked.append({
                        "node_id": kid_id,
                        "node_code": node["node_code"] if node else "",
                        "reason": gate["reason"],
                        "source": gate["source"],
                        "drift": gate["drift"],
                    })
                    continue
                seen[kid_id] = hop + 1
                queue.append(kid_id)

        affected = len(seen)
        blocked_payload = json.dumps(blocked, ensure_ascii=False)
        if blocked:
            status = "已拦停"
            message = (
                f"有效命中 {affected} 个节点；虚绑/漂移拦停 {len(blocked)} 个，"
                f"解析管道已按绑定闸门收口"
            )
        else:
            status = "成功"
            message = f"影响到 {affected} 个节点"
        conn.execute(
            "UPDATE impact_jobs SET status=?, affected_count=?, virtual_blocked=?, "
            "blocked_nodes=?, message=?, finished_at=datetime('now') WHERE id=?",
            (status, affected, len(blocked), blocked_payload, message, job_id),
        )
        row = dict(conn.execute(
            "SELECT * FROM impact_jobs WHERE id=?", (job_id,)
        ).fetchone())
        row["blocked_nodes_list"] = blocked
        return row
