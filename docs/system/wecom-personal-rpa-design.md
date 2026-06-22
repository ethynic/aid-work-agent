# 企业微信个人账号 RPA 接入设计

> 关联调研：[wecom-personal-account-rpa-research.md](../research/wecom-personal-account-rpa-research.md)
> 开发计划：[plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)
> 创建日期：2026-06-16
> 状态：📋 待开发

---

## 0. 设计目标与范围

### 0.1 目标

让 aid-work-agent 通过 **PC RPA**（Windows UI 自动化）低成本接管"真实的个人企业微信账号"，实现：

- 接收账号收到的消息（单聊、内部群、外部联系人群、外部联系人单聊）
- 以该账号身份发送消息（Phase 1 先支持文本，Phase 2 再扩展图片、文件、@提醒）
- 切换会话、识别会话上下文、支持人工暂停 / 恢复托管
- 一个企业微信账号绑定一台低成本 Windows PC / Windows 虚拟机 / Windows 云桌面实例
- 员工本人保留手机端登录能力，可通过显式操作让 agent 暂停

### 0.2 范围

采用 **PC RPA 优先** 的路线。安卓 RPA 不作为第一阶段主路径，仅作为 PC 无法覆盖时的虚拟机备选路线或可靠性对照。

| 优先级 | 路线 | 适用账号 | 执行体 | 定位 |
|------|------|---------|--------|------|
| P0 | **PC RPA** | 员工本人账号、低频客服账号 | 低成本 Windows PC / Windows VM / 云桌面 | 主路径 |
| P1 | **会话存档 SDK** | 已开通存档的企业账号 | 后端服务 | 只读对账兜底 |
| P2 | **安卓 RPA** | PC RPA 不稳定的公用账号 / 关键账号 | Android 虚拟机 / 模拟器 | 备选路径 |

> **不在范围内**：Hook / 协议逆向 / iPad 协议 / mmtls MITM（调研报告已论证合规与稳定性不可接受）。
> **Phase 1 不做**：自动通过好友申请、批量群发、朋友圈、点赞评论、跨平台转发、多开企业微信。

### 0.3 决策依据（来自对齐）

1. **成本优先**：云手机 / 真机按账号长期付费，规模化成本高；低成本 PC / VM 与 Android 虚拟机可复用现有硬件或一次性投入。
2. **员工账号体验**：PC 端登录不影响员工手机端登录，员工可以保留日常移动端使用习惯。
3. **隔离原则**：一个企业微信账号绑定一个 Windows 执行环境，不做 PC 多开，不跨账号复用同一桌面会话。
4. **PC 技术栈**：`uiautomation + pyautogui + pywin32 + OpenCV`，控件树抓取失败时降级到窗口相对坐标 + 模板匹配。
5. **可靠性边界**：PC RPA 受 UI 改版、桌面会话、分辨率、远控检测影响；Phase 1 只承诺可测量的低频收发，不承诺零漏抓。
6. **会话存档 SDK**：作为 Phase 2 的消息对账兜底；未接入前，漏抓率只能通过人工抽样核对统计。

---

## 1. 顶层架构

### 1.1 总体拓扑

