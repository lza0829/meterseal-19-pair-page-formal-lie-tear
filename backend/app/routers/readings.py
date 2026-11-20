from __future__ import annotations
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import connect
from app.services.gate import (
    assert_estimate_mutable,
    assert_period_open,
    expire_open_batches,
    get_policy,
    is_locked,
    with_write_tx,
)

router = APIRouter(tags=["readings"])


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:10]


class EstimateIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str = Field(min_length=1)


class EstimateReviseIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    doc_no: str | None = None


class CorrectionIn(BaseModel):
    meter_id: str
    period_id: str
    corrected_qty: float
    reason: str = Field(min_length=1)
    doc_no: str = Field(min_length=1)


class ActualIn(BaseModel):
    meter_id: str
    period_id: str
    qty: float
    occurred_at: str
    reader_code: str = "R00"
    doc_no: str = Field(min_length=1)


@router.get("/estimates")
def list_estimates(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM estimates WHERE period_id=? ORDER BY meter_id, loaded_at",
                (period_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM estimates ORDER BY period_id, meter_id, loaded_at"
            ).fetchall()
        policy = get_policy(conn)
        result = []
        for r in rows:
            item = dict(r)
            locked = is_locked(conn, r["meter_id"], r["period_id"])
            # 有效锁标必须与当前口径同口径：editable_until_sealed 下历史锁行不驱动写闸，也不展示为锁
            item["estimate_locked"] = bool(locked and policy == "lock_on_actual")
            lock_row = conn.execute(
                "SELECT locked_at FROM estimate_locks WHERE meter_id=? AND period_id=?",
                (r["meter_id"], r["period_id"]),
            ).fetchone()
            item["locked_at"] = lock_row["locked_at"] if lock_row else None
            corr = conn.execute(
                """SELECT correction_id, corrected_qty, reason, doc_no, created_at
                   FROM estimate_corrections
                   WHERE meter_id=? AND period_id=?
                   ORDER BY created_at DESC, correction_id DESC LIMIT 1""",
                (r["meter_id"], r["period_id"]),
            ).fetchone()
            item["latest_correction"] = dict(corr) if corr else None
            item["policy"] = policy
            result.append(item)
        return result
    finally:
        conn.close()


@router.get("/estimate-locks")
def list_locks(period_id: str | None = None):
    conn = connect()
    try:
        sql = "SELECT * FROM estimate_locks"
        params: tuple = ()
        if period_id:
            sql += " WHERE period_id=?"
            params = (period_id,)
        sql += " ORDER BY period_id, meter_id"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@router.get("/estimate-corrections")
def list_corrections(period_id: str | None = None):
    conn = connect()
    try:
        sql = "SELECT * FROM estimate_corrections"
        params: tuple = ()
        if period_id:
            sql += " WHERE period_id=?"
            params = (period_id,)
        sql += " ORDER BY created_at DESC, correction_id DESC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _meter_exists(conn, meter_id: str) -> None:
    if not conn.execute("SELECT 1 FROM meters WHERE meter_id=?", (meter_id,)).fetchone():
        raise HTTPException(404, "表具不存在")


@router.post("/estimates")
def create_estimate(body: EstimateIn):
    def work(conn):
        # 写闸由真实状态驱动：封印拒写；lock_on_actual 下已锁拒写
        assert_estimate_mutable(conn, body.meter_id, body.period_id)
        _meter_exists(conn, body.meter_id)
        eid = _new_id("E")
        try:
            conn.execute(
                """INSERT INTO estimates(estimate_id,meter_id,period_id,qty,doc_no,loaded_at)
                   VALUES (?,?,?,?,?,?)""",
                (eid, body.meter_id, body.period_id, body.qty, body.doc_no, _now()),
            )
        except Exception as e:
            raise HTTPException(409, f"估抄写入失败: {e}") from e
        expired = expire_open_batches(conn, body.period_id)
        return {"estimate_id": eid, "ok": True, "expired_batches": expired}

    return with_write_tx(work)


@router.post("/estimates/revise")
def revise_estimate(body: EstimateReviseIn):
    """覆盖修订估抄本体。lock_on_actual 下首笔实抄后会被写闸拒绝；
    editable_until_sealed 下封印前始终允许，成功即失效该期 open 对齐批。"""

    def work(conn):
        assert_estimate_mutable(conn, body.meter_id, body.period_id)
        _meter_exists(conn, body.meter_id)
        target = conn.execute(
            """SELECT * FROM estimates WHERE meter_id=? AND period_id=?
               ORDER BY loaded_at DESC, estimate_id DESC LIMIT 1""",
            (body.meter_id, body.period_id),
        ).fetchone()
        if not target:
            raise HTTPException(404, "该表该账期无估抄可修订，请先装载估抄")
        conn.execute(
            "UPDATE estimates SET qty=?, doc_no=COALESCE(?, doc_no), loaded_at=? WHERE estimate_id=?",
            (body.qty, body.doc_no, _now(), target["estimate_id"]),
        )
        expired = expire_open_batches(conn, body.period_id)
        return {
            "estimate_id": target["estimate_id"],
            "ok": True,
            "qty": body.qty,
            "expired_batches": expired,
            "policy": get_policy(conn),
        }

    return with_write_tx(work)


@router.post("/estimates/corrections")
def create_correction(body: CorrectionIn):
    """订正链路：锁定后纠错只追加订正，不动估抄本体，对齐仍以锁定时估抄为准。"""

    def work(conn):
        assert_period_open(conn, body.period_id)
        _meter_exists(conn, body.meter_id)
        policy = get_policy(conn)
        if policy != "lock_on_actual":
            raise HTTPException(409, "当前口径为封印前可改，估抄请直接修订，无需订正")
        if not is_locked(conn, body.meter_id, body.period_id):
            raise HTTPException(409, "估抄尚未被实抄锁定，可直接修订，无需订正")
        target = conn.execute(
            """SELECT * FROM estimates WHERE meter_id=? AND period_id=?
               ORDER BY loaded_at DESC, estimate_id DESC LIMIT 1""",
            (body.meter_id, body.period_id),
        ).fetchone()
        if not target:
            raise HTTPException(404, "该表该账期无估抄可订正")
        cid = _new_id("C")
        conn.execute(
            """INSERT INTO estimate_corrections
               (correction_id,meter_id,period_id,locked_qty,corrected_qty,reason,doc_no,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                cid,
                body.meter_id,
                body.period_id,
                target["qty"],
                body.corrected_qty,
                body.reason,
                body.doc_no,
                _now(),
            ),
        )
        # 订正不改对齐输入，不触发批失效；返回锁定量便于前端同口径提示
        return {
            "correction_id": cid,
            "ok": True,
            "locked_qty": target["qty"],
            "corrected_qty": body.corrected_qty,
        }

    return with_write_tx(work)


@router.post("/actuals")
def create_actual(body: ActualIn):
    def work(conn):
        # 封印闸在事务内判定，与锁标记写入原子提交
        assert_period_open(conn, body.period_id)
        _meter_exists(conn, body.meter_id)
        aid = _new_id("A")
        try:
            conn.execute(
                """INSERT INTO actuals(actual_id,meter_id,period_id,qty,occurred_at,reader_code,doc_no,loaded_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    aid,
                    body.meter_id,
                    body.period_id,
                    body.qty,
                    body.occurred_at,
                    body.reader_code,
                    body.doc_no,
                    _now(),
                ),
            )
        except Exception as e:
            raise HTTPException(409, f"实抄写入失败: {e}") from e

        policy = get_policy(conn)
        locked_now = False
        if policy == "lock_on_actual":
            # 首笔实抄到达事件：打锁标。INSERT OR IGNORE + 同一事务保证并发下不会半截无锁标
            cur = conn.execute(
                """INSERT OR IGNORE INTO estimate_locks(meter_id,period_id,locked_by_actual,locked_at)
                   VALUES (?,?,?,?)""",
                (body.meter_id, body.period_id, aid, _now()),
            )
            locked_now = cur.rowcount > 0
        # 任一口径下，实抄变化都使旧 open 对齐批失效，防止对齐沿用旧实抄快照
        expired = expire_open_batches(conn, body.period_id)
        return {
            "actual_id": aid,
            "ok": True,
            "policy": policy,
            "estimate_locked": policy == "lock_on_actual",
            "locked_now": locked_now,
            "expired_batches": expired,
        }

    return with_write_tx(work)


@router.get("/actuals")
def list_actuals(period_id: str | None = None):
    conn = connect()
    try:
        if period_id:
            rows = conn.execute(
                "SELECT * FROM actuals WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM actuals ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
