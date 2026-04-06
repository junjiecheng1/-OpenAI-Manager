"""Playwright 瀏覽器自動註冊器 — 編排器

只做流程編排，具體操作委託給:
- oauth.py → OAuth URL + Token 交換
- email_service.py → 郵箱 Provider (Gmail/TempMail/Worker)
- browser_utils.py → 頁面原子操作
- phone_verify.py → 手機驗證
- chatgpt_login.py → ChatGPT 登入
"""
import base64
import json
import os
import random
import string
import time
import urllib.parse
from typing import Dict, Any, Optional

from playwright.sync_api import sync_playwright, Page, BrowserContext

from . import browser_utils
from .chatgpt_login import login_chatgpt
from .config import Config
from .email_service import create_email_provider, EmailProvider
from .logger import get_logger
from .oauth import OAuthParams, build_oauth_url, exchange_token, jwt_payload, save_account_to_gzyi
from .phone_verify import verify_phone

logger = get_logger()


def generate_password(length: int = 12) -> str:
    """生成隨機密碼：字母+數字，12位，字母開頭"""
    first = random.choice(string.ascii_letters)
    rest = ''.join(random.choices(string.ascii_letters + string.digits, k=length - 1))
    return first + rest


class BrowserRegistrar:
    """用 Playwright 瀏覽器自動註冊 OpenAI 帳號"""

    def __init__(self, proxy: Optional[str] = None, headless: bool = True):
        self.proxy = proxy
        self.headless = headless

    def register_one(self) -> Optional[Dict[str, Any]]:
        """註冊一個帳號，自動重試最多 3 次"""
        max_retries = 3
        provider_attempt = 1  # Provider 輪換計數，只有 user_already_exists 才遞增
        for attempt in range(1, max_retries + 1):
            # 普通失敗: 換郵箱不換模式 | user_already_exists: 換模式
            provider = create_email_provider(attempt=provider_attempt)
            email = provider.create_inbox()
            password = generate_password()
            logger.info(f"[第 {attempt}/{max_retries} 次 | Provider#{provider_attempt}] 郵箱: {email} | 密碼: {password}")
            
            user_exists = False
            try:
                with sync_playwright() as p:
                    logger.info("啟動瀏覽器...")
                    launch_args = {
                        "headless": self.headless,
                        "args": [
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                        ],
                    }
                    if self.proxy:
                        launch_args["proxy"] = {"server": self.proxy}
                    browser = p.chromium.launch(**launch_args)
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
                    logger.info("瀏覽器已啟動")
                    
                    result = None
                    try:
                        result = self._do_register(page, context, email, password, provider)
                        if result is not None:
                            logger.info("✅ 註冊成功，關閉瀏覽器")
                            return result
                        # 檢查是否需要換 Provider 模式的錯誤
                        try:
                            page_text = page.inner_text("body")
                        except Exception:
                            page_text = ""
                        switch_keywords = [
                            "user_already_exists", "unsupported_email",
                            "创建帐户失败", "创建账户失败",
                        ]
                        if any(kw in page_text for kw in switch_keywords):
                            provider_attempt += 1
                            logger.warning(f"第 {attempt} 次遇到帳戶錯誤，切換 Provider 模式重試...")
                        else:
                            logger.warning(f"第 {attempt} 次失敗，換郵箱重試...")
                    except Exception as e:
                        import traceback
                        logger.error(f"註冊過程異常: {e}")
                        logger.error(traceback.format_exc())
                        try:
                            page.screenshot(path="debug_screenshot.png")
                        except Exception:
                            pass
                        if attempt >= max_retries:
                            return None
                        logger.warning(f"第 {attempt} 次異常，換郵箱重試...")
                    finally:
                        try:
                            provider.close()
                        except Exception:
                            pass
                        try:
                            browser.close()
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"瀏覽器啟動失敗: {e}")
                if attempt >= max_retries:
                    return None
        logger.error(f"已達最大重試次數 ({max_retries})，放棄")
        return None

    def _do_register(
        self,
        page: Page,
        context: BrowserContext,
        email: str,
        password: str,
        provider: EmailProvider,
    ) -> Optional[Dict[str, Any]]:
        """核心註冊流程"""

        # 1. 構建 OAuth URL 並訪問
        oauth = build_oauth_url()
        logger.info("訪問 OAuth 授權頁面...")
        page.goto(oauth.auth_url, wait_until="domcontentloaded", timeout=60000)
        browser_utils.wait_cloudflare(page)

        # 2. 切換到註冊頁
        logger.info(f"當前頁面: {page.url}")
        signup = page.query_selector('a:has-text("Sign up"), a:has-text("注册"), a:has-text("Create account")')
        if signup:
            logger.info("在登錄頁，點擊 Sign up...")
            signup.click()
            time.sleep(2)

        # 3. 填郵箱
        if not browser_utils.fill_email(page, email):
            return None

        # 4. 填密碼
        if not browser_utils.wait_password_page(page):
            logger.error("未找到密碼輸入框")
            return None
        if not browser_utils.fill_password(page, password):
            return None

        # 5. 等待跳轉到驗證碼頁面
        logger.info("等待頁面跳轉到驗證碼頁面...")
        account_exists = False
        exists_keywords = [
            "帐户已存在", "账户已存在", "already exists",
            "与此电子邮件地址相关联的帐户已存在",
            "user_already_exists",
        ]
        for i in range(20):
            time.sleep(1)
            url = page.url
            if "email-verification" in url or "verify" in url:
                logger.info(f"已跳轉到驗證碼頁面: {url}")
                break
            # 每輪檢查是否帳戶已存在（提前退出）
            try:
                page_text = page.inner_text("body")
            except Exception:
                page_text = ""
            if any(kw in page_text for kw in exists_keywords):
                logger.info("帳戶已存在，切換到忘記密碼流程...")
                account_exists = True
                break
            if i % 5 == 4:
                logger.info(f"等待中... 當前 URL: {url}")
        else:
            logger.info(f"未自動跳轉，當前 URL: {page.url}")
            if browser_utils.check_registration_error(page):
                return None

        # 5.5 帳戶已存在 → 走忘記密碼流程
        if account_exists:
            return self._fallback_to_login(page, context, email, password, provider, oauth)

        # 6. 獲取郵箱驗證碼
        browser_utils.click_resend(page, "開始時")
        otp_code = provider.wait_for_otp(email, timeout=240, interval=5)
        if not otp_code:
            logger.error("240 秒超時，未能獲取驗證碼")
            page.screenshot(path="debug_no_otp.png")
            return None
        logger.info(f"獲取到驗證碼: {otp_code}")

        # 7. 填入驗證碼
        if not browser_utils.fill_otp(page, otp_code):
            return None
        time.sleep(3)
        browser_utils.wait_otp_accepted(page)

        # 7.5 檢查 OTP 提交後是否報錯
        if browser_utils.check_registration_error(page):
            # 再次檢查是否帳戶已存在
            try:
                page_text = page.inner_text("body")
            except Exception:
                page_text = ""
            exists_keywords = ["帐户已存在", "账户已存在", "already exists", "user_already_exists"]
            if any(kw in page_text for kw in exists_keywords):
                logger.info("OTP 後提示帳戶已存在，切換到登錄...")
                return self._fallback_to_login(page, context, email, password, provider, oauth)
            logger.warning("⚠️ OTP 驗證後報錯，換郵箱重試")
            return None

        # 8. 填寫用戶資料
        browser_utils.fill_profile(page)

        # 9. 手機驗證
        time.sleep(2)
        if not verify_phone(page):
            return None

        # 10. 註冊 callback 監聽 + 點同意按鈕
        captured = browser_utils.setup_callback_listener(page)
        logger.info(f"當前步驟 URL: {page.url}")
        browser_utils.click_consent(page)

        # 11. 等待 callback
        logger.info("等待 OAuth callback 重定向...")
        callback_url = browser_utils.wait_callback(page, captured)
        
        if not callback_url:
            callback_url = self._try_workspace_fallback(page, context, email, password)
        
        if not callback_url:
            logger.error("未獲取到 callback URL")
            return None

        # 12. 提取 code 並交換 token
        logger.info(f"Callback URL: {callback_url}")
        parsed = urllib.parse.urlparse(callback_url)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [""])[0]
        
        if not code:
            logger.error("未從 callback 中提取到 code")
            return None

        logger.info("交換 Token...")
        try:
            token_resp = exchange_token(code, oauth)
        except Exception as e:
            logger.error(f"Token 交換失敗: {e}")
            return None

        # 13. 組裝結果
        result = self._build_result(token_resp, email, password)

        # 14. 保存到 gzyi.top
        if oauth.use_gzyi:
            save_account_to_gzyi(token_resp, email)
        
        # 15. ChatGPT 登入（在保存前執行，這樣可以一起保存 session）
        chatgpt_tokens = login_chatgpt(page, email, password)
        result.update(chatgpt_tokens)

        # 16. 保存到本地（包含 session + inbox_id）
        self._save_account(email, password, chatgpt_tokens, provider)

        logger.info(f"✅ 註冊成功！郵箱: {result.get('email', email)}")
        return result

    def _fallback_to_login(
        self, page: Page, context: BrowserContext,
        email: str, password: str, provider: EmailProvider, oauth
    ) -> Optional[Dict[str, Any]]:
        """帳戶已存在時，走忘記密碼流程重置密碼後登錄"""
        logger.info(f"🔄 帳戶已存在，走忘記密碼流程: {email}")
        try:
            # 1. 到登錄頁
            page.goto("https://auth.openai.com/log-in", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            browser_utils.wait_cloudflare(page)

            # 2. 填郵箱
            if not browser_utils.fill_email(page, email):
                logger.error("忘記密碼：填郵箱失敗")
                return None

            # 3. 等密碼頁出現，點忘記密碼
            if not browser_utils.wait_password_page(page):
                logger.error("忘記密碼：未找到密碼頁")
                return None

            forgot = page.query_selector(
                'a:has-text("忘记密码"), a:has-text("忘记了密码"), '
                'a:has-text("Forgot password"), a:has-text("forgot"), '
                'button:has-text("忘记密码"), button:has-text("忘记了密码")'
            )
            if not forgot:
                logger.error("忘記密碼：未找到忘記密碼鏈接")
                return None
            forgot.click()
            logger.info("已點擊忘記密碼")
            time.sleep(3)

            # 4. 確認在重置頁面，點「繼續」發送重置郵件
            current_url = page.url
            logger.info(f"忘記密碼頁 URL: {current_url}")

            # 有些頁面需要再輸入郵箱
            email_input = page.query_selector('input[name="email"], input[type="email"]')
            if email_input:
                email_input.fill(email)
                time.sleep(0.5)

            # 點繼續/提交按鈕（發送重置郵件）
            submit = page.query_selector(
                'button[type="submit"], button:has-text("Continue"), '
                'button:has-text("继续"), button:has-text("繼續")'
            )
            if submit:
                submit.click()
                logger.info("已點擊繼續，等待重置郵件...")
                time.sleep(3)
            else:
                logger.warning("未找到繼續按鈕")

            # 5. 收 OTP 驗證碼（跟註冊一樣）
            logger.info("等待 OTP 驗證碼...")
            otp_code = provider.wait_for_otp(email, timeout=180, interval=5)
            if not otp_code:
                logger.error("忘記密碼：180 秒超時，未收到驗證碼")
                return None
            logger.info(f"收到驗證碼: {otp_code}")

            # 6. 填入驗證碼
            if not browser_utils.fill_otp(page, otp_code):
                logger.error("忘記密碼：填驗證碼失敗")
                return None
            time.sleep(3)
            browser_utils.wait_otp_accepted(page)
            logger.info(f"驗證碼已提交，當前 URL: {page.url}")

            # 7. 設新密碼（重置頁面會出現密碼輸入框）
            time.sleep(2)
            pwd_inputs = page.query_selector_all('input[type="password"]')
            if pwd_inputs:
                for inp in pwd_inputs:
                    inp.fill(password)
                    time.sleep(0.3)
                submit = page.query_selector(
                    'button[type="submit"], button:has-text("Reset"), '
                    'button:has-text("重置"), button:has-text("Continue"), '
                    'button:has-text("继续")'
                )
                if submit:
                    submit.click()
                    time.sleep(3)
                logger.info("密碼已重置為我們的密碼")
            else:
                logger.info("無密碼輸入框，可能已直接登錄")

            # 8. 拿 session
            logger.info(f"當前 URL: {page.url}")
            from src.chatgpt_login import login_chatgpt
            chatgpt_tokens = login_chatgpt(page, email, password, email_provider=provider)

            self._save_account(email, password, chatgpt_tokens, provider)

            result = {
                "email": email,
                "password": password,
                "source": "reset_password_fallback",
            }
            if chatgpt_tokens:
                result.update(chatgpt_tokens)
            logger.info(f"✅ 重置密碼並登錄成功: {email}")
            return result
        except Exception as e:
            logger.error(f"忘記密碼流程失敗: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None

    def _try_workspace_fallback(
        self, page: Page, context: BrowserContext, email: str, password: str
    ) -> Optional[str]:
        """嘗試從 cookie 中獲取 workspace 並觸發 callback"""
        logger.info(f"最終 URL: {page.url}")
        auth_cookie = None
        for cookie in context.cookies():
            if cookie["name"] == "oai-client-auth-session":
                auth_cookie = cookie["value"]
                break
        
        if not auth_cookie:
            return None
        
        logger.info("找到 auth session cookie，嘗試獲取 workspace...")
        self._save_account(email, password)
        
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

    def _save_account(self, email: str, password: str, chatgpt_tokens: dict = None, provider=None):
        """保存帳號到 accounts.jsonl（包含 session + inbox_id）"""
        output_dir = Config.TOKEN_OUTPUT_DIR or "./tokens"
        os.makedirs(output_dir, exist_ok=True)
        accounts_file = os.path.join(output_dir, "accounts.jsonl")
        record = {
            "email": email,
            "password": password,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "plan_type": "free",
        }
        # 保存 ChatGPT session（Plus 開通需要）
        if chatgpt_tokens:
            record["chatgpt_session_raw"] = chatgpt_tokens.get("chatgpt_session_raw", "")
            record["chatgpt_access_token"] = chatgpt_tokens.get("chatgpt_access_token", "")
        # 保存郵箱 provider 信息（後續登入收驗證碼需要）
        if provider:
            record["email_provider"] = provider.__class__.__name__
            if hasattr(provider, "inbox_id") and provider.inbox_id:
                record["mailslurp_inbox_id"] = provider.inbox_id
        with open(accounts_file, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(f"✅ 帳號已保存: {accounts_file}")

    @staticmethod
    def _build_result(token_resp: Dict, email: str, password: str) -> Dict[str, Any]:
        """從 token 響應組裝結果"""
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
