# wecom-cli 本地库数据通道（local-db）开发计划

> 设计依据：[wecom-cli 本地库数据通道设计](../design/wecom/wecom-cli-localdb-design.md)（2026-10-14 评审 v5）
> 实验参考：`clients/wecom-cli/experiments/local-db-decrypt/`（算法与密钥扫描可吸收；WAL 重建逻辑按设计 §3.3 重做，不可照搬）
> 开发顺序原则（评审 v2）：先闭合可信快照、密钥复用、账号和会话定位三个正确性/绑定问题，交付 P1 历史读取；再做待回复检测与发送核验；独立采集进程等优化依实测决定。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| P0 | 前置调查（schema / 打包 / 登录证据） | ✅ 完成（2026-10-14，子智能体真机执行） | 水位基元=message_id；send_time=秒；无发送状态字段；群解析经 conversation_table 可行；WAL 旧代尾帧实观命中（4/6 帧）；DPAPI 单文件移植 + pycryptodome 打包仅 1 条 lock 条目 |
| P1a | 组件 C1-C9 + doctor 深检（正确性优先序） | 🔧 进行中 | 组件已实现；本轮身份修复独立测试与 CR 通过（Node 419/419、Python 97 通过/1 跳过）；1.14 真实退出登录、同 PID 切号、锁屏待用户配合 |
| P1b | read-session 本地库通道组合 + 真机读取冒烟 | 🔧 进行中（2.1–2.4 ✅ 含主控复核 432/432；2.5 真机：unread/watch ✅ 29 会话、read-session resolve schema 待适配新企业库结构） | 详见下方真机验收记录 |
| P2 | 待回复检测 + 发送本地库核验 | ✅ 完成（2026-10-16，代码层；第八、九轮评审修复同日完成；真机 db 轮/local-record 待矩阵条目后验收） | 3.1–3.5 全部交付：watch 按轮锁（间隔不持锁，真实子进程互斥测试）、unread/watch 本地库通道（待回复=最后可参与消息发言方、系统消息最小排除、focus 冻结点 4、watch DB 失败不落 UI 水位保留）、发送核验（基线时序+trusted 全等+OCR 兜底+不重入+verification 三值）；独立验收（接缝无漂移，修 -ExpectedPrefix 失效 P1）+ 一次性 CR（修 -VerifyOnly 超时 effect 误报 P1）；**第八轮评审修复（5 P1 + 2 P2，见 P2 节末记录）：发送链 ref 账号核对/核验失败归并 unknown/关注空会话水位 0/源库代际重建基线/focus 持久绑定/VerifyOnly 标题一致性/unread name DB 过滤**；**第九轮评审修复（2 P1 + 1 P2，见 P2 节末记录）：水位锚点校验（同文件重写全量重报）/ focus ref legacy+类型约束 / unread name 无 focus 反向取名**；终检 typecheck 通过、npm test 508/508、test:py 116（1 平台跳过）；**独立验证（2026-10-16）：评审者复现手法 7/7 PASS（16 项独立复现保留于 tests/independent-repro-round8.test.ts）**；**第九轮评审修复后复验（2026-10-16）**：3 项（同文件重写漏消息→水位锚点验有效/focus legacy 拒绝+类型约束/unread name 反向取名）经评审者复现手法 3/3 PASS，独立验证另修同族残留 1 处（同文件重写 max 不变时基线段锚比对绕过），终检 **npm test 509/509、test:py 116（1 平台跳过）**；**第十轮评审修复后复验（2026-10-16）**：2 项（锚点重建后 limit 分页卡第一页→重建路径允许水位回退写入，跨轮实测 [1,2]/[3,4]/[5,6]；名称过滤纳入备注/别名反向取名 + 命名来源不可用明确报 LOCALDB_UNAVAILABLE 不冒充空）经评审者复现手法 3/3 PASS，终检 **npm test 518/518（含独立复现）、test:py 122（1 平台跳过）**；评审通过后测试去重（2026-10-16）：独立复现 21 用例删除/6 用例移入模块/5 处补强断言，终检 **npm test 503/503、test:py 122**（注意：build:main 不清 dist，删测试源文件需同步清 dist/tests 产物） |
| P3 | content_type 扩展 + validate + runbook 文档 | 📋 待开发 | |
| P4 | local-db 群关键词搜索、群列表与时间线查询 | ✅ 完成（2026-10-22） | 最终独立TS64/Python100通过，CR修复复核通过；真实群搜索/limit/最近100/区间3条已验收；正文解析限制见P4记录，未提交 |
| P5 | 群发言人姓名与@提及恢复 | ✅ 完成（2026-10-22） | 独立TS15/Python127通过，CR及修复复核通过；真机100条姓名、20次提及及截图4例通过，80条文本结构可信；未提交 |

### P5 群发言人姓名与@提及恢复（2026-10-22）

