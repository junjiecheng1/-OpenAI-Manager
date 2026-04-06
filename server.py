"""OpenAI 帳號管理站點

FastAPI 後端 + 靜態前端 Dashboard
功能: 帳號管理、卡密管理、批量註冊、Plus 開通、gzyi.top 同步、Outlook 郵箱池
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.shared import _task_logs
from routes.accounts import router as accounts_router
from routes.cdkeys import router as cdkeys_router
from routes.gzyi import router as gzyi_router
from routes.outlook import router as outlook_router
from routes.patrol import router as patrol_router

app = FastAPI(title="OpenAI 帳號管理", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載路由
app.include_router(accounts_router)
app.include_router(cdkeys_router)
app.include_router(gzyi_router)
app.include_router(outlook_router)
app.include_router(patrol_router)


# ====================== 日誌 API ======================

@app.get("/api/logs")
async def get_logs(since: int = 0):
    """獲取操作日誌（支持 since 增量拉取）"""
    if since > 0:
        return [log for log in _task_logs if log["id"] > since]
    return list(_task_logs)


# ====================== 靜態文件 ======================

BASE_DIR = Path(__file__).parent
static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def index():
    """Dashboard 首頁"""
    return FileResponse(str(static_dir / "index.html"))
