"""账期创建与基础写闸。"""
from __future__ import annotations

from conftest import add_actual, add_estimate, set_policy


def test_create_period_and_duplicate_rejected(client):
    r = client.post("/api/periods", json={"period_id": "2099-12"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "open"
    dup = client.post("/api/periods", json={"period_id": "2099-12"})
    assert dup.status_code == 409


def test_writes_to_unknown_period_404(client):
    r = client.post(
        "/api/estimates",
        json={"meter_id": "M-W-101", "period_id": "NOPE", "qty": 1.0, "doc_no": "X1"},
    )
    assert r.status_code == 404


def test_unknown_meter_rejected_under_both_policies(client):
    for policy in ("lock_on_actual", "editable_until_sealed"):
        set_policy(client, policy)
        r = client.post(
            "/api/estimates",
            json={"meter_id": "M-GHOST", "period_id": "2026-09", "qty": 1.0, "doc_no": f"G-{policy}"},
        )
        assert r.status_code == 404
        r2 = client.post(
            "/api/actuals",
            json={
                "meter_id": "M-GHOST", "period_id": "2026-09", "qty": 1.0,
                "occurred_at": "2026-09-28T10:00:00", "reader_code": "R1", "doc_no": f"GA-{policy}",
            },
        )
        assert r2.status_code == 404


def test_actual_and_estimate_basic_write_chain(client):
    set_policy(client, "lock_on_actual")
    r = add_estimate(client, meter="M-E-101", qty=50.0, doc_no="EB-1")
    assert r["expired_batches"] == 0  # 该账期尚无对齐批
    r2 = add_actual(client, meter="M-E-101", qty=48.0, doc_no="AB-1")
    assert r2.status_code == 200
    assert r2.json()["estimate_locked"] is True
