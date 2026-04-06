"""Outlook 邮箱支持

功能:
1. 從 98faka 卡密提取 Outlook 帳號
2. IMAP 收件（用於接收 OpenAI 驗證碼）
3. 作為 EmailProvider 集成到註冊流程
"""
import imaplib
import email
import re
import time
import logging
from typing import Optional, List, Tuple

import httpx

from .email_service import EmailProvider, extract_otp_code

logger = logging.getLogger(__name__)


# ====================== 卡密提取 ======================

def extract_cards_from_98faka(card_codes: list[str], fmt: str = "full") -> dict:
    """從 98faka 批量提取 Outlook 帳號
    
    Args:
        card_codes: 卡號列表（如 ["F3A81812FC736FD1", ...]）
        fmt: 'simple' 或 'full'
    
    Returns:
        {"success": int, "total": int, "accounts": [...], "results": [...]}
    """
    try:
        resp = httpx.post(
            "https://tiqu.98faka.top/api/extract/batch",
            json={"card_codes": card_codes, "format": fmt},
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Origin": "https://tiqu.98faka.top",
                "Referer": "https://tiqu.98faka.top/",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        
        # 解析 data 文本為帳號列表
        accounts = []
        raw_text = data.get("data", "")
        if raw_text:
            accounts = parse_account_text(raw_text)
        
        return {
            "success": data.get("success", 0),
            "total": data.get("total", 0),
            "accounts": accounts,
            "results": data.get("results", []),
        }
    except Exception as e:
        logger.error(f"98faka 提取失敗: {e}")
        return {"success": 0, "total": 0, "accounts": [], "results": []}


def parse_account_text(text: str) -> List[dict]:
    """解析「帳號----密碼----ID----Token」格式的文本
    
    支持多種分隔符: ----, \t, |
    """
    accounts = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        
        # 嘗試不同分隔符
        parts = None
        for sep in ["----", "\t", "|"]:
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                break
        
        if not parts or len(parts) < 2:
            continue
        
        acc = {
            "email": parts[0],
            "password": parts[1],
        }
        if len(parts) >= 3:
            acc["ms_id"] = parts[2]
        if len(parts) >= 4:
            acc["ms_token"] = parts[3]
        accounts.append(acc)
    
    return accounts


# ====================== IMAP 收件 ======================

class OutlookIMAP:
    """Outlook IMAP 收件（支持 OAuth2 XOAUTH2）"""

    IMAP_SERVER = "outlook.office365.com"
    IMAP_PORT = 993
    TOKEN_URL = "https://login.live.com/oauth20_token.srf"

    def __init__(self, email_addr: str, password: str,
                 client_id: str = "", refresh_token: str = ""):
        self.email_addr = email_addr
        self.password = password
        self.client_id = client_id
        self.refresh_token = refresh_token
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    def connect(self) -> bool:
        """連接 IMAP（優先 OAuth2，fallback 基礎認證）"""
        if self.client_id and self.refresh_token:
            return self._connect_oauth2()
        return self._connect_basic()

    def _connect_oauth2(self) -> bool:
        """用 OAuth2 XOAUTH2 認證連接 IMAP"""
        try:
            access_token = self._get_access_token()
            if not access_token:
                return False

            auth_string = f"user={self.email_addr}\x01auth=Bearer {access_token}\x01\x01"

            self._conn = imaplib.IMAP4_SSL(self.IMAP_SERVER, self.IMAP_PORT)
            self._conn.authenticate("XOAUTH2", lambda x: auth_string.encode())
            logger.info(f"Outlook IMAP OAuth2 已連接: {self.email_addr}")
            return True
        except Exception as e:
            logger.warning(f"Outlook IMAP OAuth2 連接失敗 {self.email_addr}: {e}")
            return False

    def _connect_basic(self) -> bool:
        """基礎認證（已被微軟禁用，作為 fallback）"""
        try:
            self._conn = imaplib.IMAP4_SSL(self.IMAP_SERVER, self.IMAP_PORT)
            self._conn.login(self.email_addr, self.password)
            logger.info(f"Outlook IMAP 基礎認證已連接: {self.email_addr}")
            return True
        except Exception as e:
            logger.warning(f"Outlook IMAP 基礎認證失敗 {self.email_addr}: {e}")
            return False

    def _get_access_token(self) -> str:
        """用 refresh_token 換取 access_token（Live endpoint，不帶 scope）"""
        try:
            resp = httpx.post(self.TOKEN_URL, data={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token", "")
            if token:
                logger.info(f"OAuth2 access_token 獲取成功 ({len(token)} chars)")
            return token
        except Exception as e:
            logger.warning(f"OAuth2 token 交換失敗: {e}")
            return ""
    
    def wait_for_otp(self, timeout: int = 180, interval: int = 5) -> str:
        """等待 OpenAI 驗證碼郵件
        
        搜索最近的 OpenAI 郵件，提取 6 位驗證碼
        """
        if not self._conn:
            if not self.connect():
                return ""
        
        logger.info(f"等待 Outlook 收到驗證碼（{timeout}s 超時）...")
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                self._conn.select("INBOX")
                # 搜索 OpenAI 的郵件
                _, msg_nums = self._conn.search(None, '(FROM "noreply@tm.openai.com")')
                if not msg_nums[0]:
                    # 也搜索其他可能的發件人
                    _, msg_nums = self._conn.search(None, '(FROM "openai.com")')
                
                if msg_nums[0]:
                    # 取最新的
                    nums = msg_nums[0].split()
                    latest = nums[-1]
                    _, msg_data = self._conn.fetch(latest, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    
                    # 提取主題和正文
                    subject = str(email.header.decode_header(msg["Subject"])[0][0] or "")
                    if isinstance(subject, bytes):
                        subject = subject.decode("utf-8", errors="ignore")
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                            elif part.get_content_type() == "text/html":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                    
                    code = extract_otp_code(subject) or extract_otp_code(body)
                    if code:
                        logger.info(f"📬 Outlook 獲取到驗證碼: {code}")
                        # 標記已讀
                        self._conn.store(latest, "+FLAGS", "\\Seen")
                        return code
                
                elapsed = int(time.time() - start)
                if elapsed % 15 == 0 and elapsed > 0:
                    logger.info(f"已等待 {elapsed}s，繼續查詢 Outlook...")
                time.sleep(interval)
                
            except Exception as e:
                logger.debug(f"查詢 Outlook 失敗: {e}")
                # 嘗試重連
                try:
                    self.connect()
                except Exception:
                    pass
                time.sleep(interval)
        
        logger.warning(f"超時 {timeout}s，Outlook 未收到驗證碼")
        return ""
    
    def close(self):
        """關閉連接"""
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None


# ====================== EmailProvider 實現 ======================

class OutlookProvider(EmailProvider):
    """Outlook 邮箱 Provider（支持 OAuth2 IMAP）"""

    def __init__(self, email_addr: str = "", password: str = "",
                 client_id: str = "", refresh_token: str = ""):
        self.email_address = email_addr
        self.password = password
        self.client_id = client_id
        self.refresh_token = refresh_token
        self._imap: Optional[OutlookIMAP] = None

    def create_inbox(self, proxies=None) -> str:
        """返回預設的 Outlook 郵箱（不創建新的）"""
        if not self.email_address:
            raise ValueError("OutlookProvider 需要預先設定 email")
        return self.email_address

    def wait_for_otp(self, target_email: str, timeout: int = 180, interval: int = 5) -> str:
        """用 IMAP 等待驗證碼"""
        if not self._imap:
            self._imap = OutlookIMAP(
                self.email_address, self.password,
                client_id=self.client_id,
                refresh_token=self.refresh_token,
            )
        return self._imap.wait_for_otp(timeout=timeout, interval=interval)

    def close(self):
        """關閉 IMAP"""
        if self._imap:
            self._imap.close()
            self._imap = None
