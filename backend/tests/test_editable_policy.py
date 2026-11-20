"""架构二 editable_until_sealed：未封印前估抄始终可改，改后 open 批失效；实抄不锁。"""
from __future__ import annotations

from conftest import PERIOD, METER, add_actual, add_estimate, align, correct, latest_batch, revise, set_policy


def test_actual_does_not_lock_estimate(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0)
    r = add_actual(client, qty=9.0)
    assert r.status_code == 200
    assert r.json()["estimate_locked"] is False
    assert r.json()["locked_now"] is False

    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    assert rows[0]["estimate_locked"] is False
    # 实抄不产生锁标
    assert client.get("/api/estimate-locks", params={"period_id": PERIOD}).json() == []


def test_revision_after_actual_succeeds_and_changes_delta(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)

    r = revise(client, 9.0)
    assert r.status_code == 200, r.text
    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    assert rows[0]["qty"] == 9.0

    # 改后重新对齐：差额消失（匹配），不能再用旧估抄袭 10
    b = align(client)
    assert b["matched_count"] == 1
    assert b["variance_count"] == 0
    vars_ = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()
    assert vars_ == []


def test_revision_expires_prior_open_batch(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=7.0)
    b1 = align(client)
    assert latest_batch(client)["status"] == "open"

    r = revise(client, 8.0)
    assert r.status_code == 200
    assert r.json()["expired_batches"] >= 1
    old = latest_batch(client)
    assert old["status"] == "expired"
    assert old["batch_id"] == b1["batch_id"]

    # 新批反映新差额 delta = 7 - 8 = -1，而不是旧的 7 - 10 = -3
    b2 = align(client)
    vars_ = client.get("/api/variances", params={"batch_id": b2["batch_id"]}).json()
    v = [x for x in vars_ if x["meter_id"] == METER][0]
    assert v["estimate_qty"] == 8.0
    assert abs(v["delta"] - (-1.0)) < 1e-9


def test_align_uses_latest_estimate_only(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0, doc_no="EST-1")
    add_estimate(client, meter="M-W-102", qty=20.0, doc_no="EST-2")
    add_actual(client, meter="M-W-102", qty=22.0, doc_no="ACT-2")
    # 同一表同单号会 409；换单号追加第二笔估抄（旧实现会把两笔都带进对齐）
    extra = client.post(
        "/api/estimates",
        json={"meter_id": METER, "period_id": PERIOD, "qty": 30.0, "doc_no": "EST-1B"},
    )
    assert extra.status_code == 200, extra.text
    b = align(client)
    vars_ = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()
    by_meter = {v["meter_id"]: v for v in vars_}
    # METER 只有实抄缺失桶；估抄取最新 30，且不重复计两笔
    assert by_meter[METER]["estimate_qty"] == 30.0
    assert by_meter[METER]["actual_qty"] is None
    # M-W-102 delta = 22 - 20
    assert abs(by_meter["M-W-102"]["delta"] - 2.0) < 1e-9


def test_correction_endpoint_rejected_under_editable_policy(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    r = correct(client, corrected_qty=9.5)
    assert r.status_code == 409
    assert "修订" in r.json()["detail"]


def test_switch_back_to_lock_policy_restores_gate_for_existing_locks(client):
    # 先在锁定口径下制造锁
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    assert revise(client, 11.0).status_code == 409
    # 切到可改口径：同一块表立刻可改
    set_policy(client, "editable_until_sealed")
    assert revise(client, 11.0).status_code == 200
    # 切回锁定口径：历史锁标重新生效，再次拒改
    set_policy(client, "lock_on_actual")
    blocked = revise(client, 12.0)
    assert blocked.status_code == 409
    rows = client.get("/api/estimates", params={"period_id": PERIOD}).json()
    assert rows[0]["estimate_locked"] is True


def test_invalid_policy_value_rejected(client):
    r = client.put(
        "/api/settings/estimate_lock_policy",
        json={"value": "something_else"},
    )
    assert r.status_code == 400
    # 口径未被写脏
    assert client.get("/api/settings").json()["estimate_lock_policy"] == "lock_on_actual"
