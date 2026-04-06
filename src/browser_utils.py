"""瀏覽器自動化原子操作

可復用的 Playwright 頁面交互，不含業務邏輯。
"""
import datetime
import random
import time
from typing import List, Optional

from playwright.sync_api import Page

from .logger import get_logger

logger = get_logger()


def wait_cloudflare(page: Page, max_wait: int = 30):
    """等待 Cloudflare challenge 通過"""
    for i in range(max_wait):
        title = page.title()
        if "Just a moment" in title or "Checking" in title:
            logger.info(f"等待 Cloudflare challenge... ({i+1}s)")
            time.sleep(1)
        else:
            break
    time.sleep(2)


def fill_email(page: Page, email: str) -> bool:
    """填寫郵箱並提交，返回是否成功"""
    email_input = page.wait_for_selector(
        'input[name="email"], input[type="email"], input[id="email-input"]',
        timeout=15000,
    )
    if not email_input:
        logger.error("未找到郵箱輸入框")
        return False
    email_input.fill(email)
    time.sleep(0.5)
    _click_submit(page, email_input)
    logger.info(f"已填寫並提交郵箱: {email}")
    return True


def wait_password_page(page: Page, timeout: int = 15000) -> bool:
    """等待密碼頁面載入"""
    time.sleep(3)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    pwd = page.wait_for_selector('input[name="password"], input[type="password"]', timeout=timeout)
    return pwd is not None


def fill_password(page: Page, password: str) -> bool:
    """填寫密碼並提交"""
    pwd_input = page.query_selector('input[name="password"], input[type="password"]')
    if not pwd_input:
        logger.error("未找到密碼輸入框")
        return False
    pwd_input.fill(password)
    time.sleep(0.5)
    _click_submit(page, pwd_input)
    logger.info("已填寫並提交密碼")
    return True


def fill_otp(page: Page, code: str) -> bool:
    """填寫 OTP 驗證碼並提交"""
    selectors = ['input[name="code"]', 'input[type="text"]', 'input[inputmode="numeric"]']
    otp_selector = None
    for sel in selectors:
        if page.query_selector(sel):
            otp_selector = sel
            break
    if not otp_selector:
        logger.error("未找到驗證碼輸入框")
        return False
    page.fill(otp_selector, code)
    logger.info(f"已輸入驗證碼: {code}")
    time.sleep(0.5)
    submit = page.query_selector('button[type="submit"], button:has-text("Continue")')
    if submit:
        submit.click()
    else:
        page.press(otp_selector, "Enter")
    return True


def wait_otp_accepted(page: Page, max_wait: int = 30):
    """等待驗證碼生效（頁面離開 email-verification）"""
    for i in range(max_wait):
        time.sleep(1)
        url = page.url
        if "email-verification" not in url:
            logger.info(f"已離開驗證碼頁面: {url}")
            return
        if page.query_selector('input[name="name"]'):
            logger.info("檢測到用戶資料表單，驗證碼已生效")
            return
        if i % 5 == 4:
            logger.info(f"等待中... 當前 URL: {url}")


def fill_profile(page: Page):
    """填寫用戶資料（隨機姓名 + 安全範圍內的隨機生日）"""
    name_input = page.query_selector('input[name="name"]') or page.query_selector('input[type="text"]')
    if not name_input:
        return

    logger.info("填寫用戶資料...")
    name = _random_name()
    page.fill('input[name="name"], input[type="text"]', name)
    logger.info(f"已填寫名稱: {name}")
    time.sleep(0.5)

    # 生成隨機生日（22-35 歲）
    year, month, day = _random_birthday()

    # 填生日/年齡
    page_text = page.inner_text("body")
    if "年龄" in page_text and "生日" not in page_text:
        age = datetime.datetime.now().year - year
        logger.info(f"檢測到「年齡」模式，填入 {age}")
        el = page.query_selector('input[name="name"], input[type="text"]')
        if el:
            el.press("Tab")
            time.sleep(0.3)
        page.keyboard.type(str(age), delay=100)
    elif "Birthday" in page_text or "birthday" in page_text:
        logger.info("檢測到英文 Birthday 模式")
        _fill_date_inputs(page, [f"{month:02d}", f"{day:02d}", str(year)], ("MM", "DD", "YYYY"))
    else:
        logger.info("檢測到「生日日期」模式")
        _fill_date_inputs(page, [str(year), f"{month:02d}", f"{day:02d}"], ("YYYY", "MM", "DD", "年", "月", "日"))

    time.sleep(1)
    finish = page.query_selector('button:has-text("Finish"), button:has-text("Continue"), button[type="submit"]')
    if finish:
        finish.click()
        logger.info("已點擊 Finish creating account")
    time.sleep(5)


