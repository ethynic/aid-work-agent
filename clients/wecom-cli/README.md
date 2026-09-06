# wecom-cli（aid-wecom）

企业微信（WXWork.exe）操作 CLI / MCP Provider。整体骨架与 `clients/weixin-cli` 同范式：
CLI 动词子命令与 MCP stdio server 共用同一 operation 层，UI 自动化下沉到
`drivers/ps1`（DRIVER_JSON 进程协议）+ `drivers/py`（RapidOCR）。

设计与真机验证依据：`docs/research/wecom-cli/probe-and-design-20260829.md`
（2026-08-29 企业微信 5.0.9 全流程实测）。

## 命令

```
aid-wecom probe [--verbose] [--json]        # 只读环境探测：平台/交互会话/PowerShell/进程/主窗口/登录态/当前页
aid-wecom search --query <词> [--type contact|group|any] [--limit N] [--json]
                                            # 搜索联系人/群聊（只读），返回带 target_ref 的候选
aid-wecom send --target-ref <ref> --text <文本> [--json]
                                            # 写动作：向 target_ref 目标发送 1 条文本消息
aid-wecom unread [--name <名>] [--json]     # 未读会话快照（只读，不开会话不清角标）
aid-wecom read --target-ref <ref> [--max-pages N] [--since-days N] [--json]
                                            # 读会话消息（只读内容；进入会话会清除该会话未读角标）
aid-wecom watch [--interval 秒] [--once]    # 新消息跟踪循环：事件 NDJSON 逐行写 stdout，Ctrl+C 退出
aid-wecom add-customer --phone <11位> --yes [--json]
                                            # 写动作：通讯录→新的客户→添加→检索→发邀请（--yes 显式确认）
aid-wecom mcp --stdio                       # MCP server（stdout 只承载协议）
aid-wecom doctor [--json]                   # 只读环境自检（任一门禁失败退出码 1）
aid-wecom version [--json]                  # 版本 + provider manifest（含 schema_digest）
```

退出码：成功 0 / 参数错误 2 / 其余失败 1。`--json` 时 stdout 最后一行为 OperationResult JSON。

## 新消息跟踪（M3：unread / read / watch）

无会话归档场景的核心能力，三个层次：

- **`unread`**：未读会话快照。PrintWindow 截主窗口 → OCR 会话列表列 →
  `[{name, preview, unread_count, x, y}]`（无未读不出现；`x/y` 为名称行中心，
  供 watch 直点会话行）。只读，不打开会话、不清角标。
- **`read`**：读会话消息。target_ref 定位进会话（M2 搜索机制 + 标题严格校验防串会话）
  → 先下滚到底 → 逐屏上滚截图 OCR → 页间「旧页后缀 == 已合并前缀」最大重叠去重 →
  `{title, messages:[{side,text}], pages_read}`（side ∈ self/peer/timeline），
  事后滚回底部恢复原位。**副作用：进入会话会清除该会话未读角标**（企微客户端固有行为）。
  `--since-days N`：某屏最早「M月D日」分割线超龄即停止上翻（简单版）。
- **`watch`**：新消息跟踪循环。每轮 = `wecom_watch_poll` 单轮：unread 快照与本机水位
  文件 `%LOCALAPPDATA%\AidWorkAgent\wecom-cli\watch-state.json` diff（unread_count
  增大或新出现 → 候选）→ 每候选直点会话列表行（标题校验不一致降级搜索定位）→
  读当前屏 → 与水位比对只取增量 → 推进水位。**同一水位不会重复推送相同消息**；
  读取成功后角标水位归零（进会话已清角标，之后任何角标都是新增，防止「读取后来 1 条」
  被旧水位压住漏报）；读取失败的候选不推进水位（下轮重试，不丢消息）；
  会话从快照消失时水位同样归零。

`read`/`watch` 的 artifact 目录（`%LOCALAPPDATA%\AidWorkAgent\wecom-cli\artifacts\history-*` / `watch-*`）
含逐屏截图与 `driver-log.txt`（OCR 原始输出，**含消息明文**），仅用于真机排障；
watch 事件 NDJSON 的 `messages` 同样是消息明文（能力本身即读消息）。请注意本机文件与管道输出的访问控制。

watch 事件 NDJSON 协议（stdout 逐行一条 JSON，进度/告警只写 stderr）：

