# Cloudflare Email OTP Worker

一个合规的 Cloudflare Email Routing + Worker 方案，用于你自己的域名邮箱收件，并把验证码邮件提取为可读 API。

## 适用边界

- 适用于：你自己的域名、你自己的业务、自动化测试环境。
- 不适用于：批量注册第三方平台、绕过平台验证、通用代收任意验证码。

## 功能

- 通过 Cloudflare Email Routing 接收入站邮件
- 用 Worker 解析 MIME 邮件并提取验证码
- 只接受白名单发件人
- 把验证码存入 Cloudflare KV，自动过期
- 提供 HTTP API 查询和消费验证码

## 目录

- `src/index.js`: Worker 主逻辑，包含 `email` 和 `fetch` 处理器
- `wrangler.toml`: Wrangler 配置
- `.dev.vars.example`: 本地或部署时需要的环境变量示例
- `test/otp.test.js`: 关键提取逻辑测试

## 架构

1. 某个服务发邮件到 `alias@yourdomain.com`
2. Cloudflare Email Routing 把该地址路由到这个 Worker
3. Worker 校验发件人是否在白名单内
4. Worker 提取验证码并写入 KV，设置 TTL
5. 你的业务系统调用 HTTP API 获取或消费验证码

## 环境变量

- `API_TOKEN`: 访问 HTTP API 的 Bearer Token，必须设置
- `ALLOWED_SENDERS`: 允许接收验证码的发件人白名单，逗号分隔，支持 `*` 通配符
- `OTP_TTL_SECONDS`: 验证码 TTL，默认 `300`
- `OTP_CODE_REGEX`: 可选，自定义验证码提取正则；默认提取 4 到 8 位数字
- `REJECT_UNTRUSTED_SENDERS`: `true` 时直接拒绝非白名单邮件，默认 `false`
- `ALLOW_ALPHANUMERIC_CODES`: `true` 时允许提取 6 到 10 位字母数字验证码，默认 `false`

## 部署步骤

### 1. 安装依赖

```bash
cd /Users/iron/Documents/agent/tabapp/cloudflare/email-otp-worker
npm install
```

### 2. 创建 KV Namespace

```bash
npx wrangler kv namespace create OTP_CODES
npx wrangler kv namespace create OTP_CODES --preview
```

把输出的 `id` 和 `preview_id` 填到 `wrangler.toml`。

### 3. 配置变量

本地可以复制：

```bash
cp .dev.vars.example .dev.vars
```

然后修改为你的真实配置，例如：

```env
API_TOKEN=replace-with-a-long-random-token
ALLOWED_SENDERS=no-reply@yourapp.com,*@auth.yourapp.com
OTP_TTL_SECONDS=300
REJECT_UNTRUSTED_SENDERS=true
ALLOW_ALPHANUMERIC_CODES=false
```

生产环境建议使用 Wrangler secret：

```bash
npx wrangler secret put API_TOKEN
```

普通变量可用 `wrangler.toml` 或 dashboard 设置。

### 4. 部署 Worker

```bash
npx wrangler deploy
```

部署成功后会得到一个 Worker URL，例如：

```text
https://email-otp-worker.<subdomain>.workers.dev
```

### 5. 配置 Email Routing

在 Cloudflare Dashboard 中：

- 打开你的域名
- 进入 `Email` / `Email Routing`
- 启用 Email Routing
- 创建路由，例如：
  - `otp@yourdomain.com`
  - 或 `*@yourdomain.com`
- 动作选择转发到 Worker，而不是普通邮箱
- 绑定到这个 Worker

建议不要直接开放全域 catch-all，最好只开放一组明确用途的地址，例如：

- `otp@yourdomain.com`
- `signup+*@yourdomain.com`
- `test+*@yourdomain.com`

## API

所有非健康检查接口都要求：

```http
Authorization: Bearer <API_TOKEN>
```

### 健康检查

```http
GET /health
```

### 获取验证码

```http
GET /otp?email=signup+abc123@yourdomain.com
```

响应示例：

```json
{
  "ok": true,
  "otp": {
    "email": "signup+abc123@yourdomain.com",
    "code": "482911",
    "subject": "Your verification code",
    "sender": "no-reply@yourapp.com",
    "receivedAt": "2026-04-05T10:00:00.000Z",
    "expiresAt": "2026-04-05T10:05:00.000Z"
  }
}
```

### 消费验证码并删除

```http
POST /otp/consume
Content-Type: application/json
Authorization: Bearer <API_TOKEN>

{
  "email": "signup+abc123@yourdomain.com"
}
```

### 主动删除验证码

```http
DELETE /otp?email=signup+abc123@yourdomain.com
Authorization: Bearer <API_TOKEN>
```

## 推荐使用方式

最稳妥的方式不是“自动注册一堆邮箱”，而是：

- 你有自己的域名，例如 `yourdomain.com`
- 每次测试或业务流程生成一个唯一别名，例如 `signup+session123@yourdomain.com`
- 外部系统把验证码发到这个别名
- Worker 按完整收件地址存储对应验证码
- 你的系统按同一个别名读回验证码

这样不需要批量创建实际邮箱账号，也不会依赖 IMAP 轮询。

## 示例调用

获取验证码：

```bash
curl -s \
  -H "Authorization: Bearer $API_TOKEN" \
  "https://email-otp-worker.<subdomain>.workers.dev/otp?email=signup+session123@yourdomain.com"
```

消费验证码：

```bash
curl -s \
  -X POST \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email":"signup+session123@yourdomain.com"}' \
  "https://email-otp-worker.<subdomain>.workers.dev/otp/consume"
```

## 安全建议

- 只放行明确的发件人白名单
- `API_TOKEN` 使用高熵随机值，并定期轮换
- TTL 尽量短，一般 `3-5` 分钟足够
- 不要长期保存完整邮件内容
- 不要把它做成公开验证码代收平台
