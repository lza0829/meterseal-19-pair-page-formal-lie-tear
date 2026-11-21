"""配对投影与正式双侧键集合撕裂：两种配对可见性口径各自核对。

全局只贯彻一种口径（settings.pair_visibility_policy）：
  formal_intersection 以正式估抄∩实抄键集合为准：页面/总览由正式双侧重放，
      页有正式无则回滚半截配对投影，禁止只改页面缓存。
  pair_projection     以对齐配对投影表为准：投影有而行缺剔幽灵，投影缺而行齐补投影，
      总览已配跟投影，禁止行齐投影卡住，并发半截必须收敛。

夹具：页有库无、库齐页未配、交错半截、处置后页绿库拧。
铁律：禁止回执绿了双侧键与投影仍分叉。
"""
from __future__ import annotations

import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import app.config as config
import app.db as db
from app.db import connect
from app.main import app
from app.seed import seed_if_empty
from conftest import PERIOD, METER, add_actual, add_estimate, align, set_policy
from test_migration import LEGACY_SCHEMA

PAIR_KEY = "pair_visibility_policy"


# ---- 口径与端点辅助 ----

def set_pair_policy(c, value):
    r = c.put(f"/api/settings/{PAIR_KEY}", json={"value": value})
    assert r.status_code == 200, r.text
    return r


def get_pairs(c, period=PERIOD):
    r = c.get("/api/pairs", params={"period_id": period})
    assert r.status_code == 200, r.text
    return r.json()


def repair(c, period=PERIOD):
    r = c.post("/api/pairs/repair", json={"period_id": period})
    assert r.status_code == 200, r.text
    return r.json()


def overview_paired(c):
    return c.get("/api/overview").json()["paired_count"]


# ---- 夹具：直接库操作制造带外撕裂 ----

