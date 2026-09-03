# 服务端拉取模式 真机验证指引

> **关联文档**：
> - 服务端拉取存档设计：[wecom-personal-rpa-server-archive-listener-design.md](./wecom-personal-rpa-server-archive-listener-design.md)
> - 开发计划：[docs/plans/plan-wecom-personal-rpa-server-archive-listener.md](../plans/plan-wecom-personal-rpa-server-archive-listener.md)
> - 协议：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)
> - 客户端设计：[wecom-personal-rpa-client-design.md](./wecom-personal-rpa-client-design.md)
>
> **创建日期**：2026-07-01（原始客户端模式版本）
> **重写日期**：2026-07-06（Phase 1-12 服务端拉取模式已上线）
> **适用版本**：master 分支 commit `4ac87cc` 之后（含服务端拉取模式 Phase 1-12）
> **状态**：📋 待手工验证

---

## 0. 验证目标一句话

**验证服务端拉取模式（listen_mode='server'）在真实企业微信 + 真实后端环境下，能完成「用户发消息 → 服务端拉取 → agent 回复 → 客户端执行发送 → 用户收到回复」的全链路**，并满足：

- 服务端拉取漏抓率 < 3%
- 误发次数 = 0
- 端到端延迟 < 30 秒
- 回调 + 兜底轮询双路径都验证到位
- 异常场景（锁屏 / 断网 / 客户端崩溃重启）全部正确处理

整个验证预计 **3-4 小时**（含 1 小时连续监听）。

---

## 1. 架构变化提醒（重要）

**第一期上线的是「服务端拉取模式」（Phase 1-12）**，与原客户端模式有本质区别：

| 维度 | 客户端模式（已废弃） | 服务端拉取模式（**当前**） |
|------|---------------------|--------------------------|
| 拉取主体 | 客户端 C# ChatArchiveListener | 服务端 archive.fetcher（Python） |
| 凭证存放 | 客户端 `client.yaml` + 本地 .pem | 服务端 `tenant_channel_configs.config` JSON（Fernet 加密） |
| 拉取触发 | 客户端 3s 轮询 | 企微回调（实时） + 服务端 60s 兜底轮询 |
| 客户端职责 | 拉取 + 解密 + 上报 | **仅执行出站 action（发送消息）+ 心跳** |
| 配置入口 | 客户端 yaml | 租户前台 `ChannelConfig.vue`（5 字段录入） |
| 验签机制 | 客户端 HMAC | 企微官方签名（Token + EncodingAESKey） |

**因此本指引不再涉及客户端拉取相关的 client.yaml 配置 / 客户端日志 / 客户端 seq 续传**。客户端侧的验证重点是**出站执行**（F2 搜索 + F3 发送）和**客户端在线状态**。

---

## 2. 验证前准备清单（30 分钟）

### 2.1 硬件 / 软件环境

| 项 | 要求 | 检查命令 |
|----|------|---------|
| 操作系统 | Windows 11（10 也可） | `winver` |
| .NET 8 SDK | 已安装 | `dotnet --version` 应输出 `8.x` |
| PowerShell | 5.1（系统自带，**不要用 pwsh 7**） | `powershell -Command $PSVersionTable.PSVersion` |
| 企业微信 PC 客户端 | 5.0.8 或更高 | 企微 → 设置 → 关于 |
| 屏幕分辨率 | 1920×1080 或更高 | 桌面右键 → 显示设置 |
| DPI 缩放 | 100% 或 150% | 同上 |
| 网络 | 客户端机器能访问后端；后端能访问 `qyapi.weixin.qq.com` | 客户端 `curl http://<后端>` / 后端 `ping qyapi.weixin.qq.com` |

### 2.2 测试账号

| 用途 | 账号 | 说明 |
|------|------|------|
| **客户端登录账号** | 你的企业微信账号 | 登录在被测 PC 上 |
| **测试发送方 1** | 陆伟 | 给客户端账号发消息 |
| **测试发送方 2** | 孙晨 | 同上 |
| **测试发送方 3** | 芮秀 | 同上 |

3 个测试发送方可以用手机企微或另一台 PC 企微登录。**测试发送方账号必须与企业微信会话存档范围匹配**（见 2.3）。

### 2.3 企业微信会话存档凭证（**最关键的准备工作**）

服务端拉取模式需要 **5 个凭证**（不是原来的 3 个，因为多了回调验签）：

