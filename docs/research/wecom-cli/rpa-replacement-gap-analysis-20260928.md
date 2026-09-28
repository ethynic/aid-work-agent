# wecom-cli 取代 wecom-personal-rpa：功能差距分析与实验设计

> 调研日期：2026-09-28。本机企微客户端 5.0.9.6060 已登录（与 wecom-cli 标定版本 5.0.9 一致）。
> 前置文档：[probe-and-design-20260829.md](probe-and-design-20260829.md)（wecom-cli 探测与设计）。

## 0. 结论速览

wecom-cli 已覆盖 RPA UI 执行职责的**文本收发与搜索定位**（且标题严格校验强于 RPA 的「Enter 盲进第一个结果」）。真正要移植/补齐的差距按优先级：

| # | 差距 | RPA 现状 | wecom-cli 现状 | 风险 | 实验 |
|---|------|----------|----------------|------|------|
| G1 | **发送图片** | `Clipboard.SetImage` + keybd_event Ctrl+V + Enter（5.0.8 校准） | ❌ 无 | 🔴 高：keybd_event 被企微 5.0.9 丢弃，粘贴触发方式待重新验证 | E1+E2 |
| G2 | **发送文件** | `Clipboard.SetFileDropList` + Ctrl+V（触发确认弹窗）+ Enter | ❌ 无 | 🔴 高：同上 | E1+E3 |
| G3 | **搜索结果精确定位/点击** | Enter 盲进第一个结果（无校验） | ✅ OCR 分区 + 行中心坐标 + overlay hwnd 点击（已有）；但精度/同名消歧有上限 | 🟡 中：视觉模型点坐标链路（weixin-cli 方案）是否值得引入待实测 | E4 |
| G4 | **登录态 + 二维码** | get_login_state：online/need_login/offline + 二维码截图 base64 | ⚠️ probe 有 login_state，无二维码截图、无 qr_expired/account_limited | 🟢 低：窗口枚举思路可直接移植 | E5 |
| G5 | **会话复用**（同信封连续动作不重复搜索） | reuse_current_conversation | ❌ 每次 send/read 重新 search（稳但慢） | 🟢 低：设计项（显式 open 句柄或 --reuse），无需实验 | — |
| G6 | 多行文本（RPA 剪贴板天然支持换行） | ✅ | ❌ 明确拒绝换行（Enter=发送） | 🟡 中：Shift+Enter posted 修饰键无效（已知 posted Shift 落成小写），需另找换行路径 | E1 附带 |
| G7 | **决策层智能化**（消歧/终态判定目前是硬编码启发式） | 硬编码匹配，落空即拒绝 | ⚠️ name+section+subtitle 精确匹配 + 终态三选二，副标题 OCR 缺失即 TARGET_AMBIGUOUS 误杀 | 🟡 中：引入 Jev 决策模型可能提升消歧与终态判定准确率，代价是每步增加一次 API 延迟 | E6 |

**不移植**（按既定架构分工，见 wecom-cli README「与相邻项目的关系」）：

- 入站消息：服务端会话存档（`wecom_personal_rpa` channel 的 archive 栈）承担，不走 CLI；无存档场景用 CLI 已有的 `unread/read/watch`。
- 协议/可靠性层：HMAC callback、outbox 轮询、WS、SQLite 本地队列+租约、串行 worker、DesktopHealthSupervisor、Supervisor 看门狗——保留在 C# 壳（Client.App 瘦身为调用 wecom-cli 的壳）。
- `noop`/`handoff` 动作：协议级语义，无 UI 工作，留在壳层。

## 1. 三方现状盘点

### 1.1 RPA 现行 UI 执行面（`clients/wecom-personal-rpa/scripts/wecom-ops.ps1`，5 个 action）

