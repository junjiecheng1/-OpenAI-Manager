"""5sim.net SMS 接碼服務

流程:
1. buy_number("openai") → 獲取手機號
2. wait_for_code(order_id) → 等待驗證碼
3. finish(order_id) → 完成訂單
"""
import time
from typing import Optional, Tuple

import httpx

from .logger import get_logger

logger = get_logger()

BASE_URL = "https://5sim.net/v1"


class SmsService:
    """5sim.net 接碼服務"""

    def __init__(self, api_key: str, country: str = "any", operator: str = "any"):
        self.api_key = api_key
        self.country = country
        self.operator = operator
        self._client = httpx.Client(
            base_url=BASE_URL,
            timeout=30,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )

    # ------------------------------------------------------------------
    #  核心流程
    # ------------------------------------------------------------------

    def buy_number(self, product: str = "openai") -> Tuple[Optional[int], Optional[str]]:
        """
        購買一個號碼用於指定服務驗證

        Returns:
            (order_id, phone_number) 或 (None, None) 表示失敗
        """
        url = f"/user/buy/activation/{self.country}/{self.operator}/{product}"
        try:
            resp = self._client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                order_id = data.get("id")
                phone = data.get("phone")
                # 去掉开头的国家代码前缀（如果有）
                logger.info(f"📱 已購買號碼: {phone} (訂單 #{order_id})")
                return order_id, phone
            else:
                logger.error(f"購買號碼失敗: {resp.status_code} {resp.text[:200]}")
                return None, None
        except Exception as e:
            logger.error(f"購買號碼異常: {e}")
            return None, None

    def wait_for_code(
        self,
        order_id: int,
        timeout: int = 300,
        interval: int = 5,
    ) -> str:
        """
        等待 SMS 驗證碼

        Returns:
            驗證碼字串，超時返回空字串
        """
        logger.info(f"等待 SMS 驗證碼（訂單 #{order_id}，{timeout}s 超時）...")
        start = time.time()
        attempts = 0

        while time.time() - start < timeout:
            attempts += 1
            elapsed = int(time.time() - start)

            try:
                resp = self._client.get(f"/user/check/{order_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status", "")
                    sms_list = data.get("sms", [])

                    if status == "RECEIVED" and sms_list:
                        sms_item = sms_list[0]
                        code = sms_item.get("code", "")
                        if not code:
                            # 从 text 字段用正则提取 6 位数字
                            import re
                            text = sms_item.get("text", "")
                            match = re.search(r'\b(\d{6})\b', text)
                            if match:
                                code = match.group(1)
                            logger.debug(f"SMS 原文: {text}")
                        if code:
                            logger.info(f"📲 收到 SMS 驗證碼: {code}")
                            return code
                        else:
                            logger.warning(f"SMS 已收到但未解析到驗證碼: {sms_list[0]}")

                    if status == "CANCELED":
                        logger.warning("訂單已被取消")
                        return ""

                    if attempts % 5 == 0:
                        logger.info(f"已等待 {elapsed}s，狀態: {status}")
                else:
                    logger.debug(f"查詢失敗: {resp.status_code}")
            except Exception as e:
                logger.debug(f"查詢異常: {e}")

            time.sleep(interval)

        logger.warning(f"超時 {timeout}s，未收到 SMS")
        return ""

    def finish(self, order_id: int) -> bool:
        """標記訂單為完成"""
        try:
            resp = self._client.get(f"/user/finish/{order_id}")
            ok = resp.status_code == 200
            if ok:
                logger.info(f"訂單 #{order_id} 已完成")
            return ok
        except Exception:
            return False

    def cancel(self, order_id: int) -> bool:
        """取消訂單（未收到 SMS 時退款）"""
        try:
            resp = self._client.get(f"/user/cancel/{order_id}")
            ok = resp.status_code == 200
            if ok:
                logger.info(f"訂單 #{order_id} 已取消")
            return ok
        except Exception:
            return False

    def ban(self, order_id: int) -> bool:
        """舉報號碼（號碼已被註冊過）"""
        try:
            resp = self._client.get(f"/user/ban/{order_id}")
            return resp.status_code == 200
        except Exception:
            return False

    def get_balance(self) -> float:
        """查詢餘額"""
        try:
            resp = self._client.get("/user/profile")
            if resp.status_code == 200:
                balance = resp.json().get("balance", 0)
                logger.info(f"💰 5sim 餘額: {balance} RUB")
                return float(balance)
        except Exception:
            pass
        return 0.0

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass
