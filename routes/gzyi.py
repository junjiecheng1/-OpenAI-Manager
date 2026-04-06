"""gzyi.top 同步路由"""
import json
from pathlib import Path
from typing import List

import httpx
from fastapi import APIRouter, HTTPException

from .shared import (
    GzyiAccountOut, GzyiImportRequest,
    find_account, add_log,
    GZYI_API_URL, GZYI_HEADERS, TOKENS_DIR,
)

router = APIRouter(prefix="/api/gzyi", tags=["gzyi"])


def _fetch_gzyi_accounts() -> list:
    """獲取 gzyi.top 帳號列表"""
    if not GZYI_API_URL:
        return []
    try:
        resp = httpx.get(
            f"{GZYI_API_URL}/admin/openai-accounts",
            headers=GZYI_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) if isinstance(data, dict) else data
    except Exception:
        return []


@router.get("/accounts")
async def list_gzyi_accounts():
    """gzyi.top 帳號列表"""
    return _fetch_gzyi_accounts()


@router.post("/import")
async def import_to_gzyi(req: GzyiImportRequest):
    """導入帳號到 gzyi.top"""
    if not GZYI_API_URL:
        raise HTTPException(400, "未配置 GZYI_API_URL")

    acc = find_account(req.email)
    if not acc:
        raise HTTPException(404, f"帳號 {req.email} 不存在")
    if acc.get("plan_type") != "plus":
        raise HTTPException(400, "只有 Plus 帳號才能導入 gzyi.top")

    # 從 token 文件讀取 OAuth 數據
    token_data = _find_token_file(req.email)
    if not token_data:
        raise HTTPException(400, f"找不到 {req.email} 的 token 文件")

    payload = {
        "email": req.email,
        "name": req.email,
        "description": "",
        "accountType": "shared",
        "proxy": None,
        "openaiOauth": {
            "idToken": token_data.get("id_token", ""),
            "accessToken": token_data.get("access_token", ""),
            "refreshToken": token_data.get("refresh_token", ""),
            "expires_in": 864000,
        },
        "accountInfo": {},
        "priority": 50,
    }

    add_log(f"🌐 開始導入 gzyi.top: {req.email}", "info")
    try:
        resp = httpx.post(
            f"{GZYI_API_URL}/admin/openai-accounts",
            json=payload,
            headers=GZYI_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("success"):
            add_log(f"✅ 已導入 gzyi.top: {req.email}", "success")
            return {"success": True, "msg": f"已導入 gzyi.top: {req.email}"}
        add_log(f"❌ gzyi 返回失敗: {result.get('msg', '')}", "error")
        return {"success": False, "msg": f"gzyi 返回: {result.get('msg', '未知錯誤')}"}
    except Exception as e:
        raise HTTPException(500, f"gzyi.top 請求失敗: {e}")


def _find_token_file(email: str) -> dict | None:
    """從 tokens/ 找對應的 token JSON"""
    if not TOKENS_DIR.exists():
        return None
    for f in sorted(TOKENS_DIR.glob("token_*.json"), reverse=True):
        try:
            data = json.loads(f.read_text())
            if data.get("email", "").lower() == email.lower():
                return data
        except (json.JSONDecodeError, IOError):
            continue
    return None


@router.post("/reauth")
async def reauth_gzyi_account(req: GzyiImportRequest):
    """重新授權 gzyi 帳號（走瀏覽器 OAuth 登入拿新 token）"""
    if not GZYI_API_URL:
        raise HTTPException(400, "未配置 GZYI_API_URL")

    acc = find_account(req.email)
    if not acc:
        raise HTTPException(404, f"帳號 {req.email} 不存在")

    password = acc.get("password", "")
    if not password:
        raise HTTPException(400, f"帳號 {req.email} 沒有密碼記錄")

    import threading

    def _run():
        from playwright.sync_api import sync_playwright
        from src.account_authorizer import authorize_account

        add_log(f"🔄 開始重新授權: {req.email}", "info")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                    locale="zh-CN",
                )
                context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                """)
                page = context.new_page()

                # 初始化 email provider（用於二次驗證）
                email_provider = _init_email_provider(req.email, password)

                result = authorize_account(
                    page, context, req.email, password,
                    source="reauth",
                    save_to_gzyi=True,
                    save_local=True,
                    email_provider=email_provider,
                )

                browser.close()

                if result.success:
                    add_log(f"✅ 重新授權成功: {req.email}", "success")
                else:
                    add_log(f"❌ 重新授權失敗: {result.error}", "error")
        except Exception as e:
            add_log(f"❌ 重新授權異常: {e}", "error")

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"success": True, "msg": f"已啟動重新授權: {req.email}"}


def _init_email_provider(email: str, password: str):
    """根據郵箱類型初始化 provider"""
    if "@outlook" in email.lower() or "@hotmail" in email.lower():
        from src.outlook_provider import OutlookProvider
        from .session_service import _find_outlook_oauth
        client_id, refresh_token = _find_outlook_oauth(email)
        return OutlookProvider(email, password,
                               client_id=client_id, refresh_token=refresh_token)
    return None