- `search_user {keyword}`：激活主窗口（AttachThreadInput+SetForegroundWindow）→ 连按两次 Alt 聚焦搜索框 → Ctrl+A Delete 清空 → 剪贴板+Ctrl+V 输入 → Enter **盲进第一个高亮结果**（会话标题 OCR 校验是 TODO）。
- `send_text {keyword,text,reuse_current_conversation?}`：剪贴板粘贴 + Enter。
- `send_image`：`Clipboard.SetImage`（重试 3 次）→ Ctrl+V → 等图片预览框 → Enter。
- `send_file`：`Clipboard.SetFileDropList` → Ctrl+V 触发「发送文件给 X」确认弹窗 → Enter → 清剪贴板。
- `get_login_state {qr_region_bbox?}`：主窗口(≥600x400)存在→online；只有小窗(宽<500 高<700)→need_login + CopyFromScreen 按默认 bbox `[530,200,800,470]` 截二维码 base64；无窗口→offline。qr_expired/account_limited 识别 TODO。
- `reuse_current_conversation`：同信封连续发送跳过搜索，仅校验前台仍是目标 hwnd。
- 每次键盘注入前后校验前台窗口，防误注入到其他应用。

**关键事实**：RPA 全部写动作依赖 keybd_event 注入（Ctrl+V/Enter）。企微 5.0.8 校准；而 wecom-cli 2026-08-30 真机复核确认 **5.0.9 完全丢弃 SendInput/keybd_event（键盘与鼠标）**，全部交互只能走 PostMessage。RPA 的粘贴路径在 5.0.9 上是否整体失效，是 G1/G2 的核心不确定性。

### 1.2 wecom-cli 已有能力（M1+M2+M3 真机验证通过）

`probe / search / send / unread / read / watch / add-customer / doctor / version / mcp`（详见 [README](../../../../clients/wecom-cli/README.md)）。与 RPA 对照：

- 搜索定位：像素锚定搜索框 (330,72) → 输入 + OCR 回读验证 → `SearchResultWindow2` overlay 渲染稳定后 OCR 分区解析 → 结果行中心坐标点击（投递到 overlay hwnd）。消歧 name+section+subtitle 精确匹配，多项即 TARGET_AMBIGUOUS 拒绝。**已强于 RPA**。
- 文本发送：WM_CHAR 逐字 + PostMessage Enter + 标题严格校验 + 输入框无残留草稿校验 + 终态三选二校验。仅单行 ≤2000 字。
- 无剪贴板使用、无图片/文件发送、无二维码截图。

### 1.3 weixin-cli 可参照机制（按实验相关性挑）

- **视觉模型定位**（`drivers/ps1/_common.ps1` `Invoke-KimiVision`）：截图→模型返回图像内**单点坐标** {x,y}→窗口原点换算→PostMessage 点击。通道 GLM-5.3-Flash 优先、kimi-k3 备份（服务端代理计费），所有模型结论都被**本地 OCR 近似匹配二次复核**（编辑距离/前缀比对），不一致 UI_CHANGED。搜索结果点击 = 视觉点坐标 + OCR 复核 label，点击必须投给 overlay 窗口。
- **常驻 OCR**（`drivers/py/ocr_server.py` + `src/platform/ocrResident.ts`）：RapidOCR 单进程常驻、stdin/stdout JSON 协议，替代每次拉起 python 的延迟。
- **像素锚定/气泡分割**（v2 名称路径）：纯本地无云化定位——像素梯度扫描找区域边界 + RapidOCR 唯一行定位 + 帧哈希短路。
- **剪贴板粘贴 + 单批 SendInput**（submit 动作）：SetText → Ctrl+A+Ctrl+V+Enter 一次序列化注入。**注意：微信 4.x 接受 SendInput，企微 5.0.9 不接受——此机制不可直接照搬到 wecom-cli**。
- 防重发 marker（request_id 排他文件）、effect=unknown 契约：wecom-cli 已有同款。

### 1.4 Jev 决策模型（TypeSafe AI，2026-09 新发布，E6 实验对象）

**是什么**：TypeSafe AI 的 "System One" 决策模型。**不做文本生成，只输出类型化决策**——三原语：
- `Choice`：从 criteria 选项中选一个，返回各选项概率 + confidence；
- `Boolean`（Noul）：返回 yes 概率；
- `Score`：概率加权的等级位置。

**API**（官方直连，key 在 `console.typesafe.ai/keys` 申请）：

```
POST https://api.typesafe.ai/v1/systemone
Authorization: Bearer $TYPESAFE_API_KEY

{
  "state": "<一段文本描述的当前状态/上下文>",
  "model": "jev-latest",
  "questions": {
    "<问题名>": {
      "type": "choice",
      "instructions": "<问什么>",
      "criteria": { "<选项>": "<选项说明>", ... }
    }
  }
}
```