```
┌──────────────────────────────────────────────────────────────────┐
│  aid-work-agent 主服务（Linux）                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ master_agent (src/core/agent.py)                           │  │
│  └────────────┬───────────────────────────────────────────────┘  │
│               │                                                   │
│  ┌────────────▼───────────────────────────────────────────────┐  │
│  │ WecomPersonalChannel (src/channels/wecom_personal/)         │  │
│  │  - 路由：binding_id / stable_id → session_id                │  │
│  │  - 上行：HTTP Webhook 接收设备回调                          │  │
│  │  - 下行：HTTP 调用调度平台                                  │  │
│  └────────────┬───────────────────────────────────────────────┘  │
└───────────────┼──────────────────────────────────────────────────┘
                │ HTTPS（公网）
                │
┌───────────────▼──────────────────────────────────────────────────┐
│  调度平台（公网 VPS / 内网服务器，独立部署）                        │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Dispatcher Service（FastAPI + Redis Stream）                │  │
│  │  - 账号 ↔ 设备绑定表（accounts）                            │  │
│  │  - 任务队列：发送/查询/暂停/恢复                             │  │
│  │  - 设备心跳 & 任务回执                                      │  │
│  │  - Webhook 转发到 aid-work-agent                            │  │
│  └──────┬────────────────────────────────────────────────────────┘  │
└─────────┼───────────────────────────────────────────────────────────┘
          │ WebSocket / HTTPS（设备主动连出）
          │
┌─────────▼────────────────────────────────────────────────────────┐
│  Windows 执行环境池（一个账号一个实例）                            │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 低成本 PC / Windows VM / Windows 云桌面                     │  │
│  │  - 独立 Windows 用户会话                                    │  │
│  │  - 固定分辨率 1920x1080，DPI 100%                           │  │
│  │  - 企业微信 PC 客户端                                       │  │
│  │  - Worker Agent（Python）                                   │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 为什么 PC-first（而非 Android 虚拟机优先）

| 维度 | PC RPA 优先 | Android 虚拟机备选 |
|------|-------------|----------------|
| 账号成本 | 可用闲置 PC、低配迷你主机或低价 Windows VM，适合大量账号 | 可复用宿主机资源，无云手机年费 |
| 登录形态 | PC 端登录 + 员工手机端保留 | 安卓端被虚拟设备占用，不适合员工日常移动端共用 |
| 自动化稳定性 | 受桌面会话、分辨率、UI 改版影响 | 无障碍 API 更直接，但虚拟机设备指纹风险更高 |
| 运维复杂度 | 需要管理 Windows 桌面会话和远程桌面断连问题 | 需要管理模拟器镜像、无障碍权限、App 保活 |
| 扩容方式 | 一账号一 Windows 实例，硬件可一次性投入 | 一账号一 Android 虚拟机实例，受宿主机资源限制 |

**取舍**：PC-first 是成本优先的工程选择，不是稳定性最优解。Android 虚拟机同样是成本优先的备选，不等同于真机稳定性。设计上必须承认 RPA 的可靠性边界：不承诺自动故障漂移，不承诺零漏抓，不用“拟人化操作”包装规避风控；所有账号必须可暂停、可审计、可人工恢复。

### 1.3 Windows 执行环境约束

PC RPA 必须运行在一个持续可见、尺寸稳定的 Windows 桌面会话中。低成本 PC、虚拟机、云桌面都可以，但必须满足：

| 约束 | 要求 | 原因 |
|------|------|------|
| 账号隔离 | 一个企微账号绑定一个 Windows 用户 / VM / 物理机 | 避免 PC 多开和账号串扰 |
| 桌面会话 | 禁止锁屏后执行；远程桌面断开后不能让 UI 会话消失 | UI 自动化依赖真实桌面 |
| 分辨率 | 固定 1920x1080，DPI 100%，企业微信窗口固定尺寸 | 坐标和模板匹配稳定前提 |
| 自动更新 | 固定企业微信 PC 版本，先验证再升级 | 降低 UI 改版导致的脚本失效 |
| 输入权限 | Worker 独占鼠标键盘，不允许人工同时远控同一实例 | 避免任务执行中被打断 |
| 网络出口 | 固定公网出口或固定办公网出口 | 降低多地登录和异常网络风险 |
| 恢复方式 | 设备故障后暂停账号，人工重新登录备用实例 | 企业微信登录态不能假设可自动迁移 |

---

## 2. PC RPA 路线（员工个人账号）

### 2.1 技术栈

| 组件 | 选型 | 用途 |
|------|------|------|
| 控件树抓取 | `uiautomation`（Python） | 优先抓企微 PC 客户端的 UIA 节点 |
| 鼠标键盘模拟 | `pyautogui` | uiautomation 抓不到时降级 |
| 图像识别兜底 | `opencv-python` 模板匹配 + `pillow` 截图 | 极端场景（自绘按钮、图标按钮） |
| 窗口管理 | `pywin32`（win32gui） | 找窗口、置顶、恢复焦点 |
| 进程托管 | `subprocess` + Windows Service / NSSM | 启动 / 监控 / 重启企微客户端 |
| 长连接 | `websockets` / `httpx` | 与调度平台通信 |
| 屏幕分辨率 | 固定 1920×1080，禁用 DPI 缩放 | 坐标方案稳定前提 |
| 桌面保持 | 常驻本地控制台会话 / VNC / Sunshine 等可保持桌面的远控方案 | 避免 RDP 断开导致 UI 不可见 |

### 2.2 企微 4.1+ DirectUI 难点的对策

调研报告 §3.2 已指出企微 4.1+ 采用 DirectUI 自绘 + On-Demand UI Automation，标准 UIA 抓不到完整树。对策：

1. **三层降级策略**（核心机制）：

   ```
   Layer 1: uiautomation 抓控件（resource-id / automation-id）
       ↓ 失败（返回 None 或控件树为空）
   Layer 2: 坐标点击 + 剪贴板读写（基于窗口相对坐标）
       ↓ 失败（窗口位置漂移）
   Layer 3: OpenCV 模板匹配（截图 → 匹配按钮模板 → 计算坐标 → 点击）
   ```

2. **窗口锚定**：所有相对坐标基于企微主窗口的 `(left, top)`，不使用绝对屏幕坐标。窗口移动 / 缩放自动跟随。

3. **节点配置外置**：所有 resource-id / xpath / 模板图片路径 / 相对坐标写到 `worker/assets/wecom_nodes.yaml`，企微升级只改配置不改代码。

   ```yaml
   # worker/assets/wecom_nodes.yaml 示例
   main_window:
     class_name: "WeWorkWindow"
     title_contains: "企业微信"
   search_button:
     strategy: uiautomation
     automation_id: "search_entry"
     fallback:
       strategy: template_match
       template: assets/templates/search_icon.png
       region: [60, 40, 120, 80]  # 相对主窗口的搜索区域
   message_input:
     strategy: uiautomation
     control_type: EditControl
     class_name: "Edit"
     fallback:
       strategy: coordinate
       offset: [400, 580]  # 相对主窗口左上角
   send_button:
     strategy: template_match
     template: assets/templates/send_btn.png
   ```

4. **企微版本锁定**：通过 MDM 或注册表禁用自动更新，固定到验证过的版本（建议 4.1.38 或 4.1.x 稳定分支）。

5. **CI 兼容性回归**：企微 beta 版发布后，自动跑冒烟测试（发文本 / 收消息 / 发图 / 切群），失败立即告警。

### 2.3 Worker Agent（PC 端执行体）

每个 Windows 工作机部署一个 `worker_agent.py`，职责：

| 职责 | 说明 |
|------|------|
| 维持与调度平台的长连接 | WebSocket，自动重连，心跳 30s |
| 接收任务并执行 | Phase 1 任务类型：send_message / switch_chat / query_messages / pause / resume |
| 监听企微消息 | 通过 uiautomation 轮询消息列表节点（5-10s 间隔），或监听系统通知（Notification） |
| 上报消息事件 | 收到新消息 → 推送到调度平台 → 转发到 aid-work-agent |
| 监控企微进程 | 进程崩溃 / 卡死 → 自动重启 → 上报 |
| 账号登录态检查 | 离线 / 弹出重新登录 → 上报警告，暂停任务 |
| 桌面健康检查 | 检测分辨率、DPI、窗口位置、锁屏状态，异常时暂停任务 |

```python
# worker/worker_agent.py 核心结构（伪代码）
class WorkerAgent:
    def __init__(self, dispatcher_url: str, device_id: str, account_id: str):
        self.dispatcher = DispatcherClient(dispatcher_url, device_id)
        self.account_id = account_id
        self.wecom = WecomController()  # 封装 uiautomation + pyautogui + cv
        self.message_listener = MessageListener(self.wecom, self._on_message)

    async def run(self):
        await self.dispatcher.connect()
        await self.message_listener.start()
        while True:
            task = await self.dispatcher.fetch_task()
            await self._execute_task(task)

    async def _execute_task(self, task: dict):
        handler = {
            "send_message": self._handle_send_message,
            "switch_chat": self._handle_switch_chat,
            "query_messages": self._handle_query_messages,
            "pause": self._handle_pause,
            "resume": self._handle_resume,
        }[task["type"]]
        try:
            result = await handler(task["payload"])
            await self.dispatcher.report_task_result(task["id"], success=True, data=result)
        except Exception as e:
            logger.exception(f"任务执行失败: {task}")
            await self.dispatcher.report_task_result(task["id"], success=False, error=str(e))
