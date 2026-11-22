import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS lineage_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  node_code TEXT NOT NULL UNIQUE,
  table_name TEXT NOT NULL,
  layer TEXT NOT NULL CHECK (layer IN ('ODS','DWD','DWS','ADS')),
  owner_team TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('登记','在用','停用')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  edge_code TEXT NOT NULL UNIQUE,
  upstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  downstream_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  transform_kind TEXT NOT NULL DEFAULT '直抽',
  status TEXT NOT NULL CHECK (status IN ('草稿','已发布','已作废')),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(upstream_id, downstream_id)
);

CREATE TABLE IF NOT EXISTS impact_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_code TEXT NOT NULL UNIQUE,
  root_node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  status TEXT NOT NULL CHECK (status IN ('待执行','执行中','成功','失败','已拦停')),
  affected_count INTEGER NOT NULL DEFAULT 0,
  virtual_blocked INTEGER NOT NULL DEFAULT 0,
  blocked_nodes TEXT NOT NULL DEFAULT '[]',
  message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS impact_hits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  hop INTEGER NOT NULL DEFAULT 0,
  UNIQUE(job_id, node_id)
);

CREATE TABLE IF NOT EXISTS notify_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES impact_jobs(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  channel TEXT NOT NULL DEFAULT '站内',
  status TEXT NOT NULL CHECK (status IN ('待发','已发','失败')),
  body TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS coverage_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  summary TEXT NOT NULL,
  node_count INTEGER NOT NULL,
  edge_count INTEGER NOT NULL,
  orphan_count INTEGER NOT NULL,
  uncovered_edges INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lab_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  label TEXT NOT NULL
);

-- 标签目录：标签指纹的权威来源
CREATE TABLE IF NOT EXISTS tag_catalog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag_code TEXT NOT NULL UNIQUE,
  tag_name TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('启用','停用')) DEFAULT '启用',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 凭证表 / 绑定落点行：锁住绑定那一刻的节点状态与标签指纹
-- track=active 真绑（现行管道只读）/ virtual 虚绑（只读历史轨）
-- 一个节点同一时刻只许一条 state='bound' 落点，真绑虚绑禁止并存
CREATE TABLE IF NOT EXISTS tag_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tag_id INTEGER NOT NULL REFERENCES tag_catalog(id),
  node_id INTEGER NOT NULL REFERENCES lineage_nodes(id),
  track TEXT NOT NULL CHECK (track IN ('active','virtual')),
  state TEXT NOT NULL CHECK (state IN ('bound','released')) DEFAULT 'bound',
  tag_fingerprint TEXT NOT NULL,
  node_status_snapshot TEXT NOT NULL CHECK (node_status_snapshot IN ('登记','在用','停用')),
  operator TEXT NOT NULL DEFAULT '',
  lock_version INTEGER NOT NULL DEFAULT 1,
  bound_at TEXT NOT NULL DEFAULT (datetime('now')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- 单节点唯一在绑落点：真绑/虚绑禁止并存，并发争抢不会写两行
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_bind_bound_node
  ON tag_bindings(node_id) WHERE state='bound';
-- 同标签同节点不可重复在绑，杜绝重复行盖掉先手
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_bind_bound_tag_node
  ON tag_bindings(tag_id, node_id) WHERE state='bound';

-- 绑定台账：只追加的双写两轨留痕
CREATE TABLE IF NOT EXISTS tag_bind_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ref_id INTEGER NOT NULL DEFAULT 0,
  action TEXT NOT NULL,
  track TEXT NOT NULL DEFAULT '',
  tag_code TEXT NOT NULL DEFAULT '',
  node_code TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL DEFAULT '',
  node_status TEXT NOT NULL DEFAULT '',
  operator TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 绑定审计事件
CREATE TABLE IF NOT EXISTS tag_bind_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  ref_id INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_db(db_path: str | None = None, immediate: bool = False):
    conn = connect(db_path)
    try:
        if immediate:
            # 整笔提交先取写锁，两名值班争绑时后者等锁后撞唯一约束而非盖写
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