响应：`{model, answers:{<问题名>:{type, choice, probabilities, confidence}}, usage}`。
也可走 OpenRouter（`typesafe/jev-1.13`，`POST /api/alpha/decisions`）或 LiteLLM 网关。

**关键事实（决定我们的接入方式）**：
- **纯文本输入，不支持图片**（32k token 上下文）——Jev **不能**做视觉定位/坐标回归，感知（截图→元素+坐标）仍由本地 RapidOCR 承担，Jev 只做「给定元素清单，点哪个/是否成功/是否继续」的**决策层**。
- 并行推理非自回归，主打低延迟；**多个问题可合并单次调用**（社区实测 13 问合一：便宜 11.5x、快 9.6x）。
- 计费仅按输入 token，输出免费——OCR 布局 dump（几百 token）成本极低。
- 安全：`TYPESAFE_API_KEY` 只走环境变量，不写入脚本/日志/提交（项目安全原则）。

## 2. 实验设计（E1–E6）

实验执行方式沿用项目惯例：`clients/wecom-cli/experiments/probes/`（参照 weixin-cli `experiments/probes/` 先例），每个实验独立 ps1 + 截图证据存档，跑完回填本文件 §3。**所有写动作实验以「文件传输助手」为靶会话**，不触碰真实联系人。

### E1 粘贴触发机制裁定（G1/G2/G6 的前置，最优先）

问题：企微 5.0.9 聊天输入框上，除了被丢弃的 keybd_event，还有什么方式能触发「粘贴」？

| 候选 | 说明 | 预判 |
|------|------|------|
| A. PostMessage WM_KEYDOWN 组合键 Ctrl+V | WM_KEYDOWN(VK_CONTROL)+WM_KEYDOWN('V')+逆序 UP | 🔴 大概率不通：posted 修饰键无效已有旁证（Shift 落成小写、Ctrl+V 变 v） |
| B. PostMessage `WM_PASTE` (0x0302) | 标准编辑控件消息 | 🟡 Qt 自绘输入框可能忽略，值得一试 |
| C. 工具栏「+」→ 文件对话框（原生 Win32 控件） | 完全绕开剪贴板；文件对话框是标准控件，WM_SETTEXT 填路径 + 点「打开」可靠 | 🟢 不依赖粘贴，但只解决文件/图片（G1/G2），不解决多行文本 |
| D. keybd_event 复核 | 直接复跑 RPA 的注入方式确认 5.0.9 行为 | 🔴 已有 2026-08-30 复核结论，只作一次性定案排除 |

判定标准：向文件传输助手输入框注入一段可回读文本，OCR 回读内容一致即该通道可用。

### E2 发送图片（G1）

- 方案一（依赖 E1）：剪贴板 `SetImage`/`SetFileDropList`（PS 可用 System.Windows.Forms.Clipboard，STA 线程）+ E1 裁定的粘贴通道 → Enter。
- 方案二（E1-C）：工具栏「+」→ 图片 → 原生文件对话框选文件。输入工具栏图标行位置已有标定（≈0.83h），需探测各图标 tooltip/响应。
- 终态校验：截图气泡区出现图片缩略（像素方差/新气泡行检测，OCR 对图片气泡读不出文字）；预览确认弹窗（若有）识别与 Enter。
- 附带裁定：粘贴通道若通，多行文本（G6）同步验证（SetText 含 \r\n 粘贴后是否保留换行）。

### E3 发送文件（G2）

- 与 E2 同两条方案；差异点：`SetFileDropList` 粘贴会触发「发送文件给 X」确认弹窗（额外一步 Enter/点「发送」按钮）；文件对话框路径则可能直接发送或带确认。终态校验：气泡区出现文件名文本（OCR 可读）。

### E4 搜索结果定位精度对比（G3）

- 对照组：现有 OCR 分区行中心定位（已真机验证）。
- 实验组：同一批 overlay 截图走 weixin-cli `Invoke-KimiVision` 同款链路（GLM-5.3-Flash 点坐标 + 本地 OCR 复核），比较：坐标偏差、同名候选命中、分区边界 case（外部联系人「智能总结」侧栏干扰已由 OCR 路径处理，观察视觉模型是否也稳）。
- 样本：≥10 个查询词，覆盖中文名、@微信外部联系人、群聊、同名多候选、应用提醒分区。
- 判定：视觉链路显著提升精度/消歧 → search 驱动引入可选视觉通道（保留 OCR 复核与降级）；否则维持纯本地方案，视觉只留作 UI_CHANGED 时的诊断手段。

