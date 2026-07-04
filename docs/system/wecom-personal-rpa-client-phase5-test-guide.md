# Phase 5 真机测试指引

> 关联文档：
> - 设计：[wecom-personal-rpa-client-design.md](./wecom-personal-rpa-client-design.md)
> - 开发计划：[plans/plan-wecom-personal-rpa-client.md](../../plans/plan-wecom-personal-rpa-client.md) 阶段 5
> - 协议：[wecom-personal-rpa-protocol.md](./wecom-personal-rpa-protocol.md)
>
> 创建日期：2026-07-01
> 适用版本：Phase 1-4 已合入 master（commit `0335614` 起）
> 状态：📋 待测试

---

## 0. 测试目标一句话

**验证 RPA 客户端在真实企业微信 + 真实后端环境下，能完成「用户发消息 → AI 回复 → 用户收到回复」的全链路**，并满足：
- 漏抓率 < 3%
- 误发次数 = 0
- 端到端延迟 < 30 秒
- 异常场景（锁屏 / 断网 / 崩溃重启）全部正确处理

整个测试预计 **3-4 小时**（含 1 小时连续监听）。

---

## 1. 测试前准备清单（30 分钟）

### 1.1 硬件 / 软件环境

| 项 | 要求 | 检查命令 |
|----|------|---------|
| 操作系统 | Windows 11（10 也可） | `winver` |
| .NET 8 SDK | 已安装 | `dotnet --version` 应输出 `8.x` |
| PowerShell | 5.1（系统自带，**不要用 pwsh 7**） | `powershell -Command $PSVersionTable.PSVersion` |
| 企业微信 PC 客户端 | 5.0.8 或更高 | 企微 → 设置 → 关于 |
| 屏幕分辨率 | 1920×1080 或更高 | 桌面右键 → 显示设置 |
| DPI 缩放 | 100% 或 150% | 同上 |
| 网络 | 能访问 `qyapi.weixin.qq.com` 和后端服务 | `ping qyapi.weixin.qq.com` |

### 1.2 测试账号

| 用途 | 账号 | 说明 |
|------|------|------|
| **客户端登录账号** | 你的企业微信账号 | 登录在被测 PC 上 |
| **测试发送方 1** | 陆伟 | 给客户端账号发消息 |
| **测试发送方 2** | 孙晨 | 同上 |
| **测试发送方 3** | 芮秀 | 同上 |

3 个测试发送方可以用手机企微或另一台 PC 企微登录。**测试发送方账号必须与企业微信会话存档范围匹配**（见 1.3）。

### 1.3 企业微信会话存档凭证（**最关键的准备工作**）

如果还没有会话存档凭证，先去**企业微信管理后台**开通：

```
管理后台 → 管理工具 → 会话内容存档 → 开启
```

开通后创建「会话存档应用」，需要拿到 **3 个凭证**：

| 凭证 | 来源 | 示例 |
|------|------|------|
| `corpid` | 企业 ID，管理后台 → 我的企业 → 企业信息 | `ww8888888888888888` |
| `secret` | 会话存档应用的 Secret | `公7_GxFc...` |
| `private_key` | 会话存档 RSA 私钥（.pem 文件） | 下载后保存到本地 |

**重要**：会话存档的「许可」要勾选**所有需要被监听的员工**（包括你的客户端登录账号 + 3 个测试发送方），否则拉不到消息。

### 1.4 后端服务端

后端跑在哪台机器都行（本机或服务器），但需要满足：

| 项 | 要求 |
|----|------|
| Python 环境 | 已安装项目依赖（`pip install -r requirements.txt`） |
| PostgreSQL | 已 `init-postgres.sql` 初始化 + 跑过 `db_update.sql` 增量 |
| Redis | 运行中（用于 nonce 防重放 + 二维码 TTL） |
| 环境变量 | `DATABASE_URL` / `REDIS_URL` / `LLM_PROVIDER` / 模型 API key 等已配置 |
| 新增环境变量 | `WECOM_RPA_ARCHIVE_ENABLED=true`（让 /config 返回 archive_enabled=true） |

启动命令（在后端机器）：

```bash
cd c:/repos/aid-work-agent
python -m src.main
```

看到 `Uvicorn running on http://0.0.0.0:8000` 即成功。

### 1.5 在后端注册被测客户端（**一次性准备**）

