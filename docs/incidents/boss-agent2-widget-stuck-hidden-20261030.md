# agent2 BOSS 新会话续筛全拒绝：渲染辅助窗口持续隐藏拒绝（2026-10-30）

> 复盘登记：2026-10-30。调查执行：主控（luwei 侧开发助手），证据含 agent2 服务端日志/数据库与客户机 runtime 日志副本。
> 关联：[2026-10-23 BOSS问题统一开发交接](boss-agent2-issues-and-minimal-repair-handoff-20261023.md)、[恢复计划 plan-runner-local-tool-recovery-lockup-fix](../plans/plan-runner-local-tool-recovery-lockup-fix.md)（Phase 8/11）。
> 2026-10-30 二次审阅：核对本地源码、已保存脱敏节选及 Microsoft/Chromium 一手资料；未重新访问 agent2、查询数据库或操作客户机。下文区分原调查记录、可复核事实与待验机制，补充意见见 §10。本轮仅修订复盘和索引，未开发、提交或部署。
> 2026-10-31 原日志复核：找到客户机 `runtime(2).log` 完整副本，重新对齐发送失败前后协议与 HWND 记录；修正最后健康采样时刻、无采样区间及后续 wheel 拒绝，结论与新增证据见 §12。未访问服务器或操作客户机。
> 2026-10-31 客户取证回收：22 条历史恢复记录补齐跨调用 target/binding，10 条整窗记录补齐 HWND/PID/启动时间；确认两次关闭后验及第二次整窗原生点击，见 §16。§12–14 中按当时证据保留的未知，以 §16 新结论为准。

## 1. 事故概述

- **时间**：2026-10-30 10:48–11:12（北京时间）
- **环境**：agent2 测试环境；设备「孙晨1015 1047agent2」（device 161f4dc9-4960-4f41-9c29-7f192810fbcd，tenant_4f5fd42dfdea / user_cda841c5eb4c）
- **客户端版本**：BOSS 插件 manifest 摘要 `ef0b8cf5…d16644` 与 2026-10-26 生产登记一致（≥0.3.25，含 Phase 8/11 恢复链；日志中存在 `boss-input-discovery` 枚举记录即 Phase 8 代码确证）
- **现象（用户反馈）**：
  1. 会话1 筛选/打招呼基本正常；新开会话要求重新筛选后，BOSS 停在「沟通」页，智能体回不到「推荐牛人」页，**URL 直跳兜底没有执行**；
  2. 用户手动切到「推荐牛人」后，智能体没有重新设置筛选条件，**一直在点当时页面里的第一份简历**（李宇详情弹层还挂在页面上）。
- **现场约束补充（2026-10-31）**：用户明确排除本次人为最小化；手动操作页面是自动化未按指令执行后为排障所做，不应倒置为最初失效的原因。本次分析不再将最小化列为事故触发主线，详见 §13。
- **结果**：新会话 7 次工具调用失败（`STALE_OBSERVATION: render widget hidden; input refused`），另有 1 次 `boss_goto` 以 `via=already` 成功；简历 0 份读取、0 入库、0 打招呼。恢复链仍发生过 CDP 浏览输入，不能表述为页面状态完全未变。11:11:45 智能体请求用户手动关弹层后用户未再回复，会话终止。

## 2. 根因结论（TL;DR）

**已定位的直接阻断点**：调试 Chrome 主窗子树中的 `Chrome_RenderWidgetHostHWND` 在已保存探测中均无可见候选，物理输入在 discover 阶段被 `isWindowVisible(rw)` 守卫拒绝。原健康大尺寸 HWND 一度未被枚举到，随后以 `visible=0` 再出现，并持续至最后一次记录；同期探测显示主窗前台、未最小化，另有页面/整窗观察成功。

**尚未闭环的底层机制**：用户已排除本次人为最小化，撤回此前“空窗内最小化→换父→回迁”的事故解释。`IsWindowVisible=false` 仍可能来自辅助 HWND 自身或隐藏祖先；现有日志未读取自身 style、完整父链或 HWND 与 CDP target 的对应关系。Chromium 将该类 HWND 定义为无障碍/旧触控板兼容辅助窗口，页面显示与输入不能仅以它是否可见判断（§10.1）。因此“页面正常”和“这枚 HWND 隐藏”并不矛盾，也不必假定屏幕只是冻结像素。

**10-31 原日志复核增量**：`boss_send_to` 内 10:59:18.704 仍枚举到同一可见大 widget，10:59:19.625 原生点击成功，随后才发生搜索入口定位歧义；首个无可见 widget 探测在 11:06:07.425，两者相隔约 6 分 49 秒且无中间 widget 采样。相关探测的主窗/PID 未变，日志却没有 targetId，故不能由 session 摘要改变推断 target 被替换。后续只读开卡后实际受阻的是 native wheel（§12），不是所有恢复都停在开卡前。

**10-31 刷新对照增量**：用户手动刷新实验中，旧 widget 确实失效、新 widget 替换并可见；同一原生选择器顺利选到新 HWND，没有复现持续隐藏（§11.4）。这证明刷新可能更换 widget，但尚不支持“仅因刷新换句柄、代码仍缓存旧 HWND”能解释本次事故。

**10-31 客户取证增量**：事故时段 22 条恢复观察的 target/binding 全部相同，10 条整窗观察的主 HWND/PID/进程启动时间也全部相同。跨会话恢复选中另一 target 的怀疑在这些记录中未获支持。11:11:28 整窗模式原生关闭点击成功，随后 fresh inspect 为 `absent/satisfied`；11:11:39 普通 native wheel 仍因 widget hidden 被拒（§16）。现场已经证明一条受守卫约束的整窗原生点击路径可用，后续优先验证普通输入的宿主选择、坐标映射与守卫适配，而非只等待辅助 HWND 重新显示；最初隐藏的触发事件仍未知。

既有 6 道防线中 4 道如实执行但均为「等待型」或 CDP 替代通道，等不来 widget 恢复可见；2 道「动作型」（超屏归位、落点抬窗）触发条件不满足。**这是 10-23 复盘登记为「待验」的『持续隐藏拒绝分支』的首次现场实证，并证明 Phase 8 的 1.2 秒探测 + 退避重试对该状态无效。**

三个用户疑问的定性：
1. **URL 兜底未执行**：`PageNavigator.navigate()` 的直跳兜底只在「菜单点击正常返回但 URL 未变」时触发（注释：*直接异常留给共用恢复*）。本次菜单点击在输入门禁即被拒抛异常，控制流到不了兜底分支。兜底用的 `pageNavigate` 是 CDP `Page.navigate`，不依赖物理输入，**具备尝试条件但路径不可达**；未实测，不能承诺必定成功，更不能承诺它会修复后续原生点击/滚轮。
2. **没有重新设置筛选**：智能体从恢复链截图看到职位及「7 项筛选」后，推断旧条件可用。计数不能证明具体选项符合本次要求；`boss_filter_options` 又未成功，因此条件一致性仍未核实，不能直接判定该决策正确。输入阻断与筛选是否正确是两个问题。
3. **「一直点第一个简历」**：已记录的 native 点击尝试均在投递前拒绝；但恢复链确有 CDP 输入，不能把外层或某次原操作的 `input_status=not_started` 推广为“整次调用零点击”。首卡重复失败有日志支持，用户看到的动作还可能包括前台化和 CDP 浏览；具体开卡/关卡需按子步骤核对。

## 3. 时间线（服务端 × runtime 两端对齐）

服务端 invocation 落库（`local_tool_invocations`，agent2 库）；runtime 日志为客户机 `%APPDATA%/aidwork-tool-runtime/logs/runtime.log` 副本（UTC，下表已换算北京时间）。

### 会话1 session_60b0484077b3（10:48–11:01，基本正常）

| 时间 | 事件 |
|---|---|
| 10:48–10:51 | goto/list_jobs/select_job/filter_options 成功；boss_filter 首次失败（落点遮挡 WindowFromPoint）后重试成功（effect=applied） |
| 10:51 / 10:55 | boss_resume_batch、boss_resume_next 两批读简历成功 |
| 10:58:31 | boss_greet 成功，主 widget 69212272 visible=1（runtime 日志 L7685）；原文将其记为最后健康采样，已由下一行修正 |
| 10:59:18–10:59:21 | **最后健康枚举实际在 boss_send_to 内**：10:59:18.704 同一 widget visible=1（L7715），10:59:19.625 原生 click 成功；10:59:21.747 才报搜索入口 42 个候选歧义（L7727），尚未执行消息发送 |
| 10:59–11:01 | boss_send_to 及 boss_open_chat ×2 均以相同搜索入口歧义失败，恢复曾重新 attach/观察；11:01:08 boss_goto(chat) 成功（纯 URL 校验）。原调查记录会话结束时仍有详情弹层，本轮日志不含截图正文，未独立复核该画面 |
| 10:59:18.704–11:06:07.425 | **约 6 分 49 秒无 widget 采样**，包含上述定位失败与恢复，并非全程无调用；11:01:08–11:06:06 约 4 分 58 秒无新工具调用。用户已排除人为最小化；是否换标签、刷新或重挂父尚无对应事件证据 |

### 会话2 session_5d6483e07a53（11:05–11:12，全拒绝）