| 凭证 | 用途 | 来源 |
|------|------|------|
| `corpid` | 企业 ID | 管理后台 → 我的企业 → 企业信息 |
| `archive_secret` | 会话存档 secret（拉 API 用） | 管理后台 → 管理工具 → 会话内容存档 → API 基本信息 |
| `private_key` | RSA 私钥 .pem（解密 encrypt_chat_msg） | 管理后台 → 会话内容存档 → 密钥管理 → 下载私钥 |
| `token` | 回调验签 Token | 管理后台 → 会话内容存档 → 接收消息服务器 → 自行设定或随机生成 |
| `encoding_aes_key` | 回调 AES 解密密钥（43 字符 Base64） | 同上 → 点击「随机获取」 |

**重要 1**：会话存档的「许可」要勾选**所有需要被监听的员工**（包括你的客户端登录账号 + 3 个测试发送方），否则拉不到消息。

**重要 2**：服务端拉取模式需要在管理后台「接收消息服务器」配置**回调 URL**——这个 URL 在第 4 步前端录入凭证后会自动展示，先空着，4 步完成后回填。

### 2.4 后端服务端

| 项 | 要求 |
|----|------|
| Python 环境 | 已安装项目依赖（`pip install -r requirements.txt`） |
| PostgreSQL | 已 `init-postgres.sql` 初始化 + 跑过 `db_update.sql` 增量（含 Phase 1 新增的 `wecom_rpa_clients.listen_mode` 字段 + 单例索引） |
| Redis | 运行中（用于 nonce 防重放 + access_token 缓存 + 分布式锁） |
| 环境变量 | `DATABASE_URL` / `REDIS_URL` / `LLM_PROVIDER` / 模型 API key 等已配置 |
| **新增环境变量** | `RPA_SECRET_KEY=<强随机串>`（archive 凭证 Fernet 加密主密钥，**必填**） |

**注意**：原客户端模式需要的 `WECOM_RPA_ARCHIVE_ENABLED=true` 在服务端拉取模式下**不再需要**（poller 自动启动，不依赖此环境变量）。

启动命令（在后端机器）：

```bash
cd c:/repos/aid-work-agent
python -m src.main
```

启动日志应出现：
```
[ServerArchivePoller] 启动兜底轮询 interval=60s
```

看到 `Uvicorn running on http://0.0.0.0:8000` + archive poller 启动日志即成功。

### 2.5 在后端注册客户端（**仍然必要**）

服务端拉取模式下，客户端不再拉取消息，但**仍需要身份来接收服务端推送的出站 action**（执行发送）。所以客户端注册流程不变。

**方式 A：用平台后台 UI**（推荐）

1. 浏览器访问 `http://<后端>/portal/rpa-bindings`
2. 点「+ 新增绑定」→ 选择目标租户 → 起名「服务端模式测试」→ 关联一个数字员工
3. 保存后弹出**密钥一次性展示对话框**，**立即复制保存** `client_id` 和 `client_secret`

**方式 B：直接 SQL**（应急）

```sql
-- 在 PostgreSQL 里调用 register_rpa_client 函数（如有）
-- 或直接 INSERT 到 wecom_rpa_clients（参考现有 SQL）
```

记下：
```
client_id     = client_xxxxxxxxxx
client_secret = abcdef0123456789...
```

### 2.6 配置白名单 binding（用于 F6 验证）

在租户前台或平台后台，给上一步创建的 binding 配置**只监控"陆伟"**：

1. 编辑 binding 详情
2. 「监控用户名」字段填：`陆伟`
3. 「监控用户 ID」留空
4. 保存

这样 F6 白名单过滤测试时，孙晨/芮秀的消息会被服务端二次校验过滤。

---

## 3. 在前端开通 wecom_personal_rpa 渠道（10 分钟）

这是**服务端拉取模式独有的步骤**，必须在客户端配置之前完成（因为客户端配置不涉及凭证，凭证全部在前端录入）。

### 3.1 打开渠道配置页面

浏览器访问：`http://<后端>/t/<tenant_id>/channels`

### 3.2 新增 wecom_personal_rpa 配置

1. 点「添加渠道」
2. 渠道类型选「**企微个人号RPA（会话存档）**」（含图标）
3. 看到 **listen_mode 单选**：
   - 「服务端拉取（推荐）」默认选中
   - 「客户端拉取（即将开放）」**灰色不可选**（第一期禁用）
   - 下方有 ⚠️ 警示横幅：「第一期仅支持服务端拉取模式...」
4. **复制回调 URL**（页面顶部展示，形如 `https://<后端>/t/<tenant_id>/wecom_personal_rpa/callback/<config_id>`）—— 留待 3.4 步填入企微后台

### 3.3 填入 5 个凭证

按页面字段填：

