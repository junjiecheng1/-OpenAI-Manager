# OpenAI Account Manager

一套完整的 OpenAI 帳號自動化管理方案，包含自動註冊、Session 拉取、Plus 開通、gzyi 同步。

## 功能

- **自動註冊** — Playwright 瀏覽器自動化註冊 OpenAI 帳號
- **郵箱驗證** — 支持 Outlook IMAP (OAuth2)、Gmail IMAP、Cloudflare Worker
- **Session 拉取** — 自動登入 ChatGPT 獲取 Session Token
- **Plus 開通** — 卡密自動兌換 Plus 訂閱
- **gzyi 同步** — OAuth 授權後自動推送到 gzyi.top 管理平台
- **Dashboard** — Web 管理面板，帳號/卡密/巡檢/gzyi 一站式管理
- **自動巡檢** — 定時檢查帳號數量，不足自動補充

## 快速開始

### 本地運行

```bash
# 1. 複製配置
cp .env.example .env
# 編輯 .env，填入你的配置

# 2. 安裝依賴
uv sync
playwright install chromium

# 3. 啟動服務
uv run python server.py

# Dashboard: http://localhost:8900
```

### Docker 部署

```bash
# 1. 複製配置
cp .env.example .env
# 編輯 .env

# 2. 啟動
docker compose up -d

# Dashboard: http://localhost:8900
# 瀏覽器監控 (noVNC): http://localhost:6080/vnc.html (密碼: openai123)
```

## 項目結構

```
├── server.py                # FastAPI 入口
├── routes/                  # API 路由
│   ├── accounts.py          # 帳號管理
│   ├── patrol.py            # 自動巡檢
│   ├── gzyi.py              # gzyi 同步
│   ├── session_service.py   # Session 拉取
│   └── shared.py            # 共享工具
├── src/                     # 核心邏輯
│   ├── browser_registrar.py # 瀏覽器自動註冊
│   ├── browser_utils.py     # 瀏覽器操作工具
│   ├── chatgpt_login.py     # ChatGPT 登入
│   ├── account_authorizer.py# OAuth 授權抽象
│   ├── email_service.py     # 郵箱驗證碼
│   ├── outlook_provider.py  # Outlook IMAP OAuth2
│   ├── plus_upgrade.py      # Plus 開通
│   ├── oauth.py             # OAuth PKCE
│   └── config.py            # 配置管理
├── static/                  # Dashboard 前端
├── worker/                  # Cloudflare Email Worker
├── Dockerfile               # Docker 鏡像
├── docker-compose.yml       # Docker Compose
└── .env.example             # 配置模板
```

## 工作流程

```
全流程: 註冊 → 關閉瀏覽器 → 拉 Session → 開通 Plus → 導入 gzyi
```

## 環境配置

參見 `.env.example`，支持以下郵箱模式：

| 模式 | 說明 |
|------|------|
| **Outlook Pool** | Outlook OAuth2 IMAP，推薦 |
| **Gmail IMAP** | Gmail +tag 別名，需應用密碼 |
| **Worker** | Cloudflare Email Worker 收驗證碼 |

## Worker API

```bash
# 健康檢查
curl https://your-worker.workers.dev/health

# 查看驗證碼
curl -H "Authorization: Bearer $TOKEN" \
  "https://your-worker.workers.dev/otp?email=xxx@yourdomain.com"

# 消費驗證碼
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"xxx@yourdomain.com"}' \
  "https://your-worker.workers.dev/otp/consume"
```

## 注意事項

- 需要海外 IP
- 不要把 `.env` 提交到版本控制
- 本工具僅供學習和研究使用