打开后端管理后台或用 SQL 注册一个客户端，拿到 `client_id` + `client_secret`：

**方式 A：用平台后台 UI**（推荐）

1. 浏览器访问 `http://<后端>/portal/rpa-bindings`
2. 点「+ 新增绑定」→ 选择目标租户 → 起名「Phase5 测试」→ 关联一个数字员工
3. 保存后弹出**密钥一次性展示对话框**，**立即复制保存** `client_id` 和 `client_secret`（关闭后不可再看）

**方式 B：直接 SQL**（应急）

```sql
-- 在 PostgreSQL 里
SELECT register_rpa_client('tenant_xxx', 'Phase5 测试客户端');
-- 拿到 client_id 和 encrypted_secret，但 secret 是加密的，需要管理员解密
-- 不推荐，建议走 UI
```

记下：
```
client_id     = client_xxxxxxxxxx
client_secret = abcdef0123456789...
```

### 1.6 配置白名单 binding（用于 F6 验证）

在租户前台或平台后台，给上一步创建的 binding 配置**只监控"陆伟"**：

1. 编辑 binding 详情
2. 「监控用户名」字段填：`陆伟`
3. 「监控用户 ID」留空
4. 保存

这样 F6 白名单过滤测试时，孙晨/芮秀的消息会被客户端 + 服务端双重过滤。

---

## 2. 客户端配置文件准备（10 分钟）

### 2.1 复制示例配置

```bash
cd c:/repos/aid-work-agent/clients/wecom-personal-rpa
copy configs\client.example.yaml configs\client.yaml
```

### 2.2 编辑 `configs\client.yaml`

**完整填好的示例**（直接复制改一下）：

```yaml
# 后端地址（改成你的后端实际地址）
agent_base_url: "http://127.0.0.1:8000"

# 客户端身份（1.5 拿到的）
client_id: "client_xxxxxxxxxx"
client_secret: "abcdef0123456789..."

# 租户 ID（1.5 注册时选择的租户）
tenant_id: "tenant_xxx"

# 出站执行配置
outbound:
  db_path: "data/outbox.db"
  download_temp_dir: "temp/outbound-downloads"
  max_attachment_size_mb: 100
  max_retries: 3

# PowerShell 自动化
automation:
  powershell_executable: "powershell.exe"
  powershell_ops_script: "scripts/wecom-ops.ps1"
  invoke_timeout_seconds: 30
  search_box_base_x1: 330
  search_box_base_y1: 34
  search_box_base_x2: 430
  search_box_base_y2: 66
  search_box_base_window_width: 1936

# 消息源（会话存档）—— 必填
message_source:
  mode: "archive"
  corpid: "ww8888888888888888"               # 1.3 拿到的 corpid
  secret: "公7_GxFc..."                       # 1.3 拿到的 secret
  private_key_path: "config/archive_private_key.pem"  # 1.3 下载的私钥
  poll_interval_seconds: 3
  batch_limit: 1000
  download_media: true
  media_temp_dir: "temp/archive-media"

# 二维码检测
qr_code:
  poll_interval_seconds: 25
  region_bbox_base: [530, 200, 800, 470]   # 真机校准后调整

# 监控白名单
monitor_users:
  cache_refresh_minutes: 60
  binding_id: "binding_xxx"                  # 1.5 创建的 binding 的 id
```

### 2.3 放置私钥文件

把 1.3 下载的 RSA 私钥文件放到：

```
c:/repos/aid-work-agent/clients/wecom-personal-rpa/config/archive_private_key.pem
```

文件格式应该是这样的（PEM）：

```
-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQD...
（多行 base64）
-----END PRIVATE KEY-----
```

### 2.4 构建客户端

```bash
cd c:/repos/aid-work-agent/clients/wecom-personal-rpa
dotnet build WeComPersonalRpaClient.sln -c Debug
```

**期望**：`0 个警告 0 个错误`。

如果 build 失败，先回头检查环境（.NET 8 SDK 是否装好），不要继续。

---

## 3. 启动客户端（5 分钟）

### 3.1 启动企微

1. 双击桌面企业微信图标启动
2. 用客户端登录账号扫码登录
3. 等待主界面完全加载

**注意**：测试期间**不要锁屏**、**不要最小化企微**，让窗口保持可见。

### 3.2 启动客户端

打开 PowerShell（**普通权限即可，不需要管理员**）：

