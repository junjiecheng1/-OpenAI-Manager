# OpenAI 自动注册工具

一套完整的 OpenAI 账号自动注册方案，包含邮箱验证码接收（Cloudflare Worker）和注册自动化（Python）。

## 项目结构

```
openai-register/
├── worker/                  # Cloudflare Email OTP Worker（已部署）
│   ├── src/index.js         # Worker 主逻辑：收邮件 → 提取验证码 → 存 KV
│   ├── wrangler.toml        # Wrangler 配置（KV binding）
│   ├── test/otp.test.js     # 单元测试
│   └── .dev.vars.example    # 环境变量模板
│
├── src/                     # Python 注册脚本
│   ├── registrar.py         # 核心注册流程（OpenAI OAuth）
│   ├── email_service.py     # 验证码获取（Worker API / IMAP）
│   ├── oauth.py             # OAuth PKCE 客户端
│   ├── config.py            # 配置管理
│   ├── utils.py             # 工具函数
│   └── logger.py            # 日志
│
├── main.py                  # 入口：批量注册 CLI
├── .env                     # 运行配置（不提交）
├── .env.example             # 配置模板
└── pyproject.toml           # Python 依赖
```

## 工作流程

```
1. 脚本生成随机邮箱 abc123@tweet.net.cn
2. 通过 OpenAI API 提交注册（邮箱 + 随机密码）
3. OpenAI 发验证码到 abc123@tweet.net.cn
4. Cloudflare Email Routing 收到邮件 → 推送到 Worker
5. Worker 提取验证码 → 存入 KV（自动过期）
6. 脚本轮询 Worker API /otp/consume 取回验证码
7. 提交验证码 → 完成注册 → 保存 Token
```

## 快速开始

### 1. 配置 .env

```bash
cp .env.example .env
# 编辑 .env，填入你的配置
```

### 2. 安装 Python 依赖

```bash
uv sync
```

### 3. 运行注册

```bash
# 测试单次注册
uv run python main.py --once

# 注册 5 个账号
uv run python main.py -c 5

# 使用代理注册 10 个账号
uv run python main.py -p http://127.0.0.1:7890 -c 10

# 无限循环注册，间隔 30-120 秒
uv run python main.py -smin 30 -smax 120

# 调试模式
uv run python main.py --once -d
```

## Worker 管理

Worker 已部署到 Cloudflare，日常不需要操作。如需修改：

```bash
cd worker

# 安装依赖
npm install

# 本地测试
npm test

# 重新部署
npx wrangler deploy

# 更新 API Token
npx wrangler secret put API_TOKEN

# 更新发件人白名单
npx wrangler secret put ALLOWED_SENDERS
```

### Worker API

```bash
# 健康检查
curl https://email-otp-worker.junjiecheng.workers.dev/health

# 查看验证码（不删除）
curl -H "Authorization: Bearer $TOKEN" \
  "https://email-otp-worker.junjiecheng.workers.dev/otp?email=xxx@tweet.net.cn"

# 消费验证码（读后即焚）
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"xxx@tweet.net.cn"}' \
  "https://email-otp-worker.junjiecheng.workers.dev/otp/consume"
```

## 输出文件

- `tokens/token_<email>_<timestamp>.json` — Token 信息
- `tokens/accounts.txt` — 帐号记录（格式：`email----password`）

## 注意事项

- 需要非 CN/HK IP 的代理
- 建议注册间隔 30-120 秒，避免 IP 被封
- 不要把 `.env` 提交到版本控制
- 本工具仅供学习和研究使用
