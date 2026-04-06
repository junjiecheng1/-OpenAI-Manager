"""ChatGPT 獨立登入流程

用已有的郵箱密碼登入 ChatGPT 並抓取 session token。
可在 Codex 注冊後調用，也可獨立調用。
"""
import json
import time
from typing import Dict

from playwright.sync_api import Page

from . import browser_utils
from .logger import get_logger

logger = get_logger()


def login_chatgpt(page: Page, email: str, password: str, email_provider=None) -> Dict[str, str]:
    """登入 ChatGPT 並獲取 session token
    
    Args:
        email_provider: 可選的郵箱 Provider（用於接收驗證碼）
    
    返回 {"chatgpt_access_token": "...", "chatgpt_session_token": "..."}
    失敗返回空 dict。
    """
    try:
        logger.info("🔐 開始 ChatGPT 登入流程...")
        
        # 先嘗試直接抓 session（萬一已登入）
        tokens = _fetch_session(page)
        if tokens:
            return tokens
        
        # 未登入，走完整登錄
        _do_login(page, email, password, email_provider=email_provider)
        return _fetch_session(page)
    except Exception as e:
        logger.warning(f"ChatGPT 登入失敗: {e}")
        return {}


def _do_login(page: Page, email: str, password: str, email_provider=None):
    """直接跳 auth.openai.com 登入 ChatGPT"""
    
    # 直接構建 ChatGPT 的 OAuth 登入 URL，跳過首頁
    logger.info("直接跳轉 ChatGPT 登入頁...")
    page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)
    logger.info(f"跳轉後 URL: {page.url}")
    
    # 如果停在 chatgpt.com（沒跳轉），嘗試點 Log in
    if "auth.openai.com" not in page.url:
        login_btn = page.query_selector(
            'button:has-text("Log in"), a:has-text("Log in"), '
            'button:has-text("登录"), a:has-text("登录"), '
            '[data-testid="login-button"]'
        )
        if login_btn:
            login_btn.click()
            logger.info("已點擊 Log in 按鈕")
        
        # 等跳轉到 auth.openai.com
        for _ in range(15):
            time.sleep(1)
            if "auth.openai.com" in page.url:
                break
    
    logger.info(f"等待跳轉後 URL: {page.url}")
    
    # 如果在登入頁、看到 Sign up，點 Log in（切回登入）
    if "create-account" in page.url:
        login_link = page.query_selector('a:has-text("Log in")')
        if login_link:
            login_link.click()
            time.sleep(2)
    
    # 等郵箱輸入框
    try:
        page.wait_for_selector(
            'input[name="email"], input[type="email"], input[id="email-input"]',
            timeout=10000
        )
    except Exception:
        logger.warning(f"未等到郵箱輸入框，當前 URL: {page.url}")
    
    # 填郵箱
    email_input = page.query_selector('input[name="email"], input[type="email"], input[id="email-input"]')
    if email_input:
        email_input.fill(email)
        logger.info(f"ChatGPT 已輸入郵箱: {email}")
        # 用精確選擇器：name="intent" value="email" 的按鈕
        btn = page.query_selector('button[name="intent"][value="email"]')
        if not btn:
            btn = page.query_selector('button[type="submit"]')
        if btn:
            btn.click()
        else:
            email_input.press("Enter")
        time.sleep(3)
        logger.info(f"提交郵箱後 URL: {page.url}")
        
        # 新版流程: 提交郵箱後可能停在多個頁面
        clicked_pwd = False
        for _ in range(15):
            url = page.url
            if "password" in url:
                break  # 已到密碼頁
            
            # 1. log-in-or-create-account 中間頁 → 點 email 繼續按鈕
            if "log-in-or-create-account" in url:
                try:
                    page.click('button[name="intent"][value="email"]', timeout=3000)
                    logger.info("已點擊「繼續(email)」按鈕")
                    time.sleep(3)
                    continue
                except Exception:
                    pass
            
            # 2. email-verification 頁 → 先嘗試點「使用密碼繼續」
            if "email-verification" in url and not clicked_pwd:
                try:
                    page.click(
                        'button:has-text("密码"), a:has-text("密码"), '
                        'button:has-text("password"), a:has-text("password")',
                        timeout=3000
                    )
                    clicked_pwd = True
                    logger.info("已點擊「使用密碼繼續」")
                    # 等待導航到密碼頁
                    try:
                        page.wait_for_url("**/password**", timeout=10000)
                    except Exception:
                        time.sleep(3)
                    continue
                except Exception:
                    pass
                
                # 沒有密碼按鈕 → 需要驗證碼
                if email_provider:
                    logger.info("需要驗證碼，通過 email_provider 收取...")
                    otp = email_provider.wait_for_otp(email, timeout=120, interval=5)
                    if otp:
                        # 找驗證碼輸入框
                        otp_input = page.query_selector('input[type="text"], input[name="code"], input[inputmode="numeric"]')
                        if otp_input:
                            otp_input.fill(otp)
                            logger.info(f"已填入驗證碼: {otp}")
                            time.sleep(1)
                            submit = page.query_selector('button[type="submit"]')
                            if submit:
                                submit.click()
                            else:
                                otp_input.press("Enter")
                            time.sleep(5)
                            continue
                    else:
                        logger.warning("未收到驗證碼")
            
            time.sleep(1)
        
        logger.info(f"等待密碼頁，當前 URL: {page.url}")
        
        # 等密碼框
        try:
            page.wait_for_selector('input[type="password"]', timeout=15000)
        except Exception:
            logger.warning(f"未等到密碼框，當前 URL: {page.url}")
        
        # 填密碼（label 遮擋會導致 click 超時，用 JS focus 繞過）
        pwd = page.query_selector('input[type="password"], input[name="password"]')
        if pwd:
            page.evaluate("el => el.focus()", pwd)
            time.sleep(0.3)
            pwd.fill(password)
            time.sleep(0.5)
            logger.info("ChatGPT 已輸入密碼")
            pwd.press("Enter")
            time.sleep(5)
            logger.info(f"提交密碼後 URL: {page.url}")

            # 密碼登錄後可能有郵箱二次驗證
            if "email-verification" in page.url:
                logger.info("登錄後需要郵箱二次驗證...")
                _handle_email_verification(page, email, email_provider)
        else:
            logger.warning("ChatGPT 未找到密碼輸入框")
    else:
        logger.warning("ChatGPT 未找到郵箱輸入框")

    # 等頁面穩定，處理可能出現的中間頁面
    time.sleep(3)

    # 處理用戶資料頁（首次登入可能要填姓名等）
    _handle_post_login_pages(page)

    logger.info(f"ChatGPT 登入完成 URL: {page.url}")