```powershell
cd c:\repos\aid-work-agent\clients\wecom-personal-rpa\src\Client.App
dotnet run --no-build
```

或者直接跑 exe：

```powershell
cd c:\repos\aid-work-agent\clients\wecom-personal-rpa\src\Client.App\bin\Debug\net8.0-windows10.0.19041.0
.\Client.App.exe
```

### 3.3 检查启动日志

客户端启动后，控制台或日志文件会输出（路径 `%LOCALAPPDATA%\WeComPersonalRpa\Client.App\logs\client-YYYYMMDD.log`）：

**期望看到的关键日志行**：
```
[INF] [Client.App] 进程启动
[INF] Microsoft.Hosting.Lifetime Application started
[INF] [Client.App] Generic Host 已启动
[INF] [TrayApp] 托盘已挂载
[INF] MonitorUsersCache 初始化完成
[INF] OutboundDispatcher 启动完成，恢复 0 个未完成 action
[INF] ChatArchiveListener 启动，corpid=ww888... poll=3s
```

**如果出现这些日志说明有问题**：

| 日志 | 含义 | 处理 |
|------|------|------|
| `MonitorUsersCache 初始化失败` | /config 接口不通或鉴权失败 | 检查后端是否启动、client_secret 是否对 |
| `db.get_client 失败` | 客户端没在后端注册 | 检查 client_id 是否对、是否启用 |
| `ChatArchiveListener 获取 access_token 失败` | 企微 corpid/secret 错 | 检查 client.yaml 的 message_source 段 |
| `RPA 解密私钥加载失败` | private_key.pem 路径错或格式不对 | 检查 2.3 步骤 |
| `qr_image_base64` 出现在日志 | （脱敏过滤器失效）| 立即停服，反馈 bug |

启动成功后，客户端会**静默**等待消息（系统托盘有图标），不要关闭 PowerShell 窗口。

---

## 4. 功能测试执行

### 4.1 F2 + F3：搜索 + 发送（10 分钟）

**前置**：找一个绑定该客户端的数字员工（通过平台后台绑定），然后**通过后端让 agent 发消息**——也就是给 agent 发一条消息触发回复。

**简单方法**：直接通过 web 端 `http://<后端>` 找到这个数字员工对话，发一条 "回复文本：你好" 之类。

**验证步骤**：

1. 在 web 对话里发：`请帮我给陆伟发一条消息："你好，这是 Phase5 测试"`（agent 应该用工具触发 send_text action）
2. 观察：
   - **客户端日志**应出现：`OutboundDispatcher 处理 send_text ActionId=... conv=陆伟`
   - **PS 进程**短暂启动（任务管理器看到 powershell.exe 闪一下）
   - **陆伟**（手机或另一台 PC）应收到消息："你好，这是 Phase5 测试"
3. 重复 10 次（每次发不同内容），记录**成功/失败次数**

**验收**：
- 成功率 ≥ 90%（即 10 次至少 9 次成功）
- **误发 0 次**（陆伟之外的人没收到）

**测试发送图片**：

让 agent 回复一张图片（比如生成图表），观察客户端是否：
1. 日志出现 `AttachmentDownloader 下载成功 size=xxKB`
2. PS 把图片粘贴到企微
3. 陆伟收到图片

**测试发送文件**：

类似上面，让 agent 发一个 .pdf 或 .docx，观察同上。

### 4.2 F4 + F6：会话存档监听 + 白名单（60 分钟，最长）

**核心测试**——客户端连续 1 小时监听消息，验证漏抓率。

#### 4.2.1 准备工作

1. 准备好 3 个测试发送方（陆伟/孙晨/芮秀），各发送 20 条消息（共 60 条）
2. 每条消息内容可以简单：`测试1` `测试2` ... `测试20`
3. 时间间隔随意（不必每秒一条，但 1 小时内发完）

#### 4.2.2 开始监听

确认客户端已经启动并连上后端（看启动日志），开始记录时间戳：

```
开始时间：YYYY-MM-DD HH:MM:SS
```

#### 4.2.3 发消息（按计划发完 60 条）

让 3 个发送方各发 20 条到**你的客户端登录账号**。

可以群聊也可以单聊，**但要在会话存档许可范围内**。

#### 4.2.4 验证收到

每发一条，去**后端管理后台**或**后端数据库**查这条消息是否被投递给 agent。

