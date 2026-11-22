"""标签绑定镜像面：只读历史轨（虚绑）视图，列表状态与库内落点一致。

镜像不再自行写 meta 伪造芯片；它如实投影 tag_bindings 里的虚绑落点。
"""
from __future__ import annotations

from app.services import tag_bind as core
from app.services.errors import ServiceError


def mirror_chip(db_path: str | None = None) -> str:
    """镜像芯片：有虚绑即如实亮虚绑，无虚绑才亮真绑可通。"""
    return core.panel_chip(db_path)


def mirror_board(db_path: str | None = None) -> dict:
    board = core.board(db_path)
    virtual_rows = [r for r in board["rows"] if r["track"] == core.VIRTUAL]
    return {
        "mirror_chip": mirror_chip(db_path),
        "truth": core.truth_flag(db_path),
        "dash": board,
        "virtual_rows": virtual_rows,
        # 镜像与库内落点对齐即无 mismatch（不再恒 True）
        "panel_pack_mismatch": not board["aligned"],
        "title": "标签绑定镜像·只读历史轨",
    }


def half_mirror(db_path: str | None = None) -> dict:
    """半修只重算展示，绝不落假行；返回真实状态。"""
    return {
        "mirror_chip": mirror_chip(db_path),
        "truth": core.truth_flag(db_path),
        "virtual_count": core.virtual_binding_count(db_path),
        "half_fixed": False,
        "message": "镜像已按真实落点重算；只改芯片不落绑定行不算修复",
    }