```

### 2.4 关键操作的实现

#### 2.4.1 监听新消息

- **方案 A（推荐）**：监听 Windows 通知或企微任务栏角标未读数变化，触发后切换到对应会话，抓取最后 N 条消息。
- **方案 B（兜底）**：每 5s 轮询"消息"页面的会话列表，对比上次快照检测新增。
- **方案 C（Phase 2）**：接入会话存档对账，用官方只读消息源校正 RPA 漏抓和误抓。

Phase 1 不声称“无漏抓”。验收时采用人工抽样核对：每天抽取固定时段的真实聊天记录，与 RPA 上报消息对账，统计漏抓率和误抓率。

#### 2.4.2 切换到指定会话

```
1. 找到企微主窗口并置顶
2. 点击【搜索】入口（uiautomation 优先，失败降级模板匹配）
3. 剪贴板写入绑定表中的 `search_key`（优先备注名 / 群名 / 企业微信可搜索标识），Ctrl+V 粘贴
4. 等待 500-1500ms 随机延时
5. 按 Enter 或点击第一条结果
6. 等待会话窗口加载完成（监听消息列表节点出现）
```

#### 2.4.3 发送文本

```
1. 切换到目标会话（见 2.4.2）
2. 找到输入框（EditControl）
3. 剪贴板写入文本 → Ctrl+V 粘贴
4. 随机延时 200-800ms
5. 点击【发送】按钮或按 Enter
```

**为什么使用剪贴板而不是直接设置控件值**：企业微信 PC 端输入框并不总是暴露标准 UIA 写入接口；剪贴板方案在 DirectUI 场景下更可控，但需要在 Worker 执行期间独占剪贴板。

#### 2.4.4 发送图片 / 文件

```
1. 切换到目标会话
2. 打开文件传输对话框（点击 + 或拖拽）
3. 剪贴板写入文件绝对路径 → 粘贴
4. 等待文件上传完成（监听发送按钮重新可用）
5. 点击发送
```

#### 2.4.5 限速与误操作控制

限速的目标是降低误操作、降低平台异常行为风险，并给员工人工接管留出空间：

- 每步操作之间保留 200-2000ms 抖动延时，避免 UI 未加载完成时连续操作
- 单账号单日发消息 < 100 条
- 单账号单分钟发消息 < 5 条
- 单会话连续失败 2 次后暂停该会话任务，等待人工确认
- 深夜 / 凌晨默认不主动发送，仅处理白名单会话或人工触发任务

### 2.5 会话绑定与搜索键

PC RPA 不应假设总能拿到企业微信内部的 `external_userid`。在 UI 自动化路径下，稳定可见的信息通常是展示名、备注名、群名、头像、最近消息和时间。因此需要维护一张显式绑定表：

```sql
CREATE TABLE conversation_bindings (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    conversation_type TEXT NOT NULL,  -- external_user | internal_user | room
    display_name TEXT NOT NULL,
    search_key TEXT NOT NULL,
    stable_id TEXT,                   -- 可选：external_userid / userid / room_id
    last_verified_at TIMESTAMP,
    status TEXT DEFAULT 'active'
);
```

规则：

- `stable_id` 可用时进入 session_id；不可用时使用 `account_id + binding_id`，避免重名串话。
- 搜索结果不唯一时，Worker 不自动选择第一条，转为 `needs_binding_review`，由控制台人工确认。
- 群名、备注名变更后，Worker 标记绑定失效，不继续自动发送。

### 2.6 人工接管与暂停

PC 端无法可靠感知员工是否正在手机端输入，因此不把“手机端接管检测”作为强依赖。采用显式接管：

| 场景 | 机制 |
|------|------|
| 员工临时接管 | 控制台 / 内部指令将账号设置为 `paused_by_user` |
| 法务或运营暂停 | 管理员一键暂停账号或租户 |
| Worker 检测异常 | 登录态异常、桌面锁屏、搜索结果歧义、发送失败时自动暂停 |
| 恢复托管 | 员工或管理员显式恢复，Worker 先做健康检查再继续 |

---

## 3. 安卓 RPA 路线（备选）

安卓 RPA 不作为本设计的第一优先级。保留该路线是为了覆盖两类场景：

- PC RPA 对某个关键账号稳定性不足，但仍希望复用自有服务器 / PC 资源。
- 公司公用账号不需要员工手机端使用，可以独占一个 Android 虚拟机实例。

本方案不规划云手机和专用真机路线。安卓备选只考虑 Android 虚拟机 / 模拟器，因此必须接受设备指纹、App 兼容性、虚拟化图形性能带来的额外风险。

### 3.1 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 自动化引擎 | **WorkTool APP**（[gallonyin/worktool](https://github.com/gallonyin/worktool)，Apache 2.0） | 直接 fork 或部署官方打包版，已封装无障碍 + HTTP API |
| 设备形态 | Android 虚拟机 / 模拟器实例 | 一账号一实例，禁止同实例多开 |
| 系统 | Android 9-13（不要 14+） | 以 WorkTool 兼容版本为准，需在目标虚拟机镜像上实测 |
| 调试工具 | `uiautomator2`（Python）+ `scrcpy` / 模拟器控制台 | dump UI 树定位节点变化 |
| 长连接 | WorkTool 内置 MQTT | 调度平台 → 设备 |
| 企微 App | 锁定到 WorkTool 兼容版本 | 通过镜像和应用市场策略禁用自动更新 |

### 3.2 何时启用 WorkTool

| 维度 | 自研无障碍 | 用 WorkTool |
|------|-----------|------------|
| 开发周期 | 2-3 个月 | 1-2 周（部署 + 二开） |
| 已实现能力 | 0 | 消息收发、群管理、@、文件等 |
| 兼容性维护 | 自行跟进企微升级 | WorkTool 团队跟进 |
| 风险 | 高 | 中（依赖上游维护节奏） |

**结论**：仅在 PC RPA 无法满足关键账号稳定性且 Android 虚拟机实测可用时，fork WorkTool 作为基础，二开只做两件事：
1. 替换 WorkTool 默认的回调地址为我们的调度平台
2. 加入账号心跳、登录态检测、断网重连

### 3.3 WorkTool 对接

WorkTool 提供 HTTP API（[官方文档](https://worktool.apifox.cn/doc-850007)），核心接口：

| 接口 | 用途 |
|------|------|
| `/sendTextMsg` | 发送文本消息 |
| `/sendImageMsg` | 发送图片 |
| `/sendFileMsg` | 发送文件 |
| `/sendGroupMsg` | 群消息 + @ |
| `/acceptFriend` | 通过好友申请（本方案 Phase 1 不启用） |
| Webhook 回调 | 收到消息主动推送 |

调度平台作为中间层：

```
aid-work-agent
    ↓ HTTP
