"""标签绑定台账：只追加留痕，读真实绑定落点，不回正主路径写库。"""
from __future__ import annotations

from app.db import get_db
from app.services import tag_bind as core
from app.services.errors import ServiceError


def append_ledger(ref_id: int, note: str, db_path: str | None = None) -> dict:
    """记一条人工台账备注（只追加），不产生/篡改任何绑定落点行。"""
    note = (note or "").strip() or core.TITLE
    with get_db(db_path, immediate=True) as conn:
        binding = None
        if ref_id:
            binding = conn.execute(
                "SELECT b.*, t.tag_code, n.node_code FROM tag_bindings b "
                "JOIN tag_catalog t ON t.id=b.tag_id "
                "JOIN lineage_nodes n ON n.id=b.node_id WHERE b.id=?",
                (ref_id,),
            ).fetchone()
        if binding:
            conn.execute(
                "INSERT INTO tag_bind_ledger(ref_id, action, track, tag_code, node_code, "
                "fingerprint, node_status, operator, note) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ref_id, "ledger_note", binding["track"], binding["tag_code"],
                    binding["node_code"], binding["tag_fingerprint"],
                    binding["node_status_snapshot"], "值班", note,
                ),
            )
            conn.execute(
                "INSERT INTO tag_bind_events(event_type, ref_id, detail) VALUES (?,?,?)",
                ("ledger_note", ref_id, note),
            )
        else:
            conn.execute(
                "INSERT INTO tag_bind_ledger(ref_id, action, note) VALUES (?,?,?)",
                (ref_id, "ledger_note", note),
            )
    board = core.board(db_path)
    return {
        "ref_id": ref_id,
        "note": note,
        "chip": core.panel_chip(db_path),
        "truth": core.truth_flag(db_path),
        "bound_count": board["bound_count"],
        "virtual_count": board["virtual_count"],
    }


def ledger_rows(db_path: str | None = None) -> list[dict]:
    return core.ledger_rows(db_path)


def board(db_path: str | None = None) -> dict:
    """看板 = 真实绑定落点 + 台账；对齐与否完全由落点行决定。"""
    dash = core.board(db_path)
    dash["ledger"] = core.ledger_rows(db_path)
    dash["panel_pack_mismatch"] = not dash["aligned"]
    return dash
