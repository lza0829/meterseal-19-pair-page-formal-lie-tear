from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import connect
from app.seed import DEFAULT_PAIR_POLICY, DEFAULT_POLICY, VALID_PAIR_POLICIES, VALID_POLICIES

router = APIRouter(tags=["settings"])


class SettingIn(BaseModel):
    value: str


@router.get("/settings")
def list_settings():
    conn = connect()
    try:
        data = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}
        data.setdefault("estimate_lock_policy", DEFAULT_POLICY)
        data.setdefault("pair_visibility_policy", DEFAULT_PAIR_POLICY)
        return data
    finally:
        conn.close()


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingIn):
    # 口径切换走白名单：全局只贯彻两种架构之一，写脏值必须拒绝而不是静默落到第三种行为
    if key == "estimate_lock_policy" and body.value not in VALID_POLICIES:
        allowed = ", ".join(VALID_POLICIES)
        raise HTTPException(400, f"非法估抄锁口径，允许值: {allowed}")
    if key == "pair_visibility_policy" and body.value not in VALID_PAIR_POLICIES:
        allowed = ", ".join(VALID_PAIR_POLICIES)
        raise HTTPException(400, f"非法配对可见性口径，允许值: {allowed}")
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, body.value),
        )
        conn.commit()
        return {"key": key, "value": body.value}
    finally:
        conn.close()
