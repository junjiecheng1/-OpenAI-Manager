"""Session 刷新共享邏輯

提取自 routes/accounts.py，供「拉 Session」和「註冊後自動刷新」共用。
"""
import json
import os
from typing import Callable, Optional, Tuple

from .shared import find_account, update_account_field, add_log


def _find_outlook_oauth(email: str) -> Tuple[str, str]:
    """從 outlook_pool.jsonl 中查找 OAuth2 參數"""
    pool_file = os.path.join("tokens", "outlook_pool.jsonl")
    if not os.path.exists(pool_file):
        return "", ""
    try:
        with open(pool_file) as f:
            for line in f:
                acc = json.loads(line.strip())
                if acc.get("email", "").lower() == email.lower():
                    return acc.get("ms_id", ""), acc.get("ms_token", "")
    except Exception:
        pass
    return "", ""


def refresh_session_sync(
    email: str,
    password: str,
    inbox_id: str = "",
    is_outlook: bool = False,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """同步刷新帳號 Session（在工作線程中調用）

    Args:
        email: 帳號郵箱
        password: 密碼
        inbox_id: MailSlurp inbox ID（可選）
        is_outlook: 是否為 Outlook 郵箱
        on_progress: 進度回調 (msg, level)

    Returns:
        {"success": bool, "plan": str, "tokens": dict}
    """
    def emit(msg: str, level: str = "info"):
        add_log(msg, level)
        if on_progress:
            on_progress(msg, level)

    email_provider = None
    try:
        from playwright.sync_api import sync_playwright
        from src.chatgpt_login import login_chatgpt

        # 初始化 email provider（收驗證碼用）
        if inbox_id:
            from src.email_service import MailSlurpProvider
            provider = MailSlurpProvider()
            provider.inbox_id = inbox_id
            provider.email_address = email
            email_provider = provider
            emit(f"使用 MailSlurp 收驗證碼 (inbox: {inbox_id[:8]}...)")
        elif is_outlook:
            from src.outlook_provider import OutlookProvider
            # 從 pool 中找 OAuth2 參數
            client_id, refresh_token = _find_outlook_oauth(email)
            email_provider = OutlookProvider(email, password,
                                             client_id=client_id, refresh_token=refresh_token)
            emit("使用 Outlook OAuth2 IMAP 收驗證碼")

        emit("啟動瀏覽器...")

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

            emit("瀏覽器已啟動，開始登入 ChatGPT...")
            tokens = login_chatgpt(page, email, password, email_provider=email_provider)
            emit("登入流程完成，關閉瀏覽器...")
            browser.close()

            if tokens and tokens.get("chatgpt_session_raw"):
                access_token = tokens.get("chatgpt_access_token", "")
                # 從 session 解析 plan
                plan = "free"
                try:
                    session_obj = json.loads(tokens["chatgpt_session_raw"])
                    plan = session_obj.get("account", {}).get("planType", "free")
                except (json.JSONDecodeError, AttributeError):
                    pass

                update_account_field(email, {
                    "chatgpt_session_raw": tokens["chatgpt_session_raw"],
                    "chatgpt_access_token": access_token,
                    "plan_type": plan,
                })
                emit(f"Session 刷新成功 (plan: {plan})", "success")
                return {"success": True, "plan": plan, "tokens": tokens}
            else:
                emit("未獲取到 token", "error")
                return {"success": False, "plan": "free", "tokens": {}}

    except Exception as e:
        emit(f"刷新失敗: {e}", "error")
        return {"success": False, "plan": "free", "tokens": {}}
    finally:
        if email_provider and hasattr(email_provider, "close"):
            email_provider.close()