**SQL 查询方式**（在后端机器执行）：

```sql
-- 看客户端上报的消息（会话存档 source_type=wecom_personal_rpa）
SELECT
    sender_display_name,
    text,
    occurred_at,
    created_at
FROM channel_messages
WHERE session_id LIKE 'wecom_personal_rpa:%'
  AND created_at > 'YYYY-MM-DD HH:MM:SS'  -- 4.2.2 的开始时间
ORDER BY occurred_at;
```

#### 4.2.5 计算漏抓率

| 发送方 | 实际发送数 | 数据库收到数 | 漏抓数 |
|--------|----------|------------|--------|
| 陆伟 | 20 | ? | ? |
| 孙晨 | 20 | ? | ? |
| 芮秀 | 20 | ? | ? |

**验收**：

- **总漏抓率 < 3%**（即 60 条至少收到 58 条）
- 如果配置了白名单只监控陆伟（1.6 步），那么**孙晨和芮秀的消息应该全部被过滤**，只收到陆伟的 20 条
  - 这是 F6 白名单测试的关键点

#### 4.2.6 如果漏抓率高

排查方向：
1. 看客户端日志是否有 `ChatArchiveListener 解密失败`（RSA 私钥不对？）
2. 看客户端日志是否有 `拉取超时 / 45009 频率限制`（拉太快？）
3. 看后端日志是否有 `monitor_whitelist_filtered` 审计（白名单过滤的）
4. 看 `archive_seq` SQLite 表是否在推进：

```bash
cd c:/repos/aid-work-agent/clients/wecom-personal-rpa
powershell -Command "sqlite3 data/archive.db 'SELECT * FROM archive_seq'"
```

### 4.3 F7：二维码截取（10 分钟）

**测试步骤**：

1. **关闭企业微信**（任务管理器 → WXWork.exe → 结束任务，或企微菜单 → 退出）
2. **等 30 秒以内**（QrCodeWatcher 每 25 秒检查一次）
3. 客户端日志应出现：
   ```
   QrCodeWatcher 上报 status=NeedLogin has_qr=True
   ```
4. 打开后端管理后台 → 找到该客户端绑定的账号 → 应该能看到**二维码图片**
5. 用手机企微扫码登录
6. **30 秒内**客户端日志应出现：
   ```
   QrCodeWatcher 上报 status=Online has_qr=False  detail=扫码成功
   ```
7. 后端管理后台的二维码应消失

**验收**：
- 关闭企微后 30 秒内服务端有二维码
- 扫码后 30 秒内状态恢复 online
- 二维码图片清晰可见

**如果二维码区域是黑屏 / 错位**：

→ PS 端 `qr_code.region_bbox_base` 坐标没校准，参考 6.1 节真机校准。

### 4.4 异常场景测试（30 分钟）

#### 4.4.1 桌面锁屏

**步骤**：
1. 客户端正常运行中
2. 按 `Win + L` 锁屏
3. 等 60 秒（DesktopHealthSupervisor 每 60 秒检查一次）

**期望日志**：
```
DesktopHealthSupervisor 检测到异常 → status=DesktopLocked
```

后端应收到 status=desktop_locked 上报。

**验证**：锁屏期间，让陆伟发消息，**客户端不应该调 PS 发送**（PS 在锁屏后无法操作 UI）。Unlock 后客户端恢复。

#### 4.4.2 网络断开 + WebSocket 重连

**步骤**：
1. 客户端正常运行中
2. 拔网线 / 关 WiFi（或临时禁用网卡）
3. 客户端日志应出现：
   ```
   WebSocketConnectionManager 连接断开，1s 后重连
   WebSocketConnectionManager 连接断开，2s 后重连
   WebSocketConnectionManager 连接断开，4s 后重连
   （指数退避，30s 封顶）
   ```
4. 恢复网络，客户端日志应出现：
   ```
   WebSocketConnectionManager 重连成功
   ServerMessageDispatcher 收到 actions 事件  # 服务端推 outbox 增量
   ```

**验证**：断网期间服务端下发的 action，恢复网络后**全部补发执行**（不会丢）。

#### 4.4.3 客户端崩溃 + 重启

**步骤**：
1. 客户端正常运行中，正在处理一个出站 action
2. 任务管理器强杀 `Client.App.exe`
3. `archive_seq` SQLite 里的 seq 应该是某个数字，**记下来**：

