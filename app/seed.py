"""初始化样例血缘节点、边、台参数与标签绑定凭证。"""
from __future__ import annotations

from app.db import SCHEMA, get_db, init_schema
from app.services.tag_bind import (
    ACTIVE,
    BOUND,
    VIRTUAL,
    binding_fingerprint,
    tag_fingerprint,
)

SCHEMA_VERSION = "lineage-v2"


def _schema_compatible(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='lineage_nodes'"
    ).fetchone()
    if not row:
        return False
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(lineage_nodes)").fetchall()
    }
    return "node_code" in cols and "layer" in cols


def seed_if_needed(db_path: str | None = None) -> dict:
    init_schema(db_path)
    with get_db(db_path) as conn:
        ver = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if not _schema_compatible(conn) or (ver and ver["value"] != SCHEMA_VERSION):
            for table in (
                "tag_bind_events",
                "tag_bind_ledger",
                "tag_bindings",
                "tag_catalog",
                "notify_logs",
                "impact_hits",
                "impact_jobs",
                "coverage_reports",
                "lineage_edges",
                "lineage_nodes",
                "lab_settings",
                "meta",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.executescript(SCHEMA)
        elif conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone():
            return {"seeded": False, "reason": "already"}

        nodes = [
            ("N-ODS-ORD", "ods_orders", "ODS", "采集组", "在用", "订单原始"),
            ("N-ODS-PAY", "ods_payments", "ODS", "采集组", "在用", "支付原始"),
            ("N-DWD-ORD", "dwd_orders", "DWD", "仓建组", "在用", "订单明细"),
            ("N-DWD-PAY", "dwd_payments", "DWD", "仓建组", "在用", "支付明细"),
            ("N-DWS-REV", "dws_revenue", "DWS", "分析组", "在用", "收入汇总"),
            ("N-ADS-DASH", "ads_dash_rev", "ADS", "分析组", "在用", "收入看板"),
            ("N-ADS-ALERT", "ads_alert_rev", "ADS", "值班组", "在用", "收入告警"),
            # 停用退役节点：演示虚绑只入只读历史轨，不挂主链边
            ("N-ADS-LEGACY", "ads_legacy_rev", "ADS", "值班组", "停用", "退役看板"),
        ]
        ids = {}
        for code, table, layer, team, status, note in nodes:
            cur = conn.execute(
                "INSERT INTO lineage_nodes(node_code, table_name, layer, owner_team, status, note) "
                "VALUES (?,?,?,?,?,?)",
                (code, table, layer, team, status, note),
            )
            ids[code] = cur.lastrowid

        edges = [
            ("E-01", "N-ODS-ORD", "N-DWD-ORD", "清洗"),
            ("E-02", "N-ODS-PAY", "N-DWD-PAY", "清洗"),
            ("E-03", "N-DWD-ORD", "N-DWS-REV", "汇总"),
            ("E-04", "N-DWD-PAY", "N-DWS-REV", "汇总"),
            ("E-05", "N-DWS-REV", "N-ADS-DASH", "直抽"),
            ("E-06", "N-DWS-REV", "N-ADS-ALERT", "直抽"),
        ]
        for code, up, down, kind in edges:
            conn.execute(
                "INSERT INTO lineage_edges(edge_code, upstream_id, downstream_id, "
                "transform_kind, status, note) VALUES (?,?,?,?,'已发布','')",
                (code, ids[up], ids[down], kind),
            )
        # one draft edge for coverage gap demo
        conn.execute(
            "INSERT INTO lineage_edges(edge_code, upstream_id, downstream_id, "
            "transform_kind, status, note) VALUES ('E-DRAFT',?,?,'直抽','草稿','待审')",
            (ids["N-ODS-ORD"], ids["N-ADS-DASH"]),
        )
        conn.execute(
            "INSERT INTO lab_settings(key, value, label) VALUES "
            "('impact_max_hop','8','影响最大跳数'),"
            "('notify_channel','站内','默认通知渠道'),"
            "('coverage_mode','严格','覆盖对账模式')"
        )

        # ---- 标签凭证目录（指纹权威） ----
        tags = [
            ("TAG-ORD", "订单采集标签", "采集组"),
            ("TAG-REV", "收入采集标签", "采集组"),
        ]
        tag_ids = {}
        tag_fps = {}
        for code, name, _team in tags:
            fp = tag_fingerprint(code, name, 1)
            cur = conn.execute(
                "INSERT INTO tag_catalog(tag_code, tag_name, version, fingerprint, status) "
                "VALUES (?,?,1,?,'启用')",
                (code, name, fp),
            )
            tag_ids[code] = cur.lastrowid
            tag_fps[code] = fp

        # ---- 绑定落点行：锁住绑定那一刻的节点状态与标签指纹 ----
        # 健康真绑：在用看板节点，现行管道可读
        dash_node = ids["N-ADS-DASH"]
        dash_fp = tag_fps["TAG-REV"]
        conn.execute(
            "INSERT INTO tag_bindings(tag_id, node_id, track, state, "
            "tag_fingerprint, node_status_snapshot, operator) "
            "VALUES (?,?,?,?,?,?,'seed')",
            (tag_ids["TAG-REV"], dash_node, ACTIVE, BOUND, dash_fp, "在用"),
        )
        conn.execute(
            "INSERT INTO tag_bind_ledger(action, track, tag_code, node_code, fingerprint, "
            "node_status, operator, note) VALUES (?,?,?,?,?,?,?,?)",
            ("bind", ACTIVE, "TAG-REV", "N-ADS-DASH",
             binding_fingerprint(dash_fp, dash_node, "在用"), "在用", "seed", "种子真绑"),
        )
        # 虚绑：停用退役节点，只入只读历史轨，现行管道不读
        legacy_node = ids["N-ADS-LEGACY"]
        legacy_fp = tag_fps["TAG-ORD"]
        conn.execute(
            "INSERT INTO tag_bindings(tag_id, node_id, track, state, "
            "tag_fingerprint, node_status_snapshot, operator) "
            "VALUES (?,?,?,?,?,?,'seed')",
            (tag_ids["TAG-ORD"], legacy_node, VIRTUAL, BOUND, legacy_fp, "停用"),
        )
        conn.execute(
            "INSERT INTO tag_bind_ledger(action, track, tag_code, node_code, fingerprint, "
            "node_status, operator, note) VALUES (?,?,?,?,?,?,?,?)",
            ("virtual_bind", VIRTUAL, "TAG-ORD", "N-ADS-LEGACY",
             binding_fingerprint(legacy_fp, legacy_node, "停用"), "停用", "seed",
             "停用节点虚绑入只读历史轨"),
        )

        conn.execute("INSERT INTO meta(key, value) VALUES ('seeded','1')")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.execute("INSERT INTO meta(key, value) VALUES ('lab','表血缘影响台')")
        return {
            "seeded": True,
            "nodes": len(nodes),
            "edges": len(edges) + 1,
            "settings": 3,
            "tags": len(tags),
            "bindings": 2,
        }


def reset_seed(db_path: str | None = None) -> None:
    init_schema(db_path)
    with get_db(db_path) as conn:
        for table in (
            "tag_bind_events",
            "tag_bind_ledger",
            "tag_bindings",
            "tag_catalog",
            "notify_logs",
            "impact_hits",
            "impact_jobs",
            "coverage_reports",
            "lineage_edges",
            "lineage_nodes",
            "lab_settings",
            "meta",
        ):
            conn.execute(f"DELETE FROM {table}")
    seed_if_needed(db_path)
