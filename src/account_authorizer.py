"""帳號授權抽象層

統一「註冊後授權」和「已有帳號重新授權」的 OAuth → Token → 保存流程。
"""
import json
import os
import time
import urllib.parse
from dataclasses import dataclass
from typing import Dict, Any, Optional

from playwright.sync_api import Page, BrowserContext

from . import browser_utils
from .config import Config
from .oauth import (
    OAuthParams, build_oauth_url, exchange_token,
    jwt_payload, save_account_to_gzyi,
)
from .logger import get_logger

logger = get_logger()


@dataclass
class AuthResult:
    """授權結果"""
    success: bool
    email: str
    password: str = ""
    source: str = ""  # "register" | "reauth" | "reset_password"
    tokens: Dict[str, Any] = None
    chatgpt_tokens: Dict[str, str] = None
    error: str = ""

    def __post_init__(self):
        self.tokens = self.tokens or {}
        self.chatgpt_tokens = self.chatgpt_tokens or {}


def authorize_account(
    page: Page,
    context: BrowserContext,
    email: str,
    password: str,
    source: str = "reauth",
    save_to_gzyi: bool = True,
    save_local: bool = True,
    email_provider=None,
) -> AuthResult:
    """完整的帳號授權流程

    1. 構建 OAuth URL（優先 gzyi.top 模式）
    2. 瀏覽器走 OAuth 登入
    3. 交換 token
    4. 保存到 gzyi.top + 本地

    Args:
        page: Playwright 頁面
        context: 瀏覽器 context
        email: 帳號郵箱
        password: 密碼
        source: 來源標記
        save_to_gzyi: 是否推送到 gzyi.top
        save_local: 是否保存到本地
        email_provider: 郵箱 provider（用於二次驗證）

    Returns:
        AuthResult
    """
    try:
        # 1. 構建 OAuth URL
        oauth = build_oauth_url()
        logger.info(f"🔗 授權帳號: {email} (source={source})")

        # 2. 瀏覽器走登入
        page.goto(oauth.auth_url, wait_until="domcontentloaded", timeout=60000)
        browser_utils.wait_cloudflare(page)

        # 填郵箱
        if not browser_utils.fill_email(page, email):
            return AuthResult(success=False, email=email, error="填郵箱失敗")

        # 填密碼
        if not browser_utils.wait_password_page(page):
            return AuthResult(success=False, email=email, error="未找到密碼頁")
        if not browser_utils.fill_password(page, password):
            return AuthResult(success=False, email=email, error="填密碼失敗")

        # 等待登入完成
        time.sleep(5)
        logger.info(f"登入後 URL: {page.url}")

        # 處理可能的郵箱二次驗證
        if "email-verification" in page.url and email_provider:
            logger.info("需要郵箱二次驗證...")
            otp = email_provider.wait_for_otp(email, timeout=120, interval=5)
            if otp:
                browser_utils.fill_otp(page, otp)
                time.sleep(3)
                browser_utils.wait_otp_accepted(page)

        # 3. 設置 callback 監聽 + 走 consent
        captured = browser_utils.setup_callback_listener(page)
        browser_utils.click_consent(page)

        # 等 callback
        logger.info("等待 OAuth callback...")
        callback_url = browser_utils.wait_callback(page, captured)

        if not callback_url:
            # 嘗試從 cookie/workspace 獲取
            callback_url = _try_workspace_callback(page, context)

        if not callback_url:
            return AuthResult(success=False, email=email, error="未獲取到 callback URL")

        # 4. 提取 code 並交換 token
        parsed = urllib.parse.urlparse(callback_url)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]

        if not code:
            return AuthResult(success=False, email=email, error="未提取到 code")

        logger.info("交換 Token...")
        token_resp = exchange_token(code, oauth)

        # 5. 保存到 gzyi.top
        if save_to_gzyi and oauth.use_gzyi:
            save_account_to_gzyi(token_resp, email)

        # 6. 保存到本地
        if save_local:
            _save_token_local(token_resp, email, password)

        # 7. 組裝結果
        result_tokens = _build_token_dict(token_resp, email, password)

        logger.info(f"✅ 授權完成: {email}")
        return AuthResult(
            success=True,
            email=email,
            password=password,
            source=source,
            tokens=result_tokens,
        )

    except Exception as e:
        logger.error(f"授權流程失敗: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return AuthResult(success=False, email=email, error=str(e))


def _build_token_dict(token_resp: Dict, email: str, password: str) -> Dict[str, Any]:
    """從 token 響應組裝標準結果"""
    access_token = token_resp.get("access_token", "")
    refresh_token = token_resp.get("refresh_token", "")
    id_token = token_resp.get("id_token", "")
    expires_in = int(token_resp.get("expires_in", 0))

    claims = jwt_payload(id_token)
    user_email = claims.get("email", email)
    auth = claims.get("https://api.openai.com/auth", {})
    account_id = auth.get("chatgpt_account_id", "")

    now = int(time.time())
    return {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "email": user_email,
        "password": password,
        "type": "codex",
        "expired": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))),
    }


def _save_token_local(token_resp: Dict, email: str, password: str):
    """保存 token 到本地 JSON 文件"""
    output_dir = Config.TOKEN_OUTPUT_DIR or "./tokens"
    os.makedirs(output_dir, exist_ok=True)

    result = _build_token_dict(token_resp, email, password)
    safe_email = email.replace("@", "_at_").replace(".", "_")
    filename = os.path.join(output_dir, f"token_{safe_email}.json")

    with open(filename, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    logger.info(f"✅ Token 已保存: {filename}")


def _try_workspace_callback(page: Page, context: BrowserContext) -> Optional[str]:
    """從 cookie 中嘗試觸發 workspace callback"""
    import base64

    auth_cookie = None
    for cookie in context.cookies():
        if cookie["name"] == "oai-client-auth-session":
            auth_cookie = cookie["value"]
            break

    if not auth_cookie:
        return None

    logger.info("找到 auth session cookie，嘗試獲取 workspace...")
    try:
        auth_data = json.loads(base64.b64decode(auth_cookie.split(".")[0]))
        workspaces = auth_data.get("workspaces", [])
        if workspaces:
            workspace_id = workspaces[0]["id"]
            page.evaluate(f"""
                fetch('https://auth.openai.com/api/accounts/workspace/select', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{workspace_id: '{workspace_id}'}})
                }}).then(r => r.json()).then(data => {{
                    if (data.continue_url) window.location.href = data.continue_url;
                }});
            """)
            time.sleep(5)
            if "localhost:1455/auth/callback" in page.url:
                return page.url
    except Exception as e:
        logger.error(f"Workspace 處理失敗: {e}")
    return None
