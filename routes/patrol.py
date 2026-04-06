"""巡檢路由: 自動維持 Plus 帳號數量 + gzyi 可用號檢測"""
import json
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import httpx
from fastapi import APIRouter

from .shared import (
    load_accounts, load_cdkeys, update_account_field, add_log,
    find_account,
    GZYI_API_URL, GZYI_HEADERS, ACCOUNTS_FILE, TOKENS_DIR,
)

router = APIRouter(prefix="/api/patrol", tags=["patrol"])

# ====================== 配置 ======================

MIN_PLUS_COUNT = 6           # Plus 帳號最低數量
MIN_GZYI_AVAILABLE = 3       # gzyi 周限額可用最低數量
PATROL_INTERVAL_SEC = 1800   # 巡檢間隔 (30 分鐘)

# 狀態持久化文件
PATROL_STATE_FILE = TOKENS_DIR / "patrol_state.json"

# ====================== 狀態 ======================

@dataclass
class PatrolState:
    """巡檢狀態"""
    enabled: bool = False
    running: bool = False
    last_run: str = ""
    last_result: str = ""
    current_plus: int = 0
    target_plus: int = MIN_PLUS_COUNT
    gzyi_total: int = 0
    gzyi_available: int = 0
    gzyi_target: int = MIN_GZYI_AVAILABLE
    interval_sec: int = PATROL_INTERVAL_SEC
    timer: Optional[threading.Timer] = field(default=None, repr=False)


def _save_state():
    """保存巡檢狀態到本地文件"""
    data = {
        "enabled": patrol_state.enabled,
        "last_run": patrol_state.last_run,
        "last_result": patrol_state.last_result,
        "current_plus": patrol_state.current_plus,
        "target_plus": patrol_state.target_plus,
        "gzyi_total": patrol_state.gzyi_total,
        "gzyi_available": patrol_state.gzyi_available,
        "gzyi_target": patrol_state.gzyi_target,
        "interval_sec": patrol_state.interval_sec,
    }
    try:
        PATROL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PATROL_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _load_state() -> PatrolState:
    """從本地文件載入巡檢狀態"""
    state = PatrolState()
    if PATROL_STATE_FILE.exists():
        try:
            data = json.loads(PATROL_STATE_FILE.read_text())
            state.enabled = data.get("enabled", False)
            state.last_run = data.get("last_run", "")
            state.last_result = data.get("last_result", "")
            state.current_plus = data.get("current_plus", 0)
            state.target_plus = data.get("target_plus", MIN_PLUS_COUNT)
            state.gzyi_total = data.get("gzyi_total", 0)
            state.gzyi_available = data.get("gzyi_available", 0)
            state.gzyi_target = data.get("gzyi_target", MIN_GZYI_AVAILABLE)
            state.interval_sec = data.get("interval_sec", PATROL_INTERVAL_SEC)
        except Exception:
            pass
    return state


# 啟動時載入
patrol_state = _load_state()


# ====================== 計數邏輯 ======================

def _count_plus() -> int:
    """統計當前 Plus 帳號數量（從 session 解析）"""
    accounts = load_accounts()
    count = 0
    for acc in accounts:
        raw = acc.get("chatgpt_session_raw", "")
        if raw:
            try:
                session = json.loads(raw)
                if session.get("account", {}).get("planType") == "plus":
                    count += 1
                    continue
            except (json.JSONDecodeError, AttributeError):
                pass
        if acc.get("plan_type") == "plus":
            count += 1
    return count


_gzyi_cache = {"data": [], "ts": 0}
_GZYI_CACHE_TTL = 60  # 緩存 60 秒


