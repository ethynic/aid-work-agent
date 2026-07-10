# 企业微信个人账号 RPA 线协议（共享契约）

> 本文件是服务端实现 agent 与 C# 客户端实现 agent 的**唯一共享协议文档**。
> 所有下游 agent 在编码前必须 Read 本文件。
> Pydantic 权威模型见 `src/channels/wecom_personal_rpa/schemas.py`；表结构见 `deploy/init-postgres.sql` 与 `deploy/db_update.sql`。
> 创建日期：2026-06-22
> 协议版本：`PROTOCOL_VERSION = "1.0.0"`（见 `schemas.py`）
> 关联设计：[wecom-personal-rpa-design.md](./wecom-personal-rpa-design.md)
> 关联调研：[../research/wecom-personal-rpa-client-implementation-research.md](../research/wecom-personal-rpa-client-implementation-research.md)

---

## A. 线协议逐字段说明

### A.0 配置标识与归属

`GET /api/v1/channels/wecom-personal-rpa/config` 仅向已鉴权客户端下发
`config.client_id` 与当前 `X-Client-Id` 完全一致的渠道配置。响应中的 `config_id`
统一使用稳定业务标识（`chan_*`），不得返回数据库数字主键，也不得回退到租户内第一条
配置。没有明确归属时返回 `config_id: null`，由管理员在后台完成分配；服务端不会把其他
客户端或未分配配置暴露给调用方。

WebSocket 路径以业务标识为准：
`/t/{tenant_id}/wecom_personal_rpa/ws/{config_id}`。为兼容已安装旧客户端，服务端暂时也
接受同一记录的历史数字主键，但解析时必须同时匹配 `tenant_id` 和
`channel_type=wecom_personal_rpa`。已有归属不能通过 WebSocket 覆盖；未归属配置只有在
管理员通过可信配置流程明确提供其标识后，才允许合法客户端首次认领。

### A.1 鉴权头（所有客户端 → 服务端请求必带）

| 头名 | schemas 常量 | 说明 |
|------|--------------|------|
| `X-Client-Id` | `HEADER_CLIENT_ID` | 客户端身份标识，注册时由服务端分配（如 `client_001`） |
| `X-Timestamp` | `HEADER_TIMESTAMP` | Unix 秒级时间戳，字符串形式 |
| `X-Nonce` | `HEADER_NONCE` | 一次性随机串，10 分钟内不可重复 |
| `X-Signature` | `HEADER_SIGNATURE` | HMAC-SHA256 签名，小写十六进制 |

**校验规则（`auth.verify_request`）**：

- `client_id` 必须存在且 `status='active'`，否则返回 `auth_failed` 或 `client_disabled`。
- 时间戳偏移超过 `TIMESTAMP_TOLERANCE_SECONDS = 300` 秒拒绝（`auth_failed`）。
- `nonce` 在 `NONCE_TTL_SECONDS = 600` 秒内不可重复（Redis 防重放，见下）。
- HMAC 使用常量时间比较（`hmac.compare_digest`）。

**签名串构造（`auth.compute_signature`）**：

```
sig = hmac_sha256(
    key   = client_secret_bytes,        # 服务端从 encrypted_secret 解密得到
    msg   = client_id + timestamp + nonce + raw_body,
            # 全部 ASCII/UTF-8 原始字节直接拼接，无分隔符
).hexdigest()                           # 小写十六进制
```

- `raw_body` 必须是收到的原始请求体字节，**不得**做任何 `json.loads` 后再 `json.dumps` 的 re-serialize（字段顺序/空白变化会改变签名）。
- `client_secret` 仅在服务端解密后参与签名，**任何 API 响应、日志、错误信息中不得出现明文 secret**。

**nonce 防重放**：使用 `src/core/redis_client.RedisClient`，键 `wecom_rpa:nonce:{client_id}:{nonce}`，`SET NX EX 600`；Redis 不可用时降级内存（记录 `logger.warning`）。

### A.2 入站回调信封

客户端 `POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}` 的 body 顶层：