| 时间 | 调用 | runtime 证据 | 结果 |
|---|---|---|---|
| 11:05:13 | 用户「帮我筛选简历」，表单确认 | — | — |
| 11:06:06 | boss_goto#1（8c3663b5） | **主 widget 从枚举消失**（仅剩 1480×44 窄条 + 1px 两个隐藏 widget）；7 次探测（discover_ms=1240）后拒绝 | ❌ render widget hidden |
| 11:06:49 | boss_goto#2（0a26ca15） | **主 widget 回到枚举但 visible=0**（rect 与会话1 相同） | ❌ 同上 |
| 11:07:19 | 智能体告知用户被挡，请手动切换 | — | — |
| 11:07:31 | 用户手动切到「推荐牛人」（回复「好了」） | — | — |
| 11:07:33 | boss_goto#3（56ed6228） | 纯 URL 校验，零输入 | ✅ via=already |
| 11:07:36 | boss_list_jobs（694d525d） | **用户真实点击过页面后，widget 依然 visible=0**（L8132+） | ❌ |
| 11:08:07 | boss_filter_options#1（eae1e5da） | 同态拒绝；恢复链 window 诊断确认页面无遮挡 | ❌ |
| 11:08:14–11:09:25 | （无调用）用户按智能体提示摆弄窗口 | **rect 245,95 → 212,92（内容整体平移 33px）**：窗口被手动移动过，widget 依然隐藏 | — |
| 11:09:24 | boss_filter_options#2（29adbf36） | 同态拒绝 | ❌ |
| 11:10:01 | boss_resume_batch#1（f8762ca5） | 首卡点击两次被拒；11:10:13 CDP 开卡输入 ACK，随后 11:10:16 **native wheel 被拒**；恢复再进入 dismiss | ❌ |
| 11:10:28 | 恢复链 boss_overlay_dismiss | CDP 关闭输入返回；新回收记录确认 11:10:28.934 开始的 fresh inspect 为 absent/satisfied，11:10:33 后续做；11:10:36 CDP 浏览点击，11:10:39 **native wheel 再被拒** | — |
| 11:10:43 | boss_resume_batch#2（ae8ff3ad） | 原生点击拒绝后，11:10:57 CDP 浏览点击→11:11:00 wheel 拒绝；11:11:28 **整窗原生 dismiss 成功**→11:11:30 fresh inspect 为 absent/satisfied→11:11:38 CDP 浏览点击→11:11:39 wheel 拒绝（§16） | ❌ |
| 11:11:45 | 智能体请用户手动关弹层；两次关闭后验已由回收记录确认，之后续读又开卡且滚轮失败，不能仅归为用户未关闭 | — | 会话终止 |

## 4. 证据

### 4.1 服务端

- 主日志 `log/agent/aid-work-agent_20261030.log`：7 次失败调用 claim/result 行（L10709–10888 区间）。
- `local_tool_invocations`：原调查登记 8 个 invocation（7 个失败与 1 个 already 成功）；本轮未重新取得数据库结果 JSON。`input_status` 须区分原操作、CDP 开卡和恢复子步骤，不能将早期 not_started 外推为整次调用零输入；视觉诊断与 URL 仍以原调查留档为准。
- `chat_messages`（session_5d6483e07a53）：智能体 11:07:19 / 11:08:37 / 11:11:45 三次如实向用户说明被挡并请求配合，无误导。

### 4.2 runtime 日志（关键节选）

**脱敏节选**：[evidence/boss-widget-stuck-hidden-20261030/runtime-excerpts.redacted.log](evidence/boss-widget-stuck-hidden-20261030/runtime-excerpts.redacted.log)（保留原文件行号）。

决定性事实链（window=4662058 为调试 Chrome 的 BOSS 主窗，pid=33272）：

1. **健康采样**（原调查 10:48 起，本轮复核延伸至 10:59:18）：窗口下可见大 widget 为 **69212272**，另有 1px 占位 598126、1480×44 窄条 34676444，后两者隐藏；10:59:18 同组 HWND 仍保持该状态。
2. **11:06:07**：原大尺寸 widget 未出现在该次主窗子树枚举中。换父、销毁或枚举期间状态变化均可能；仅凭“枚举不到”不能证明销毁。`sequence.ts` 的旧真机注释也不是本次销毁证据；原文关于空窗内人为最小化的推断已按用户现场反馈撤回。
3. **11:06:50**：同数值 HWND 69212272 再次被枚举到，**visible=0**，后续已记录探测均如此。同值重现与换父回迁相容，但未监控 create/destroy/parent 事件，不能据此排除句柄复用，也不能确认其一直对应原页面。
4. **前台/在屏证据**：探测记录 `foreground=4662058 minimized=0`；后续矩形整体平移（左边界 -33px、上边界 -3px）后仍 hidden，支持期间发生窗口移动，但移动者与具体 API 未记录。用户反馈已手动切页，goto#3 读取到 recommend URL；没有同步鼠标命中取证，不能断言点击具体经哪枚 HWND。辅助 HWND 的可见性与页面可交互性可分离，见 §10.1。
5. **恢复链实际执行**：仅凭两次 `boss_overlay_dismiss ok=1` 不能证明关闭效果；§16 新回收的两次 fresh inspect 均为 `absent/satisfied`，补足此前后验缺口。11:10:28 perf 的 `cdp:clickBrowse` 对应 page 模式；11:11:28 对应 window 模式，经 `clickWindow` 原生脚本。后续续读再次开卡并在 native wheel 处拒绝，不能将恢复失败归结为“关闭从未生效”，也不能把合法续读开卡本身认定为错误。

### 4.3 代码路径定位

- 零输入守卫：`clients/shared/win32-native-input/src/sequence.ts` discover 阶段——绑定模式找到唯一 BOSS 主窗、还原+前台化成功后，枚举 `Chrome_RenderWidgetHostHWND`，无可见者取隐藏回退并抛 `STALE_OBSERVATION: render widget hidden; input refused`（exit 2）。
- URL 兜底不可达：`clients/boss-resume-assistant/src/main/boss/PageNavigator.ts:91-124`——`deps.click()` 抛异常直接上抛，仅「点击正常返回而 URL 未变」才走 `pageNavigate`；`pageNavigate` 实现为 CDP `Page.navigate`（`operations/bossContext.ts:248`），不需要物理输入。
- Phase 8 探测：`sequence.ts` discover 最多 7 次探测、其间 6 次 ×200ms 等待，仅等待不干预（commit `54d5befc`）。
- 超屏归位：`clients/boss-resume-assistant/src/main/input/windowExperiment.ts:79`——触发条件为窗口截图工具报 `window_outside_screen`（窗口矩形超出虚拟屏幕），预算每调用 1 次（commit `e44830e2` / `371a73a0`）。

## 5. 既有六道防线为何全部未接住

| # | 防护措施 | 提交 | 触发条件 | 本次执行情况 | 为什么没用 |
|---|---|---|---|---|---|
| 1 | 最小化还原+前台化（SW_RESTORE + AttachThreadInput） | 早期已有 | 前台化每次执行；SW_RESTORE 仅 iconic 时 | **前台化已执行；SW_RESTORE 是否执行未知** | activate 耗时和事后 minimized=0 不能证明还原分支发生；前台化未使辅助 HWND 可见 |
| 2 | 隐藏 widget 1.2s 重枚举探测 | `54d5befc`（Phase 8） | 枚举无可见 widget | **执行了**（7 probes，全程 `phase=wait reason=hidden`） | 只「等」不「推」，粘滞态下等不来 |
| 3 | native_window 调用级退避重试 | `54d5befc` | 认证的零输入拒绝 | **执行了**（backoff 1000/2000ms，completed=0） | 重跑后状态原样 |
| 4 | 只读开卡/page 或 window dismiss/readonly 早退 | `54d5befc`/`371a73a0` | 恢复链判定 | **执行了**（dismiss ×2，后验均 absent/satisfied，§16） | 关闭不恢复普通 native 滚轮；后者仍被 widget 守卫拒绝 |
| 5 | 超屏归位（移回屏内，预算 1 次） | `e44830e2`/`371a73a0`（Phase 11） | 截图工具报 `window_outside_screen` | **未触发（正确）** | 本次窗口始终在屏内、截图一直成功，归位的门没开。注意其触发是「超屏」不是「被其它窗口遮挡」 |
| 6 | 落点遮挡抬窗重试（guard 阶段） | 早期已有 | `pointOnWidget` 失败（widget 已可见之后） | **从未到达** | 死在更早的 widget 可见性检查 |

**已证实的缺口**：现有输入路径要求可见 widget，却没有在持续失败时区分“页面真的不可交互”和“辅助 HWND 不再适合作为输入代理”。等待未恢复该前提，超屏/落点分支也未满足触发条件；这证明当前路径未恢复成功，尚不证明 Chrome 本身无法恢复。

## 6. 与 10-23 复盘及 Phase 8 的关系

- 10-23 事故（同错误 15 次）后，[恢复计划](../plans/plan-runner-local-tool-recovery-lockup-fix.md) Phase 8 完成开发，进度行明确：**「10-24 最小化自动恢复续读已真验，持续隐藏拒绝分支待验」**；§21 受控实验把机制缩小为「有效 widget 换父并隐藏，原父窗口选择器只能返回隐藏回退」，但注明「尚未复现历史事故的严格条件——activateTarget 已 ACK、原生抬窗之后仍无法取得可见 widget」。
- 本次原调查记录符合该严格条件：activateTarget 有 ACK、前台化成功，而后续约 5 分钟的探测仍无可见 widget；与 10-24 已验的普通最小化自愈路径不同。它补上“现有恢复未救回本次持续隐藏态”的现场失败证据，不等于 §22 所有分支已覆盖，也不能据此把本次与旧事故定为同一底层原因。

## 7. 修复方案（内容区输入已开发，其余待排期，见§17）

### P0-1 goto 输入被拒时走 CDP 直跳兜底

