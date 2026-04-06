"""卡密路由: 列表、導入、Plus 開通"""
import json
from typing import List

from fastapi import APIRouter, HTTPException

from src.plus_upgrade import check_cdkey, activate_plus, get_next_cdkey, _mark_used

from .shared import (
    CdkeyOut, CdkeyImportRequest, ActivateRequest,
    load_cdkeys, find_account, update_account_field, add_log,
    CDKEYS_FILE,
)

router = APIRouter(prefix="/api", tags=["cdkeys"])


@router.get("/cdkeys", response_model=List[CdkeyOut])
async def list_cdkeys():
    """卡密列表（先查本地 used 文件，再調 API 查真實狀態）"""
    all_keys, used = load_cdkeys()
    result = []
    for k in all_keys:
        if k in used:
            result.append(CdkeyOut(cdkey=k, status="used"))
        else:
            # 調 API 查真實狀態
            try:
                status = check_cdkey(k)
                if status.use_status == 1:
                    # API 說已用，同步到本地
                    _mark_used(k)
                    result.append(CdkeyOut(
                        cdkey=k, status="used",
                        gift_name=status.gift_name,
                        account=status.account,
                    ))
                elif status.success:
                    result.append(CdkeyOut(
                        cdkey=k, status="available",
                        gift_name=status.gift_name,
                    ))
                else:
                    result.append(CdkeyOut(cdkey=k, status="invalid"))
            except Exception:
                result.append(CdkeyOut(cdkey=k, status="available"))
    return result


@router.post("/cdkeys/import")
async def import_cdkeys(req: CdkeyImportRequest):
    """導入卡密"""
    keys = [k.strip() for k in req.cdkeys.strip().splitlines() if k.strip()]
    if not keys:
        raise HTTPException(400, "沒有有效卡密")
    existing, _ = load_cdkeys()
    existing_set = set(existing)
    new_keys = [k for k in keys if k not in existing_set]
    if new_keys:
        with open(CDKEYS_FILE, "a") as f:
            for k in new_keys:
                f.write(k + "\n")
    add_log(f"導入 {len(new_keys)} 張卡密", "success")
    return {"imported": len(new_keys)}


@router.post("/plus/activate")
async def activate_plus_route(req: ActivateRequest):
    """Plus 開通"""
    acc = find_account(req.email)
    if not acc:
        raise HTTPException(404, f"帳號 {req.email} 不存在")

    session_raw = acc.get("chatgpt_session_raw", "")
    if not session_raw:
        raise HTTPException(400, f"帳號 {req.email} 沒有 session，需要先刷新 session")

    cdkey = req.cdkey
    if not cdkey:
        cdkey = get_next_cdkey()
        if not cdkey:
            raise HTTPException(400, "沒有可用卡密")
    else:
        status = check_cdkey(cdkey)
        if not status.success or status.use_status != 0:
            raise HTTPException(400, f"卡密 {cdkey} 不可用: {status.msg}")

    add_log(f"開始 Plus 開通: {req.email} (卡密: {cdkey})", "info")
    result = activate_plus(cdkey, session_raw)
    if result.success:
        _mark_used(cdkey)

        # 同步更新 session_raw 中的 planType，確保前端即時顯示
        updates = {"plan_type": "plus"}
        try:
            session_obj = json.loads(session_raw)
            if "account" in session_obj:
                session_obj["account"]["planType"] = "plus"
                updates["chatgpt_session_raw"] = json.dumps(session_obj, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass

        update_account_field(req.email, updates)
        add_log(f"Plus 開通成功: {req.email}", "success")
    else:
        add_log(f"Plus 開通失敗: {req.email} — {result.msg}", "error")

    return {
        "success": result.success,
        "msg": result.msg,
        "cdkey": result.cdkey,
        "account": result.account,
    }
