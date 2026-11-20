"""架构一 lock_on_actual：首笔实抄到达后估抄拒绝再改，纠错走订正链路。"""
from __future__ import annotations

from conftest import PERIOD, METER, add_actual, add_estimate, align, correct, latest_batch, revise, set_policy


def test_default_policy_is_lock_on_actual(client):
    s = client.get("/api/settings").json()
    assert s["estimate_lock_policy"] == "lock_on_actual"


def test_estimate_revisable_before_first_actual(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    r = revise(client, 11.0)
    assert r.status_code == 200, r.text
    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    assert rows[0]["qty"] == 11.0
    assert rows[0]["estimate_locked"] is False


def test_first_actual_locks_estimate_and_blocks_revision(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    r = add_actual(client, qty=9.0)
    assert r.status_code == 200
    body = r.json()
    assert body["estimate_locked"] is True
    assert body["locked_now"] is True

    # 锁后改估抄必须被写闸拒绝（不是仅文案提示）
    blocked = revise(client, 12.0)
    assert blocked.status_code == 409
    assert "锁定" in blocked.json()["detail"]

    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    assert rows[0]["qty"] == 10.0  # 估抄本体未被改动
    assert rows[0]["estimate_locked"] is True

    locks = client.get("/api/estimate-locks", params={"period_id": PERIOD}).json()
    assert len(locks) == 1 and locks[0]["meter_id"] == METER


def test_second_actual_keeps_single_lock(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client)
    add_actual(client, doc_no="ACT-1")
    r2 = add_actual(client, qty=9.5, doc_no="ACT-2")
    assert r2.status_code == 200
    assert r2.json()["locked_now"] is False  # 锁已存在，不重复打标
    locks = client.get("/api/estimate-locks", params={"period_id": PERIOD}).json()
    assert len(locks) == 1
    # 锁后修订依旧被拒
    assert revise(client, 20.0).status_code == 409


def test_correction_chain_after_lock_does_not_change_estimate_nor_align_input(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)

    # 未锁表不能订正
    add_estimate(client, meter="M-W-102", doc_no="EST-X2")
    pre = correct(client, 99.0, meter="M-W-102", doc_no="C-PRE")
    assert pre.status_code == 409

    # 已锁表走订正链路
    r = correct(client, corrected_qty=9.2, reason="现场复核偏差", doc_no="C-1")
    assert r.status_code == 200, r.text
    assert r.json()["locked_qty"] == 10.0

    # 订正不动估抄本体
    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    mine = [x for x in rows if x["meter_id"] == METER][0]
    assert mine["qty"] == 10.0
    assert mine["latest_correction"]["corrected_qty"] == 9.2

    # 对齐输入仍以锁定时估抄为准：delta = 9 - 10 = -1
    b = align(client)
    vars_ = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()
    v = [x for x in vars_ if x["meter_id"] == METER][0]
    assert v["estimate_qty"] == 10.0
    assert v["actual_qty"] == 9.0
    assert abs(v["delta"] - (-1.0)) < 1e-9
    assert v["estimate_locked"] == 1


def test_lock_applies_per_meter_period_other_meter_still_editable(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, meter=METER, qty=10.0)
    add_estimate(client, meter="M-W-102", qty=20.0, doc_no="EST-2")
    add_actual(client, meter=METER, qty=9.0)

    # 同账期另一块表未到实抄，估抄仍可修订
    ok = revise(client, 21.0, meter="M-W-102")
    assert ok.status_code == 200
    # 已锁表依旧拒
    assert revise(client, 11.0, meter=METER).status_code == 409


def test_actual_arrival_expires_open_batch_and_snapshot_locked(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    b1 = align(client)
    assert latest_batch(client)["status"] == "open"

    r = add_actual(client, qty=9.0)
    assert r.json()["expired_batches"] >= 1
    assert latest_batch(client)["status"] == "expired"
    assert latest_batch(client)["batch_id"] == b1["batch_id"]

    b2 = align(client)
    assert b2["policy"] == "lock_on_actual"
    assert b2["locked_count"] == 1
    vars_ = client.get("/api/variances", params={"batch_id": b2["batch_id"]}).json()
    assert all(v["estimate_locked"] == 1 for v in vars_ if v["meter_id"] == METER)


def test_estimate_write_before_actual_also_expires_open_batch(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)  # 锁定 METER
    b1 = align(client)["batch_id"]

    # 未锁的新表装载估抄，也必须使该期 open 批失效
    r = add_estimate(client, meter="M-W-102", qty=5.0, doc_no="EST-NEW")
    assert r["expired_batches"] >= 1
    assert latest_batch(client)["status"] == "expired"
    assert latest_batch(client)["batch_id"] == b1