```json
{"type":"new_messages","session":"陆伟@微信","unread_count":3,"messages":[{"side":"peer","text":"在吗"}]}
{"type":"tick","unread_total":8}
```

`new_messages` 每个有增量的候选会话一条；本轮无任何新消息时发一条 `tick`。
`--once` 单轮（测试/手动）；循环期间持有跨进程互斥，与 send/search 等命令不并行。

MCP 只暴露 `wecom_unread_list` 与 `wecom_watch_poll` 两个 M3 工具（每轮一次 tool call）；
`wecom_history_read` 只走 CLI `read`（长滚动抓取不适合 Host 高频调用）。

## target_ref（搜索 → 发送 的目标句柄）

`search` 返回的每个候选带 `target_ref`：base64url(payload) + HMAC-SHA256 签名
（本机密钥 `%LOCALAPPDATA%\AidWorkAgent\wecom-cli\target-ref.key`，不可跨机验证），
payload 含 name/type/subtitle，**有效期 5 分钟**。`send` 必须持有效 target_ref：
过期 → TARGET_REF_STALE（重新 search 获取）；篡改/格式非法 → INVALID_ARGUMENT。
发送时驱动按 name+section+subtitle 在搜索结果里精确匹配，同名多项消歧失败 →
TARGET_AMBIGUOUS 拒绝发送。

## 运行依赖

- **Windows（win32-x64）**，已登录且未锁屏的交互桌面会话；
- 企业微信 Windows 客户端（WXWork.exe）**已登录**（5.0.9 实测）；
- PowerShell（powershell.exe 在 PATH）；
- `add-customer` / `search` / `send` / `unread` / `read` / `watch` 另需 OCR：仓库根 `venv` 的 python + `rapidocr_onnxruntime`
  （`drivers/ps1` 上四级为仓库根，取 `venv\Scripts\python.exe`；缺失 → CONFIG_MISSING）。

## 关键实现事实（真机实测，勿随意改）

- 主窗口 = 可见、class `WeWorkWindow`、标题「企业微信」、≥600x400 且面积最大者；hwnd 动态，每次重新解析。
- 内容子窗口 class `WXworkWindow - 企业微信-<页名>`，类名编码页名（页面状态校验用）。
- 点击走 PostMessage（WM_MOUSEMOVE+LBUTTONDOWN/UP），按 WindowFromPoint 路由到坐标归属 HWND；
  截图走 `PrintWindow(hwnd, hdc, 2)`（遮挡/最小化可用，9 点采样全黑回退 CopyFromScreen）。
- 文本输入只用纯 WM_KEYDOWN+WM_KEYUP（**禁止同发 WM_CHAR**，双倍字符）；
  注入输入（SendInput/keybd_event，**键盘与鼠标**）被企微 5.0.9 完全丢弃，**禁止**（2026-08-30 真机复核，
  覆盖早期"SendInput 鼠标可用"的误判）——全部交互走 PostMessage，驱动层不提供任何 SendInput 原语。
- Enter 触发检索用 PostMessage（焦点由 PostMessage 点击输入框 + WM_KEYDOWN 输入建立）；
  「添加」按钮 PostMessage 点击有效，但 `InputReasonWnd` 弹窗有数秒延迟（等 15s）。
- 实测时序（2026-08-30）：PostMessage 点「发送」后 `InputReasonWnd` ≤1s 关闭，检索弹窗结果行
  ≤3s 才变「已发送申请」——终态校验必须轮询（每 1s，上限 10s），单次快照会误报 EXECUTION_UNKNOWN。
- 每次 add-customer 运行的点击坐标与 OCR 原始输出写 artifact 目录 `driver-log.txt`（真机排障用）。
- 写动作零自动重试：`effect=unknown` 一律人工核对；`CUSTOMER_NOT_FOUND` 不重试。
- M2（2026-08-31 实测；**2026-09-04 随客户端更新重新标定**）：搜索 = 点主窗口搜索框
  （**像素锚定 (330,72)**，新版客户端左栏为固定像素列、比例坐标在窗口变宽后全部偏移；
  × 清空按钮同理 (473,77)）→ 输入关键词 → `SearchResultWindow2` overlay，OCR 读分区结果
  列表；点结果行进会话 overlay 自动关闭。中文输入必须 WM_CHAR 逐字（VkKeyScanW 对中文
  返回 -1，Send-WeComText 自动降级），英文/数字纯 WM_KEYDOWN。发送 = PostMessage Enter；
  发送前必须 OCR 校验会话标题一致 + 输入框无残留草稿（fail-closed 防串消息）；
  打开外部联系人会话主窗口可能变宽（2196→2916 物理 px）。