调度平台（适配 WorkTool 协议）
    ↓ HTTPS（WorkTool 后台 API）
WorkTool 服务端
    ↓ MQTT
WorkTool APP（Android 虚拟机 / 模拟器）
    ↓ 无障碍
企业微信 App
```

---

## 4. 调度平台（Dispatcher）

调度平台是整个系统的"大脑"，独立部署在公网 VPS，承担：

### 4.1 核心职责

1. **账号 ↔ 设备绑定**：表 `accounts`
   ```sql
   CREATE TABLE accounts (
       id TEXT PRIMARY KEY,              -- 账号唯一 ID
       display_name TEXT,                -- 显示名（员工名 / 客服号名）
       route_type TEXT NOT NULL,         -- 'pc_rpa' | 'android_rpa'
       device_id TEXT NOT NULL,          -- 绑定的 Windows 实例 / Android 虚拟机 ID
       corp_id TEXT,                     -- 企业 ID
       userid TEXT,                      -- 企微 userid
       status TEXT DEFAULT 'offline',    -- online/offline/error
       tenant_id TEXT,                   -- 所属租户（与 aid-work-agent 对齐）
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   );
   ```

2. **任务队列**：表 `tasks` + Redis Stream
   ```sql
   CREATE TABLE tasks (
       id TEXT PRIMARY KEY,
       account_id TEXT NOT NULL,
       task_type TEXT NOT NULL,           -- send_message / switch_chat / ...
       payload JSONB NOT NULL,
       status TEXT DEFAULT 'pending',     -- pending/running/success/failed
       result JSONB,
       error TEXT,
       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
       finished_at TIMESTAMP,
       INDEX idx_account_status (account_id, status)
   );
   ```

3. **设备心跳与故障处理**：
   - 设备每 30s 上报心跳
   - 心跳超 90s 未到 → 标记 `device.status = 'offline'`，触发告警
   - 该设备的账号自动暂停任务，等待人工恢复；备用实例需要人工重新登录企业微信后才能接管

4. **消息中转**：
   - 设备/Worker 上报消息 → 调度平台落库（`messages` 表，仅做对账用，不长期存储原始内容）
   - 同时 Webhook 推送到 aid-work-agent

### 4.2 API 规范

调度平台对 aid-work-agent 暴露的接口：

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/accounts/{id}/send` | 下行消息（文本/图/文件/@） |
| POST | `/api/v1/accounts/{id}/switch` | 切换到指定会话 |
| POST | `/api/v1/accounts/{id}/query_messages` | 查询某会话最近 N 条消息 |
| POST | `/api/v1/accounts/{id}/pause` | 暂停账号托管 |
| POST | `/api/v1/accounts/{id}/resume` | 恢复账号托管 |
| GET  | `/api/v1/accounts/{id}/status` | 账号在线状态 |
| GET  | `/api/v1/devices/{id}/health` | 设备健康 |