### E5 登录态 + 二维码截图（G4）

- 移植 RPA get_login_state 思路到 wecom-cli probe 驱动：小窗判定（宽<500 且高<700 → need_login）+ CopyFromScreen 截二维码（PrintWindow 对登录小窗可能全黑，双路都试）。主窗口/无窗口路径 probe 已有。
- 限制：本机当前已登录，need_login 分支只能构造性验证（登出/另一账号机会成熟时补）——先做代码移植 + 已登录分支回归。
- qr_expired/account_limited 识别：二维码重拍比对/文案 OCR，列为后续项不阻塞替代。

### E6 Jev 决策层接入（G7，与 E4 同批样本可复用）

定位：感知（RapidOCR 元素+坐标）→ **Jev 决策（点哪个/成功没/继续否）** → 执行（PostMessage 点击）。Jev 不做视觉定位，替代的是现在写死在驱动里的启发式判断。

- **E6a 连通与延迟基线**：`TYPESAFE_API_KEY` 环境变量注入（不入脚本/日志），curl `POST api.typesafe.ai/v1/systemone` 发 1 问 vs 13 问合并，记录 P50/P99 延迟与 token 用量；同时测 OpenRouter 通道备选。
- **E6b 搜索结果选择**（用户核心用例：「搜了陆伟，OCR 出布局，Jev 决定点哪个」）：state = E4 同批 overlay 的 OCR 元素清单（编号 + 名称 + 副标题 + 分区 + 坐标），questions = `which_target`(choice，criteria=各候选元素) + `needs_disambiguation`(boolean) + `top2_gap`(score)。与现有 name+section+subtitle 精确匹配对比：同名多候选正确率、副标题 OCR 缺失场景的拯救率、误点率（必须 0）。
- **E6c 终态校验决策**：send 后的 OCR 证据（输入框状态/最新气泡前缀/会话列表预览）作为 state，questions = `sent_successfully`(boolean) + `evidence_sufficient`(boolean) + `failure_mode`(choice: not_sent/sent_elsewhere/unclear)。对比现有三选二启发式的误判率。
- 判定标准：Jev 链路在消歧/终态判定准确率显著优于启发式、单步延迟增量 ≤1s（并行问合并单次调用）、误点率 0 → 引入为驱动决策层（保留启发式降级：Jev 不可用/低置信度回退现有规则）；否则仅留作离线分析工具。

## 3. 实验记录（回填区）

### 已有能力实测（2026-09-28）

- probe / doctor / unread 通过；unread 端到端 4.6s（2 未读会话）。
- `search 陆伟` 失败：**残留误判 bug**——搜索框实际为空（占位符「搜索」），但 OCR 检查带 `x0∈[140,510]、y∈[0.02h,0.065h]` 把聊天区当前会话标题「文件传输助手」（实测 x0=423-424）误读为残留 → 点 × 无效果 → 复核仍"有残留" → UI_CHANGED fail-closed 中止。根因：检查带按窗口宽 2196/2916 标定，窗口 1449/1280 时标题 x0 前移入带内。**修复方向：动态检测搜索框边界替代固定像素带**。
- 新增实测事实：**ESC 会最小化企微主窗口**（用户观察，2026-09-28）——驱动清理逻辑禁用 ESC，改点主窗口空白；最小化状态下 PrintWindow 出黑图回退 CopyFromScreen 会抓到遮挡窗口（VS Code），截图证据被污染。
- `SearchResultWindow2` 关闭后窗口对象以 **visible=False 残留**（不销毁）——检测 overlay 必须过滤 IsWindowVisible（驱动 Wait-WeComWindow 已过滤，实验脚本曾因此误判）。

### E-CF：Ctrl+F 触发搜索框（2026-09-28，企微 5.0.9.6060）——✅ 已裁定

人工对照（用户真键盘 Ctrl+F）确认**快捷键存在且效果等同点击搜索框**。程序化通道裁定：