| 字段 | 值 | 说明 |
|------|----|----|
| 企业 ID (CorpID) | `ww8888888888888888` | 2.3 拿到的 corpid |
| 会话存档 Secret | `公7_GxFc...` | 2.3 拿到的 archive_secret |
| RSA 私钥 | 点击「选择文件」上传 `.pem` | 上传后显示「✓ 已上传」（不上传明文） |
| 回调 Token | `xxx` | 2.3 拿到的 token |
| EncodingAESKey | `abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG` | 2.3 拿到的 43 字符 aes_key |
| 关联数字员工 | 选 2.5 关联的数字员工 | 决定消息由哪个 agent 处理 |

### 3.4 在企微后台配置回调 URL

**注意顺序**：先在本系统保存，再去企微后台点保存（企微会发 GET echostr 验证 URL）。

1. 切到企业微信管理后台 → 管理工具 → 会话内容存档 → 接收消息服务器
2. URL 栏粘贴 3.2 复制的回调 URL
3. Token / EncodingAESKey 填和 3.3 一样的值（企微后台和本系统必须一致）
4. **先在本系统点「保存」**（让本系统的 callback 路由就位）
5. **再去企微后台点「保存」** → 企微会发 GET echostr → 本系统 callback_handler 验签 + AES 解密 + 返回明文 → 企微后台显示「保存成功」

如果企微后台点保存时报错「URL 验证失败」：
- 看后端日志是否有 `[ArchiveCallback] GET echostr 验签失败`
- 检查本系统填的 Token / EncodingAESKey 与企微后台是否完全一致
- 检查回调 URL 是否能从公网访问（本地开发用 ngrok / frp 内网穿透）

### 3.5 点「验证连接」做 5 步自测

回到本系统渠道配置页，点该配置行的「验证连接」按钮，触发后端 5 步链路自测：

| 步骤 | 检查内容 | 失败诊断 |
|------|---------|---------|
| 1. access_token | corpid + archive_secret 拉取企微 access_token | errcode=40013/40125 → 「Corp ID 或 Secret 错误」 |
| 2. chat_data | 用 access_token 拉一条密文 | errcode=60011 → 「应用未获得会话存档 SDK 权限」 |
| 3. RSA 解密 | 用 private_key 解密 encrypt_random_key | 「RSA 私钥错误或与 encrypt_chat_msg 不匹配」 |
| 4. AES 解密 | 用 random_key 解密 encrypt_chat_msg | （私钥错也会触发，与 3 同诊断） |
| 5. 回调自测 | 构造假事件自验签 + AES 解密 token / encoding_aes_key | 「Token 或 EncodingAESKey 错误」 |

**期望**：返回 `{"success": true, "verified": true, "stages_passed": [...]}`，配置列表中该行徽章从「未验证」变「已验证」。

**特殊场景**：新企业暂无消息时，步骤 3+4 会被跳过，message 含「私钥暂未测试：企业暂无会话存档消息」——这是正常的，不算失败。

---

## 4. 客户端配置文件准备（5 分钟）

服务端模式下客户端配置大幅简化，**不含任何凭证**。

### 4.1 复制示例配置

```bash
cd c:/repos/aid-work-agent/clients/wecom-personal-rpa
copy configs\client.example.yaml configs\client.yaml
```

### 4.2 编辑 `configs\client.yaml`

只填这几项：

```yaml
# 后端地址
agent_base_url: "http://127.0.0.1:8000"

# 客户端身份（2.5 拿到的）
client_id: "client_xxxxxxxxxx"
client_secret_ref: "dpapi:Client.WeComPersonalRpa:client_secret"  # 或直接写 client_secret
tenant_id: "tenant_xxx"

# 自动化节点配置（出站发送消息用）
nodes_config_path: "./assets/wecom_nodes.yaml"
```

**注意**：原客户端模式需要的 `message_source`（corpid / secret / private_key_path）、`qr_code`、`monitor_users.binding_id` 等段**不再需要**——这些信息全部由服务端管理。

### 4.3 构建客户端

```bash
cd c:/repos/aid-work-agent/clients/wecom-personal-rpa
dotnet build WeComPersonalRpaClient.sln -c Debug
```

**期望**：`0 个警告 0 个错误`。

### 4.4 客户端 client_secret 写入受保护存储（推荐）

按 client.example.yaml 注释，client_secret 不应明文写在 yaml。用 DPAPI 加密存储：

```powershell
# 参考客户端工具脚本（如有），或用 Windows Credential Manager
```

如果只是测试，可以直接在 yaml 写 `client_secret: "<明文>"` 临时跑通，**生产环境必须加密**。

---

## 5. 启动客户端（5 分钟）

### 5.1 启动企微

1. 双击桌面企业微信图标启动
2. 用客户端登录账号扫码登录
3. 等待主界面完全加载

**注意**：测试期间**不要锁屏**、**不要最小化企微**，让窗口保持可见。

### 5.2 启动客户端

打开 PowerShell（**普通权限即可**）：

