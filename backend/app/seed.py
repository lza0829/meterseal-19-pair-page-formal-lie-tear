from __future__ import annotations
from app.db import connect

# 全局只贯彻一种估抄锁口径，由 settings.estimate_lock_policy 决定：
#   lock_on_actual      首笔实抄到达即锁定该表该账期估抄，纠错走订正链路
#   editable_until_sealed 封印前估抄始终可改，改后 open 对齐批失效
POLICY_LOCK_ON_ACTUAL = "lock_on_actual"
POLICY_EDITABLE = "editable_until_sealed"
VALID_POLICIES = (POLICY_LOCK_ON_ACTUAL, POLICY_EDITABLE)
DEFAULT_POLICY = POLICY_LOCK_ON_ACTUAL

SCHEMA = """
CREATE TABLE IF NOT EXISTS meters (
  meter_id TEXT PRIMARY KEY,
  building TEXT NOT NULL,
  unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS periods (
  period_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT,
  batch_no TEXT
);
CREATE TABLE IF NOT EXISTS estimates (
  estimate_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS actuals (
  actual_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  reader_code TEXT NOT NULL,
  doc_no TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  batch_id TEXT PRIMARY KEY,
  period_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  matched_count INTEGER NOT NULL DEFAULT 0,
  variance_count INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variances (
  variance_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  estimate_qty REAL,
  actual_qty REAL,
  delta REAL NOT NULL,
  fate TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- 实到达锁标记：仅 lock_on_actual 口径写入；一行代表该表该账期估抄已被首笔实抄锁定
CREATE TABLE IF NOT EXISTS estimate_locks (
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  locked_by_actual TEXT NOT NULL,
  locked_at TEXT NOT NULL,
  PRIMARY KEY (meter_id, period_id)
);
-- 订正链路：锁定后不允许改估抄本体，只允许追加订正，对齐仍以锁定时估抄为准
CREATE TABLE IF NOT EXISTS estimate_corrections (
  correction_id TEXT PRIMARY KEY,
  meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL,
  locked_qty REAL NOT NULL,
  corrected_qty REAL NOT NULL,
  reason TEXT NOT NULL,
  doc_no TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def migrate(conn) -> None:
    """幂等迁移：老库补列、补表，不破坏既有数据。"""
    # 对齐批快照记录生成时的口径与锁计数
    _add_column_if_missing(conn, "reconcile_batches", "policy", "policy TEXT")
    _add_column_if_missing(conn, "reconcile_batches", "locked_count", "locked_count INTEGER NOT NULL DEFAULT 0")
    # 每条差额快照该表该账期在对齐瞬间的锁标记
    _add_column_if_missing(conn, "variances", "estimate_locked", "estimate_locked INTEGER NOT NULL DEFAULT 0")


def _backfill_locks(conn) -> None:
    """默认 lock_on_actual 口径：为既有实抄补齐首笔实抄锁标记，保证口径一致。"""
    for r in conn.execute(
        "SELECT DISTINCT meter_id, period_id, MIN(loaded_at) AS first_at FROM actuals GROUP BY meter_id, period_id"
    ).fetchall():
        conn.execute(
            """INSERT OR IGNORE INTO estimate_locks(meter_id,period_id,locked_by_actual,locked_at)
               VALUES (?,?, (SELECT actual_id FROM actuals
                             WHERE meter_id=? AND period_id=?
                             ORDER BY loaded_at LIMIT 1), ?)""",
            (r["meter_id"], r["period_id"], r["meter_id"], r["period_id"], r["first_at"]),
        )


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        if n:
            # 老库迁移：补齐口径设置，并在默认锁定口径下回填历史实抄锁标
            exists = conn.execute(
                "SELECT 1 FROM settings WHERE key='estimate_lock_policy'"
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES ('estimate_lock_policy',?)",
                    (DEFAULT_POLICY,),
                )
            _backfill_locks(conn)
            conn.commit()
            return
        meters = [
            ("M-W-101", "A栋", "101", "water"),
            ("M-W-102", "A栋", "102", "water"),
            ("M-E-101", "A栋", "101", "electric"),
            ("M-W-201", "B栋", "201", "water"),
            ("M-E-201", "B栋", "201", "electric"),
            ("M-E-202", "B栋", "202", "electric"),
        ]
        conn.executemany(
            "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?,?,?,?, 'active')",
            meters,
        )
        conn.execute(
            "INSERT INTO periods(period_id,status) VALUES ('2026-10','open')"
        )
        conn.execute(
            "INSERT INTO periods(period_id,status) VALUES ('2026-09','open')"
        )
        # estimates for 2026-10
        est = [
            ("E1", "M-W-101", "2026-10", 12.0, "EST-101", "2026-10-01T08:00:00"),
            ("E2", "M-W-102", "2026-10", 15.0, "EST-102", "2026-10-01T08:00:00"),
            ("E3", "M-E-101", "2026-10", 220.0, "EST-E101", "2026-10-01T08:00:00"),
            ("E4", "M-W-201", "2026-10", 18.0, "EST-201", "2026-10-01T08:00:00"),
            ("E5", "M-E-201", "2026-10", 310.0, "EST-E201", "2026-10-01T08:00:00"),
            ("E6", "M-E-202", "2026-10", 280.0, "EST-E202", "2026-10-01T08:00:00"),
        ]
        conn.executemany(
            "INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at) VALUES (?,?,?,?,?,?)",
            est,
        )
        # actuals: most match; two variance
        act = [
            ("A1", "M-W-101", "2026-10", 12.0, "2026-10-28T10:00:00", "R01", "ACT-101", "2026-10-28T10:05:00"),
            ("A2", "M-W-102", "2026-10", 14.0, "2026-10-28T10:10:00", "R01", "ACT-102", "2026-10-28T10:15:00"),  # short
            ("A3", "M-E-101", "2026-10", 220.0, "2026-10-28T11:00:00", "R02", "ACT-E101", "2026-10-28T11:05:00"),
            ("A4", "M-W-201", "2026-10", 18.0, "2026-10-28T11:20:00", "R02", "ACT-201", "2026-10-28T11:25:00"),
            ("A5", "M-E-201", "2026-10", 330.0, "2026-10-28T12:00:00", "R03", "ACT-E201", "2026-10-28T12:05:00"),  # over
            ("A6", "M-E-202", "2026-10", 280.0, "2026-10-28T12:20:00", "R03", "ACT-E202", "2026-10-28T12:25:00"),
        ]
        conn.executemany(
            "INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at) VALUES (?,?,?,?,?,?,?,?)",
            act,
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('current_period','2026-10')"
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('product_title','抄表簿')"
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES ('estimate_lock_policy',?)",
            (DEFAULT_POLICY,),
        )
        # 默认 lock_on_actual：种子实抄即视为首笔实抄，补齐锁标记，保证口径一致
        _backfill_locks(conn)
        # 界面不露英文：表类型仍存 water/electric 码，前端映射为中文
        conn.commit()
    finally:
        conn.close()