| 通道 | 结果 | 备注 |
|------|------|------|
| PostMessage 裸组合键（Ctrl↓→F↓→F↑→Ctrl↑） | ❌ | posted 修饰键 Qt 读不到（GetKeyState 不随 PostMessage 更新） |
| keybd_event（前台激活已验证） | ❌ | 复证 2026-08-30「注入被企微 5.0.9 的底层键盘钩子丢弃」 |
| SendInput（4/4 注入成功、前台已验证） | ❌ | 同上（LLKHF_INJECTED 被吞） |
| **AttachThreadInput + SetKeyboardState(VK_CONTROL) + PostMessage F** | ✅ **成功** | `attach-ctrlf.ps1` 闭环验证：搜索框聚焦（占位符隐藏）→ 输入 z 落框（× 按钮出现、4x 放大 OCR 读出「Q z」）。**全程后台、零真注入、不需前台** |

关键沉淀：

- **attachstate 技术要点**：AttachThreadInput 到企微 UI 线程后 SetKeyboardState 把 VK_CONTROL 写入共享键盘状态表，再 PostMessage WM_KEYDOWN(F)——Qt 处理该消息时 GetKeyState(VK_CONTROL) 读到按下，Ctrl+F 快捷键命中。用完恢复键状态并 detach。注：该 P/Invoke 在长脚本中曾稳定返回 False、独立短脚本稳定 True，原因未明（疑似进程内状态影响），产品化时若复现可拆子进程承载。
- **动态 × 清空验证成功**：4x 放大裁切 OCR 定位 × 按钮（当前窗口宽 1280 时中心 ≈(337,38)，**旧像素锚定 (473,77) 在此宽度下已点歪**），PostMessage 点击后框清空、占位符回归。
- search box 内容检测同样可动态化：框内文本 x0≈200 起、× 在内容右侧出现即有残留；占位符「搜索」出现即空框——不再依赖固定像素带（修复残留误判 bug 的正解）。
- Ctrl+F 聚焦不弹 overlay（后台态）——overlay 在输入查询词后才出现，驱动流程无需依赖 Ctrl+F 弹层。

实验脚本：`clients/wecom-cli/experiments/probes/e1-ctrlf/`（ctrlf-probe.ps1 四通道对照、attach-ctrlf.ps1 成功通道闭环、clear-box-dynamic.ps1 动态清空、attach-diag.ps1 诊断）；OCR 坐标 dump 工具 `experiments/probes/e0-diag/ocr_dump.py`。

### E-S：search 链路端到端原型（2026-09-28）——✅ 全链路跑通

`experiments/probes/e2-search-full/search-full-attach.ps1`，搜「陆伟」端到端 **9.5s** 返回 5 候选（含同名「陆伟」外部联系人/内部同事各一，天然消歧样本）。链路组成（全部无固定像素锚定、无真注入、无 ESC）：

| 阶段 | 耗时 | 机制 |
|------|------|------|
| 主窗口解析 | 72ms | EnumWindows |
| Ctrl+F 聚焦搜索框 | ~250ms | **attachstate 通道**（AttachThreadInput+SetKeyboardState+PostMessage F） |
| 残留检测（空框判定） | ~1.7s | 截图裁切框带（x∈[120,420],y∈[0,62]）4x 放大 OCR：占位符「搜索」=空、内容 token=有残留（判定用 token 的 x1≥212——OCR 常把图标+文本合并成「Q 陆伟」，x0 是图标的） |
| 残留清空 | ~0.8s | **attachstate Ctrl+A + PostMessage Delete**（新原语，零 OCR 依赖；× 按钮 OCR 漏检率高已弃用；清空同时自动关闭 overlay） |
| 输入+回读验证 | ~2.1s | Send-WeComText（中文 WM_CHAR）+ 裁切 OCR 回读（剥前导 Q 图标误读） |
| overlay 等待+稳定 | ~2.6s | Wait-WeComWindow 可见性过滤 + 高度稳定轮询 |
| 结果 OCR | 已含在上 | chat_ocr search 模式（分区解析，返回 name/subtitle/section/x,y） |
| 清理 | ~2.8s | Ctrl+A+Delete 清框（overlay 随之关闭）+ 复核 |

性能注：每次框状态 OCR ~1.7-2s 大头是 python/RapidOCR 进程冷启动——weixin-cli 的常驻 OCR server（ocr_server.py）方案可直接照搬优化。空白点击**关不掉**搜索 overlay（实测）；确定性关闭 = 清空搜索框内容。

### E6：Jev 决策层接入（2026-09-28，官方 API jev-1.13.0）——✅ 连通/延迟/真实样本全部通过