```json
{
  "event_id": "evt_20260622_001",
  "client_id": "client_001",
  "account_id": "wecom_account_001",
  "event_type": "message",
  "occurred_at": "2026-06-22T10:00:00+08:00",
  "payload": { /* 见 A.3 / A.4 / A.5 */ }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | str | 客户端生成，全局稳定；重传同一事件必须携带相同值 |
| `client_id` | str | 与 `X-Client-Id` 一致，服务端以 body 为准做归属校验 |
| `account_id` | str | 事件归属的个人企微账号 ID |
| `event_type` | `"message"` \| `"status"` \| `"action_result"` | 决定 payload 结构 |
| `occurred_at` | datetime(ISO 8601) | 客户端本地事件时间 |
| `payload` | dict | 见 A.3 / A.4 / A.5 |

### A.3 event_type=message 的 payload（`RpaMessagePayload`）

```json
{
  "conversation_id": "binding_abc",
  "conversation_type": "external_user",
  "sender_display_name": "张三",
  "sender_stable_id": null,
  "message_type": "text",
  "text": "你好",
  "attachments": []
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `conversation_id` | str | 客户端本地会话标识，用于路由 |
| `conversation_type` | `"internal_user"` \| `"internal_group"` \| `"external_user"` \| `"external_group"` | 会话语义 |
| `sender_display_name` | str | 发送人显示名（可能重名） |
| `sender_stable_id` | str \| null | 稳定 ID（external_userid/userid/room_id），首版可空 |
| `message_type` | `"text"` \| `"image"` \| `"file"` \| `"voice"` \| `"video"` \| `"link"` | 内容类型 |
| `text` | str \| null | 文本内容（`message_type=text` 时必填） |
| `attachments` | `List[RpaAttachment]` | 附件列表 |

`RpaAttachment`：`type:str`、`url:str=""`、`name:str|null`、`size:int|null`、`mime_type:str|null`。

### A.4 event_type=status 的 payload（`RpaStatusPayload`）

```json
{
  "status": "need_login",
  "account_display_name": "销售-王经理",
  "detail": "二维码已展示，等待扫码",
  "qr_image_ref": "tmp://qr/abc.png",
  "qr_image_base64": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `"online"` \| `"offline"` \| `"need_login"` \| `"qr_expired"` \| `"account_limited"` \| `"desktop_locked"` \| `"window_not_visible"` \| `"paused"` \| `"recovering"` | 账号/桌面状态 |
| `account_display_name` | str \| null | 账号显示名 |
| `detail` | str \| null | 脱敏补充说明 |
| `qr_image_ref` | str \| null | **短期**二维码引用，不长期存储；日志禁止打印 |
| `qr_image_base64` | str \| null | base64 PNG 二维码，30s TTL；优先于 `qr_image_ref`；不入审计/DB |

### A.5 event_type=action_result 的 payload（`RpaActionResultPayload`）

```json
{
  "request_id": "req_xxx",
  "action_result_id": "res_001",
  "action_index": 0,
  "action_type": "send_text",
  "success": true,
  "error_code": null,
  "error_message": null,
  "executed_at": "2026-06-22T10:00:05+08:00"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `request_id` | str | 对应 `ActionEnvelope.request_id` |
| `action_result_id` | str | 客户端生成的回执唯一 ID，用于幂等去重 |
| `action_index` | int | 对应 `actions` 列表下标（从 0） |
| `action_type` | str | 回执对应的 action.type |
| `success` | bool | 执行是否成功 |
| `error_code` | str \| null | 失败时的错误码（见 A.8） |
| `error_message` | str \| null | 脱敏失败说明 |
| `executed_at` | datetime | 实际执行完成时间 |

### A.6 出站 actions（服务端 → 客户端，`ActionEnvelope`）

WebSocket 推送或离线拉取（`GET /api/v1/channels/wecom-personal-rpa/outbox`）共用此结构：

```json
{
  "request_id": "req_xxx",
  "session_id": "wecom_personal_rpa:wecom_account_001:binding_abc",
  "account_id": "wecom_account_001",
  "conversation_id": "binding_abc",
  "reply_context": {
    "sender_display_name": "张三",
    "sender_stable_id": "wm_xxx",
    "conversation_search_name": "张三",
    "inbound_text": "请发一份报价单",
    "agent_reply_text": "您好，报价单已发送。"
  },
  "actions": [
    {"type": "send_text", "text": "您好，已收到。"},
    {"type": "send_file", "file_url": "https://agent.example.com/files/xxx?sig=...", "filename": "报价单.xlsx"}
  ]
}
```

`reply_context` 是 v1.1.0 新增的可选兼容字段，用于让客户端校验本次回复对应的发送人、
入站文本和 Agent 回复。旧服务端/历史 outbox 可不含该字段，旧客户端也必须忽略未知字段。
`conversation_id` 是服务端幂等/会话路由稳定标识，不能假定企微桌面端可搜索；
`conversation_search_name` 是管理员确认的客户端搜索名称。服务端生成该字段时仅移除
`sender_display_name` 末尾精确后缀 `@微信` 并 trim（例如 `陆伟@微信` → `陆伟`），
不替换中间文本或其他后缀。客户端以 `conversation_search_name` 定位企微窗口，以
`actions` 顺序执行；旧信封缺字段时可对 `sender_display_name` 做同样归一化。若两者均
缺失，客户端必须拒绝执行，禁止用不可搜索的 `conversation_id` / `session_id` 盲搜。
不得把 `agent_reply_text` 再执行一次。
日志只能记录 request_id、conversation_id 和 sender_stable_id 等定位信息，禁止输出
`inbound_text`、`agent_reply_text` 完整正文。

服务端 archive 模式的内部 `account_id` 使用
`rpa_acct_<sha256(tenant_id + NUL + logical_account_key)[:24]>`。其中
`logical_account_key` 优先取渠道 `config.account_id`，未配置时取稳定的 `config_id`；
`subagent_type` 仅用于 Agent 路由，禁止作为账号主键。这样同一配置重启后 ID 不变，
不同租户即使都使用 `travel-consultant` 也不会冲突。

历史版本若已用 `subagent_type` 生成账号 ID，升级前应在事务中将
`wecom_rpa_accounts.id`、`wecom_rpa_conversation_bindings.account_id` 和
`wecom_rpa_action_outbox.account_id` 同步迁移到上述新 ID；迁移前应暂停对应渠道，
确认旧 ID 只属于当前租户且不存在 pending/running outbox，完成后再恢复。不要只改账号表，
否则历史 active binding 会丢失关联。

五类 action（`Union` 判别字段 `type`）：

| type | 字段 | 说明 |
|------|------|------|
| `send_text` | `text:str` | 文本回复 |
| `send_image` | `file_url:str`、`filename:str?` | 图片（短期签名 URL） |
| `send_file` | `file_url:str`、`filename:str` | 文件（短期签名 URL + 文件名必填） |
| `noop` | （无） | 仅记录、不自动回复 |
| `handoff` | `reason:str?` | 转人工，客户端暂停该会话或账号 |

执行规则：
- 同一会话 actions 顺序执行；同一账号全局串行。
- 文件类 action 先下载到客户端临时目录再发送，完成后删除临时文件。
- 任一 action 失败上报回执，按策略停止后续 action 或转人工。

### A.7 配置下发（`RpaConfigResponse`，`GET /api/v1/channels/wecom-personal-rpa/config`）

```json
{
  "protocol_version": "1.1.0",
  "min_client_version": "1.0.0",
  "paused": false,
  "paused_scope": null,
  "rate_limits": {"per_minute": 5, "per_day": 100, "consecutive_failure_pause": 2},
  "server_time": "2026-06-22T10:00:00+08:00",
  "archive_enabled": false,
  "monitor_users": {
    "binding_abc": {"user_names": ["陆伟"], "user_ids": ["wm_xxx"]}
  }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `archive_enabled` | bool | 服务端是否启用会话存档（客户端按此选择监听方式） |
| `monitor_users` | Dict[str, MonitorUsersEntry] | 绑定级监控白名单（key=binding_id，仅含白名单非空的 binding） |

`MonitorUsersEntry` 结构：

```json
{"user_names": ["陆伟"], "user_ids": ["wm_xxx"]}
```

- `user_names`：服务端不可见的客户端维度用户标识（用于审计显示）
- `user_ids`：企业微信内部 ID（用于精确匹配 message payload）

#### monitor_users 字段使用说明

客户端拿到 `monitor_users` dict 后，需要通过以下方式找到当前会话对应的 binding_id：

1. **会话存档模式**（archive）：会话存档 API 返回的 `from` + `tolist`/`roomid` 直接是稳定 ID，
   客户端用这些 ID 反查 binding（按 monitor_user_ids 匹配）。匹配规则见客户端设计文档 §F6。
2. **绑定关系已知**：客户端启动时拉取 `/config`，dict 的 key（binding_id）本身就是
   客户端注册时分配的会话标识，客户端本地维护 binding_id ↔ 当前会话的映射。

如果当前会话的 binding_id 不在 `monitor_users` dict 里，说明该 binding 没配置白名单
（按"监控所有"处理）。

### A.8 错误码语义（`RpaErrorResponse`）

所有 4xx/5xx 统一信封：`{"error": <ErrorCode>, "message": <str>, "debug": <str|null>}`。
`debug` 字段在序列化前**必须脱敏**（剔除 secret/token/signature/绝对路径），生产环境可空。

| error | 含义 | 客户端处理 |
|-------|------|------------|
| `auth_failed` | 签名/token/时间戳/nonce 校验失败 | 停止请求并报警 |
| `client_disabled` | 客户端被禁用 | 暂停本地托管 |
| `account_paused` | 账号被服务端暂停 | 不再投递消息给 agent，可继续上报健康 |
| `conversation_needs_review` | 会话需要人工绑定（重名/未确认） | 暂停该会话自动发送 |
| `agent_timeout` | agent 推理超时 | 本地稍后重试或转人工 |
| `unsupported_action` | 客户端不支持该回复类型 | 上报失败，服务端降级文本或转人工 |
| `bad_request` | 请求体格式错误 | 不重试，记录日志 |
| `unsupported_file_type` | 媒体上传文件扩展名不在白名单（见 §A.10） | 不重试，向用户提示支持的类型 |
| `internal_error` | 服务端内部错误 | 指数退避重试 |
| `monitor_whitelist_filtered` | 白名单过滤（消息发送方不在 `monitor_users` 白名单） | 不算错误，写审计但不投递 agent |

### A.9 幂等键

所有幂等键必须带 `wecom_personal_rpa:` 前缀，避免与其它渠道在 `channel_message_dedup` 表碰撞：

| 场景 | 幂等键 | 去重存储 |
|------|--------|----------|
| 入站事件去重 | `wecom_personal_rpa:{tenant_id}:{event_id}` | `channel_message_dedup` 表（复用 `MessageDeduplicator`） |
| action 回执去重 | `wecom_personal_rpa:{tenant_id}:{action_result_id}` | `channel_message_dedup` 表 |
| 出站动作入队去重 | `wecom_rpa:{tenant_id}:{request_id}` | `wecom_rpa_action_outbox.dedup_key` UNIQUE 约束 |

### A.10 媒体上传（`POST /api/v1/channels/wecom-personal-rpa/media-upload`）

客户端把会话存档拿到的图片/文件回传给服务端，换取 24 小时短期签名下载 URL，
供后续 agent 推理或审计使用。

**请求**

- 路径：`POST /api/v1/channels/wecom-personal-rpa/media-upload`
- Content-Type：`multipart/form-data`，字段 `file`
- 鉴权：HMAC-SHA256，**与 callback 同款请求头**（`X-Client-Id` / `X-Timestamp` / `X-Nonce` / `X-Signature`）

**签名约定（重要）**：HMAC body **不是** multipart 原始字节（boundary 在不同 HTTP
客户端实现里差异巨大、签名校验脆弱），而是**固定占位串**：

```text
HMAC-SHA256(key=client_secret, message="media-upload")
```

即客户端按下式构造签名输入（参考 `src/channels/wecom_personal_rpa/auth.py:compute_signature`）：

```text
raw_body = b"media-upload"   # 固定占位串，不要写 multipart 原始字节
message = f"{client_id}\n{timestamp}\n{nonce}\n".encode() + raw_body
signature = hmac_sha256(secret, message).hexdigest()
```

**文件大小限制**：100 MB（路由常量 `_MEDIA_MAX_SIZE_BYTES = 100 * 1024 * 1024`）。
路由先用 `Content-Length` 头做预检（+1024 字节余量给 multipart 开销），超限直接 413。

**文件类型白名单**：仅允许以下扩展名（小写，文件名取 `os.path.splitext` 后比较）：

```
png, jpg, jpeg, gif, bmp, webp, pdf, docx, xlsx, pptx, zip, txt, csv
```

不在白名单的扩展名（如 `.exe`/`.bat`/`.ps1`/`.js`）返回 `unsupported_file_type`（HTTP 400），
错误响应 `message` 字段包含当前允许的扩展名列表，方便客户端调试。

**响应**：

```json
{
  "file_id": "a1b2c3d4e5f6_fixture.png",
  "url": "/api/v1/channels/wecom-personal-rpa/files/a1b2c3d4e5f6_fixture.png?tenant_id=tenant_xxx&sig=1719038400.7c8d...",
  "expires_at": "2026-06-23T10:00:00+08:00",
  "size": 12345
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `file_id` | str | 存储文件名（`{uuid12}_{original_filename}`，原文件名取 basename 并截断 64 字符） |
| `url` | str | 24h 短期签名下载 URL（`GET /files/{file_id}?tenant_id=...&sig=...`） |
| `expires_at` | datetime | URL 过期时间（ISO，本地时区） |
| `size` | int | 实际写入字节数 |

**存储路径**：`storage/tenants/{tenant_id}/conversation/`（统一走 `src/core/storage.py`
工具函数，遵循 `.claude/rules/backend_dev.md` 租户附件存储规范）。

**URL TTL**：24 小时（路由常量 `_MEDIA_TOKEN_TTL_SECONDS = 24 * 3600`）。

**错误码**：参考 §A.8。鉴权失败 → 401 `auth_failed`；超限 → 413 `bad_request`；
内部错误 → 500 `internal_error`。

### A.11 WebSocket 推送事件（服务端 → 客户端）

客户端通过 WebSocket 长连接接收服务端的主动推送事件。鉴权见 §A.1（连接建立时携带），事件载荷统一格式：

```json
{ "type": "<event-type>", "scope": "<scope>", "conversation_id": "<可选>" }
```

| `type` | 含义 | 必填字段 | 客户端响应 |
|--------|------|----------|-----------|
| `paused` | 暂停指令 | `scope`、`conversation_id`（仅 conversation scope） | 调 `ClientSession.PauseAsync` 写入 `PauseState` |
| `resumed` | 恢复指令 | `scope`、`conversation_id`（仅 conversation scope） | 调 `ClientSession.ResumeAsync` 清除 `PauseState` |
| `actions` | 服务端推送 ActionEnvelope（出站指令） | `request_id`、`actions[]` 等（见 §A.6） | 解析后调 `OutboundActionDispatcher.EnvelopeEnqueueAsync` 入队本地 outbox |
| `config_invalidate` | 配置失效通知（如监控白名单变更） | 无 | 调 `MonitorUsersCache.RefreshAsync` 强制刷新白名单缓存 |

**`scope` 字段语义**（`paused` / `resumed` 共用）：

| scope | 含义 | 客户端动作 |
|-------|------|-----------|
| `tenant` | 整租户暂停 | `PauseState.SetTenantPaused(true)`，所有出站动作停止、ChatArchiveListener 暂停上报 |
| `account` | 单账号暂停（当前客户端绑定账号） | `PauseState.SetAccountPaused(true)`，该账号所有动作停止 + ChatArchiveListener 暂停；其他账号继续 |
| `conversation` | 单会话暂停（其他会话不受影响） | `PauseState.PauseConversation(conversation_id)`，仅该会话的 `OutboundActionDispatcher` 跳过执行；InboundEventReporter 不受影响 |

**心跳保活**：WebSocket 心跳靠 TCP keepalive + 客户端定时检查 `ClientWebSocket.State == Open` 实现，**不依赖应用层 pong**。客户端定时（默认 30s）发空字节 ping 作为 keepalive（部分代理对长连接静默有超时），离线检测以 `State != Open` 为准。

**未知 `type` / `scope`**：客户端记录 warning 后丢弃单条，不抛异常、不传染（保证后续事件流不被破坏）。

---

## B. Python 模块函数签名契约（服务端实现 agent 必须逐字对齐）

下游服务端 agent 在阶段2并行实现以下模块。签名一经锁定不得变更。

### B.1 `src/channels/wecom_personal_rpa/auth.py`

```python
from dataclasses import dataclass
from typing import Callable, Optional

@dataclass
class VerifyResult:
    ok: bool
    client_id: Optional[str]   # 校验通过时为请求 client_id，失败时 None
    error: Optional[str]       # 失败时为 ErrorCode 字面量，成功时 None

def compute_signature(
    client_id: str,
    timestamp: str,
    nonce: str,
    body: str | bytes,
    secret: bytes,
) -> str:
    """返回小写十六进制 HMAC-SHA256。
    body 接受 str 或 bytes；str 时按 UTF-8 编码。
    """

def verify_request(
    headers: dict,
    raw_body: str | bytes,
    get_secret: Callable[[str], bytes | None],
) -> VerifyResult:
    """完整校验：client_id 存在性 + 时间戳窗口 + nonce 防重放 + 签名常量时间比较。
    get_secret(client_id) 返回解密后的 secret bytes；客户端不存在/禁用时返回 None。
    任一步骤失败返回 VerifyResult(ok=False, error=<ErrorCode>)。
    """
```

### B.2 `src/channels/wecom_personal_rpa/router.py`

```python
class WeComPersonalRpaRouter:
    """account_id + conversation_id → session_id 路由器。"""

    def route(
        self,
        account_id: str,
        conversation_id: str,
        stable_id: Optional[str] = None,
    ) -> str:
        """返回 'wecom_personal_rpa:{account_id}:{stable_id or conversation_id}'。
        route_key = stable_id or conversation_id。
        """
```

### B.3 `src/channels/wecom_personal_rpa/message.py`

```python
from src.models.message import UnifiedMessage
from src.channels.wecom_personal_rpa.schemas import (
    RpaMessagePayload, RpaStatusPayload, RpaActionResultPayload,
)

def parse_rpa_message(raw: dict) -> UnifiedMessage:
    """raw 为 RpaCallbackEnvelope.payload（event_type=message 分支）。
    返回 UnifiedMessage，channel_type=ChannelType.WECOM_PERSONAL_RPA。
    message_id 取 envelope.event_id；user_id 取 payload.sender_stable_id or sender_display_name。
    """

def parse_status_event(raw: dict) -> RpaStatusPayload:
    """event_type=status 分支。"""

def parse_action_result(raw: dict) -> RpaActionResultPayload:
    """event_type=action_result 分支。"""
```

### B.4 `src/channels/wecom_personal_rpa/action_client.py`

```python
async def deliver_actions(
    tenant_id: str,
    account_id: str,
    conversation_id: str,
    request_id: str,
    session_id: str,
    actions: list,
) -> None:
    """在线客户端：通过 connection.ClientConnectionRegistry 直接推送 ActionEnvelope。
    离线客户端：序列化 actions 后调用 db.enqueue_action 写入 outbox（dedup_key=
    'wecom_rpa:{tenant_id}:{request_id}'）。
    actions 为 RpaAction 列表（dict 或 pydantic 实例）。
    """
```

### B.5 `src/channels/wecom_personal_rpa/adapter.py`

```python
from src.channels.base import ChannelAdapter
from src.models.message import UnifiedMessage, UnifiedResponse

class WeComPersonalRpaAdapter(ChannelAdapter):
    def __init__(
        self,
        client_id: str,
        encrypted_secret: str | None = None,
        secret_resolver=None,   # Optional[Callable[[str], bytes | None]]
        get_secret=None,        # Optional[Callable[[str], bytes | None]] 供 verify_request
        tenant_id: str = "",
        account_id: str = "",
    ): ...

    @property
    def channel_type(self) -> str:
        return "wecom_personal_rpa"

    async def parse_message(self, raw_message: dict) -> UnifiedMessage: ...
    async def send_message(self, message: UnifiedResponse) -> bool: ...
    async def get_user_info(self, user_id: str) -> dict: ...
    async def verify_signature(self, signature, timestamp, nonce, body) -> bool: ...
```

### B.6 `src/channels/wecom_personal_rpa/connection.py`

```python
class ClientConnectionRegistry:
    """模块级单例，管理在线客户端 WebSocket 连接。"""

    def register(self, client_id: str, ws) -> None: ...
    def unregister(self, client_id: str) -> None: ...
    def is_online(self, client_id: str) -> bool: ...
    async def send(self, client_id: str, payload: dict) -> bool:
        """在线则发送并返回 True，离线返回 False（调用方据此写 outbox）。"""
    def list_online(self, client_ids: list[str]) -> list[str]: ...

# 模块级单例
client_connection_registry = ClientConnectionRegistry()
```

### B.7 `src/channels/wecom_personal_rpa/db.py`（已在阶段1实现）

CRUD 函数见 `src/channels/wecom_personal_rpa/db.py`，下游 management / action_client 直接 import：
`create_client / get_client / list_clients / update_client_status / update_last_seen / rotate_secret`、
`upsert_account / get_account / list_accounts / set_account_status / get_account_status`、
`get_or_create_binding / get_binding / list_bindings / set_binding_status / find_binding_by_search_key`、
`enqueue_action / claim_pending / mark_outbox_status / list_outbox`、
`write_audit / list_audit`。

---

## C. C# 客户端工程约定（5 个客户端实现 agent 必须逐字对齐）

### C.1 解决方案与工程

| 项 | 值 |
|----|----|
| 解决方案 | `clients/wecom-personal-rpa/WeComPersonalRpaClient.sln` |
| 工程1 | `Client.Core`（net8.0）— 状态机、队列、协议 DTO、限速、HMAC |
| 工程2 | `Client.Automation`（net8.0-windows）— FlaUI/UIA3、Win32、OpenCvSharp 自动化 |
| 工程3 | `Client.App`（net8.0-windows）— WPF/托盘 UI，交互式桌面运行 |
| 工程4 | `Client.Supervisor`（net8.0-windows）— Windows Service/计划任务监督进程 |
| 工程5 | `Client.Tests`（net8.0-windows）— xUnit 单元/集成测试（ProjectReference 引用 Client.Automation，须随之为 -windows） |

目录结构见设计文档 §2.3。

### C.2 命名空间

| 范围 | 命名空间 |
|------|----------|
| 根 | `WeCom.PersonalRpa` |
| Client.Core | `WeCom.PersonalRpa.Core` |
| Client.Automation | `WeCom.PersonalRpa.Automation` |
| Client.App | `WeCom.PersonalRpa.App` |
| Client.Supervisor | `WeCom.PersonalRpa.Supervisor` |
| Client.Tests | `WeCom.PersonalRpa.Tests` |
| 协议 DTO | `WeCom.PersonalRpa.Core.Protocol` |

### C.3 协议 DTO（`WeCom.PersonalRpa.Core.Protocol`，镜像 Python schemas）

| C# 类 | 对应 Python |
|-------|-------------|
| `InboundEvent` | `RpaCallbackEnvelope` |
| `MessagePayload` | `RpaMessagePayload` |
| `StatusPayload` | `RpaStatusPayload` |
| `ActionResultPayload` | `RpaActionResultPayload` |
| `Attachment` | `RpaAttachment` |
| `SendTextAction` / `SendImageAction` / `SendFileAction` / `NoopAction` / `HandoffAction` | 同名 Pydantic |
| `ActionEnvelope` | `ActionEnvelope` |
| `RpaConfigResponse` / `RateLimits` | `RpaConfigResponse` / `RpaRateLimits` |
| `RpaErrorResponse` | `RpaErrorResponse` |
| `RequestHeaders` | 请求头常量（`X-Client-Id` 等） |

序列化使用 `System.Text.Json`，命名策略 `camelCase`，与 Python `ensure_ascii=False` 的 JSON 字段名一一对齐。

### C.4 跨工程接口（Core 定义，Automation/App 实现）

| 接口 | 定义位置 | 实现位置 | 职责 |
|------|----------|----------|------|
| `IActionExecutor` | Core | Automation | 执行单个 SendAction（文本/图片/文件） |
| `IWeComAutomation` | Core | Automation | FlaUI/Win32/OpenCvSharp 三层定位与会话操作 |
| `IStateManager` | Core | App | 状态机驱动，唯一允许执行出站 action 的是 Running |
| `ClientSession` | Core | App | 单账号运行时会话（队列、限速、登录态） |
| `IAgentApiClient` | Core | Core | 发起 callback、拉取 config、拉取 outbox、上报 status/action_result |
| `IRateLimiter` | Core | Core | per_minute / per_day / consecutive_failure_pause 限速 |
| `IClipboardGuard` | Core | Automation | 输入独占：执行期间锁剪贴板/键盘/鼠标 |
| `IHealthSupervisor` | Core | Supervisor | 监督 App 存活、拉起、离线上报 |
| `INodesConfig` | Core | Core | 加载/热更新 `wecom_nodes.yaml`（窗口、控件、模板路径） |

### C.5 状态机枚举（`WeCom.PersonalRpa.Core`）

`ClientState`（`IStateManager` / `ClientSession` 使用）：

| 状态 | 含义 |
|------|------|
| `Starting` | 启动中 |
| `CheckingEnvironment` | 环境自检（分辨率/DPI/企微进程/窗口） |
| `NeedLogin` | 需扫码登录 |
| `Running` | **唯一允许执行出站 action 的状态** |
| `PausedByUser` | 用户手动暂停 |
| `PausedByServer` | 服务端下发暂停 |
| `PausedError` | 异常自动暂停（登录态/桌面/绑定/连续失败） |
| `Recovering` | 崩溃/断网恢复中 |

非 `Running` 状态一律禁止发送，仅允许上报 status 与健康。

### C.6 队列项状态枚举（`WeCom.PersonalRpa.Core`）

`SendAction` 队列项的 `QueueStatus`（本地 SQLite 队列与服务端 outbox 共用语义）：

| 状态 | 含义 |
|------|------|
| `Pending` | 待执行 |
| `Running` | 执行中 |
| `Succeeded` | 执行成功（终态） |
| `Retryable` | 可重试（等待退避后重试） |
| `Failed` | 执行失败（终态，转人工） |
| `Paused` | 已暂停（等待恢复） |

与服务端 `wecom_rpa_action_outbox.status` 的取值集合完全对齐。

### C.7 共享包与版本

| 包 | 版本 | 工程 |
|----|------|------|
| `FlaUI.Core` / `FlaUI.UIA3` / `FlaUI.UIA2` | 4.x | Automation |
| `OpenCvSharp4` + `OpenCvSharp4.runtime.win` | 4.x | Automation |
| `Serilog.Sinks.*` / `Serilog.*` | 3.x | Core/App/Supervisor |
| `Dapper` | 2.x | Core |
| `Microsoft.Data.Sqlite` | 8.x | Core |
| `Polly` | 8.x | Core |
| `Microsoft.Extensions.Http` / `DependencyInjection` / `Hosting` | 8.x | Core/App/Supervisor |
| `xUnit` + `xUnit.runner.visualstudio` | latest | Tests（仅 Tests） |

App/Supervisor/Automation 目标框架 `net8.0-windows`（需 Windows 桌面/Service 特性）；Core 为 `net8.0`（不依赖 Windows 特性）。Client.Tests 实际为 `net8.0-windows`（因其 ProjectReference 引用了 Client.Automation，必须随之为 -windows），与 Core 不同、无法在非 Windows CI 跑。

**实现精度注记（2026-06-22 实现后回填）**：
- §C.4 的自动化接口（`IWeComAutomation`/`IActionExecutor`/`IHealthSupervisor`/`IClipboardGuard`/`INodesConfig`）实际定义在 **Client.Automation** 的子命名空间 `WeCom.PersonalRpa.Automation.Contracts`（而非 Core），Client.App 通过 `using WeCom.PersonalRpa.Automation.Contracts;` 引用。语义与 §C.4 一致（Automation 定义/实现、App 消费），仅物理命名空间不同。
- Client.Tests 目标框架见上一段（net8.0-windows）。