def tear_ghost(period, meter):
    """页有库无：投影里塞幽灵键，正式双侧并没有。"""
    conn = connect()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO pair_projection
               (period_id,meter_id,status,source_batch_id,updated_at)
               VALUES (?,?,'paired',NULL,'2026-01-01T00:00:00+00:00')""",
            (period, meter),
        )
        conn.commit()
    finally:
        conn.close()


def tear_missing(period, meter):
    """库齐页未配：正式双侧齐，投影行被抹。"""
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM pair_projection WHERE period_id=? AND meter_id=?",
            (period, meter),
        )
        conn.commit()
    finally:
        conn.close()


def drop_formal_actual(period, meter):
    """删一侧正式：实抄被带外删除，投影原地成幽灵。"""
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM actuals WHERE period_id=? AND meter_id=?", (period, meter)
        )
        conn.commit()
    finally:
        conn.close()


def projection_keys(period):
    conn = connect()
    try:
        return {
            r["meter_id"]
            for r in conn.execute(
                "SELECT meter_id FROM pair_projection WHERE period_id=?", (period,)
            ).fetchall()
        }
    finally:
        conn.close()


# ---- 口径本身：默认值与白名单 ----

def test_default_pair_policy_is_formal_intersection(client):
    s = client.get("/api/settings").json()
    assert s[PAIR_KEY] == "formal_intersection"


def test_pair_policy_whitelist_rejects_dirty_value(client):
    r = client.put(f"/api/settings/{PAIR_KEY}", json={"value": "page_cache"})
    assert r.status_code == 400
    # 口径未被写脏，绝不静默落到第三种行为
    assert client.get("/api/settings").json()[PAIR_KEY] == "formal_intersection"
    set_pair_policy(client, "pair_projection")
    assert client.get("/api/settings").json()[PAIR_KEY] == "pair_projection"


# ---- 夹具一：页有库无 ----

def test_formal_caliber_delete_one_side_page_not_green_then_rollback(client):
    """正式架构：删一侧正式后页不得仍绿；修复即回滚半截配对投影。"""
    set_pair_policy(client, "formal_intersection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    align(client)
    assert get_pairs(client)["consistent"] is True
    base = overview_paired(client)

    drop_formal_actual(PERIOD, METER)  # 正式一侧被删 → 投影原地成幽灵
    page = get_pairs(client)
    assert page["pair_policy"] == "formal_intersection"
    assert page["pairs"] == []  # 页面由正式双侧实时重放：页不得仍绿
    assert page["paired_count"] == 0
    assert page["diverged"] is True  # 分叉显式报出，绝不装绿
    assert [k["meter_id"] for k in page["ghost_keys"]] == [METER]
    # 总览已配跟正式交集，不跟页面缓存
    assert overview_paired(client) == base - 1

    r = repair(client)
    assert r["action"] == "rollback_projection"
    assert r["removed"] == 1 and r["added"] == 0
    assert r["consistent"] is True
    assert projection_keys(PERIOD) == set()  # 半截投影已回滚
    assert get_pairs(client)["consistent"] is True


def test_projection_caliber_ghost_flagged_then_purged_and_overview_follows(client):
    """投影架构：页有库无→幽灵显式标记；剔幽灵后总览必跟投影。"""
    set_pair_policy(client, "pair_projection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    align(client)
    base = overview_paired(client)

    tear_ghost(PERIOD, "M-GHOST-1")
    page = get_pairs(client)
    assert page["diverged"] is True
    assert page["consistent"] is False
    ghost_rows = [p for p in page["pairs"] if p["meter_id"] == "M-GHOST-1"]
    assert ghost_rows and ghost_rows[0]["formal_present"] is False  # 幽灵不装绿
    # 总览已配跟投影：幽灵在修复前也计入
    assert overview_paired(client) == base + 1

    r = repair(client)
    assert r["action"] == "converge_projection"
    assert r["removed"] == 1
    assert get_pairs(client)["consistent"] is True
    assert overview_paired(client) == base  # 剔幽灵后总览必跟


# ---- 夹具二：库齐页未配 ----

def test_formal_caliber_library_aligned_page_replays_from_formal(client):
    """正式架构：库齐页未配不可能停留在页面上——页面由正式双侧重放自给。"""
    set_pair_policy(client, "formal_intersection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    tear_missing(PERIOD, METER)  # 投影缺，正式齐
    page = get_pairs(client)
    assert [p["meter_id"] for p in page["pairs"]] == [METER]  # 正式重放自给已配
    assert page["paired_count"] == 1
    assert page["diverged"] is True  # 投影缺键仍显式报分叉
    assert [k["meter_id"] for k in page["missing_keys"]] == [METER]

    r = repair(client)
    assert r["action"] == "rollback_projection"
    assert r["added"] == 1  # 投影回滚对齐正式
    assert get_pairs(client)["consistent"] is True


def test_projection_caliber_backfill_then_overview_follows(client):
    """投影架构：投影缺而行齐→补投影；补投影后总览必跟。"""
    set_pair_policy(client, "pair_projection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    tear_missing(PERIOD, METER)
    page = get_pairs(client)
    assert page["pairs"] == []  # 投影为准：未补前页面不认、总览不计
    assert page["diverged"] is True
    assert [k["meter_id"] for k in page["missing_keys"]] == [METER]
    base = overview_paired(client)

    r = repair(client)
    assert r["action"] == "converge_projection"
    assert r["added"] == 1
    page2 = get_pairs(client)
    assert page2["consistent"] is True
    assert page2["paired_count"] == 1
    assert overview_paired(client) == base + 1  # 补投影后总览必跟


# ---- 装载口联动：禁止行齐投影卡住 ----

def test_load_entry_maintains_projection_inline_under_projection_caliber(client):
    set_pair_policy(client, "pair_projection")
    r1 = add_estimate(client, qty=10.0)
    assert r1["paired_now"] is False
    assert projection_keys(PERIOD) == set()  # 只有一侧，未配

    r2 = add_actual(client, qty=10.0)
    assert r2.json()["paired_now"] is True
    # 未跑对齐，投影也不卡：装载口同事务补投影
    assert projection_keys(PERIOD) == {METER}
    page = get_pairs(client)
    assert page["consistent"] is True
    assert page["paired_count"] == 1


# ---- 夹具三：交错半截 ----

def test_concurrent_two_loads_projection_matches_formal(client):
    """并发装载两笔：投影与正式不抢写，提交即收敛。"""
    # 配对投影与估抄锁口径正交：用可改口径排除锁闸干扰，专注投影-正式并发
    set_policy(client, "editable_until_sealed")
    barrier = threading.Barrier(2)
    outcomes = {}

    def post_estimate():
        barrier.wait()
        c = TestClient(app)
        try:
            outcomes["est"] = c.post(
                "/api/estimates",
                json={"meter_id": METER, "period_id": PERIOD, "qty": 10.0, "doc_no": "EST-CC"},
            )
        finally:
            c.close()

    def post_actual():
        barrier.wait()
        c = TestClient(app)
        try:
            outcomes["act"] = c.post(
                "/api/actuals",
                json={
                    "meter_id": METER,
                    "period_id": PERIOD,
                    "qty": 10.0,
                    "occurred_at": "2026-09-28T10:00:00",
                    "reader_code": "R01",
                    "doc_no": "ACT-CC",
                },
            )
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(post_estimate)
        f2 = ex.submit(post_actual)
        f1.result()
        f2.result()

    assert outcomes["est"].status_code == 200, outcomes["est"].text
    assert outcomes["act"].status_code == 200, outcomes["act"].text
    # 无论谁先谁后：正式双侧齐 ⇔ 投影恰有一行，无半截
    assert projection_keys(PERIOD) == {METER}
    page = get_pairs(client)
    assert page["consistent"] is True
    assert page["paired_count"] == 1


def test_interleaved_loads_and_align_converge(client):
    """装载与对齐交错：串行化提交，最终投影==正式交集，不得长期现行半截。"""
    set_pair_policy(client, "pair_projection")
    # 可改口径下实抄不锁估抄，装载恒成功，专注交错收敛
    set_policy(client, "editable_until_sealed")
    meters = [f"M-X-{i:02d}" for i in range(6)]
    conn = connect()
    try:
        for m in meters:
            conn.execute(
                "INSERT INTO meters(meter_id,building,unit_label,meter_type,status) VALUES (?,'T栋','1','water','active')",
                (m,),
            )
        conn.commit()
    finally:
        conn.close()

    def load_estimate(m):
        c = TestClient(app)
        try:
            return c.post(
                "/api/estimates",
                json={"meter_id": m, "period_id": PERIOD, "qty": 10.0, "doc_no": f"E-{m}"},
            ).status_code
        finally:
            c.close()

    def load_actual(m):
        c = TestClient(app)
        try:
            return c.post(
                "/api/actuals",
                json={
                    "meter_id": m,
                    "period_id": PERIOD,
                    "qty": 10.0,
                    "occurred_at": "2026-09-28T10:00:00",
                    "reader_code": "R01",
                    "doc_no": f"A-{m}",
                },
            ).status_code
        finally:
            c.close()

    def align_loop():
        c = TestClient(app)
        try:
            return [
                c.post("/api/align", json={"period_id": PERIOD}).status_code
                for _ in range(4)
            ]
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=13) as ex:
        futs = [ex.submit(load_estimate, m) for m in meters]
        futs += [ex.submit(load_actual, m) for m in meters]
        futs.append(ex.submit(align_loop))
        results = [f.result() for f in futs]

    load_codes = results[:-1]
    align_codes = results[-1]
    assert all(code == 200 for code in load_codes)
    assert all(code in (200, 400) for code in align_codes)  # 早期对齐可能无数据

    # 交错结束：投影==正式交集，页面一致，无半截现行
    assert projection_keys(PERIOD) == set(meters)
    page = get_pairs(client)
    assert page["consistent"] is True
    assert page["paired_count"] == 6
    ov = client.get("/api/overview").json()
    assert ov["pair_diverged"] is False


# ---- 夹具四：处置后页绿库拧 ----

def test_fate_disposition_converges_instead_of_green_over_tear(client):
    """处置入口：回执不得在双侧键与投影分叉时变绿——同事务收敛后再回。"""
    set_pair_policy(client, "pair_projection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=9.0)
    b = align(client)
    v = client.get("/api/variances", params={"batch_id": b["batch_id"]}).json()[0]

    tear_ghost(PERIOD, "M-GHOST-2")
    assert get_pairs(client)["diverged"] is True

    r = client.post(f"/api/variances/{v['variance_id']}/fate", json={"fate": "accept_short"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pair_consistent"] is True  # 处置后页绿 ⇒ 库必不拧
    assert projection_keys(PERIOD) == {METER}  # 幽灵已在处置事务内剔除
    assert get_pairs(client)["consistent"] is True


# ---- 对齐回执：禁止绿了仍分叉 ----

def test_align_receipt_converges_projection_never_green_over_tear(client):
    set_pair_policy(client, "pair_projection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    tear_ghost(PERIOD, "M-GHOST-4")
    tear_missing(PERIOD, METER)

    b = align(client)
    assert b["paired_count"] == 1
    assert b["pair_policy"] == "pair_projection"
    assert b["pair_consistent"] is True  # 回执绿 ⇒ 双侧键与投影必不分叉
    assert projection_keys(PERIOD) == {METER}
    assert get_pairs(client)["consistent"] is True
    # 批头快照已配键数
    batch = client.get("/api/batches", params={"period_id": PERIOD}).json()[0]
    assert batch["paired_count"] == 1


# ---- 封印预检：已配集合与选定口径一致 ----

def test_seal_precheck_paired_set_matches_formal_caliber(client):
    set_pair_policy(client, "formal_intersection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    align(client)
    tear_ghost(PERIOD, "M-GHOST-3")  # 封印前存在幽灵投影

    r = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pair_policy"] == "formal_intersection"
    assert body["paired_count"] == 1  # 预检已配跟正式交集，幽灵不计
    assert body["pair_consistent"] is True
    assert projection_keys(PERIOD) == {METER}  # 封印事务内已收敛


def test_seal_precheck_paired_set_matches_projection_caliber(client):
    set_pair_policy(client, "pair_projection")
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    align(client)
    tear_missing(PERIOD, METER)  # 封印前行齐投影缺

    r = client.post("/api/seal", json={"period_id": PERIOD, "batch_id": None})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pair_policy"] == "pair_projection"
    assert body["paired_count"] == 1  # 先补投影再按投影计数，口径一致
    assert body["pair_consistent"] is True
    assert projection_keys(PERIOD) == {METER}


# ---- 总览：已配计数跟口径，不跟页面缓存 ----

def test_overview_paired_count_source_follows_caliber(client):
    add_estimate(client, qty=10.0)
    add_actual(client, qty=10.0)
    align(client)
    tear_ghost(PERIOD, "M-GHOST-5")

    set_pair_policy(client, "formal_intersection")
    ov_formal = client.get("/api/overview").json()
    assert ov_formal["pair_visibility_policy"] == "formal_intersection"
    formal_n = ov_formal["paired_count"]

    set_pair_policy(client, "pair_projection")
    ov_proj = client.get("/api/overview").json()
    assert ov_proj["paired_count"] == formal_n + 1  # 投影多一个幽灵，总览跟投影
    assert ov_proj["pair_diverged"] is True
    assert ov_proj["pair_ghost_count"] == 1
    assert ov_proj["pair_missing_count"] == 0

    repair(client)
    ov_after = client.get("/api/overview").json()
    assert ov_after["paired_count"] == formal_n
    assert ov_after["pair_diverged"] is False


# ---- 老库迁移：投影表补建、回填、旧页面缓存废弃 ----

def test_legacy_db_backfills_pair_projection(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy-pair.db"
    for mod in (config, db):
        monkeypatch.setattr(mod, "DATA_DIR", pathlib.Path(tmp_path))
        monkeypatch.setattr(mod, "DB_PATH", db_path)

    conn = connect()
    conn.executescript(LEGACY_SCHEMA)
    conn.execute("INSERT INTO meters VALUES ('M-OLD-1','旧栋','1','water','active')")
    conn.execute("INSERT INTO periods(period_id,status) VALUES ('2026-08','open')")
    conn.execute(
        "INSERT INTO estimates VALUES ('E-OLD-1','M-OLD-1','2026-08',10.0,'EST-OLD','2026-08-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO actuals VALUES ('A-OLD-1','M-OLD-1','2026-08',10.0,'2026-08-28T00:00:00','R01','ACT-OLD','2026-08-28T00:05:00')"
    )
    # 旧版 JSON 页面缓存：与正式双侧不一致的脏缓存
    conn.execute(
        "INSERT INTO settings(key,value) VALUES ('pair_projection_v1','{\"pairs\": [{\"period_id\": \"2026-08\", \"meter_id\": \"M-GHOST-OLD\"}], \"paired_count\": 1}')"
    )
    conn.commit()
    conn.close()

    seed_if_empty()

    conn = connect()
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "pair_projection" in tables
        # 正式双侧齐 → 启动收敛补投影
        row = conn.execute(
            "SELECT * FROM pair_projection WHERE period_id='2026-08' AND meter_id='M-OLD-1'"
        ).fetchone()
        assert row is not None
        # 幽灵键不存在于投影（旧 JSON 缓存不作为权威重放）
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM pair_projection WHERE meter_id='M-GHOST-OLD'"
        ).fetchone()["c"] == 0
        # 旧 JSON 页面缓存废弃
        assert conn.execute(
            "SELECT 1 FROM settings WHERE key='pair_projection_v1'"
        ).fetchone() is None
        # 口径设置补齐默认值
        assert conn.execute(
            "SELECT value FROM settings WHERE key='pair_visibility_policy'"
        ).fetchone()["value"] == "formal_intersection"
        # 对齐批补 paired_count 快照列
        batch_cols = {r[1] for r in conn.execute("PRAGMA table_info(reconcile_batches)")}
        assert "paired_count" in batch_cols
    finally:
        conn.close()