调度平台对设备的接口（由设备主动 pull 或长连接 push）：

| 方法 | 路径 | 用途 |
|------|------|------|
| WS   | `/ws/worker/{device_id}` | Worker Agent 长连接 |
| WS   | `/ws/device/{device_id}` | 安卓设备 MQTT 桥接 |
| POST | `/callback/message` | 设备上报新消息 |
| POST | `/callback/task_result` | 设备上报任务结果 |
| POST | `/callback/heartbeat` | 设备心跳 |

### 4.3 多账号 / 多设备调度策略

| 场景 | 策略 |
|------|------|
| 一个账号绑一台设备 | 1:1 强绑定，禁止跨设备漂移 |
| 设备故障 | 该账号任务暂停，告警人工介入；可选启用备用设备 |
| 任务排队 | 同账号任务串行（避免并发触发企微风控），不同账号并行 |
| 限速 | 单账号每分钟任务数 ≤ 5，单日 ≤ 100（参考调研 §5.1） |

---

## 5. 渠道适配器（aid-work-agent 端）

### 5.1 目录结构

```
src/channels/wecom_personal/
├── __init__.py
├── adapter.py              # WecomPersonalChannel，实现 ChannelAdapter
├── dispatcher_client.py    # 调用调度平台的 HTTP 客户端
├── router.py               # external_userid → session_id 路由
├── callback.py             # FastAPI 路由，接收调度平台 Webhook
├── message.py              # 消息格式转换
└── config.py               # 渠道配置加载
```