**E6a 连通与延迟**（`experiments/probes/e6-jev/jev_probe.py`）：

| 场景 | 延迟 | token | 结果 |
|------|------|-------|------|
| 单问（choice，搜索框残留判定） | 831ms | in 393 / out 34（免费） | 正确 |
| **13 问合并单次调用**（发送终态校验全家桶） | **719ms** | in 1127 / out 414（免费） | 13/13 全对 |

合并调用近零边际延迟（13 问 ≈ 1 问）实测成立——驱动里「消歧+终态校验+歧义评估」可打包一次调用。

**E6b 真实样本决策**（search 陆伟 的 5 候选，任务=给外部微信联系人发消息）：
- `which_target`(choice)：选 **#1 陆伟@微信/微信联系人/联系人分区，confidence 0.98**；同为「陆伟@微信」但在聊天记录分区的 #5 得 0.02——正确理解分区语义。750ms。
- `is_ambiguous`：no，但 confidence 仅 0.23（yes 0.39/no 0.61）——对同名候选的存在给了诚实的歧义评估。
- score 类型 schema：`criteria` 必须是**等级数组**（`['very_low',...,'very_high']`），返回 `{score: 3.86(0基), probabilities(各等级), confidence, legend}`。821ms。

**与规则方案对比（定位决策环节）**：

| 方案 | 延迟/次 | 同名跨分区消歧 | 成本/次 |
|------|---------|----------------|---------|
| 规则（name+section+subtitle 精确匹配，现有） | 0ms | ❌ TARGET_AMBIGUOUS 拒绝（副标题 OCR 缺失时也误杀） | 0 |
| Jev choice（OCR 布局 → 决策） | ~750ms | ✅ 语义选对 + 概率化置信度 | ~400-1100 input token，output 免费 |

**集成结论**：Jev 不做感知（坐标仍由 OCR 出），做「点哪个/成功没/歧义否」的决策层。生产驱动集成建议沿用 weixin-cli `Invoke-KimiVision` 模式（PS 驱动内 curl.exe 调 API，key 走环境变量注入），规则匹配保留为降级路径（Jev 不可用/低置信度时回退；写动作低置信度直接 fail-closed）。

安全注：key 经用户级环境变量 `TYPESAFE_API_KEY` 注入（setx），不入脚本/日志/提交；该 key 曾在对话明文出现，建议实验期结束后在 console.typesafe.ai 轮换。

### E6c：Jev 全流程闭环（兜底定位 + 结果选择，2026-09-28）——✅ 真机点击闭环通过

`experiments/probes/e3-jev-search/jev-search.ps1`：**两级决策全部由 Jev 做，真机点击执行**，全链路 15.7s：

1. **搜索框定位（兜底路径）**：OCR 顶部布局（含头像名 WayneLu、占位符 Q搜素、聊天标题、导航「消息」等干扰 token）→ Jev choice → **选中占位符 token，conf 1.0**，点击 (214,36) → 输入 陆伟 回读 ✓（点击确实聚焦了搜索框）。783ms。
2. **结果判定（三问合一）**：search_successful=yes(1.0)；**which_result=R0 陆伟@微信 conf 0.63**（概率 R0=0.71, R4=0.18——聊天记录分区的同名候选，合理保留概率）；is_ambiguous=no 但 **conf 仅 0.04（近乎对半）——诚实暴露歧义**。750ms。
3. **点击 R0 → 会话打开验证 ✓**（标题带出现「陆伟@微信」）→ Ctrl+F+Ctrl+A+Delete 清理，overlay 关闭。

关键发现：

- Jev 置信度有波动（同场景两次 which_result conf 0.98 / 0.63，state 措辞差异所致）——**写动作集成必须设置信度阈值**（如 <0.6 fail-closed 或转人工消歧），且 state 构造要模板化稳定。
- is_ambiguous 的低置信度是免费的质量信号：现有规则在此场景直接 TARGET_AMBIGUOUS 拒绝，Jev 给出主选 + 歧义度 + 次选概率，可支撑「高置信自动执行 / 低置信拒绝」的分级策略。
- 工程坑：含中文的 .ps1 无 BOM 会被 PS 5.1 按 ANSI 解析直接语法错误（README 已有警告，实验脚本同样适用——Write 工具默认无 BOM，需补）。

