"""ChatGPT Plus 自動開通

通過 ai-member.icu API 用卡密開通 Plus:
1. check — 驗證卡密狀態
2. activate — 提交 session 開通

卡密來源: 本地文件 cdkeys.txt (一行一卡)，用完自動標記。
"""
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from .config import Config
from .logger import get_logger

logger = get_logger()

API_BASE = "https://api.ai-member.icu"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
    "Referer": "https://recharge.ai-member.icu/",
}


@dataclass
class CdkeyStatus:
    """卡密狀態"""
    cdkey: str
    success: bool
    use_status: int  # 0=待提交, 1=已使用
    gift_name: str = ""
    msg: str = ""
    account: str = ""


@dataclass
class ActivateResult:
    """開通結果"""
    success: bool
    msg: str
    cdkey: str = ""
    account: str = ""
    gift_name: str = ""


def check_cdkey(cdkey: str) -> CdkeyStatus:
    """驗證卡密狀態"""
    try:
        with httpx.Client(timeout=15, headers=HEADERS) as client:
            resp = client.post(f"{API_BASE}/check", json={"cdkey": cdkey})
            resp.raise_for_status()
            data = resp.json()
            d = data.get("data", {})
            return CdkeyStatus(
                cdkey=cdkey,
                success=data.get("success", False),
                use_status=d.get("use_status", -1),
                gift_name=d.get("gift_name", ""),
                msg=data.get("msg", ""),
                account=d.get("account", ""),
            )
    except Exception as e:
        logger.warning(f"卡密檢查失敗: {e}")
        return CdkeyStatus(cdkey=cdkey, success=False, use_status=-1, msg=str(e))


def activate_plus(cdkey: str, session_info: str, force: int = 0) -> ActivateResult:
    """用卡密 + session 開通 Plus
    
    Args:
        cdkey: 卡密
        session_info: ChatGPT session JSON 字符串
                      (https://chatgpt.com/api/auth/session 的完整響應)
        force: 0=正常, 1=強制
    """
    try:
        with httpx.Client(timeout=30, headers=HEADERS) as client:
            resp = client.post(
                f"{API_BASE}/activate",
                json={
                    "cdkey": cdkey,
                    "session_info": session_info,
                    "force": force,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            d = data.get("data", {})
            result = ActivateResult(
                success=data.get("success", False),
                msg=data.get("msg", ""),
                cdkey=d.get("cdkey", cdkey),
                account=d.get("account", ""),
                gift_name=d.get("gift_name", ""),
            )
            if result.success:
                logger.info(f"✅ Plus 開通成功: {result.account} ({result.gift_name})")
            else:
                logger.warning(f"❌ Plus 開通失敗: {result.msg}")
            return result
    except Exception as e:
        logger.warning(f"Plus 開通請求失敗: {e}")
        return ActivateResult(success=False, msg=str(e))


# ====================== 卡密管理 ======================

CDKEY_FILE = Path(__file__).parent.parent / "cdkeys.txt"
USED_FILE = Path(__file__).parent.parent / "cdkeys_used.txt"


def get_next_cdkey() -> Optional[str]:
    """從 cdkeys.txt 取下一張可用卡密
    
    格式: 每行一卡 (如 7VTW6-JJONS-C5EDH)
    用過的移到 cdkeys_used.txt
    """
    if not CDKEY_FILE.exists():
        logger.warning(f"卡密文件不存在: {CDKEY_FILE}")
        return None
    
    # 讀取已用卡密
    used = set()
    if USED_FILE.exists():
        used = set(USED_FILE.read_text().strip().splitlines())
    
    # 找第一張未用的
    for line in CDKEY_FILE.read_text().strip().splitlines():
        cdkey = line.strip()
        if not cdkey or cdkey.startswith("#") or cdkey in used:
            continue
        # 驗證卡密狀態
        status = check_cdkey(cdkey)
        if status.success and status.use_status == 0:
            logger.info(f"🎫 找到可用卡密: {cdkey} ({status.gift_name})")
            return cdkey
        else:
            logger.info(f"跳過卡密 {cdkey}: {status.msg}")
            # 標記已用/不可用
            _mark_used(cdkey)
    
    logger.warning("沒有可用卡密了")
    return None


def _mark_used(cdkey: str):
    """標記卡密已用"""
    with open(USED_FILE, "a") as f:
        f.write(cdkey + "\n")


def upgrade_account(session_json: str) -> ActivateResult:
    """完整開通流程: 取卡密 → 開通 Plus
    
    Args:
        session_json: ChatGPT session API 的完整 JSON 字符串
    """
    cdkey = get_next_cdkey()
    if not cdkey:
        return ActivateResult(success=False, msg="沒有可用卡密")
    
    result = activate_plus(cdkey, session_json)
    if result.success:
        _mark_used(cdkey)
    return result
