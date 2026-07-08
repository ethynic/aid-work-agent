# 企业微信个人账号 RPA — 服务端监听会话存档（Server-Archive-Listener）方案设计

> **关联文档**：[docs/ideas.md §渠道集成 #29](../ideas.md) · [客户端设计](wecom-personal-rpa-client-design.md) · [绑定管理 Tab 设计](wecom-personal-rpa-portal-binding-design.md) · [协议](wecom-personal-rpa-protocol.md)
>
> **关联开发计划**：[plans/plan-wecom-personal-rpa-server-archive-listener.md](../../plans/plan-wecom-personal-rpa-server-archive-listener.md)
>
> **核心定位**：本方案是 **现有 `wecom_personal_rpa` 渠道的功能扩展**，新增"服务端拉取模式"作为默认推荐，保留"客户端拉取模式"为可选 fallback。**不新增渠道类型**，因为两种模式本质都是「企业微信个人号绑定 agent 服务用户」，区别只是「在服务端拉存档 vs 在客户端拉存档」这一功能开关。
>
> **🎯 第一期 MVP 范围（本次上线）**：
> - **仅启用服务端模式（listen_mode='server'）**，作为默认且唯一可选模式
> - 客户端模式（listen_mode='client'）**前端禁用、灰显，标注「即将开放」**，用户不可切换
> - 服务端模式完整链路全部上线：回调接收 + 拉取 + RSA 解密 + 复用 `_process_inbound_message` + 兜底轮询
> - 客户端模式相关**后端代码保留**（双验签兼容路由、`wecom_rpa_clients.listen_mode` 字段、`secret_crypto` 加密 client_secret），为未来开放做准备，**但前端不暴露入口**
> - 现有客户端上报路径（已部署的 C# 客户端 + 现有 HMAC 路由）**继续工作不受影响**，因为没有租户切换到 server 之外的模式，且新租户只能选 server

---

## 1. 背景

### 1.1 现状

当前 wecom_personal_rpa 渠道的入站消息路径是**客户端轮询企微会话存档 → POST callback 上报服务端**：

```
ChatArchiveListener (C# 客户端)
  → ArchiveHttpClient.GetChatDataAsync  ← 拉密文
  → ArchiveCryptoService                ← RSA 私钥解密
  → NewMessageReceived 事件
  → InboundEventReporter
  → POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}
  → 服务端 _process_inbound_message → agent 主循环 → outbound 下发
```

### 1.2 问题

企业微信后台开通「会话内容存档」时会下发**一对凭证组合**：

- **拉取凭证**：corpid + 会话存档 secret + RSA 私钥（拉密文 + 解密）
- **回调凭证**：接收消息 URL + Token + EncodingAESKey（企微推送事件通知）

这两份凭证天然是**租户级**的（一个企业一份），但目前被装到了**客户端机器**上（`ArchiveOptions.PrivateKeyPath` + `Secret`）。

实际部署面临的问题：

1. **凭证配置不现实**：客户端被装到各个员工机器上，没有统一地址。每个机器都要拷贝私钥文件 + secret，运维成本不可接受。
2. **凭证泄露面扩大**：私钥拷到 N 台机器上，每一台都是泄露点。
3. **客户端不在线则丢消息**：客户端机器关机 / 重启 / 离线时，企微会话存档的 seq 会持续前进（默认 5 天内可补拉），客户端拉不到的消息会被丢掉。
4. **seq 持久化分散**：每个客户端各自维护一份 seq，账号切换机器时需手动迁移。
5. **没有回调 URL 配置**：企微「会话内容存档」需要配置回调 URL（接收事件通知），但目前系统没有暴露这个 URL 给租户填到企微后台。

### 1.3 诉求

把「接收企微回调事件 + 拉取密文 + 解密」这一职责**迁到服务端**，并**对齐微信客服（wecom_kf）渠道的开通/配置流程**：

- 租户在管理后台「开通 wecom_personal_rpa_archive 渠道」→ 系统自动生成回调 URL（含 config_id）
- 租户把回调 URL 复制到企微后台「会话存档 → 接收消息服务器」
- 租户在企微后台拿到 corpid/secret/RSA 私钥/Token/EncodingAESKey，录回系统
- 服务端按租户接收回调 + 拉取密文 + RSA 解密
- 解密后**复用现有 callback handler `_process_inbound_message`** 进入 agent 主循环
- agent 回复后**仍走现有 outbound 路径**，由当时在线的客户端执行「发送消息」动作
- 保留客户端轮询路径作为可选 fallback

### 1.4 企微官方机制

经查证企微官方文档（[获取会话内容](https://developer.work.weixin.qq.com/document/path/91774)、[产生会话回调事件](https://qiyeweixin.apifox.cn/doc-417835)），「会话内容存档」是**回调 + 拉取结合**：

| 模式 | 触发方 | 用途 | 限制 |
|------|--------|------|------|
| **回调（事件推送）** | 企微→服务端 URL | 实时通知"有新消息"，降低无效轮询 | 后台需配置 URL + Token + EncodingAESKey；仅通知事件，不含消息内容 |
| **拉取（SDK 调用）** | 服务端→企微 | 拉取具体密文（每次 ≤1000 条，5 天内有效） | 调用频率 ≤4000 次/分钟；服务器 IP 白名单；密文需 RSA 私钥解密 |

**正确的工作流**：

```
企微有新消息
   │
   ▼  ① 回调（事件推送）
服务端 URL 收到通知（POST，AES 加密）
   │  → Token 验签 + EncodingAESKey 解密
   │  → 立即 200 OK + 异步触发拉取
   │
   ▼  ② 拉取（SDK 主动调用）
服务端调 chat_check_in/list 拉取密文批次
   │  → 用 RSA 私钥解密 encrypt_random_key + encrypt_chat_msg
   │  → 得到明文消息
   │
   ▼  ③ 投递
构造 envelope → 复用 _process_inbound_message → agent → outbound
```

**兜底轮询**：60s 一次的定时拉取（无回调时补漏），防止回调 URL 短暂不可达时漏消息。

---

## 2. 目标与非目标

### 2.1 第一期 MVP 目标（本次上线）

1. **复用现有 `wecom_personal_rpa` 渠道类型**：不新增 channel_type，通过 `listen_mode` 字段标识模式。
2. **租户单例**：一个租户同一企业微信号只能有一份 `wecom_personal_rpa` 渠道配置。
3. **listen_mode 默认且仅 'server'**：新建配置强制为 server，前端 client 选项禁用、灰显、标注「即将开放」。
4. 服务端模式完整链路：服务端接收企微**回调事件**（URL + Token + EncodingAESKey）+ **拉取密文**（corpid + secret + RSA 私钥）+ RSA 解密 + 复用 `_process_inbound_message`。
5. 全套凭证（5 个字段）**加密存入** `tenant_channel_configs.config` JSON 列。
6. **回调触发拉取 + 60s 兜底轮询**双保险。
7. 白名单（`monitor_user_names` / `monitor_user_ids`）在 server 模式下生效。
8. **现有客户端上报路径不变**：已部署的 C# 客户端 + 现有 HMAC 路由继续工作。

### 2.2 客户端模式的处置（入站代码已全删）

**决策**：客户端入站消息路径（拉取 + 解密 + 上报）相关代码**已全部删除**，不再保留。

**理由**：

1. **企微会话存档回调要求公网域名**：企微后台配置「接收消息服务器」时，回调 URL 必须是公网可达的 HTTPS 端点。客户端机器（员工 PC）没有公网域名，无法接收企微回调，因此客户端拉取模式（`listen_mode='client'`）从架构上不可行。
2. **客户端没法监听回调**：即使绕过企微回调，客户端也无法监听企微服务端的推送（无固定公网入口）。
3. **拉取 + 解密全归服务端**：客户端只需要保留**出站路径**（接收服务端指令 → RPA 发送消息），入站完全由服务端负责。

**已删除范围**（详见开发记录）：

| 删除内容 | 状态 |
|---------|------|
| 客户端 C# `ChatArchiveListener` 类及其依赖（`ArchiveHttpClient` / `ArchiveCryptoService` / `ArchiveMediaDownloader` / `ArchiveSeqStore` / `WeComRateLimitException`） | ⛔ **已删** |
| 客户端 C# `InboundEventReporter` / `InboundEventBuilder` / `MonitorUsersCache` / `MonitorUsersHostedService` 类 | ⛔ **已删** |
| 客户端 C# `Services/InboundReporter`（孤儿门面，无消费者） | ⛔ **已删** |
| 客户端 C# `Protocol/IMessageWatcher`（入站消息监听器接口） | ⛔ **已删** |
| 客户端 `AgentApiClient.ReportInboundAsync` / `GetMonitorUsersAsync` 方法 | ⛔ **已删** |
| 客户端 `RpaConfigResponse.ListenMode` / `MonitorUsers` 字段及对应枚举/类型 | ⛔ **已删** |
| 入站相关测试（`MessageArchive/` / `Inbound/` 全部） | ⛔ **已删** |
| 服务端 Python 代码（`src/channels/wecom_personal_rpa/`） | ✅ **保留**（服务端负责拉取/解密） |
| 后端双验签兼容路由（HMAC 路径） | ✅ **保留**（status / action_result 等仍走客户端上报） |

> **保留说明**：客户端到服务端的通用 callback 信封 `InboundEvent` / `EventType` 枚举 / `PostCallbackAsync` 方法**保留**——它们被 status 状态上报（如离线检测）和 action_result 出站回执复用，与入站消息路径无关。`OfflineReporter` 同样保留（Supervisor 拉起失败时上报客户端离线状态）。

### 2.3 非目标

- 不修改客户端 RPA 自动化（PS 脚本）执行逻辑。
- 不修改 agent 主循环与 outbound 出站路径。
- 不变更现有客户端上报 callback 协议字段（`RpaCallbackEnvelope`）。
- 不在服务端实现客户端原有的 `ArchiveMediaDownloader`（媒体下载仍由客户端发起）。
- **不替换/废弃现有 `wecom_personal_rpa` 渠道**。
- **本期不开放客户端模式**：前端不可切换，租户只能用 server 模式。

---

## 3. 总体架构

### 3.1 单一渠道 + 模式开关

```
┌─────────────────────────────────────────────────────────────────────┐
│                          服务端（agent 后端）                        │
│                                                                      │
│  ┌──────────────────────────────────────────────┐                    │
│  │ 渠道：wecom_personal_rpa（与现有渠道同一类型）│                    │
│  │ ──────────────────────────────────────────── │                    │
│  │ tenant_channel_configs 表里一条配置           │                    │
│  │ channel_type='wecom_personal_rpa'             │                    │
│  │                                               │                    │
│  │ config JSON（加密）含两组凭证：               │                    │
│  │                                               │                    │
│  │  ◆ 通用凭证（两种模式都用）                   │                    │
│  │    corp_id                                    │                    │
│  │                                               │                    │
│  │  ◆ 服务端拉取凭证（仅 server 模式用）         │                    │
│  │    archive_secret（会话存档 secret）          │                    │
│  │    private_key（RSA 私钥）                    │                    │
│  │    token（回调验签）                          │                    │
│  │    encoding_aes_key（回调解密）               │                    │
│  │                                               │                    │
│  │  ◆ 客户端凭证（仅 client 模式用，已有）       │                    │
│  │    client_secret（HMAC 签名密钥）             │                    │
│  │                                               │                    │
│  │  ◆ 模式开关                                   │                    │
│  │    listen_mode: 'server' | 'client'           │                    │
│  │    （默认 server，可切换）                    │                    │
│  └──────────────────────────────────────────────┘                    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────┐           │
│  │ 统一回调 URL（双验签兼容）                            │           │
│  │ /t/{tenant_id}/wecom_personal_rpa/callback/{config_id} │          │
│  │                                                      │           │
│  │  路由函数先试企微官方签名（Token+AES）                │           │
│  │  失败再试客户端 HMAC（client_secret）                 │           │
│  │                                                      │           │
│  │  实际运行只走一种（由 listen_mode 决定），            │           │
│  │  双验签只为了路由设计简单（避免两个 URL）             │           │
│  └──────────────────────────────────────────────────────┘           │
│                                                                      │
│  ┌──────────────────────────────────────────────────────┐           │
│  │ 服务端拉取链路（仅 listen_mode='server' 时启用）      │           │
│  │                                                      │           │
│  │  收到企微回调（验签通过）                             │           │
│  │      │                                              │           │
│  │      ▼  立即 200 OK + 异步触发拉取                   │           │
│  │  ServerArchiveFetcher.fetch_once(tenant_id)         │           │
│  │   - chat_check_in/list 拉密文                        │           │
│  │   - RSA 解密                                         │           │
│  │   - 构造 envelope                                    │           │
│  │   - 复用 _process_inbound_message ─┐                │           │
│  │                                      │                │           │
│  │  兜底轮询（60s 一次，补漏）          │                │           │
│  └──────────────────────────────────────┼──────────────┘           │
│                                          ▼                           │
│  ┌────────────────────────────────────────────────────┐             │
│  │ _process_inbound_message（共用复用入口）            │             │
│  │ → parse → 白名单 → agent → adapter.send_message    │             │
│  └────────────────────────────────────────────────────┘             │
│                              │                                       │
│                              ▼                                       │
│            client_connection_registry WS + outbox                    │
│                              │                                       │
└──────────────────────────────┼───────────────────────────────────────┘
                               │ WS / HTTP polling
                               ▼
                  ┌──────────────────────┐
                  │  客户端（员工机器上  │
                  │  的 C# 程序）         │
                  │  - 仅执行发送消息动作 │
                  │  - 不再做本地拉取     │
                  │    （入站代码已删）   │
                  └──────────────────────┘
```

### 3.2 默认路径（server 模式）

**开通/配置流程**（对齐 wecom_kf）：

1. 租户管理员在「渠道配置」页面点「添加渠道」→ 选「企业微信个人号 RPA」。
2. 系统在 `tenant_channel_configs` 表插入一条记录，`channel_type='wecom_personal_rpa'`，`listen_mode='server'`（默认），生成 `config_id`。
3. 前端展示回调 URL：`https://your-domain/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`。
4. 租户复制 URL 到企微后台「会话存档 → 接收消息服务器」。
5. 租户在企微后台拿到 corp_id + 会话存档 secret + RSA 私钥 + Token + EncodingAESKey，回到本系统录入。
6. 系统加密保存到 `config` JSON 列，标记 `verified=true`。
7. 企微后台点保存时发 GET echostr 验证 → 本系统回调路由企微签名验签通过 → 返回明文 echostr。

**消息流程**：

8. 企微有新消息 → POST 到 `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`。
9. 路由函数**双验签兼容**：先试企微官方签名（Token + EncodingAESKey），通过则走 server 路径。
10. **Token 验签 + EncodingAESKey AES 解密**：得到事件明文（企微会话存档回调事件明文为 XML，由 callback_handler 解析）。
11. 立即 200 OK 响应企微 + 异步触发拉取。
12. `ServerArchiveFetcher.fetch_once(tenant_id)`：
    - Redis 分布式锁 `wecom_rpa:archive:lock:{tenant_id}`（防多 worker 并发）
    - 调 `chat_check_in/list` 拉一批密文
    - RSA 私钥解密 `encrypt_random_key` → random_key → 解密 `encrypt_chat_msg` → 明文
    - 构造与客户端上报**完全相同**的 `RpaCallbackEnvelope`
    - 调用 `_process_inbound_message(tenant_id, config_id, envelope, source="server_fetcher")`
    - 逐条推进 seq

**兜底轮询**：60s 一次扫描所有 `channel_type='wecom_personal_rpa' AND listen_mode='server' AND verified=true` 的配置，调 `fetch_once`。

### 3.3 客户端拉取路径（已删除）

> ⛔ **本期状态**：客户端入站拉取路径已全部删除。详见 §2.2。

**历史设计**（仅供追溯，不再实现）：客户端 `ChatArchiveListener` 拉密文 → RSA 解密 → POST 上报。

**删除原因**：企微会话存档回调要求公网域名，客户端机器没有公网域名收不到回调；客户端没法监听企微服务端推送；拉取 + 解密全归服务端做更合理。

**消息流程**（保留设计）：

1. 客户端 `ChatArchiveListener`（C#）拉密文 → RSA 解密 → POST 到 `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`。
2. 路由函数双验签兼容：企微签名验签失败 → 试客户端 HMAC（`client_secret`）→ 通过。
3. 走原有客户端上报路径 → `_process_inbound_message`。

### 3.4 关键不变量

- **同一渠道，同一 URL**：服务端拉取和客户端上报共用 `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`，路由层自动识别签名类型。
- **入站处理路径完全等价**：两条入站路径都汇聚到 `_process_inbound_message`，差异仅在"谁拉密文谁解密"。
- **outbound 出站路径完全一致**：agent 回复始终通过 WS + outbox 走客户端执行。
- **租户隔离**：拉取、解密、seq 持久化、白名单过滤，**全部以 tenant_id 为隔离维度**。
- **租户单例**：一个 tenant 同时只能有一份 `wecom_personal_rpa` 配置，模式切换通过编辑而非新建。

---

## 4. 数据模型（复用 `tenant_channel_configs`）

### 4.1 复用现有表 + 复用现有 channel_type

**不新建独立表，不新增 channel_type**。直接复用 `tenant_channel_configs`（`deploy/init-postgres.sql:593-603`）和现有的 `channel_type='wecom_personal_rpa'`：

```sql
CREATE TABLE IF NOT EXISTS tenant_channel_configs (
    id SERIAL PRIMARY KEY,
    config_id TEXT UNIQUE NOT NULL,         -- 如 chan_abc123，回调路由用
    tenant_id TEXT NOT NULL,
    channel_type TEXT NOT NULL,             -- 复用 'wecom_personal_rpa'
    config TEXT NOT NULL,                   -- JSON：含两组加密凭证 + 模式开关
    verified INT NOT NULL DEFAULT 0,        -- 凭证是否通过验证
    subagent_type TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**租户单例约束**（应用层强制）：`channel_config_db.py` 的 `create` 函数对 `channel_type='wecom_personal_rpa'` 类型加唯一性检查——同 tenant 已有该类型配置时拒绝创建，提示「请编辑现有配置切换模式」。可通过 PostgreSQL 部分唯一索引兜底：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_channel_configs_wecom_personal_rpa
    ON tenant_channel_configs(tenant_id, channel_type)
    WHERE channel_type = 'wecom_personal_rpa';
```

### 4.2 `config` JSON 结构（凭证加密存储）

```json
{
  "corp_id": "ww1234567890abcdef",
  "listen_mode": "server",
  "encrypted_archive_secret": "gAAAAABm...==",
  "encrypted_private_key": "gAAAAABm...==",
  "encrypted_token": "gAAAAABm...==",
  "encrypted_encoding_aes_key": "gAAAAABm...==",
  "encrypted_client_secret": "gAAAAABm...==",
  "poll_interval_seconds": 60,
  "batch_limit": 1000,
  "last_seq": 12345678,
  "last_callback_at": "2026-07-02T14:23:11",
  "last_fetch_at": "2026-07-02T14:23:12",
  "last_error_at": null,
  "last_error_msg": null
}
```

**字段说明**：

| 字段 | 用途 | 加密 | 模式 |
|------|------|------|------|
| `corp_id` | 企业微信 corp id（两种模式都用） | ❌ 明文 | 通用 |
| `listen_mode` | `'server'`（默认）/ `'client'` | ❌ 明文 | 通用 |
| `encrypted_archive_secret` | 会话存档 secret（拉 API 用） | ✅ Fernet | server |
| `encrypted_private_key` | RSA 私钥 PEM（解密 encrypt_chat_msg 用） | ✅ Fernet | server |
| `encrypted_token` | 回调验签 Token | ✅ Fernet | server |
| `encrypted_encoding_aes_key` | 回调 AES 解密密钥 | ✅ Fernet | server |
| `encrypted_client_secret` | 客户端 HMAC 签名密钥（已有，原 `wecom_rpa_clients.encrypted_secret` 迁移过来） | ✅ Fernet | client |
| `poll_interval_seconds` | 兜底轮询间隔（默认 60） | ❌ 明文 | server |
| `batch_limit` | 拉取批次大小（默认 1000） | ❌ 明文 | server |
| `last_seq` | 租户级 seq 断点 | ❌ 明文 | server |
| `last_callback_at` / `last_fetch_at` | 最近时间（前端展示） | ❌ 明文 | server |
| `last_error_*` | 错误信息（脱敏后存） | ❌ 明文 | server |

**加密方案**：复用 `src/channels/wecom_personal_rpa/secret_crypto.py` 的 Fernet 对称加密（与 client_secret 同源），主密钥来自环境变量 `RPA_SECRET_KEY`。

> ⚠️ **比 wecom_kf 现状更安全**：wecom_kf 的 config 列是明文 JSON，没有加密。本方案多一个 RSA 私钥，明文存储风险更高，所以强制加密所有敏感字段。

### 4.3 客户端表 `wecom_rpa_clients` 字段扩展

```sql
ALTER TABLE wecom_rpa_clients
    ADD COLUMN IF NOT EXISTS listen_mode TEXT;  -- NULL | 'client'
```

- NULL：客户端走租户默认（即 server 模式，客户端**不拉存档**）。
- `'client'`：客户端强制启用本地轮询（即 client 模式）。

该字段由服务端在客户端拉 `/config` 时根据 `tenant_channel_configs.config.listen_mode` 同步给客户端。客户端读到此字段后决定是否启动本地 `ChatArchiveListener`。

### 4.4 与现有 `wecom_rpa_clients` 表的关系

**关系不变**：

- `tenant_channel_configs`：存租户级渠道配置（含 5 个凭证 + listen_mode 开关）
- `wecom_rpa_clients`：存客户端实例注册（client_id + client_secret + 在线状态 + listen_mode 镜像）
- `wecom_rpa_conversation_bindings`：存监控白名单（`monitor_user_names` / `monitor_user_ids`）

`wecom_rpa_clients.encrypted_secret` **保留不变**，作为客户端鉴权的 fallback。**但 listen_mode='server' 时，客户端不再用这个 secret 上报消息**（因为没有本地拉取），该 secret 仅用于客户端登录鉴权。

### 4.5 与 wecom_kf 的差异对照

| 维度 | wecom_kf | 本方案 |
|------|---------|--------|
| 配置表 | `tenant_channel_configs` | ✅ 相同 |
| `channel_type` | `'wecom_kf'` | `'wecom_personal_rpa'`（复用现有） |
| `config` 字段 | corp_id + secret + token + encoding_aes_key + kf_account（明文） | corp_id + listen_mode + 4 个加密 server 字段 + 1 个加密 client 字段 |
| 回调路由风格 | `/t/{tenant_id}/wecom_kf/callback/{config_id}` | `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`（**复用现有路由**） |
| 回调签名机制 | 企微官方签名（单一） | **双验签兼容**：企微官方签名 + 客户端 HMAC |
| 回调后处理 | 直接调 `sync_msg` 拉消息 | server 模式：调 `chat_check_in/list` 拉密文 + RSA 解密；client 模式：客户端已解密，直接处理 |
| 白名单 | 不在渠道层 | 复用现有 `wecom_rpa_conversation_bindings.monitor_user_*` |

---

## 5. 服务端实现

### 5.1 新增模块：`src/channels/wecom_personal_rpa/archive/`

```
src/channels/wecom_personal_rpa/archive/
├── __init__.py
├── callback_crypto.py    # 回调验签 + AES-CBC 解密（对齐 wecom_kf adapter.crypto）
├── chat_crypto.py        # 拉取路径的 RSA 解密（移植 C# ArchiveCryptoService）
├── http_client.py        # 企微会话存档 HTTP 客户端（access_token + GetChatData）
├── fetcher.py            # ServerArchiveFetcher（拉取 + RSA 解密 + 复用入口）
├── poller.py             # 兜底轮询调度器（60s 一次）
└── callback_handler.py   # 接收企微回调的 FastAPI 路由处理函数
```

### 5.2 回调接收路径（复用现有路由 + 双验签兼容）

#### 5.2.1 路由复用

**不新增路由**。复用现有客户端上报路由：

```
GET  /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}    # URL 验证（echostr，server 模式专用）
POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}    # 事件接收（双模式共用）
```

现有路由处理函数 `wecom_personal_rpa_callback`（`wecom_personal_rpa_routes.py:249`）需要扩展为**双验签兼容**：

1. **先试企微官方签名**（Token + EncodingAESKey）：通过 → server 模式路径
2. **失败再试客户端 HMAC**（`client_secret`，现有逻辑）：通过 → client 模式路径
3. **都失败** → 401

**实际运行只走一种**（由 `listen_mode` 决定），双验签只是为了**共用同一个 URL**，避免维护两个路由带来的歧义。

#### 5.2.2 验签分发逻辑

```python
async def wecom_personal_rpa_callback(
    tenant_id: str, config_id: str, request: Request, ...
):
    cfg = await channel_config_db.get_by_tenant_and_id(tenant_id, config_id)
    if cfg is None or cfg.channel_type != "wecom_personal_rpa":
        raise HTTPException(404)

    creds = decrypt_credentials(cfg.config)
    config_data = json.loads(cfg.config)

    # ① GET echostr 验证（仅 server 模式企微首次配置时触发）
    if request.method == "GET" and config_data.get("listen_mode") == "server":
        return await _handle_archive_echostr(creds, request, ...)

    # ② POST 双验签兼容
    if request.method == "POST":
        # 先试企微官方签名
        if config_data.get("listen_mode") == "server":
            try:
                event = callback_crypto.verify_and_decrypt_event(
                    token=creds.token, encoding_aes_key=creds.encoding_aes_key, ...
                )
                # 立即 200 + 异步触发拉取
                asyncio.create_task(fetcher.fetch_once(tenant_id, config_id))
                return JSONResponse({"code": 0, "msg": "ok"})
            except SignatureError:
                pass  # 落到客户端 HMAC 验签

        # 试客户端 HMAC（现有逻辑）
        if _verify_client_hmac(creds.client_secret, request, ...):
            return await _handle_client_callback(...)

    raise HTTPException(401, "签名验证失败")
```

**关键约束**：

- **必须 5s 内响应**：企微回调超时会重试 3 次。server 模式收到回调后**立即触发拉取任务**（`asyncio.create_task`），主流程立刻 200。
- **幂等**：企微可能重试回调，但 fetcher 内部按 seq 去重，重复触发安全。
- **GET 验证 URL**：仅 server 模式企微首次配置时触发，client 模式不需要。

#### 5.2.3 `callback_crypto.py`

实现企微「企业微信回调加解密」官方算法（与 wecom_kf `adapter.crypto` 同源）：

- `verify_signature(token, timestamp, nonce, encrypt, msg_signature) -> bool`
  - 算法：`SHA1(sort([token, timestamp, nonce, encrypt]))` 比较 msg_signature
  - **必须用 `hmac.compare_digest`**（避免时序攻击）
- `decrypt_aes(encoding_aes_key, encrypt_b64) -> tuple[bytes, str]`
  - AES-CBC-256 解密（key = base64decode(encoding_aes_key + "=")）
  - 返回 (明文 bytes, receiveid)
- `verify_and_decode_echostr(token, encoding_aes_key, msg_signature, timestamp, nonce, echostr) -> str`
- `verify_and_decrypt_event(token, encoding_aes_key, msg_signature, timestamp, nonce, encrypt_field) -> str`（返回事件明文，**不绑定 JSON/XML 格式**；会话存档回调明文为 XML，由 callback_handler 自行解析）

> 💡 **可直接参考 `src/channels/wecom_kf/adapter.py` 的 crypto 模块**，几乎 1:1 复用（企微加解密算法所有渠道通用）。

### 5.3 拉取执行路径（`fetcher.py`）

#### 5.3.1 `ServerArchiveFetcher.fetch_once(tenant_id, config_id)`

```python
class ServerArchiveFetcher:
    async def fetch_once(self, tenant_id: str, config_id: str):
        """
        拉取一次该租户的所有新消息。
        被两处调用：
          - callback_handler 收到回调后异步触发（主路径）
          - poller 60s 兜底轮询（保险路径）
        """
        async with redis_lock(f"wecom_rpa:archive:lock:{tenant_id}", ttl=60):
            cfg = await channel_config_db.get_by_tenant_and_id(tenant_id, config_id)
            if cfg is None or not cfg.verified:
                return

            creds = decrypt_archive_credentials(cfg.config)
            config_data = json.loads(cfg.config)

            token = await http_client.get_access_token(
                tenant_id=tenant_id,
                corpid=creds.corp_id,
                secret=creds.secret,
            )
            batch = await http_client.get_chat_data(
                access_token=token,
                seq=config_data["last_seq"],
                limit=config_data["batch_limit"],
            )

            for item in batch.items:
                try:
                    random_key = chat_crypto.decrypt_random_key(
                        creds.private_key, item.encrypt_random_key
                    )
                    plain = chat_crypto.decrypt_chat_msg(
                        random_key, item.encrypt_chat_msg
                    )
                    envelope = self._build_envelope(cfg, item, plain)
                    await self._process_inbound_message(
                        tenant_id, config_id, envelope,
                        source="server_fetcher",
                    )
                    if item.seq > config_data["last_seq"]:
                        await channel_config_db.update_config_field(
                            tenant_id, config_id, "last_seq", item.seq
                        )
                except Exception as ex:
                    logger.warning(f"archive 解密失败 seq={item.seq}: {ex}")
                    break
```

#### 5.3.2 `chat_crypto.py`（拉取路径的 RSA 解密）

移植 C# `ArchiveCryptoService`（与 C# 客户端解密算法完全一致，确保跨语言互通）：

- `decrypt_random_key(private_key_pem: str, encrypt_random_key_b64: str) -> bytes`
  - **RSA-OAEP-SHA1** 解密 `encrypt_random_key`（企微官方规范，与 Java 默认一致）
  - 返回 random_key 字节（典型 32 字节）
- `decrypt_chat_msg(random_key: bytes, encrypt_chat_msg_b64: str) -> str`
  - **AES-256-CBC + PKCS7**（不是 GCM，与 C# 客户端一致）
  - key = random_key 前 32 字节
  - IV = base64 解码后的 encrypt_chat_msg 前 16 字节
  - 密文 = encrypt_chat_msg 剩余字节

#### 5.3.3 `http_client.py`

封装企微会话存档 HTTP API（与 C# `ArchiveHttpClient` 端点一致）：

- `get_access_token(tenant_id, corpid, secret) -> str`
  - GET `https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=X&corpsecret=Y`
  - Redis 缓存：key=`wecom_rpa:archive:token:{tenant_id}:{corpid}`，TTL = `expires_in - 300`（提前 5 分钟刷新）
- `get_chat_data(access_token, seq, limit) -> ChatDataBatch`
  - POST `https://qyapi.weixin.qq.com/cgi-bin/msg/get_chat_data?access_token=...`
  - body: `{"seq": ..., "limit": ..., "proxy": "", "last_snap_shot": 0}`
  - 处理 45009 → 抛 `WeComRateLimitException(retry_after_seconds=60)`

### 5.4 兜底轮询（`poller.py`）

```python
class ServerArchivePoller:
    """
    兜底轮询调度器。主路径是回调触发拉取，这里只负责 60s 一次的保险扫描。

    场景：
      - 回调 URL 短暂不可达（DNS、网络、服务重启期间）
      - 回调丢失
      - 服务端长期停机后启动，需要补拉 5 天内的存档
    """

    async def start(self):
        async for tick in periodic_timer(60):
            configs = await channel_config_db.list_by_channel_type(
                channel_type="wecom_personal_rpa_archive",
                verified_only=True,
            )
            for cfg in configs:
                asyncio.create_task(
                    fetcher.fetch_once(cfg.tenant_id, cfg.config_id)
                )
```

**关键约束**：

- 每个租户的 `fetch_once` 由 Redis 锁兜底，即使被多次触发也不会并发拉取。
- 启动时立即扫一次（补偿服务停机期间的消息）。

### 5.5 复用入口：`_process_inbound_message`

**保持现状**：`src/saas/api/wecom_personal_rpa_routes.py:351` 的 `_process_inbound_message` 函数签名扩展为：

```python
async def _process_inbound_message(
    tenant_id: str,
    config_id: str,
    envelope: RpaCallbackEnvelope,
    source: str = "client_callback",  # 新增：仅用于日志/审计区分
):
    ...
```

server fetcher 调用：

```python
await _process_inbound_message(
    tenant_id=tenant_id,
    config_id=config_id,
    envelope=envelope,
    source="server_fetcher",
)
```

**为什么 `_process_inbound_message` 是合适的复用点**：

- 它已经做了：消息去重、白名单过滤（`is_allowed_by_monitor_whitelist`）、用户注册（`ensure_user_registered`）、session 创建（`channel_session_manager.get_or_create_session`）、agent 调用、outbound 出站。
- 三条入站路径（渠道 A 回调拉取 / 渠道 A 兜底轮询 / 渠道 B 客户端上报）的行为**天然一致**。

### 5.6 Envelope 构造

server fetcher 拉到明文后，按 C# `InboundEventBuilder.BuildAsync` 的等价逻辑构造 envelope（**字段对齐，不引入新字段**）：

```python
envelope = RpaCallbackEnvelope(
    event_id=f"archive-{msg.msg_id}",
    client_id="_server_",              # server 模式占位（出站靠 account_id 路由）
    account_id=...,
    event_type="message",
    occurred_at=datetime.fromtimestamp(msg.msg_time, tz=utc),
    payload={...},  # 同客户端模式
)
```

**client_id 占位策略**：租户可能有多个客户端（员工机器），server fetcher 拉到消息时不知道哪个客户端在线。`client_id="_server_"` 是占位，出站时通过 `account_id` 在 `client_connection_registry` 找在线客户端。`deliver_actions` 已支持按 `account_id` 路由，无需额外改动。

### 5.7 启动与生命周期

在 `src/main.py` 应用启动时：

```python
archive_poller = ServerArchivePoller()
await archive_poller.start()
```

回调路径是 FastAPI 路由，随应用启动自动生效。

---

## 6. 管理后台（复用 `ChannelConfig.vue`）

### 6.1 复用现有渠道配置页面

**不新建专用前端组件，不新建专用 API**。直接在现有 `frontend/src/components/saas/ChannelConfig.vue` 中新增 `wecom_personal_rpa_archive` 渠道分支。

页面结构（已在 `https://agent2.aidingyi.cn/t/{tenant_id}/channels` 上线）：

- **列表区**：所有渠道配置以卡片列表展示（每张卡片显示 `config_id` / 渠道类型徽章 / 已验证状态 / 回调地址 + 复制按钮 / 行内操作按钮「配置指南/验证连接/编辑/删除」）
- **编辑弹窗**（BaseModal size=xl，支持全屏）：点击「添加渠道」或「编辑」打开，按 `channel_type` 动态渲染字段表
- 字段定义集中在 `channelFieldMap`（`ChannelConfig.vue:388`），按 `channel_type` 字典查找
- 配置指南集中在 `quickGuideMap`（`:420`）和 `fullGuideMap`（`:472`），同样按 `channel_type` 字典查找

### 6.2 wecom_personal_rpa 字段表（含模式开关，client 第一期禁用）

现有 `channelTypes`（`:374`）和 `channelTypeLabel`（`:382`）**已经包含** `wecom_personal_rpa`（label「企业微信个人号 RPA」），不需要新增渠道类型。

需要在 `channelFieldMap`（`:388`）**改造**现有的 `wecom_personal_rpa` 字段表：

```ts
// ChannelConfig.vue :388 改造 wecom_personal_rpa 字段表
wecom_personal_rpa: [
  // ─── 通用字段 ───
  { key: 'corp_id', label: '企业 ID (CorpID)', placeholder: 'ww...', location: '「我的企业」→「企业信息」' },

  // ─── 模式开关（特殊渲染，单选；第一期 client 禁用） ───
  {
    key: '__listen_mode__',
    label: '会话存档拉取模式',
    type: 'radio',
    options: [
      { value: 'server', label: '服务端拉取：凭证存服务端，企微推送回调（推荐）' },
      { value: 'client', label: '客户端拉取：凭证存客户端机器，客户端上报（即将开放）', disabled: true },
    ],
    default: 'server',
    // 第一期强制 server：即便用户绕过前端，后端创建时也会强制覆盖为 server
    forceValue: 'server',  // MVP 阶段锁定
  },

  // ─── server 模式专用字段（默认显示，因为 client 不可选） ───
  { key: 'archive_secret', label: '会话存档 Secret', placeholder: '', hint: '会话存档专用 Secret', location: '「管理后台」→「会话内容存档」→「API 基本信息」', showWhen: { listen_mode: 'server' } },
  { key: 'private_key', label: 'RSA 私钥', placeholder: '', hint: '上传 .pem 文件', type: 'file', location: '「会话内容存档」→「生成密钥对」', showWhen: { listen_mode: 'server' } },
  { key: 'token', label: '回调 Token', placeholder: '', hint: '企微后台生成', location: '「会话内容存档」→「接收消息服务器」', showWhen: { listen_mode: 'server' } },
  { key: 'encoding_aes_key', label: 'EncodingAESKey', placeholder: '43 字符', hint: '43 字符 Base64', location: '「会话内容存档」→「接收消息服务器」', showWhen: { listen_mode: 'server' } },

  // ─── client 模式专用字段（第一期永不渲染） ───
  // 代码保留，未来开放时取消 forceValue 即可启用
  { key: 'client_secret', label: '客户端 HMAC 密钥', placeholder: '', hint: '客户端上报签名用', showWhen: { listen_mode: 'client' } },
],
```

**第一期前端约束**：

- 模式开关 radio 中 `client` 选项**禁用、灰显**，label 后追加「（即将开放）」
- radio 默认且锁定 `server`，用户无法切换
- 实际渲染时 `client_secret` 字段永远不会出现
- 视觉上可加一个「Badge：第一期仅支持服务端模式」提示横幅

**后端强制保险**（防绕过）：

- `channel_config_db.create` / `update` 函数对 `wecom_personal_rpa` 类型强制 `listen_mode='server'`，忽略请求中的其他值
- 这样即便有用户用 API 工具直接发 PATCH 请求改 `listen_mode='client'`，后端也会强制覆盖回 `'server'`

**前端字段渲染逻辑改造**（与 v3 相同）：

- 支持 `showWhen` 条件渲染
- 支持 `disabled` 选项
- 支持 `forceValue` 锁定
- radio / file 类型特殊渲染

### 6.3 配置指南（按 listen_mode 区分）

`quickGuideMap.wecom_personal_rpa`（`:420`）和 `fullGuideMap.wecom_personal_rpa`（`:472`）改造为按 listen_mode 区分的两套指南：

```ts
quickGuideMap.wecom_personal_rpa = {
  title: '企微个人号 RPA 接入步骤',
  // 根据 form.listen_mode 显示不同 steps
  getSteps: (listenMode: string) => listenMode === 'server' ? [
    '前往企业微信管理后台 →「管理后台」→「会话内容存档」→ 开通功能',
    '在「API 基本信息」记录 CorpID 和会话存档 Secret',
    '在「会话内容存档」→「生成密钥对」上传公钥，下载 RSA 私钥',
    '将下方回调地址填入「接收消息服务器」URL 栏，生成 Token 和 EncodingAESKey',
    '先在此页面保存凭证（含 RSA 私钥），再到企业微信后台点击保存完成验证',
  ] : [
    '前往企业微信管理后台 →「管理后台」→「会话内容存档」→ 开通功能',
    '在客户端机器上配置 CorpID + 会话存档 Secret + RSA 私钥',
    '系统会自动给客户端下发 client_secret 用于上报签名',
  ],
  docUrl: 'https://developer.work.weixin.qq.com/document/path/91774',
}
```

### 6.4 列表区回调地址展示（已有，自动生效）

列表卡片回调地址逻辑（`ChannelConfig.vue:46-52`）已支持 `wecom_personal_rpa` 类型，回调 URL 格式 `/t/{tenant_id}/wecom_personal_rpa/callback/{config_id}`。

**两种模式共用同一 URL**（双验签兼容），不需要在列表区分开展示。租户管理员无论选哪种模式，看到的都是同一个 URL。

### 6.5 验证按钮（第一期仅 server 模式）

复用现有 `handleVerify` 逻辑，调 `POST /api/saas/channels/{config_id}/verify`。后端 `verify` 路由对 `wecom_personal_rpa` 类型：

**第一期**（仅 server 模式）：

- 拉一次最小批次（limit=1）
- RSA 解密一条
- 同时构造一个假事件自测验签 + AES 解密
- 返回详细错误诊断（corpid 错 / archive_secret 错 / 私钥错 / token 错 / encoding_aes_key 错）

**client 模式验证代码保留**（`verify_archive_client_mode` 函数已实现），第一期永远不会被调用。未来开放时直接生效。

### 6.6 凭证管理 API（完全复用现有）

**完全复用 `/api/saas/channels` CRUD**（`src/saas/api/channel_config.py`）：

| 方法 | 路径 | 用途 |
|------|------|------|
| `POST` | `/api/saas/channels` | 创建配置（应用层强制单例：同 tenant 已有 wecom_personal_rpa 配置则拒绝） |
| `GET` | `/api/saas/channels` | 列表 |
| `PATCH` | `/api/saas/channels/{config_id}` | 更新凭证 / 切换 listen_mode |
| `DELETE` | `/api/saas/channels/{config_id}` | 删除 |
| `POST` | `/api/saas/channels/{config_id}/verify` | 测试连通性（按 listen_mode 分支） |

**扩展点**：

1. `_REQUIRED_FIELDS.wecom_personal_rpa` 按当前 `listen_mode` 动态校验：
   - server 模式必填：`corp_id` + `archive_secret` + `private_key` + `token` + `encoding_aes_key`
   - client 模式必填：`corp_id` + `client_secret`
2. `channel_config_db.create` 函数对 `wecom_personal_rpa` 加单例检查（应用层 + DB 部分唯一索引兜底）。
3. `channel_config_db.create` / `update` 函数加密 hook：敏感字段先 Fernet 加密再 `json.dumps`。

### 6.7 回调接收路由（复用现有，无需新增）

```
GET  /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}
POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}
```

- **路由本身不变**：现有路由处理函数 `wecom_personal_rpa_callback` 改造为双验签兼容。
- **无 Cookie/Token 鉴权**：企微/客户端调用的 URL，无法携带登录态。
- **安全性**：靠签名验证（企微官方 / 客户端 HMAC）+ 内容解密双重保证。
- **限流**：单 config_id QPS 上限 100。
- 在 `src/main.py` 路由注册时，跳过 `TenantContextMiddleware` 鉴权（与现有处理一致）。

---

## 7. 白名单复用

**关键复用点**：服务端监听路径与客户端上报路径**共享同一份白名单**，不需要任何额外配置。

`is_allowed_by_monitor_whitelist` 已在 `_process_inbound_message` 中调用（`router.py:64-92`），server fetcher 复用此入口后自动生效。租户管理员在管理后台调整 `monitor_user_names` / `monitor_user_ids` 后：

- 服务端 fetcher 拉到的消息会立即走最新白名单（每次调用都读 DB）。
- 客户端 `MonitorUsersCache` 通过 `config_invalidate` 事件触发刷新（已有机制）。

不在白名单的消息**直接丢弃**（不入 channel_sessions、不调 agent）。

---

## 8. 密钥与安全

### 8.1 凭证加密存储

- `encrypted_secret` / `encrypted_private_key` / `encrypted_token` / `encrypted_encoding_aes_key` 用 `secret_crypto.py` 现有 Fernet 对称加密（与 client_secret 同源）。
- 加密主密钥来自环境变量 `RPA_SECRET_KEY`，不引入新密钥管理体系。
- 加密后的密文存入 `tenant_channel_configs.config` JSON 列。

### 8.2 日志脱敏

- secret / private_key / token / encoding_aes_key **绝不写日志**。
- poller / fetcher 错误日志中 corp_id 可显示，其他显示掩码。
- 已有 `sanitize_error_info` 工具，错误统一走它过滤。

### 8.3 私钥使用范围

- 私钥只在 `chat_crypto.py` 的解密函数内存中使用，不传出。
- `decrypt_archive_credentials(config_json)` 函数仅返回解密后的明文凭证对象，**仅 fetcher 内部传递**，不通过 API 返回。

### 8.4 比 wecom_kf 更严格的安全

| 维度 | wecom_kf | 本方案 |
|------|---------|--------|
| secret | 明文 JSON | ✅ Fernet 加密 |
| token | 明文 JSON | ✅ Fernet 加密 |
| encoding_aes_key | 明文 JSON | ✅ Fernet 加密 |
| 私钥 | 无 | ✅ Fernet 加密（新增） |

**理由**：会话存档多一个 RSA 私钥（能解密整个企业的会话内容），明文存储风险远高于 wecom_kf。

---

## 9. 监控与可观测性

复用现有 `wecom_rpa_audit_logs` 表：

| audit 事件 | 触发点 |
|-----------|--------|
| `archive_callback_received` | 收到企微回调（含 config_id、是否验签通过） |
| `archive_callback_verify_failed` | 验签/解密失败（疑似伪造或 Token 错误） |
| `archive_fetch_started` | 回调或轮询触发拉取 |
| `archive_fetch_success` | 单次拉取成功（含 seq、batch_size、来源 callback/poller） |
| `archive_fetch_rate_limited` | 45009 触发暂停 |
| `archive_fetch_error` | 拉取/解密异常 |
| `archive_credential_updated` | 凭证更新 |
| `archive_callback_url_regenerated` | 重新生成 config_id |

前端「渠道配置」审计列表自动展示这些事件（复用现有审计基础设施）。

指标暴露（`/api/saas/channels/{config_id}/metrics` 或复用 wecom_personal_rpa_admin metrics）：

- `wecom_rpa_archive_callback_total{tenant_id, verify_status}`
- `wecom_rpa_archive_fetch_total{tenant_id, status, source}`
- `wecom_rpa_archive_seq_lag_seconds{tenant_id}`
- `wecom_rpa_archive_decrypt_failures_total{tenant_id}`

---

## 10. 灰度与迁移

### 10.1 部署节奏

1. **第 1 周**：服务端实现 + 单元测试，所有租户 channel 配置页面可见 `wecom_personal_rpa_archive` 选项，但**没有租户开通**（即不实际拉取）。
2. **第 2 周**：选定 1 个租户作为灰度，开通 + 录入真实凭证，观察 1 周。
3. **第 3 周**：所有租户开放录入凭证。

### 10.2 已有客户端的影响

- 服务端升级后，**所有客户端首次拉 `/config` 时收到 `listen_mode="server"`**（默认），自动停止本地轮询。
- 租户管理员录入会话存档凭证（server 模式字段）后，server 路径接管。
- **第一期不支持切回 client 模式**：租户如需保留客户端拉取（如合规要求），暂时不上线本方案，等未来开放 client 模式开关。

### 10.3 回滚

- 删除该租户的 `tenant_channel_configs` 配置记录即可。
- 客户端代码不删除本地 `ChatArchiveListener`，保留至少 2 个版本。

### 10.4 服务停机补偿

- 服务端停机期间，企微回调会失败但企微**自身会重试 3 次**（间隔几秒）。
- 服务恢复后，60s 内兜底轮询会拉到所有积压消息（**企微会话存档保留 5 天**）。
- 不需要在停机期间人工干预。

---

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 回调 URL 被伪造攻击 | Token 验签 + EncodingAESKey 解密双重保证；不通过则 401 + audit `verify_failed` |
| 回调 URL 泄露 | 「重新生成 URL」按钮，旧 config_id 立即失效 |
| 服务端拉取被 45009 频率限制 | 暂停 60s + 指数退避 |
| 回调丢失（URL 短暂不可达） | 60s 兜底轮询；seq 持久化保证不丢消息 |
| RSA 私钥泄露 | Fernet 加密落库 + 日志全链路脱敏 + 私钥仅在内存流转 |
| Gunicorn 多 worker 重复拉取 | Redis 分布式锁 `wecom_rpa:archive:lock:{tenant_id}` |
| seq 长期未推进导致消息丢失 | 60s 兜底轮询 + 监控 seq_lag 指标 |
| 凭证配置错误一直拉不到 | 管理后台「测试连通性」按钮 + audit_log 可见 |
| 回调与拉取死锁（fetch_once 卡住） | Redis 锁 TTL 60s 自动释放 + 单次拉取超时 30s |
| 模式切换瞬间可能丢消息 | server↔client 切换时正在传输的消息可能丢；缓解：依赖 envelope `event_id` 去重 + 兜底轮询补拉 |
| 双验签失败风险（路由误判） | 严格按 listen_mode 决定走哪种验签，先企微签名后 HMAC；不通过则 401，audit 记录 |
| 客户端在线但 server 模式回复路由不到 | outbound 已有 account_id 路由 + outbox 兜底 |

---

## 12. 实现优先级（详见开发计划，第一期 MVP）

| 优先级 | 工作项 | 工时估算 |
|--------|--------|---------|
| P0 | listen_mode 字段定义 + 凭证加密/解密工具 + 单例约束 + 后端强制 server | 2h |
| P0 | 回调签名校验 + AES 解密（复刻 wecom_kf adapter.crypto） | 3h |
| P0 | 拉取 RSA 解密工具 + HTTP 客户端 | 4h |
| P0 | ServerArchiveFetcher + 复用 `_process_inbound_message` | 4h |
| P0 | 兜底轮询调度器 | 2h |
| P0 | 现有回调路由双验签兼容改造（client 分支代码保留） | 3h |
| P0 | 客户端 `/config` listen_mode 字段 + 客户端禁用本地轮询逻辑 | 2h |
| P1 | verify 路由 server 模式专属逻辑（client 模式函数保留不调用） | 1.5h |
| P1 | 前端 ChannelConfig.vue 改造（模式开关锁定 server + 条件字段） | 3h |
| P1 | 监控指标 + audit 事件接入 | 2h |
| P2 | 灰度 + 文档更新 | 2h |

**合计**：约 28.5h（约 3.5 工作日，比 v3 减少 5.5h，因为 client 模式不暴露测试场景减少）。

> 💡 **未来开放 client 模式时的增量工作**（不在本期）：
> - 移除前端 `forceValue: 'server'` 和 client 选项 `disabled: true`
> - 移除后端 `channel_config_db.create/update` 中强制 server 的逻辑
> - 启用 client 模式 verify 分支
> - 估算增量工时：~3h

---

## 13. 与现有文档的关系

- 本设计**不新增渠道**，是现有 `wecom_personal_rpa` 渠道的功能扩展（新增 server 模式作为默认推荐）。
- **第一期 client 模式前端不开放**，但后端代码 + 协议字段保留，为未来开放做准备。
- [客户端设计](wecom-personal-rpa-client-design.md) `ChatArchiveListener` 章节需补充说明：「第一期 `listen_mode` 永远为 `server`，客户端跳过本地轮询；未来开放 client 模式后，仅当 `listen_mode='client'` 时启用」。
- [协议](wecom-personal-rpa-protocol.md) 不变（envelope 字段不变，回调 URL 不变）。
- [绑定管理 Tab 设计](wecom-personal-rpa-portal-binding-design.md) 不冲突（绑定管理与渠道配置是两个维度）。

---

## 14. 后续可演进方向（不在本期）

- **开放客户端模式**：移除前端 `forceValue: 'server'` 和后端强制覆盖逻辑，让租户能自由切换 server/client。增量工时约 3h。
- **多企业支持**：放开「租户单例」约束，允许同一 tenant 配置多个 `wecom_personal_rpa`。
- **私钥热轮换**：企微后台换公钥 → 服务端平滑切换私钥。
- **去重跨模式**：server↔client 切换瞬间可能丢/重，依赖 envelope `event_id` 去重（已实现）。
- **统一所有渠道凭证加密**：把 wecom_kf / wecom / dingtalk / feishu 的 config 列也升级为加密存储。