```bash
powershell -Command "sqlite3 data/archive.db 'SELECT * FROM archive_seq'"
# 输出：client_xxx|12345|2026-07-01T...
```

4. 重新 `dotnet run` 启动客户端

**验证**：
- 启动日志 `ChatArchiveListener 启动 seq=12345`（从断点续传，**不重头拉**）
- 启动日志 `OutboundDispatcher 启动完成，恢复 N 个未完成 action`（之前未完成的 action 重新入队执行）
- 崩溃前用户发的消息，重启后**不应该重复上报**

### 4.5 完整链路联调（10 分钟）

**目的**：验证 7 大功能串起来跑通。

**步骤**：

1. 用陆伟的手机企微给你的客户端账号发：`今天天气怎么样？`
2. 计时开始
3. 观察全链路：
   - 客户端日志：`ChatArchiveListener 收到 msgid=xxx msgtype=text from=wm_luwei`
   - 客户端日志：`InboundEventReporter 上报 event_id=xxx`
   - 后端日志：`agent 推理开始 session=wecom_personal_rpa:...`
   - 后端日志：`agent 推理完成，回复："今天北京晴..."`
   - 客户端日志：`OutboundDispatcher 处理 send_text conv=陆伟`
   - **陆伟手机收到回复**
4. 计时结束

**验收**：
- **端到端延迟 < 30 秒**（从陆伟发出消息到收到回复）
- 陆伟收到的回复是合理的（不是乱码、不是错回）

### 4.6 媒体附件完整链路（10 分钟）

**目的**：验证图片附件全链路（F4 下载 → F6 上传 → agent 处理 → F3 回图）。

**步骤**：

1. 陆伟发一张**图片**到客户端账号（手机选图发）
2. 观察：
   - 客户端日志：`ChatArchiveListener 收到 msgid=xxx msgtype=image sdkfileid=xxx`
   - 客户端日志：`ArchiveMediaDownloader 下载完成 size=xxKB path=temp/archive-media/...`
   - 客户端日志：`UploadMediaAsync 上传成功 url=https://...`
   - 客户端日志：`temp/archive-media/xxx.png 已删除`
   - 后端日志：`agent 收到 image attachment`
   - 后端日志：`agent 处理图片完成`
   - 客户端日志：`OutboundDispatcher 处理 send_image conv=陆伟`
   - **陆伟收到 agent 的图片回复**

**验收**：图片不丢、不上错、临时文件被清理。

---

## 5. 测试结果汇总（10 分钟填表）

测完后填这张表（建议直接保存到 `docs/test-results/phase5-result-YYYYMMDD.md`）：

| 测试项 | 验收标准 | 实际结果 | 通过？ |
|--------|---------|---------|--------|
| F2 搜索用户 | 10 次成功 ≥ 9 次 | 成功 X 次 | ✅ / ❌ |
| F3 发文本 | 误发 0 次 | 误发 X 次 | ✅ / ❌ |
| F3 发图片 | 客户端真机收到 | ✅ / ❌ | |
| F3 发文件 | 客户端真机收到 | ✅ / ❌ | |
| F4 漏抓率 | < 3%（60 条至少 58 条） | 收到 X 条 | ✅ / ❌ |
| F6 白名单 | 只收到陆伟的 20 条 | 收到陆伟 X 条 + 其他 X 条 | ✅ / ❌ |
| F7 二维码 | 30 秒内展示 + 扫码恢复 | X 秒内展示 | ✅ / ❌ |
| 锁屏暂停 | 锁屏期间不调 PS | ✅ / ❌ | |
| WebSocket 重连 | 断网重连 + outbox 补发 | ✅ / ❌ | |
| 客户端崩溃恢复 | seq 续传 + action 不丢 | ✅ / ❌ | |
| 完整链路延迟 | < 30 秒 | X 秒 | ✅ / ❌ |
| 媒体附件链路 | 图片不丢 + 临时文件清理 | ✅ / ❌ | |

---

## 6. 常见问题排查

### 6.1 二维码区域是黑屏 / 错位

PS 端 `qr_code.region_bbox_base` 默认 `[530, 200, 800, 470]`，真机不一定准。

**校准方法**：

```powershell
# 在客户端机器跑校准脚本（如果有的话），或手动：
cd c:\repos\aid-work-agent\clients\wecom-personal-rpa
powershell -ExecutionPolicy Bypass -File scripts\debug-navigate.ps1 -Step screenshot
```

