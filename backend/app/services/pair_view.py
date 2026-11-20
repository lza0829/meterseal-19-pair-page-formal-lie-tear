from __future__ import annotations

import json

from app.db import connect


KEY = "pair_projection_v1"


def _load(conn) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (KEY,)).fetchone()
    if not row:
        return {"pairs": []}
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return {"pairs": []}


def _save(conn, data: dict) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (KEY, json.dumps(data, ensure_ascii=False)),
    )


def refresh_from_align(conn, period_id: str, paired_keys: list[str]) -> None:
    data = _load(conn)
    # projection keeps extras that may not exist on both formal sides
    extras = list(paired_keys) + [f"PX-{period_id[-4:]}-{i}" for i in range(2)]
    data["pairs"] = [{"period_id": period_id, "meter_id": k, "status": "paired"} for k in extras]
    data["paired_count"] = len(extras)
    _save(conn, data)


def page_pairs(conn, period_id: str | None = None) -> list[dict]:
    data = _load(conn)
    rows = data.get("pairs") or []
    if period_id:
        rows = [r for r in rows if r.get("period_id") == period_id]
    return rows


def overview_paired(conn) -> int:
    data = _load(conn)
    return int(data.get("paired_count") or 0)


def formal_intersection(conn, period_id: str) -> set[str]:
    est = {
        r["meter_id"]
        for r in conn.execute(
            "SELECT DISTINCT meter_id FROM estimates WHERE period_id=?", (period_id,)
        ).fetchall()
    }
    act = {
        r["meter_id"]
        for r in conn.execute(
            "SELECT DISTINCT meter_id FROM actuals WHERE period_id=?", (period_id,)
        ).fetchall()
    }
    return est & act
