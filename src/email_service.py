"""郵箱服務 — 策略模式

三種 Provider 實現同一接口:
- GmailProvider — Gmail +tag 別名 + IMAP 讀驗證碼
- TempMailProvider — tempmail.lol 一次性郵箱
- WorkerProvider — Cloudflare Worker 自有域名

Factory 根據 Config 自動選擇。
"""
import email as email_lib
import imaplib
import json
import os
import random
import re
import string
import time
from abc import ABC, abstractmethod
from email.header import decode_header
from typing import Optional, Tuple

import httpx

from .config import Config
from .logger import get_logger
from .utils import extract_otp_code, generate_random_email

logger = get_logger()


# ====================== Provider 接口 ======================

class EmailProvider(ABC):
    """郵箱 Provider 抽象基類"""
    
    @abstractmethod
    def create_inbox(self, proxies: Optional[dict] = None) -> str:
        """建立收件箱，返回郵箱地址"""
        ...
    
    @abstractmethod
    def wait_for_otp(self, target_email: str, timeout: int = 240, interval: int = 5) -> str:
        """等待 OTP 驗證碼，返回 6 位碼或空串"""
        ...
    
    def close(self):
        """清理資源"""
        pass


# ====================== Gmail Provider ======================

class GmailProvider(EmailProvider):
    """Gmail +tag 別名 + IMAP 讀驗證碼"""
    
    def __init__(self):
        self.email_user = Config.EMAIL_USER
        self.email_pass = Config.EMAIL_PASS
        self.imap_server = Config.IMAP_SERVER
        self.imap_port = Config.IMAP_PORT
    
    def create_inbox(self, proxies=None) -> str:
        local, domain = self.email_user.strip().split("@", 1)
        # 去掉已有的點號，得到純字母數字用戶名
        clean = local.replace(".", "")
        # 隨機在用戶名中插入點號（Gmail 忽略點，但 OpenAI 當不同郵箱）
        dotted = _random_dots(clean)
        alias = f"{dotted}@{domain}"
        logger.info(f"📧 Gmail dot-trick 郵箱: {alias}")
        return alias
    
    def wait_for_otp(self, target_email: str, timeout: int = 240, interval: int = 5) -> str:
        logger.info(f"等待 Gmail IMAP 收到 {target_email} 的驗證碼...")
        start = time.time()
        mail: Optional[imaplib.IMAP4_SSL] = None
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            mail.login(self.email_user, self.email_pass.replace(" ", ""))
            # 用 All Mail 搜所有郵件（包括促銷/社交等分類）
            mail.select("INBOX")
            
            # 快照: 記錄最近 5 封之前的郵件 ID，留緩衝避免競態
            status, messages = mail.search(None, 'ALL')
            seen_ids = set()
            if status == "OK" and messages[0]:
                all_existing = messages[0].split()
                # 排除最後 5 封，因為驗證碼可能已在發送中
                safe_count = max(0, len(all_existing) - 5)
                seen_ids = set(all_existing[:safe_count])
            logger.info(f"IMAP 快照: 共 {len(seen_ids) + 5} 封，排除前 {len(seen_ids)} 封")
            
            while time.time() - start < timeout:
                elapsed = int(time.time() - start)
                # 刷新收件箱
                mail.noop()
                status, messages = mail.search(None, 'ALL')
                if status == "OK" and messages[0]:
                    all_ids = messages[0].split()
                    # 只看新郵件
                    new_ids = [eid for eid in all_ids if eid not in seen_ids]
                    for eid in reversed(new_ids):
                        try:
                            status, msg_data = mail.fetch(eid, "(RFC822)")
                            if status != "OK" or not msg_data or not msg_data[0]:
                                continue
                            msg = email_lib.message_from_bytes(msg_data[0][1])
                            subject = _decode_subject(msg.get("Subject", ""))
                            from_addr = msg.get("From", "")
                            to_addr = msg.get("To", "").lower()
                            
                            # 精確匹配: To header 必須包含當前 alias
                            if target_email.lower() not in to_addr:
                                continue
                            
                            if _is_openai_email(subject, from_addr):
                                code = extract_otp_code(subject)
                                if code:
                                    logger.info(f"📬 Gmail IMAP 獲取到驗證碼: {code}")
                                    return code
                        except Exception:
                            continue
                if elapsed % 15 == 0 and elapsed > 0:
                    logger.info(f"已等待 {elapsed}s，繼續查詢 IMAP...")
                time.sleep(interval)
            logger.warning(f"超時 {timeout}s，IMAP 未收到驗證碼")
            return ""
        except Exception as e:
            logger.error(f"IMAP 錯誤: {e}")
            return ""
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass


# ====================== TempMail Provider ======================

class TempMailProvider(EmailProvider):
    """TempMail.lol 臨時郵箱"""
    
    def __init__(self):
        self.address = ""
        self.token = ""
        self._client: Optional[httpx.Client] = None
    
    def create_inbox(self, proxies=None) -> str:
        proxy_url = None
        if proxies:
            proxy_url = proxies if isinstance(proxies, str) else proxies.get("https") or proxies.get("http")
        transport = httpx.HTTPTransport(proxy=proxy_url) if proxy_url else None
        self._client = httpx.Client(
            timeout=15, verify=False, transport=transport,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        resp = self._client.post("https://api.tempmail.lol/v2/inbox/create", json={})
        resp.raise_for_status()
        data = resp.json()
        self.address = data["address"]
        self.token = data["token"]
        logger.info(f"📧 TempMail 郵箱: {self.address}")
        return self.address
    
    def wait_for_otp(self, target_email: str, timeout: int = 240, interval: int = 5) -> str:
        if not self._client:
            return ""
        logger.info(f"等待 TempMail 收到驗證碼（{timeout}s 超時）...")
        start = time.time()
        attempts = 0
        while time.time() - start < timeout:
            attempts += 1
            elapsed = int(time.time() - start)
            try:
                resp = self._client.get(f"https://api.tempmail.lol/v2/inbox?token={self.token}")
                resp.raise_for_status()
                msgs = resp.json().get("emails", [])
                if attempts % 5 == 0:
                    logger.info(f"已等待 {elapsed}s，收到 {len(msgs)} 封郵件...")
                for msg in msgs:
                    sender = msg.get("from", "").lower()
                    subject = msg.get("subject", "")
                    if "openai" in sender or "chatgpt" in subject.lower():
                        logger.info(f"📬 收到 OpenAI 郵件: {subject}")
                        for field in ("subject", "body", "html"):
                            code = extract_otp_code(msg.get(field, "") or "")
                            if code:
                                return code
                        logger.warning(f"郵件中未找到驗證碼")
            except Exception as e:
                logger.debug(f"查詢 TempMail 失敗: {e}")
            time.sleep(interval)
        logger.warning(f"超時 {timeout}s，TempMail 未收到驗證碼")
        return ""
    
    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass


# ====================== Worker Provider ======================

class WorkerProvider(EmailProvider):
    """Cloudflare Worker 自有域名"""
    
    def __init__(self):
        self.worker_url = Config.OTP_WORKER_URL
        self.worker_token = Config.OTP_WORKER_TOKEN
        self.domain = Config.MAIL_DOMAIN
    
    def create_inbox(self, proxies=None) -> str:
        addr = generate_random_email(self.domain)
        logger.info(f"📧 Worker 郵箱: {addr}")
        return addr
    
    def wait_for_otp(self, target_email: str, timeout: int = 240, interval: int = 5) -> str:
        logger.info(f"等待 Worker 收到 {target_email} 的驗證碼...")
        start = time.time()
        headers = {
            "Authorization": f"Bearer {self.worker_token}",
            "Accept": "application/json",
        }
        url = f"{self.worker_url.rstrip('/')}/otp"
        with httpx.Client(verify=False, timeout=10) as client:
            while time.time() - start < timeout:
                elapsed = int(time.time() - start)
                try:
                    resp = client.get(url, params={"email": target_email.strip().lower()}, headers=headers)
                    if resp.status_code == 200:
                        code = resp.json().get("otp", {}).get("code", "")
                        if code:
                            logger.info(f"從 Worker 獲取到驗證碼: {code}")
                            return code
                except Exception as e:
                    logger.debug(f"查詢 Worker 失敗: {e}")
                if elapsed % 15 == 0 and elapsed > 0:
                    logger.info(f"已等待 {elapsed}s，繼續查詢 Worker...")
                time.sleep(interval)
        logger.warning(f"超時 {timeout}s，Worker 未收到驗證碼")
        return ""


# ====================== MailSlurp Provider ======================

class MailSlurpProvider(EmailProvider):
    """MailSlurp API 邮箱接码"""
    
    def __init__(self):
        self.api_key = Config.MAILSLURP_API_KEY
        self.inbox_id = ""
        self.email_address = ""
        self._base = "https://api.mailslurp.com"
    
    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
    
    def create_inbox(self, proxies=None) -> str:
        with httpx.Client(timeout=15) as client:
            resp = client.post(f"{self._base}/inboxes", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            self.inbox_id = data["id"]
            self.email_address = data["emailAddress"]
        logger.info(f"📧 MailSlurp 郵箱: {self.email_address}")
        return self.email_address
    
    def wait_for_otp(self, target_email: str, timeout: int = 240, interval: int = 5) -> str:
        logger.info(f"等待 MailSlurp 收到驗證碼（{timeout}s 超時）...")
        start = time.time()
        with httpx.Client(timeout=15) as client:
            while time.time() - start < timeout:
                elapsed = int(time.time() - start)
                try:
                    # 等待收到新邮件
                    resp = client.get(
                        f"{self._base}/waitForLatestEmail",
                        params={
                            "inboxId": self.inbox_id,
                            "timeout": min(30000, (timeout - elapsed) * 1000),
                            "unreadOnly": "true",
                        },
                        headers=self._headers(),
                        timeout=35,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        subject = data.get("subject", "")
                        body = data.get("body", "")
                        logger.info(f"📬 MailSlurp 收到郵件: {subject[:60]}")
                        code = extract_otp_code(subject) or extract_otp_code(body)
                        if code:
                            logger.info(f"📬 MailSlurp 獲取到驗證碼: {code}")
                            return code
                except Exception as e:
                    logger.debug(f"查詢 MailSlurp 失敗: {e}")
                if elapsed % 15 == 0 and elapsed > 0:
                    logger.info(f"已等待 {elapsed}s，繼續查詢 MailSlurp...")
                time.sleep(interval)
        logger.warning(f"超時 {timeout}s，MailSlurp 未收到驗證碼")
        return ""


# ====================== Factory ======================

# Provider 轮换顺序
_PROVIDER_ORDER = ["gmail", "mailslurp", "tempmail", "worker"]


def create_email_provider(proxies=None, attempt: int = 1) -> EmailProvider:
    """根據配置創建郵箱 Provider

    attempt=1 用配置默認 Provider
    attempt>1 自動降級到下一個 Provider（避免同一個模式反復失敗）
    """
    mode = Config.EMAIL_MODE
    
    # 確定當前和降級 Provider
    if attempt <= 1:
        modes_to_try = _get_modes_for(mode)
    else:
        # 重試時輪換: 跳過前 attempt-1 個
        all_modes = _get_modes_for(mode)
        skip = attempt - 1
        modes_to_try = all_modes[skip:] + all_modes[:skip]
    
    for m in modes_to_try:
        provider = _try_create(m, proxies)
        if provider:
            return provider
    
    # 最終兜底
    return WorkerProvider()


def _get_modes_for(mode: str) -> list:
    """根據配置模式決定嘗試順序
    
    Outlook 郵箱池為首選（穩定），然後 MailSlurp / Gmail
    """
    # 所有模式都先嘗試 Outlook 池
    if mode == "gmail":
        return ["outlook_pool", "gmail", "mailslurp"]
    if mode == "mailslurp":
        return ["outlook_pool", "mailslurp", "gmail"]
    if mode == "tempmail":
        return ["outlook_pool", "tempmail", "worker", "gmail"]
    # auto
    if Config.use_gmail():
        return ["outlook_pool", "gmail", "mailslurp", "worker"]
    if Config.MAILSLURP_API_KEY:
        return ["outlook_pool", "mailslurp", "gmail", "worker"]
    return ["outlook_pool", "tempmail", "worker", "gmail"]


def _try_create(mode: str, proxies=None) -> Optional[EmailProvider]:
    """嘗試創建指定模式的 Provider，失敗返回 None"""
    if mode == "outlook_pool":
        return _try_create_outlook_pool()
    if mode == "gmail" and Config.use_imap():
        logger.info("使用 Gmail dot-trick 模式")
        return GmailProvider()
    if mode == "mailslurp" and Config.MAILSLURP_API_KEY:
        logger.info("使用 MailSlurp 模式")
        return MailSlurpProvider()
    if mode == "tempmail":
        try:
            p = TempMailProvider()
            p.create_inbox(proxies)
            logger.info("📧 使用 TempMail 模式")
            return p
        except Exception as e:
            logger.warning(f"TempMail 建立失敗: {e}")
            return None
    if mode == "worker" and Config.use_cloudflare_worker():
        logger.info("📧 使用 Worker 模式")
        return WorkerProvider()
    return None


def _try_create_outlook_pool() -> Optional[EmailProvider]:
    """從 Outlook 郵箱池取一個可用郵箱

    讀取 tokens/outlook_pool.jsonl，找 status=available 的，
    先測 IMAP 連通性，連不上的標記 imap_failed 跳過。
    """
    from .outlook_provider import OutlookProvider, OutlookIMAP

    pool_file = os.path.join(Config.TOKEN_OUTPUT_DIR or "./tokens", "outlook_pool.jsonl")
    if not os.path.exists(pool_file):
        return None

    try:
        with open(pool_file) as f:
            lines = [json.loads(l) for l in f if l.strip()]
    except Exception:
        return None

    # 遍歷可用郵箱，測 IMAP
    target = None
    modified = False
    for acc in lines:
        if acc.get("status") != "available":
            continue

        email_addr = acc["email"]
        password = acc["password"]
        client_id = acc.get("ms_id", "")
        refresh_token = acc.get("ms_token", "")

        # 預先測試 IMAP 連通性
        logger.info(f"測試 Outlook IMAP: {email_addr}")
        imap = OutlookIMAP(email_addr, password,
                           client_id=client_id, refresh_token=refresh_token)
        if imap.connect():
            imap.close()
            target = acc
            break
        else:
            logger.warning(f"Outlook IMAP 不可用: {email_addr}，標記跳過")
            acc["status"] = "imap_failed"
            modified = True

    if not target:
        logger.info("Outlook 池無 IMAP 可用郵箱，跳過")
        if modified:
            with open(pool_file, "w") as f:
                for acc in lines:
                    f.write(json.dumps(acc, ensure_ascii=False) + "\n")
        return None

    email_addr = target["email"]
    password = target["password"]
    client_id = target.get("ms_id", "")
    refresh_token = target.get("ms_token", "")

    # 標記為 used
    for acc in lines:
        if acc["email"] == email_addr:
            acc["status"] = "used"
            break
    with open(pool_file, "w") as f:
        for acc in lines:
            f.write(json.dumps(acc, ensure_ascii=False) + "\n")

    logger.info(f"使用 Outlook 池郵箱 (IMAP OK): {email_addr}")
    provider = OutlookProvider(email_addr, password,
                               client_id=client_id, refresh_token=refresh_token)
    return provider


# ====================== 工具函數 ======================

def _random_dots(username: str) -> str:
    """在用戶名中隨機插入點號（Gmail dot trick）
    
    Gmail 忽略用戶名中的點，但 OpenAI 視為不同郵箱。
    例: johndoe1234 → j.ohnd.oe.1234
    """
    if len(username) < 2:
        return username
    chars = list(username)
    result = [chars[0]]
    for c in chars[1:]:
        if random.random() < 0.3:  # 30% 概率插入點
            result.append(".")
        result.append(c)
    dotted = "".join(result)
    # 確保不以點開頭/結尾，不連續點
    dotted = dotted.strip(".")
    while ".." in dotted:
        dotted = dotted.replace("..", ".")
    # 確保和原始不同（至少一個點）
    if dotted == username:
        mid = len(username) // 2
        dotted = username[:mid] + "." + username[mid:]
    return dotted


def _decode_subject(subject: str) -> str:
    if not subject:
        return ""
    parts = []
    for part, encoding in decode_header(subject):
        if isinstance(part, bytes):
            parts.append(part.decode(encoding or 'utf-8', errors='ignore'))
        else:
            parts.append(str(part))
    return ''.join(parts)


def _is_openai_email(subject: str, from_addr: str) -> bool:
    s = subject.lower()
    f = from_addr.lower()
    return "openai" in f or "chatgpt" in s or "code is" in s