```powershell
cd c:\repos\aid-work-agent\clients\wecom-personal-rpa\src\Client.App
dotnet run --no-build
```

### 5.3 检查启动日志

客户端启动后，控制台或日志文件会输出（路径 `%LOCALAPPDATA%\WeComPersonalRpa\Client.App\logs\client-YYYYMMDD.log`）：

**期望看到的关键日志行**（与原客户端模式对比，关键差异已标注）：

```
[INF] [Client.App] 进程启动
[INF] Microsoft.Hosting.Lifetime Application started
[INF] [Client.App] Generic Host 已启动
[INF] [TrayApp] 托盘已挂载
[INF] MonitorUsersCache 初始化完成
[INF] OutboundDispatcher 启动完成，恢复 0 个未完成 action
[INF] [ChatArchiveListener] DisablePolling=true，跳过本地轮询启动（服务端拉取模式）   ← 关键差异
[INF] ClientSession 连接成功，listen_mode=server                                       ← 关键差异
```

**关键差异**：第一期 listen_mode 永远 'server'，所以**不应**出现：
- ❌ `ChatArchiveListener 启动，corpid=ww888... poll=3s`（说明客户端仍在拉取，配置错了）
- ❌ `拉取超时 / 45009 频率限制`（同上）
- ❌ `RPA 解密私钥加载失败`（同上，客户端不再加载私钥）

应出现的是 `DisablePolling=true，跳过本地轮询启动（服务端拉取模式）`，证明 listen_mode 字段已正确下发。

**如果出现这些日志说明有问题**：

| 日志 | 含义 | 处理 |
|------|------|------|
| `MonitorUsersCache 初始化失败` | /config 接口不通或鉴权失败 | 检查后端是否启动、client_secret 是否对 |
| `db.get_client 失败` | 客户端没在后端注册 | 检查 client_id 是否对、是否启用 |
| `listen_mode=client` | 服务端配置错误（理论上不可能，codec 强制 server） | 检查 tenant_channel_configs.config |
| `qr_image_base64` 出现在日志 | （脱敏过滤器失效） | 立即停服，反馈 bug |

启动成功后，客户端会**静默**等待出站 action（系统托盘有图标），不要关闭 PowerShell 窗口。

---

## 6. 功能验证执行

### 6.1 F2 + F3：搜索 + 发送（10 分钟）

**前置**：客户端已连后端（看启动日志 `ClientSession 连接成功`）。

**简单方法**：直接通过 web 端 `http://<后端>` 找到这个数字员工对话，发一条 "回复文本：你好" 之类。

**验证步骤**：

1. 在 web 对话里发：`请帮我给陆伟发一条消息："你好，这是服务端模式测试"`（agent 应该用工具触发 send_text action）
2. 观察：
   - **服务端日志**应出现：`adapter.send_message → deliver_actions → client_connection_registry.send`（推送到客户端）
   - **客户端日志**应出现：`OutboundDispatcher 处理 send_text ActionId=... conv=陆伟`
   - **PS 进程**短暂启动（任务管理器看到 powershell.exe 闪一下）
   - **陆伟**（手机或另一台 PC）应收到消息："你好，这是服务端模式测试"
3. 重复 10 次（每次发不同内容），记录**成功/失败次数**

**验收**：
- 成功率 ≥ 90%（即 10 次至少 9 次成功）
- **误发 0 次**（陆伟之外的人没收到）

**测试发送图片**：

让 agent 回复一张图片（比如生成图表），观察客户端是否：
1. 客户端日志出现 `AttachmentDownloader 下载成功 size=xxKB`
2. PS 把图片粘贴到企微
3. 陆伟收到图片

**测试发送文件**：

类似上面，让 agent 发一个 .pdf 或 .docx，观察同上。

### 6.2 服务端拉取 + 白名单（60 分钟，最长）

**核心测试**——服务端拉取模式连续 1 小时监听消息，验证漏抓率。**这是与原客户端模式测试最大的不同**：测试主体是**服务端**，不是客户端。

#### 6.2.1 准备工作

1. 确认 3.5 验证连接通过（5 步链路全绿）
2. 确认客户端已启动并连后端（用于 agent 回复发送）
3. 准备好 3 个测试发送方（陆伟/孙晨/芮秀），各发送 20 条消息（共 60 条）
4. 每条消息内容可以简单：`测试1` `测试2` ... `测试20`

#### 6.2.2 开始监听

记录开始时间：
```
开始时间：YYYY-MM-DD HH:MM:SS
```

#### 6.2.3 发消息（按计划发完 60 条）

让 3 个发送方各发 20 条到**你的客户端登录账号**。可以群聊也可以单聊，**但要在会话存档许可范围内**。

#### 6.2.4 验证收到（服务端日志 + 数据库）

