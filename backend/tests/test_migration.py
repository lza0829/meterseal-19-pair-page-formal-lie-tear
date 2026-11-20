"""老库迁移：旧 schema（无锁表/订正表/新列）启动后必须平滑升级且数据不丢。"""
from __future__ import annotations

import pathlib

import app.config as config
import app.db as db
from app.db import connect
from app.seed import DEFAULT_POLICY, seed_if_empty


LEGACY_SCHEMA = """
CREATE TABLE meters (
  meter_id TEXT PRIMARY KEY, building TEXT NOT NULL, unit_label TEXT NOT NULL,
  meter_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE periods (
  period_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open',
  sealed_at TEXT, batch_no TEXT
);
CREATE TABLE estimates (
  estimate_id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, period_id TEXT NOT NULL,
  qty REAL NOT NULL, doc_no TEXT NOT NULL, loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE actuals (
  actual_id TEXT PRIMARY KEY, meter_id TEXT NOT NULL, period_id TEXT NOT NULL,
  qty REAL NOT NULL, occurred_at TEXT NOT NULL, reader_code TEXT NOT NULL,
  doc_no TEXT NOT NULL, loaded_at TEXT NOT NULL,
  UNIQUE(meter_id, period_id, doc_no)
);
CREATE TABLE reconcile_batches (
  batch_id TEXT PRIMARY KEY, period_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  matched_count INTEGER NOT NULL DEFAULT 0, variance_count INTEGER NOT NULL DEFAULT 0,
  fingerprint TEXT, created_at TEXT NOT NULL
);
CREATE TABLE variances (
  variance_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, meter_id TEXT NOT NULL,
  period_id TEXT NOT NULL, estimate_qty REAL, actual_qty REAL,
  delta REAL NOT NULL, fate TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def test_legacy_db_migrates_and_preserves_data(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    for mod in (config, db):
        monkeypatch.setattr(mod, "DATA_DIR", pathlib.Path(tmp_path))
        monkeypatch.setattr(mod, "DB_PATH", db_path)

    # 手工造一个旧版库
    conn = connect()
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO meters VALUES ('M-OLD-1','旧栋','1','water','active')"
    )
    conn.execute("INSERT INTO periods(period_id,status) VALUES ('2026-08','open')")
    conn.execute(
        "INSERT INTO estimates VALUES ('E-OLD-1','M-OLD-1','2026-08',10.0,'EST-OLD','2026-08-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO actuals VALUES ('A-OLD-1','M-OLD-1','2026-08',9.0,'2026-08-28T00:00:00','R01','ACT-OLD','2026-08-28T00:05:00')"
    )
    conn.execute(
        "INSERT INTO reconcile_batches VALUES ('B-OLD-1','2026-08','open',0,1,'fpold','2026-08-29T00:00:00')"
    )
    conn.execute(
        "INSERT INTO variances VALUES ('V-OLD-1','B-OLD-1','M-OLD-1','2026-08',10.0,9.0,-1.0,'pending')"
    )
    conn.commit()
    conn.close()

    # 启动应用时执行迁移与种子（meter 已存在，走“老库补设置”分支）
    seed_if_empty()

    conn = connect()
    try:
        # 既有数据原样保留
        assert conn.execute("SELECT qty FROM estimates WHERE estimate_id='E-OLD-1'").fetchone()["qty"] == 10.0
        assert conn.execute("SELECT status FROM reconcile_batches WHERE batch_id='B-OLD-1'").fetchone()["status"] == "open"
        old_v = conn.execute("SELECT * FROM variances WHERE variance_id='V-OLD-1'").fetchone()
        assert old_v["delta"] == -1.0
        assert old_v["estimate_locked"] == 0  # 新列默认值

        # 新表/新列就位
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"estimate_locks", "estimate_corrections"} <= tables
        batch_cols = {r[1] for r in conn.execute("PRAGMA table_info(reconcile_batches)")}
        assert {"policy", "locked_count"} <= batch_cols

        # 口径设置补齐为默认值
        policy = conn.execute(
            "SELECT value FROM settings WHERE key='estimate_lock_policy'"
        ).fetchone()["value"]
        assert policy == DEFAULT_POLICY

        # 既有实抄被回填为锁标（默认 lock_on_actual 口径一致性）
        lock = conn.execute(
            "SELECT * FROM estimate_locks WHERE meter_id='M-OLD-1' AND period_id='2026-08'"
        ).fetchone()
        assert lock is not None
        assert lock["locked_by_actual"] == "A-OLD-1"
    finally:
        conn.close()


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "twice.db"
    for mod in (config, db):
        monkeypatch.setattr(mod, "DATA_DIR", pathlib.Path(tmp_path))
        monkeypatch.setattr(mod, "DB_PATH", db_path)
    seed_if_empty()
    seed_if_empty()  # 第二次不应报错、不重复加列
    conn = connect()
    try:
        batch_cols = [r[1] for r in conn.execute("PRAGMA table_info(reconcile_batches)")]
        assert batch_cols.count("policy") == 1
        assert batch_cols.count("locked_count") == 1
    finally:
        conn.close()
