"""共享狀態: 路徑、模型、工具函數、操作日誌"""
import json
import time as _time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

from src.config import Config

# ====================== 路徑 ======================

BASE_DIR = Path(__file__).parent.parent
TOKENS_DIR = BASE_DIR / (Config.TOKEN_OUTPUT_DIR or "tokens")
ACCOUNTS_FILE = TOKENS_DIR / "accounts.jsonl"
CDKEYS_FILE = BASE_DIR / "cdkeys.txt"
CDKEYS_USED_FILE = BASE_DIR / "cdkeys_used.txt"
OUTLOOK_POOL_FILE = TOKENS_DIR / "outlook_pool.jsonl"

# gzyi.top 配置
GZYI_API_URL = (Config.GZYI_API_URL or "").rstrip("/")
GZYI_TOKEN = Config.GZYI_TOKEN or ""
GZYI_HEADERS = {
    "Authorization": f"Bearer {GZYI_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# ====================== 操作日誌 ======================

_task_logs: deque = deque(maxlen=100)
_log_counter = 0


def add_log(msg: str, level: str = "info"):
    """添加操作日誌"""
    global _log_counter
    _log_counter += 1
    _task_logs.append({
        "id": _log_counter,
        "time": datetime.now().strftime("%H:%M:%S"),
        "msg": msg,
        "level": level,
    })


# ====================== 數據模型 ======================

class AccountOut(BaseModel):
    email: str
    password: str
    created_at: str = ""
    plan_type: str = "free"
    chatgpt_access_token: str = ""
    chatgpt_session_raw: str = ""
    has_session: bool = False
    in_gzyi: bool = False


class CdkeyOut(BaseModel):
    cdkey: str
    status: str
    gift_name: str = ""
    account: str = ""


class GzyiAccountOut(BaseModel):
    email: str
    plan_type: str = "free"
    status: str = "active"
    is_active: bool = True
    schedulable: bool = True


class ActivateRequest(BaseModel):
    email: str
    cdkey: Optional[str] = None


class CdkeyImportRequest(BaseModel):
    cdkeys: str


class RefreshSessionRequest(BaseModel):
    email: str


class RegisterRequest(BaseModel):
    count: int = 1
    email_source: str = "mailslurp"
    headless: bool = False


class StatsOut(BaseModel):
    total_accounts: int
    plus_accounts: int
    free_accounts: int
    available_cdkeys: int
    used_cdkeys: int
    outlook_pool: int = 0


class OutlookExtractRequest(BaseModel):
    card_codes: List[str]


class OutlookImportTextRequest(BaseModel):
    text: str


class GzyiImportRequest(BaseModel):
    email: str


# ====================== 工具函數 ======================

def load_accounts() -> List[dict]:
    """讀取所有帳號"""
    if not ACCOUNTS_FILE.exists():
        return []
    accounts = []
    for line in ACCOUNTS_FILE.read_text().strip().splitlines():
        if line.strip():
            try:
                accounts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return accounts


def load_cdkeys():
    """讀取卡密"""
    all_keys = []
    if CDKEYS_FILE.exists():
        for line in CDKEYS_FILE.read_text().strip().splitlines():
            k = line.strip()
            if k and not k.startswith("#"):
                all_keys.append(k)
    used = set()
    if CDKEYS_USED_FILE.exists():
        used = set(CDKEYS_USED_FILE.read_text().strip().splitlines())
    return all_keys, used


def find_account(email: str) -> Optional[dict]:
    """根據郵箱找帳號"""
    for acc in load_accounts():
        if acc.get("email") == email:
            return acc
    return None


def update_account_field(email: str, updates: dict):
    """更新帳號字段"""
    if not ACCOUNTS_FILE.exists():
        return
    lines = ACCOUNTS_FILE.read_text().strip().splitlines()
    new_lines = []
    for line in lines:
        try:
            acc = json.loads(line)
            if acc.get("email") == email:
                acc.update(updates)
            new_lines.append(json.dumps(acc, ensure_ascii=False))
        except json.JSONDecodeError:
            new_lines.append(line)
    ACCOUNTS_FILE.write_text("\n".join(new_lines) + "\n")


def load_outlook_pool() -> List[dict]:
    """讀取 Outlook 郵箱池"""
    if not OUTLOOK_POOL_FILE.exists():
        return []
    pool = []
    for line in OUTLOOK_POOL_FILE.read_text().strip().splitlines():
        if line.strip():
            try:
                pool.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pool


def save_outlook_pool(pool: List[dict]):
    """保存 Outlook 郵箱池"""
    OUTLOOK_POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTLOOK_POOL_FILE, "w") as f:
        for acc in pool:
            f.write(json.dumps(acc, ensure_ascii=False) + "\n")