每发一批（比如 20 条），去**后端服务端**验证：

**A. 看服务端日志**（实时）：

```
[ServerArchiveFetcher] 拉取完成 tenant=tenant_xxx batch=N processed=M last_seq=...
```

如果回调路径正常，还应看到：
```
[ArchiveCallback] POST 事件接收成功 tenant=tenant_xxx config=chan_xxx
[ServerArchiveFetcher] 拉取完成 batch=N processed=N
```

**B. 查数据库**（确认）：

```sql
-- 看服务端拉取并入库的消息（source_type=wecom_personal_rpa）
SELECT
    sender_display_name,
    text,
    occurred_at,
    created_at
FROM channel_messages
WHERE session_id LIKE 'wecom_personal_rpa:%'
  AND created_at > 'YYYY-MM-DD HH:MM:SS'  -- 6.2.2 的开始时间
ORDER BY occurred_at;
```

**C. 看审计事件**（确认 audit 接入）：

```sql
-- 看 archive 相关 audit 事件（fetcher 成功 / 回调收到）
SELECT
    category,
    payload,
    created_at
FROM wecom_rpa_audit_logs
WHERE category LIKE 'archive_%'
  AND created_at > 'YYYY-MM-DD HH:MM:SS'
ORDER BY created_at;
```

应看到 `archive_callback_received` 和 `archive_fetch_success` 两类事件交替出现。

#### 6.2.5 计算漏抓率

| 发送方 | 实际发送数 | 数据库收到数 | 漏抓数 |
|--------|----------|------------|--------|
| 陆伟 | 20 | ? | ? |
| 孙晨 | 20 | ? | ? |
| 芮秀 | 20 | ? | ? |

**验收**：
- **总漏抓率 < 3%**（即 60 条至少收到 58 条）
- 如果配置了白名单只监控陆伟（2.6 步），那么**孙晨和芮秀的消息应该全部被过滤**，只收到陆伟的 20 条
  - 这是 F6 白名单测试的关键点
  - 后端审计表会有 `monitor_whitelist_filtered` 类似事件（如有），或看 `channel_messages` 是否完全没收到孙晨/芮秀

#### 6.2.6 如果漏抓率高

排查方向（**全部在服务端**）：

1. **回调路径是否生效**：后端日志搜 `[ArchiveCallback] POST`——如果完全没有，说明企微回调未到达（检查 3.4 企微后台 URL 配置 + 公网可达性）。此时仅靠 60s 兜底轮询，可能延迟但不应漏。
2. **拉取是否被 45009 限速**：后端日志搜 `45009` + 审计表查 `archive_fetch_rate_limited`——如果有，加大 `poll_interval_seconds` 或排查为何回调路径失效。
3. **RSA 解密是否失败**：后端日志搜 `[ServerArchiveFetcher] 解密/处理失败`——如果有，说明私钥与企微公钥不匹配（重新生成密钥对）。
4. **白名单是否误过滤**：后端审计表搜白名单过滤相关事件。
5. **seq 是否在推进**：

```sql
-- tenant_channel_configs.config 是 JSON，看 last_seq 字段
SELECT config_id, config->>'last_seq' AS last_seq,
       config->>'last_callback_at' AS last_callback_at,
       config->>'last_fetch_at' AS last_fetch_at
FROM tenant_channel_configs
WHERE channel_type = 'wecom_personal_rpa';
```

`last_seq` 应在持续增长，`last_callback_at` 应接近实时，`last_fetch_at` 间隔不超过 60s+。

### 6.3 F7：二维码截取（10 分钟）

二维码监听在客户端（与服务端拉取模式无关），测试步骤不变。

**测试步骤**：

1. **关闭企业微信**（任务管理器 → WXWork.exe → 结束任务，或企微菜单 → 退出）
2. **等 30 秒以内**（QrCodeWatcher 每 25 秒检查一次）
3. 客户端日志应出现：`QrCodeWatcher 上报 status=NeedLogin has_qr=True`
4. 打开后端管理后台 → 找到该客户端绑定的账号 → 应该能看到**二维码图片**
5. 用手机企微扫码登录
6. **30 秒内**客户端日志应出现：`QrCodeWatcher 上报 status=Online has_qr=False detail=扫码成功`
7. 后端管理后台的二维码应消失

**验收**：
- 关闭企微后 30 秒内服务端有二维码
- 扫码后 30 秒内状态恢复 online
- 二维码图片清晰可见

**如果二维码区域是黑屏 / 错位** → 客户端 PS 端坐标没校准，参考 8.1 节真机校准。

### 6.4 异常场景测试（30 分钟）

#### 6.4.1 桌面锁屏

