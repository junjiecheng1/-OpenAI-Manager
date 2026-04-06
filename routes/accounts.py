"""帳號路由: 列表、Session 刷新(SSE)、批量註冊"""
import json
import queue
import threading
from typing import List

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from .shared import (
    AccountOut, RefreshSessionRequest, RegisterRequest, StatsOut,
    load_accounts, load_cdkeys, find_account, update_account_field,
    load_outlook_pool, add_log,
)

router = APIRouter(prefix="/api", tags=["accounts"])


def detect_plan_type(acc: dict) -> str:
    """從 session 數據判斷帳號 plan

    ChatGPT /api/auth/session 返回的 JSON 中:
      account.planType = "free" | "plus"
    """
    raw = acc.get("chatgpt_session_raw", "")
    if raw:
        try:
            session = json.loads(raw) if isinstance(raw, str) else raw
            plan = session.get("account", {}).get("planType", "")
            if plan:
                return plan
        except (json.JSONDecodeError, AttributeError):
            pass
    return acc.get("plan_type", "free")


@router.get("/stats")
async def get_stats():
    """總覽統計"""
    accounts = load_accounts()
    all_keys, used = load_cdkeys()
    plus_count = sum(1 for a in accounts if detect_plan_type(a) == "plus")
    outlook_pool = load_outlook_pool()
    outlook_avail = sum(1 for a in outlook_pool if a.get("status") == "available")

    # gzyi 數據（從巡檢緩存拿，不額外調 API）
    gzyi_total = 0
    gzyi_available = 0
    try:
        from .patrol import _count_gzyi_available
        gzyi_total, gzyi_available, _ = _count_gzyi_available()
    except Exception:
        pass

    return {
        "total_accounts": len(accounts),
        "plus_accounts": plus_count,
        "free_accounts": len(accounts) - plus_count,
        "total_cdkeys": len(all_keys),
        "available_cdkeys": len([k for k in all_keys if k not in used]),
        "used_cdkeys": len(used),
        "total_outlook": len(outlook_pool),
        "outlook_available": outlook_avail,
        "gzyi_total": gzyi_total,
        "gzyi_available": gzyi_available,
    }


@router.get("/accounts", response_model=List[AccountOut])
async def list_accounts():
    """帳號列表"""
    accounts = load_accounts()
    return [
        AccountOut(
            email=a.get("email", ""),
            password=a.get("password", ""),
            created_at=a.get("created_at", ""),
            plan_type=detect_plan_type(a),
            chatgpt_access_token=a.get("chatgpt_access_token", ""),
            chatgpt_session_raw=a.get("chatgpt_session_raw", ""),
            has_session=bool(a.get("chatgpt_session_raw")),
        )
        for a in accounts
    ]


@router.post("/accounts/refresh-session")
async def refresh_session(req: RefreshSessionRequest):
    """SSE 實時刷新帳號 Session，每一步進度即時推送"""
    acc = find_account(req.email)
    if not acc:
        raise HTTPException(404, f"帳號 {req.email} 不存在")

    password = acc.get("password", "")
    if not password:
        raise HTTPException(400, "帳號沒有密碼")

    inbox_id = acc.get("mailslurp_inbox_id", "")
    is_outlook = "@outlook" in req.email.lower() or "@hotmail" in req.email.lower()

    q: queue.Queue = queue.Queue()

    def _do_refresh():
        from .session_service import refresh_session_sync

        def on_progress(msg, level):
            q.put({"msg": msg, "level": level, "done": False, "success": False})

        result = refresh_session_sync(
            email=req.email,
            password=password,
            inbox_id=inbox_id,
            is_outlook=is_outlook,
            on_progress=on_progress,
        )

        if result["success"]:
            q.put({"msg": f"Session 刷新成功 (plan: {result['plan']})", "level": "success", "done": True, "success": True})
        else:
            q.put({"msg": "Session 刷新失敗", "level": "error", "done": True, "success": False})

    # 啟動工作線程
    thread = threading.Thread(target=_do_refresh, daemon=True)
    thread.start()

    # SSE generator
    async def event_stream():
        while True:
            try:
                event = q.get(timeout=0.3)
            except queue.Empty:
                yield ": heartbeat\n\n"
                continue

            data = json.dumps(event, ensure_ascii=False)
            yield f"data: {data}\n\n"

            if event.get("done"):
                break

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/register")
async def register_accounts(req: RegisterRequest):
    """批量註冊 OpenAI 帳號（後台執行）"""
    count = min(req.count, 10)
    add_log(f"開始批量註冊 {count} 個帳號", "info")

    def _do_register():
        try:
            from src.browser_registrar import BrowserRegistrar
            from .session_service import refresh_session_sync

            success_count = 0
            for i in range(count):
                add_log(f"註冊第 {i+1}/{count} 個帳號...", "info")
                try:
                    registrar = BrowserRegistrar(headless=req.headless)
                    result = registrar.register_one()
                    if result:
                        email = result.get("email", "?")
                        password = result.get("password", "")
                        add_log(f"註冊成功: {email}", "success")
                        success_count += 1

                        # 註冊成功後自動拉 Session
                        if not result.get("chatgpt_session_raw"):
                            add_log(f"自動拉取 session: {email}", "info")
                            is_outlook = "@outlook" in email.lower() or "@hotmail" in email.lower()
                            refresh_session_sync(
                                email=email,
                                password=password,
                                is_outlook=is_outlook,
                            )
                    else:
                        add_log(f"第 {i+1} 個註冊失敗", "error")
                except Exception as e:
                    add_log(f"第 {i+1} 個註冊異常: {e}", "error")

            add_log(f"批量註冊完成: 成功 {success_count}/{count}", "success")
        except Exception as e:
            add_log(f"批量註冊失敗: {e}", "error")

    thread = threading.Thread(target=_do_register, daemon=True)
    thread.start()
    return {"success": True, "msg": f"已開始註冊 {count} 個帳號（後台執行中）"}
