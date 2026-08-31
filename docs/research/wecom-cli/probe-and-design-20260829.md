# 企业微信 CLI（wecom-cli）真机探测与方案设计

> 日期：2026-08-29
> 目的：参考 `clients/weixin-cli`（微信 CLI）的成熟操作模式和 `clients/wecom-personal-rpa`（企业微信 RPA）的实现，设计一个新的企业微信 CLI。
> 探测环境：本机 Windows 桌面，企业微信 5.0.9.6060（`C:\Program Files (x86)\WXWork\WXWork.exe`），账号 WayneLu 已登录。探测脚本在 `.tmp/wecom-probe/`。

---

## 1. 输入资产盘点

### 1.1 weixin-cli（主要参考对象，操作模式来源）

- **三层架构**：TypeScript 控制面（operation 注册表 / CLI 动词 / MCP server）→ PowerShell 驱动层（`drivers/ps1/*.ps1`，内嵌 C# P/Invoke）→ Win32 原语（PostMessage / PrintWindow / AttachThreadInput）+ 视觉模型（GLM/kimi）/ RapidOCR 定位。
- **统一契约**：operation 永不抛异常，返回 `{success, code, message, effect, data, retryable, run_id}`；PS stdout 最后一行 `DRIVER_JSON: {...}` 为进程边界协议；写动作 `effect=unknown` 时**绝不自动重试**。
- **定位哲学**：Qt 自绘窗口 UIA 树无业务语义，**不解析控件树**；固定相对坐标 + 截图喂视觉模型返回 JSON 坐标 + OCR 兜底，操作后**再截图校验终态**（fail-closed）。
- **输入原语**：PostMessage 点击（WM_MOUSEMOVE/LBUTTONDOWN/UP）、WM_CHAR 逐字输入（中文 OK）、WM_KEYDOWN Enter 发送、PrintWindow(PW_RENDERFULLCONTENT) 截图（遮挡/非前台可用）。
- **安全**：写操作必须持短期签名 `target_ref`（HMAC，5 分钟 TTL），防止 agent 拿模糊名字乱发；三层单飞互斥（进程内标志 + Windows 命名管道 + 驱动层命名 Mutex）。
- **缺口**：weixin-cli **没有新消息跟踪/水位机制**（`unread_list` 只是快照式 OCR 轮询，去重靠调用方）——这正是 wecom-cli 要新设计的部分。

### 1.2 wecom-personal-rpa（被取代对象，复杂度教训）

- C# 5 工程 + WPF 托盘 + Supervisor 服务 + WebSocket/HMAC/outbox/SQLite 队列，约 5600 行 C#，但**真正操作企微的只有 ~770 行 PowerShell**（`scripts/wecom-ops.ps1` + `wecom-ops-lib.ps1`）：窗口发现（class `WeWorkWindow`）、双 Alt 聚焦搜索框、剪贴板粘贴发文本/图片/文件、登录态检测。
- 复杂来源：多租户协议、离线上报、8 态状态机、健康检查——对单机 CLI 全部可砍。
- **已验证失败的路线（勿再试）**：FlaUI/UIA3（企微 UIA dump 0 控件）、整窗 OCR 读消息（中文识别率 <10%）、Windows Toast 监听（企微私有弹窗不走系统通知）、UIA 事件（被吞）、读进程内存/本地 DB（加密私有格式）。
- **客户端入站已废弃**：当前架构会话归档拉取在**服务端**（`src/channels/wecom_personal_rpa/archive/`，multiprocessing 子进程加载 WeWorkFinanceSdk C SDK，回调触发 + 60s 兜底轮询），客户端只做出站执行器。

### 1.3 会话归档服务端集成（保留复用）