- **位置**：`PageNavigator.navigate()`。
- **改动**：捕获菜单点击阶段经过现有 `nativeWindowInputFailure()` 函数认证的零输入拒绝，当 `pageNavigate` 可用时进入既有直跳分支。本次优先覆盖已证实的 hidden/unavailable；是否同时覆盖 foreground/target_activation_failed 需独立核对目标绑定语义，不按错误字符串宽泛兜底。该函数是模块导出，完整集合实际还含 `target_activation_failed`。
- **边界**：仅 goto（page_state 类）适用；保留原 target/binding、取消检查和 URL 后验，仍由 `attempt.input` 包裹导航。共享剩余普通重试预算，不能经 `onRetry` 重置/扩充预算；导航投递后异常按 attempted/unknown 记录，不能沿用此前菜单零输入证明。该改动恢复导航路径，不等于恢复筛选/读简历的 native 输入。

### P0-2 先定位隐藏对象与实际输入宿主；主窗口轻推降为实验

- **优先工作**：在失败现场只读记录 HWND 自身 style、完整父链、预期内容落点的 `WindowFromPoint` 命中对象，以及 CDP target 与真实活动标签/窗口的对应关系，见 §10.3。先分辨隐藏辅助 HWND、隐藏祖先、错误 target 或可见性生命周期异常，再选恢复动作。
- **实验候选**：对主窗口 `SetWindowPos` 轻推一次（x±1px 后复位），等待后重枚举；尚无证据支持直接加入每次物理输入的公共 discover 路径。现场已有更大幅度平移后仍 hidden 的反证，轻推与手动拖动消息序列可能不同，但不能因此预设它有效。
- **不采用**直接对 Chrome 子窗口 `ShowWindow`：子窗口归 Chrome 管理，强改可能被再次隐藏或与内部状态不一致。
- **后续候选**：先实验同一 target 真正切走再切回能否触发恢复；重复 activate 已活动 target 不等于发生 hide/show 转换。新开标签并迁移绑定会丢失当前页面条件/滚动/详情状态，与“原目标接续”边界冲突，本轮不作为自动兜底。
- **验证关键**：本次优先覆盖用户确认的未最小化场景；历史最小化恢复只作为既有兼容回归，不再作为事故复现条件。需验证可见页面的真实输入宿主与 CDP 目标关联；没有复现持续隐藏态时，只能报告实验未覆盖，不能将普通最小化自愈当作本次修复成功。

### P1 核对 dismiss 后原目标接续，不重复增加已有 re-inspect

- **位置**：`localOperationRecovery.ts` / `visualRecovery.ts`。
- **现状**：`localOperationRecovery.ts` 已在 dismiss 后以 purpose=verify、available_actions=[] 再 inspect，确认 obstruction=absent、goal_state=satisfied 才 resume；原提案中的 re-inspect 已存在。
- **核对方向**：区分“原目标正常开详情，随后 native 滚轮失败”和“恢复误点重新制造遮挡”。`ResumeBatchReader` 已有多次 native 拒绝后改走 CDP 开卡的窄分支，但读详情仍用 native wheel；开卡后再次失败不必由弹层循环引起。先取得 action_kind、后验、Reader phase 和失败 op，再决定是否改代码，不能将 dismiss 与合法读简历开卡永久互斥。

### P1 会话收尾关闭详情弹层

- 检查已有领域清理路径，能确认原详情身份且无在途用户操作时尝试关闭；失败如实登记页面收尾未完成。不得为清理重放 greet/send，也不能让清理失败覆盖已确认/unknown 的业务结果。优先确认新任务起点和旧弹层身份，不能假设会话结束时仍独占用户页面。

### 兼容与发布

- 目前只有方案审阅；goto 候选可局限 BOSS 插件，原生输入候选可能影响共享包的其他消费者，最终发行范围须核对实际依赖。预计不需服务端/wire 变更；服务端继续兼容已交付旧客户端。进入开发前更新关联设计及开发计划、登记修复任务，执行相应独立测试/CR；打包与真机验收不能替代用户当场部署授权。

## 8. 验收标准（修复完成后）

1. 按用户确认的现场顺序验收：筛选/打招呼→沟通页发送前定位失败→保持未最小化→新会话要求重新筛选。goto 自动恢复或走 CDP 直跳，并验证后续筛选和详情滚轮；另覆盖用户已手动切回推荐页时的接续。未触发持续隐藏时不得称该事故已复现或修复通过。
2. 对实际复现的持续隐藏态，先保存隐藏来源与输入宿主证据，再验证选定方案能完成 native 点击及滚轮；不得把“轻推必须有效”写成验收前提。未恢复时明确失败；切标签/用户重启浏览器仅作待验人工规避，不自动重启或迁移原任务。
3. 既有防护回归：超屏归位、最小化自动恢复（10-24 已验路径）、落点遮挡重试不回退。
4. dismiss 输入返回、关闭后验、原目标合法开卡和后续读图/滚轮分别可核对；不误点无关目标，不把合法开卡误判为弹层循环，部分成果和 unknown 语义保留。

## 9. 遗留未知

- 隐藏来源是 HWND 自身、祖先窗口、原页面失活还是 Chrome 可见性异常；实际接收人工输入的宿主是谁（Chrome 版本与复现条件未定，后续现场补记）。
- 若确实发生换父，中间宿主窗口是谁（日志只枚举 BOSS 主窗子树，未记录完整父链或全进程窗口树）。
- 10:59:18.704–11:06:07.425 无 widget 采样区间内，何时由何事件触发变化；用户已排除人为最小化，未记录的刷新、目标/宿主状态变化仍待证。
- 11:10:13/11:10:36/11:10:57/11:11:38 CDP 浏览点击的精确落点与候选身份未留语义日志；本轮已确认其后均进入 native wheel 并拒绝，开卡后读图受阻的控制流解释明显增强（§12.4）。

## 10. 二次审阅意见与最小验证路线（2026-10-30）

### 10.1 为什么页面正常，代码却持续说隐藏

这里有三个不同对象：网页/合成内容、Chrome 的真实窗口及输入路由、程序选中的 `Chrome_RenderWidgetHostHWND`。本次代码检查的是第三者，不能用结果直接替代前两者的状态。