- **2026-09-04 客户端更新后的布局事实（真机实测，勿随意改）**：
  - 搜索框固定在左栏 x∈[154,505]、y∈[44,100]（物理 px @2x DPI）；框内文本 x0≈221；
    右侧聊天区会话标题与框同高（普通会话 x0≈0.296w、外部联系人 x0≈0.22w），
    searchbox 残留检查必须用像素带 x∈[140,510]。
  - 新版搜索 overlay 分区新增「应用提醒」（官方应用账号如文件传输助手归此分区）；
    搜「陆伟@微信」零结果——驱动搜索词必须剥掉 @微信 后缀（结果行名称本就带后缀）。
  - 外部联系人（@微信）会话右侧出现「智能总结」侧栏（客户需求/客户意向/成交卡点…，
    x0≥0.76w），且主窗口撑宽到 2916：history/bubble/input 的 OCR band 必须检测并排除
    侧栏（「立即总结」按钮会被误判成输入草稿、侧栏标签会被误读成 self 消息）；
    聊天区左边界从比例改为像素锚定 max(0.10w, 620)。
  - 未读角标在头像右上角 x≈[0.09w,0.11w]（旧标定 [0.15w,0.26w] 完全错过）；会话列表列
    文本中心 x∈[0.08w,0.30w]；时间列中心 ≈0.257w；输入工具栏图标行移到 ≈0.83h。
  - MCP stdio 场景下 Host 可能只传白名单环境变量（SDK getDefaultEnvironment）：PS 5.1
    对管道化原生命令在该环境下抛 CantActivateDocumentInPipeline（python 未执行、
    $LASTEXITCODE 为空）——驱动内 OCR 必须用 System.Diagnostics.Process 直启。
- M3（2026-08-31 截图校 OCR）：未读角标是**会话行头像右上角**的红色圆形白字数字
  （不是行右侧——任务书描述与实测不符，以像素为准）；RapidOCR 对角标白字小数字漏检率高，
  必须红色像素 blob 检测定位 + 裁切 5x 放大（原图/二值化各试一次）OCR 读数，读不出兜底 1。
  会话行右侧时间列误识多（「07/09」→「60/L0」），按宽松形近字符模式剔除。
  消息区滚动：PostMessage WM_MOUSEWHEEL（wParam=delta<<16 按 uint32 掩码，lParam=屏幕坐标）有效；
  输入工具栏图标行在 0.71-0.73h（OCR 会读成「X·三」碎字），history 模式消息区上界卡 0.70h。
- 含中文的 .ps1 必须 **UTF-8 with BOM**。

## 目录

```
src/cli/          动词子命令 + 薄 renderer（进度/退出码/跨进程互斥）
src/mcp/          MCP stdio server + toolDefs + manifest digest
src/operations/   业务能力层（统一 OperationResult 契约，永不 reject）
src/platform/     PowerShell 驱动执行器 / 环境探测 / 命名互斥 / target_ref（HMAC 短期句柄）/ watch 水位状态
src/security/     日志脱敏（手机号不明文入日志）
drivers/ps1/      UI 自动化驱动（_common.ps1 底座 + probe/add-customer/chat-search/message-send/unread-list/history-read）
drivers/py/       RapidOCR：add_customer_result.py（添加客户弹窗）+ chat_ocr.py（search/send/unread/history 单一入口）
tests/            node:test，全部 mock（绝不触达真实企微窗口）
```

## 与相邻项目的关系

- **weixin-cli**：骨架/契约/驱动协议的直接参照（个人微信版）。
- **wecom-personal-rpa**：被取代对象——wecom-cli 接管其 UI 执行职责
  （C# 客户端 + `wecom-ops.ps1` 将瘦身为调用本 CLI 的壳）；服务端会话归档
  （`wecom_personal_rpa` channel 的 outbox/签名/inbox）不动，入站消息不走 CLI。

## 开发

```
npm install
npm run typecheck   # tsc --noEmit
npm test            # build + node --test（全 mock，不需要真实企微）
```
