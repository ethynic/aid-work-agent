# wecom-cli（aid-wecom）

Agent 业务面使用三个工具：`wecom_read_session(session, cursor?, limit?)`、`wecom_message_send(session, text)`、`wecom_read_sessions(sessions?, cursor?, limit?)`。读单聊和群复用同一接口；批量读取同时返回聊天内容与 `pending_sessions`，按消息标注 `sender_platform=wechat|wecom|unknown`。首次历史为 `baseline`，后续游标增量不能与历史回放混用；群里同事最后发言不会自动清除客户候选。

`session` 可使用首次完整名称，后续优先原样回传读取结果中的稳定标识；它不因短期搜索 ref 到期要求重搜。批量默认每次最多 50 条、16KiB，`has_more` 分页继续，局部失败在 `failed` 返回并保留补读位置。发送先核对当前会话，匹配就直接发送，非目标才内部搜索选择；未知发送只读对账，不重发。原诊断/底层工具保留旧调用兼容，Agent 无需逐项编排它们。

```bash
aid-wecom read-session --session '会话名称' --json
aid-wecom read-sessions --sessions '["会话甲","会话乙"]' --limit 50 --json
aid-wecom send --session '<读取返回的session>' --text '消息' --json
```

当前平台来源：已实证的微信虚拟 corp 精确标为微信，本企业 corp 标为企微；其他尚无可靠类型来源时返回 `unknown`，不以“外部联系人”等同微信。系统消息及是否需要回复继续由消息类型与任务上下文判断。

批量续读省略 `sessions` 时继承原游标目标；目标暂时缺失只列入该目标的 `failed`，其他会话继续。未知发送者候选同时返回 `unknown_messages[{msg_id,sender_id,baseline,recovery_cursor}]`，身份元数据补齐后用该行的 `session` 与 `recovery_cursor` 调用 `wecom_read_session(...,limit=1)`，可重读原消息而不重置监控进度。消息正文留在原读取结果中；恢复游标和整个响应共同受 16KiB 边界约束。

每条消息的 `baseline` 由游标保留的启动水位判断，局部失败补读及字节分页不会改变历史/新增属性；单会话 `new_messages` 仅包含启动后的记录。候选同时保留 `history_message_ids/new_message_ids`，未知身份恢复沿用原启动水位。仅本轮尚未发布的中间 `wb1` 格式若既无启动水位、又已进入补读阶段，因无法准确重建历史边界而明确返回 `CURSOR_UNSUPPORTED` 并保留进度；原正式群游标格式不受影响。

Runner 将首次按名称发送拆为内部只读定位与稳定会话发送：原生 MCP `wecom_read_session` 的 `resolve_only=true` 复用发送内部的 DB 元数据定位，不解析正文、不返回消息游标，因此超长最新消息不会阻塞定位。此参数不进入 Agent 三工具业务面；已获得稳定 `session` 的发送不增加定位调用。

企业微信（WXWork.exe）操作 CLI / MCP Provider。整体骨架与 `clients/weixin-cli` 同范式：
CLI 动词子命令与 MCP stdio server 共用同一 operation 层，UI 自动化下沉到
`drivers/ps1`（DRIVER_JSON 进程协议）+ `drivers/py`（本地库只读驱动）。

设计与真机验证依据：`docs/research/wecom-cli/probe-and-design-20260829.md`
（2026-08-29 企业微信 5.0.9 全流程实测）。

## 命令