def click_consent(page: Page, max_attempts: int = 3) -> bool:
    """點擊同意/繼續按鈕，返回是否有點擊"""
    clicked = False
    for _ in range(max_attempts):
        btn = page.query_selector(
            'button:has-text("Continue"), button:has-text("Agree"), '
            'button:has-text("继续"), button:has-text("同意"), '
            'button:has-text("I agree"), button:has-text("Accept"), '
            'button[type="submit"]'
        )
        if not btn:
            break
        try:
            page.evaluate("el => el.click()", btn)
            text = btn.inner_text().strip() if btn else "?"
            logger.info(f"已點擊按鈕: {text}")
            clicked = True
            time.sleep(3)
        except Exception:
            break
    return clicked


def click_resend(page: Page, label: str = ""):
    """嘗試點擊 Resend email 按鈕"""
    try:
        btn = page.query_selector('button:has-text("Resend")') or page.query_selector('a:has-text("Resend")')
        if btn:
            btn.click()
            logger.info(f"✉️ 已點擊 Resend email（{label}）")
    except Exception as e:
        logger.debug(f"Resend 點擊失敗: {e}")


def setup_callback_listener(page: Page) -> List[str]:
    """註冊 request 監聽器攔截 callback URL，返回 captured_urls 列表"""
    captured: List[str] = []
    def on_request(request):
        url = request.url
        if "localhost:1455" in url:
            captured.append(url)
            logger.info(f"🎯 攔截到 callback: {url}")
    page.on("request", on_request)
    return captured


def wait_callback(page: Page, captured: List[str], max_wait: int = 30) -> Optional[str]:
    """等待 callback URL"""
    if captured:
        return captured[0]
    for _ in range(max_wait):
        if captured:
            return captured[0]
        url = page.url
        if "localhost:1455/auth/callback" in url:
            return url
        if "chrome-error" in url:
            break
        time.sleep(1)
    return captured[0] if captured else None


def check_registration_error(page: Page) -> bool:
    """檢查是否有帳戶創建錯誤，返回 True 表示有錯誤"""
    try:
        text = page.inner_text("body")
    except Exception:
        text = ""
    error_keywords = [
        "创建帐户失败", "创建账户失败", "请重试", "error",
        "incorrect", "invalid", "wrong password",
        "帐户已存在", "账户已存在", "already exists",
        "user_already_exists",
    ]
    if any(kw in text for kw in error_keywords):
        return True
    if "log-in/password" in page.url or "create-account/password" in page.url:
        return True
    return False


# ====================== 內部工具 ======================

def _click_submit(page: Page, fallback_input):
    """點擊提交按鈕或按回車"""
    btn = page.query_selector('button[type="submit"], button:has-text("Continue"), button:has-text("继续")')
    if btn:
        btn.click()
    else:
        fallback_input.press("Enter")


def _random_name() -> str:
    """從擴展名字池隨機生成姓名"""
    first_names = [
        "James", "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia",
        "Mason", "Isabella", "Lucas", "Mia", "Oliver", "Charlotte", "Aiden",
        "Amelia", "Elijah", "Harper", "Logan", "Evelyn", "Alexander", "Abigail",
        "Daniel", "Emily", "Henry", "Ella", "Sebastian", "Scarlett", "Jack",
        "Grace", "Owen", "Chloe", "Samuel", "Penelope", "Ryan", "Layla",
        "Nathan", "Riley", "Caleb", "Zoey", "Michael", "Nora", "Benjamin",
        "Lily", "William", "Eleanor", "David", "Hannah", "Joseph", "Aria",
    ]
    last_names = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Wilson", "Anderson", "Taylor",
        "Thomas", "Moore", "Jackson", "Martin", "Lee", "Thompson", "White",
        "Harris", "Clark", "Lewis", "Robinson", "Walker", "Young", "Allen",
        "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson",
        "Hill", "Campbell", "Mitchell", "Roberts", "Carter", "Phillips",
    ]
    return f"{random.choice(first_names)} {random.choice(last_names)}"


def _random_birthday():
    """生成隨機生日（年齡 22-35 歲之間），返回 (year, month, day)"""
    now_year = datetime.datetime.now().year
    year = now_year - random.randint(22, 35)
    month = random.randint(1, 12)
    days_in_month = {
        1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
    }
    day = random.randint(1, days_in_month[month])
    return year, month, day


def _fill_date_inputs(page: Page, values: list, placeholders: tuple):
    """填寫日期輸入框"""
    all_inputs = page.query_selector_all('input')
    idx = 0
    for inp in all_inputs:
        ph = (inp.get_attribute("placeholder") or "").upper()
        maxlen = inp.get_attribute("maxlength") or ""
        if ph in placeholders or maxlen in ("2", "4"):
            inp.click()
            inp.press("Control+a")
            if idx < len(values):
                inp.type(values[idx], delay=50)
                idx += 1
            time.sleep(0.2)
    if idx > 0:
        logger.info(f"已填寫日期: {'/'.join(values[:idx])}")
    else:
        # Tab fallback
        el = page.query_selector('input[name="name"], input[type="text"]')
        if el:
            el.press("Tab")
            time.sleep(0.3)
        for v in values:
            page.keyboard.type(v, delay=100)
            time.sleep(0.3)
        logger.info(f"已填寫日期(Tab): {'/'.join(values)}")