### 5.2 WecomPersonalChannel 关键接口

```python
class WecomPersonalChannel(ChannelAdapter):
    """企业微信个人账号渠道（通过 RPA 接管）"""

    def __init__(self, dispatcher_url: str, callback_token: str):
        self.dispatcher = DispatcherClient(dispatcher_url)
        self.router = SessionRouter()
        self.callback_token = callback_token

    @property
    def channel_type(self) -> str:
        return "wecom_personal"

    async def parse_message(self, raw: dict) -> UnifiedMessage:
        """解析调度平台回调的消息"""
        # raw 来自 callback.py，字段：
        # {account_id, binding_id, stable_id, source_msg_key, sender_label, msg_type, content, timestamp}
        session_id = self.router.route(
            raw["account_id"],
            raw["binding_id"],
            raw.get("stable_id"),
        )
        return UnifiedMessage(
            session_id=session_id,
            channel_type="wecom_personal",
            user_id=raw.get("stable_id") or raw["binding_id"],
            content=raw["content"],
            msg_type=self._map_msg_type(raw["msg_type"]),
            tenant_id=raw.get("tenant_id"),
            metadata={
                "account_id": raw["account_id"],
                "binding_id": raw["binding_id"],
                "source_msg_key": raw["source_msg_key"],
            },
        )

    async def send_message(self, message: UnifiedResponse) -> bool:
        """通过调度平台下发到设备"""
        account_id = message.metadata["account_id"]
        payload = self._build_send_payload(message)
        result = await self.dispatcher.send(account_id, payload)
        return result["success"]

    async def get_user_info(self, user_id: str) -> dict:
        """查询单聊对方信息（可选，从调度平台拉取）"""
        ...
```

### 5.3 会话路由

```python
# src/channels/wecom_personal/router.py
class SessionRouter:
    def route(self, account_id: str, binding_id: str, stable_id: str | None = None) -> str:
        """PC RPA 优先使用人工确认过的绑定记录，stable_id 可用时作为补充。"""
        route_key = stable_id or binding_id
        return f"wecom_personal:{account_id}:{route_key}"
```

**关键**：`account_id` 和人工确认过的 `binding_id` 必须进入 session_id。PC RPA 不假设 UI 中总能拿到 `external_userid`，搜索结果重名或无法确认时必须暂停发送，转人工绑定。

### 5.4 回调签名

调度平台 → aid-work-agent 的 Webhook 用 HMAC-SHA256 签名：

```
X-WecomPersonal-Signature: t=<timestamp>,v1=<hmac_sha256(timestamp + body, secret)>
```

aid-work-agent 验签：
- `timestamp` 与当前时间相差 > 5 分钟 → 拒绝
- HMAC 不匹配 → 拒绝

### 5.5 配置加载

`configs/config.yaml`：

```yaml
channels:
  wecom_personal:
    enabled: true
    dispatcher_url: ${WECOM_PERSONAL_DISPATCHER_URL}
    callback_token: ${WECOM_PERSONAL_CALLBACK_TOKEN}
    sign_secret: ${WECOM_PERSONAL_SIGN_SECRET}
    # 可选：直接绑定到某些账号（多租户场景下，租户管理员通过 UI 配置）
```

---

## 6. 消息可靠性

UI 自动化天然会漏抓 / 重复 / 乱序，需要三重保障（调研报告 §4.5）：

### 6.1 幂等去重

```python
# PC RPA 不一定能拿到服务端 msgid，因此使用 Worker 生成的 source_msg_key。
dedup_key = f"msg:{account_id}:{binding_id}:{source_msg_key}"
if not redis_client.set(dedup_key, "1", nx=True, ex=86400):
    logger.warning(f"重复消息已忽略: {dedup_key}")
    return  # 已处理过
```

### 6.2 状态机对账

每条消息经历：`received` → `processing` → `replied`。

后台任务每 1 分钟扫描：`received` 超 5 分钟仍未 `replied` 的消息 → 触发补偿重试。

### 6.3 会话存档兜底（Phase 2）

