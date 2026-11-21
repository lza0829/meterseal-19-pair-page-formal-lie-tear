from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timezone
from app.services import pair_view

from fastapi import HTTPException

from app.db import connect
from app.services.gate import (
    assert_period_open,
    get_policy,
    lock_set,
    with_write_tx,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_id(prefix: str) -> str:
    return prefix + "-" + uuid.uuid4().hex[:10]


def run_align(period_id: str) -> dict:
    def work(conn):
        assert_period_open(conn, period_id)
        policy = get_policy(conn)

        # 对齐输入：每个表该账期只取最新一笔估抄（与估抄修订覆盖语义一致）
        est_rows = conn.execute(
            """SELECT e.* FROM estimates e
               JOIN (
                 SELECT meter_id, MAX(loaded_at) AS max_loaded
                 FROM estimates WHERE period_id=? GROUP BY meter_id
               ) m ON m.meter_id=e.meter_id AND m.max_loaded=e.loaded_at
               WHERE e.period_id=?""",
            (period_id, period_id),
        ).fetchall()
        est = {r["meter_id"]: r for r in est_rows}
        act = {
            r["meter_id"]: r
            for r in conn.execute(
                "SELECT * FROM actuals WHERE period_id=?", (period_id,)
            ).fetchall()
        }
        keys = sorted(set(est) | set(act))
        if not keys:
            raise HTTPException(400, "该账期无估抄/实抄可对齐")
        # 已配键集合：正式估抄∩正式实抄，配对页与对齐批必须能由正式双侧重放
        paired_keys = sorted(set(est) & set(act))

        # 锁标记快照：对齐瞬间该表该账期是否已被首笔实抄锁定
        # 有效锁受当前口径门控：editable_until_sealed 下即便存在历史锁行也不视为锁定
        raw_locked = lock_set(conn, set(keys), period_id)
        locked = raw_locked if policy == "lock_on_actual" else set()

        # 先失效该期既有 open 批，再物化新批，杜绝“写成功却沿用旧估抄”
        conn.execute(
            "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status='open'",
            (period_id,),
        )

        batch_id = _new_id("B")
        matched = 0
        variances: list[tuple] = []
        for mid in keys:
            eq = est[mid]["qty"] if mid in est else None
            aq = act[mid]["qty"] if mid in act else None
            if eq is not None and aq is not None and abs(eq - aq) < 1e-9:
                matched += 1
                continue
            # 差额分桶：实抄缺、估抄缺、短量、溢量（桶由 delta 与两侧空值决定，落 fate 前可处置）
            delta = (aq if aq is not None else 0.0) - (eq if eq is not None else 0.0)
            variances.append(
                (
                    _new_id("V"),
                    batch_id,
                    mid,
                    period_id,
                    eq,
                    aq,
                    delta,
                    "pending",
                    1 if mid in locked else 0,
                )
            )

        fp_src = (
            f"{period_id}|{policy}|{matched}|{len(variances)}|"
            + ",".join(f"{k}:{1 if k in locked else 0}" for k in keys)
        )
        fingerprint = hashlib.sha256(fp_src.encode()).hexdigest()[:16]
        conn.execute(
            """INSERT INTO reconcile_batches
               (batch_id,period_id,status,matched_count,variance_count,paired_count,fingerprint,created_at,policy,locked_count)
               VALUES (?,?,'open',?,?,?,?,?,?,?)""",
            (batch_id, period_id, matched, len(variances), len(paired_keys), fingerprint, _now(), policy, len(locked)),
        )
        if variances:
            conn.executemany(
                """INSERT INTO variances
                   (variance_id,batch_id,meter_id,period_id,estimate_qty,actual_qty,delta,fate,estimate_locked)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                variances,
            )
        # 配对投影与对齐批同一事务物化：剔幽灵 + 补缺失，回执不得绿了仍分叉
        pair_view.sync_period(conn, period_id, source_batch_id=batch_id)
        if pair_view.divergence(conn, period_id)["diverged"]:
            raise HTTPException(500, "配对投影收敛失败：双侧键与投影仍分叉")
        return {
            "batch_id": batch_id,
            "period_id": period_id,
            "matched_count": matched,
            "variance_count": len(variances),
            "paired_count": len(paired_keys),
            "fingerprint": fingerprint,
            "status": "open",
            "policy": policy,
            "locked_count": len(locked),
            "pair_policy": pair_view.get_pair_policy(conn),
            "pair_consistent": True,
        }

    return with_write_tx(work)


def seal_period(period_id: str, batch_id: str | None = None) -> dict:
    def work(conn):
        period = conn.execute(
            "SELECT * FROM periods WHERE period_id=?", (period_id,)
        ).fetchone()
        if not period:
            raise HTTPException(404, "账期不存在")
        if period["status"] == "sealed":
            raise HTTPException(409, "账期已封印")

        selected_batch_id = batch_id
        if selected_batch_id:
            batch = conn.execute(
                "SELECT * FROM reconcile_batches WHERE batch_id=?", (selected_batch_id,)
            ).fetchone()
            if not batch or batch["period_id"] != period_id:
                raise HTTPException(400, "对账批与账期不匹配")
            if batch["status"] != "open":
                raise HTTPException(409, "对账批不可用（已作废或已封印），请重新对齐")
        else:
            batch = conn.execute(
                """SELECT * FROM reconcile_batches WHERE period_id=? AND status='open'
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (period_id,),
            ).fetchone()
            if not batch:
                raise HTTPException(400, "无 open 对账批，请先对齐")
            selected_batch_id = batch["batch_id"]

        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM variances WHERE batch_id=? AND fate='pending'",
            (selected_batch_id,),
        ).fetchone()["c"]
        # 封印预检：同事务先收敛投影，已配集合按选定口径取数，口径与集合必须一致
        pair_view.sync_period(conn, period_id)
        if pair_view.divergence(conn, period_id)["diverged"]:
            raise HTTPException(500, "配对投影收敛失败：双侧键与投影仍分叉")
        pair_policy = pair_view.get_pair_policy(conn)
        paired = pair_view.paired_count(conn, period_id)
        sealed_at = _now()
        conn.execute(
            "UPDATE periods SET status='sealed', sealed_at=?, batch_no=? WHERE period_id=?",
            (sealed_at, selected_batch_id, period_id),
        )
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed' WHERE batch_id=?",
            (selected_batch_id,),
        )
        return {
            "period_id": period_id,
            "batch_id": selected_batch_id,
            "sealed_at": sealed_at,
            "pending_left": pending,
            "status": "sealed",
            "paired_count": paired,
            "pair_policy": pair_policy,
            "pair_consistent": True,
        }

    return with_write_tx(work)


def set_fate(variance_id: str, fate: str) -> dict:
    if fate not in ("pending", "accept_short", "accept_over", "quarantine"):
        raise HTTPException(400, "非法命运")

    def work(conn):
        row = conn.execute(
            "SELECT v.*, p.status AS period_status FROM variances v JOIN periods p ON p.period_id=v.period_id WHERE v.variance_id=?",
            (variance_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "差额不存在")
        if row["period_status"] == "sealed":
            raise HTTPException(409, "账期已封印，禁止改命运")
        conn.execute("UPDATE variances SET fate=? WHERE variance_id=?", (fate, variance_id))
        # 差额入口联动：处置同事务收敛该期投影，处置后页绿必须库不拧
        pair_view.sync_period(conn, row["period_id"])
        if pair_view.divergence(conn, row["period_id"])["diverged"]:
            raise HTTPException(500, "配对投影收敛失败：双侧键与投影仍分叉")
        return {
            "variance_id": variance_id,
            "fate": fate,
            "paired_count": pair_view.paired_count(conn, row["period_id"]),
            "pair_policy": pair_view.get_pair_policy(conn),
            "pair_consistent": True,
        }

    return with_write_tx(work)