def _handle_email_verification(page: Page, email: str, email_provider=None):
    """處理登錄後的郵箱二次驗證（OTP）"""
    if not email_provider:
        logger.warning("無 email_provider，無法自動完成郵箱二次驗證")
        return

    # 收驗證碼
    logger.info("通過 email_provider 收取驗證碼...")
    otp = email_provider.wait_for_otp(email, timeout=120, interval=5)
    if not otp:
        logger.warning("郵箱二次驗證：未收到驗證碼")
        return

    logger.info(f"收到驗證碼: {otp}")

    # 填入驗證碼
    if browser_utils.fill_otp(page, otp):
        time.sleep(3)
        browser_utils.wait_otp_accepted(page)
        logger.info(f"郵箱二次驗證完成，當前 URL: {page.url}")
    else:
        logger.warning("郵箱二次驗證：填驗證碼失敗")


def _handle_post_login_pages(page: Page):
    """處理登入後可能出現的中間頁面（姓名、條款等）
    
    複用 browser_utils.fill_profile 和 click_consent
    """
    for _ in range(5):
        try:
            # 等頁面穩定（防止導航中查詢崩潰）
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            time.sleep(2)
            continue

        try:
            url = page.url

            # 已到 ChatGPT 主頁，不用處理
            if "chatgpt.com" in url and "auth" not in url:
                break

            # 用戶資料頁（姓名、生日等）
            name_input = page.query_selector('input[name="name"], input[name="full_name"], input[name="firstName"]')
            if name_input:
                browser_utils.fill_profile(page)
                continue

            # 條款/同意按鈕
            if browser_utils.click_consent(page, max_attempts=1):
                continue

            # 其他情況等一下
            time.sleep(2)
        except Exception as e:
            # 導航中 context destroyed 等異常，等頁面穩定
            logger.info(f"頁面跳轉中，等待穩定... ({e.__class__.__name__})")
            time.sleep(3)


def _fetch_session(page: Page) -> Dict[str, str]:
    """訪問 session API 抓取 token
    
    返回包含完整 session 的 dict:
    - chatgpt_access_token
    - chatgpt_session_token  
    - chatgpt_session_raw (完整 JSON，Plus 開通需要)
    """
    page.goto("https://chatgpt.com/api/auth/session", wait_until="domcontentloaded", timeout=15000)
    time.sleep(2)
    text = page.inner_text("body")
    if text and text.strip().startswith("{"):
        data = json.loads(text.strip())
        access = data.get("accessToken", "")
        session = data.get("sessionToken", "")
        if access:
            logger.info("✅ 已抓取 ChatGPT session token")
            return {
                "chatgpt_access_token": access,
                "chatgpt_session_token": session,
                "chatgpt_session_raw": text.strip(),
            }
        logger.info("ChatGPT session 中無 accessToken，需要登入")
    else:
        logger.info("ChatGPT session 返回空或非 JSON，需要登入")
    return {}
