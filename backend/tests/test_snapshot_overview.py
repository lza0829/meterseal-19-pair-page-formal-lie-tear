"""快照 estimate_locked 与总览口径联动核对。"""
from __future__ import annotations

from conftest import PERIOD, METER, add_actual, add_estimate, align, latest_batch, revise, set_policy


def test_snapshot_locked_flag_differs_by_policy_at_align_time(client):
    # 锁定口径：先实抄后对齐 → 快照带锁
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=8.0)
    b = align(client)
    assert b["locked_count"] == 1
    vars_ = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()
    assert vars_[0]["estimate_locked"] == 1

    # 切可改口径：批失效后重新对齐，新批快照不带锁，旧批保留历史快照不回改
    set_policy(client, "editable_until_sealed")
    revise(client, 10.0)  # 触发批失效
    b2 = align(client)
    assert b2["locked_count"] == 0
    old_vars = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()
    assert old_vars[0]["estimate_locked"] == 1
    new_vars = client.get("/api/variances", params={"batch_id": b2["batch_id"]}).json()
    assert new_vars[0]["estimate_locked"] == 0


def test_align_before_actual_then_lock_after_keeps_old_snapshot_unlocked(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    b1 = align(client)  # 实抄缺失桶，对齐瞬间无锁
    v1 = client.get("/api/variances", params={"batch_id": b1["batch_id"]}).json()
    assert v1[0]["estimate_locked"] == 0
    assert b1["locked_count"] == 0

    add_actual(client, qty=9.0)  # 批被失效，但历史快照不回改
    assert latest_batch(client)["status"] == "expired"
    v1_after = client.get("/api/variances", params={"batch_id": b1["batch_id"]}).json()
    assert v1_after[0]["estimate_locked"] == 0

    b2 = align(client)
    v2 = client.get("/api/variances", params={"batch_id": b2["batch_id"]}).json()
    assert b2["locked_count"] == 1
    assert v2[0]["estimate_locked"] == 1


def test_overview_counts_consistent_with_batches_and_locks(client):
    set_policy(client, "lock_on_actual")
    base_locked = client.get("/api/overview").json()["locked_estimates"]
    add_estimate(client, qty=10.0)
    add_actual(client, qty=8.0)
    align(client)  # 1 个 open 批，1 条 pending 差额，新增 1 个有效锁

    ov = client.get("/api/overview").json()
    assert ov["estimate_lock_policy"] == "lock_on_actual"
    assert ov["locked_estimates"] == base_locked + 1
    assert ov["open_batch_count"] == 1
    assert ov["expired_batch_count"] == 0
    assert ov["pending_variances"] == 1

    # 切到可改口径：历史锁不再有效，总览有效锁归零
    set_policy(client, "editable_until_sealed")
    ov_editable = client.get("/api/overview").json()
    assert ov_editable["locked_estimates"] == 0
    set_policy(client, "lock_on_actual")

    # 再对齐一次：旧批作废，新批 open，pending 仍只统计 open 批那一份
    align(client)
    ov2 = client.get("/api/overview").json()
    assert ov2["open_batch_count"] == 1
    assert ov2["expired_batch_count"] == 1
    assert ov2["pending_variances"] == 1

    # 处置掉新批差额后 pending 归零
    b = latest_batch(client)
    v = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()[0]
    client.post(f"/api/variances/{v['variance_id']}/fate", json={"fate": "accept_short"})
    ov3 = client.get("/api/overview").json()
    assert ov3["pending_variances"] == 0


def test_overview_correction_count(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    from conftest import correct

    correct(client, corrected_qty=9.1, doc_no="C-1")
    correct(client, corrected_qty=9.2, doc_no="C-2")
    ov = client.get("/api/overview").json()
    assert ov["correction_count"] == 2
