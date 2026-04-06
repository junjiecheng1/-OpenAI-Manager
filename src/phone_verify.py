"""手機號驗證流程

封裝 5sim 買號 → 選國家 → 填號碼 → 收 SMS → 填驗證碼 → 提交 的完整流程。
"""
import time
from typing import Optional

from playwright.sync_api import Page

from .config import Config
from .sms_service import SmsService
from .logger import get_logger

logger = get_logger()

# 國家代碼映射: 5sim_country -> (電話代碼, 中文名)
COUNTRY_CODES = {
    "india": ("91", "印度"),
    "usa": ("1", "美国"),
    "england": ("44", "英国"),
    "indonesia": ("62", "印度尼西亚"),
    "brazil": ("55", "巴西"),
    "philippines": ("63", "菲律宾"),
    "russia": ("7", "俄罗斯"),
}


def verify_phone(page: Page) -> bool:
    """完整的手機號驗證流程
    
    返回 True 表示驗證成功，False 表示失敗。
    """
    phone_input = page.query_selector('input[type="tel"], input[name="phone"], input[placeholder*="phone" i]')
    if not phone_input:
        phone_input = page.query_selector('input[name="phoneNumber"]')
    
    if not phone_input and "phone" not in page.url.lower():
        logger.info("無需手機驗證")
        return True
    
    logger.info("📱 需要手機號驗證...")
    
    if not Config.FIVESIM_API_KEY:
        logger.error("未配置 FIVESIM_API_KEY，無法自動驗證手機")
        page.screenshot(path="debug_phone_required.png")
        return False
    
    sms = SmsService(
        api_key=Config.FIVESIM_API_KEY,
        country=Config.FIVESIM_COUNTRY,
        operator=Config.FIVESIM_OPERATOR,
    )
    
    try:
        sms.get_balance()
        order_id, phone_number = sms.buy_number("openai")
        if not order_id or not phone_number:
            logger.error("購買號碼失敗")
            return False
        
        raw_phone = phone_number.lstrip("+")
        cc_info = COUNTRY_CODES.get(Config.FIVESIM_COUNTRY, ("", ""))
        country_code, country_label = cc_info
        
        # 去掉國家代碼
        local_phone = raw_phone
        if country_code and raw_phone.startswith(country_code):
            local_phone = raw_phone[len(country_code):]
        
        # 選擇國家
        country_selected = _select_country(page, country_code, country_label)
        
        # 填入手機號
        if not phone_input:
            phone_input = page.query_selector('input[type="tel"], input[name="phone"], input[name="phoneNumber"]')
        
        if not phone_input:
            logger.error("未找到手機號輸入框")
            sms.cancel(order_id)
            return False
        
        page.evaluate("el => el.click()", phone_input)
        page.evaluate("el => { el.value = ''; }", phone_input)
        fill_number = local_phone if country_selected else raw_phone
        phone_input.type(fill_number, delay=50)
        logger.info(f"已填入手機號: {fill_number} (國家已選: {country_selected})")
        
        time.sleep(0.5)
        submit = page.query_selector('button:has-text("Continue"), button:has-text("Send"), button[type="submit"]')
        if submit:
            page.evaluate("el => el.click()", submit)
            logger.info("已提交手機號")
        
        time.sleep(3)
        
        # 等待 SMS 驗證碼
        sms_code = sms.wait_for_code(order_id, timeout=120, interval=5)
        if not sms_code:
            logger.error("未收到 SMS 驗證碼")
            sms.cancel(order_id)
            return False
        
        # 填入 SMS 驗證碼
        time.sleep(2)
        sms_input = page.query_selector('input[name="code"], input[type="tel"], input[inputmode="numeric"]')
        if not sms_input:
            logger.error("未找到 SMS 驗證碼輸入框")
            sms.cancel(order_id)
            return False
        
        sms_input.click()
        sms_input.fill("")
        sms_input.type(sms_code, delay=50)
        logger.info(f"已填入 SMS 驗證碼: {sms_code}")
        
        time.sleep(0.5)
        verify_btn = page.query_selector('button:has-text("Verify"), button:has-text("Continue"), button[type="submit"]')
        if verify_btn:
            verify_btn.click()
            logger.info("已提交 SMS 驗證碼")
        
        time.sleep(3)
        sms.finish(order_id)
        
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        logger.info(f"手機驗證後 URL: {page.url}")
        return True
        
    finally:
        sms.close()


def _select_country(page: Page, country_code: str, country_label: str) -> bool:
    """選擇國家代碼，返回是否成功"""
    if country_label == "美国":
        logger.info("默認國家已是美國 (+1)，跳過選擇")
        return True
    
    if not country_code or not country_label:
        return False
    
    try:
        page.click('text=美国', timeout=3000)
        time.sleep(1.5)
        logger.info("已打開國家下拉列表")
        
        try:
            page.click(f'text={country_label}', timeout=5000)
            logger.info(f"已選擇國家: {country_label} (+{country_code})")
            time.sleep(0.5)
            return True
        except Exception:
            logger.info("直接點擊失敗，嘗試鍵盤搜索...")
            page.keyboard.type(country_label[:3], delay=80)
            time.sleep(0.5)
            page.keyboard.press("Enter")
            time.sleep(0.5)
            try:
                btn_text = page.locator('button[aria-haspopup="listbox"]').first.inner_text()
                if country_label in btn_text or f"+{country_code}" in btn_text:
                    logger.info(f"已通過鍵盤選擇國家: {btn_text}")
                    return True
            except Exception:
                pass
            page.keyboard.press("Escape")
            return False
    except Exception as e:
        logger.warning(f"選擇國家失敗: {e}")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False
