"""配对投影与正式双侧键集合：两种配对可见性口径的联动实现。

全局不变量：任意写事务提交时，pair_projection == 正式估抄∩实抄键集合。
口径只决定「配对可见性听谁」，不允许任何一侧长期撕裂：

  formal_intersection 以正式估抄∩实抄键集合为准
      配对页/总览由正式双侧实时重放；页有库无立即不绿；
      修复 = 回滚半截配对投影；禁止只改页面缓存。
  pair_projection     以对齐配对投影表为准
      配对页/总览读物化投影；投影有而行缺剔幽灵（正式读数是计量事实，
      投影不能反向捏造正式行）；投影缺而行齐补投影；总览已配跟投影；
      禁止行齐投影卡住；并发半截必须收敛。
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.seed import (
    DEFAULT_PAIR_POLICY,
    PAIR_FORMAL,
    PAIR_PROJECTION,
    VALID_PAIR_POLICIES,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_pair_policy(conn) -> str:
    """当前全局唯一配对可见性口径。库里被写脏时回退默认口径，绝不静默走第二种逻辑。"""
    row = conn.execute(
        "SELECT value FROM settings WHERE key='pair_visibility_policy'"
    ).fetchone()
    value = row["value"] if row else DEFAULT_PAIR_POLICY
    return value if value in VALID_PAIR_POLICIES else DEFAULT_PAIR_POLICY


def _formal_pairs(conn, period_id: str | None = None) -> set[tuple[str, str]]:
    """正式双侧键集合：(账期, 表号) 同时在正式估抄表与正式实抄表中出现。"""
    if period_id:
        rows = conn.execute(
            """SELECT period_id, meter_id FROM estimates WHERE period_id=?
               INTERSECT
               SELECT period_id, meter_id FROM actuals WHERE period_id=?""",
            (period_id, period_id),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT period_id, meter_id FROM estimates
               INTERSECT
               SELECT period_id, meter_id FROM actuals"""
        ).fetchall()
    return {(r["period_id"], r["meter_id"]) for r in rows}


