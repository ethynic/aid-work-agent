# 钉钉接入智能体 - 实施文档

本文档指导完成钉钉开放平台应用创建、回调配置、系统侧配置、签名验证模式选择，以及联调与排障。

## 目录

- [1. 实施概览](#1-实施概览)
- [2. 钉钉开放平台配置](#2-钉钉开放平台配置)
- [3. 系统侧配置](#3-系统侧配置)
- [4. 签名验证机制](#4-签名验证机制)
- [5. 联调验证](#5-联调验证)
- [6. 消息处理架构](#6-消息处理架构)
- [7. 故障排查](#7-故障排查)
- [8. 安全注意事项](#8-安全注意事项)
- [附录 A：路由与接口一览](#附录-a路由与接口一览)
- [附录 B：钉钉事件字段参考](#附录-b钉钉事件字段参考)

---

## 1. 实施概览

### 已实现能力

| 能力 | 模块 | 状态 |
|------|------|------|
| HmacSHA256 签名验证（timestamp + "\n" + appSecret） | `src/channels/dingtalk/crypto.py` | ✅ |
| 消息接收（JSON 格式，支持文本/图片/文件/富文本） | `DingTalkAdapter.parse_message` | ✅ |
| 群聊 @机器人 识别 | `parse_message` + conversationType 判断 | ✅ |
| 发送单聊消息（oToMessages/batchSend） | `send_text` / `send_long_message` | ✅ |
| 发送群聊消息（groupMessages/send） | `send_group_message` | ✅ |
| 长消息三级拆分（段落 → 行 → 字节） | `send_long_message` | ✅ |
| 图片/文件 下载 + 上传 | `src/channels/dingtalk/media.py` | ✅ |
| access_token 并发刷新锁 | `_refresh_access_token` | ✅ |
| 每 user 滑动窗口速率限制 | `_check_rate_limit` | ✅ |
| 事件去重（基于 `msgId`，TTL 5 分钟） | `channel_routes._get_dingtalk_event_dedup` | ✅ |
| 路由立即返回 2xx + 后台异步处理 | `asyncio.create_task` | ✅ |
| 多租户配置（管理后台 UI） | `ChannelConfig` 表 + `channel_type=dingtalk` | ✅ |

### 回调端点

| 方法 | 路径 | 触发时机 | 说明 |
|------|------|----------|------|
| POST | `/t/{tenant_id}/dingtalk/callback` | 用户发消息 / 各类事件 | 签名校验 → 解析 → 去重 → 异步处理 |

> 钉钉不使用 url_verification challenge 机制，而是在开放平台配置回调 URL 时直接验证（返回 200 即可）。

### 接入架构

```
钉钉用户 ←→ 钉钉服务器 ←(HTTPS 回调)→ 你的服务器 (AID Work Agent)
                                    ↑                    ↓
                         /t/{tenant_id}/dingtalk/callback   Agent 处理 + 主动消息 API
```

---

## 2. 钉钉开放平台配置

### 2.1 创建应用

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/)
2. 进入「应用开发」→「企业内部应用」→「创建应用」
3. 记录 `AppKey` 和 `AppSecret`（对应飞书的 App ID / App Secret）

### 2.2 配置权限

进入「权限管理」页面，按需开通：

| 权限 | 用途 | 必需 |
|------|------|------|
| `qyapi_robot_sendmsg` | 机器人发送消息 | ✅ |
| `qyapi_chat_manage` | 群会话管理 | 群聊场景必需 |
| `qyapi_media_upload` | 媒体文件上传 | 处理附件时必需 |
| `qyapi_user_read` | 读取用户信息 | 可选 |

### 2.3 开启机器人能力

「应用能力」→「机器人」→ 启用

### 2.4 配置事件订阅

「事件订阅」页面：

1. **请求网址 URL**：填入 `https://your-domain.com/t/{tenant_id}/dingtalk/callback`
2. **加密方式**：钉钉不使用 Encrypt Key，签名验证通过 AppSecret 实现
3. **添加事件**：
   - `chat_add_member` — 群成员加入（可选）
   - `chat_remove_member` — 群成员移除（可选）
   - `chat_disband` — 群解散（可选）

> ⚠️ 钉钉在保存回调 URL 时会发起一次验证请求（POST，带签名头），必须通过才能保存成功。

### 2.5 发布应用

完成权限申请 → 创建版本 → 申请发布 → 企业管理员审批通过 → 应用上线

---

## 3. 系统侧配置

### 3.1 多租户模式（推荐）

通过管理后台「渠道配置」页面添加钉钉渠道：

| 字段 | 来源 | 必填 |
|------|------|------|
| `channel_type` | 固定 `dingtalk` | ✅ |
| `app_key` | 钉钉开放平台 AppKey | ✅ |
| `app_secret` | 钉钉开放平台 AppSecret | ✅ |
| `welcome_message` | 首次会话欢迎消息 | ❌ |

保存后系统会为该租户创建一条 `ChannelConfig` 记录，`tenant_id` 自动绑定。

回调 URL 中的 `{tenant_id}` 路径段由系统从 URL 解析，无需在配置中显式指定。

### 3.2 单租户模式（config.yaml）

非 SaaS 部署且不方便用管理后台时，可直接在 `configs/config.yaml` 中配置（注意:项目已不再读取 `DINGTALK_*` 环境变量,需将凭证明文填入配置）：

```yaml
channels:
  dingtalk:
    enabled: true
    app_key: "your_dingtalk_app_key"
    app_secret: "your_dingtalk_app_secret"
    welcome_message: "你好，我是你的智能助手"
    max_bytes: 4000          # 单条消息最大字节数
    rate_limit_window: 60    # 速率限制窗口（秒）
    rate_limit_max: 10       # 每窗口最大消息数
```

> **历史变更**: 旧版本支持 `${DINGTALK_APP_KEY}` / `${DINGTALK_APP_SECRET}` 占位符,现已被管理后台取代,请使用上述明文配置或管理后台。

---

## 4. 签名验证机制

### 4.1 钉钉签名算法

钉钉使用 **HmacSHA256** 签名验证请求来源，与飞书/企微不同：

```python
import hmac
import hashlib
import base64

def verify_signature(timestamp: str, sign: str, app_secret: str) -> bool:
    """
    验证钉钉请求签名
    
    Args:
        timestamp: 请求头中的 timestamp（毫秒时间戳）
        sign: 请求头中的 sign（Base64 编码的签名）
        app_secret: 钉钉 AppSecret
    
    Returns:
        True 表示签名有效
    """
    # 构造待签名字符串：timestamp + "\n" + appSecret
    string_to_sign = f"{timestamp}\n{app_secret}"
    
    # 使用 HmacSHA256 算法，密钥为 appSecret
    hmac_code = hmac.new(
        app_secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha256
    ).digest()
    
    # Base64 编码
    expected_sign = base64.b64encode(hmac_code).decode('utf-8')
    
    return sign == expected_sign
```

### 4.2 签名验证流程

1. 从请求头提取 `timestamp` 和 `sign`
2. 检查 timestamp 是否在合理范围内（±1 小时）
3. 按上述算法计算期望的签名
4. 比对计算结果与请求头中的 sign

### 4.3 与飞书/企微的对比

| 项目 | 钉钉 | 飞书 | 企微 |
|------|------|------|------|
| 签名算法 | HmacSHA256 | SHA256 | SHA1 |
| 签名内容 | timestamp + "\n" + appSecret | timestamp + nonce + encrypt_key + body | sort(token, timestamp, nonce) |
| 消息加密 | 无（HTTPS 传输） | AES-256-CBC | AES-256-CBC |
| 验证时机 | 每次请求 | 每次请求 | url_verification + 消息事件 |

### 4.4 为什么不使用消息加密

钉钉采用 HTTPS 传输 + 签名验证的组合，**不**对消息体进行加密。这与飞书/企微不同：

- **飞书/企微**：消息体包裹在 `{"encrypt": "<密文>"}` 中，需要 AES 解密
- **钉钉**：消息体为明文 JSON，通过 HTTPS 保证传输安全，通过签名验证保证来源可信

这种设计简化了实现，但要求生产环境**必须启用 HTTPS**。

---

## 5. 联调验证

### 5.1 回调 URL 验证

钉钉在保存回调 URL 时自动触发验证请求（POST，带签名头），通过即表示基本链路通。

**日志关键字**：

```
[Tenant DingTalk] 回调 URL 验证成功: tenant=xxx
```

**失败排查**：

| 错误 | 原因 | 处理 |
|------|------|------|
| `invalid signature` | 签名校验失败 | 检查 `app_secret` 配置 |
| `timestamp expired` | 时间戳偏差过大 | 检查服务器时间同步 |
| 404 | 租户 ID 错误或无配置 | 核对 URL 路径和管理后台配置 |

### 5.2 发送测试消息

1. 在钉钉客户端找到已上线的机器人，发送一条「你好」
2. 期望日志：
   ```
   [Tenant DingTalk] 收到消息: tenant=xxx, user=xxx, type=text
   [Tenant DingTalk] 调度后台处理: tenant=xxx, msgId=xxx
   ```
3. 机器人应回复（具体回复内容由智能体逻辑决定）

### 5.3 群聊测试

1. 把机器人拉入群聊
2. @机器人 + 消息文字
3. 期望日志中出现 `conversationType=2`（群聊）

> 群聊中**必须 @ 机器人**才会触发处理，避免对无关消息响应。

### 5.4 事件去重验证

钉钉在未收到 2xx 响应或超时时会重试推送。通过日志过滤：

```
[Tenant DingTalk] 重复事件: tenant=xxx, msgId=xxx
```

表示去重生效，同一事件未被重复处理。

---

## 6. 消息处理架构

```
钉钉回调 POST
   │
   ▼
[1] 签名校验（HmacSHA256，timestamp + "\n" + appSecret）
   │
   ▼
[2] 解析 JSON 消息体
   │
   ▼
[3] msgId 去重（PostgreSQL，TTL 5 分钟）
   │
   ▼
[4] asyncio.create_task 调度后台处理，路由立即返回 200
   │
   ▼
[5] 后台：parse_message → 主智能体 → send_text/send_long_message
```

**关键点**：

- 钉钉要求 **3 秒内**返回 2xx，否则触发重试
- 所有耗时操作（智能体调用、媒体下载）都走 `asyncio.create_task`
- 去重基于 `msgId`（不是 conversationId），避免重试被误认为新消息
- 钉钉不使用 url_verification challenge，直接验证签名即可

---

## 7. 故障排查

### 7.1 回调 URL 验证失败

**现象**：钉钉后台保存 URL 时报「签名验证失败」或「返回非 200」

**排查**：

1. 检查 `app_key` + `app_secret` 是否一致（钉钉后台 vs 系统配置）
2. 确认服务器时间与标准时间偏差在 ±1 小时内
3. 查看系统日志，定位签名验证阶段的 4xx 响应

### 7.2 签名校验失败（403 invalid signature）

**常见原因**：

1. `app_secret` 配置错误（多一个空格、少一个字符）
2. 时间戳偏差过大（钉钉允许 ±1 小时）
3. 反向代理修改了请求头（如 Nginx 重写 header）

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
| `重复事件` | 5 分钟内相同 `msgId` 已处理，去重拦截 |
| 无日志 | 群聊未 @ 机器人，被 `parse_message` 忽略 |

### 7.4 发送消息失败

**常见错误码**（钉钉 `errcode` 字段）：

| errcode | 含义 | 处理 |
|---------|------|------|
| 40001 | access_token 过期 | 系统自动刷新重试，无需人工干预 |
| 40003 | 用户不存在 | 检查 userId 是否正确 |
| 40014 | 不合法的 access_token | 检查 app_key + app_secret |
| 45009 | API 调用次数超限 | 检查速率限制配置 |
| 48001 | 未授权调用接口 | 检查权限是否已申请 |

**日志关键字**：

```
[DingTalk] 发送消息失败: errcode=xxx, errmsg=xxx
```

### 7.5 access_token 刷新失败

**现象**：所有 API 调用失败，日志报 `token refresh failed`

**排查**：

1. 检查 `app_key` + `app_secret` 是否正确
2. 确认应用已发布且企业管理员已审批
3. 网络是否能访问 `https://api.dingtalk.com/v1.0/oauth2/accessToken`

---

## 8. 安全注意事项

1. **敏感信息加密**：`app_secret` 在数据库中以密文存储，日志中脱敏显示
2. **HTTPS 必须**：钉钉强制要求回调地址为 HTTPS，生产环境必须配置 TLS
3. **签名校验必须启用**：生产环境必须验证签名，否则无法校验请求来源
4. **速率限制**：每用户每窗口 10 条，防止单用户耗尽钉钉 API 配额
5. **事件去重**：基于 PostgreSQL 的分布式去重，多 worker 部署安全
6. **回调 URL 路径含 tenant_id**：每个租户独立回调路径，避免配置串扰

---

## 附录 A：路由与接口一览

| 接口 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 钉钉回调 | POST | `/t/{tenant_id}/dingtalk/callback` | 所有事件推送入口 |

**请求头**：

| Header | 说明 |
|--------|------|
| `timestamp` | 时间戳（毫秒） |
| `sign` | 请求签名（Base64 编码的 HmacSHA256） |

---

## 附录 B：钉钉事件字段参考

### 接收消息（文本）

```json
{
  "msgtype": "text",
  "text": {
    "content": "你好"
  },
  "msgId": "msg_xxx",
  "createAt": 1700000000000,
  "conversationType": "1",  // "1" 单聊，"2" 群聊
  "conversationId": "cid_xxx",
  "conversationTitle": "",  // 群聊时有值
  "senderId": "dingtalk_xxx",
  "senderNick": "张三",
  "senderStaffId": "user123",  // 企业内员工 ID
  "senderCorpId": "ding_xxx",
  "chatbotUserId": "dingtalk_bot_xxx",
  "robotCode": "robot_xxx",
  "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession",
  "sessionWebhookExpiredTime": 1700000000000,
  "isAdmin": false,
  "sessionTags": []
}
```

### 接收消息（图片）

```json
{
  "msgtype": "picture",
  "picture": {
    "downloadCode": "download_code_xxx",
    "pictureDownloadCode": "picture_download_code_xxx"
  },
  "msgId": "msg_xxx",
  "conversationType": "1",
  "senderId": "dingtalk_xxx",
  "robotCode": "robot_xxx",
  "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession"
}
```

### 接收消息（文件）

```json
{
  "msgtype": "file",
  "file": {
    "downloadCode": "download_code_xxx",
    "fileName": "document.pdf",
    "fileType": "pdf"
  },
  "msgId": "msg_xxx",
  "conversationType": "1",
  "senderId": "dingtalk_xxx",
  "robotCode": "robot_xxx",
  "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession"
}
```

### 发送单聊消息

```json
{
  "robotCode": "robot_xxx",
  "userIds": ["user1", "user2"],
  "msgKey": "sampleText",
  "msgParam": "{\"content\":\"你好\"}"
}
```

**请求**：`POST https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend`

### 发送群聊消息

```json
{
  "robotCode": "robot_xxx",
  "openConversationId": "cid_xxx",
  "msgKey": "sampleText",
  "msgParam": "{\"content\":\"你好\"}"
}
```

**请求**：`POST https://api.dingtalk.com/v1.0/robot/groupMessages/send`

### 获取 access_token

```json
{
  "appKey": "ding_xxx",
  "appSecret": "secret_xxx"
}
```

**请求**：`POST https://api.dingtalk.com/v1.0/oauth2/accessToken`

**响应**：

```json
{
  "accessToken": "token_xxx",
  "expireIn": 7200
}
```

---

## 对接检查清单

实施前按此清单确认：

- [ ] 钉钉开放平台：应用已创建，记录 AppKey 和 AppSecret
- [ ] 权限：`qyapi_robot_sendmsg`、`qyapi_chat_manage` 已开通
- [ ] 机器人能力已启用
- [ ] 事件订阅 URL 已配置并通过验证
- [ ] 系统侧配置已填写（管理后台 或 config.yaml）
- [ ] 应用已发布、管理员已审批
- [ ] 用钉钉客户端发测试消息，机器人有回复
- [ ] 群聊测试：@机器人能正常回复
- [ ] 生产环境已启用 HTTPS
