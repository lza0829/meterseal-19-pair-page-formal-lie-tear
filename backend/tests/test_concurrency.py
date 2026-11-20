"""并发核对：实抄到达与改估抄竞争时，锁标记与写入必须原子，禁止半截无锁标。"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app.routers import readings


def _install_monotonic_clock(monkeypatch):
    counter = {"n": 0}
    lock = threading.Lock()

    def fake_now():
        with lock:
            counter["n"] += 1
            n = counter["n"]
        # 定宽序号：字典序与时间序一致，MAX(loaded_at)/锁顺序断言确定性可重复
        return f"2026-09-01T00:00:{n:06d}.000000+00:00"

    monkeypatch.setattr(readings, "_now", fake_now)


def _make_period_meter(period: str, meter: str):
    conn = connect()
    try:
        conn.execute("INSERT INTO periods(period_id,status) VALUES (?,'open')", (period,))
        conn.execute(
            "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?, 'T栋', ?,'water','active')",
            (meter, meter),
        )
        conn.execute(
            "INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at) VALUES (?,?,?,?,'SEED','2026-08-31T00:00:00.000000+00:00')",
            (f"E-{meter}", meter, period, 10.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_concurrent_first_actual_vs_revise_never_half_locked(client, monkeypatch):
    _install_monotonic_clock(monkeypatch)
    client.put("/api/settings/estimate_lock_policy", json={"value": "lock_on_actual"})
    rounds = 50

    def one_round(i):
        period = f"2026-09-{i:03d}"  # 虚拟账期号，仅做主键隔离
        meter = f"M-T-{i:03d}"
        _make_period_meter(period, meter)
        barrier = threading.Barrier(2)
        outcomes = {}

        def post_actual():
            barrier.wait()
            c = TestClient(app)
            try:
                outcomes["actual"] = c.post(
                    "/api/actuals",
                    json={
                        "meter_id": meter,
                        "period_id": period,
                        "qty": 9.0,
                        "occurred_at": "2026-09-28T10:00:00",
                        "reader_code": "R01",
                        "doc_no": f"ACT-{i}",
                    },
                )
            finally:
                c.close()

        def post_revise():
            barrier.wait()
            c = TestClient(app)
            try:
                outcomes["revise"] = c.post(
                    "/api/estimates/revise",
                    json={"meter_id": meter, "period_id": period, "qty": 11.0},
                )
            finally:
                c.close()

        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(post_actual)
            f2 = ex.submit(post_revise)
            f1.result()
            f2.result()

        assert outcomes["actual"].status_code == 200, outcomes["actual"].text
        # 不变量 1：实抄存在则锁标必存在（无半截状态）
        conn = connect()
        try:
            n_act = conn.execute(
                "SELECT COUNT(*) c FROM actuals WHERE meter_id=? AND period_id=?",
                (meter, period),
            ).fetchone()["c"]
            n_lock = conn.execute(
                "SELECT COUNT(*) c FROM estimate_locks WHERE meter_id=? AND period_id=?",
                (meter, period),
            ).fetchone()["c"]
            assert n_act == 1
            assert n_lock == 1
            lock_row = conn.execute(
                "SELECT locked_at FROM estimate_locks WHERE meter_id=? AND period_id=?",
                (meter, period),
            ).fetchone()
            est_row = conn.execute(
                "SELECT qty, loaded_at FROM estimates WHERE meter_id=? AND period_id=?",
                (meter, period),
            ).fetchone()
        finally:
            conn.close()

        # 不变量 2：修订要么被拒(409)，要么严格发生在锁定之前；锁后修订绝不可能成功
        rev = outcomes["revise"]
        if rev.status_code == 200:
            assert est_row["loaded_at"] < lock_row["locked_at"]
            assert est_row["qty"] == 11.0
        else:
            assert rev.status_code == 409
            assert est_row["qty"] == 10.0  # 被拒后估抄本体不变

    for i in range(rounds):
        one_round(i)


def test_concurrent_two_first_actuals_single_lock(client, monkeypatch):
    _install_monotonic_clock(monkeypatch)
    client.put("/api/settings/estimate_lock_policy", json={"value": "lock_on_actual"})
    period, meter = "2026-09-901", "M-T-901"
    _make_period_meter(period, meter)
    barrier = threading.Barrier(2)

    def actual(doc):
        barrier.wait()
        c = TestClient(app)
        try:
            return c.post(
                "/api/actuals",
                json={
                    "meter_id": meter,
                    "period_id": period,
                    "qty": 9.0,
                    "occurred_at": "2026-09-28T10:00:00",
                    "reader_code": "R01",
                    "doc_no": doc,
                },
            )
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        rs = list(ex.map(actual, ["ACT-901A", "ACT-901B"]))
    assert all(r.status_code == 200 for r in rs), rs
    conn = connect()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM actuals WHERE meter_id=? AND period_id=?", (meter, period)
        ).fetchone()["c"] == 2
        assert conn.execute(
            "SELECT COUNT(*) c FROM estimate_locks WHERE meter_id=? AND period_id=?", (meter, period)
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_concurrent_revise_under_editable_policy_always_succeeds(client, monkeypatch):
    _install_monotonic_clock(monkeypatch)
    client.put("/api/settings/estimate_lock_policy", json={"value": "editable_until_sealed"})
    period, meter = "2026-09-902", "M-T-902"
    _make_period_meter(period, meter)
    barrier = threading.Barrier(2)

    def run(kind):
        barrier.wait()
        c = TestClient(app)
        try:
            if kind == "actual":
                return c.post(
                    "/api/actuals",
                    json={
                        "meter_id": meter,
                        "period_id": period,
                        "qty": 7.0,
                        "occurred_at": "2026-09-28T10:00:00",
                        "reader_code": "R01",
                        "doc_no": "ACT-902",
                    },
                )
            return c.post(
                "/api/estimates/revise",
                json={"meter_id": meter, "period_id": period, "qty": 7.0},
            )
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        r_act, r_rev = list(ex.map(run, ["actual", "revise"]))
    assert r_act.status_code == 200
    assert r_rev.status_code == 200  # 可改口径下实抄不锁，修订恒成功
    conn = connect()
    try:
        # 可改口径不落锁标
        assert conn.execute(
            "SELECT COUNT(*) c FROM estimate_locks WHERE meter_id=? AND period_id=?", (meter, period)
        ).fetchone()["c"] == 0
        qty = conn.execute(
            "SELECT qty FROM estimates WHERE meter_id=? AND period_id=?", (meter, period)
        ).fetchone()["qty"]
        assert qty == 7.0
    finally:
        conn.close()
    # 重新对齐必须用修订后的估抄，差额匹配为 0
    r = client.post("/api/align", json={"period_id": period})
    assert r.json()["variance_count"] == 0
    assert r.json()["matched_count"] == 1


def test_mixed_stress_no_half_state(client, monkeypatch):
    _install_monotonic_clock(monkeypatch)
    client.put("/api/settings/estimate_lock_policy", json={"value": "lock_on_actual"})
    for i in range(10):
        _make_period_meter(f"2026-09-8{i:02d}", f"M-S-{i:03d}")

    def op(i):
        meter = f"M-S-{i:03d}"
        period = f"2026-09-8{i:02d}"
        c = TestClient(app)
        try:
            r1 = c.post(
                "/api/actuals",
                json={
                    "meter_id": meter,
                    "period_id": period,
                    "qty": float(i),
                    "occurred_at": "2026-09-28T10:00:00",
                    "reader_code": "R01",
                    "doc_no": f"ACT-S{i}",
                },
            )
            r2 = c.post(
                "/api/estimates/revise",
                json={"meter_id": meter, "period_id": period, "qty": 99.0},
            )
            return r1.status_code, r2.status_code
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(op, range(10)))
    for act_code, rev_code in results:
        assert act_code == 200
        assert rev_code in (200, 409)

    # 全局不变量：每条实抄所在 (表,账期) 都有且仅有一条锁标
    conn = connect()
    try:
        pairs = conn.execute(
            """SELECT a.meter_id, a.period_id,
                      (SELECT COUNT(*) FROM estimate_locks l
                       WHERE l.meter_id=a.meter_id AND l.period_id=a.period_id) AS locks
               FROM (SELECT DISTINCT meter_id, period_id FROM actuals) a"""
        ).fetchall()
        assert pairs and all(r["locks"] == 1 for r in pairs)
    finally:
        conn.close()
