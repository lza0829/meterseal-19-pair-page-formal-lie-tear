from __future__ import annotations
import sqlite3

from fastapi import HTTPException

from app.db import connect
from app.seed import DEFAULT_POLICY, VALID_POLICIES


def get_setting(key: str, default: str = "") -> str:
    conn = connect()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def get_policy(conn: sqlite3.Connection) -> str:
    """当前全局唯一口径。库里被写脏时回退默认口径，绝不静默走第二种逻辑。"""
    row = conn.execute(
        "SELECT value FROM settings WHERE key='estimate_lock_policy'"
    ).fetchone()
    value = row["value"] if row else DEFAULT_POLICY
    return value if value in VALID_POLICIES else DEFAULT_POLICY


def assert_policy_valid(policy: str) -> None:
    if policy not in VALID_POLICIES:
        raise HTTPException(400, f"非法估抄锁口径: {policy}")


def get_period(conn: sqlite3.Connection, period_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM periods WHERE period_id=?", (period_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, f"账期不存在: {period_id}")
    return row


def assert_period_open(conn: sqlite3.Connection, period_id: str) -> sqlite3.Row:
    """封印闸：两架构共用，已封印一律拒写。"""
    period = get_period(conn, period_id)
    if period["status"] == "sealed":
        raise HTTPException(409, f"账期已封印，禁止写入: {period_id}")
    return period


def assert_period_writable(period_id: str) -> None:
    conn = connect()
    try:
        assert_period_open(conn, period_id)
    finally:
        conn.close()


def is_locked(conn: sqlite3.Connection, meter_id: str, period_id: str) -> bool:
    """锁标记以 estimate_locks 表为准（仅 lock_on_actual 口径会有行）。"""
    row = conn.execute(
        "SELECT 1 FROM estimate_locks WHERE meter_id=? AND period_id=? LIMIT 1",
        (meter_id, period_id),
    ).fetchone()
    return row is not None


def lock_set(conn: sqlite3.Connection, meter_ids: set[str], period_id: str) -> set[str]:
    if not meter_ids:
        return set()
    placeholders = ",".join("?" * len(meter_ids))
    rows = conn.execute(
        f"SELECT meter_id FROM estimate_locks WHERE period_id=? AND meter_id IN ({placeholders})",
        (period_id, *meter_ids),
    ).fetchall()
    return {r["meter_id"] for r in rows}


def assert_estimate_mutable(conn: sqlite3.Connection, meter_id: str, period_id: str) -> None:
    """估抄写入口的统一写闸，由真实状态驱动，不允许只改展示文案绕过。"""
    assert_period_open(conn, period_id)
    policy = get_policy(conn)
    if policy == "lock_on_actual" and is_locked(conn, meter_id, period_id):
        raise HTTPException(
            409,
            "该表该账期已存在正式实抄，估抄已锁定，禁止再改；如需纠错请走估抄订正链路",
        )
    # editable_until_sealed：未封印即可改；封印闸在上面已拦


def expire_open_batches(conn: sqlite3.Connection, period_id: str) -> int:
    """估抄/实抄任一侧变化后，该期 open 对齐批整体失效，杜绝对齐仍用旧快照。"""
    cur = conn.execute(
        "UPDATE reconcile_batches SET status='expired' WHERE period_id=? AND status='open'",
        (period_id,),
    )
    return cur.rowcount


def begin_immediate(conn: sqlite3.Connection) -> None:
    """显式写事务：并发的实抄与改估抄必须串行化，禁止出现半截无锁标的状态。"""
    conn.execute("BEGIN IMMEDIATE")


def with_write_tx(work):
    """统一事务包装：SQLITE_BUSY 做有限重试，其余异常回滚后原样抛出。"""
    conn = connect()
    try:
        last_err: Exception | None = None
        for _ in range(20):
            try:
                begin_immediate(conn)
                result = work(conn)
                conn.commit()
                return result
            except sqlite3.OperationalError as e:  # database is locked
                last_err = e
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
            except Exception:
                conn.rollback()
                raise
        raise HTTPException(503, f"数据库繁忙，请重试: {last_err}")
    finally:
        conn.close()
