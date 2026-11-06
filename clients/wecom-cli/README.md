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
`drivers/ps1`（DRIVER_JSON 进程协议）+ `drivers/py`（RapidOCR）。

设计与真机验证依据：`docs/research/wecom-cli/probe-and-design-20260829.md`
（2026-08-29 企业微信 5.0.9 全流程实测）。

## 命令

```
aid-wecom probe [--verbose] [--json]        # 只读环境探测：平台/交互会话/PowerShell/进程/主窗口/登录态/当前页
                                            # 登录态三态 online/need_login/offline；need_login 时附登录窗二维码
                                            # PNG base64 供扫码，qr_status_hint 固定 unknown（见 probe 命令章节）
aid-wecom search --query <词> [--type contact|group|any] [--limit N] [--json]
                                            # 搜索联系人/群聊（只读）：Jev 选最优候选，返回 best/坐标/概率 + 带 target_ref 的候选
                                            # 副作用：搜索结果面板保持打开（坐标句柄供后续 select 命令消费）
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
aid-wecom add-customer --phone <11位> --yes [--json]
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

## target_ref（搜索 → 发送 的目标句柄）

`search` 返回的每个候选带 `target_ref`：base64url(payload) + HMAC-SHA256 签名
（本机密钥 `%LOCALAPPDATA%\AidWorkAgent\wecom-cli\target-ref.key`，不可跨机验证），
payload 含 name/type/subtitle 与条目 overlay 相对坐标 x/y（M4 起可选；老 ref 无坐标对，
verify 不因未知字段失败；`send` 不要求坐标，`select` 要求），**有效期 5 分钟**。`send` 必须持有效
target_ref：过期 → TARGET_REF_STALE（重新 search 获取）；篡改/格式非法 → INVALID_ARGUMENT。
send 的分发阶段按 name+section（subtitle 优先收紧）匹配搜索候选：取 Jev best（须与目标
name+section+subtitle 消歧键一致才可信——Jev 看不到 target_ref 的 subtitle，同名同分区
多条时直接信任 best 会发错人），否则取规则唯一匹配项；多项无法消歧 → TARGET_AMBIGUOUS 拒绝发送。

## --target-name 直达模式（M11a：runtime/agent 路径只有会话名时的免 ref 接入）

`send` / `send-image` / `send-file` / `read-session` 四命令支持 `--target-name`（`target_name`）与 `--target-ref`（`target_ref`）**二选一**：
两个都传或都不传 → INVALID_ARGUMENT。发送类操作的定位语义（`src/operations/navigate.ts` 的
`resolveTargetByName`，runtime 接入 M11a 的前置）：内部调 chatSearch（query 剥
`@微信` 后缀，type=any）→ Jev best 过身份校验（best.name 剥 @微信 归一化 == target_name
归一化，且落在 subtitle 收紧后的候选集内）才采纳；best 身份不符 → 回退 items 里
name（+send 的 `--subtitle` 消歧键）唯一匹配；多项 → TARGET_AMBIGUOUS 拒绝（fail-closed
不猜）；0 项 → TARGET_NOT_FOUND；search 失败透传错误码。定位成功后**用命中条目的
target_ref 走既有发送链路**（快路径直发；navigate_required 时二次 search+select 分发，
首次 search 的 overlay 由分发 search 开头的残留清空处理，ref 5 分钟 TTL 在内部链路
耗时可忽略）。`data.resolved_target = {name, subtitle, section}` 透传实际定位到的条目，
供 agent 观察定位结果；effect/错误码/零重试契约与 ref 模式完全一致。`read-session` 的旧目标参数改由本地库精确定位，同名歧义拒绝，不走这条 UI 搜索链。

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
- **副作用**：进入会话会**清除该会话未读角标**（企微客户端固有行为，与 read-session 同款），
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
  1. **快路径**——发送驱动截取标题带，由 GLM 标定标题点击坐标（同尺寸复用缓存）；底部输入带
     OCR 提供输入点候选，Jev #1 只选输入点；随后从企微复制当前标题一次，与目标核对通过后
     点击输入框、清理旧内容并沿原方式输入发送；
  2. **分发路径**——驱动判定非目标/无法判定 → 返回 `navigate_required=true` 交还 TS 层 →
     内部依次调用 chatSearch（注入同一 runDriverFn，Jev 选 best）与 chatSelect（点击进会话 +
     标题严格校验）→ **再次调用发送驱动**完成发送。两轮均不在目标会话 → TARGET_NOT_FOUND；
     编排失败（search 无结果 / select 校验失败等）透传对应错误码（此时未发送消息，effect=none）。
- **Jev 两次判定**：#1 只选输入点（见上）；#2 终态两问（sent_successfully / failure_mode，
     state = 发送后输入区/消息区末尾/会话列表 OCR 证据）。#2 判 no/unclear →
     EXECUTION_UNKNOWN（消息可能已发出，绝不自动重试）。
- **降级链**（Jev 无 key/超时/坏响应，逐问独立降级）：会话标题由企微复制后按目标规则
     核对；input_point → 比例坐标 (0.500w, 0.900h)（M2 标定）；终态 → M2 三选二（输入框清空 /
     消息区末尾任一行含 text 归一化前缀 12 字 / 会话列表含目标名且含前缀），<2 项 →
     EXECUTION_UNKNOWN。
- **文本输入替换**：目标标题确认后只点击一次输入区，投递 Ctrl+A + Delete 清理既有内容，清理键
     投递失败不粘贴或发送；不再以 OCR/Jev 草稿判定拒发，也不重复复制标题。
     此行为会覆盖已有未发送文字。
- **text 约束**：≤2000 字、纯空白拒绝（归一化后为空会使终态校验失真）；单行沿用
   `Send-WeComText` 逐字输入；含换行的多行沿用**剪贴板粘贴通道**：`Set-Clipboard` 重试 5 次
   （×150ms，全败 CONFIG_MISSING）→ attachstate Ctrl+V（投递失败不按 Enter）→ 500ms。
   粘贴后不再以 OCR 正文回读作发送门禁。**副作用：发送多行消息会覆盖用户剪贴板且不恢复**
   （paste handler 异步读取，立即恢复可能粘贴到错误内容）；driver-log 对多行只记前 12 字
   与总字数，不落全文。
  输入前标题确认与终态校验对多行无需特判（气泡/会话列表预览显示为单行截断，归一化前缀取整文
  开头 8 字 = 首行开头）。驱动超时 → EXECUTION_UNKNOWN；取消 → CANCELLED。
- **artifact 结构**（同一 artifact 根下独立留痕）：`send-<ts>/`（两轮分发则为两个目录）
  含 `step1-precheck.png`（两带判定现场）、`step2-typed.png`（输入后诊断截图）、
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
- `add-customer` / `search` / `select` / `send` / `send-image` / `send-file` 另需 OCR：仓库根 `venv` 的 python + `rapidocr_onnxruntime`
  （`drivers/ps1` 上四级为仓库根，取 `venv\Scripts\python.exe`；缺失 → CONFIG_MISSING）；`probe` 不依赖 OCR；
- `search` / `send` / `send-image` / `send-file` / `read-session` 的 Jev 决策另需可选环境变量 `TYPESAFE_API_KEY`（缺失自动降级规则链，功能不中断；watch 轮询内的 read-session 同样适用）；
- `read-session` 的**模型主通道**另需可选环境变量 `AID_WECOM_SERVER_URL`（服务端地址）+
  `AID_WECOM_SERVER_TOKEN`（服务端长期 token，管理端 `POST /api/saas/client-bindings/static`
  签发）——两者均未配置时直接走本地 OCR 通道（不报错）；模型通道按次计积分，详见
  M10b 章节。

## 关键实现事实（真机实测，勿随意改）

- 主窗口 = 可见、class `WeWorkWindow`、标题「企业微信」、≥600x400 且面积最大者；hwnd 动态，每次重新解析。
- 内容子窗口 class `WXworkWindow - 企业微信-<页名>`，类名编码页名（M1 时代页面状态校验用；
  **2026-09-29 客户端更新后已不存在**——主窗口 0 子窗口，probe current_page=null 同源，勿再依赖，见 M12 条）。
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
   文本发送前确认目标标题，清空其输入框后沿原方式输入；
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
   文本发送现按用户要求先确认目标，再覆盖目标输入区已有内容；输入后不再以标题 OCR 阻断发送
   （Clear-WeComFocusedInput 使用 Ctrl+A+Delete）；
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
- M9（2026-09-28 设计定稿）：read → read-session 改名（旧动词废弃）+ 导航迁移共享
  智能分发（navigate.ts readonly 模式：阶段超时/取消原样透传，不套写动作 unknown
  语义）+ 时间戳沿袭（页合并后沿消息流记最近 timeline 分割线原文为 time，近似时间）。
  旧驱动内搜索链退役原因：固定像素带在窄窗口把聊天区标题误判为搜索框残留（已证实
  bug），且 ESC 关闭路径有最小化企微风险——搜索/清空统一走 M4 V2 链（Ctrl+F 聚焦 +
  Ctrl+A+Delete 清空），会话切换交 TS 编排 chatSearch+chatSelect。
- M10b（历史实现，2026-11-06 已退出 read-session）：截图双通道解析——模型主通道（截图 base64 上传
  服务端 `/api/client/v1/session-history`，GLM-5.3-Flash 多模态并行分页 + 服务端重叠
  去重，来源 E7 实验结论）+ OCR 兜底。驱动加 `-ParseMode ocr|none`（none=只截图不逐页
  OCR，省每页 ~2s 冷启动；缺省 ocr 向后兼容 watch）；TS 侧 serverProxy.ts；
  402 余额不足/配置错误不降级直接报错（INSUFFICIENT_CREDIT / CONFIG_MISSING）。
- M10c（2026-09-29 用户定稿）：模型通道鉴权由「激活码模式」简化为「直连长期 token
  模式」——自有机器调自有服务端，去掉激活码；配置 `AID_WECOM_SERVER_URL`+
  `AID_WECOM_SERVER_TOKEN`（服务端管理端签发的 static 长期 token，见 M10b 章节配置
  说明）直接调用扣积分。激活码链路（`AID_WECOM_ACTIVATION_CODE`、/activate 调用、
  401 重激活、DPAPI 缓存 server-binding.json、src/security/dpapi.ts）全部移除；
  401 token 无效改判 config 直报不重试不降级。
- **M12（2026-09-29 真机重标定）：add-customer 改走主窗口搜索直达路线，旧「通讯录→新的
  客户→⊕添加」路线整体废弃**——客户端更新后内容子窗口 `WXworkWindow - 企业微信-<页名>`
  已不存在（子窗口校验失效）、通讯录导航坐标漂移、④添加按钮 PostMessage/mouse_event 均
  无效（真机逐步验证）。新流程：attachstate **Ctrl+F 聚焦搜索框**（失败 fail-closed，绝不
  带不确定焦点输入）→ `Clear-WeComSearchBoxV2` 残留清空 + 状态复核 → 输入手机号 + 搜索框
  带 token 回读（**contains 判定**：天然容忍前导 Q 图标伪读/末尾光标伪影/「网络查找」行
  混入；截断丢字必失败）→ 等 `SearchResultWindow2` overlay 高度稳定（手机号搜索形态实测
  高 148 = 单行 + 底部按钮行）→ boxes OCR 找「网络查找」行（实测 token 形态
  「网络查找手机W/邮箱：13671705876」，W=「号」误读，按 contains '网络查找' 匹配 + **同
  视觉行拼接校验含手机号**防点错行/陈旧面板）→ PostMessage 点行中心（显式投 overlay
  hwnd，同 M2 结果行点击模式）→ **SearchExternalsWnd 弹窗（400x292，类名与 M1 相同）
  自动填号**（标题「网络查找手机/邮箱：\<号\>」+ 输入框数字两处 OCR 可验；未自动填回退：
  点输入框 M1 标定 0.5w/0.257h + 清空 + 手输 + 回读）→ 弹窗段（Enter 检索重发 / result
  模式 OCR / InputReasonWnd 15s / 发送 / 终态「已发送申请」10s 轮询）**M1 原样保留**。
  收尾成功/失败统一 best-effort：关两弹窗 + 清空搜索框手机号残留（Ctrl+F + Ctrl+A+Delete，
  同时关掉可能残留的 overlay）。**陈旧弹窗防御**：点「网络查找」行与点结果行「添加」之前
  先关闭已存在的同类可见弹窗（SearchExternalsWnd/InputReasonWnd；上轮被超时 kill 或人工
  遗留时，残留旧号码的结果行/「发送」窗无法与新开弹窗区分，会被当成新结果误发——关不掉
  → UI_CHANGED 中止；正常路径无残留时为空操作）。`add_customer_result.py` result/reason
  模式对新弹窗兼容（同布局，实测结果行「WayneLu」+「添加」可解析），原样复用。
- 含中文的 .ps1 必须 **UTF-8 with BOM**。

## 目录

```
src/cli/          动词子命令 + 薄 renderer（进度/退出码/跨进程互斥）
src/mcp/          MCP stdio server + toolDefs + manifest digest
src/operations/   业务能力层（统一 OperationResult 契约，永不 reject）
src/platform/     PowerShell 驱动执行器 / 环境探测 / 命名互斥 / target_ref（HMAC 短期句柄）/ watch 水位状态 / serverProxy（M10c 模型通道：长期 token 直连 /session-history）
src/security/     日志脱敏（手机号不明文入日志）
drivers/ps1/      UI 自动化驱动（_common.ps1 底座 + probe/add-customer/chat-search/chat-select/message-send/send-image/send-file/unread-list/read-session）
drivers/py/       RapidOCR：add_customer_result.py（添加客户弹窗）+ chat_ocr.py（search/send/unread/history 单一入口）
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

凭据位于既有`artifacts/send-*/send-receipt.dpapi`，一个发送阶段一个小文件；清理该证据目录后无法恢复。不要将密文或原始群消息上传日志。只有旧OCR路径的发送没有可重建本地证明，不产生receipt。MCP同时暴露`wecom_send_status({receipt_id})`，沿用CLI同一只读operation；Runtime受信清单追加此只读工具，并在调用前核实际Provider能力，旧CLI提示升级而不重发。云端代理接收`send_invocation_id`，服务端从原发送可信结果提取receipt，核对同主体/会话/固定原设备后派发；不允许用模型自报receipt绕过权限或其他设备hold。对账调用本身0积分，模型实际用量照常计费；查询成功仍须看`data.send_status`，不自动改变原unknown/hold或解除原Runner核验等待。新真实发送、多天持续任务及正式发行升级仍待后续验收，不改原Runtime结果补传设施。