def _fetch_gzyi_accounts(force: bool = False) -> list:
    """獲取 gzyi 帳號列表（含 codexUsage），60秒緩存"""
    now = time.time()
    if not force and _gzyi_cache["data"] and (now - _gzyi_cache["ts"]) < _GZYI_CACHE_TTL:
        return _gzyi_cache["data"]

    if not GZYI_API_URL:
        return []
    try:
        resp = httpx.get(
            f"{GZYI_API_URL}/admin/openai-accounts",
            headers=GZYI_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("data", []) if isinstance(data, dict) else data
        _gzyi_cache["data"] = result
        _gzyi_cache["ts"] = now
        return result
    except Exception as e:
        add_log(f"[巡檢] gzyi 帳號 API 失敗: {e}", "error")
        return _gzyi_cache["data"]  # 返回舊緩存


def _count_gzyi_available(force: bool = False) -> tuple:
    """統計 gzyi 可用帳號數

    判斷邏輯: codexUsage 的 primary(小時窗口) 和 secondary(周窗口)
    任一 remainingSeconds == 0 就是不可用

    返回: (total, available, details)
    """
    accounts = _fetch_gzyi_accounts(force=force)
    total = len(accounts)
    available = 0
    details = []

    for acc in accounts:
        name = acc.get("name", acc.get("email", "?"))
        is_active = acc.get("isActive", True)
        rate_limited = (acc.get("rateLimitStatus") or {}).get("isRateLimited", False)

        codex = acc.get("codexUsage") or {}
        primary = codex.get("primary") or {}
        secondary = codex.get("secondary") or {}

        primary_remaining = primary.get("remainingSeconds", -1)
        secondary_remaining = secondary.get("remainingSeconds", -1)
        primary_pct = primary.get("usedPercent", 0)
        secondary_pct = secondary.get("usedPercent", 0)

        primary_ok = primary_remaining != 0 if primary_remaining >= 0 else True
        secondary_ok = secondary_remaining != 0 if secondary_remaining >= 0 else True

        is_available = (
            is_active
            and not rate_limited
            and primary_ok
            and secondary_ok
        )

        if is_available:
            available += 1

        details.append({
            "name": name,
            "available": is_available,
            "rate_limited": rate_limited,
            "primary_pct": primary_pct,
            "secondary_pct": secondary_pct,
        })

    return total, available, details

# ====================== 手動註冊全流程 ======================

@router.post("/register-one")
async def register_one_full():
    """手動觸發註冊一個帳號（全流程：註冊→Session→Plus→gzyi）"""
    if patrol_state.running:
        return {"success": False, "msg": "巡檢正在執行，請稍後"}

    import threading
    thread = threading.Thread(target=_run_register_one_full, daemon=True)
    thread.start()
    return {"success": True, "msg": "已啟動全流程註冊"}


def _run_register_one_full():
    """單個帳號完整流水線"""
    patrol_state.running = True
    _save_state()

    try:
        from src.browser_registrar import BrowserRegistrar
        from src.plus_upgrade import get_next_cdkey, activate_plus, _mark_used

        add_log("[註冊] 開始全流程註冊...", "info")

        # 1. 註冊
        registrar = BrowserRegistrar(headless=False)
        result = registrar.register_one()
        if not result:
            add_log("[註冊] 註冊失敗", "error")
            return

        reg_email = result.get("email", "?")
        reg_pwd = result.get("password", "")
        add_log(f"[註冊] ✅ 註冊成功: {reg_email}", "success")

        # 2. 拉 Session
        acc_data = find_account(reg_email)
        if acc_data and not acc_data.get("chatgpt_session_raw"):
            add_log(f"[註冊] 拉取 Session: {reg_email}", "info")
            try:
                from .session_service import refresh_session_sync
                is_outlook = "@outlook" in reg_email.lower() or "@hotmail" in reg_email.lower()
                sess = refresh_session_sync(reg_email, reg_pwd, is_outlook=is_outlook)
                if sess.get("success"):
                    add_log(f"[註冊] ✅ Session 拉取成功", "success")
                else:
                    add_log(f"[註冊] Session 拉取失敗", "error")
            except Exception as e:
                add_log(f"[註冊] Session 異常: {e}", "error")

        # 3. 開通 Plus
        acc_data = find_account(reg_email)
        if acc_data and acc_data.get("chatgpt_session_raw"):
            cdkey = get_next_cdkey()
            if cdkey:
                add_log(f"[註冊] 開通 Plus: {reg_email}", "info")
                try:
                    plus_result = activate_plus(cdkey, acc_data["chatgpt_session_raw"])
                    if plus_result.success:
                        _mark_used(cdkey)
                        # 更新 plan_type 和 session 中的 planType
                        try:
                            session_obj = json.loads(acc_data["chatgpt_session_raw"])
                            if "account" in session_obj:
                                session_obj["account"]["planType"] = "plus"
                            update_account_field(reg_email, {
                                "plan_type": "plus",
                                "chatgpt_session_raw": json.dumps(session_obj, ensure_ascii=False),
                            })
                        except (json.JSONDecodeError, TypeError):
                            update_account_field(reg_email, {"plan_type": "plus"})
                        add_log(f"[註冊] ✅ Plus 開通成功", "success")

                        # 4. 導入 gzyi
                        add_log(f"[註冊] 導入 gzyi: {reg_email}", "info")
                        try:
                            from playwright.sync_api import sync_playwright
                            from src.account_authorizer import authorize_account
                            from .gzyi import _init_email_provider

                            with sync_playwright() as p:
                                browser = p.chromium.launch(
                                    headless=False,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                                )
                                ctx = browser.new_context(
                                    viewport={"width": 1280, "height": 800},
                                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                                    locale="zh-CN",
                                )
                                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")
                                page = ctx.new_page()
                                ep = _init_email_provider(reg_email, reg_pwd)
                                auth_result = authorize_account(
                                    page, ctx, reg_email, reg_pwd,
                                    source="register_full",
                                    save_to_gzyi=True, save_local=True,
                                    email_provider=ep,
                                )
                                browser.close()
                                if auth_result.success:
                                    add_log(f"[註冊] ✅ gzyi 導入成功", "success")
                                else:
                                    add_log(f"[註冊] gzyi 導入失敗: {auth_result.error}", "error")
                        except Exception as e:
                            add_log(f"[註冊] gzyi 導入異常: {e}", "error")
                    else:
                        add_log(f"[註冊] Plus 開通失敗: {plus_result.msg}", "error")
                except Exception as e:
                    add_log(f"[註冊] Plus 開通異常: {e}", "error")
            else:
                add_log("[註冊] 沒有可用卡密，跳過 Plus 開通", "warning")
        else:
            add_log("[註冊] 無 Session，跳過 Plus 開通", "warning")

        add_log(f"[註冊] ✅ 全流程完成: {reg_email}", "success")

    except Exception as e:
        add_log(f"[註冊] 全流程異常: {e}", "error")
    finally:
        patrol_state.running = False
        _save_state()


# ====================== 巡檢主邏輯 ======================

def _run_patrol():
    """執行一次巡檢"""
    from datetime import datetime

    patrol_state.running = True
    patrol_state.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_state()
    add_log("[巡檢] 開始自動巡檢...", "info")

    try:
        plus_count = _count_plus()
        patrol_state.current_plus = plus_count
        plus_deficit = max(0, patrol_state.target_plus - plus_count)
        add_log(f"[巡檢] 本地 Plus: {plus_count}/{patrol_state.target_plus}", "info")

        gzyi_total, gzyi_avail, gzyi_details = _count_gzyi_available(force=True)
        patrol_state.gzyi_total = gzyi_total
        patrol_state.gzyi_available = gzyi_avail
        gzyi_deficit = max(0, patrol_state.gzyi_target - gzyi_avail)
        add_log(f"[巡檢] gzyi 可用: {gzyi_avail}/{gzyi_total} (目標 ≥{patrol_state.gzyi_target})", "info")

        exhausted = [d for d in gzyi_details if not d["available"]]
        if exhausted:
            names = ", ".join(d["name"].split("@")[0] for d in exhausted[:5])
            add_log(f"[巡檢] 額度耗盡: {names}{'...' if len(exhausted) > 5 else ''}", "info")

        total_deficit = max(plus_deficit, gzyi_deficit)

        if total_deficit <= 0:
            msg = f"[巡檢] 帳號充足 (Plus: {plus_count}, gzyi可用: {gzyi_avail})"
            add_log(msg, "success")
            patrol_state.last_result = msg
            _save_state()
            return

        add_log(f"[巡檢] 需補 {total_deficit} 個帳號 (Plus缺{plus_deficit}, gzyi缺{gzyi_deficit})", "info")

        accounts = load_accounts()
        free_with_session = [
            a for a in accounts
            if _session_plan(a) != "plus" and a.get("chatgpt_session_raw")
        ]

        activated = 0
        for acc in free_with_session:
            if activated >= total_deficit:
                break
            email = acc["email"]
            session_raw = acc["chatgpt_session_raw"]
            add_log(f"[巡檢] 嘗試為 {email} 開通 Plus...", "info")
            try:
                from src.plus_upgrade import get_next_cdkey, activate_plus, _mark_used

                cdkey = get_next_cdkey()
                if not cdkey:
                    add_log("[巡檢] 沒有可用卡密，停止開通", "error")
                    break

                result = activate_plus(cdkey, session_raw)
                if result.success:
                    _mark_used(cdkey)
                    try:
                        session_obj = json.loads(session_raw)
                        if "account" in session_obj:
                            session_obj["account"]["planType"] = "plus"
                        update_account_field(email, {
                            "plan_type": "plus",
                            "chatgpt_session_raw": json.dumps(session_obj, ensure_ascii=False),
                        })
                    except (json.JSONDecodeError, TypeError):
                        update_account_field(email, {"plan_type": "plus"})

                    add_log(f"[巡檢] Plus 開通成功: {email}", "success")
                    activated += 1
                else:
                    add_log(f"[巡檢] Plus 開通失敗: {email} — {result.msg}", "error")
            except Exception as e:
                add_log(f"[巡檢] 開通異常: {email} — {e}", "error")

        remaining = total_deficit - activated

        if remaining > 0:
            add_log(f"[巡檢] 還差 {remaining} 個，開始註冊新帳號...", "info")
            try:
                from src.browser_registrar import BrowserRegistrar
                from src.plus_upgrade import get_next_cdkey, activate_plus, _mark_used

                for i in range(remaining):
                    add_log(f"[巡檢] 註冊第 {i+1}/{remaining} 個...", "info")
                    try:
                        registrar = BrowserRegistrar(headless=False)
                        result = registrar.register_one()
                        if not result:
                            add_log(f"[巡檢] 第 {i+1} 個註冊失敗", "error")
                            continue

                        reg_email = result.get("email", "?")
                        reg_pwd = result.get("password", "")
                        add_log(f"[巡檢] 註冊成功: {reg_email}", "success")

                        # 1. 拉 Session（如果還沒有）
                        acc_data = find_account(reg_email)
                        if acc_data and not acc_data.get("chatgpt_session_raw"):
                            add_log(f"[巡檢] 為 {reg_email} 拉 Session...", "info")
                            try:
                                from .session_service import refresh_session_sync
                                is_outlook = "@outlook" in reg_email.lower() or "@hotmail" in reg_email.lower()
                                sess_result = refresh_session_sync(
                                    reg_email, reg_pwd,
                                    is_outlook=is_outlook,
                                )
                                if sess_result.get("success"):
                                    add_log(f"[巡檢] Session 拉取成功: {reg_email}", "success")
                                else:
                                    add_log(f"[巡檢] Session 拉取失敗: {reg_email}", "error")
                            except Exception as e:
                                add_log(f"[巡檢] Session 拉取異常: {e}", "error")

                        # 重新讀取帳號（可能 session 已更新）
                        acc_data = find_account(reg_email)

                        # 2. 開通 Plus
                        if acc_data and acc_data.get("chatgpt_session_raw"):
                            session_raw = acc_data["chatgpt_session_raw"]
                            add_log(f"[巡檢] 為 {reg_email} 開通 Plus...", "info")
                            cdkey = get_next_cdkey()
                            if cdkey:
                                try:
                                    plus_result = activate_plus(cdkey, session_raw)
                                    if plus_result.success:
                                        _mark_used(cdkey)
                                        update_account_field(reg_email, {"plan_type": "plus"})
                                        add_log(f"[巡檢] Plus 開通成功: {reg_email}", "success")
                                        activated += 1

                                        # 3. 導入 gzyi
                                        add_log(f"[巡檢] 導入 {reg_email} 到 gzyi...", "info")
                                        try:
                                            from playwright.sync_api import sync_playwright
                                            from src.account_authorizer import authorize_account
                                            from .gzyi import _init_email_provider

                                            with sync_playwright() as p:
                                                browser = p.chromium.launch(
                                                    headless=False,
                                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                                                )
                                                ctx = browser.new_context(
                                                    viewport={"width": 1280, "height": 800},
                                                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                                                    locale="zh-CN",
                                                )
                                                ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")
                                                page = ctx.new_page()
                                                ep = _init_email_provider(reg_email, reg_pwd)
                                                auth_result = authorize_account(
                                                    page, ctx, reg_email, reg_pwd,
                                                    source="patrol",
                                                    save_to_gzyi=True, save_local=True,
                                                    email_provider=ep,
                                                )
                                                browser.close()
                                                if auth_result.success:
                                                    add_log(f"[巡檢] gzyi 導入成功: {reg_email}", "success")
                                                else:
                                                    add_log(f"[巡檢] gzyi 導入失敗: {auth_result.error}", "error")
                                        except Exception as e:
                                            add_log(f"[巡檢] gzyi 導入異常: {e}", "error")
                                    else:
                                        add_log(f"[巡檢] Plus 開通失敗: {reg_email} — {plus_result.msg}", "error")
                                except Exception as e:
                                    add_log(f"[巡檢] 開通異常: {reg_email} — {e}", "error")
                            else:
                                add_log("[巡檢] 沒有可用卡密", "error")

                    except Exception as e:
                        add_log(f"[巡檢] 註冊異常: {e}", "error")
            except Exception as e:
                add_log(f"[巡檢] 註冊模組異常: {e}", "error")

        final_plus = _count_plus()
        patrol_state.current_plus = final_plus
        msg = f"[巡檢] 完成: Plus {final_plus}/{patrol_state.target_plus}, 本次開通 {activated}"
        add_log(msg, "success")
        patrol_state.last_result = msg

    except Exception as e:
        msg = f"[巡檢] 異常: {e}"
        add_log(msg, "error")
        patrol_state.last_result = msg
    finally:
        patrol_state.running = False
        _save_state()
        if patrol_state.enabled:
            _schedule_next()


def _session_plan(acc: dict) -> str:
    raw = acc.get("chatgpt_session_raw", "")
    if raw:
        try:
            return json.loads(raw).get("account", {}).get("planType", "free")
        except (json.JSONDecodeError, AttributeError):
            pass
    return acc.get("plan_type", "free")


def _schedule_next():
    if patrol_state.timer:
        patrol_state.timer.cancel()
    patrol_state.timer = threading.Timer(patrol_state.interval_sec, _patrol_thread)
    patrol_state.timer.daemon = True
    patrol_state.timer.start()
    add_log(f"[巡檢] 下次巡檢: {patrol_state.interval_sec // 60} 分鐘後", "info")


def _patrol_thread():
    _run_patrol()


# 啟動時如果 enabled，自動恢復排程
if patrol_state.enabled:
    add_log("[巡檢] 載入已啟用的巡檢排程", "info")
    _schedule_next()


# ====================== API ======================

@router.get("/status")
async def patrol_status():
    """巡檢狀態（用緩存數據，秒回）"""
    # Plus 數量讀本地，很快
    plus_count = _count_plus()
    # gzyi 用緩存（60秒 TTL）
    gzyi_total, gzyi_avail, _ = _count_gzyi_available()

    # 更新狀態並保存
    patrol_state.current_plus = plus_count
    patrol_state.gzyi_total = gzyi_total
    patrol_state.gzyi_available = gzyi_avail
    _save_state()

    return {
        "enabled": patrol_state.enabled,
        "running": patrol_state.running,
        "last_run": patrol_state.last_run,
        "last_result": patrol_state.last_result,
        "current_plus": plus_count,
        "target_plus": patrol_state.target_plus,
        "gzyi_total": gzyi_total,
        "gzyi_available": gzyi_avail,
        "gzyi_target": patrol_state.gzyi_target,
        "interval_min": patrol_state.interval_sec // 60,
    }


@router.post("/toggle")
async def patrol_toggle():
    patrol_state.enabled = not patrol_state.enabled
    if patrol_state.enabled:
        add_log("[巡檢] 自動巡檢已啟用", "success")
        _schedule_next()
    else:
        if patrol_state.timer:
            patrol_state.timer.cancel()
            patrol_state.timer = None
        add_log("[巡檢] 自動巡檢已停用", "info")
    _save_state()
    return {"enabled": patrol_state.enabled}


@router.post("/run")
async def patrol_run_now():
    if patrol_state.running:
        return {"success": False, "msg": "巡檢正在執行中"}
    thread = threading.Thread(target=_run_patrol, daemon=True)
    thread.start()
    return {"success": True, "msg": "已開始巡檢"}


@router.post("/config")
async def patrol_config(target_plus: int = MIN_PLUS_COUNT, target_gzyi: int = MIN_GZYI_AVAILABLE, interval: int = 30):
    patrol_state.target_plus = max(1, target_plus)
    patrol_state.gzyi_target = max(1, target_gzyi)
    patrol_state.interval_sec = max(5, interval) * 60
    _save_state()
    add_log(f"[巡檢] 配置: Plus≥{patrol_state.target_plus}, gzyi可用≥{patrol_state.gzyi_target}, 間隔{interval}分鐘", "info")
    return {
        "target_plus": patrol_state.target_plus,
        "target_gzyi": patrol_state.gzyi_target,
        "interval_min": patrol_state.interval_sec // 60,
    }


@router.get("/gzyi-details")
async def patrol_gzyi_details():
    """gzyi 帳號明細（含可用狀態）"""
    _, _, details = _count_gzyi_available()
    return details