**步骤**：
1. 客户端正常运行中
2. 按 `Win + L` 锁屏
3. 等 60 秒（DesktopHealthSupervisor 每 60 秒检查一次）

**期望客户端日志**：`DesktopHealthSupervisor 检测到异常 → status=DesktopLocked`
**期望服务端日志**：收到 status=desktop_locked 上报

**验证**：锁屏期间，让陆伟发消息——
- **服务端应正常拉取到消息并调 agent**（拉取不依赖客户端）
- **客户端不应该调 PS 发送**（PS 在锁屏后无法操作 UI），出站 action 进入 outbox 等恢复
- Unlock 后客户端恢复，outbox 中的 action 补发执行

#### 6.4.2 网络断开 + WebSocket 重连

**步骤**：
1. 客户端正常运行中
2. 拔网线 / 关 WiFi（或临时禁用网卡）
3. 客户端日志应出现：
   ```
   WebSocketConnectionManager 连接断开，1s 后重连
   WebSocketConnectionManager 连接断开，2s 后重连
   （指数退避，30s 封顶）
   ```
4. 恢复网络，客户端日志应出现：
   ```
   WebSocketConnectionManager 重连成功
   ServerMessageDispatcher 收到 actions 事件
   ```

**关键验证**：断网期间**服务端拉取仍正常进行**（不依赖客户端网络），agent 回复会暂存 outbox；客户端恢复网络后**全部补发执行**（不会丢）。

**反向验证**：断网期间发送方发的消息，服务端应仍在 `channel_messages` 入库（看 6.2.4 SQL），只是 agent 回复延迟到客户端恢复后才送达。

#### 6.4.3 客户端崩溃 + 重启

**步骤**：
1. 客户端正常运行中，正在处理一个出站 action
2. 任务管理器强杀 `Client.App.exe`
3. 服务端日志应出现：`ClientConnection 离线 / WebSocket 关闭`，后续 agent 回复进入 outbox
4. 重新 `dotnet run` 启动客户端

**验证**：
- 启动日志 `OutboundDispatcher 启动完成，恢复 N 个未完成 action`（之前未完成的 action 重新入队执行）
- **服务端 archive 不受客户端崩溃影响**——崩溃期间发送方的消息仍正常入库 + agent 调用
- 客户端重启后 outbox 中的 action 全部补发执行

**与原客户端模式的关键差异**：客户端崩溃**不会丢失消息**（因为消息在服务端 archive，不在客户端 seq）。

### 6.5 完整链路联调（10 分钟）

**目的**：验证服务端拉取 + agent + 客户端出站全链路。

**步骤**：

1. 用陆伟的手机企微给你的客户端账号发：`今天天气怎么样？`
2. 计时开始
3. 观察全链路：
   - **服务端日志**：`[ArchiveCallback] POST 事件接收成功` 或 `[ServerArchivePoller] 触发拉取`
   - **服务端日志**：`[ServerArchiveFetcher] 拉取完成 batch=1 processed=1`
   - **服务端日志**：`agent 推理开始 session=wecom_personal_rpa:...`
   - **服务端日志**：`agent 推理完成，回复："今天北京晴..."`
   - **服务端日志**：`adapter.send_message → deliver_actions → WS push to client`
   - **客户端日志**：`OutboundDispatcher 处理 send_text conv=陆伟`
   - **陆伟手机收到回复**
4. 计时结束

**验收**：
- **端到端延迟 < 30 秒**（从陆伟发出消息到收到回复）
- 陆伟收到的回复是合理的（不是乱码、不是错回）

**性能基线参考**：
- 企微回调到达：实时（< 1s）
- 兜底轮询触发：60s 周期（仅当回调丢失时才走这条路径）
- 服务端拉取 + 解密：100ms 内
- agent 推理：取决于模型（DeepSeek/Qwen，一般 5-15s）
- 客户端 PS 发送：3-8s

### 6.6 媒体附件链路（可选，10 分钟）

**注意**：服务端拉取模式 Phase 1-12 **未实现服务端媒体下载**——服务端 archive.fetcher 仅透传 `sdkfileid` 到 envelope.attachments，由客户端 C# ArchiveMediaDownloader 处理。但客户端需要从 envelope 拿到 sdkfileid 才能 download，这个链路在第一期是否完整运行需要真机验证。

**步骤**：

1. 陆伟发一张**图片**到客户端账号
2. 观察全链路：
   - **服务端日志**：`[ServerArchiveFetcher] 拉取完成 ... msgid=xxx msgtype=image`
   - **服务端 envelope**：`payload.attachments[0].sdk_file_id = ...`（透传，URL 为空）
   - **客户端日志**：观察是否能拿到 sdk_file_id 并触发 ArchiveMediaDownloader（**这一步可能失败**，因为 envelope 的 attachments 结构与原客户端 InboundEventBuilder 不完全一致）
