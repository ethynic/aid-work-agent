# 飞书接入智能体 - 实施文档

本文档指导完成飞书开放平台应用创建、回调配置、系统侧配置、加解密模式选择，以及联调与排障。

## 目录

- [1. 实施概览](#1-实施概览)
- [2. 飞书开放平台配置](#2-飞书开放平台配置)
- [3. 系统侧配置](#3-系统侧配置)
- [4. 加密模式选择](#4-加密模式选择)
- [5. 联调验证](#5-联调验证)
- [6. 消息处理架构](#6-消息处理架构)
- [7. 故障排查](#7-故障排查)
- [8. 安全注意事项](#8-安全注意事项)
- [附录 A：路由与接口一览](#附录-a路由与接口一览)
- [附录 B：飞书事件字段参考](#附录-b飞书事件字段参考)

---

## 1. 实施概览

### 已实现能力

| 能力 | 模块 | 状态 |
|------|------|------|
| AES-256-CBC 消息加解密（PKCS7 填充，块大小 32） | `src/channels/feishu/crypto.py` | ✅ |
| v2.0 事件签名校验（`X-Lark-Signature`） | `FeishuCrypto.verify_signature` | ✅ |
| url_verification（POST，含加密/非加密两种模式） | `FeishuCrypto.verify_url_verification` | ✅ |
| v2.0 消息解析（文本、图片、文件） | `FeishuAdapter.parse_message` | ✅ |
| 群聊 @机器人 识别与占位符还原 | `parse_message` + `_replace_mentions` | ✅ |
| 发送消息（content 字段必须是 JSON 字符串） | `send_text` / `send_long_message` | ✅ |
| 长消息三级拆分（段落 → 行 → 字节） | `send_long_message` | ✅ |
| 图片/文件 下载 + 上传 | `src/channels/feishu/media.py` | ✅ |
| tenant_access_token 并发刷新锁 | `_refresh_access_token` | ✅ |
| 每 user 滑动窗口速率限制 | `_check_rate_limit` | ✅ |
| 事件去重（基于 `event_id`，TTL 5 分钟） | `channel_routes._get_feishu_event_dedup` | ✅ |
| 路由立即返回 2xx + 后台异步处理 | `asyncio.create_task` | ✅ |
| 多租户配置（管理后台 UI） | `ChannelConfig` 表 + `channel_type=feishu` | ✅ |

### 回调端点

| 方法 | 路径 | 触发时机 | 说明 |
|------|------|----------|------|
| GET | `/t/{tenant_id}/feishu/callback` | 飞书后台「请求网址配置」阶段 | 返回 challenge 完成验证（兼容模式） |
| POST | `/t/{tenant_id}/feishu/callback` | 用户发消息 / url_verification / 各类事件 | 签名校验 → 解密 → 去重 → 异步处理 |

### 接入架构

```
飞书用户 ←→ 飞书服务器 ←(HTTPS 回调)→ 你的服务器 (AID Work Agent)
                                    ↑                    ↓
                         /t/{tenant_id}/feishu/callback   Agent 处理 + 主动消息 API
```

---

## 2. 飞书开放平台配置

### 2.1 创建应用

1. 登录 [飞书开放平台](https://open.feishu.cn/app)
2. 创建「企业自建应用」
3. 记录 `App ID` 和 `App Secret`

### 2.2 配置权限

进入「权限管理」页面，按需开通：

| 权限 | 用途 | 必需 |
|------|------|------|
| `im:message` | 接收消息事件 | ✅ |
| `im:message:send_as_bot` | 以机器人身份发消息 | ✅ |
| `im:chat` | 获取群信息 | 群聊场景必需 |
| `im:resource` | 下载图片/文件 | 处理附件时必需 |
| `contact:user.base:readonly` | 读取用户基本信息 | 可选 |
| `contact:user.employee_id:readonly` | 读取员工工号 | 可选 |

### 2.3 开启机器人能力

「应用能力」→「机器人」→ 启用

### 2.4 配置事件订阅

「事件订阅」页面：

1. **请求网址 URL**：填入 `https://your-domain.com/t/{tenant_id}/feishu/callback`
2. **Encrypt Key**：可选，见 [§4 加密模式选择](#4-加密模式选择)
3. **Verification Token**：复制粘贴到系统侧配置
4. **添加事件**：
   - `im.message.receive_v1` — 接收消息（必需）
   - `im.message.recalled_v1` — 消息撤回（可选）
   - `im.chat.disbanded_v1` — 群解散通知（可选）

> ⚠️ 飞书在保存 URL 时会先发起一次 POST url_verification 请求，必须通过才能保存成功。

### 2.5 发布应用

完成权限申请 → 创建版本 → 申请发布 → 企业管理员审批通过 → 应用上线

---

## 3. 系统侧配置

### 3.1 多租户模式（推荐）

通过管理后台「渠道配置」页面添加飞书渠道：

| 字段 | 来源 | 必填 |
|------|------|------|
| `channel_type` | 固定 `feishu` | ✅ |
| `app_id` | 飞书开放平台 App ID | ✅ |
| `app_secret` | 飞书开放平台 App Secret | ✅ |
| `verification_token` | 飞书事件订阅 Verification Token | ✅ |
| `encrypt_key` | 飞书事件订阅 Encrypt Key（可选） | ❌ |
| `welcome_message` | 首次会话欢迎消息 | ❌ |

保存后系统会为该租户创建一条 `ChannelConfig` 记录，`tenant_id` 自动绑定。

回调 URL 中的 `{tenant_id}` 路径段由系统从 URL 解析，无需在配置中显式指定。

### 3.2 单租户模式（config.yaml）

非 SaaS 部署且不方便用管理后台时，可直接在 `configs/config.yaml` 中配置（注意:项目已不再读取 `FEISHU_*` 环境变量,需将凭证明文填入配置）：

```yaml
channels:
  feishu:
    enabled: true
    app_id: "cli_xxxxxxxxxxxxxxxxxx"
    app_secret: "your_feishu_app_secret"
    verification_token: "your_verification_token"
    encrypt_key: "your_encrypt_key_32_chars"  # 可选
    welcome_message: "你好，我是你的智能助手"
    max_bytes: 4000          # 单条消息最大字节数
    rate_limit_window: 60    # 速率限制窗口（秒）
    rate_limit_max: 10       # 每窗口最大消息数
```

> **历史变更**: 旧版本支持 `${FEISHU_APP_ID}` / `${FEISHU_APP_SECRET}` / `${FEISHU_VERIFICATION_TOKEN}` / `${FEISHU_ENCRYPT_KEY}` 占位符,现已被管理后台取代,请使用上述明文配置或管理后台。

---

## 4. 加密模式选择

飞书支持两种事件推送模式，按「事件订阅 → Encrypt Key」是否填写区分：

### 4.1 非加密模式（Encrypt Key 为空）

- 事件以明文 JSON 推送
- 仅校验 `verification_token`（url_verification 阶段）
- **不适用**于生产环境（飞书官方推荐启用加密）

### 4.2 加密模式（填写 Encrypt Key）— 推荐

- 所有事件体包裹在 `{"encrypt": "<密文>"}` 中
- 必须提供 `X-Lark-Signature` 等签名头（用于验证请求来源）
- 必须同时配置 `verification_token` 和 `encrypt_key`

**签名算法**（与企微不同，注意区分）：

```
signature = SHA256(timestamp + nonce + encrypt_key + raw_body)
```

- `encrypt_key` 使用**原始字符串**（不是 AES key）
- 签名在**原始 body**（未解密）上计算

**AES 密钥派生**：

```
aes_key = SHA256(encrypt_key)  # 32 字节
```

**IV 来源**（与企微相反）：

```
iv = base64_decode(ciphertext)[:16]  # 密文的前 16 字节
```

> ⚠️ 企微：IV 来自 Base64 解码后的 key；飞书：IV 来自 Base64 解码后的 ciphertext。实现时不要混淆。

### 4.3 模式对比

| 项目 | 非加密模式 | 加密模式 |
|------|-----------|---------|
| 配置复杂度 | 低 | 中 |
| 安全性 | 低（明文传输） | 高（签名 + 加密） |
| 生产环境适用 | ❌ 不推荐 | ✅ 推荐 |
| 实现路径 | `adapter.crypto = None` | `adapter.crypto = FeishuCrypto(...)` |

---

## 5. 联调验证

### 5.1 url_verification 验证

飞书在保存回调 URL 时自动触发，通过即表示基本链路通。

**日志关键字**：

```
[Tenant Feishu] url_verification 成功: tenant=xxx
```

**失败排查**：

| 错误 | 原因 | 处理 |
|------|------|------|
| `token mismatch` | `verification_token` 配置错误 | 核对飞书后台与系统侧配置 |
| `invalid signature` | 加密模式下签名校验失败 | 检查 `encrypt_key` 一致性 |
| 404 | 租户 ID 错误或无配置 | 核对 URL 路径和管理后台配置 |

### 5.2 发送测试消息

1. 在飞书客户端找到已上线的机器人，发送一条「你好」
2. 期望日志：
   ```
   [Tenant Feishu] 收到消息: tenant=xxx, user=ou_xxx, type=text
   [Tenant Feishu] 调度后台处理: tenant=xxx, event_id=evt_xxx
   ```
3. 机器人应回复（具体回复内容由智能体逻辑决定）

### 5.3 群聊测试

1. 把机器人拉入群聊
2. @机器人 + 消息文字
3. 期望日志中出现 `@机器人` 占位符被还原为 `@机器人` 或 `@用户名`

> 群聊中**必须 @ 机器人**才会触发处理，避免对无关消息响应。

### 5.4 事件去重验证

飞书在未收到 2xx 响应或超时时会重试推送（最多 3 次）。通过日志过滤：

```
[Tenant Feishu] 重复事件: tenant=xxx, event_id=evt_xxx
```

表示去重生效，同一事件未被重复处理。

---

## 6. 消息处理架构

```
飞书回调 POST
   │
   ▼
[1] v2.0 签名校验（原始 body，解密前）
   │
   ▼
[2] 解密（如启用加密模式）
   │
   ▼
[3] url_verification 快速返回 challenge
   │
   ▼
[4] event_id 去重（PostgreSQL，TTL 5 分钟）
   │
   ▼
[5] 仅处理 im.message.receive_v1，其他事件返回 200
   │
   ▼
[6] asyncio.create_task 调度后台处理，路由立即返回 200
   │
   ▼
[7] 后台：parse_message → 主智能体 → send_text/send_long_message
```

**关键点**：

- 飞书要求 **2 秒内**返回 2xx，否则触发重试
- 所有耗时操作（智能体调用、媒体下载）都走 `asyncio.create_task`
- 去重基于 `event_id`（不是 `message_id`），避免重试被误认为新消息

---

## 7. 故障排查

### 7.1 url_verification 失败

**现象**：飞书后台保存 URL 时报「返回数据不是合法 JSON」或「challenge 不匹配」

**排查**：

1. 检查 `verification_token` 是否一致（飞书后台 vs 系统配置）
2. 加密模式下，检查 `encrypt_key` 是否一致
3. 加密模式下，确认 `X-Lark-Signature` 头存在（飞书 SDK 自动附加）
4. 查看系统日志，定位 `url_verification` 阶段的 4xx 响应

### 7.2 签名校验失败（403 invalid signature）

**常见原因**：

1. `encrypt_key` 配置错误（多一个空格、少一个字符）
2. 反向代理修改了 body 内容（如 Nginx 压缩、body 改写）
3. 时间戳偏差过大（飞书允许 ±1 小时）

**排查**：

```bash
# 临时打印签名对比
# 在 crypto.py 的 verify_signature 入口临时加 logger.debug
```

### 7.3 消息未触发处理

**现象**：日志显示 `event ignored` 或 `重复事件`

**排查**：

| 日志 | 原因 |
|------|------|
| `event ignored` | 事件类型不是 `im.message.receive_v1`，正常忽略 |
| `重复事件` | 5 分钟内相同 `event_id` 已处理，去重拦截 |
| 无日志 | 群聊未 @ 机器人，被 `parse_message` 忽略 |

### 7.4 发送消息失败

**常见错误码**（飞书 `code` 字段）：

| code | 含义 | 处理 |
|------|------|------|
| 99991663 | tenant_access_token 过期 | 系统自动刷新重试，无需人工干预 |
| 230001 | image not found | 检查 image_key 是否正确 |
| 230010 | invalid image | 图片格式或大小不合规 |
| 230011 | file too large | 文件大小超限（飞书限制约 30MB） |

**日志关键字**：

```
[Feishu] 发送消息失败: code=xxx, msg=xxx
```

### 7.5 tenant_access_token 刷新失败

**现象**：所有 API 调用失败，日志报 `token refresh failed`

**排查**：

1. 检查 `app_id` + `app_secret` 是否正确
2. 确认应用已发布且企业管理员已审批
3. 网络是否能访问 `https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`

---

## 8. 安全注意事项

1. **敏感信息加密**：`app_secret`、`encrypt_key` 在数据库中以密文存储，日志中脱敏显示
2. **HTTPS 必须**：飞书强制要求回调地址为 HTTPS，生产环境必须配置 TLS
3. **签名校验必须启用**：生产环境必须配置 `encrypt_key`，否则无法校验请求来源
4. **速率限制**：每用户每窗口 10 条，防止单用户耗尽飞书 API 配额
5. **事件去重**：基于 PostgreSQL 的分布式去重，多 worker 部署安全
6. **回调 URL 路径含 tenant_id**：每个租户独立回调路径，避免配置串扰

---

## 附录 A：路由与接口一览

| 接口 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 飞书回调（兼容） | GET | `/t/{tenant_id}/feishu/callback` | 飞书后台「请求网址配置」阶段（部分版本用 GET） |
| 飞书回调（主） | POST | `/t/{tenant_id}/feishu/callback` | 所有事件推送入口 |

**请求头**（加密模式）：

| Header | 说明 |
|--------|------|
| `X-Lark-Signature` | 请求签名 |
| `X-Lark-Request-Timestamp` | 时间戳（秒） |
| `X-Lark-Request-Nonce` | 随机数 |

---

## 附录 B：飞书事件字段参考

### im.message.receive_v1（消息接收）

```json
{
  "header": {
    "event_id": "evt_xxx",
    "event_type": "im.message.receive_v1",
    "create_time": "1700000000000",
    "token": "verification_token",
    "app_id": "cli_xxx",
    "tenant_key": "tenant_xxx"
  },
  "event": {
    "sender": {
      "sender_id": {"open_id": "ou_xxx", "user_id": "", "union_id": ""},
      "sender_type": "user"
    },
    "message": {
      "message_id": "msg_xxx",
      "create_time": "1700000000000",
      "chat_id": "oc_xxx",
      "chat_type": "p2p",        // "p2p" 或 "group"
      "message_type": "text",    // "text" / "image" / "file" / ...
      "content": "{\"text\":\"hello\"}",  // JSON 字符串
      "mentions": []
    }
  }
}
```

### url_verification

```json
{
  "type": "url_verification",
  "token": "verification_token",
  "challenge": "challenge_xxx"
}
```

**期望返回**：`{"challenge": "challenge_xxx"}`

---

## 对接检查清单

实施前按此清单确认：

- [ ] 飞书开放平台：应用已创建，记录 App ID 和 App Secret
- [ ] 权限：`im:message`、`im:message:send_as_bot`、`im:resource` 已开通
- [ ] 机器人能力已启用
- [ ] 事件订阅：`im.message.receive_v1` 已添加
- [ ] Encrypt Key 已生成（推荐启用加密模式）
- [ ] Verification Token 已复制
- [ ] 系统侧配置已填写（管理后台 或 config.yaml）
- [ ] 回调 URL 已填写并通过 url_verification
- [ ] 应用已发布、管理员已审批
- [ ] 用飞书客户端发测试消息，机器人有回复