设计依据：[§12.1](../design/wecom/wecom-cli-localdb-design.md#121-群消息用户名与提及恢复2026-10-22)。继续高风险开发、独立测试、独立CR；不提交或部署。本轮基线另存本机临时目录，保留此前P4及其他工作区改动。

| 任务 | 状态 | 验收标准 / 记录 |
|------|------|----------------|
| P5.1 结构调查与history专用解析 | ✅ 完成（2026-10-22） | 实证type0/2有序片段及ID0所有人，兼容type0原直接文本；严格varint/wire；未知片段原位标记，识别状态独立于占位文案 |
| P5.2 姓名与CLI/MCP输出 | ✅ 完成（2026-10-22） | 精确ID批量取名、历史mention名优先、未知姓名明确；JSON保留字符串ID/来源及完整性，人工输出显示姓名 |
| P5.3 独立测试与CR | ✅ 完成（2026-10-22） | 最终build/TS15、Python127全部通过、0跳过；旧read/unread/watch隔离65通过结合AST不变证据复用；独立oracle/fuzz12000组合通过；CR P2修复复核通过 |
| P5.4 截图对照及查看文件 | ✅ 完成（2026-10-22） | 真机100条全部命中sender_name、20次结构提及；80条文本结构可信、20条非文本占位；截图4例/时间升序/清理通过 |

**P5 真机验收（2026-10-22，5.0.12.6018）**：重跑 `read-group --target-name <目标群> --limit 100 --json`，`channel=local-db/effect=none`；100条发言人姓名全部命中，`naming_incomplete=false`，20次结构化提及，80条文本的`parse_trusted=true`，20条非文本仍占位，按发送时间/ID升序且无清理失败。用户截图中引用后短回复加@、引用后@加正文、连续@两人、@所有人四类均核对通过。P4记录的23条不可信文本属于修复前历史状态，本轮降至0；仅说明已知结构完整解析，未承诺非文本实体解析或逐条人工校对全部正文。

查看文件为受Git忽略的 `clients/wecom-cli/.tmp/group-acceptance-20261022/history-latest100-names.txt`；原ID/解析元数据保留于同名JSON，正文/原始blob/凭据不进文档或日志。独立CR发现合法正文与占位文案相同会误触fallback，已改为独立recognized状态并加入回归；新增type0直接文本/富文本兼容，旧`extract_content/_message_row/_message_anchor/_query_messages`与本轮前基线AST一致。未提交、部署或发布插件；原全量3项既有失败记录仍见P4。

### P4 本地库群查询（2026-10-22）

设计依据：[本地库设计 §12](../design/wecom/wecom-cli-localdb-design.md#12-本地库群列表与时间线查询2026-10-22)。高风险流程：主控开发和定向自测，独立测试、独立 CodeReview 后整合。不得提交或部署。

| 任务 | 状态 | 验收标准 / 记录 |
|------|------|----------------|
| P4.1 Python groups/history 查询 | ✅ 完成（2026-10-22） | 字面子串/limit/空群；发送时间最近N、闭区间、显式截断；实证适配5.0.12.6018的id TEXT；开发自测群21/query66/watch13通过 |
| P4.2 CLI/MCP 共用 operation | ✅ 完成（2026-10-22） | list-groups/read-group及2个MCP工具；复用身份/准入/快照/清理，无UI兜底；MCP真实发现/调用、manifest同源digest通过 |
| P4.3 独立测试及 CodeReview | ✅ 完成（2026-10-22） | 最终build通过；独立TS64/64、Python100/100（均0跳过）；CR及缺值参数/schema/解析标记修复复核通过；随机oracle375/375 |
| P4.4 真机与文档 | ✅ 完成（2026-10-22） | local-db关键词“测试”命中指定测试群；limit3返回3且has_more；目标群最近100条及10月区间3条均时间升序、无清理失败；README/设计同步 |

共同边界：只增加 Provider 工具，OperationResult/Runtime wire/binding/权限不变；工具与 digest 继续由 toolDefs 推导。主控唯一维护 TypeScript/CLI/MCP/文档；开发角色仅维护 Python 驱动及新测试，独立验证角色不改共享源码。

**P4 验证记录（2026-10-22）**：本机 Windows / 企业微信 5.0.12.6018；所有真实调用返回 `channel=local-db/effect=none`，不执行界面搜索或切换会话。指定群“新劳务云系统项目沟通群”最近100条范围为北京时间2026-05-29 14:19:17至2026-10-22 17:30:05，按发送时间/消息ID升序，`has_more=true`、`invalid_time_count=0`。区间2026-10-01 00:00:00至2026-10-22 23:59:59返回3条、`has_more=false`，与最近100条按同区间过滤的ID集合完全一致。100条中20条非文本占位、23条文本为递归提取（`parse_trusted=false`，正文准确性未确认）；人工输出及查看文件明确标记，不能宣称100条正文均精确解析。查看文件仅保存在受Git忽略的 `clients/wecom-cli/.tmp/group-acceptance-20261022/`，不在日志/文档登记正文、实际账号或群ID。

**既有回归缺口**：初轮Node全套510项为507通过/3失败（fingerprint 1项、operations-m3 unread 2项），对应源码与测试与任务前基线哈希一致；后两项未隔离宿主doctor状态，测试只mock UI但会切到本地库，因此停止重复运行，不扩大本批修复。该全量结果不得称为全绿。旧D1静态清单未增加新工具，未发布/安装Runtime插件；本批只验收本地CLI和本Provider标准MCP。

### P1a 验收记录（2026-10-15）

> **本轮身份修复（2026-10-15，独立测试与 CR 通过）**：CEF 降为候选，删除进程时间/容差/日志身份准入；复用操作主窗口 PID，完整查询其 File 类型磁盘句柄，以唯一普通本地数字账号目录下的 `Data/message.db` 确认目录。无/多账号、权限或枚举失败明确未确认，旧日志不背书；直接证据不受日志缺失/冲突、mtime 或在线时长限制。跨文件候选统一按行事件时间排序。矩阵按主窗口 PID 实际版本准入，真正扫描时才核验其余 PID 同版本，不限制找钥只能在主窗口。静态只读已核对真实主窗 PID 与唯一消息库；开发自测 typecheck/build 通过、定向 Node 83/83、Python 新增 2/2（真实合成 WXWork.exe 同 PID 句柄切换/多账号/释放 + 映像拒绝）。独立验证：`npm test` exit 0，34 文件 419/419 通过、0 跳过（本轮唯一全量 Node 回归）；定向 Node 83/83；Python 全量 98 项、97 通过/1 个非 Windows 分支跳过，exit 0；typecheck/build exit 0；PS1 parser 通过。额外独立合成 WXWork.exe 真实 active-account CLI 4/4（唯一 111→多账号拒绝→同 PID 唯一 222→零句柄拒绝），重复 5 次查询前后测试进程句柄数相同；定向版本探测返回辅助映像实际 AssemblyFileVersion 5.0.11.6029/source=process。主控独立 CR 通过，并亲自通过 typecheck、dist doctor/datadir import 和 PS1 BOM 检查，全程仅评审验证、未修改实现。真实退出登录、同 PID 切号、锁屏仍待用户配合，归 1.14。以下第六/七轮记录为历史验证，原日志身份证据已由本轮替代。

> 收尾执行：1.13 启动清扫接线（doctor 步骤 1 前调用 sweepArtifacts，清扫失败只记日志不失败）完成后全量实跑。真机环境：Windows，企业微信 5.0.12.6018，登录态（WXWork 5 进程）。
> 全量验证：`npm run typecheck` 通过；`npm test` **387/387 全绿**（localdb TS 用例 162 条，其中 doctor 31 条含本次新增启动清扫接线 2 条）；`npm run test:py` **91 条 OK**（1 条为非 win32 平台门禁跳过；含 keyscan 合成进程端到端命中）。
> **第六轮评审修复后复验（2026-10-15）**：8 项修复（身份绑定/EPIPE/清扫存活规则/快照重试 WAL 重判/取消等 close/状态保存失败提示/SCHEMA_UNSUPPORTED 真实协议/测试环境密闭）经独立测试用评审者复现手法逐条复核 8/8 PASS；无 SESSIONNAME 环境多轮全量零失败；终检 typecheck 通过、**npm test 401/401、test:py 96（1 平台跳过）**。
> **登录驱动已知限制（三态验收须覆盖）**：localdb-login.ps1 把托盘隐藏、无可识别主窗口归为 offline——后台/最小化运行场景会被判未登录。该限制须在退出登录、同 PID 切号、锁屏三项验收中一并确认实际表现，不能因移除交互会话门禁就认定已支持后台。
> **第七轮评审修复后复验（2026-10-15）**：3 项遗留（历史 CEF 行背书身份/owner 发布空窗误删/保存失败自称已启用）经独立测试用评审者复现手法逐条复核 3/3 PASS；终检 typecheck 通过、**npm test 410/410**。identity 语义现为「最新绑定事件 + 文件 mtime ≤24h 时效窗」（历史一致行不再背书，最新事件冲突即拒绝）；workdir 改两段发布（暂存名对清扫不可见，rename 原子发布，崩溃泄漏由 pid 死活兜底清扫）；保存失败时报告不含「已启用」表述。
> **第七轮二次意见修复后复验（2026-10-15，身份证据根因）**：identity 语义再重写为「**锚定当前进程实例**」——version.ts 探测全部 WXWork 进程取最早 CreationDate 为实例锚点（失败→null 保守拒绝），datadir 只认**行级时间戳 ≥ 锚点−2min 容差**的会话内最后一条带 account_id 绑定行（无账号行不回退历史绑定），**24h TTL 与全部 mtime 时效逻辑删除**（无关追加/陈旧 mtime 双向均不影响判定，grep 零残留）；doctor 步骤 3 锚点缺失 → process-anchor-unavailable 保守拒绝。评审者复现场景（旧绑定 + 新上下文缺失）翻转为 false 并归因 no-binding-in-current-session；真机冒烟：锚点=主进程创建时刻、绑定行晚 4 秒（浏览器进程初始化写入）、identity_confirmed=true。独立验证 32/32 PASS；终检 typecheck 通过、**npm test 420/420、test:py 96（1 平台跳过）**。

**逐任务状态（1.1–1.14）**

| # | 状态 | 验证证据（一句） |
|---|------|------|
| 1.1 | ✅ | version/running/matrix 三组件 + 矩阵进打包产物（copy-matrix.mjs + dist 优先）；真机冒烟 5.0.12.6018 → unknown 禁止硬扫；三轮评审修复后 253/253 全绿 |
| 1.2 | ✅ | 直接句柄证据优先，CEF 按事件时序只选候选；旧日志无准入权；本轮独立测试与 CR 通过，三态归 1.14 |
| 1.3 | ✅ | fingerprint.ts 库头魔数+明文页头双匹配（页参数漂移可检）+ 可选 exe 常量指纹（默认关闭）；15 用例；真机指纹匹配由批次 4b doctor 步骤 5 实证 |
| 1.4 | ✅ | TS 捕获编排（H0/H1 头比较+基线 stat+边界语义）11 用例 + py 冻结点 1 场景表 22 用例（坏链/撕裂截断/未提交尾帧/错钥等，失败场景全部断言不产生半解密产物）；**场景表全过，生产捕获规则冻结** |
| 1.5 | ✅ | py decrypt（页 1 先知快速失败→逐页解密→有效前缀+提交边界重建 WAL、明文校验和重算→integrity_check）；独立参考实现 ref_encryptor.py（sqlite3mc cipher_wxaes128.c 规格）生成固定向量，6 用例消费独立向量非自往返 |
| 1.6 | ✅ | keyscan 三层验证 13 用例（独立向量先知/对齐预筛+回退纯函数/合成进程端到端）；**接线验证点真机实证（2026-10-15）**：持钥进程恰为 CIM 首选 PID 51788（命中 2.72s/11.26MB，两轮稳定），非持钥 UI 进程 34844 回退全量 153.4s 未命中——doctor 以 detectRunning 全列表候选正确覆盖密钥位置 |
| 1.7 | ✅ | keystore.ts DPAPI 存储/绑定三元组/失效语义 7 用例 + scripts/smoke-dpapi.mjs 真机冒烟（手动）；批次 4b doctor 全链 keystore 复用命中 |
| 1.8 | ✅ | py query 只读 SQL 四任务，契约按 0.1 结论冻结（水位=last_message_id 行、cursor 只认返回集）；合成明文库 50 用例（迟到同步/同刻排序/排除表双向语义/同名消歧） |
| 1.9 | ✅ | C9a extract_content 严格解析+递归兜底标 trusted=false（py 场景表）+ C9b messages.ts 契约归一（P0 最小类型集、非文本占位）+ 游标编解码，12 用例（对 py 输出不信任的显式边界） |
| 1.10 | ✅ | 登录探针复用操作主窗面积选择与并列歧义，返回 window_pid；独立测试与 CR 通过；真实登录态只读核对，三态仍留 1.14 |
| 1.11 | ✅ | 接入主窗 PID 的 active-account 与定向版本核验；未确认不得进入密钥/快照；缓存复用不探测其他候选；独立测试与 CR 通过 |
| 1.12 | ✅ | log.ts 五类事件 JSONL/轮转/整行脱敏 11 用例；真机 localdb.log 实测 mode_decided/keyscan_result 记录正常 |
| 1.13 | ✅ | cleanup.ts sweepArtifacts 9 用例（owner 存活/陈旧挂名/删除失败收集不中断）+ doctor 启动清扫接线 2 条（收尾批次）；真机 artifacts/localdb 全程无残留；P1b 已接取消路径「等待 py close 后删目录」，合成回归通过 |
| 1.14 | 🔧 部分 | 自动化项完成：全量三链全绿 + doctor 真机 + keyscan 直测×2（见上/下）；未登录/登录/锁屏三态 + 登录证据三态（进程在已登出、同 PID 切号、锁屏）+ 锁屏本地库只读可用性实证待用户配合 |

**真机实测（2026-10-15，登录态，只读边界遵守）**

- **doctor --localdb**（当前矩阵仅 5.0.11.6029）：步骤 1-3 绿（PID 51788 / 版本 5.0.12.6018 / 账号 1688854831832237 目录命中），止于步骤 4 `ui(compat)`「未知版本禁止硬扫」——本机 5.0.12.6018 不在矩阵属**正确行为**；退出码 0，全程 ~1.8s；localdb-state.json 正确落盘 {mode:ui, kind:compat}。未注入临时矩阵条目（该验证已于批次 4b 完成）。
- **keyscan 真机直测**（绕过 doctor，venv python 直驱，--db 只读库页 1 4KB）：
  - 单 PID 34844（`--switch-account` UI 进程，非持钥）：对齐预筛+全量步长 1 回退共 153.4s 未命中——补 §9.5 回退段成本数据；
  - 全候选 5 PID × 2 次：均命中 PID 51788（`/upgraderun` 进程，恰为 doctor 步骤 1 CIM 首选），aes128，2722ms / 2732ms（波动 0.4%），扫描 11,255,808 字节——命中稳定；key_hex 未记录（stdout 即看即弃，不入任何日志）。
  - 5.0.12.6018 兼容性证据（批次 4b 临时注入实测 + 本机日志在案）：指纹匹配 + 密钥页 1 验证 + 104 页整库及 WAL 全解密 integrity=ok + 查询冒烟通过，doctor 全链 mode=db。
- **残留与状态**：`artifacts/localdb/` 全程为空（启动清扫无对象 + 工作目录 finally 清理 + keyscan 不产生产物）；原始库文件 mtime 未变化（只读保证成立）；localdb-state.json 与 logs/localdb.log 内容符合预期（无 sweep_failed；keyscan 直驱不经 TS 桥、不写日志属预期）。

**冻结点状态**

| 冻结点 | 状态 |
|---|---|
| 1 可信快照生产规则 | ✅ 已冻结（批次场景表全过：正常含旧代尾帧截断/坏校验和链/撕裂截断/未提交尾帧/错钥等，失败场景均不返回半解密产物、不推进水位） |
| 2 P0 结论（水位基元/发送核验语义） | ✅ 已冻结（2026-10-14，见「P0 结论摘要」） |
| 3 P1b 错误分流表 | ✅ 已冻结（2026-10-15，实施方案经主控评审通过；见设计 §2 冻结点 3） |
| 4 P2 关注集合 schema | ✅ 已冻结（2026-10-16，见设计 §2 冻结点 4；3.1 已按冻结 schema 实现 focus 字符串数组形态，watch/水位存储形态归 3.2） |
| 5 登录判定规则 | ✅ 已冻结（三态验收：A 退出登录/B 切号 7 项 PASS；C 锁屏不适用【部署 VM 永不锁屏】；判定=主窗 PID→进程句柄 message.db 直接证据，日志不背书） |

**需用户配合项清单**

1. **登录判定三态验证**（冻结点 5 前置，需用户在真机配合操作）：
   - ① 退出登录但进程仍在：验证 probe 判定（need_login/offline）+ datadir 身份证据仍可读时本地库路径正确拒绝；
   - ② 同 PID 切换账号：验证 keystore binding 失效重扫 + datadir 定位切换到新账号目录；
   - ③ 锁屏三态：登录态探测在锁屏下的表现 + **锁屏下本地库只读可用性实证**（设计 §9.6 理论收益验证）。
2. **矩阵是否加入 5.0.12.6018 条目**：证据已齐（指纹匹配 + keyscan 命中 + 全链解密 integrity ok；批次 4b 临时注入 doctor 全链绿灯），等用户拍板后写入 version-matrix.json 并登记 verified_date，随后重跑 doctor --localdb 复核 mode=db。
3. **提交记录**：P1a 已按用户授权提交 `8a1f3b1f`，当前分支 `feature/ask-user-question-card` 已推送 Codeup；GitHub 镜像 TLS 失败待处理。P1b 已获本轮提交授权，仅提交已评审范围；后续开发由原开发者继续，主控恢复评审角色。

**已知限制与 P2 备案汇总**（随批次发现累积，长期有效）

| 限制 | 说明 | 处置备案 |
|---|---|---|
| PowerShell 5.1 OEM 代码页解析 | 含中文的 ps1 驱动必须 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1 按 GBK 解析报错（localdb-login.ps1 已带 BOM） | 长期注意：编辑工具链保持 BOM |
| defaultExecFile 等义复制 | running.ts/version.ts 各复制一份 environment.ts 未导出的 defaultExecFile（5s 只读命令超时同语义） | P2 重构：导出共享 helper |
| 矩阵 dist 优先的源码态陈旧性 | loadMatrix 先读 dist 副本（发行布局），改 src 矩阵后未重建时 dist 陈旧 | copy-matrix 已挂 build；维护者须知 |
| cross_check 语义混同 | Config.cfg 不可解析时与「无直记键」不可区分（同落 'no-id-key'），身份契约属设计侧问题 | 设计评审/P2 |
| CEF existsSync 网络路径阻塞 | CEF 命中根上的同步 existsSync 遇死网络路径可能阻塞（本机实测步骤 3 仅 13ms，未复现） | P2：候选根探测异步化 |
| keyscan payload 未区分对齐/回退段 | 返回值只有 scanned_bytes/elapsed_ms，命中无法归因对齐预筛还是全量回退段（§9.5 命中率归因不完整；本机 2.7s 命中量级支持对齐段有效，但无字段证实） | P2：payload 增加段级计数 |
| 实验代码 decrypt_wal 缺陷 | experiments 的 decrypt_wal 未重算明文校验和（与 docstring 不符），正确逻辑在 decrypt_full；工程化已按 §3.3 重做 | 已记录（P0 结论）：实验分支不可照搬 |

## P0：前置调查（真机，产出结论回填设计 §4/§9；结论+证据是后续冻结点依据）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 0.1 | message_table schema 调查 | ✅ 完成（2026-10-14） | `message_id INTEGER PRIMARY KEY AUTOINCREMENT`（=rowid），18 列，sqlite_sequence 存在；196 行 max=197 空洞 1（无害，cursor 按 ≤水位）；message_id 全唯一，与 rowid 序逆序对 0/19110；**send_time=秒**（1.7e9）；同秒 7 组 15 条（sequence 列同秒递增可决胜）；server_id 全局唯一 |
| 0.2 | 迟到同步实证 | ✅ 完成（2026-10-14，库内证据） | 同会话 rowid 序 send_time 回退仅 2 例且均为系统/控制类消息（ct=1017/38/31），陆伟会话 0 例；迟到插入获得更大 message_id → **rowid/message_id 水位天然覆盖迟到同步**；手机端实时落库延迟未测，留待 P1b 联调 |
| 0.3 | 打包与移植依赖验证 | ✅ 完成（2026-10-14） | pyCryptodome 4.0.0 已入本机 venv（子代理安装）；打包态 = `ocr-assets.lock.json` 加 1 条 wheel 条目 + 重跑 prepare-ocr.py（+7.3MB，无门禁冲突）；DPAPI 移植 = 拷 weixin-cli `src/security/dpapi.ts` 单文件（PowerShell ProtectedData，零 npm 依赖，~1.5-2h 含测试） |
| 0.4 | 群会话解析调查 | ✅ 完成（2026-10-14） | session.db `conversation_table`（72 行）含 name/room_info_pb，R: 群名→id 实测唯一命中（name 无唯一索引，需防重）；S: 经 user_table；Y: 应用号 2/17 会话无法解析显示名，显式降级 |
| 0.5 | WAL 旧代尾帧实观 + 登录证据手段 | ✅ 完成（2026-10-14，候选证据调查） | 真实 WAL 6 帧中 4 帧旧代尾帧（旧 salt=新 salt−1），有效前缀 2 帧含 1 提交帧——评审合成场景真实命中，**必须显式按代过滤**；登录证据候选：Config.cfg 登录键（kPollCorpMessageSyncKey 活跃游标/LastStorageBackupTimeKey 当日）+ 文件 mtime + 进程存在，窗口标题无账号信息——**均为辅助信息非硬门禁**（页 1 核验≠当前已登录，评审 v5），判定规则待 1.10 原语 + 三态验证冻结 |

### P0 结论摘要（冻结依据，2026-10-14）

1. **水位基元冻结：`message_id`**（AUTOINCREMENT 主键=入库序；rowid 0 逆序为同字段单调性验证，非写入策略独立证明）。覆盖范围限定：**迟到消息以新记录插入取得更大 message_id 时被覆盖**（不覆盖原地更新，全同步路径行为待 P1b 手机端联调）；cursor 在 message_id 空间（绑定账号/会话/格式版本），**不用 send_time（秒级且系统消息可迟到回退）也不用 sequence 做游标**；同秒展示排序以 server_id/sequence 决胜。
2. **发送核验范围冻结：无发送状态字段**（flag 与方向无关，方向=sender_id 比对；send_failed_unnotified 表存在但恒空）。核验命中=本地已有记录，映射：db 命中 → success/applied + `data.verification='local-record'`；OCR 命中 → applied + `'ocr-bubble'`；均无 → EXECUTION_UNKNOWN。**success/applied 语义=客户端动作已记录并经本地核验，不得被消费侧解释为对方已收到/已读**。
3. **快照协议：普通样本已验证，专项验证待完成**：H0/H1 头比较可行且通过（复制窗口 6ms）；真实 WAL 4/6 帧为旧代尾帧 → §3.3 有效前缀过滤必须显式实现（decrypt_full 靠 SQLite 自行忽略属侥幸语义）；**并发 checkpoint/reset/截断/撕裂场景按 1.4 场景表专项验证后才冻结生产规则（含失败场景不返回不可信结果、不推进水位）**。
4. **DPAPI 移植与打包路径明确**（0.3），依赖声明落点（venv README 惯例 vs requirements）在 1.5/1.6 开发时冻结。
5. **登录判定未证明**：P0 仅完成候选证据调查，Config.cfg/mtime/游标均为辅助信息；判定规则待 1.10 登录探测原语交付 + 三态验证（已登录→退出但进程在、同 PID 切号、锁屏）后冻结。

### 性能实测：单人聊天历史端到端（2026-10-15，真机 5.0.12.6018，3 轮取中位）

| 阶段 | 耗时 |
|---|---|
| 快照复制（db+wal，1.28MB） | 0.88ms |
| 页 1 密钥核验 | 4.41ms |
| 整库解密 + WAL 明文校验和重建（104 页） | 7.11ms |
| 打开 + WAL 回放 + 定位陆伟会话 | 13.50ms |
| 提取该会话全部消息（11 条，含 protobuf 文本） | 0.18ms |
| **端到端合计（缓存密钥复用，0 扫描）** | **27.26ms** |

- 提取吞吐：**4.47ms/千条**（~16µs/条）；外推 1000 条 ≈ 4.5ms、10000 条 ≈ 45ms（仅解析环节）。
- 解密成本随**整库页数**线性增长（~68µs/页），与会话数无关——大库时期单次调用成本仍为亚秒级（万页 ≈ 0.7s）。
- 冷路径（现场扫描）本轮未复测，历史值 266s @5.0.11。
- **5.0.12.6018 兼容性证据**：库头指纹匹配 + 昨日密钥页 1 验证通过 + 104 页整库及全部 WAL 帧解密成功 + integrity=ok——现有 wxaes128 方案在升级后实际有效，升级未改加密方案与主密钥。按 runbook 尚差 doctor 深检/动态冒烟正式流程，矩阵加条目前补齐。
- 实验代码缺陷记录：`decrypt_wxaes128.decrypt_wal` 函数体未重算明文校验和（与 docstring 不符，SQLite 会拒绝回放），正确逻辑在 decrypt_full——工程化按 §3.3 重做，实验分支不可照搬。
- 环境事实：message.db 内无 user_table（在 user.db），会话定位走 conversation_id LIKE 路线（已计入 13.5ms）。

## P1a：提供者层组件 + doctor 深检（正确性优先序）

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 1.1 | C1/C2/C4：安装检测、运行检测、矩阵加载 | ✅ 完成（2026-10-14/15，三智能体流程 + 两轮代码评审修复） | version/running/matrix + version-matrix.json + 单测；真机冒烟：进程 executable 实测 5.0.12.6018（已自动升级，非矩阵版本 → unknown 禁止硬扫，设计预期成立）；首轮 CR 修复原型键穿透；**外部评审修复轮**：矩阵进打包产物（copy-matrix.mjs + dist 优先加载 + 发行布局冒烟）、版本来源区分（进程/安装目录，防残留版本冒充准入）、矩阵/状态契约收紧（schema 门槛 + validated 必需字段 + mode/kind 枚举）、tasklist 探测状态 probe 字段、PID 严格解析；复审修 version 探针载荷断言脱钉；**修订版评审修复轮**：validated 字段类型/值校验（非空串 + page_size 正整数，6 复现值全降级）、ui 状态 kind 必填契约（类型收窄 + 非法组合 → null）、防冒充用例前提钉死（完整合法条目 + 前置 validated 断言）、发行布局素材改用 dist 产物（脚本失效测试变红实测）；终检 typecheck 通过、253/253 全绿 |
| 1.2 | C3：数据目录定位 + 账号↔目录交叉核验 | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 主窗口 PID 的完整磁盘句柄枚举确认唯一普通本地 `Data/message.db`；历史 CEF/Config.cfg/mtime 不作确认依据，CEF 候选按事件时间统一排序；开发自测 datadir 25 项通过，真实切号/登出转移仍待 1.14 |
| 1.3 | C5：指纹预检 | ✅ 完成（2026-10-15） | fingerprint.ts：库头魔数+明文页头双匹配（页参数漂移可检）+ 可选 exe 常量指纹（默认关闭，runbook/P3 显式启用）；15 用例；真机指纹匹配由批次 4b doctor 步骤 5 实证 |
| 1.4 | **可信快照协议**（py + TS 编排，设计 §3.3） | ✅ 完成（2026-10-15） | snapshot.ts 捕获编排（H0/H1 头比较+基线 stat+失败显式 reason）11 用例 + py snapshot-verify/decrypt 场景表 22 用例（坏链/撕裂截断/未提交尾帧/错钥等，失败场景全部断言不产生半解密产物）；**冻结点 1 场景表全过，生产捕获规则已冻结** |
| 1.5 | C7：解密 + 独立来源固定测试向量 | ✅ 完成（2026-10-15） | py decrypt：页 1 先知快速失败→逐页解密→有效前缀+提交边界重建 WAL（明文校验和重算，实验 decrypt_wal 缺陷不照搬）→integrity_check；独立参考实现 ref_encryptor.py 按 sqlite3mc cipher_wxaes128.c 规格生成固定向量，test_independent_vectors 6 用例消费独立向量（非自往返，含错钥快速失败与 fixture 自检） |
| 1.6 | C6：keyscan（去 psutil、对齐预筛+回退） | ✅ 完成（2026-10-15） | test_keyscan 13 用例三层（独立向量先知/对齐预筛+回退纯函数/合成进程端到端命中）；**接线验证点真机实证（收尾批次）**：持钥进程恰为 CIM 首选 PID（对齐命中 2.72s/11.26MB 两轮稳定），非持钥 UI 进程回退全量 153.4s 未命中——doctor 以 detectRunning 全列表为候选正确覆盖实际密钥位置 |
| 1.7 | C6：DPAPI keystore + 生命周期（设计 §3.4） | ✅ 完成（2026-10-15） | keystore.ts：DPAPI(CurrentUser) 存储 {binding 三元组, key_blob, saved_at}，binding 不匹配顺带清除；**仅页 1 核验明确失败才删密钥**（doctor 编排侧）；7 用例 + scripts/smoke-dpapi.mjs 真机冒烟（手动）；批次 4b doctor 全链 keystore 复用命中 |
| 1.8 | C8：读库查询（conversations/messages/last-per-conv/resolve） | ✅ 完成（2026-10-15） | py query 只读 SQL（uri ro），契约按 0.1 结论冻结（水位=last_message_id 行、cursor 只认返回集）；合成明文库 50 用例（含迟到同步、同刻排序、排除表双向语义、同名消歧、来源库缺失降级） |
| 1.9 | C9a/C9b：内容提取（带 trusted 标注）+ 契约归一 | ✅ 完成（2026-10-15） | C9a extract_content 严格解析+递归兜底标 trusted=false（py 场景表）；C9b messages.ts 契约归一（P0 最小类型集、非文本占位 kind 省略）+ 游标编解码（P1a v1；P1b 升级 v2，绑定 account/conv/源库代际与非零水位锚点），12 用例（对 py 输出不信任的显式边界） |
| 1.10 | 轻量登录探测原语（从 probe 抽取登录判定核心，不带交互桌面门禁） | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 原有只读登录谓词保持，主窗口 PID 按操作定位面积最大规则选择，最大并列不给 PID；无新增交互门禁，托盘隐藏限制与三态验收仍未关闭 |
| 1.11 | doctor 深检（随组件增量点亮，设计 §5） | ✅ 完成（2026-10-15，本轮独立测试与 CR 通过） | 步骤 3 查询主窗 PID 唯一账号库；步骤 4 定向探测同一 PID 的实际版本；需要扫描时才验证额外候选同版本。任一身份失败禁止密钥与快照，开发自测 doctor/login/version/datadir 共 83 项通过 |
| 1.12 | 结构化日志 logs/localdb.log + redaction 接入 | ✅ 完成（2026-10-15） | log.ts logLocaldbEvent（五类事件 JSONL/ISO UTC/未知 type 拒写）+ >5MB 轮转保留 .1 一个历史（轮转失败不阻断写入）+ 整行 redactSensitive；redaction.ts 扩展 32-hex 密钥样式串兜底（设计 §8，先于手机号规则防 hex 内数字段被改写）；deps 注入 fs/clock/dir；11 用例 |
| 1.13 | 遗留产物清扫（owner 存活校验）+ 删除失败报告 | ✅ 完成（2026-10-15） | cleanup.ts sweepArtifacts：owner.json 缺失/坏 JSON/pid 非法或死亡 → 删；存活且启动时刻一致 → 跳过（不按在线时长删除）；PID 已复用 → 删；存活但启动时刻不可判 → 保守跳过；删除失败收集 {dir,error} 不抛不中断；只动 base 直接子目录；9 用例；P1b 组合任务已按 driver.ts 语义等待子进程 close 后清理目录（合成取消回归通过）；**收尾批次接线：doctor 启动清扫已接（步骤 1 前调用，失败只记 db_fallback(cleanup/sweep_failed) 日志不阻断），2 用例** |
| 1.14 | P1a 验收：doctor 全链真机绿灯 | ✅ 完成（A 态 6/6 PASS、B 态 7/7 PASS 零串号全过、C 态不适用【部署 VM 永不锁屏，用户 2026-10-20 确认】；全链 db 绿灯随矩阵条目加入后复核——验收说明与执行记录见 [wecom-cli-localdb-three-state-acceptance](wecom-cli-localdb-three-state-acceptance.md)） | 全量三链实跑全绿（typecheck / npm test 387/387 / test:py 91 含 1 平台跳过）+ doctor 真机止于步骤 4 ui(compat)（5.0.12.6018 不在矩阵，正确行为）+ keyscan 直测×2 稳定命中 ~2.7s（详见「P1a 验收记录（2026-10-15）」）；未登录/登录/锁屏三态 + 登录证据三态（进程在已登出、同 PID 切号、锁屏）+ 锁屏下本地库只读可用性实证待用户配合 |

## P1b：read-session 本地库通道组合

### P1b 验收记录（2026-10-15，独立验证与最终 CR 通过，真机项待验）

- 已实现 2.1–2.4：专用只读执行器复用身份/版本/密钥/快照/清理原语；CLI/MCP 增量参数同源，保留 UI `model/ocr` 语义；未修改 Runtime wire、设备 binding、发送或 P2。
- v2 cursor 绑定源文件稳定身份与 schema_version，非零水位锚点使用消息稳定字段摘要，排除可后补的 server_id/sequence；空会话水位 0 续读按最早新 ID 分批，不重走最近 N 条。目录账号须在 user_table.id 唯一匹配后才用于 sender 域比较，ID 全程字符串。
- 合成整链采用独立参考加密器生成三库，真实执行捕获/解密/只读查询；覆盖 64 位 ID、最近 100 条、ACK 元数据补齐、源库替换/水位复用、空群/空 contact、来源缺失/坏 schema、可选 session 损坏、同名无历史联系人消歧、模式提升、强制通道、账号绑定优先、搜索前后切号、跨进程扫描负缓存、取消等待 close 与清理失败回报。平台自测为 Windows，无平台跳过。
- 验证：`npm run typecheck` / `npm run build:main` exit 0；13 文件定向 Node 203/203、0 跳过，exit 0；`python -m unittest discover -s tests/py -p test_localdb_query.py -q` 55/55，exit 0；最后补入搜索账号探针成功时取消的断言后，typecheck/build 与相关 3 文件 27/27 再次通过。源码与测试保持冻结，主控全程仅评审和验证；本阶段全包回归由独立测试统一执行。
- 最终 CR 修复：P1b 候选消费只接受 user_table 的 S 联系人命名与 conversation_table 的 R 群命名，拒绝用 session 的旧 S 缓存标题替代联系人名称；session 的 S 仍可证明 user 命中后的实际空会话。新增真实合成 user 改名/session 保留旧名回归，旧名在名称读取与绑定 contact ref 均 TARGET_NOT_FOUND，新名正常读取；已有空 contact 水位 0/续读回归继续通过。修复后 typecheck/build exit 0，定向 2 文件 24/24、0 跳过，exit 0；源码再次冻结；独立真实签名 ref 与名称读取复验均确认旧名拒绝/新名成功，空 contact 水位 0 续读通过，主控最终 CR 通过、无剩余阻断项。
- 独立回归边界：包级 Node 431/431 通过对应最后名称来源修复之前的代码状态，未受影响部分复用该结果；最终状态通过上述 typecheck/build、2 文件 24/24 及独立名称/ref/空 contact 定向复验，未声称最终状态重新全量。另用 6 个新 Node 进程确认 db/unset 模式扫描负缓存跨进程保留、doctor epoch 变化后重试。独立测试未重跑 Python 全量，复用开发查询 55/55；CLI/MCP 参数另经静态核对，最终结论无阻断交付问题。
- 合成测试产物残留：独立测试者清理 3 个仅含合成数据的临时根被自动审批拒绝（blocked by policy，未提供具体原因），当前未删除、未代删；边界已校验的递归删除仅返回拒绝后已停止尝试，不宣称全程无残留。路径：`C:\Users\PC\AppData\Local\Temp\p1b-independent-2e6b2a1057354ea994735b1c69d0c3ae`、`C:\Users\PC\AppData\Local\Temp\p1b-cache-independent-75bf1ff2157842f5bfe9fe279111beaa`、`C:\Users\PC\AppData\Local\Temp\p1b-cache-independent-b1c0727d1e6c46e5aded9cfc9d38368b`。
- 2.5 实机同会话 UI 对比、手机端实时同步、CPU/磁盘/工程化整链性能未验：当前实际版本 5.0.12.6018 仍不在矩阵，未添加条目、未写真实 state/keystore、未读取真实聊天或操作 UI。P1a 1.14 退出登录/同 PID 切号/锁屏仍待验，P1a/P1b 整阶段均未标完成。
- 主控复核（2026-10-16，接手后续阶段前）：基线三链实跑全绿（typecheck 通过、npm test 432/432、test:py 96 含 1 平台跳过）；核心代码逐文件核对——执行器严格版本准入/负缓存上下文/会话内身份校验/cursor v2 锚点与生成代际/读后账号与源文件复核、operation 层通道分派与冻结点 3 兜底白名单（cursor 三处 CURSOR_UNSUPPORTED、legacy ref DB 拒绝 UI 标注、能力差异说明、ui(transient) 提升带 checked_at 防并发）、身份链（主窗 PID→进程句柄 message.db→目录，强于 CEF 日志证据）均与冻结点 3 契约一致；**范围核实：本提交仅覆盖 P1b 2.1–2.4，P2（watch 锁修正/unread/watch 本地库通道/发送核验接线）未交付，计划 P2 仍为待开发**。


| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 2.1 | read-session operation 接入本地库通道（channel 值域 + fallback_reason + 安全网预检） | ✅ 完成（2026-10-15） | 按 §2 状态转移表与**错误分流表**实现（分流表为冻结点 3：本任务前评审冻结）；取消/参数/游标/账号/目标歧义不兜底；SNAPSHOT_UNSTABLE retryable 映射 |
| 2.2 | 游标增量（limit=100 默认 + cursor 绑定） | ✅ 完成（2026-10-15） | 按 0.1 冻结的水位基元实现；limit 只推进到已返回消息；CURSOR_UNSUPPORTED 结构化失败 |
| 2.3 | target_name/target_ref → conversation_id 解析 + 同名消歧 + ref 账号绑定 | ✅ 完成（2026-10-15） | TARGET_AMBIGUOUS 附候选；新签发 ref 携带账号；**legacy ref 在 DB 路径一律 TARGET_REF_STALE 不执行读取**（评审 v4 #35）；既有 UI 路径保留 legacy 兼容 |
| 2.4 | mode=unset / ui(compat/transient) / db 行为接线 + hint | ✅ 完成（2026-10-15） | unset 不阻断 UI 能力 + 引导 hint；ui(transient) 负缓存到期单次探测与运行时提升；契约增量（msg_id/parse_trusted/cursor）；**评审 v6 追加：ui(transient) 运行时提升 db 前必须要求 identity_confirmed=true（reason=identity-unconfirmed 期间禁止提升）** |
| 2.5 | 真机读取冒烟 + 整链实测 | 🔧 合成独立验收通过，真机待验 | 与 UI 通道结果比对（同会话同消息）；耗时/CPU/磁盘实测（回填设计 §9）。**前置基线已测（2026-10-15，见「性能实测」节）：热路径端到端 27.26ms（11 条会话），P1b 验收时对比工程化实现与 UI 通道** |

## P2：待回复检测 + 发送本地库核验

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 3.1 | unread_list 本地库通道（待回复 = 最后一条**可参与回复判定**的消息发送方为对方） | 🔧 部分完成（2026-10-16，批次 A 开发自测通过） | 分派矩阵对齐 readSession 范本（unset→UI+hint / ui(compat)→UI / db、ui(transient)→本地库；白名单 VERSION_UNSUPPORTED/KEY_SCAN_FAILED/SNAPSHOT_UNSTABLE/LOCALDB_UNAVAILABLE 兜底，取消/身份/focus 解析类不兜底，强制 local-db 保留具体错误）；本地库执行器 `platform/localdb/unread.ts`（context 任务 user_table.id 唯一映射 → py `last-per-conv` 待回复双向判定 → focus 逐名 resolve 部分返回）；密钥/快照解密段提取共享 helper `platform/localdb/access.ts`（readSession 同段手术式搬移，行为不变）；输出 `channel='local-db'` + `judgment_basis='last_sender'`，conversations 仅待回复（unreplied:true），自会话不出现，全系统类计数标注；UI 通道加 `judgment_basis='badge'` + 能力差异说明，旧字段原样保留，focus 忽略并注明；MCP schema 增 channel/focus，CLI 增 --channel/--focus。合成测试 17 条（分派矩阵 8 + 真实三库整链 9：双向判定/自会话排除/focus 部分失败/版本准入 forced/missing-self/可选 session/清理失败/keyscan 负缓存）；真机冒烟：compat 态兜底 UI 行为不变、强制 local-db 报 VERSION_UNSUPPORTED 不兜底 | 
| 3.2 | watch_poll 本地库通道 + 关注集合（水位增量独立于待回复判定） | 🔧 部分完成（2026-10-16，批次 B 开发自测通过） | py 增 `watch` 任务（多会话独立 limit 增量、watermarks 经 workdir 临时 JSON 传入、last_sender_state 按最后可参与判定消息判向、next_watermarks 只含返回过消息的会话=水位只推进到已返回消息）；TS 执行器 `platform/localdb/watch.ts`（复用 access.ts 共享段 + unread 的 resolveFocusConversation；DB 水位存储 `platform/localdb/watchDbState.ts`→`localdb-watch-state.json`，账号变化全量重置基线；首次解析成功会话基线=当前 max(message_id) 只报之后）；operation 分派对齐 unread 范本（unset→UI+hint / ui(compat)→UI / db、ui(transient)→本地库），**DB 不可用一律结构化 `{ok:false, code, db_unavailable:true}` 不落 UI**（含强制 local-db），取消/IDENTITY_UNCONFIRMED 不吞；冻结点 4 事件 `{channel:'local-db', new_messages, last_sender, failed?}` + resume_note 消费侧规则（恢复 DB 后核对最新发言方与已回复状态）；CLI watch 增 `--channel/--focus`（UI 模式 focus 忽略注明；本地库首轮基线提示一次，存量待回复不回放）；py 单测 10 条 + TS 合成整链 10 条（基线/增量/水位不推进/双向/limit 截断/focus/账号切换/准入/取消/响应防御归一）+ 分派矩阵 8 条；真机冒烟 compat 态分派 UI + 强制 local-db 结构化失败不落 UI（本机 5.0.12.6018 不在矩阵，db 轮真机验收待矩阵扩展） |
| 3.3 | **watch 持锁修正**：按轮获取/释放既有互斥（CLI 与 MCP 轮询路径锁入口一并核对对齐） | ✅ 完成（2026-10-16，批次 A 开发自测通过） | watch.ts 改为每轮 acquire/finally release（含失败轮；BUSY 轮跳过等下轮，--once 保持退出码 1），轮询间隔不持锁；MCP runExclusive 本就是每调用独占，两侧锁粒度对齐已在 watch.ts 注明；集成验证：子进程真实跑 watch 桩轮（AID_WECOM_TEST_DRIVER_STUB，不触达真实企微），间隔窗口内另一进程成功 acquire 同 scope 互斥 → 持锁期间轮跳过（BUSY 告警、循环存活）→ 释放后下一轮自行恢复 |
| 3.4 | 发送核验接线调整（PS 发送/核验分离 + TS 编排） | 🔧 部分完成（2026-10-16，批次 B 开发自测通过） | message-send.ps1 增 `-SkipVerify`（跳过 step5/6 终态证据，返回 sent=true/verified=false，不产生 EXECUTION_UNKNOWN）与 `-VerifyOnly`（不做输入/发送，仅既有终态证据+三选二/Jev 判定；`-ExpectedPrefix` 可选核验文本，缺省取 -Text；两开关互斥，既有默认行为零改动）；messageSend.ts 本地库编排（state db/ui(transient) 分派读法同 unread）：resolve conversation_id（复用 resolveFocusConversation，失败回退老路径）→ 快照解密取基线（conversations 任务当前 max，时序在发送动作前）→ `-SkipVerify` 发送 → 重新快照 messages --after-id=基线匹配「方向=本人 ∧ trusted 正文全等」（短轮询 3×1s 可注入）→ 命中 `verification='local-record'`（verification_detail 含匹配 message_id）→ 未命中/DB 失败转 `-VerifyOnly` → `ocr-bubble` → 双不足 EXECUTION_UNKNOWN（不重入发送，绝不重发）；本地库不可用完全走既有单次路径零改动；toolDefs 写明 success/applied=客户端动作已记录并经本地核验不代表对方已收到（决策 #47）；TS 合成链 7 条（命中/未命中转 VerifyOnly/快照失败转 VerifyOnly/双不足/老路径零改动/发送后取消 partial/基线前取消 none）；真机冒烟 compat 态走老路径 Jev 校验不变（local-record 真机验收待矩阵扩展） |
| 3.5 | toolDefs 描述更新（待回复检测为主要用途 + 通道语义） | 🔧 部分完成（2026-10-16，批次 B 开发自测通过） | wecom_watch_poll 重写（待回复检测/增量为主要用途 + channel/focus 参数 + 本地库失败不落 UI + resume_note + CLI watch 对照，不承诺毫秒级/不漏检）；wecom_message_send 增 verification 三值语义（local-record/ocr-bubble/缺省，local-record 含义=客户端已记录并经本地核验不代表对方已收到）；wecom_unread_list 批次 A 已更新（本轮核对一致）；wecom_read_session 描述不变 |

**第八轮评审修复（2026-10-16，5 P1 + 2 P2，独立用例全绿：typecheck / npm test 488/488 / test:py 108 含 1 平台跳过）**：

1. **发送链 target_ref 账号绑定校验**（messageSend.ts）：verifyRefFn 之后、任何发送或降级之前核对 ref.account 与 probe 当前账号（本地库兜底 try 之外，不被 planLocaldb catch 吞掉转旧发送）；跨账号 → TARGET_REF_STALE、驱动零调用。用例：跨账号拒绝 / 账号匹配正常 local-record。
2. **发送后核验失败统一归并**（messageSend.ts）：completed=1 后 -VerifyOnly 的一切错误（含 UI_CHANGED/RESULT_TIMEOUT/驱动失败）→ EXECUTION_UNKNOWN/effect=unknown（不再误报 effect=none 误导重发）；取消保持 CANCELLED/partial。用例：VerifyOnly 抛 UI_CHANGED → EXECUTION_UNKNOWN、不重入发送。
3. **watch 显式关注空会话水位存 0**（watch.ts）：显式 focus 解析成功的空会话（message_table 无行）→ 水位存 0（首批消息全量按增量上报）；未传 focus 的全量模式首见会话仍按当前 max 建基线。用例：focus 空会话 → 新增 ID1、2 两条都报。
4. **watch 源库代际校验**（watchDbState.ts + watch.ts）：存储增 generation = hash(源库 fileIdentity + schema_version)（与 cursor generation 同源同构）；有水位时代际缺失/不一致或 max < 存储水位（回退）→ 输出 `generation_changed:true` + `generation_reason` 并按当前 max 重建基线，不当「正常无消息轮」。用例：同账号换库（新 fileIdentity + AUTOINCREMENT 归零）/ 旧文件缺 generation / 水位回退，均重建后新消息正常报。
5. **watch focus 持久绑定**（watchDbState.ts + watch.ts）：建立时一次性解析并持久绑定 `{key, conversation_id, display_name, source:'name'|'ref', bound_at}`；后续轮次只按 conversation_id 读数（改名不影响跟踪，输出显示名用绑定时值）；ref 输入 → verifyRefFn + 账号一致性核对 → 按 ref.name resolve 一次；会话消失/读取失败进 failed 不自动解绑。用例：改名后按绑定增量 / 真实签名+账号匹配 ref 绑定跟踪 / 跨账号 ref failed 不绑定。
6. **-VerifyOnly 目标会话一致性**（message-send.ps1）：核验终态判定前以 Test-WeComTitleMatch 核对当前标题与 -TargetName（复用发送路径规则），不一致 → EXECUTION_UNKNOWN（证据可能来自非目标会话，绝不 ocr-bubble 成功）。用例：静态核对检查在 VerifyOnly 分支且先于三选二。
7. **unread name 过滤 DB 通道**（unread.ts/unreadList.ts）：UI 通道 name 既有语义为 display_name 子串包含（`u.name.includes`），DB 通道应用同一语义；无显示名的会话（未传 focus）在指定 name 时排除；toolDefs/CLI help 注明。用例：子串过滤 DB 通道生效且语义与 UI 一致。

**第九轮评审修复（2026-10-16，2 P1 + 1 P2，typecheck / npm test 508/508 / test:py 116 含 1 平台跳过）**：

1. **watch 水位锚点（同文件重写漏消息）**（watchDbState.ts + watch.ts + wecom_localdb.py）：水位条目增 `anchor` = 水位行消息稳定字段摘要（py `_message_anchor` 同源，排除 server_id/sequence；基线建 max 行、watch 推进存返回末端、空会话水位 0 → ''）；py `watch` 对非零水位先 SELECT 该行复算摘要与存储比对，**不匹配/行不存在（含旧桥整数水位无锚）→ 水位失效按 0 处理（该会话全量重报）并输出 `watermark_reset:true`**；TS 透传会话级 `watermark_rebuilt` + 顶层 `watermark_reset` 明细与 message 标注，水位更新为返回末端 + 新锚。补代际校验盲区：同文件 DML 重写（fileIdentity/schema_version 不变）重生更小 id 不再被旧水位压住。trade-off：库重建/恢复旧备份会重报旧内容一次——「漏消息」与「重复」之间取不漏（消费侧 msg_id 去重 + resume_note）。conversations 任务增 `last_anchor`（基线锚来源）。用例：同文件重写 ID1-6 全量返回+标注 / 水位行删除重建 / 正常轮不触发 / 锚不匹配与缺锚 py 单测。
2. **watch focus ref 身份约束**（watch.ts + unread.ts resolveFocusConversation + watchDbState.ts）：`identity.account` 缺失的 legacy ref → failed('legacy-ref-unsupported')（本地库只接受账号绑定 ref）；`identity.type`（contact/group）传入 resolve 作类型约束——contact 只认 S:、group 只认 R:，类型不符/无命中 → failed（群引用不得绑到同名联系人会话）；绑定条目增 `type` 存证（name 绑定不写）；已有绑定后续轮询按 conversation_id 读数不受影响。用例：legacy ref failed / group ref「Peer」+同名 contact failed / contact 绑定带 type 回归。
3. **unread name 过滤无 focus 反向取名**（wecom_localdb.py + unread.ts）：py `last-per-conv` 增 `--with-names`（须与 --account 同传）——R: 群名只认 conversation_table、S: 联系人名只认 user_table（会话 id 解析对方 id；无命中/来源缺失 → name=null，与 resolve 同一命名纪律）；TS 指定 name 时反向取名后按实际名称子串过滤（name=null 指定 name 时排除、未指定时保留且 name 缺省），无 focus 的 name 查询不再把全部会话当「无显示名」排除；修正 independent-repro-round8.test.ts:400 的错误预期（无 focus name='Peer' 应命中真实待回复的 Peer）。用例：反向取名 R 群名/S 联系人名/无命中 null（py）+ 无 focus name 命中/排除/null 名行为（TS）。

## P3：扩展与维护工具

| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| 4.1 | content_type 全量语义表（实测样本归纳） | ☐ 未开始 | 从本机各库采样标注；占位升级为结构化表示 |
| 4.2 | validate 动词（静态/常量/动态三级复检） | ☐ 未开始 | 复用 doctor 深检管线；维护者版本复检入口 |
| 4.3 | 新版本接入 runbook 文档 | ☐ 未开始 | 逆向流程操作手册（EVIDENCE + iterations 决策树沉淀） |
| 4.4 | 独立采集进程等优化评估 | ☐ 未开始 | 依 P1b/P2 实测数据决定是否立项 |

## 已知限制（长期有效）

- 仅限本机当前登录账号自己的数据；未登录无法提取密钥
- 版本强绑定：任何升级都可能使方案失效——以矩阵/doctor 显式呈现，禁止静默硬扫
- v1 仅文本消息库内解析；非文本占位