3. 如果客户端下载失败，记录日志，**不视为 P0 bug**（设计文档 §5.3.1 已注明「媒体下载仍由客户端发起」但具体接入路径在第一期未完整联调）

**预期结果**：媒体附件链路**可能不完整**，需根据真机结果评估。本期验收范围**不含媒体附件**，可记为「已知限制」。

---

## 7. 验证结果汇总（10 分钟填表）

测完后填这张表（建议直接保存到 `docs/test-results/server-archive-result-YYYYMMDD.md`）：

| 验证项 | 验收标准 | 实际结果 | 通过？ |
|--------|---------|---------|--------|
| 3.5 5 步验证连接 | 全部 stages_passed | stages_passed=[?] | ✅ / ❌ |
| 6.1 F2 搜索用户 | 10 次成功 ≥ 9 次 | 成功 X 次 | ✅ / ❌ |
| 6.1 F3 发文本 | 误发 0 次 | 误发 X 次 | ✅ / ❌ |
| 6.1 F3 发图片 | 客户端真机收到 | ✅ / ❌ | |
| 6.1 F3 发文件 | 客户端真机收到 | ✅ / ❌ | |
| 6.2 服务端拉取漏抓率 | < 3%（60 条至少 58 条） | 收到 X 条 | ✅ / ❌ |
| 6.2 F6 白名单 | 只收到陆伟的 20 条 | 收到陆伟 X 条 + 其他 X 条 | ✅ / ❌ |
| 6.2 回调路径 | 服务端日志有 `[ArchiveCallback] POST` | ✅ / ❌ | |
| 6.2 audit 事件 | archive_callback_received + archive_fetch_success | ✅ / ❌ | |
| 6.3 F7 二维码 | 30 秒内展示 + 扫码恢复 | X 秒内展示 | ✅ / ❌ |
| 6.4.1 锁屏暂停 | 锁屏期间不调 PS + 服务端拉取不受影响 | ✅ / ❌ | |
| 6.4.2 WebSocket 重连 | 断网重连 + outbox 补发 + 服务端拉取不丢 | ✅ / ❌ | |
| 6.4.3 客户端崩溃恢复 | action 不丢 + 服务端拉取不丢 | ✅ / ❌ | |
| 6.5 完整链路延迟 | < 30 秒 | X 秒 | ✅ / ❌ |
| 6.6 媒体附件链路 | （可选，已知可能不完整） | ✅ / ❌ / N/A | |

---

## 8. 常见问题排查

### 8.1 二维码区域是黑屏 / 错位

PS 端坐标基准需要真机校准（与服务端拉取无关，属于客户端出站 PS 自动化）。

**校准方法**：

```powershell
cd c:\repos\aid-work-agent\clients\wecom-personal-rpa
powershell -ExecutionPolicy Bypass -File scripts\debug-navigate.ps1 -Step screenshot
```

打开 `debug-out/cap_xxx/screenshot.png`，用图片编辑器找二维码的左上角和右下角坐标（像素），更新客户端 assets/wecom_nodes.yaml 中相关节点的坐标。

### 8.2 搜索框点击不准

类似 8.1，是 PS 自动化坐标问题。检查客户端 `assets/wecom_nodes.yaml` 中的搜索框节点坐标。

### 8.3 服务端拉取拉不到任何消息

排查顺序（**全部在服务端**）：

1. **企微后台会话内容存档**：确认开通了，且许可范围勾选了客户端登录账号 + 测试发送方
2. **secret 对不对**：在后端机器直接 curl 测试 gettoken：

```bash
curl "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=ww888...&corpsecret=公7_GxFc..."
# 应返回 {"errcode":0,"access_token":"..."} 而不是 {"errcode":40001}
```

3. **私钥对不对**：会话存档的私钥只能用一次（首次下载），如果用过了需要重新生成
4. **回调 URL 是否可达**：从公网 curl 验证：

```bash
curl "https://<后端公网域名>/t/<tenant_id>/wecom_personal_rpa/callback/<config_id>?msg_signature=x&timestamp=x&nonce=x&echostr=x"
# 应返回非 404 / 非 500（具体 echostr 解密失败 401 是正常的，因为这里 query 是假的）
```

5. **白名单是否误过滤**：

```sql
-- 查 is_allowed_by_monitor_whitelist 相关审计
SELECT * FROM wecom_rpa_audit_logs
WHERE payload LIKE '%monitor_whitelist%'
AND created_at > 'YYYY-MM-DD HH:MM:SS';
```

### 8.4 PS 调用一直超时（客户端出站）