def _projection_pairs(conn, period_id: str | None = None) -> set[tuple[str, str]]:
    if period_id:
        rows = conn.execute(
            "SELECT period_id, meter_id FROM pair_projection WHERE period_id=?",
            (period_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT period_id, meter_id FROM pair_projection"
        ).fetchall()
    return {(r["period_id"], r["meter_id"]) for r in rows}


def formal_meter_ids(conn, period_id: str) -> set[str]:
    return {m for (_p, m) in _formal_pairs(conn, period_id)}


def projection_meter_ids(conn, period_id: str) -> set[str]:
    return {m for (_p, m) in _projection_pairs(conn, period_id)}


def formal_count(conn, period_id: str | None = None) -> int:
    return len(_formal_pairs(conn, period_id))


def projection_count(conn, period_id: str | None = None) -> int:
    return len(_projection_pairs(conn, period_id))


def divergence(conn, period_id: str | None = None) -> dict:
    """投影与正式双侧的分叉：幽灵=投影有正式缺；缺失=正式齐投影缺。"""
    formal = _formal_pairs(conn, period_id)
    proj = _projection_pairs(conn, period_id)
    ghosts = sorted(proj - formal)
    missing = sorted(formal - proj)
    return {
        "ghosts": [{"period_id": p, "meter_id": m} for p, m in ghosts],
        "missing": [{"period_id": p, "meter_id": m} for p, m in missing],
        "diverged": bool(ghosts or missing),
    }


def _apply_sync(conn, ghosts, missing, source_batch_id: str | None = None) -> dict:
    now = _now()
    if ghosts:
        conn.executemany(
            "DELETE FROM pair_projection WHERE period_id=? AND meter_id=?",
            sorted(ghosts),
        )
    if missing:
        conn.executemany(
            """INSERT OR IGNORE INTO pair_projection
               (period_id,meter_id,status,source_batch_id,updated_at)
               VALUES (?,?,'paired',?,?)""",
            [(p, m, source_batch_id, now) for (p, m) in sorted(missing)],
        )
    return {"added": len(missing), "removed": len(ghosts)}


def sync_period(conn, period_id: str, source_batch_id: str | None = None) -> dict:
    """收敛一个账期：投影 := 正式交集（剔幽灵 + 补缺失），与调用方同一事务。"""
    formal = _formal_pairs(conn, period_id)
    proj = _projection_pairs(conn, period_id)
    result = _apply_sync(conn, proj - formal, formal - proj, source_batch_id)
    if source_batch_id is not None:
        conn.execute(
            "UPDATE pair_projection SET source_batch_id=?, updated_at=? WHERE period_id=?",
            (source_batch_id, _now(), period_id),
        )
    return result


def sync_all_periods(conn) -> dict:
    """启动收敛：全量按正式双侧重建，交错半截不得长期现行。"""
    formal = _formal_pairs(conn)
    proj = _projection_pairs(conn)
    return _apply_sync(conn, proj - formal, formal - proj)


def sync_key(conn, period_id: str, meter_id: str) -> bool:
    """装载口联动：单笔正式行变化后同步一个键；返回该键当前是否已配。"""
    row = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM estimates WHERE period_id=? AND meter_id=?) AS e,
             (SELECT COUNT(*) FROM actuals   WHERE period_id=? AND meter_id=?) AS a""",
        (period_id, meter_id, period_id, meter_id),
    ).fetchone()
    both = row["e"] > 0 and row["a"] > 0
    if both:
        conn.execute(
            """INSERT OR IGNORE INTO pair_projection
               (period_id,meter_id,status,source_batch_id,updated_at)
               VALUES (?,?,'paired',NULL,?)""",
            (period_id, meter_id, _now()),
        )
    else:
        conn.execute(
            "DELETE FROM pair_projection WHERE period_id=? AND meter_id=?",
            (period_id, meter_id),
        )
    return both


def page_pairs(conn, period_id: str | None = None) -> list[dict]:
    """配对页数据源：formal 口径由正式双侧实时重放；projection 口径读物化表。"""
    policy = get_pair_policy(conn)
    formal = _formal_pairs(conn, period_id)
    if period_id:
        proj_rows = conn.execute(
            "SELECT * FROM pair_projection WHERE period_id=? ORDER BY period_id, meter_id",
            (period_id,),
        ).fetchall()
    else:
        proj_rows = conn.execute(
            "SELECT * FROM pair_projection ORDER BY period_id, meter_id"
        ).fetchall()
    proj_by_key = {(r["period_id"], r["meter_id"]): r for r in proj_rows}
    rows: list[dict] = []
    if policy == PAIR_FORMAL:
        # 以正式估抄∩实抄键集合为准：页面必须能由正式双侧重放，投影只作批号/时刻注记
        for key in sorted(formal):
            prow = proj_by_key.get(key)
            rows.append(
                {
                    "period_id": key[0],
                    "meter_id": key[1],
                    "status": "paired",
                    "source": "formal_replay",
                    "formal_present": True,
                    "source_batch_id": prow["source_batch_id"] if prow else None,
                    "updated_at": prow["updated_at"] if prow else None,
                }
            )
    else:
        # 以配对投影表为准：页面读物化投影，幽灵行显式标记正式缺侧，绝不装绿
        for key in sorted(proj_by_key):
            prow = proj_by_key[key]
            rows.append(
                {
                    "period_id": key[0],
                    "meter_id": key[1],
                    "status": "paired",
                    "source": "projection",
                    "formal_present": key in formal,
                    "source_batch_id": prow["source_batch_id"],
                    "updated_at": prow["updated_at"],
                }
            )
    return rows


def paired_count(conn, period_id: str | None = None) -> int:
    """已配计数跟口径：formal 跟正式交集，projection 跟投影表。"""
    if get_pair_policy(conn) == PAIR_PROJECTION:
        return len(_projection_pairs(conn, period_id))
    return len(_formal_pairs(conn, period_id))


def overview_paired(conn) -> int:
    """总览已配计数：与配对页同口径，不跟任何页面缓存。"""
    return paired_count(conn, None)
