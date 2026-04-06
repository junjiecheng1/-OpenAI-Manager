"""OAuth 統一層

支持兩種模式:
1. gzyi.top 後端 — 服務端持有 PKCE verifier，客戶端只走瀏覽器
2. 本地 PKCE — 客戶端自己生成 verifier，直接和 OpenAI 交換 token
"""
import base64
import hashlib
import json
import secrets
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from .config import Config
from .logger import get_logger

logger = get_logger()

# ====================== 常量 ======================
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid email profile offline_access"


# ====================== 數據結構 ======================

@dataclass
class OAuthParams:
    """OAuth 授權參數"""
    auth_url: str
    state: str
    code_verifier: str = ""
    # gzyi.top 模式
    session_id: str = ""
    use_gzyi: bool = False


# ====================== 工具函數 ======================

def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def jwt_payload(token: str) -> dict:
    """解碼 JWT payload（不驗證簽名）"""
    if not token or token.count(".") < 2:
        return {}
    payload_b64 = token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii")))
    except Exception:
        return {}


# ====================== gzyi.top 後端 ======================

def _build_oauth_url_gzyi() -> Optional[OAuthParams]:
    """通過 gzyi.top 後端生成 OAuth URL"""
    api_base = (Config.GZYI_API_URL or "").rstrip("/")
    token = Config.GZYI_TOKEN or ""
    if not api_base or not token:
        return None
    try:
        resp = httpx.post(
            f"{api_base}/admin/openai-accounts/generate-auth-url",
            json={},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("success"):
            auth_url = data["data"]["authUrl"]
            session_id = data["data"]["sessionId"]
            parsed = urllib.parse.urlparse(auth_url)
            params = urllib.parse.parse_qs(parsed.query)
            state = params.get("state", [""])[0]
            logger.info(f"🔗 gzyi.top 生成 OAuth URL (sessionId: {session_id})")
            return OAuthParams(
                auth_url=auth_url,
                state=state,
                session_id=session_id,
                use_gzyi=True,
            )
        logger.warning(f"gzyi.top generate-auth-url 失敗: {data}")
    except Exception as e:
        logger.warning(f"gzyi.top API 不可用: {e}")
    return None


def _exchange_token_gzyi(code: str, session_id: str) -> Dict[str, Any]:
    """通過 gzyi.top 後端用 code 換取 token"""
    api_base = (Config.GZYI_API_URL or "").rstrip("/")
    token = Config.GZYI_TOKEN or ""
    resp = httpx.post(
        f"{api_base}/admin/openai-accounts/exchange-code",
        json={"code": code, "sessionId": session_id},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ====================== 本地 PKCE ======================

def _build_oauth_url_local() -> OAuthParams:
    """本地構建 OAuth PKCE 授權 URL"""
    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _sha256_b64url(code_verifier)
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthParams(auth_url=auth_url, state=state, code_verifier=code_verifier)


def _exchange_token_local(code: str, code_verifier: str) -> Dict[str, Any]:
    """本地用 code + PKCE verifier 換取 token"""
    import urllib.request
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ====================== 統一入口 ======================

def build_oauth_url() -> OAuthParams:
    """構建 OAuth URL（優先 gzyi.top，降級本地 PKCE）"""
    gzyi = _build_oauth_url_gzyi()
    if gzyi:
        return gzyi
    return _build_oauth_url_local()


def exchange_token(code: str, oauth: OAuthParams) -> Dict[str, Any]:
    """用 code 換取 token（根據 OAuth 模式自動選路徑）
    
    統一返回格式: {access_token, refresh_token, id_token, expires_in, account_info?}
    """
    if oauth.use_gzyi:
        logger.info(f"通過 gzyi.top 提交 code (sessionId: {oauth.session_id})")
        resp = _exchange_token_gzyi(code, oauth.session_id)
        logger.info(f"gzyi.top 返回: success={resp.get('success')}")
        # gzyi.top 格式: {success, data: {tokens: {idToken, accessToken, refreshToken, expires_in}, accountInfo: {...}}}
        data = resp.get("data", {})
        tokens = data.get("tokens", {})
        account_info = data.get("accountInfo", {})
        return {
            "access_token": tokens.get("accessToken", ""),
            "refresh_token": tokens.get("refreshToken", ""),
            "id_token": tokens.get("idToken", ""),
            "expires_in": tokens.get("expires_in", 0),
            # 保留原始數據供後續 save 用
            "_gzyi_tokens": tokens,
            "_gzyi_account_info": account_info,
        }
    return _exchange_token_local(code, oauth.code_verifier)


def save_account_to_gzyi(token_resp: Dict[str, Any], email: str) -> bool:
    """將帳號保存到 gzyi.top 後端"""
    api_base = (Config.GZYI_API_URL or "").rstrip("/")
    token = Config.GZYI_TOKEN or ""
    if not api_base or not token:
        return False
    
    gzyi_tokens = token_resp.get("_gzyi_tokens", {})
    account_info = token_resp.get("_gzyi_account_info", {})
    
    if not gzyi_tokens:
        return False
    
    payload = {
        "name": email,
        "description": "",
        "accountType": "shared",
        "proxy": None,
        "openaiOauth": {
            "idToken": gzyi_tokens.get("idToken", ""),
            "accessToken": gzyi_tokens.get("accessToken", ""),
            "refreshToken": gzyi_tokens.get("refreshToken", ""),
            "expires_in": gzyi_tokens.get("expires_in", 0),
        },
        "accountInfo": account_info,
        "priority": 50,
    }
    
    try:
        resp = httpx.post(
            f"{api_base}/admin/openai-accounts",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("success"):
            logger.info("✅ 帳號已保存到 gzyi.top")
            return True
        logger.warning(f"gzyi.top 保存帳號失敗: {result}")
    except Exception as e:
        logger.warning(f"gzyi.top 保存帳號異常: {e}")
    return False