接入企微官方会话存档 SDK（[文档](https://developer.work.weixin.qq.com/document/path/91774)）：

- 每 10 分钟拉取会话存档（限频 4000 次/分钟，单次最多 1000 条）
- 与 RPA 抓取的消息做差集
- 补齐 RPA 漏抓的消息
- 同时清理 RPA 误抓的"幻觉消息"（如 UI 解析错误产生的虚假消息）

**Phase 1 暂不接入**，因此 Phase 1 的漏抓率只能通过人工抽样核对获得；状态机只能兜住“已抓到但未处理”的消息，不能发现“根本没抓到”的消息。

---

## 7. 防封策略

### 7.1 平台风险与节流策略

| 检测层 | 检测点 | 对策 |
|--------|--------|------|
| 行为层 | 操作频率、固定间隔、瞬间粘贴 | 限速、错峰、失败暂停、人工确认 |
| 客户端完整性 | 进程被 Hook、内存被改 | ✅ RPA 方案天然规避（不修改 App、不注入） |
| 网络层 | 异常 IP、多地登录、设备指纹 | 固定 Windows 实例 + 固定出口 IP |

PC RPA 仍可能违反平台对自动化工具的限制。本文档只把 RPA 作为低侵入工程实现，不把任何节流策略表述为“绕过检测”。

### 7.2 绝对禁止操作

- 批量主动加好友（>20 人/天必封）
- 群发广告、刷屏
- 朋友圈定时群发外挂
- 自动点赞、评论
- 在被接管账号上做任何"营销动作"

### 7.3 员工个人账号的特殊风险

由于 PC RPA 在云端工作机跑，员工本人手机端可能同时操作 → 同账号双端活跃。对策：

- **任务时间窗**：默认仅工作日 9:00-22:00 接管，员工休息时间交给手机端
- **显式暂停**：员工或管理员通过控制台 / 内部指令暂停托管；PC 端不假设能可靠检测手机端正在输入
- **异常让位**：搜索结果歧义、连续发送失败、登录态异常时 Worker 自动暂停，等待人工确认
- **明确告知对方**：建议自动回复首条消息加一句"我是 AI 助手，由 [员工名] 授权"

### 7.4 封号应急

- 监控到账号被限制（消息发送失败提示"账号异常"）→ 立即停止所有任务
- 通过调度平台控制台一键暂停某账号 / 全部账号
- 启动申诉流程（企微管理后台 → 安全中心）

---

## 8. 合规与法律

### 8.1 风险等级（来自调研报告 §6.1）

| 维度 | PC RPA | 安卓 RPA |
|------|--------|---------|
| 用户协议 | 🟡 中（模拟键鼠仍有灰色空间） | 🟡 中（无障碍是工信部明文支持） |
| 刑法 285 条 | 🟢 低（不注入不改客户端） | 🟢 低 |
| 个人信息保护法 | 🔴 高（读取员工所有聊天） | 🔴 高 |
| 员工内部合规 | 🔴 高（员工需授权） | 🟢 低（公用账号无员工权益问题） |

### 8.2 必须做的合规动作

1. **员工授权书**（仅 PC RPA 路线）：被接管员工签署书面知情同意书，明确告知
   - AI 将接管 PC 端账号
   - 所有收发消息将被处理
   - 员工可随时撤销授权（撤销后立即停用）
   - 员工手机端可随时人工接管
2. **数据最小化**：只把必要字段（发送人、内容）传给 agent，原始聊天记录不长期存储
3. **告知对话方**：自动回复首条消息声明 AI 身份
4. **审计日志**：所有 RPA 操作落库（谁授权、何时、操作了什么），保留 ≥ 6 个月
5. **法务审查**：上线前由法务审查用户协议、隐私政策

---

## 9. 监控与运维

### 9.1 关键指标

| 指标 | 告警阈值 | 含义 |
|------|---------|------|
| `account_online_ratio` | < 95% | 账号在线率 |
| `task_success_rate` | < 95% | 任务成功率 |
| `task_p95_latency` | > 30s | 任务端到端时延 P95 |
| `message_drop_rate` | > 1% | 消息漏抓率（Phase 2 与存档对账后可测） |
| `worker_restart_count` | > 3 次/天 | Worker 重启次数（企微崩溃或脚本异常） |
| `wecom_alert_count` | > 0 | 企微风控告警数 |

### 9.2 运维工具

- **scrcpy**（安卓）：实时查看 Android 虚拟机画面，排查无障碍操作问题
- **uiautomator2 dump**（安卓）：dump UI 树，定位节点变化
- **VNC / 远程桌面**（PC）：远程登录工作机，排查 RPA 操作问题
- **Inspect / FlaUI Inspector**（PC）：检查企微 PC 客户端的 UIA 树
- **调度平台控制台**：账号管理、任务查询、一键暂停、日志查看

### 9.3 企微版本升级应对

1. **锁定版本**：禁用自动更新（PC：注册表 / 安卓：虚拟机镜像和应用市场策略）
2. **订阅企微发布说明**：每次企微更新，评估是否影响节点配置
3. **CI 兼容性回归**：企微 beta 版本在测试环境跑冒烟测试
4. **节点配置外置**：UI 变化只改 YAML，不改代码
5. **WorkTool 订阅**：安卓路线跟进 WorkTool 适配版本

---

## 10. 落地分阶段

### Phase 1：PC RPA 可行性验证（1-2 周）

- 目标：验证一台低成本 Windows 执行环境能稳定托管 1 个企业微信账号的低频文本收发。
- 范围：
  - 1 台低成本 PC / Windows VM，1 个测试账号
  - 固定分辨率、DPI、企微版本、窗口位置，完成执行环境基线文档
  - Worker Agent 实现桌面健康检查、消息监听、会话切换、文本发送、暂停 / 恢复
  - 简化版调度平台（单进程，不集群）
  - WecomPersonalChannel adapter 骨架
  - 人工抽样核对漏抓率和误抓率
- 验收：连续运行 7 天，无账号限制；任务成功率 > 90%；人工抽样漏抓率 < 5%；出现桌面 / 登录异常时能自动暂停而不是继续误发。

### Phase 2：PC RPA 灰度生产化（2-3 周）

- 目标：3-5 个内部账号灰度，验证低成本 PC / VM 池的运维模型。
- 范围：
  - 调度平台完整化：多账号、设备心跳、账号暂停、限速、监控
  - 会话绑定控制台：绑定搜索键、处理重名、处理绑定失效
  - WecomPersonalChannel 完整化：图片 / 文件 / @提醒按需扩展
  - 接入企微官方会话存档做对账兜底（如企业已开通）
  - 监控告警接入现有可观测性体系（参见 [observability-design.md](infrastructure/observability-design.md)）
  - 法务审查、员工授权书模板、对话方告知策略
- 验收：3-5 个内部账号灰度 2-4 周稳定；任务成功率 > 95%；会话存档可用时漏抓率 < 1%；所有暂停 / 恢复 / 审计链路可查。

### Phase 3：扩展与备选路线（1-2 周）

- 目标：扩大 PC RPA 账号规模，并明确何时切换 Android 虚拟机备选。
- 范围：
  - 多租户：租户管理员通过 UI 绑定账号、配置授权
  - 智能限速：根据账号行为画像动态调整阈值
  - 失败重试与补偿队列
  - 低成本硬件 / VM 镜像标准化
  - 对关键账号评估 Android 虚拟机 RPA 备选成本和收益
- 验收：PC RPA SLO 达标；不可稳定托管的关键账号有明确回退到 Android 虚拟机 RPA 或官方只读方案的决策记录。

> 详细任务拆解见 [plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)。

---

## 11. 关键风险与决策记录

| # | 决策 | 理由 | 风险 |
|---|------|------|------|
| 1 | 走 RPA 不走 Hook | 合规与法律风险 | RPA 稳定性受 UI 改版影响 |
| 2 | PC RPA 作为第一优先级 | 低成本 PC / VM 可降低长期账号托管成本 | 稳定性弱于安卓无障碍，需要更严格运维约束 |
| 3 | 安卓路线降级为 Android 虚拟机备选 | 云手机 / 真机不符合成本约束 | 虚拟机设备指纹和 App 兼容性风险更高 |
| 4 | Phase 1 不接入会话存档 | 快速验证 PC RPA 成本模型 | 漏抓只能人工抽样核对，不能自动证明零漏抓 |
| 5 | PC 技术栈 uiautomation + pyautogui | 合规与社区参考 | 企微 4.1+ DirectUI 难点，靠三层降级 + 节点配置外置 |
| 6 | 账号 ↔ Windows 执行环境 1:1 强绑定 | 防账号串扰，避免 PC 多开 | 设备故障需人工重新登录备用实例 |

---

## 12. 不在范围内（明确排除）

- ❌ Hook / DLL 注入（法律风险大）
- ❌ iPad 协议 / Cookie 模拟（功能受限且仍属协议逆向）
- ❌ mmtls MITM（技术不可行且违法）
- ❌ PC 上多开企业微信（官方明文违规）
- ❌ 朋友圈、点赞、加好友等营销动作（封号风险极高）
- ❌ 跨平台消息互通（如把企微消息转到微信，违反两方协议）
