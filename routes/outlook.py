"""Outlook 郵箱池路由"""
from typing import List

from fastapi import APIRouter, HTTPException

from src.outlook_provider import extract_cards_from_98faka, parse_account_text

from .shared import (
    OutlookExtractRequest, OutlookImportTextRequest,
    load_outlook_pool, save_outlook_pool, add_log,
)

router = APIRouter(prefix="/api/outlook", tags=["outlook"])


@router.get("/pool")
async def list_outlook_pool():
    """Outlook 郵箱池列表"""
    pool = load_outlook_pool()
    return [
        {
            "email": a["email"],
            "password": a.get("password", ""),
            "status": a.get("status", "available"),
        }
        for a in pool
    ]


@router.post("/extract")
async def extract_outlook_cards(req: OutlookExtractRequest):
    """從 98faka 卡密提取 Outlook 帳號並加入郵箱池"""
    if not req.card_codes:
        raise HTTPException(400, "請輸入卡號")

    add_log(f"📦 開始提取 {len(req.card_codes)} 張卡密...", "info")

    result = extract_cards_from_98faka(req.card_codes)
    accounts = result.get("accounts", [])

    if not accounts:
        add_log("❌ 卡密提取失敗，未獲取到帳號", "error")
        return {"success": False, "msg": "未提取到帳號", "count": 0}

    pool = load_outlook_pool()
    existing = {a["email"].lower() for a in pool}
    added = 0
    for acc in accounts:
        if acc["email"].lower() not in existing:
            acc["status"] = "available"
            pool.append(acc)
            existing.add(acc["email"].lower())
            added += 1

    save_outlook_pool(pool)
    add_log(f"✅ 提取完成: {len(accounts)} 個帳號，新增 {added} 個", "success")

    return {
        "success": True,
        "msg": f"提取 {len(accounts)} 個帳號，新增 {added} 個到郵箱池",
        "count": added,
        "total": len(accounts),
    }


@router.post("/import-text")
async def import_outlook_text(req: OutlookImportTextRequest):
    """手動匯入 Outlook 帳號文本"""
    accounts = parse_account_text(req.text)
    if not accounts:
        raise HTTPException(400, "未解析到有效帳號")

    pool = load_outlook_pool()
    existing = {a["email"].lower() for a in pool}
    added = 0
    for acc in accounts:
        if acc["email"].lower() not in existing:
            acc["status"] = "available"
            pool.append(acc)
            existing.add(acc["email"].lower())
            added += 1

    save_outlook_pool(pool)
    add_log(f"📧 匯入 Outlook 帳號: 新增 {added} 個", "success")
    return {"success": True, "imported": added}