```
aid-wecom probe [--verbose] [--json]        # 只读环境探测：平台/交互会话/PowerShell/进程/主窗口/登录态/当前页
                                            # 登录态三态 online/need_login/offline；need_login 时附登录窗二维码
                                            # PNG base64 供扫码，qr_status_hint 固定 unknown（见 probe 命令章节）
aid-wecom search --query <词> [--type contact|group|any] [--limit N] [--json]
                                            # 搜索联系人/群聊（只读）：Jev 选最优候选，返回 best/坐标/概率 + 带 target_ref 的候选
                                            # 本地库唯一匹配返回账号绑定句柄；不打开搜索面板
aid-wecom select --target-ref <ref> [--json]
                                            # 点击 search 返回的搜索结果进入会话（动作；不发送消息，
                                            # 进入会话会清除其未读角标并切换当前会话视图）
aid-wecom send (--target-ref <ref> | --target-name <会话名> [--subtitle <副标题>]) --text <文本> [--json]
                                            # 写动作：向目标发送 1 条文本（支持多行，经剪贴板
                                            # 粘贴通道，会覆盖用户剪贴板；智能分发：当前会话对→直接发；
                                            # 不对→自动 search+select 切换后发）
                                            # M11a 直达模式：--target-name 与 --target-ref 二选一，
                                            # 内部自动 search 定位（身份校验+唯一匹配，歧义拒绝），
                                            # data 附 resolved_target；--subtitle 仅直达模式消歧用
aid-wecom send-image (--target-ref <ref> | --target-name <会话名>) --image <本地绝对路径> [--json]
                                            # 写动作：向目标发送 1 张图片（png/jpg/jpeg/bmp/gif
                                            # ≤20MB；文件契约见 send-image 章节；--target-name 同 M11a 直达模式）
aid-wecom send-file (--target-ref <ref> | --target-name <会话名>) --file <本地绝对路径> [--json]
                                            # 写动作：向目标发送 1 个文件（扩展名不限
                                            # ≤100MB；文件契约见 send-file 章节；--target-name 同 M11a 直达模式）
aid-wecom unread [--name <名>] [--focus <名1,名2>] [--json]  # 本地库待回复快照，不切会话/清角标
aid-wecom read-session (--target-ref <ref> | --target-name <会话名>) [--limit N] [--cursor <cursor>] [--since-days N] [--json]
                                            # 本地库读取精确消息/游标；同名消歧，不切会话/清角标
aid-wecom watch [--interval 秒] [--once] [--focus <名1,名2>]
                                            # 本地库增量事件 NDJSON；失败轮保留水位
                                            # 写动作：主窗口搜索手机号→「网络查找」→检索→发邀请（--yes 显式确认；
                                            # M12 搜索直达路线，旧通讯录路线已废弃，见「关键实现事实」M12 条）
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
  （登录窗**整窗** PNG base64，供扫码上线）、`qr_status_hint=unknown`（兼容既有字段）。
  截图策略：PrintWindow(hwnd,2) 优先 → 9 点采样**全黑或全白**回退 Graphics.CopyFromScreen 按窗口
  rect（M6 真机实测 PrintWindow 对 CEF 区域出纯白 mean=247 stddev=0，登录页同源——
  **CopyFromScreen 是主路径而非兜底**，需登录窗在屏幕可见，登录窗通常前台弹出，成立）。
  截图失败不致命：仅缺 `qr_image_base64`，need_login 语义不变。窗口枚举只能确认登录窗存在，
  无法判断二维码是否过期或受限，因此 `qr_status_hint` 固定为 `unknown`，probe 不调用 OCR。
- **安全注记**：二维码是登录凭证——`qr_image_base64` 只随 OperationResult 走 stdout，
  绝不写 driver-log、绝不存 artifact（probe 无 artifact）；截图临时文件固定名、用完即删，
  文件名不含敏感信息。online/offline 状态不返回任何 qr 字段。
- **待真机复验**：本机开发态已登录，need_login 分支（登出/断网凭据失效场景）未真机实测，
  实现参照原 wecom-personal-rpa `wecom-ops.ps1` get_login_state 的先例（该脚本已随
  RPA 清退删除；小窗判定 + CopyFromScreen 截图 base64 思路保留在本 CLI）；
  上线前需登出企微复验二维码可见性与登录态判定。

## 本地库读取（P1b）

### 群关键词搜索、群列表及时间线（P4）

以下命令直接查询当前登录账号的本地数据库，不使用截图/OCR/Jev，不切换会话或清除未读。复用版本矩阵、账号确认和可信快照；缺库或不兼容时明确失败，不回退视觉。列表范围是**本机已同步群**，包含无聊天记录的群，不能视为企业全部群或当前成员资格清单。

```powershell
# 按群名关键词作字面子串匹配，过滤后最多返回10个（%/_不作通配符）
node dist/src/cli/index.js list-groups --query "测试" --limit 10 --json
# 列出最多20个群，按群名和会话ID稳定排序
node dist/src/cli/index.js list-groups --limit 20 --json
# 取最近100条，再按发送时间从早到晚排列
node dist/src/cli/index.js read-group --target-name "新劳务云系统项目沟通群" --limit 100 --json
# 时间闭区间内最近100条，仍按时间升序；未写时区时固定按北京时间
node dist/src/cli/index.js read-group --target-name "新劳务云系统项目沟通群" --from-time "2026-10-01 00:00:00" --to-time "2026-10-22 23:59:59" --limit 100 --json
```

对应 MCP 工具为 `wecom_group_list({query?,limit?})` 与 `wecom_group_read({target_name,limit?,from_time?,to_time?})`。完整群名精确匹配，仅在群中消歧；多个同名群返回 `TARGET_AMBIGUOUS`。时间须完整到秒，也接受 `2026-10-22T12:00:00+08:00` / `2026-10-22T04:00:00Z`，起止两端均包含。只有单侧时间也可查询。

两种查询默认 `limit=100`，返回 `count` 和 `has_more`；`has_more=true` 表示数量被限制，消息查询保留区间内最近 N 条。消息按 `send_time` 升序，同秒按 `msg_id` 升序；`sender_id` 保持字符串，`time` 固定北京时间，`send_time` 和输出的 `from_time/to_time` 为 Unix 秒。没有消息成功返回空数组，异常时间不进入时间线并以 `invalid_time_count` 明示（当前群总异常数）；非文本消息仍占位，`parse_trusted` 表示正文解析可信性，人工输出会给不可信文本加上 `[正文解析待核验]`。本命令不返回增量游标。

原 `search` / `wecom_chat_search` 仍使用界面搜索；需要本地库搜群请使用 `list-groups`。新增工具进入本 Provider 的标准 MCP 发现面；本批不修改旧 D1 Runtime 的静态工具清单，也未发布或安装新版 Runtime 插件。

群消息显示姓名并保留字符串 `sender_id`，`sender_name` 从当前账号本地用户表精确映射；查无姓名时为 `null`，人工输出显示 `未知用户(ID)`，不会按同名推断身份。`sender_name_source` 标明 `user_table/unresolved`，顶层 `naming_incomplete` 明示姓名缺口。

文本消息按本地有序片段拼接，恢复普通文字、多次 `@成员`、`@所有人` 以及引用前文。`mentions:[{kind,user_id,name,name_source}]` 仅记录结构化提及，引用文字中的字面 `@` 不冒充真实提及；提及优先使用消息内历史显示名，缺名时才按ID补用户表姓名。`mentions_complete=false` 或 `parse_trusted=false` 表示未知/损坏片段等解析缺口；无法识别的片段明确占位，不能视为完整正文。引用前文按消息里的原始文字保留，不猜引用作者ID；非文本消息仍不解析实体内容。既有 read-session 增量游标和 watch 锚点语义保持不变。

`read-session`、`unread`、`watch` 只使用当前登录账号的本地数据库。首次读取默认最近 100 条，后续原样传 `data.cursor` 续读；`--limit` 控制批次条数。目标名称从本地库精确消歧，账号绑定 ref 必须匹配当前账号，旧无账号 ref 拒绝读取并提示重新搜索或使用名称。

三入口每次核对实际运行进程版本及支持矩阵、账号、库指纹和快照；doctor 状态 unset 或 UI 兼容态也尝试本地库，实际不支持时明确报原错。旧 `--channel ui` 返回 `VERSION_UNSUPPORTED`，旧 `--max-pages` 返回参数错误；不会截屏、调用 OCR、切换会话或清未读。监控数据库失败轮保留水位并在下一轮重试，不能把失败当作“没有新消息”。

## 新消息跟踪（本地库）

- `unread` 按最后发言方返回待回复快照，覆盖已读未回复；可用 `--focus` 指定会话名。
- `read-session` 返回消息 ID、精确时间、发送方向与账号/会话/源库代际绑定游标。
- `watch` 每轮按数据库水位返回 `{channel:'local-db',new_messages,last_sender,failed?}`；新关注首轮建立基线，只返回之后的新消息。存量待回复请先用 `unread` 读取。失败轮不推进水位，CLI 按轮释放互斥，轮间不占锁。

## target_ref（唯一名称目标句柄）

`search` 只在本地库命中且完整名称跨联系人/群聊类型也唯一时签发 5 分钟有效的本机 HMAC 句柄，绑定当前账号和目标完整名称。新句柄没有 OCR 坐标。过期、篡改、切换账号或名称不再唯一时拒绝使用。旧含坐标句柄的坐标不会用于 `select`；图片/文件发送在任何 UI 前核对账号、本地库目标唯一性与发送基线，旧无账号句柄拒绝。

## --target-name 直达模式

`send` / `send-image` / `send-file` / `read-session` 仍支持 `target_name` 与 `target_ref` 二选一。文本发送和读取沿各自本地库目标解析；图片、文件先由本地库唯一解析完整名称，再按双点缓存搜索、复制标题并发送。

## search 命令（本地库名称匹配）

在操作企微窗口前读取已验证账号的本地群会话和联系人名称，同一联系人只计一个身份。完整名称匹配优先；没有完整匹配再按关键词包含匹配。

- 唯一命中：返回账号绑定 `target_ref`，可交给 `select`。
- 无命中：`TARGET_NOT_FOUND`，返回近似名称供 agent 向用户核对；不会自动把错字改成另一人。
- 多个不同名称：`TARGET_AMBIGUOUS`，列出名称，请用户提供完整名称。多个身份完全同名时提示备注或重命名。结果超过 `limit` 时注明已截断及总数。

此命令不打开搜索框、不切换会话，也不调用 OCR。

## select 命令（唯一名称进入会话）

消费 `search` 的唯一目标句柄。先复查当前账号及本地名称仍唯一，再复用文本发送的 Ctrl+F、清框、同进程结果面板与默认结果 Enter 路径；进入后用双点缓存复制标题核对：群名全等，联系人只接受全名或全名后接 `@微信` / ` @微信` 两种全等形式。没有有效缓存时先标定本人会话。

`select` 不发送消息，但进入会话会清除未读角标并切换视图。标题不符时报 `UI_CHANGED`；超时结果不明时报 `EXECUTION_UNKNOWN`，不自动重试。新路径不调用 OCR。
## send 与 calibrate 命令（文本发送双点标定）

文本发送仅支持已验证版本的本地库。发送前解析当前账号、本人显示名和唯一目标，并记录消息基线；本地库不可用时拒发。独立 calibrate 命令可手动重标定，send 在双点缓存缺失或窗口宽高/DPI 变化时自动执行同一标定：搜索本人名、Enter 打开默认自会话，GLM 一次定位标题首字与输入区中心，复制自会话标题与本人名全等后缓存窗口相对坐标。缓存有效时不调用 GLM 或 OCR。

send 搜索目标名时先用主线程焦点与搜索框区域内的 caret 确认 Ctrl+F 已聚焦搜索框，才清理并输入搜索词；同进程新结果面板出现后 Enter 打开默认会话。复制标题与目标全等（微信联系人须为明确的 `名字@微信`）后点击缓存输入点、Ctrl+A/Delete 清草稿、把发送文本复制到剪贴板并 Ctrl+V 粘贴、Enter 发送。不回读输入内容；单行和多行均覆盖系统剪贴板且不立即恢复，避免企微异步粘贴取到旧内容。标题不匹配时本次拒发并清除旧坐标缓存，下一次重新标定。发送后仅用本地库唯一出站记录核验；未知结果保留回执供 send-status 只读对账，不自动重发。文本发送链路不调用 chat-search/chat-select 的 OCR/Jev，也不使用旧 OCR 终态回退。独立 search/select 与媒体发送也已改为本地目录、双点缓存和标题复制。

## send-image 命令（M7：发送图片）

向唯一的 `target_ref` 或 `target_name` 目标发送 1 张本地图片。`--image` 须为本机绝对路径，支持 png/jpg/jpeg/bmp/gif、≤20MB；调用方负责先落盘。发送前本地库核对账号、唯一会话和消息基线并保存加密 receipt；双点缓存缺失时标定本人会话。Ctrl+F/Enter 打开目标，复制标题严格核对，点击缓存输入点并清草稿，`Clipboard.SetImage`、Ctrl+V、Enter。覆盖用户剪贴板且不立即恢复。

Enter 后只读本地库，基线后时间窗内唯一 101 本人出站记录表示**客户端有唯一图片记录**，不证明与原图片字节一致，也不代表对方已收到。无记录、多记录或库故障返回 `EXECUTION_UNKNOWN`，用 `receipt_id` 调 `send-status` 只读对账，绝不自动重发。此 W26 路径尚无真实 101 样本或媒体真机发送验收。

## send-file 命令（M8：发送文件）

向唯一的 `target_ref` 或 `target_name` 目标发送 1 个本地文件。`--file` 须为本机绝对路径，扩展名不限、≤100MB；调用方负责先落盘，极短文件名也可发送。发送前本地库核对账号、唯一会话和消息基线并保存加密 receipt；双点缓存缺失时标定本人会话。Ctrl+F/Enter 打开目标，复制标题严格核对，点击缓存输入点并清草稿，`Clipboard.SetFileDropList`、Ctrl+V、Enter。覆盖用户剪贴板且不立即恢复。

Enter 后只读本地库，基线后时间窗内唯一 102 本人出站记录且 `attachment.name` 与文件名全等，表示**客户端有唯一同名文件记录**，不代表对方已收到。无记录、多记录、名称不符或库故障返回 `EXECUTION_UNKNOWN`，用 `receipt_id` 调 `send-status` 只读对账，绝不自动重发。此 W26 路径尚无真实 102 样本或媒体真机发送验收。

## 运行依赖

- **Windows（win32-x64）**，已登录且未锁屏的交互桌面会话；
- 企业微信 Windows 客户端（WXWork.exe）**已登录**（5.0.9 实测）；
- PowerShell（powershell.exe 在 PATH）；
- 企微发行包使用独立的 `localdb-python`（便携 Python + pycryptodome + tzdata + app-local CRT），不含 OCR 资产；双点缓存缺失时需要已配置的 GLM 标定代理。

## 实现记录

旧 OCR/添加客户实验已退役；当前路径与验证边界见 `docs/design/wecom/wecom-cli-design.md`、`docs/plans/plan-wecom-cli-group-chat-experiment.md`。含中文的 `.ps1` 使用 UTF-8 with BOM。

## 目录

```
src/cli/          动词子命令 + 薄 renderer（进度/退出码/跨进程互斥）
src/mcp/          MCP stdio server + toolDefs + manifest digest
src/operations/   业务能力层（统一 OperationResult 契约，永不 reject）
src/platform/     PowerShell 驱动执行器 / 环境探测 / 命名互斥 / target_ref（HMAC 短期句柄）/ watch 水位状态
src/security/     日志脱敏
drivers/ps1/      UI 自动化驱动（_common.ps1 底座 + probe/chat-select/message-send/send-image/send-file/point-calibrate）
drivers/py/       本地库驱动 wecom_localdb.py + python_bootstrap.py
tests/            node:test，全部 mock（绝不触达真实企微窗口）
```

## 与相邻项目的关系

- **weixin-cli**：骨架/契约/驱动协议的直接参照（个人微信版）。
- **wecom-personal-rpa**：已全量删除（2026-09-29 废弃，2026-11-05 删除完毕，
  见 `docs/plans/plan-wecom-personal-rpa-removal.md`）。其 UI 执行职责由
  runtime + wecom-cli 接管；服务端 `wecom_personal_rpa` channel（outbox/签名/
  inbox）随清退一并删除，入站消息不再经过该链路。

## 开发

```
npm install
npm run typecheck   # tsc --noEmit
npm test            # build + node --test（全 mock，不需要真实企微）
```

## 不确定发送结果的只读恢复

本地库基线可用的文本发送在调用发送驱动前写入DPAPI CurrentUser加密凭据，并通过checkpoint进度和最终结果/错误的`data.receipt_id`提供ID。调用方应保存ID；`effect=unknown/partial`或进程退出没有原结果时，不要再次调用send，改为：

```powershell
node dist/src/cli/index.js send-status --receipt-id <原receipt_id> --json
```

该命令只读本地库，不切群、不输入、不发送。`data.send_status=confirmed`只表示原账号、会话、源代际及基线之后、原时间窗内有唯一可信全文匹配记录，不代表对方已收到/已读，也不能区分窗口内人工发送的同文。`unknown`仍可能已发出，应稍后只读核查或人工判断，不得自动重发。凭据缺失/损坏、换账号/库、锚失效、歧义或扫描达到上限均不推断未发送。

明确无发送动作的阶段会删除凭据（如checkpoint取消、导航前检查、写前拒绝）；删除失败立即停止，`receipt_invalidation_failed=true`的ID不可用于自动确认。驱动中超时/取消/unknown保留证据用于只读核对。

凭据位于既有`artifacts/send-*/send-receipt.dpapi`，一个发送阶段一个小文件；清理该证据目录后无法恢复。不要将密文或原始群消息上传日志。MCP同时暴露`wecom_send_status({receipt_id})`，沿用CLI同一只读operation；Runtime受信清单追加此只读工具，并在调用前核实际Provider能力，旧CLI提示升级而不重发。云端代理接收`send_invocation_id`，服务端从原发送可信结果提取receipt，核对同主体/会话/固定原设备后派发；不允许用模型自报receipt绕过权限或其他设备hold。对账调用本身0积分，模型实际用量照常计费；查询成功仍须看`data.send_status`，不自动改变原unknown/hold或解除原Runner核验等待。新真实发送、多天持续任务及正式发行升级仍待后续验收，不改原Runtime结果补传设施。