- **API 语义**：`IsWindowVisible` 检查被测 HWND 及其祖先的 `WS_VISIBLE`，不是“屏幕上是否看得到网页”的像素检测。`false` 无法区分自身隐藏与祖先隐藏；被其他窗口遮挡又不一定使它变成 false。[Microsoft：IsWindowVisible](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-iswindowvisible)。
- **Chromium 窗口职责**：`LegacyRenderWidgetHostHWND` 的头文件将其定义为给无障碍和旧触控板驱动使用的兼容 HWND，尺寸可以覆盖内容区，却不等于页面的实际绘制窗口。页面继续显示，不要求这枚兼容窗口同步可见。[Chromium：legacy_render_widget_host_win.h](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/renderer_host/legacy_render_widget_host_win.h)。
- **隐藏/换父的实现依据**：`RenderWidgetHostViewAura::HideImpl()` 有迁往 `ui::GetHiddenWindow()` 和隐藏辅助窗口的路径；`UpdateLegacyWin()` 会重新挂父、设几何，但是否 Show 受 `host()->IsHidden()` 约束。因此“回到原子树、矩形正常、仍隐藏”在机制上可以解释；它可能来自目标失活或内部可见性状态未同步，不能仅凭几何断言恢复完成。[Chromium：render_widget_host_view_aura.cc](https://chromium.googlesource.com/chromium/src/+/main/content/browser/renderer_host/render_widget_host_view_aura.cc)。

**上述 Chromium 链接为本轮查阅的 main 源码，只用于说明机制，未匹配客户机版本，不能作为该版本存在某个确定 Chrome bug 的证明。** 原生辅助窗口自身的 `OnPaint` 也只进行 BeginPaint/EndPaint；其鼠标处理会向父宿主转交事件，支持“兼容窗口与实际内容/事件宿主应分开看”的解释。[Chromium：legacy_render_widget_host_win.cc](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/content/browser/renderer_host/legacy_render_widget_host_win.cc)。

本项目实际采用更强前提：先找可见 widget，按其矩形换算坐标，再要求 `WindowFromPoint(point) === widget`。这条历史上有效的保护可以阻止误点，但它同时让“没有可见兼容 HWND”的状态必然失败；**native 事件本身尚未投递，故日志不能证明 Chrome 会拒收同位置的真实 OS 输入**。人工操作没有经过这条自定义前置检查，所以两种表现可以共存。

也不能简单删掉可见性检查：`WindowFromPoint` 不返回隐藏或 disabled 窗口，后续等值守卫仍可能失败；更不能保留隐藏 HWND 的旧矩形后直接盲点。[Microsoft：WindowFromPoint](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfrompoint)。

### 10.2 为什么反复恢复没有效果

1. **恢复没有改变阻断前提**：1.2 秒重枚举与 1/2 秒退避主要是等待；抬前台不保证辅助 HWND 或其祖先被 Show。重复激活已经活动的 target 也不等于真正切标签。
2. **处理了另一层问题**：CDP 能观察/关闭页面弹层，不代表 native 所依赖的 HWND 状态恢复；弹层关闭成功之后仍可能死在相同 discover 守卫。
3. **只读开卡不是完整替代路线**：`ResumeBatchReader.ts` 在累计 native 拒绝后允许 fresh 定位同一候选人走 CDP 开卡，但详情读取仍依赖 native wheel。因此“详情可被打开，但读不到/最后仍报隐藏”符合现有控制流，不能据此认定 CDP 点击也失败或开卡是误操作。
4. **尚未验证恢复对象选对**：`boundFind` 依据 debug Chrome PID、类名、BOSS 标题唯一找主窗，`findRenderWidget` 再按可见/面积选择；这不是 CDP targetId 到 HWND 的直接映射。失活页面遗留 HWND、错误标签关联或隐藏祖先仍需排查；多 target 不代表已经证实绑错。

**另一个可直接核对的实现差异**：`VisualRecovery.dismiss()` 的 page 模式走 `session.clickBrowse`，window 模式走 `clickWindow` → `window-experiment.ps1` → `BossWindowExperiment.Click`。后者使用整窗截图像素定位，核对窗口身份/几何、局部图像、前台、光标和 `BelongsTo(hit, browser)`，**不要求 `WindowFromPoint === 某枚 render widget`**。因此整窗恢复原生点击可执行、普通 native widget 路径仍拒绝，在代码上也是相容的；11:11:28 的具体模式还需原回执确认。

这两条守卫各有明确用途，不能直接折中为“统一允许同 PID 输入”：整窗观察有独立坐标和图像校验，DOM 内容点击则需要可靠的页面内容区映射。后续若实验确认辅助 HWND 是唯一阻断，可评估复用既有窗口归属/物理输入原语；不能直接复制整窗路径或据同根窗放开网页所有点击。相关实现：[普通输入守卫](../../clients/shared/win32-native-input/src/physical.ts)、[视觉关闭](../../clients/boss-resume-assistant/src/main/boss/visualRecovery.ts)、[整窗点击守卫](../../clients/boss-resume-assistant/scripts/BossWindowExperiment.cs)。

### 10.3 建议的取证与对照实验

先保留故障态，再逐项改变一个变量。这里只定义后续验证，不表示本轮已执行；无需先部署修复才能开展用户授权的现场诊断。

| 顺序 | 最小取证/实验 | 要回答的问题 |
|---|---|---|
| 1 | 记录 Chrome `Browser.getVersion` 的 product/revision、Windows 版本、显示缩放/屏幕布局；枚举目标进程相关 HWND，读取自身 style、parent/root/owner、可见性和矩形 | 是辅助窗口自己缺 WS_VISIBLE，还是父链隐藏？原 HWND 不在子树时到底去了哪里？不采集命令行凭据或候选人正文 |
| 2 | 在同一时刻核对绑定 targetId、URL、实际活动标签与主窗；对 fresh 内容落点只读调用 WindowFromPoint，记录命中 HWND 类名、PID、父链及客户区映射 | 人工能点的位置实际属于谁？代码选中的辅助 HWND 是否仍代表当前页面？同 PID/根窗本身不能证明落点在网页内容区 |
| 3 | 分别对照等待、仅前台化、真实切走标签再切回、移动、改变尺寸；每个动作前后重新枚举并验证 target/几何 | 哪个动作真正恢复辅助窗口与原生输入？移动和 resize 不能混作同一种已验恢复；正常最小化自愈不能代替持续隐藏复现 |
| 4 | 若辅助 HWND 始终隐藏但原页面实际可交互，先在隔离只读页面验证真实输入宿主与内容坐标的映射，再评估现有输入代理是否需兼容另一宿主 | 是否属于守卫与 Chrome 当前窗口模型不匹配？若考虑新宿主，必须同时验证目标、内容区、DPI、遮挡与输入前几何，不能只放宽为“同进程就允许” |
| 5 | 保存 dismiss 的实际 action、关闭后 inspect 结论、Reader open/read/close 阶段与失败 op，和之后原目标结果对齐 | 是恢复误点，还是合法开卡后 native wheel 受阻？区分主操作零输入、恢复浏览输入与外部业务效果 |

若要继续跟踪 HWND 身份，可在受控实验监听 create/destroy/show/hide/parent 变化；无需先给生产增加长期事件日志。只有同步父链/命中证据，才能把“Chrome 自身粘滞隐藏”和“我们观察/守卫对象不适配”进一步分开。

**实施建议**：先做 goto 窄兜底和现场最小取证；轻推、resize、切标签都作为对照实验。输入宿主兼容分支需要实验支持后另行设计，不能直接削弱现有守卫。现阶段最确定的是“导航异常路径缺兜底、native 前置条件持续未满足”，最不确定的是“哪个 Chrome 生命周期事件造成隐藏、哪个动作能可靠修复”。

## 11. 本机手动对照实验

### 11.1 第一轮：仅切换推荐牛人与沟通（2026-10-30）

用户按“推荐牛人停留 10 秒→左侧沟通停留 20 秒→左侧推荐牛人停留 20 秒”操作后确认完成；助手只读采样，不激活窗口、不注入输入。记录范围北京时间 17:13:31–17:15:16，3,839 次采样，最大间隔 47ms；保存了 38 条状态变化记录。没有主动最小化或刷新步骤。

**结果：未复现持续隐藏，也未观察到切页替换 widget。** 原生选择器全程选中 HWND `18946916`，其自身 WS_VISIBLE 与 IsWindowVisible 均为 true，父窗保持 `72660`；枚举到的 widget 句柄集合、父链及可见性组合未变。另两个大尺寸隐藏 HWND `32119484`、`14556052` 仍存在，但选择器没有误选它们。BOSS 前台记录中，采样内容区中心的 WindowFromPoint 命中 `18946916`。

这轮不支持“正常推荐→沟通→推荐必然销毁旧 widget、而程序仍缓存旧句柄”的解释；当前实现每次输入重枚举，本机该路径下能找到可见对象。它不能排除其他 Chrome 状态、失败发送过程、后台停留或长时间间隔共同触发事故。

**验证限制**：Chrome 进程命令行标记端口 19089，但该端口未能取得 CDP 响应；未重新启动浏览器。切页与等待由用户确认，没有独立 URL/target 时间线，亦未采到 Chrome 版本。轮询不能排除短于采样间隔的变化；本轮未实际执行 native 点击/滚轮或读取简历，不把窗口状态正常写成业务全链路通过。

脱敏摘要：[manual-switch-round1-summary.json](evidence/boss-widget-stuck-hidden-20261030/manual-switch-round1-summary.json)。原始无页面正文的 HWND 记录与只读采样脚本保留于本机 `.tmp/boss-widget-switch-20261030/`；本轮结果属于对照实验，不替代 agent2 事故取证。

### 11.2 第二轮操作与采样时段错开（2026-10-31 核对）

第二轮拟验证“沟通页→后台停留 2 分钟→返回推荐牛人”。原只读采样于 10 月 30 日北京时间约 17:16:26 启动、17:31:26 按 15 分钟上限自动结束，共 29,324 次采样；该时段未记录 widget 选择、父链或可见性的变化。

用户于 10 月 31 日明确确认第二轮是当天才操作，故**昨天数据未覆盖第二轮动作，该轮不能登记为通过或未复现**，也不能用其全程可见来否定后台停留触发机制。原记录保留于 `.tmp/boss-widget-switch-20261030/manual-round2.jsonl`，与第一轮证据分开使用。

10 月 31 日 14:24:29 的追加只读快照显示，浏览器 PID `49296`、主窗 `72660`、选中可见 widget `18946916` 仍与第一轮相同，其父窗为 `72660`；这是用户第二轮操作后的单点状态，不能还原操作期间是否短暂隐藏/换父。CDP 端口仍未响应。快照保留于 `.tmp/boss-widget-switch-20261030/current-20261031.json`，不将其表述为已覆盖完整第二轮。

### 11.3 第二轮重做：后台停留与返回观察（2026-10-31）

重做采样于北京时间 14:24:42 启动，用户在采样运行期间确认操作完成，14:38:55 停止。共 27,812 次采样、25 条状态变化记录，最大采样间隔 33ms。该次未自动激活/移动窗口、投递输入或执行业务动作。

**已观察到的结果**：选中 widget 始终为 `18946916`，自身 WS_VISIBLE 与 IsWindowVisible 均为 true，父窗始终 `72660`；枚举到的 widget 句柄集合、父链及可见性组合未变，没有发现新增可见 widget 被选择器遗漏，也没有记录最小化。BOSS 前台时采样内容区中心的 WindowFromPoint 始终命中 `18946916`。原两个大尺寸隐藏 widget 仍隐藏，选择器未回退到它们。

**前台时间线与验证边界**：采样初始至 14:35:46，前台为另一窗口；14:35:46–14:38:48 为 BOSS 主窗，之后切回另一窗口。后段约三分钟 BOSS 前台期间，没有记录中间的“BOSS 前台→Codex 前台→BOSS 前台”转换。用户已确认重做完成，但不能仅凭该记录独立确认“先进入沟通页、再在后台等待两分钟、再返回推荐页”的精确顺序；开始处较长的非前台时段也没有 URL 佐证当时页面。CDP 仍不可用，本轮未采集页面内容或版本。

**结论范围**：这段实际采样未复现持续隐藏、换父或 widget 替换，未取得支持“切页或非前台停留本身必然导致旧 HWND 失效”的证据；不能据此排除事故现场的失败发送链、特定标签状态或其他组合触发。尤其不能登记为“精确后台沟通场景已验通过”，也未验证 native 点击/滚轮或完整筛选业务。

脱敏摘要：[manual-switch-round2-retry-summary.json](evidence/boss-widget-stuck-hidden-20261030/manual-switch-round2-retry-summary.json)。原始记录为本机 `.tmp/boss-widget-switch-20261030/manual-round2-retry-20261031.jsonl`。两轮均未复现后，后续优先检查原事故发送失败前后的 target/原生窗口关联与动作日志，避免仅靠重复手动切页将未复现当成已定位根因。

### 11.4 普通刷新：捕捉到 widget 替换，未复现持续隐藏（2026-10-31）

用户按“沟通页普通 F5 刷新，再返回推荐牛人”的指引操作并确认完成。只读采样覆盖北京时间 **14:47:25.509–14:48:19.298**，共 1,755 次循环采样、21 次状态变化，另保存停止时最终状态，共 22 条记录；最大采样间隔 35ms。采样器不激活/移动窗口、不投递输入。

| 北京时间 | 旧 widget 18946916 | 新 widget 60824230 | 同一选择器的结果 |
|---|---|---|---|
| 刷新前 | IsWindow=true、visible=true、父窗 72660 | 尚未被枚举到 | 18946916，可见 |
| 14:47:54.939 | 仍有效且可见 | 首次记录到，有效但自身 WS_VISIBLE=false，父窗 72660，内容矩形与旧对象相同 | 仍选旧的可见对象，不误选新隐藏对象 |
| 14:47:55.036 | **IsWindow=false**，不再是有效窗口，非仅隐藏/换父 | **visible=true、own_ws_visible=true**，父窗仍 72660 | **已改选 60824230**，采样内容区中心也命中新对象 |
| 14:48:19.298，停止时 | 仍无效 | 仍有效且可见 | 60824230，可见 |

主窗 HWND `72660`、浏览器 PID `49296` 全程保持，未记录最小化。新 widget 从首次记录为隐藏到首次记录为可见，两个状态时间戳相隔约 97ms；这是轮询观测差，不等于精确的创建/隐藏持续时长。所有已保存状态的 selected_visible 均为 true：新对象尚隐藏时旧对象仍可用，随后旧对象失效、新对象可见，选择器没有继续引用已失效旧 HWND。

**结论**：这次刷新确实伴随 widget 替换，用户关于“刷新可能产生新 widget”的猜测有本机实证；也验证了重新枚举能适应这次正常替换。事故与本次实验仍有关键差异：事故后续没有记录到新可见候选，而是原大 HWND 同值重现并持续 hidden；本次则旧 HWND 失效、新 HWND 可见并被正常选中。因此不能将“普通刷新更换 HWND”直接定为事故根因；若原事故确由刷新触发，还需证明额外的可见性/目标关联异常。

**验证限制**：CDP 仍不可用，F5 动作与业务页切换以用户操作确认，未取得 loaderId、target 或精确按键时间；无独立页面正常显示/菜单结果记录，不将“好了”扩展为完整业务验收。单次本机对照不能排除其他 Chrome 版本、强制刷新、后台刷新或失败发送后状态的差异。采样不是原子事件追踪，不能排除短于采样间隔的隐藏；本轮未执行产品的 native 点击、滚轮或筛选业务。

脱敏摘要：[manual-refresh-summary-20261031.json](evidence/boss-widget-stuck-hidden-20261030/manual-refresh-summary-20261031.json)，含关键新旧对象状态、原始记录 SHA256 与采样统计。原始 HWND 记录和分析脚本保留于本机 `.tmp/boss-widget-switch-20261030/`，采样已停止。

## 12. 发送失败前后的原日志复核（2026-10-31）

### 12.1 来源与复核范围

本轮在本机 Downloads 找到客户机 `runtime(2).log` 完整副本，共 9,375 行，SHA256 为 `48360eb92c153cdfe9f83001f748f51facba93d043adbcf077aa5db5966cd4e1`；其 L7683–7686、L7941 等与原脱敏节选对应。重新核对 UTC 02:58:29–03:11:41（北京时间 10:58:29–11:11:41）的 CDP 连接、物理输入及枚举记录。未查询远程 agent2、未访问客户机或重新发消息。

新增证据：[结构化摘要](evidence/boss-widget-stuck-hidden-20261030/send-failure-target-hwnd-review-20261031.json)、[带原始行号的脱敏节选](evidence/boss-widget-stuck-hidden-20261030/send-failure-target-hwnd-excerpts-20261031.redacted.log)。分析脚本与中间报告保留在本机 `.tmp/boss-widget-incident-review-20261031/`。只提取协议方法/阶段、session 摘要、原生几何及搜索歧义，不复制消息正文、候选隐私或凭据。

### 12.2 发送失败并非隐藏守卫先触发，健康边界向后修正

| 北京时间 | 原日志行号 | 可直接确认的事实 |
|---|---|---|
| 10:58:31–10:58:34 | L7683–7693 | greet 时 widget 69212272 可见，原生 click 成功；这不是最后健康采样 |
| 10:59:18.042–10:59:18.216 | L7697–7712 | send-to 建立 CDP attach，页面 session 摘要为 1f03ab8ab6；两次 DOMSnapshot 与 activateTarget 均 ACK |
| **10:59:18.704–10:59:19.625** | **L7715–7716** | 同一主窗 4662058、PID 33272 下，widget **69212272 visible=1**；随后 native **click ok=1** |
| 10:59:21.134–10:59:21.747 | L7717–7727 | 仍在同一 CDP session 内读取快照、执行 Escape 后再读快照，最后报搜索入口 42 个候选歧义；没有 clickAndType 或消息发送步骤 |
| 10:59:39–11:01:03 | L7752–7904 | 原目标续做及两次 open-chat 同样以搜索定位歧义失败，期间反复 attach/观察，没有新的 widget 枚举 |
| 11:01:07.974–11:01:08.134 | L7909–7924 | goto(chat) 仅 attach、DOMSnapshot/URL 查询、detach 后成功并 ACK；没有物理输入，不能证明此时 widget 仍可见 |
| **11:06:07.425** | **L7941–7962** | 新会话 goto 的第一次枚举只见两个隐藏小 widget，仍是主窗 4662058、PID 33272；七次探测后输入拒绝 |

按当前 `bossSendTo → ensureChatPage → PageNavigator` 与 `ChatOpenExecutor → ChatSearchExecutor` 的控制流，以及原日志快照/URL/点击的先后顺序对照，send-to 内唯一成功的物理点击对应进入沟通页的菜单导航，之后才在搜索入口定位阶段失败；这是源码与日志时序支持的解释，不是鼠标坐标日志直接标明的动作语义。搜索歧义发生在打开联系人搜索及输入消息之前，不能将它解释为“消息已投递但因隐藏而失败”。

**可见性变化只能限定在 10:59:18.704–11:06:07.425，跨度 408.721 秒（约 6 分 49 秒）**。中间仍有工具/观察，故原文“10:58–11:06 八分钟无调用”不成立；11:01:08 后至 11:06:06 才是约五分钟无新工具调用，期间只有上一调用的结果 ACK。没有中间 HWND 探测，不能定位成“搜索报错瞬间隐藏”，也不能排除它在发送失败后的恢复或空闲期间才变化。

### 12.3 target、CDP session 与 HWND 的关联能证明到哪一步

**Win32 端**：本轮区间的枚举记录只有一个主窗 HWND `4662058`、一个浏览器 PID `33272`，且只有 `34676444`、`598126`、`69212272` 这组三个 widget 数值。主窗没有记录到替换；原大 widget 后来同值重现且隐藏。没有“新可见 widget 已出现、选择器仍缓存旧 HWND”的正证据。不过日志只遍历原主窗子树，没有全进程 HWND 树、父链或生命周期事件，仍不能排除有效 widget 去了其他宿主。

**CDP 端**：区间内按每次 `id=1 stage=send` 划分出 48 个连接段，每段均有一次 attach 与一次 detach；共 493 次 send、493 次 ACK、0 次 timeout，未出现 Page.navigate。`session=` 是 `SHA256(sessionId)` 的前十位，来自 `CdpSocket.send()`，**它不是 targetId，也不是 widget 身份**。例如 send-to 的 1f03ab8ab6、恢复续做的 565a5e2c3f、11:01 goto 的 e43d8ee4c8、11:06 goto 的 1373ead119 各处于重新 attach 的连接段；不能把摘要不同解读成切换到新 target。`close()` 的 detach/关闭 socket 也是既有连接收尾，日志没有表明它关闭了网页。

首次 send-to 的导航点击前后使用同一 CDP session，期间没有再次 attach，支持“此次进入沟通页到搜索定位失败，没有在本连接内重绑另一 target”。**跨连接是否仍为同一 target 无法复核**：事故当日日志没有输出 targetId、browser_target_ref、browser instance，也没有 Target.getTargets 的响应列表或 target→HWND 映射。ACK 只证明该协议请求成功返回，不能替代“激活的 target 正是用户眼前那一页”或“辅助 HWND 已完成显示”的证据；未记录 target 生命周期事件也不能证明它们未发生。

当前 `defaultSessionFactory` 每次建立新 gateway；有 binding 时按已存 target attach，无 binding 时 `attachToRecommendPage()` 按业务页优先级及原返回顺序选 BOSS 页。它并不直接用 HWND 证明 target 归属；Win32 侧则按调试 Chrome PID、BOSS 主窗及可见 widget 独立选择。因此“同一主窗中有多个 BOSS target，新会话或恢复时选了另一个”仍是一条需验证的路径，**尚无日志证明现场存在多个 target、缺少 binding或实际选错**。本轮源码对照未对事故安装 payload 做逐文件摘要比对，不以当前实现细节冒充客户机完整版本验收。

### 12.4 原日志确认：开卡后受阻的是滚轮，不能再笼统记成点击失败

| CDP 鼠标按下/释放时间 | 同连接随后失败的原生步骤 | 原日志行号 |
|---|---|---|
| 11:10:13.761 / 13.767 | 11:10:16.330，**op=wheel ok=0** | L8837–8846、L8874 |
| 11:10:36.764 / 36.793 | 11:10:39.407，**op=wheel ok=0** | L8968–8973、L9005 |
| 11:10:57.134 / 57.141 | 11:11:00.517，**op=wheel ok=0** | L9163–9168、L9202 |
| 11:11:38.519 / 38.520 | 11:11:39.996，**op=wheel ok=0** | L9301–9306、L9338 |

这些输入的 mousePressed/mouseReleased 均 ACK，之后仍能取得 fresh DOMSnapshot，再进入 native wheel 并在 discover 阶段拒绝。结合 `ResumeBatchReader` 多次 native 拒绝后使用 CDP 浏览开卡、`phase=read` 调用 native wheel 的路径，**“只读开卡兜底已进入详情读取阶段，滚动依赖仍被同一守卫卡住”比“开卡根本没成功”更符合现有证据**。仍不能仅由 ACK 声称点中了哪位候选人或关闭后验已满足。

特别是第二行发生在 dismiss 与 inspect 之后，解释了为何用户看到详情再次打开：这是继续读取简历所需的开卡动作，未证明它属于误点；原文“关了又开造成失败”应降为待验，不能据此新增永久互斥或重复增加已有 postcheck。前两次原生开卡点击确实被拒，但后续失败 op 已切换为 wheel；外层 effect=none 也不等于整个恢复过程无页面输入。

### 12.5 缩小后的结论与剩余取证

1. **两个已定位问题应分别处理**：首次发消息卡在搜索入口定位歧义；后续新会话卡在 native widget 前置条件。发送切页时原生点击还能成功，不能将两者直接归为同一次“发送触发 widget 隐藏”。搜索歧义仍可能发生于异常页面/旧弹层上，未完成 DOM 与画面取证前不判定其更底层原因。
2. **新会话本身不是已证实触发器**：已记录主窗与 PID 未变、没有窗口级重新定位到另一实例的证据；是否重新选择了别的 BOSS target，原日志缺少身份字段，必须保留未知。现有证据更适合描述为“同一观测主窗的可用内容 widget 丢失/隐藏，CDP 通道仍可读可浏览”。
3. **后续恢复的确定缺口是 native wheel 仍必需**：CDP 开卡不能解决详情滚动，对 goto 增加 CDP 直跳也不能单独闭环筛选/读简历。没有依据直接删除可见性或命中守卫，也没有依据把轻推 1px 当确定修复。
4. **如需继续追根，最小补证位置是 attach 与输入拒绝处**：保留同一 browser instance 内稳定的 target 摘要、URL pathname、原 binding 及主窗/PID/widget/父链/实际命中，才能把 CDP 目标和原生宿主对齐；不记 URL query、联系人或消息正文。优先检查原客户机若仍保留的当次 binding/视觉 record（target_id、observation mode 等），仅有 runtime 日志无法补造这段历史。若需新增日志再进入开发流程，本轮不实施代码或部署。

### 12.6 用户补充假设：期间有人刷新页面（2026-10-31）

**该假设不能由现有日志排除，值得与页内菜单切换分开验证。** F5/Ctrl+R 的整页重载与 BOSS 左侧菜单的页内切换不同：前者重新加载文档；但文档、RenderFrameHost、RenderWidgetHost/View 与 Win32 辅助 HWND 是不同层次，**文档更新不意味着 HWND 必定变化，也不保证 HWND 保持不变**。是否复用或重建渲染视图需要现场观测，不能从 RenderDocument 创建新 RenderFrameHost 直接推导为“必定创建新 Chrome_RenderWidgetHostHWND”。机制区分参考 [Chromium renderer_host 概念](https://chromium.googlesource.com/chromium/src/+/main/content/browser/renderer_host/README.md)与 [RenderDocument](https://chromium.googlesource.com/chromium/src/+/main/docs/render_document.md)；未匹配客户机版本，不据此断言现场走了哪条重建路径。

**已有记录的含义**：10:58–11:06 区间没有 Page.reload、Page.navigate 请求，也没有 frameNavigated/frameStartedNavigating 等事件记录，但这不证明没有人工刷新。F5、Ctrl+R 或浏览器刷新按钮不需要经由本工具的 CDP 请求；`CdpSocket` 当前日志主要记录自身请求 send/ACK，接收事件只转发给监听器，不自动落日志，相关区间也没有 Page.enable 请求。因此本轮能排除的是“日志中记录到工具主动派发刷新”，不能排除浏览器 UI 或网页自身发起重载。

主窗 HWND/PID 未变、旧 widget 同数值重现，均不能单独否定刷新：同一标签内文档重载不必关闭整个浏览器主窗，widget 也可能被复用。反过来，事故中一度枚举不到大 widget、后来同值 hidden，也不是刷新发生的专属证据；最小化、失活或其他生命周期路径仍可能产生类似现象。即使实测刷新导致 widget 更换，现有输入每次重新枚举，**仍需解释为何新可见 widget 没被找到或没有恢复**，不能将“换 HWND”直接当成持续失败的完整根因。

**单变量验证与后续补证**：普通 F5 已按 §11.4 完成一次本机 HWND 对照，捕捉到新旧对象替换且选择器恢复到新可见对象，未复现持续隐藏。若要独立核实整页重载及其 target 归属，仍应结合主 frame 的 loaderId 与导航事件；CDP `Page.frameStartedNavigating` 的 navigationType 在支持的版本中可区分 reload/reloadBypassingCache，`frameNavigated` 表示关联新 loader；仅 URL 相同或 target 未变不够。协议参考 [CDP Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/)。当前本机 CDP 不通，人工刷新与 HWND 对照不能补回事故历史的刷新证据；不强行启用未支持的事件或修改协议白名单。

## 13. 现场约束更正与修复收敛方向（2026-10-31）

### 13.1 人工操作是排障响应，不作为首次异常的默认归因

用户明确表示本次不可能人为最小化，手动操作是因 agent 未按指令执行、怀疑页面卡住而进行。此前从无采样空窗推定最小化，缺少证据，现已撤回；10-24 的最小化实验仅是另一个已验恢复场景，不得继续代替本次事故解释。

已定位到的手动切回推荐页约为 11:07:31，晚于 11:06:07 的首次隐藏枚举，所以**这次已记录的手动切页不是首次隐藏的起因**。后续移动也未恢复可见性，不能把排障动作与故障结果的共存写成用户造成故障。其他手动动作未精确打点，暂不补写其具体时间或操作。

### 13.2 当前已知的工程缺口与触发原因分开处理

本轮已经读取本机 `C:/Users/PC/Downloads/runtime(2).log` 的完整 9,375 行副本，源文件摘要见 §12.1。该记录能定位“哪里拒绝、何时仍健康、恢复走到哪一步”，但没有关键时段的 target 身份、父链、frame/loader 变化；不能通过反复解读同一份日志还原未曾记录的事件。若用户另有同次运行的更完整日志或附属记录，应按时段和源摘要区分，不以当前本机的新日志替代事故日志。

目前无需等 Chrome 内部触发原因全部查清，便可明确以下修复目标；它们仍需设计、实现与独立验收，**本轮尚未实施**：

| 缺口 | 可审查的修复方向 | 完成证明 |
|---|---|---|
| goto 的认证零输入拒绝绕过既有 CDP 导航兜底 | 复用原绑定与剩余预算接入窄兜底，保留导航后验 | 失败分支确实执行一次导航；后续原生输入能力单独核验 |
| 普通输入只接受可见 legacy widget；页面内容仍可交互时没有经验证的替代宿主 | 先对齐原 target、实际命中窗口与内容坐标；评估复用已有整窗路径的身份/几何/前台/光标守卫来支持真实输入 | 在辅助 HWND 不可用但内容可交互的受控场景完成真实点击和滚轮；错误 target、遮挡或坐标失效仍拒绝，不能只用 fake 返回成功代替实证 |
| 恢复能 CDP 开卡，却仍卡在 native wheel，让用户看见反复打开同一详情 | 领域恢复区分 open 与 read/wheel 阶段；不能把“关闭弹层/页面无遮挡”当成原生滚轮已恢复 | 既有预算内接续并保留已读成果；未恢复时明确落在滚轮阻断，不再将合法开卡误诊为用户未关弹层 |
| 故障时只记录孤立 HWND 与临时 session 摘要，不能关联真实目标 | 在 attach 和输入拒绝处增加有限脱敏关联信息，优先利用原客户机现存记录 | 一份故障证据能回答原 target 是谁、内容落点命中谁、widget 隐藏在自身还是父链；不采集消息/候选正文或凭据 |

已有整窗点击按 `BelongsTo(hit, browser)` 守卫，普通输入按 `WindowFromPoint === widget` 守卫，二者采用不同的坐标与观察证明。前者提供可复用原语，但不能直接复制成“同 PID 都可点”，也不能未经验证删掉后者。故障触发未知不意味着可以盲放输入，反过来也不应把等待 Chrome 自愈当作唯一产品恢复策略。

### 13.3 下一步取证聚焦原客户机的关联记录

用户已明确确认，所持日志就是本轮已完整读取的 `C:/Users/PC/Downloads/runtime(2).log`，不是另一份待分析日志，不再要求重复提供。该文件缺少 §12 所述关联字段；下一步补证对象是原客户机附属记录，而非重复解读同一文件。客户机若仍保留 `%TEMP%/aid-boss-targets` 的当次 binding，以及 `%TEMP%/aid-boss-visual/<request_id>/record.json` 的视觉恢复记录，其中 target_id、browser_instance、observation mode 可能补足跨调用身份或整窗点击模式；它们不是运行历史事件流，当前文件也可能已变化，不能仅凭今天的内容推出事故时刻事实。只提取必要字段和时间，不能让用户打包整个包含设备凭据的 Runtime home。

取得额外记录或明确日志缺口后，再把输入宿主兼容的最小方案同步到原设计/开发计划并按开发流程实施，不新增一轮与原事故条件无关的最小化/切页实验来替代修复。当前责任仍在产品恢复与证据链完善，不归咎用户正常操作。

## 14. 客户机只读取证附件（2026-10-31）

交付与验证登记：[专项计划](../plans/plan-boss-widget-incident-evidence-collection.md)。附件源为 [采集脚本](evidence/boss-widget-stuck-hidden-20261030/collect-boss-widget-evidence.ps1)、[双击入口](evidence/boss-widget-stuck-hidden-20261030/run-collect-boss-evidence.cmd)、[客户说明](evidence/boss-widget-stuck-hidden-20261030/取证说明.txt)。

用户只需在**原故障电脑、原 Windows 用户**下解压附件并双击入口，返回同目录生成的 `boss-widget-evidence-*.json`。不用重新制造故障、操作 BOSS 或重启 Runtime。采集仅投影 BOSS 临时绑定及 10 月 30 日 10:40–11:20 的恢复记录，不读取 Runtime 配置，不联网；源文件不修改，输出排除图片、姓名、消息、Cookie、设备令牌、自由文本诊断及原始本机路径。只返回生成的 JSON，不打包原缓存或 Runtime home。

历史目录不存在或记录不在时段内会在报告中明确计数。这种情况只能确认历史证据不足，不能把取证工具运行成功当成故障已经定位或修复。程序本身不接入产品启动、执行或更新链路，本轮也没有安装或部署产品。

附件已完成主控自测、独立23项检查及独立CR，结果与1项文件符号链接环境跳过记录在专项计划；客户实际执行结果已回收并核验，见 §16。最终ZIP保留本机 `.tmp/BOSS-incident-evidence-kit-20261031.zip`，只含3个采集附件，不含原事故日志或客户数据。

## 15. 后续必须内置到Runtime的诊断能力（2026-10-31）

用户明确要求：后续在Runtime客户端内补齐日志采集/导出，降低客户取证成本；本轮可暂不开发，但不能遗漏或以当前脚本交付代替。已更新[Runtime唯一设计第14节](../system/runtime-plugin-host-architecture-design.md#14-客户端内置诊断留存与一键导出2026-10-31待开发)及[统一开发计划第14节](../plans/plan-runtime-plugin-host.md#14-内置诊断留存与一键导出开发计划2026-10-31)，并在ideas单独登记为待开发必做项。

方案同时包含默认故障关联元数据留存与客户端一键导出：补齐target/binding/session、HWND/父链/可见性、实际click/wheel阶段和恢复后验，事后按时间导出脱敏ZIP。复用core日志/home定位与公共客户端，不依赖客户手工找TMP目录；离线/Host异常仍能导出已有证据，历史缺失如实报告。具体范围、保留上限、隐私、兼容及真实客户端验收以Runtime设计/计划为准。本轮仅规划，未实施客户端代码或部署。

## 16. 客户回收元数据：同一目标，整窗原生点击可用（2026-10-31）

### 16.1 来源、完整性与证据边界

用户返回 `boss-widget-evidence-20261031-152140-7cec3b.json`，40,532 字节，SHA256 `3961a1a305695c33c539aba3863c161e433f39eef348e11a687d37ef3dd797bc`；采集时间为北京时间 2026-10-31 15:21:41。摘要及按时间排序的白名单记录见 [customer-metadata-review-20261031.json](evidence/boss-widget-stuck-hidden-20261030/customer-metadata-review-20261031.json)。没有采集或提交原始图片、候选资料、消息或设备凭据。本轮未操作 BOSS、重现故障、修改产品或部署。

报告解析 2 份 binding、23 份视觉记录，其中 22 份在事故时间窗，1 份被时间窗排除；解析错误、无效时间及 issues 均为 0。22 个 request_id 唯一，所有视觉响应 `http_status=200, visual_state=completed`。这些只证明采集和视觉响应完整，不能当成业务工具执行成功。

### 16.2 跨会话 target/binding 未见替换，整窗身份保持一致

| 关联字段 | 事故观察结果 |
|---|---|
| target_id | 22/22 均为 `1686731FD02BF5B38AF6D1ED1221884C` |
| browser_target_ref | 22/22 均为 `ed71990c-edbe-4877-adfc-e1f7a20aa607` |
| 整窗 HWND / PID | 10/10 均为 `4662058 / 33272` |
| 整窗进程启动时间 | 10/10 均为北京时间 10 月 30 日 10:48:21.1624868 |
| 整窗矩形 `[left,top,width,height]` | 早期 `[244,8,1676,972]`，后期 `[211,5,1676,972]`；与原日志内容区域左移 33px、上移 3px 相符 |

观察覆盖 10:49:55–11:11:30，包括 10:59:23 send-to 失败后的 page 观察、10:59–11:00 open-chat 恢复、11:06 goto 恢复、11:07–11:09 筛选失败及后续读简历恢复。pathname 从 `/web/chat/index` 变为 `/web/chat/recommend`，同一 target/binding 保持不变，符合用户手动切回推荐页的反馈。10 月 27 日另一份 binding/target 未出现在这 22 条观察中。

因此 §12 基于 runtime 单文件保留的“跨调用可能选择另一 BOSS target”已获补证：**已保存的恢复点没有切换到另一 target，也没有重启浏览器进程或换主窗的迹象**。不能将不同 CDP session 摘要理解成不同标签；也不能继续以“新会话误绑旧标签”作为当前主解释。恢复观察是离散点，不覆盖空白区间，仍不能排除短暂切换后返回。整窗记录也不是 target 到实际内容命中 HWND 的完整映射。

事故 binding 文件最后修改于当天 13:47:01，晚于故障；其 browser_instance、page_path、restore_pending 是采集时保存状态，不能单独倒推事故时刻。上述跨调用身份结论主要依据当时保存的视觉 record，而非这份晚修改 binding。page 模式中的窗口字段为空、geometry_epoch 为空，也不能解释成窗口消失或取证解析失败。

### 16.3 两次关闭的后验均满足，第二次确为整窗原生路径

| 北京时间 | 新旧证据联结 | 可确认含义 |
|---|---|---|
| 11:10:18.042 | page diagnose，遮挡 present，动作 dismiss，request `9f124141…` | 恢复判定有待关闭遮挡 |
| 11:10:28.316 | runtime L8926，dismiss `ok=1`，含 `cdp:clickBrowse` | 首次关闭经 CDP 页面路径 |
| 11:10:28.934–11:10:33.775 | page verify，request `865c349e…`，`stage=postcheck, absent/satisfied` | fresh inspect 认定遮挡消失，关闭目标已满足 |
| 11:10:39.407 | runtime L9005，native wheel `ok=0` | 续读仍受普通原生输入阻断 |
| 11:11:13.623 | window diagnose，遮挡 present，动作 dismiss，request `d231b771…`，同一主窗/进程 | 第二次恢复使用整窗观察 |
| 11:11:28.114 | runtime L9263，dismiss `ok=1`，含 activateTarget，无 clickBrowse | 结合原观察模式与代码分支，为整窗原生关闭输入 |
| 11:11:30.177–11:11:34.451 | window verify，request `213cec0d…`，同一主窗，`stage=postcheck, absent/satisfied` | 第二次 fresh inspect 同样认定关闭目标满足 |
| 11:11:38–11:11:39.996 | CDP 浏览开卡后，runtime L9338 native wheel `ok=0`；大 widget 仍 visible=0 | 恢复后继续开卡读取，滚动再次在投递前被拒 |

代码核对：`visualRecovery.ts` 根据原 record 的 window 模式重新激活 target、确认窗口身份/几何和当前 URL/target，再调用 `clickWindow()`；`windowExperiment.ts` 启动原生脚本，`BossWindowExperiment.cs::Click()` 在几何、前台、光标及命中窗口归属守卫后调用 `mouse_event`。本路径没有“未输入但成功返回”的关闭分支。故“第二次只是截图成功”“两次关闭都只是 CDP”均不符合当前关联证据。源码与事故 payload 未逐文件比对，结论按原日志、record 和当前对应实现联合判断，不冒充完整版本复验。

**这里证明的是：现场整窗原生点击路径至少成功执行过一次，并有关闭后验；普通原生滚轮随后仍拒绝。** 两者并非同一输入宿主选择与坐标证明：整窗使用窗口图像坐标与受约束的窗口归属命中，普通路径依赖可见 widget 及 `WindowFromPoint === widget`。不能把普通路径拒绝解释为整台电脑或 Chrome 完全无法接收真实鼠标输入。点击瞬间没有同步 widget/父链采样，不能进一步声称“测得该点击瞬间辅助 HWND 必然隐藏”；视觉后验也不是原始读简历业务已完成的证明。

### 16.4 修复优先级与仍未证明的原因

以下为代码开发前的取证判断；用户随后授权实施，最新开发与验证结果见§17。

1. **优先验证普通输入宿主兼容**：§13.2 中“评估已有整窗守卫原语”的方向已获得客户现场支持。下一步以同一 target 的内容区域为边界，验证可靠的内容坐标到屏幕映射及实际命中归属，覆盖筛选点击和详情滚轮；保留身份、几何、遮挡与输入结果未知处理。关闭按钮使用整窗截图坐标成功，并不自动证明任意 DOM 坐标可直接平移后安全点击，也不证明滚轮已可用。
2. **goto 窄兜底仍有价值，但不是完整修复**：零输入拒绝时可按原方案尝试 CDP 导航；普通 click/wheel 没有恢复，仍无法完成筛选和读简历。不能用一次 goto 成功代替闭环验收。
3. **恢复记录应准确区分关闭与续读**：两次遮挡关闭已经后验满足，之后开卡是在继续原目标，确定失败步骤是 wheel。应避免让用户重复关闭已被恢复链处理的遮挡，或持续重试同一无法滚动的详情；不能永久禁止续读开卡，也不重复新增已有 fresh inspect。
4. **首次隐藏的触发事件仍未记录**：本文件不能回答自身 WS_VISIBLE/祖先隐藏、父链变化、短暂 target 激活或人工刷新是否发生。刷新可以保留 target/主窗/PID，故本次 target 一致也不能排除刷新；但它进一步削弱了“单纯沿用已失效旧标签/旧进程”解释。原生输入是每次重新枚举，仍无“缓存旧 widget 不更新”的正证据。

本轮成功条件是核实客户附件、补齐跨调用身份与关闭动作证据、缩小修复范围，已完成；**产品故障尚未修复，辅助 HWND 隐藏的底层触发原因仍未知**。Runtime 内置诊断计划继续保留 target 生命周期、父链/自身可见 style 与实际命中证据的待开发要求。

### 16.5 用户设计目标复核：观察定位与 Windows 输入分工

用户再次明确，采用 Win32 的目的就是模拟人在桌面上的鼠标和键盘操作，CDP 负责观察与定位。该目标应作为后续修复基线：确认当前业务目标的屏幕位置后，经 Windows 输入流完成点击、滚轮及键入；Chrome 内部辅助 HWND 的可见性不应成为独立的业务准入条件。

本轮源码核对表明，当前普通路径把同一辅助 widget 同时用作三件事的依据：`screenPoint()` 的 CSS→屏幕坐标参照、`isWindowVisible(rw)` 的输入准入条件、`WindowFromPoint === rw` 的落点归属条件。这是对 Chrome 内部窗口模型的过强依赖。现场普通路径的 hidden 错误由程序在投递前主动抛出，不是 Windows 鼠标输入 API 报“无法点击”。将辅助窗口隐藏等同于页面不可操作，是这处守卫的设计依据错误；不断等待或恢复辅助 HWND 会延续该依赖。

必要的检查应直接服务于“此刻确实点中预期对象”：当前 target 对应眼前页面、页面内容坐标能准确映射到物理屏幕、落点未被其他窗口覆盖。对于已经可靠确认的屏幕落点，不应再要求某个指定辅助窗口类名可见或命中 HWND 必须等于它。键入仍需确认焦点，业务完成仍需结果核验，但无需让这些要求重新依赖 legacy widget。

这也说明不能只删除 hidden 分支：当前坐标计算和后续命中校验仍绑定该 HWND，单删一条检查可能继续拒绝或使用失效参照。最小完整修复应同时纠正坐标参照与命中判断，移除“辅助 widget 必须可见”这一业务前提，复用已有 Windows 输入与整窗定位原语，不新增一层层 widget 恢复机制。该段是设计判断及修复目标，尚未实施代码，也不宣称任意已知 DOM 坐标无需换算即可直接用于屏幕输入。

## 17. 内容区物理输入修正已开发，客户现场待验（2026-10-31）

用户授权按§16.5方向修正并走开发流程。先核实必要前提：原日志L7933–7965的goto、L8321–8358/L8568–8605的筛选探查均已取得快照、成功定位并把合法页面坐标交给native click，才在discover因hidden拒绝；详情滚轮同样已取得区域。没有历史x/y和像素，不能追认旧屏幕映射一定正确，但不能再把本次失败解释为CDP完全无法读元素位置。首次send-to搜索入口42候选歧义仍独立保留。

已更新[原设计§10.11](../design/recruiting/boss-resume-assistant-native-cdp-design.md#1011-解除物理输入对-legacy-widget-的依赖2026-10-31)及[原计划§16](../plans/recruiting/boss-resume-assistant-native-cdp-dev-plan.md#16-普通物理输入内容区定位修正2026-10-31)。普通点击、滚轮、点击后键入及拖动以UIA唯一顶层Document物理内容区映射屏幕位置，不再枚举、等待或要求legacy widget可见；保持原target激活、视口新鲜度、内容边界、主窗根命中、光标/几何及键盘焦点检查，未新增恢复机制或改用CDP输入。

隔离Chrome主动隐藏全部legacy HWND，**第一次UIA读取之前即隐藏**，投递前/后fresh枚举排除新建可见widget假通过；菜单/筛选点击、中文emoji键入和真实滚轮均生效。独测deviceScale1.5及真实浏览器缩放后同样完成，拖动以网页收到按键态pointermove核验。native54、BOSS相关153及独立反例10项通过；独立CR三项P2已修正复核，无未解决P0/P1/P2。完整范围与原始失败修正登记见计划；[持久验收摘要](evidence/boss-widget-stuck-hidden-20261030/content-input-validation-20261031.json)关联最终源码hash。

全量npm test未全绿：5项失败通过隔离HEAD A/B确认原已存在，MCP并发用例的清理挂起也在两侧复现；相关默认工厂夹具因新增视口读取的失败已修正并回归。不将局部通过写成全量通过，不扩展本轮修理旧清单或祖先滚动问题。

**当前结论**：辅助widget隐藏不再是普通输入的准入条件；本轮验证证明在相应受控场景能完成真实输入。未提交、打新发行包、部署或替换客户客户端，未在客户BOSS账号上筛选或通讯；客户业务现场仍待验收，最初隐藏的Chrome内部触发事件仍未知。§7中的goto异常直跳兜底及首次搜索歧义未在本轮实施，Runtime内置诊断计划也仍待开发。

## 18. 本机真实BOSS回归：输入可用，标准搜索定位仍失败（2026-11-02）

用户授权打开BOSS真机回归，手动登录后使用修复源码重新编译的CLI及Node22.23.3执行。8个生产源码摘要与10-31验收版本一致，未安装新插件/Runtime。完整步骤见[原计划W4](../plans/recruiting/boss-resume-assistant-native-cdp-dev-plan.md#w4本机真实boss回归2026-11-02)，[脱敏证据](evidence/boss-widget-stuck-hidden-20261030/real-boss-regression-20261102.json)。

- **普通操作通过**：沟通/推荐页菜单往返、13行筛选档位探查、3职位下拉、3–5年＋本科＋10–20K提交均有状态后验。简历读取1份成功/0失败，原生滚轮采4段拼接774×3714，确认到底、无可疑接缝，详情已关闭。
- **标准会话搜索未通过**：boss_open_chat在搜索入口定位时报64候选歧义，尚未投递键入。相同快照含3个document，只将主文档交给原定位器时入口唯一；临时探针进一步用原session.clickAndType在该搜索框完成测试词键入、精确值核验、清空和收起。后者只证明原生键入组件正常，不能冒称标准会话搜索已恢复。
- **定位证据的新边界**：10-30推荐菜单/筛选按钮已取得坐标而被widget守卫拒绝，与本次搜索入口根本未唯一定位是不同失败阶段；移除widget依赖不能修复所有上游定位问题。其他文档图标污染搜索候选已有同快照对照支持，后续需单独修定位并验证联系人/发送后验，本轮未改搜索代码。
- **现场恢复与边界**：同一target、原职位保持，筛选回到0项，测试查询已清空，最终为推荐牛人页，Chrome保持打开。无消息/打招呼/邀约/候选状态更改，无云端简历识别入库；没有在真实页面人为隐藏widget或复现原事故触发，原持续隐藏场景仍以隔离验收为限。未提交或部署。


## 19. 沟通广告混排、搜索及完整姓名修复（2026-11-02）

用户补充沟通页首位广告及客户置顶场景。现场确认首项为`batch-chat-intention`，真实会话在后续`geek-item-wrap → geek-item → geek-name`，URL仍是沟通页。旧列表定位仅按文字/坐标带找姓名，离线可复现广告同名被算成第二目标、置顶短提示被当姓名；这是上游业务对象识别问题，与辅助widget隐藏不同。当前没有证据证明原事故中广告曾被实际点击或是widget隐藏触发原因。

已实施主文档搜索范围、真实会话/姓名字段识别和联系人状态错误码修复；另在真机发现姓名高亮拆节点导致header截断，合并实测唯一`name-box`完整姓名，继续保留目标身份后验。没有新增恢复机制。实现与边界见[原设计§10.6.1](../design/recruiting/boss-resume-assistant-native-cdp-design.md#1061-沟通页混排与搜索定位修正2026-11-02)、[原计划§17](../plans/recruiting/boss-resume-assistant-native-cdp-dev-plan.md#17-沟通页混排与搜索定位修正2026-11-02)。

**验收**：广告保留，标准open-chat两次搜索打开及完整姓名验证通过，read-chat成功，target与沟通URL不变；主控67项、独立254项＋9探针通过，CR无阻断。真实置顶仅结构fixture，未真机置顶/发送消息。中间光标回读拒点、header截断及旧fixture三项失败均登记在计划，未隐去。未提交或部署。[脱敏证据](evidence/boss-widget-stuck-hidden-20261030/chat-mixed-validation-20261102.json)。这补齐§18发现的搜索定位缺陷，不改变原事故首次隐藏触发仍未知的结论。