打开 `debug-out/cap_xxx/screenshot.png`，用图片编辑器找二维码的左上角和右下角坐标（像素），更新 `client.yaml` 的 `qr_code.region_bbox_base`。

### 6.2 搜索框点击不准

类似 6.1，`automation.search_box_base_*` 默认值是 1936 宽度窗口下的，真机宽度不同需要按比例换算。代码会自动 scale，但如果基准值本身偏了，就要改：

```yaml
automation:
  search_box_base_x1: ???
  search_box_base_y1: ???
  search_box_base_x2: ???
  search_box_base_y2: ???
  search_box_base_window_width: ???   # 填写你截屏时的窗口宽度
```

### 6.3 会话存档拉不到任何消息

排查顺序：
1. **企业微信管理后台 → 会话内容存档**：确认开通了
2. **许可范围**：确认许可勾选了客户端登录账号 + 测试发送方
3. **secret 对不对**：直接 curl 测试 `gettoken`：

```bash
curl "https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=ww888...&corpsecret=公7_GxFc..."
# 应返回 {"errcode":0,"access_token":"..."} 而不是 {"errcode":40001}
```

4. **私钥对不对**：会话存档的私钥只能用一次（首次下载），如果用过了需要重新生成

### 6.4 PS 调用一直超时

可能原因：
- 企微窗口被遮挡（其他全屏应用盖住了）→ 把企微窗口置顶
- DPI 不对 → 检查 1.1 的 DPI 设置
- 输入法干扰 → 切换到英文输入法

### 6.5 漏抓率高的常见原因

| 原因 | 排查 |
|------|------|
| 拉取频率被限速（45009） | 客户端日志搜 `45009` |
| RSA 解密失败 | 客户端日志搜 `解密失败`，确认私钥 |
| 网络抖动 | 客户端日志搜 `拉取超时` |
| 服务端白名单误过滤 | 后端审计表搜 `monitor_whitelist_filtered` |

### 6.6 客户端启动崩溃

最常见的：
- `client_secret` 不对 → 重新去平台后台注册
- `archive_private_key.pem` 路径错 → 用绝对路径试
- 端口被占用 → 后端可能跑在 8000，客户端连不上

---

## 7. 测试反馈格式

测完后请把以下信息发给我（Claude）：

1. **第 5 节的测试结果汇总表**（直接截图或文字）
2. **遇到的所有问题**（按 6.x 章节对应）
3. **客户端日志关键片段**（特别是有 Error / Warning 的行）
4. **任何"没在文档里覆盖"的现象**

我会根据反馈：
- 修代码 bug（如果是 Phase 1-4 的实现问题）
- 调整真机配置参数（如坐标校准）
- 补充文档（如果是文档没说清楚）

---

## 8. 时间预算总览

| 阶段 | 时间 |
|------|------|
| 1. 测试前准备 | 30 分钟 |
| 2. 客户端配置 | 10 分钟 |
| 3. 启动客户端 | 5 分钟 |
| 4.1 F2/F3 搜索发送 | 10 分钟 |
| 4.2 F4/F6 监听 + 白名单 | 60 分钟（连续 1 小时） |
| 4.3 F7 二维码 | 10 分钟 |
| 4.4 异常场景 | 30 分钟 |
| 4.5 完整链路 | 10 分钟 |
| 4.6 媒体附件 | 10 分钟 |
| 5. 结果汇总 | 10 分钟 |
| **总计** | **约 3 小时（含 1 小时连续监听）** |

可以分两次测：上午测 4.1/4.3/4.4/4.5/4.6（约 1.5 小时），下午专门测 4.2（1 小时连续监听）。

---

## 9. 重要提醒

1. **会话存档凭证要提前准备**（1.3 步），这是最容易卡住的点
2. **测试期间不要锁屏**（除非测 4.4.1 锁屏场景）
3. **测试期间不要最小化企微**（PS 操作需要窗口可见）
4. **不要在测试机器上做其他重操作**（CPU/磁盘占用会影响 PS 调用稳定性）
5. **遇到误发立即停服**（消息发错人比漏抓严重得多）
6. **每次测前先备份数据库**（出问题能回滚）：
   ```bash
   pg_dump -U postgres aid_work_agent > backup_before_phase5.sql
   ```

祝测试顺利。