可能原因：
- 企微窗口被遮挡（其他全屏应用盖住了）→ 把企微窗口置顶
- DPI 不对 → 检查 2.1 的 DPI 设置
- 输入法干扰 → 切换到英文输入法

### 8.5 漏抓率高的常见原因

| 原因 | 排查 |
|------|------|
| 回调路径完全失效（只靠兜底轮询） | 服务端日志搜 `[ArchiveCallback] POST`——完全没有则回调失败 |
| 拉取频率被限速（45009） | 服务端日志搜 `45009` + 审计表查 `archive_fetch_rate_limited` |
| RSA 解密失败 | 服务端日志搜 `解密/处理失败`，确认 private_key |
| 网络抖动 | 服务端日志搜 `拉取超时` 或 `网络异常` |
| 服务端白名单误过滤 | 审计表搜白名单过滤相关事件 |

### 8.6 客户端启动崩溃

最常见的：
- `client_secret` 不对 → 重新去平台后台注册
- 后端地址不通 → 用 `curl http://<后端>` 测试
- 端口被占用 → 后端可能跑在 8000，客户端连不上

### 8.7 验证连接某一步失败

| 失败步骤 | 诊断 | 处理 |
|---------|------|------|
| Step 1 access_token：Corp ID 或 Secret 错误 | errcode=40013/40125 | 重新核对 corpid + archive_secret |
| Step 2 chat_data：未获得 SDK 权限 | errcode=60011/48002 | 企微后台开通会话存档 SDK 权限 |
| Step 2 chat_data：45009 频率限制 | errcode=45009 | 等几分钟再测，避免短时间反复点验证 |
| Step 3+4 private_key：RSA 解密错 | 私钥与公钥不匹配 | 重新生成密钥对，下载新私钥 |
| Step 5 callback：Token 或 EncodingAESKey 错 | 自测事件验签失败 | 重新核对 token + encoding_aes_key |

---

## 9. 验证反馈格式

测完后请把以下信息发给我（Claude）：

1. **第 7 节的验证结果汇总表**（直接截图或文字）
2. **遇到的所有问题**（按 8.x 章节对应）
3. **服务端日志关键片段**（特别是 `[ServerArchiveFetcher]` / `[ArchiveCallback]` / `archive_*` audit 相关）
4. **客户端日志关键片段**（特别是出站 + 锁屏 + 重连相关）
5. **任何"没在文档里覆盖"的现象**

我会根据反馈：
- 修代码 bug（如果是服务端 archive 模块的问题）
- 调整真机配置参数（如客户端 PS 坐标校准）
- 补充文档（如果是文档没说清楚）
- 决定是否补做 Phase 13（如媒体附件链路、verify 路由 e2e 集成测试）

---

## 10. 时间预算总览

| 阶段 | 时间 |
|------|------|
| 2. 测试前准备 | 30 分钟 |
| 3. 前端开通渠道 + 企微后台回调 + 验证连接 | 15 分钟 |
| 4. 客户端配置 | 5 分钟 |
| 5. 启动客户端 | 5 分钟 |
| 6.1 F2/F3 搜索发送 | 10 分钟 |
| 6.2 服务端拉取 + 白名单 | 60 分钟（连续 1 小时） |
| 6.3 F7 二维码 | 10 分钟 |
| 6.4 异常场景 | 30 分钟 |
| 6.5 完整链路 | 10 分钟 |
| 6.6 媒体附件（可选） | 10 分钟 |
| 7. 结果汇总 | 10 分钟 |
| **总计** | **约 3.5 小时（含 1 小时连续监听）** |

可以分两次测：上午测 6.1/6.3/6.4/6.5（约 1.5 小时），下午专门测 6.2（1 小时连续监听）。

---

## 11. 重要提醒

1. **会话存档凭证要提前准备**（2.3 步），5 个凭证缺一不可
2. **企微后台回调 URL 配置**（3.4 步）是服务端拉取模式**最关键**的步骤，公网可达性必须先打通
3. **测试期间不要锁屏**（除非测 6.4.1 锁屏场景）
4. **测试期间不要最小化企微**（PS 操作需要窗口可见）
5. **不要在测试机器上做其他重操作**（CPU/磁盘占用会影响 PS 调用稳定性）
6. **遇到误发立即停服**（消息发错人比漏抓严重得多）—— 误发是 F3 出站问题，与 archive 无关，但要立即停客户端
7. **每次测前先备份数据库**（出问题能回滚）：

```bash
pg_dump -U postgres aid_work_agent > backup_before_server_archive_test.sql
```

8. **第一期限制**：
   - listen_mode 强制 'server'（client 选项前端禁用）
   - 服务端不实现媒体下载（仅透传 sdkfileid 给客户端）
   - verify 路由 client 模式分支代码保留但不调用

祝验证顺利。
