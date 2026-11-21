from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services import pair_view

from app.db import connect
from app.seed import PAIR_FORMAL
from app.services import reconcile_service
from app.services.gate import get_period, with_write_tx

router = APIRouter(tags=["reconcile"])


class AlignIn(BaseModel):
    period_id: str


class PeriodIn(BaseModel):
    period_id: str


class SealIn(BaseModel):
    period_id: str
    batch_id: str | None = None


class FateIn(BaseModel):
    fate: str


@router.post("/align")
def align(body: AlignIn):
    return reconcile_service.run_align(body.period_id)


@router.get("/batches")
def batches(period_id: str | None = None):
    conn = connect()
    try:
        # created_at 为秒级精度，同秒并列时按 rowid 倒序，保证「最新批」确定性
        if period_id:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches WHERE period_id=? ORDER BY created_at DESC, rowid DESC",
                (period_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.get("/variances")
def variances(batch_id: str | None = None, period_id: str | None = None):
    conn = connect()
    try:
        if batch_id:
            rows = conn.execute(
                "SELECT * FROM variances WHERE batch_id=? ORDER BY meter_id", (batch_id,)
            ).fetchall()
        elif period_id:
            rows = conn.execute(
                "SELECT * FROM variances WHERE period_id=? ORDER BY meter_id", (period_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM variances ORDER BY period_id, meter_id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/variances/{variance_id}/fate")
def set_fate(variance_id: str, body: FateIn):
    return reconcile_service.set_fate(variance_id, body.fate)


@router.post("/seal")
def seal(body: SealIn):
    return reconcile_service.seal_period(body.period_id, body.batch_id)


@router.post("/periods")
def create_period(body: PeriodIn):
    def work(conn):
        exists = conn.execute(
            "SELECT 1 FROM periods WHERE period_id=?", (body.period_id,)
        ).fetchone()
        if exists:
            raise HTTPException(409, f"账期已存在: {body.period_id}")
        conn.execute(
            "INSERT INTO periods(period_id,status) VALUES (?,'open')", (body.period_id,)
        )
        return {"period_id": body.period_id, "status": "open"}

    return with_write_tx(work)


@router.get("/periods")
def periods():
    conn = connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM periods ORDER BY period_id DESC").fetchall()]
    finally:
        conn.close()


@router.get("/pairs")
def pair_page(period_id: str | None = None):
    """配对页：按全局口径出数；任何分叉都显式报出，绝不回执绿了仍分叉。"""
    conn = connect()
    try:
        div = pair_view.divergence(conn, period_id)
        return {
            "period_id": period_id,
            "pair_policy": pair_view.get_pair_policy(conn),
            "pairs": pair_view.page_pairs(conn, period_id),
            "paired_count": pair_view.paired_count(conn, period_id),
            "formal_intersection_count": pair_view.formal_count(conn, period_id),
            "projection_count": pair_view.projection_count(conn, period_id),
            "diverged": div["diverged"],
            "ghost_keys": div["ghosts"],
            "missing_keys": div["missing"],
            "consistent": not div["diverged"],
        }
    finally:
        conn.close()


class RepairIn(BaseModel):
    period_id: str


@router.post("/pairs/repair")
def repair_pairs(body: RepairIn):
    """按口径收敛撕裂：formal=回滚半截配对投影；projection=剔幽灵+补投影。

    正式读数是计量事实，投影不能反向捏造正式行，故修复只收敛投影侧。
    """

    def work(conn):
        get_period(conn, body.period_id)
        policy = pair_view.get_pair_policy(conn)
        result = pair_view.sync_period(conn, body.period_id)
        if pair_view.divergence(conn, body.period_id)["diverged"]:
            raise HTTPException(500, "配对投影收敛失败：双侧键与投影仍分叉")
        return {
            "period_id": body.period_id,
            "pair_policy": policy,
            "action": "rollback_projection" if policy == PAIR_FORMAL else "converge_projection",
            "added": result["added"],
            "removed": result["removed"],
            "paired_count": pair_view.paired_count(conn, body.period_id),
            "consistent": True,
        }

    return with_write_tx(work)
