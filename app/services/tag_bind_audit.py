"""标签绑定审计：读真实绑定事件与只读历史轨，不再只记展示事件。"""
from __future__ import annotations

from app.db import get_db
from app.services.tag_bind_mirror import half_mirror, mirror_board


def audit_list(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, event_type, ref_id, detail, created_at "
            "FROM tag_bind_events ORDER BY id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def audit_summary(db_path: str | None = None) -> dict:
    board = mirror_board(db_path)
    with get_db(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM tag_bind_events").fetchone()["c"]
    return {
        "title": "采集标签绑定审计",
        "event_count": int(total),
        "events": audit_list(db_path),
        "board": board,
        "half": half_mirror(db_path),
    }
