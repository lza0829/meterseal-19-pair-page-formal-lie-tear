from __future__ import annotations

import os
import tempfile
import pathlib

import pytest

# 每个测试会话使用独立临时库，绝不动工作区 data/
_tmp = tempfile.mkdtemp(prefix="meterseal-test-")
os.environ["METERSEAL_TEST_DIR"] = _tmp

import app.config as config  # noqa: E402

config.DATA_DIR = pathlib.Path(_tmp)
config.DB_PATH = pathlib.Path(_tmp) / "test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import connect  # noqa: E402

_ALL_TABLES = (
    "variances",
    "reconcile_batches",
    "estimate_corrections",
    "estimate_locks",
    "actuals",
    "estimates",
    "settings",
    "periods",
    "meters",
)


@pytest.fixture()
def client():
    conn = connect()
    try:
        for table in _ALL_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        # 强制 WAL 回收：并发用例的线程连接可能留下未合并帧，
        # TRUNCATE 保证下一个用例读到的就是刚清完的库，杜绝跨用例状态泄漏
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    from app.seed import seed_if_empty

    seed_if_empty()
    with TestClient(app) as c:
        yield c


# ---- 测试辅助 ----

PERIOD = "2026-09"  # 种子中无任何读数的开放账期，专门用于“首笔实抄前后”对照
METER = "M-W-101"


def set_policy(c, policy: str):
    r = c.put(
        "/api/settings/estimate_lock_policy",
        json={"value": policy},
    )
    assert r.status_code == 200, r.text
    return r


def add_estimate(c, meter=METER, period=PERIOD, qty=10.0, doc_no="EST-T1"):
    r = c.post(
        "/api/estimates",
        json={"meter_id": meter, "period_id": period, "qty": qty, "doc_no": doc_no},
    )
    assert r.status_code == 200, r.text
    return r.json()


def add_actual(c, meter=METER, period=PERIOD, qty=9.0, doc_no="ACT-T1"):
    r = c.post(
        "/api/actuals",
        json={
            "meter_id": meter,
            "period_id": period,
            "qty": qty,
            "occurred_at": "2026-09-28T10:00:00",
            "reader_code": "R01",
            "doc_no": doc_no,
        },
    )
    return r


def revise(c, qty, meter=METER, period=PERIOD, doc_no=None):
    return c.post(
        "/api/estimates/revise",
        json={"meter_id": meter, "period_id": period, "qty": qty, "doc_no": doc_no},
    )


def correct(c, corrected_qty, meter=METER, period=PERIOD, reason="估抄偏差", doc_no="C-T1"):
    return c.post(
        "/api/estimates/corrections",
        json={
            "meter_id": meter,
            "period_id": period,
            "corrected_qty": corrected_qty,
            "reason": reason,
            "doc_no": doc_no,
        },
    )


def align(c, period=PERIOD):
    r = c.post("/api/align", json={"period_id": period})
    assert r.status_code == 200, r.text
    return r.json()


def latest_batch(c, period=PERIOD):
    rows = c.get("/api/batches", params={"period_id": period}).json()
    return rows[0] if rows else None
