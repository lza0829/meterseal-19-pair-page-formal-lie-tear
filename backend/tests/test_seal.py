"""封印闸：两种架构下，已封印账期一律拒改估抄/实抄/命运。"""
from __future__ import annotations

from conftest import PERIOD, add_actual, add_estimate, align, correct, revise, set_policy


def _seal(client, period=PERIOD):
    r = client.post("/api/seal", json={"period_id": period, "batch_id": None})
    assert r.status_code == 200, r.text
    return r.json()


def test_sealed_period_blocks_all_writes_under_lock_policy(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    align(client)
    _seal(client)

    assert revise(client, 11.0).status_code == 409
    sealed_est = client.post(
        "/api/estimates",
        json={"meter_id": "M-W-101", "period_id": PERIOD, "qty": 12.0, "doc_no": "EST-AFTER"},
    )
    assert sealed_est.status_code == 409
    assert add_actual(client, qty=8.0, doc_no="ACT-AFTER").status_code == 409
    assert correct(client, corrected_qty=9.0, doc_no="C-AFTER").status_code == 409
    # 已封印账期不能再对齐
    assert client.post("/api/align", json={"period_id": PERIOD}).status_code == 409
    # 差额命运也冻结
    b = client.get("/api/batches", params={"period_id": PERIOD}).json()
    sealed_batch = [x for x in b if x["status"] == "sealed"][0]
    v = client.get("/api/variances", params={"batch_id": sealed_batch["batch_id"]}).json()[0]
    r = client.post(f"/api/variances/{v['variance_id']}/fate", json={"fate": "quarantine"})
    assert r.status_code == 409


def test_sealed_period_blocks_revision_under_editable_policy(client):
    set_policy(client, "editable_until_sealed")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    align(client)
    _seal(client)

    # 可改架构在封印后同样拒改
    r = revise(client, 11.0)
    assert r.status_code == 409
    assert "封印" in r.json()["detail"]
    assert add_actual(client, qty=8.0, doc_no="ACT-AFTER").status_code == 409


def test_seal_requires_open_batch_and_rejects_expired(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    # 无 open 批
    r = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": None})
    assert r.status_code == 400
    b1 = align(client)
    # 新实抄使批作废
    add_actual(client, qty=8.5, doc_no="ACT-2")
    r = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": b1["batch_id"]})
    assert r.status_code == 409
    # 重新对齐后可封印
    b2 = align(client)
    ok = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": b2["batch_id"]})
    assert ok.status_code == 200


def test_double_seal_rejected(client):
    set_policy(client, "lock_on_actual")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    align(client)
    _seal(client)
    r = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": None})
    assert r.status_code == 409
