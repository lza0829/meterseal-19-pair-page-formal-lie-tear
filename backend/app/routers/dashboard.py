from __future__ import annotations
from fastapi import APIRouter
from app.config import PROJECT_NAME
from app.db import connect
from app.services.gate import get_policy, get_setting
from app.services import pair_view

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health():
    conn = connect()
    try:
        return {
            "ok": True,
            "product": PROJECT_NAME,
            "paired_count": pair_view.overview_paired(conn),
            "pair_visibility_policy": pair_view.get_pair_policy(conn),
        }
    finally:
        conn.close()


@router.get("/overview")
def overview():
    conn = connect()
    try:
        open_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='open'"
        ).fetchone()["c"]
        sealed_periods = conn.execute(
            "SELECT COUNT(*) AS c FROM periods WHERE status='sealed'"
        ).fetchone()["c"]
        meters = conn.execute("SELECT COUNT(*) AS c FROM meters").fetchone()["c"]
        # 总览与对齐页同口径：只统计 open 批上的未决差额，已作废批不再计入
        pending = conn.execute(
            """SELECT COUNT(*) AS c FROM variances v
               JOIN reconcile_batches b ON b.batch_id=v.batch_id
               WHERE v.fate='pending' AND b.status='open'"""
        ).fetchone()["c"]
        current = get_setting("current_period", "")
        policy = get_policy(conn)
        # 有效锁计数与写闸同口径：editable_until_sealed 下历史锁行不生效也不计入总览
        if policy == "lock_on_actual":
            locked_estimates = conn.execute(
                "SELECT COUNT(*) AS c FROM estimate_locks"
            ).fetchone()["c"]
        else:
            locked_estimates = 0
        corrections = conn.execute(
            "SELECT COUNT(*) AS c FROM estimate_corrections"
        ).fetchone()["c"]
        open_batches = conn.execute(
            "SELECT COUNT(*) AS c FROM reconcile_batches WHERE status='open'"
        ).fetchone()["c"]
        expired_batches = conn.execute(
            "SELECT COUNT(*) AS c FROM reconcile_batches WHERE status='expired'"
        ).fetchone()["c"]
        batches = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reconcile_batches ORDER BY created_at DESC, rowid DESC LIMIT 8"
            ).fetchall()
        ]
        # 总览已配计数跟选定口径（正式交集 / 投影表），并暴露分叉告警，不跟页面缓存
        pair_div = pair_view.divergence(conn)
        return {
            "current_period": current,
            "estimate_lock_policy": policy,
            "pair_visibility_policy": pair_view.get_pair_policy(conn),
            "paired_count": pair_view.overview_paired(conn),
            "pair_diverged": pair_div["diverged"],
            "pair_ghost_count": len(pair_div["ghosts"]),
            "pair_missing_count": len(pair_div["missing"]),
            "open_periods": open_periods,
            "sealed_periods": sealed_periods,
            "meter_count": meters,
            "pending_variances": pending,
            "locked_estimates": locked_estimates,
            "correction_count": corrections,
            "open_batch_count": open_batches,
            "expired_batch_count": expired_batches,
            "recent_batches": batches,
        }
    finally:
        conn.close()