- 数据流：企微回调（XML，验签+AES）/ 60s 轮询 → `ServerArchiveFetcher.fetch_once`（Redis 分布式锁）→ `GetChatData` 拉密文 → RSA 解 random_key + SDK `DecryptData` → 方向判定（`direction.py`）→ 外部联系人姓名解析（`external_contact_resolver.py`，需独立 `external_contact_secret`，`externalcontact/get`，24h 缓存，48002 退避）→ PG inbox 去重 → `_process_inbound_message` 进 agent。
- 出站：agent → adapter → `action_client.deliver_actions` → PG outbox（唯一权威）→ 客户端轮询执行 → action_result 回执。
- 配置：`tenant_channel_configs.config`（corp_id / archive_secret / private_key / token / encoding_aes_key / external_contact_secret / listen_mode=server 强制 / last_seq 等），敏感字段 Fernet 加密。

---

## 2. 真机探测结论（2026-08-29，全部已实测）

### 2.1 窗口拓扑（与微信 4.x 不同，关键差异）

| 窗口 | class | 说明 |
|------|-------|------|
| 主外壳 | `WeWorkWindow`（标题「企业微信」） | 左侧导航栏 + 消息页的会话列表/聊天区直接绘在它上面 |
| 内容子窗口 | `WXworkWindow - 企业微信-<页名>` | **每个功能页一个独立子 HWND**（通讯录页实测），切换页面时 hide/show；**类名本身编码当前页名，可做状态校验** |
| 弹窗 | 独立顶层窗口，class 语义化 | 「添加客户」弹窗 = `SearchExternalsWnd`（400x292） |
| 其他 | `TitleBarWindow` / `PerryShadowWnd` / `SearchResultWindow2` 等 | 阴影/标题栏/搜索浮层 |

**影响**：PostMessage 点击必须投递到**拥有目标坐标的那个 HWND**（`WindowFromPoint` 可判定）。点导航栏投递给 `WeWorkWindow`，点通讯录内容区投递给子窗口 `WXworkWindow - 企业微信-通讯录`，点弹窗投递给弹窗 hwnd。hwnd 动态变化，每次操作前重新解析。

### 2.2 已验证可行

