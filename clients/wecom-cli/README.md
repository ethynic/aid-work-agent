# wecom-cli（aid-wecom）

企业微信（WXWork.exe）操作 CLI / MCP Provider。整体骨架与 `clients/weixin-cli` 同范式：
CLI 动词子命令与 MCP stdio server 共用同一 operation 层，UI 自动化下沉到
`drivers/ps1`（DRIVER_JSON 进程协议）+ `drivers/py`（RapidOCR）。

设计与真机验证依据：`docs/research/wecom-cli/probe-and-design-20260829.md`
（2026-08-29 企业微信 5.0.9 全流程实测）。

## 命令

```
aid-wecom probe [--verbose] [--json]        # 只读环境探测：平台/交互会话/PowerShell/进程/主窗口/登录态/当前页
                                            # 登录态三态 online/need_login/offline；need_login 时附登录窗二维码
                                            # PNG base64 + qr_status_hint 供扫码上线（见 probe 命令章节）
aid-wecom search --query <词> [--type contact|group|any] [--limit N] [--json]
                                            # 搜索联系人/群聊（只读）：Jev 选最优候选，返回 best/坐标/概率 + 带 target_ref 的候选
                                            # 副作用：搜索结果面板保持打开（坐标句柄供后续 select 命令消费）
aid-wecom select --target-ref <ref> [--json]
                                            # 点击 search 返回的搜索结果进入会话（动作；不发送消息，
                                            # 进入会话会清除其未读角标并切换当前会话视图）
aid-wecom send --target-ref <ref> --text <文本> [--json]
                                            # 写动作：向 target_ref 目标发送 1 条文本（支持多行，经剪贴板
                                            # 粘贴通道，会覆盖用户剪贴板；智能分发：当前会话对→直接发；
                                            # 不对→自动 search+select 切换后发）
aid-wecom send-image --target-ref <ref> --image <本地绝对路径> [--json]
                                            # 写动作：向 target_ref 目标发送 1 张图片（png/jpg/jpeg/bmp/gif
                                            # ≤20MB；文件契约见 send-image 章节）
aid-wecom send-file --target-ref <ref> --file <本地绝对路径> [--json]
                                            # 写动作：向 target_ref 目标发送 1 个文件（扩展名不限
                                            # ≤100MB；文件契约见 send-file 章节）
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

## probe 命令（E5：登录态增强，RPA get_login_state 完整替代）

- **登录态三态**：`online` = 主窗口（≥600x400 可见 WeWorkWindow）在；
  `need_login` = 检测到登录二维码小窗（宽<500 且高<700 的可见 WeWorkWindow，复用
  Find-WeComLoginWindow）；`offline` = WXWork 进程不在或无任何 WeWorkWindow（托盘隐藏/已退出）。
- **need_login 时额外返回**：`login_window`（登录窗 rect {x,y,w,h}）、`qr_image_base64`
  （登录窗**整窗** PNG base64，供扫码上线）、`qr_status_hint`（`normal|expired|limited|unknown`）。
  截图策略：PrintWindow(hwnd,2) 优先 → 9 点采样**全黑或全白**回退 Graphics.CopyFromScreen 按窗口
  rect（M6 真机实测 PrintWindow 对 CEF 区域出纯白 mean=247 stddev=0，登录页同源——
  **CopyFromScreen 是主路径而非兜底**，需登录窗在屏幕可见，登录窗通常前台弹出，成立）。
  截图失败不致命：仅缺 `qr_image_base64`（hint=unknown），need_login 语义不变。
- **qr_status_hint 判定**（best-effort）：对同一张登录窗截图跑 RapidOCR，
  「已失效/过期/刷新」→ `expired`、「受限/冻结/异常」→ `limited`；OCR 不可用（venv 缺失）或
  无标记 → `normal`——探测不因可选依赖缺席而失败。
- **安全注记**：二维码是登录凭证——`qr_image_base64` 只随 OperationResult 走 stdout，
  绝不写 driver-log、绝不存 artifact（probe 无 artifact）；OCR 临时文件固定名、用完即删，
  文件名不含敏感信息。online/offline 状态不返回任何 qr 字段。
- **待真机复验**：本机开发态已登录，need_login 分支（登出/断网凭据失效场景）未真机实测，
  代码按 wecom-personal-rpa `wecom-ops.ps1` get_login_state 先例实现（小窗判定 +
  CopyFromScreen 截图 base64）；上线前需登出企微复验二维码可见性与 hint 判定。

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
payload 含 name/type/subtitle 与条目 overlay 相对坐标 x/y（M4 起可选；老 ref 无坐标对，
verify 不因未知字段失败；`send` 不要求坐标，`select` 要求），**有效期 5 分钟**。`send` 必须持有效
target_ref：过期 → TARGET_REF_STALE（重新 search 获取）；篡改/格式非法 → INVALID_ARGUMENT。
send 的分发阶段按 name+section（subtitle 优先收紧）匹配搜索候选：取 Jev best（须与目标
name+section+subtitle 消歧键一致才可信——Jev 看不到 target_ref 的 subtitle，同名同分区
多条时直接信任 best 会发错人），否则取规则唯一匹配项；多项无法消歧 → TARGET_AMBIGUOUS 拒绝发送。

## search 命令（M4：Jev 决策 + 坐标句柄）

流程（2026-09-28 真机验证）：attachstate Ctrl+F 聚焦搜索框（attach 失败降级
「裁切 OCR → Jev 选 token → 点击」，Jev 不可用再降级规则点占位符 token）→
动态搜索框状态检测（裁 x∈[120,420]/y∈[0,62] + 4x 放大 OCR，坐标判态；替代已证实
有误判 bug 的固定像素带）→ 残留清空（Ctrl+A+Delete，清不空 UI_CHANGED fail-closed）→
输入 query + OCR 回读验证 → 等 SearchResultWindow2 高度稳定 → 稳定帧 OCR 条目 →
**Jev 单次合并调用**（best_result + is_ambiguous，附概率分布）。

- 返回 `data.best`（最优候选：name/坐标/screen 参考坐标/confidence/概率分布）与
  `items`（每项含 overlay 相对坐标 x/y、probability、target_ref）；`search_successful`
  按规则判定（items 非空），不用 Jev；无结果 → TARGET_NOT_FOUND（data 附
  reason=no_results/filtered_out 与 jev/timing_ms 诊断）。`--type` 过滤把 best 滤掉时，
  从过滤后候选重选（probability 最高，全 null 则第一条）。
- **副作用：搜索结果面板（overlay）在返回后保持打开**——条目坐标与 overlay rect 是
  后续 select 命令的消费句柄；下一次 search 开头的残留清空会自动关掉旧 overlay。
- **Jev 集成与降级**：决策 API `api.typesafe.ai/v1/systemone`（模型 jev-latest），
  需要环境变量 `TYPESAFE_API_KEY`（可选依赖；MCP stdio 白名单场景回退用户级 env）。
  无 key/超时/HTTP 错 → `jev.used=false` + reason，best 走规则（归一化 name 精确 ==
  query 的第一条，否则第一条），probability 全 null。key 只经临时头文件瞬态使用，
  绝不入日志/命令行/artifact。
- artifact 目录 `artifacts/search-<ts>/`：稳定帧截图 + `driver-log.txt`（OCR 原始
  token、Jev state/answer 摘要、各阶段耗时）。

## select 命令（M5：点击搜索结果进入会话）

消费 `search` 留下的搜索结果面板（overlay），点击指定条目进入会话。**不发送任何消息**
（effect 恒 none），但属「半写」动作——CLI 执行前打印 ⚠️ 提示，MCP 注解非幂等。

- **前置条件**：必须先 `search` 且搜索结果面板（SearchResultWindow2）仍处于打开状态；
  `target_ref` 有效期 5 分钟，且必须是 M4+ 签发的含坐标版本（旧版无坐标 ref →
  INVALID_ARGUMENT，提示重新 search）。
- **点击前校验（防陈旧面板）**：驱动按类名 + 可见性找 overlay（面板关闭后窗口以
  visible=False 残留，只按类名会误判），PrintWindow 截图 OCR 复核条目名称（剥 @微信
  后缀双向归一化比较；同名多条——同一人在「联系人」与「聊天记录」分区各一行——
  取距 payload 坐标最近的一条）。**坐标信任策略**：OCR 复核到的条目自身 x/y 优先，
  payload 坐标作对照——两者中心距 >40px 判 UI_CHANGED（面板可能已变）；≤40px 用
  OCR 坐标（更新鲜）；OCR 坐标缺失时退用 payload 坐标。点击后等面板自动关闭
  （≤3s），再重新解析主窗口（外部联系人会话会撑宽主窗口）并 OCR 校验会话标题
  （复用 send 的严格语义：归一化相等或「名字+@/（」前缀，防「陆伟」误入「陆伟民」）。
- **副作用**：进入会话会**清除该会话未读角标**（企微客户端固有行为，与 read 同款），
  并切换主窗口当前会话视图；不清理搜索框内残留查询词（下次 search 自行清空）。
- **失败语义**：面板已关或 ref 过期 → TARGET_REF_STALE（重新 search 获取新句柄）；
  面板内容与句柄不符 / 坐标漂移 >40px / 点击后面板未关 / 会话标题不一致 → UI_CHANGED
  （同样建议重新 search）；链路超时 → EXECUTION_UNKNOWN（可能已切换会话，不自动重试）。
- artifact 目录 `artifacts/select-<ts>/`：点击前 overlay 截图 + 点击后主窗口截图 +
  `driver-log.txt`（各步耗时、OCR 摘要、坐标换算记录）。

## send 命令（M6：智能分发发送）

向 target_ref 目标发送 1 条文本（写动作，零自动重试）。TS 层编排 + 发送阶段驱动
（`drivers/ps1/message-send.ps1`）两阶段交互：

- **两条路径**：
  1. **快路径**——发送驱动先 PrintWindow 截图 + OCR 两带（标题带 y<0.07h、底部输入带
     y>0.72h 含工具栏图标行与输入区，boxes 模式带坐标），**Jev #1 三问合一**（当前会话是否
     目标 / 点哪聚焦输入框 / 输入区是否有草稿）判定当前会话就是目标 → 直接输入发送；
  2. **分发路径**——驱动判定非目标/无法判定 → 返回 `navigate_required=true` 交还 TS 层 →
     内部依次调用 chatSearch（注入同一 runDriverFn，Jev 选 best）与 chatSelect（点击进会话 +
     标题严格校验）→ **再次调用发送驱动**完成发送。两轮均不在目标会话 → TARGET_NOT_FOUND；
     编排失败（search 无结果 / select 校验失败等）透传对应错误码（此时未发送消息，effect=none）。
- **Jev 两次判定**：#1 发送前三问（见上）；#2 终态两问（sent_successfully / failure_mode，
     state = 发送后输入区/消息区末尾/会话列表 OCR 证据）。#2 判 no/unclear →
     EXECUTION_UNKNOWN（消息可能已发出，绝不自动重试）。
- **降级链**（Jev 无 key/超时/坏响应，逐问独立降级）：right_conversation → 标题归一化规则
     匹配；input_point → 比例坐标 (0.500w, 0.900h)（M2 标定）；has_draft → input 模式判空
     （占位符「发送消息/输入消息/聊点什么」与图标行碎字剔除）；终态 → M2 三选二（输入框清空 /
     消息区末尾任一行含 text 归一化前缀 12 字 / 会话列表含目标名且含前缀），<2 项 →
     EXECUTION_UNKNOWN。
- **草稿防串**：输入区已有**用户草稿** → 立即 UI_CHANGED 中止，绝不清除（可能是用户未发送
     的文字）；输入后、发送前规则复核会话标题，不一致 → **清空自己刚输入的草稿**
     （attachstate Ctrl+A + Delete，只清自己输入的内容）再 UI_CHANGED 中止，绝不把文字留在
     错误会话、绝不带着错误标题按 Enter。
- **text 约束**：≤2000 字、纯空白拒绝（归一化后为空会使终态校验失真）；**支持多行**——
  含换行时驱动走**剪贴板粘贴通道**：`Set-Clipboard` 重试 5 次（×150ms，全败 CONFIG_MISSING）→
  attachstate Ctrl+V（输入框已聚焦，2026-09-28 真机验证换行保留）→ 500ms → OCR 回读输入带
  （y≥0.80h、300<x<0.97w，token 按 (y,x) 序拼接归一化）须含 text 归一化前 8 字，不符 →
  UI_CHANGED fail-closed（不按 Enter，输入框可能有残留需人工检查）。**副作用：发送多行消息
  会覆盖用户剪贴板且不恢复**（不备份恢复是刻意为之：paste handler 异步读剪贴板，恢复竞态会
  粘贴到错的内容，weixin-cli 先例）；driver-log 对多行文本只记前 12 字 + 总字数，不落全文。
  发送前复核与终态校验对多行无需特判（气泡/会话列表预览显示为单行截断，归一化前缀取整文
  开头 8 字 = 首行开头）。驱动超时 → EXECUTION_UNKNOWN；取消 → CANCELLED。
- **artifact 结构**（同一 artifact 根下独立留痕）：`send-<ts>/`（两轮分发则为两个目录）
  含 `step1-precheck.png`（两带判定现场）、`step2-typed.png`（输入后复核现场）、
  `step3-after.png`（发送后终态现场）与 `driver-log.txt`（Jev state/answer 摘要、OCR 摘要、
  各阶段耗时；绝不含 TYPESAFE_API_KEY）；分发路径另有 `search-<ts>/`、`select-<ts>/`
  （内部命令自建）。返回 data 附 `navigated`（是否走了分发）、`sent_verification`
  （method=jev|rule_2of3）、`input_point` 与 `timing_ms`。

## send-image 命令（M7：发送图片）

向 target_ref 目标发送 1 张本地图片（写动作，零自动重试）。编排骨架与 `send` 同源
（M7 抽取的 `src/operations/navigate.ts` 智能分发共享实现），发送阶段驱动
`drivers/ps1/send-image.ps1`。E2 真机验证 2026-09-28（`experiments/probes/e5-image`）。

- **文件契约**：`--image` 只收**本地绝对路径**——调用方（agent/上层）负责把文件落到装有
  runtime 与 wecom-cli 的机器上；支持 png/jpg/jpeg/bmp/gif，**≤20MB**。TS 校验语义：
  不存在/类型不符/超限/相对路径 → INVALID_ARGUMENT；路径存在但读取失败 → CONFIG_MISSING。
  target_ref 须为 M4+ 含坐标版本（旧版无坐标 ref → INVALID_ARGUMENT，重新 search）。
- **发送链路与判定链**（驱动内）：Jev #1 三问判定当前会话（措辞同 send；非目标 →
  navigate_required 交还 TS 编排 search+select；输入区有用户草稿 → UI_CHANGED 中止）→
  点输入框 → 取输入区（x∈[0.35w,0.90w]、y∈[0.82h,0.95h]）灰度方差基线 std0 →
  `Clipboard.SetImage`（Bitmap FromFile，5 次重试×150ms，全败 CONFIG_MISSING）→
  attachstate Ctrl+V → 1.5s 后方差复测：std1 ≤ std0+8 → UI_CHANGED「图片预览未出现
  （粘贴可能未生效）」，**此时未按 Enter，无发送副作用** → 发送前标题复核（同 send 严格
  匹配）→ Send-WeComEnter → 1.8s → 终态证据双判据：①输入区方差回落（std2 < std0+8）
  ②会话列表（左栏）boxes OCR 归一化含「[图片]」；Jev #2 两问可用以其判定为准
  （method=jev；state 含方差数值、列表证据行、图片文件名+sha256+大小；注明图片消息
  无文本气泡属正常），no/unclear → EXECUTION_UNKNOWN；Jev 不可用/答案非法降级规则
  双判据须 2/2 全过（method=rule_2of2），<2/2 → EXECUTION_UNKNOWN。
- **副作用**：粘贴经剪贴板通道，**覆盖用户剪贴板且不恢复**（与 send 多行通道同款刻意
  行为）；成功后剪贴板仍保留该图片。
- **已知限制**：发送前标题复核失败中止时，输入区可能残留图片预览需**人工清理**——
  图片预览不是文本草稿，Ctrl+A 清不掉，ESC 会最小化企微（禁用），驱动不做任何清理动作。
- **artifact**：`send-image-<ts>/`（两轮分发则为两个目录）含 step1-precheck /
  step2-baseline / step3-pasted / step4-after 四步截图 + `driver-log.txt`（记图片
  文件名/sha256/大小/方差数值/Jev 摘要；**不复制图片本体**）。返回 data 附
  `input_stddev:{before,paste,after}`、`image:{name,size_bytes,sha256}`、
  `sent_verification`（method=jev|rule_2of2）、`navigated` 与 `timing_ms`。
- MCP 工具 `wecom_send_image`（target_ref + image_path），description 注明同一文件契约。

## send-file 命令（M8：发送文件）

向 target_ref 目标发送 1 个本地文件（写动作，零自动重试）。编排骨架与 `send`/`send-image`
同源（`src/operations/navigate.ts` 智能分发共享实现），发送阶段驱动
`drivers/ps1/send-file.ps1`。E3 真机验证 2026-09-28（`experiments/probes/e6-file`）。

- **文件契约**：`--file` 只收**本地绝对路径**——调用方（agent/上层）负责把文件落到装有
  runtime 与 wecom-cli 的机器上；**扩展名不限**（任意文件），**≤100MB**（对齐 RPA
  AttachmentDownloader 先例），**文件名去空白后须 ≥3 字符**（「a」「ab」这类极短名无论
  主干还是全名都只有 1-2 字符，OCR contains 判据无法与无关 token 区分 → INVALID_ARGUMENT
  拒发提示改名；「a.txt」不在拒绝范围——主干过短时驱动回退用完整文件名做匹配键）。TS
  校验语义：不存在/是目录/超限/相对路径/文件名过短 → INVALID_ARGUMENT；路径存在但读取
  失败 → CONFIG_MISSING（SHA-256 由 TS 计算，仅供日志/结果摘要，驱动不复算）。target_ref
  须为 M4+ 含坐标版本（旧版无坐标 ref → INVALID_ARGUMENT，重新 search）。
- **发送链路与判定链**（驱动内）：Jev #1 三问判定当前会话（措辞同 send-image；非目标 →
  navigate_required 交还 TS 编排 search+select；输入区有用户草稿 → UI_CHANGED 中止，
  上次失败尝试残留的文件卡片同样会被 input 判空拦下）→ 点输入框 → 取输入区
  （y≥0.75h、x∈(聊天区左界 max(0.10w,620), 0.74w]——E3 标定带；左界像素锚定防宽窗口
  漏卡片、右界 0.74w 排除外部联系人「智能总结」侧栏，同 chat_ocr.py input 模式阈值）
  OCR 干净基线 → `Clipboard.SetFileDropList`（StringCollection + 绝对路径，5 次重试×
  150ms，全败 CONFIG_MISSING）→ attachstate Ctrl+V → 1.5s 后粘贴校验：输入区出现
  **基线没有的新 token** 且归一化 contains 文件名主干（去扩展名防 OCR 把 .txt 误读，
  取前 12 字；主干归一化后 <3 字符回退完整文件名匹配键）——不含 → UI_CHANGED「文件卡片
  未出现（粘贴可能未生效）」，**此时未按 Enter，无发送副作用** → 发送前标题复核（同
  send-image 严格匹配）→ Send-WeComEnter → 1.8s → 终态证据双判据（同为**基线差分**，
  只认基线之后新出现的 token，防占位符/侧栏/其他会话预览等既有 token 假命中）：①输入区
  文件名 token 消失（复测粘贴校验同带）②会话列表（左栏 x0<0.40w）新 token 归一化含
  「[+文件名主干」或文件名主干（列表预览格式实测「[e3-test-file.txt]」，容忍括号误读）；
  Jev #2 两问可用以其判定为准（method=jev；state 含两判据结果、列表证据行、文件名+
  sha256+大小；注明文件消息无文本气泡属正常、重发同名文件列表预览不变属正常），no/
  unclear → EXECUTION_UNKNOWN；Jev 不可用/答案非法降级规则双判据须 2/2 全过
  （method=rule_2of2），<2/2 → EXECUTION_UNKNOWN。
- **副作用**：粘贴经剪贴板通道，**覆盖用户剪贴板且不恢复**（与 send-image 同款刻意
  行为）；成功后剪贴板仍保留该文件列表。
- **已知限制**：发送前标题复核失败中止时，输入区可能残留文件卡片需**人工清理**——
  文件卡片不是文本草稿，Ctrl+A 清不掉，ESC 会最小化企微（禁用），驱动不做任何清理动作；
  **重发同名文件**时会话列表预览保持不变、基线差分不命中 → 规则链可能报
  EXECUTION_UNKNOWN（保守方向：文件实际已发出，人工确认即可；Jev 链有重发例外提示）。
- **artifact**：`send-file-<ts>/`（两轮分发则为两个目录）含 step1-precheck /
  step2-baseline / step3-pasted / step4-after 四步截图 + `driver-log.txt`（记文件
  文件名/sha256/大小/匹配键/判据结果/Jev 摘要；**不复制文件本体**）。返回 data 附
  `paste_check:{stem,paste_hit,input_gone,list_hit}`（stem=实际匹配键：主干，主干
  过短时为完整文件名）、`file:{name,size_bytes,sha256}`、`sent_verification`
  （method=jev|rule_2of2）、`navigated` 与 `timing_ms`。
- MCP 工具 `wecom_send_file`（target_ref + file_path），description 注明同一文件契约。

## 运行依赖

- **Windows（win32-x64）**，已登录且未锁屏的交互桌面会话；
- 企业微信 Windows 客户端（WXWork.exe）**已登录**（5.0.9 实测）；
- PowerShell（powershell.exe 在 PATH）；
- `add-customer` / `search` / `select` / `send` / `send-image` / `send-file` / `unread` / `read` / `watch` 另需 OCR：仓库根 `venv` 的 python + `rapidocr_onnxruntime`
  （`drivers/ps1` 上四级为仓库根，取 `venv\Scripts\python.exe`；缺失 → CONFIG_MISSING）；
  `probe` 的 `qr_status_hint` 也走该 OCR 但为 best-effort（缺失 → hint=normal，探测不失败）；
- `search` / `send` / `send-image` / `send-file` 的 Jev 决策另需可选环境变量 `TYPESAFE_API_KEY`（缺失自动降级规则链，功能不中断）。

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
- M4（2026-09-28 实测）：attachstate 组合键（AttachThreadInput 共享键状态 + PostMessage
  Ctrl+F/Ctrl+A）全后台 ~250ms 可用，是聚焦/全选主路径；ESC 禁用（最小化企微）。
  搜索框状态检测必须动态裁切（x∈[120,420]、y∈[0,62] + 4x 放大 + 坐标判态，内容 token
  按 x1∈[212,400] 判定）：旧固定像素带在 1280 宽窗口把聊天区标题误判为残留（已证实 bug）。
  搜索 overlay 用后**保持打开**（坐标句柄供 select 消费）；overlay 关闭后窗口以
  visible=False 残留，判开必须带可见性过滤。Jev 决策 API 措辞敏感，state 模板不得随意改。
- M6（2026-09-28 设计定稿）：send 智能分发——驱动返回 `navigate_required=true` 表示当前
  会话非目标，TS 层内部编排 chatSearch→chatSelect→二次 send（不经 CLI，注入同一驱动依赖）；
  用户草稿绝不清除（UI_CHANGED 中止），自己输入的草稿发送前复核失败必清
  （Clear-WeComFocusedInput，与 Clear-WeComSearchBoxV2 同款 Ctrl+A+Delete 原语）；
  终态 Jev 判定 no/unclear 或降级三选二 <2 项 → EXECUTION_UNKNOWN 绝不自动重试。
- M7（2026-09-28 真机验证 e5-image）：Clipboard.SetImage（System.Windows.Forms，FromFile
  加载 Bitmap）+ attachstate Ctrl+V（Send-WeComAttachChordKey -Vk 0x56）在输入框聚焦时
  可后台粘贴图片；输入区缩略图使灰度方差 9.8→36.5（检测函数 Get-WeComRegionStddev，
  区域 x∈[0.35w,0.90w]、y∈[0.82h,0.95h] 隔 4px 采样，判据 ±8）；Send-WeComEnter 发送后
  1.8s 方差回落 + 会话列表预览出现「[图片]」。图片消息无文本气泡，终态双判据 =
  方差回落 + 列表 [图片]，Jev state 须注明气泡空白属正常。智能分发编排在 M7 抽取为
  navigate.ts 共享实现（send 与 send-image 共用）。
- M8（2026-09-28 真机验证 e6-file）：Clipboard.SetFileDropList（StringCollection 装绝对
  路径）+ attachstate Ctrl+V → **无确认弹窗**，输入区直接出现内联文件卡片（OCR 可读
  文件名 + 大小标签「53B」，y≈0.86h）→ Send-WeComEnter 后卡片消失 + 会话列表预览出现
  「[e3-test-file.txt]」，文件真实送达。文件卡片是 OCR 可读文本，判据直接 token 匹配
  文件名主干（去扩展名取前 12 字，防 .txt 误读），无需图片方差通道。CR 加固（2026-09-28）：
  三判据全部**基线差分**（只认粘贴/发送后新出现的 token，防占位符「发送消息」/侧栏/其他
  会话预览含主干假命中——通用主干「a」「test」的假阳性链由差分+短主干全名回退+TS 极短
  名拒发三层关闭）；输入区带左界像素锚定 max(0.10w,620)（纯比例 0.30w 在 2916 宽窗口会
  整段漏掉卡片 x0≈650px≈0.22w）、右界 0.74w 无条件排除智能总结侧栏（chat_ocr.py input
  模式同阈值）。
- 含中文的 .ps1 必须 **UTF-8 with BOM**。

## 目录

```
src/cli/          动词子命令 + 薄 renderer（进度/退出码/跨进程互斥）
src/mcp/          MCP stdio server + toolDefs + manifest digest
src/operations/   业务能力层（统一 OperationResult 契约，永不 reject）
src/platform/     PowerShell 驱动执行器 / 环境探测 / 命名互斥 / target_ref（HMAC 短期句柄）/ watch 水位状态
src/security/     日志脱敏（手机号不明文入日志）
drivers/ps1/      UI 自动化驱动（_common.ps1 底座 + probe/add-customer/chat-search/chat-select/message-send/send-image/send-file/unread-list/history-read）
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