| 能力 | 方法 | 证据 |
|------|------|------|
| 截图（读状态） | `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)` | 前台/后台/被遮挡/**最小化**均出完整画面（9 点采样无黑块）→ **CLI 可完全后台运行，不需要窗口可见** |
| 点击（导航） | PostMessage WM_MOUSEMOVE+LBUTTONDOWN/UP 到 `WeWorkWindow` | 消息↔通讯录切换成功 |
| 点击（内容区/弹窗） | 同上，投递到对应子窗口/弹窗 hwnd | 打开「添加客户」弹窗成功；点弹窗 × 清空输入成功 |
| 文本输入（ASCII/数字） | PostMessage **WM_CHAR 逐字符**，或 **WM_KEYDOWN+KEYUP**（VkKeyScanW+MapVirtualKey 带 scan code） | 均成功。**两者不可同发（Qt 会把 keydown 也翻成字符 → 双倍字符）** |
| 弹窗枚举 | EnumWindows 按 class/title | 「添加客户」弹窗可被独立发现和截图 |
| 最小化后台 | ShowWindow(SW_MINIMIZE) 后 PrintWindow 仍出图 | 已验证 |

### 2.3 已验证不可行 / 注意事项

| 路线 | 结果 |
|------|------|
| UIA 控件树 | 主窗口 UIA 子树仅 Window+2 Pane，**零业务语义**（与 RPA 当年结论一致） |
| PostMessage Ctrl+V 粘贴 | Qt **从真实键盘状态读修饰键**，posted Ctrl 无效 → 输入框出现字面量 `v` |
| **SendInput/keybd_event 注入输入（键盘+鼠标）** | **企微 5.0.9 全部丢弃**（2026-08-30 复核覆盖早期误判：真实鼠标点击导航栏/弹窗按钮均无反应；GetGUIThreadInfo 证实焦点正确时注入键盘也不生效）→ **一切交互只走 PostMessage** |
| PostMessage 点击的延迟 | 点「添加」后 InputReasonWnd 有 5-8s 延迟才出现——不是无反应，等待窗口要放宽到 15s |

### 2.4 添加客户流程探测（核心新能力）——✅ 全流程已闭环验证（2026-08-29 探测 + 2026-08-30 CLI 端到端真机复验通过）

实测完整链路（测试号 13671705875 → 解析结果 WayneLu，申请已成功发出，终态「已发送申请」）：

1. 主窗口点「通讯录」（PostMessage 点击 `WeWorkWindow` 导航）→ 内容子窗口 `WXworkWindow - 企业微信-通讯录` 出现并自动落在「新的客户」。
2. 点子窗口右上角「⊕添加」（PostMessage 点击子窗口）→ 弹出 **`SearchExternalsWnd`「添加客户」对话框**（400x292，输入框占位符「手机号/邮箱」）。
3. 对话框内 PostMessage 点击输入框 + **纯 WM_KEYDOWN 逐字输入手机号**（文本正确显示）。
4. **检索触发 = 回车（已按用户描述复核确认）**：输入完成后不会自动检索。PostMessage 点击输入框建立焦点 + 纯 WM_KEYDOWN 输入后，**PostMessage WM_KEYDOWN Enter** 即可触发检索（无需任何真实点击；早期"需真实点击建焦点"的认知已随 SendInput 整体被否决而废弃）→ 结果行渲染：微信图标 + 头像 + **微信名称（WayneLu）** + 右侧「添加」按钮。结果区持续为空时每 ~2s 可安全重发 Enter（检索只读）。
5. 点「添加」（PostMessage 点击，**弹窗有 5-8s 延迟**，等待上限 15s）→ 弹出 **`InputReasonWnd`「发送添加邀请」对话框**（393x244，预填验证语「我是爱定义的陆伟…」可编辑，× 可清空）。
6. 点「发送」（PostMessage 点击有效）→ 邀请发出，`InputReasonWnd` ≤1s 关闭，`SearchExternalsWnd` 结果行按钮 **≤3s 变为「已发送申请」**（终态校验必须轮询，单次快照会落在刷新前——M1 真机复验时踩过）。

**关键约束（注入输入过滤）**：企业微信 5.0.9 **丢弃注入键盘事件**（SendInput/keybd_event 即使窗口持真实键盘焦点也不生效——GetGUIThreadInfo 证实焦点在对话框上但字符不进）——键盘只能走 PostMessage；鼠标两条路都通（PostMessage 大部分可用，个别按钮如「添加」需真实 SendInput 点击）。SendInput 鼠标会移动真实光标，用后需恢复原位。

**数据记录点**：第 4 步结果行的微信名称即「手机号 → 客户微信名」解析结果，截图 + OCR/视觉读出后与手机号一并记录。

---

## 3. wecom-cli 方案设计

### 3.1 定位与架构

新建 `clients/wecom-cli`，可执行名 `aid-wecom`。**整体骨架照抄 weixin-cli**（符合 #51 第一方 CLI/MCP Provider 规范）：

```
CLI 动词 (probe/search/send/read/watch/add-customer/doctor/version)
        │  统一 OperationResult 契约 + target_ref + 单飞互斥
MCP stdio server（agent 直接调用）
        │
operations/*.ts（业务能力层）
        │
drivers/ps1/*.ps1（DRIVER_JSON 协议）── Win32: EnumWindows/PrintWindow/PostMessage/AttachThreadInput
        │
视觉模型（GLM-5.3-Flash 主 / kimi 备）+ RapidOCR（未读角标、列表文字）
```

与旧 RPA 的关系：**wecom-cli 取代 `wecom-ops.ps1` + C# 客户端的 UI 执行职责**。服务端 `wecom_personal_rpa` channel 的 outbox/签名/会话归档全部不动，出站执行器从「C# 客户端轮询 + PS」换成「调用本机 aid-wecom CLI」（或 C# 客户端瘦身为只调 CLI 的壳，后续再决定是否淘汰 C#）。

### 3.2 命令面（第一期）

| 命令 | 说明 | 写动作 |
|------|------|--------|
| `probe` | 环境检查：进程/主窗口/登录态/页面状态（读内容子窗口类名） | 否 |
| `unread` | 截消息页会话列表，OCR 未读角标 → `{name, preview, unread_count}[]` 快照 | 否 |
| `read --target <ref>` | 进会话拉当前页/多页消息（滚动截图 + OCR + 页间重叠去重，移植 weixin-cli p4 算法） | 半（会清未读） |
| `search --query <名>` | 搜索联系人/会话，返回带 target_ref 的候选 | 否 |
| `send --target-ref <ref> --text <t>` | 校验会话标题 → WM_CHAR 输入 → Enter → **发送后截图校验最后一条气泡** | 是 |
| `add-customer --phone <号>` | 通讯录→新的客户→添加→弹窗输入→读解析出的微信名称→确认添加→记录 `{phone, wechat_name, added_at}` | 是 |
| `watch` | 新消息跟踪（见 3.3），NDJSON 事件输出 | 半 |

### 3.3 无会话归档时的新消息跟踪（`watch`，weixin-cli 没有的新设计）

```
loop（默认 5s 间隔，可配）:
  1. unread 快照（PrintWindow 不需要前台，不打断用户）
  2. 与本地状态（SQLite/JSONL，`%LOCALAPPDATA%\aid-wecom\watch-state.db`）diff：
     未读数增加或新出现的会话 → 候选
  3. 对每个候选会话：search+进入 → read 当前页 → 提取增量消息
     （水位 = 该会话最后一条已见消息的内容 hash + 时间戳；页内逐条比对）
  4. 发出 NDJSON 事件 {type:"new_messages", session, messages:[...]}，推进水位
```

- 去重/水位在 CLI 本地持久化，重启不丢、不重发。
- 「进入会话会清未读」是固有副作用：watch 模式下接受（读完即视为已处理）。
- agent 回复回路：调用方（MCP host / 服务端 channel）消费 NDJSON → agent 生成回复 → `send --target-ref`。与 wecom_kf 的 cursor 水位语义对齐。

### 3.4 有会话归档时

入站**不走 CLI**（服务端 `archive/` 栈已完整：SDK 拉取、解密、方向判定、外部联系人姓名解析、inbox 去重）。CLI 此时只承担：出站发送（替代 wecom-ops.ps1）、归档覆盖不到的能力（add-customer、watch 兜底）。两种模式的切换由服务端 channel 的 `listen_mode` 决定，CLI 本身无状态、无感知。

### 3.5 添加客户详细设计（步骤已按 2.4 实测修正）

1. `probe` 确认登录态 → 点「通讯录」（`WeWorkWindow` 导航固定相对坐标）→ 校验内容子窗口类名变为 `WXworkWindow - 企业微信-通讯录`（fail-closed）。
2. 确认落在「新的客户」（截图 OCR/视觉校验标题），点右上角「添加」（子窗口右上角固定相对坐标，PostMessage）。
3. EnumWindows 发现 `SearchExternalsWnd` 弹窗（超时 → `UI_CHANGED`）。
4. 弹窗内 PostMessage 点击输入框 → **纯 WM_KEYDOWN** 逐字输入手机号 → **PostMessage WM_KEYDOWN Enter 触发检索**（结果区持续为空时每 ~2s 重发 Enter，最多 5 次）→ 轮询截图等待结果行渲染（≤10s，超时 → `CUSTOMER_NOT_FOUND`，不重试）。
5. 截图 + 视觉模型/OCR 读出结果行：`{wechat_name}`（及头像特征）——**名称在此记录**。
6. **SendInput 真实点击**结果行「添加」→ 等待 `InputReasonWnd`「发送添加邀请」弹窗（可选：WM_CHAR 改验证语）→ PostMessage 点「发送」→ 校验终态：结果行按钮变「已发送申请」。
7. 写本地记录 `{phone, wechat_name, added_at, effect}`；任一步骤与预期不符 → 关闭弹窗恢复原状，`effect=unknown` 绝不重试。

### 3.6 风险与待验证项

| 项 | 状态 |
|------|------|
| 添加客户全流程（检索 + 名称解析 + 发送邀请） | ✅ **2026-08-30 CLI 端到端真机复验通过**（`aid-wecom add-customer --phone … --yes` 全程纯 PostMessage，终态「已发送申请」校验通过） |
| 中文 WM_CHAR / WM_KEYDOWN 输入 | 微信 Qt 已验证，企微同框架预期可行，需一次 probe |
| 聊天窗 Enter 发送（PostMessage keydown） | 添加客户对话框已验证 PostMessage Enter 有效；聊天输入框需单独 probe |
| 聊天消息区滚动 + OCR 读消息 | 移植 weixin-cli p4，企微气泡布局需重新调 prompt |
| 企微版本升级改窗口类名/布局 | `probe` + doctor 做启动自检，类名可配置 |
| watch 模式与用户同时操作的互斥 | 全链路 PostMessage 不动真实鼠标/键盘；进会话会改变用户当前视图 → watch 巡检期间记录并恢复原会话 |

---

## 4. 探测脚本存档

`.tmp/wecom-probe/`：`probe-windows.ps1`（进程+窗口枚举）、`probe-children.ps1`（子窗口枚举）、`probe-shot.ps1`（PrintWindow 截图）、`probe-click.ps1` / `probe-fg-click.ps1`（PostMessage 点击±前台激活）、`probe-input.ps1`（WM_CHAR 输入）、`probe-fullkey.ps1`（keydown+keyup 输入）、`probe-paste.ps1` / `probe-sendinput.ps1` / `probe-typekeys.ps1` / `probe-focus-type.ps1`（已证伪路线留档）、`probe-point.ps1`（WindowFromPoint 归属判定）、`probe-minimize.ps1`（最小化截图）。产品化时按 weixin-cli 的 probe→driver 晋级流程重写，不直接搬这些脚本。

---

## 5. M2/M3 真机验证补遗（2026-08-31）

M2（chat-search/message-send）与 M3（unread/history/watch）真机端到端验证通过。新沉淀的实测事实：

1. **Shift 修饰字符必须走 WM_CHAR**：keydown 路径发「M」落成「m」（Qt 从真实键盘状态读修饰键，posted Shift 无效，与 Ctrl+V 变 v 同源）。规则：VkKeyScanW 高位含修饰位或无 VK 的字符 → 纯 WM_CHAR；无修饰 ASCII → 纯 keydown。两路径不可混发（双倍字符）。
2. **搜索框保留上次查询词**：不清理则新输入拼接旧词（实测「WayneLWayneLu」）；框内右侧 × 按钮 PostMessage 点击即清空；清空后/输入后必须 OCR 回读验证。
3. **搜索 overlay（SearchResultWindow2）渲染异步**：刚出现可能只有一行「进入全局搜索」，结果加载后才长高——等待必须轮询到高度稳定，不能用固定 sleep。
4. **OCR 区域必须避开 overlay**：searchbox 读取带 y 下缘卡 0.065h（overlay 从 y≈54 弹出），否则 overlay 的搜索历史会被误读为框内残留。
5. **OCR 光标伪影**：文本框末尾 caret 会被 OCR 成 `l`/`|`，回读比较要容忍末尾单伪影字符（单向）。
6. **滚轮：PostMessage WM_MOUSEWHEEL(0x020A) 有效**，wParam=delta<<16（±120），lParam 是屏幕坐标。
7. **未读角标在头像右上角**（不是行右侧），RapidOCR 对角标白字小数字整体漏检——实现为红色像素 blob 检测定位 + 裁切 5x 放大 OCR 读数。
8. **进会话即清角标**（固有副作用）：watch 水位读取成功后必须将 last_unread 归零，否则「角标 1 < 旧水位」的新消息会被静默漏报（CR P1 修复）。
9. **时间分割线识别必须整行锚定**：前缀匹配会把「昨天发的文件收到了吗」这类真实消息误吞为分割线（CR P1 修复）。
10. **消息文本归一化三侧一致**（py/ps1/TS）：去空白 + 全角转半角 + 小写折叠；气泡/预览的前缀比对、页间去重、水位比对全部走归一化。

M3 真机验证记录：`unread` 快照准确（微信客服=7、邮件提醒=1）；`watch --once` 首轮正确产出 2 个 new_messages 事件（含消息正文），第二轮无候选输出 tick，同内容不重发。
