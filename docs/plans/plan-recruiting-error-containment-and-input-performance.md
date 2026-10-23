# 招聘链路单点错误治理与 Win32 输入性能修复

来源：[招聘筛选耗时与恢复阻塞复盘（2026-10-20）](../incidents/recruiting-runtime-filter-timeout-and-resume-blocked-20261020.md)剩余未排期项 + 2026-10-20 会话代码审计。恢复交互出口（登录/设备分类、Worker 切换对账、同会话结束等待、unknown 分层核验）由 [恢复计划](plan-boss-resume-next-recovery-fix.md) Phase 6～10 承担，本计划不重复；本计划处理「一次报错杀死整任务」的**触发面**与**输入性能根因**。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | Win32 输入 Node 原生化（共享输入能力，BOSS CLI 先行） | 🔧 进行中 | 开发+独立测试+CR 完成（2026-10-21）：单测 32、通道 19、打包 8 全绿，真实输入端到端通过；待用户真实 BOSS 网页实测；未提交 |
| Phase 2 | model_phase 供应商结果分类与单份失败收敛 | ✅ 完成（2026-10-21） | 产品纠正完成：删除新增模型等待 UI/API/标记，单份失败后台自动跳过；复用既有评估事实防止恢复重识别，失败不扣识别费。独立后端 52、前端 65、额外探针 4 通过，Web build 通过，独立 CR 无阻塞；四轮 CR（2026-10-21）：CLI 批次打开失败分支补齐自动跳过——详情可关闭（列表仍可操作）即记失败继续下一人，仅关不掉才停批进恢复，移除 detail_not_opened 停止码；五轮 CR（2026-10-21）：重试前原卡片消失分支补齐单份收敛（vanishAfterClick 夹具用例）；六轮 CR（2026-10-21）续筛/恢复链路完整闭环四项：①continuation_retry_safe 区分诊断元数据与业务成果（已知诊断键集合 fail-closed），NOT_LOGGED_IN/WRONG_PAGE 解决后沿原游标续筛不再被 STALE_CONTINUATION 拦截；②回执 visual_recovery.page_restored=true 时 available 判定不再被旧 stop_reason 禁止（unsafe/missing_context/终局门禁保留），恢复后沿游标续筛不重读；③重试分支空视口不再终止——记失败回主循环复用滚动/无进展/边界判断（refreshList 夹具用例）；④SUBAGENT.md 失败处理拆出 WRONG_PAGE 自动导航（boss_goto 先行核验后重试），人工场景统一「处理好后回复继续、沿既有成果续跑」语义。resume-batch 36、continuation 42、CLI 合流定向 61、客户端全量 917/925（8 失败为 manifest/input 既知存量）；七轮 CR（2026-10-21）人工恢复两阻断：①available 判定放行页面状态类停止原因（page_restored 自动恢复之外，人工恢复经既有页面/职位校验本次再核验，通过沿游标续筛，unsafe/missing_context/终局门禁保留）；②retry-safe 扩展「无成果页面失败」——UI_CHANGED 与登录/错页同等，空 resumes/processed_names+attempted=0 确认无成果才放行（非空/类型异常/未知键仍拦），新续筛不 STALE_CONTINUATION 沿原游标；continuation 60 用例（含双通道×门禁回归）、local_tools 598 通过；八轮 CR（2026-10-21）人工恢复再两分支：①「无成果」判定改以 resumes 空为准（attempted>0/processed_names 含失败候选人不再拦截，fail-closed 保留 resumes 非空/类型异常/未知键），失败候选人沿游标合并排除（list_resume_next_attempts + 恢复证明同口径注入三调用点）；②no_progress 纳入放行停止原因（双通道×门禁回归覆盖，search_limit/cancelled 维持禁止）；continuation 75 用例、恢复证明 76、local_tools 613 通过；九轮 CR（2026-10-21）排序缺陷：failed_attempt_names 返回改为按名字排序（名单合成与派发/授权两侧查询顺序无关，消除恢复证明列表顺序严格比较误拒），游标两处查询加 id DESC tiebreaker；复现用例红绿验证（还原排序即失败），owner_proof 21、continuation 75、local_tools 616、agent_runner 177 通过（各 1 存量失败）；未提交未部署 |
| Phase 3 | 恢复消费已取回简历成果 | ✅ 完成（2026-10-20） | 恢复沿原 invocation 消费已取回成果：不重抓、不重复计费、新视觉调用独立尝试身份；1210 挂起→恢复→单份跳过→终局全链通过；评审 4 项 low 反馈已处置（overlay heal 补测、docstring 修正）。单测 14+相关 76 通过（2 项失败为存量），CR approve；遗留：集成测试未跑，domain_recovery/durable_flow 有被还原史需提交前核对符号在位；未提交 |
| Phase 4 | 180 秒持久截止语义拆除 | ✅ 完成（2026-10-20） | 投影方案：存量 deadline 保留旧到期语义、新绑定不再持久化工具 timeout 截止；durable 观察无墙钟、租约停续经权威清扫器收敛、cancel_requested 纳入租约清扫（补全保护）。单测 20+受影响目录 541/176 通过（失败经 stash A/B 甄别为存量），CR approve（4 条 low 不阻塞）；遗留：集成套件与 expire_stale_claims SQL 需有库环境回归；未提交 |
| Phase 5 | 存活热点分段与替换前基线固定 | ✅ 完成（2026-10-20） | FilterSetter 5 段+filter:verify:fallback、快照 snapshot:* 分段、调用层 [runtime-perf] 四字段落地（desktopCheck 本体不计时），替换前基线登记至 docs/perf/recruiting-input-hotspot-baseline.md。组合回归 148、host-core 74、runtime 相关 65 全绿、typecheck 0 错误（全量 9 失败甄别为存量/Phase 1 域），CR approve；未提交 |
| Phase 7 | 进度语义分离与可观测性缺口 | ✅ 完成（2026-10-20） | 心跳与语义进展分离（重复同句去重、带标注心跳、无进展告警一次且随语义进展复位）、log 写失败限频 stderr 告警+健康累计、诊断导出按 installation-v2 指针定位 home 修复 homeExists 误报。定向 71/71、host-core 74/74、PS1 语法+夹具实测通过（全量 4 失败经 HEAD worktree 复现为存量），CR approve；外部 CR 修复（2026-10-21）：进度入队即固定快照（连续 push 不再丢中间步骤）、log 健康落盘 log-health.json 并进诊断导出摘要（ps1 醒目告警行），定向 20/20 通过；二轮 CR 修复（2026-10-21）：健康文件两级写入（logs 目录故障降级 home 根、恢复自愈）、诊断读取端目录探测明确报错（不再呈现「无记录无错误」），6 场景测试+ps1 夹具 22 断言通过；三轮 CR 修复（2026-10-21）：读取改两位置新鲜度选择（记录内 updatedAt 优先、文件 mtime 兜底，TS/ps1 同规则），主位置旧文件不再遮蔽降级新记录（红绿验证），8 用例+夹具通过；未提交 |
| Phase 8 | 独立测试与 CodeReview | ✅ 完成（2026-10-21） | 本轮自动跳过纠正已独立验证与 CR：后端 52、前端 65、额外探针 4 通过；真实集成未通过 Windows 夹具平台前置，保留验证缺口；此前其他阶段验证仍以各行记录为准 |
| Phase 6 | 真机观察输入性能是否复现（末位） | 📋 待开发 | 2026-10-20 用户决策：不设隔离 A/B，仅真机观察；依赖 Phase 1 上线 |

## 2026-10-21 产品纠正：单份失败后台自动跳过

批量筛选允许少量简历无法查看或识别；该份后台记失败并继续下一份，不挂起整批、不询问用户、不新增前端按钮或横幅。此前为此新增的模型等待交互和 `end-waiting` scope、等待目标绑定、幂等标记全部撤销；原有设备恢复能力不在本次变更范围。

- 模型无响应的该份不再重试；原供应商回执保持 unknown，不伪造成功，不扣该份识别费。完整拒绝/不可解析沿用一次重试，耗尽后自动跳过。
- 复用既有 `resume.evaluation` 保存单份失败结论；批次恢复时不重新识别已跳过份，不另建跳过标记或恢复通道。
- 图像不可解析、姓名不匹配等单份失败同样自动跳过；后台保留失败与图像元信息，批次结果仅汇总跳过数。全部失败仍如实返回零入库，不伪报成功。
- 租约、存储、取消及实际外部写入的失败不作为单份模型失败吞掉。

### 本轮验证记录

- 开发自测：相关后端四文件 48 项通过；追加坏图、取消与执行完整性用例后，组合文件 7 项通过。
- 独立测试：四个后端文件 52 项通过，0 失败/跳过；前端 Runner API 与 useAgentRunner 65 项通过，0 失败/跳过。额外 4 个独立探针在系统临时文件中运行通过，覆盖全部失败零入库、失败事实保存异常穿透、模型回执存储失败穿透、姓名不匹配恢复不重识别。
- Web `npm run build` 成功；被撤销的前端与 end-waiting API 文件与 HEAD 一致。独立 CodeReview 未发现本轮阻塞问题，核对了真实 phase 写入、恢复证明与 covered 模型回执结算代码。
- 后端命令：`./scripts/dev_test.sh tests/unit/services/agent_runner/test_model_phase_outcome.py tests/unit/services/agent_runner/test_phase2_single_item_failure_regression.py tests/unit/services/agent_runner/test_phase3_recovered_resume_consumption.py tests/unit/services/agent_runner/test_phase8_failure_recovery_combination.py -p no:cacheprovider -q`。
- 前端命令（frontend 目录）：`npm test -- web/__tests__/composables/useAgentRunner.test.ts web/__tests__/api/runner.test.ts`、`npm run build`。
- 持久化与计费落账测试使用边界桩，不冒充真实 PG/CAS 集成验收。此前 Windows 集成夹具在 `os.getuid`/`os.killpg` 前置阻塞，本轮未重复启动；未提交、未部署。

下面“事实基础”为开发前历史快照；与本节冲突的人工恢复要求已废弃，以本节及更新后的 Phase 2 为准。

## 事实基础（2026-10-20 审计，开发前复核）

**错误升级链（d470 GLM 1210 案例）**：

1. `src/services/agent_runner/local_owner.py:262-266`：`model_phase()` 把 `invoke()` 的**所有**异常（除 `authoritative_storage_failure`）统一转成 `LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED`，不区分「供应商明确拒绝」与「无响应」。该包装覆盖每一份简历的每一次 VL 识别调用。
2. `src/services/resume_vl_service.py:283-285`：`evaluate_resume()` 的 2 次重试循环对带 `tool_continuation_required` 的异常立即抛出——而第 1 条把一切异常都变成了该标记，故 Runner 路径上**第 2 次重试对任何供应商错误不可达**（仅"返回空文本/JSON 不合法"可走到第 2 次）。
3. `src/local_tools/resume_flow.py:74-76` 已有正确的单份收敛设计：`ResumeVLError → _failure('RESUME_VL_FAILED')`，批次继续下一份——异常到不了这里。
4. 前端 parked guard（`frontend/web/composables/useAgent.ts`）拦住 waiting 下普通输入；后端 `_model_proof()` 因 unknown receipt 拒绝恢复，两层叠加成死锁。**恢复批次出口覆盖核对（dd634717，2026-10-20）**：通用「结束等待」与设备侧 unknown 分层核验已交付，但 [local_wait.py:43-45](../../src/services/agent_runner/local_wait.py) 明确排除「domain（模型阶段）事实未知」的等待，且要求存在 unknown 设备事实——d470 案例设备调用全部成功、挂起在模型阶段，两个条件均不满足。**HEAD 上该类等待仅剩显式取消可用（任务终结、会话释放），无任何「继续」路径**：普通输入仍被 parked guard 拦（前端 `recoveryControlMessage` 只映射设备侧两个码）、resume 被 `_model_proof` 拒、end-waiting 被拒——任务与已取回成果全部作废。设备侧卡死家族已治愈，模型侧是最后一个无继续出口的家族，本 Phase 2/3 即其解法。

**会话占位机制**：`src/services/agent_runner/repository.py:151-152` 将 `running/waiting/paused/interrupted/finalizing` 视为活跃并占住会话；`useAgent.ts:1007-1051` 把后续消息转为 reply/resume 控制。出口修复在恢复批次；本计划减少进入这些死态的入口。

**180 秒截止**：`src/local_tools/proxy_tool.py` 基础 `timeout_seconds=180`；`src/local_tools/durable_flow.py:24-25` 首调持久化为 `deadline_at` 并在恢复时复用；到期抛 `LOCAL_DEADLINE_VERIFICATION_REQUIRED`。仅改前端 HTTP/SSE 超时无效。

**输入性能链（每次物理输入）**：`clients/boss-resume-assistant/src/main/input/powerShell.ts` 起 2 个 PowerShell 进程（ACL nonce 目录 + 主脚本）+ 拷贝脚本；`scripts/win-click.ps1:31` 每次调用 `Add-Type` 现场编译约 200 行 C#；绑定模式 `:155` 走 `Get-CimInstance`（WMI）反查 Chrome。`desktopCheck.ts` 同款"起 PowerShell + Add-Type"。故障实测：单次点击 26.235s / 拖动 27.495s（历史样本 2.2～2.4s，约 10～12 倍退化）；body 未覆盖 81.381s、调用层 13.784s 未细分；write 工具前置 desktopCheck 与前置 11～15s 延迟高度相关；列职位 3 次 snapshot 累计 14.030s 为另一独立热点。客户重启 Windows 后恢复正常（临时恢复，根因待取证）。

## Phase 1：Win32 输入链路 Node 原生化（共享输入能力，BOSS CLI 先行）

消除「每次输入起 2 个 PowerShell 进程 + 现场编译 C# + WMI 查询」的公共退化面（健康机器 ~2.2s → ~1.1s，拟人节奏为地板；病态机器直接消除进程创建/csc/WMI/AV 扫描敏感面）。

**共享复用定位（2026-10-20 用户确定）**：输入能力实现于共享层（候选 `local-tool-host-core` 或独立共享包），BOSS CLI 为首个接入方；已知复用方：`wecom-cli`、`wecom-personal-rpa`（现有 C# `Win32Input.cs`/`InputExecutor.cs`）、`wechat-souyisou-rpa`（PS 点击脚本）、`association-client-cli`（共用 souyisou 脚本）、`weixin-cli`。其余 CLI 的迁移替换另行立项（列迁移计划统一替换），不阻塞本阶段。

**验证策略（不依赖 BOSS 场景）**：独立测试用例先行——Windows 真机对专用测试目标窗口的真实点击/拖动/键盘输入（环境变量门控 opt-in，避免干扰日常使用）+ 守卫反例（遮挡、几何变化、光标被移、双端拖动）以窗口操作构造 + FFI/逻辑单元测试。真实 BOSS 网页实测由用户次日安排（2026-10-21）。

1. 选型评估 `koffi` / `ffi-rs` 级 N-API FFI（prebuild、Electron ABI 稳定、签名链路可过）；不用停维护的 ffi-napi，不自写 C++ addon（除非评估否决）。
2. `BossPhysicalInput.cs` 守卫 **1:1 移植、一条不删**：`RequireCursorOnWidget`、`PointOnWidget`、`ReadRect`、DPI 线程上下文（`SetThreadDpiAwarenessContext`）、`MarkInputStarted`/`BOSS_INPUT_UNKNOWN` 会计、`STALE_OBSERVATION`、render widget 面积最大选取、前台绑定与 AttachThreadInput。
3. **内建分段计时与守卫取证**：进程内逐段计时（前台激活、窗口探查、光标守卫、拟人移动、按下/拖动/键入、收尾）直接写 perf 日志，替代旧路径的 `win32:*` 整段计时；守卫失败分别记录两项谓词（光标坐标 vs WindowFromPoint 归属）及预期/实际坐标、目标 widget、命中 HWND、前台/几何变化。不采集简历正文。
4. `desktopCheck` 进程内化：`OpenInputDesktop` 单调用替换整套 PowerShell+Add-Type。
5. 消灭 WMI：宿主为 `ensureDebugChrome` 启动方，直接持有 PID，进程内传参，不再 `Get-CimInstance` 反查。
6. `clickAndType` 点击+逐字输入原子性保持（进程内天然成立）。
7. 通道切换不改 wire schema / Device API / binding / 授权，属插件与共享库内部实现，无需新增共同契约 capability（CR 复核确认）；PowerShell 回退常驻，`AID_BOSS_INPUT=powershell` 为逃生口，CLI 内默认 native（2026-10-20 用户确认先行试用），真实 BOSS 网页实测为验收；验收未过用 env 切回，不删除回退。
8. 打包链路更新：NSIS、插件签名、`verify-runtime-package` 清单纳入新原生依赖。
9. 守卫场景验收（复盘清单第 4 项）：年龄双端校准、页面位置变化、光标/遮挡变化——不盲点、不假成功、准确阶段提示与原始故障码。

### Phase 1 验证登记（2026-10-21，开发流程三步 + 主控亲验）

- 交付：共享包 `clients/shared/win32-native-input/`（koffi 3.3.3，bindings 层唯一触碰 koffi）；`WinMouseClicker` 原生后端（通道：构造参数 > `AID_BOSS_INPUT` > 默认 native，加载失败单行日志回退 PS 且本进程不重试）；`desktopCheck` 惰性动态 import 原生路径（host-core 保持零运行时依赖）；`runtime-plugin-packaging` 新增 `stageNativeInputDependency`（file: 依赖 npm pack tgz 入 stage）。
- 等价性：守卫序列、退出码 0/1/2、BOSS_INPUT_UNKNOWN/STALE_OBSERVATION/REFUSED 会计、银行家舍入坐标换算、DPI 线程上下文、INPUT 联合体 x64 布局（40 字节）经 CR 逐行对照 ps1/C# 确认 1:1；异常路径 finally 释放左键有显式单测。
- 测试：新包单测 32/32；BOSS 通道用例 19/19；packaging 8/8；host-core 74/74；真实输入端到端 1/1（独立 profile Chrome 真实点击+中文/emoji 键入读回一致、拖动到位、遮挡守卫 REFUSED）。boss/runtime 全量各有 8/4 个失败，经文件级 A/B 验证为 HEAD 预存（dd634717 注册第 25 个工具未同步测试期望等），与本批无关。
- 性能：desktopCheck native 11ms vs PS 373ms；click 序列总 ~2.5s（activate 425ms + move 811ms + press 91ms，拟人节奏为地板）。
- CR 结论：无 P0/P1；9 条 P2/nit 登记在案（最值得顺手修：CreateToolhelp32Snapshot 的 INVALID_HANDLE_VALUE(-1) 判错、遮挡取证行 cursor_match 硬编码 0）。
- 遗留：runtime-plugin-packaging 全量构建（签名+Node 22.23.3）需构建机执行；未提交未部署；真实 BOSS 网页实测待用户安排。
- **外部评审修复轮（2026-10-21，三项全改并重验）**：① [P1] 构建顺序——boss/runtime 的 `build`/`build:main`/`typecheck` 新增 `build:native` 前置（沿用 `build:core` 模式），删除共享包 dist 后两包 typecheck 复现通过；② [P2] 打包新鲜度——`stageNativeInputDependency` 改为每次从当前构建产物 `npm pack` 到临时目录再入 stage，不再复用源码树同名旧 tgz（临时目录用后即删），单测新增「源码树残留 stale tgz 不被复用」断言；③ [P2] 进程定位——`processes.ts` 命令行读不到的进程按旧 WMI null CommandLine 语义跳过不阻断（目标可读+无关不可读→正常输入；全部不可读→matched=0 兜底 STALE），单测覆盖两分支。重验：新包 32/32、打包 8/8、BOSS 通道 19/19、boss/runtime typecheck 干净 dist 下通过、真实输入端到端 1/1。评审建议的「启动方直传 PID」登记为后续优化（会话工厂当前在 ensure 前构造 clicker，需先重构构造时序，不阻塞本批）。

## Phase 2：model_phase 供应商结果分类与单份失败收敛

按供应商结果三分，让已有的单份失败设计真正可达：

1. **明确拒绝**（收到完整 HTTP 响应体，如 400/1210、4xx/5xx 带错误码）：以可识别的业务异常穿透 `model_phase`，进入 `resume_vl_service` 既有重试（第 2 次可达），仍失败走 `resume_flow._failure('RESUME_VL_FAILED')` 单份跳过、批次继续。非 dict 响应同属"有响应"类。
2. **无响应/结果未知**（超时、连接中断）：模型层保留真实未知事实，单份处理层自动保存失败并跳过；不重试该份、不挂起整批、不扣识别费。
3. **返回不可解析**（有响应但内容无效）：同明确拒绝处理，保留原始码与描述形成可审计单份失败事实。
4. 计费账本按实际回执记录，不伪造 observed/成功；网络无响应与设备外部写动作仍按各自证据保护。
5. **无需前端交互**：删除本阶段新增的模型等待横幅、重试/跳过按钮及配套接口扩展；后台逐份收敛失败后继续批次，复用既有单份评估事实记录失败，恢复不重识别已跳过份。
6. 图像诊断：不含简历内容的格式、模式、尺寸、字节数、摘要及供应商错误分类。
7. 次要触发面收窄：`cloud_flow.py:17-18` 纯读恢复允许直接重读（读无副作用）；意图变化保留既有错误与取消出口，不为此新增前端交互。

## Phase 3：恢复消费已取回简历成果

d470 案例第二批 3 份已取回但未处理即挂起，现无任何路径消费：

1. 恢复沿原 invocation 已保存的逐份结果与处理事实继续，保留前 3 份已入库记录。
2. 禁止重新点击/抓取前 6 份绕状态或重复计费；任何新视觉调用须有独立可追溯尝试身份与原业务授权。
3. 与 Phase 2 组合验收：1210 挂起 → 恢复 → 消费第二批 3 份 → 失败份按策略跳过 → 任务正常终局。

## Phase 4：180 秒持久截止语义拆除

1. 去掉「工具默认 timeout 作为健康后台任务总时限」语义：Runner 观察与执行解耦，用持久原调用及活动续租观察完成；前端离开/订阅重连不结束任务。
2. 兼容已保存旧 `deadline_at` 的存量任务，不忽略既有持久 deadline（迁移或投影方案在实施前细化）。
3. 不取消租约、身份校验、显式取消或效果未知保护；不得仅放大数字（180→600）冒充修复。
4. 验收对应复盘清单：模拟超过 180 秒但有有效执行归属的操作不进错误验证态；前端断开再订阅不取消或重发；旧 checkpoint/deadline 兼容测试。

## Phase 5：存活热点分段与替换前基线固定

**范围决策（2026-10-20，用户确定）**：输入链路直接 Node 原生化（Phase 1），**不再对 PowerShell/C# 路径做任何新增子阶段取证**——给将被删除的实现埋计时是丢弃型工作。复盘验收清单第 1 项「细分单次约 27 秒输入成本」改由 Phase 1 native 实现的内建分段计时 + Phase 6 真机观察满足（同日二次决策：不设隔离环境 A/B 判别实验，native 上线后真机观察是否复现即可），无需逐段测量旧路径。

本阶段只保留**替换也解决不了**的热点分段与基线：

1. **body 未覆盖 81.381s 互斥分段**：筛选结构解析、逐行预校验、可见区几何计算、年龄值读取/验证、失败后核验及未包装调用——均在插件 TS 侧（FilterSetter/bossContext），与输入通道无关，原生化后依然存在。
2. **快照热点**：列职位 3 次 snapshot 累计 14.030s 等前置工具快照采集分段（同上，TS/CDP 侧）。
3. **调用层非 desktopCheck 部分分段**：桌面锁等待、Provider 就绪/调度；desktopCheck 本体由 Phase 1 进程内化消除，其耗时经 Phase 6 真机观察核对是否随之收敛。
4. **基线固定**：以已采集 runtime.log 数据（单次点击均值 26.235s、拖动 27.495s、write 工具前置间隔 11～15s、body/call 层漏计值）登记为替换前基线，供 Phase 6 前后对比；不要求客户重复提供日志。
5. 守卫失败证据（预期/实际坐标、widget、命中 HWND、前台/几何变化）不在旧路径补做，作为 Phase 1 native 实现的内建能力交付。
6. 替换前基线已固定（2026-10-20）：[docs/perf/recruiting-input-hotspot-baseline.md](../perf/recruiting-input-hotspot-baseline.md)。

## Phase 7：进度语义分离与可观测性缺口

1. `ProgressForwarder` 心跳续租与业务语义进展分离：报告当前步骤、最近语义进展、实际无进展时长；无进展告警与诊断，不默认重放。不将重复同一句心跳当作业务推进（68 条相同「设置筛选面板」教训）。
2. `legacy/log.ts` 文件写失败静默降级增加可见告警；Electron supervisor 侧不依赖壳层 stdout 兜底。
3. 诊断导出跟随 `installation-v2/current.json` 指针定位实际 home，保留 legacy 路径兼容（`homeExists=false` 误报修复）。

## Phase 8：独立测试与 CodeReview

非平凡开发按 [开发流程规范](../../.claude/rules/dev_workflow.md) 执行：开发自测 → 独立测试 → 独立 CodeReview → 主控提交前验证。重点组合：

- 三类失败路径（明确 400/无响应/不可解析）分别断言单份状态、剩余批次处理、计费边界。
- 1210 挂起 → 恢复消费第二批 → 单份跳过 → 终局，全链路不重复抓取/计费。
- >180s 健康执行、前端断开重连、旧 deadline 存量兼容。
- native 输入守卫反例：遮挡、几何变化、光标被移、双端年龄滑块；PowerShell 回退可用性（Phase 1 交付物，由其开发会话自测，本计划组合验收不重复执行）。
- 心跳/进展分离展示与无进展告警。

## Phase 6（末位）：真机观察输入性能是否复现

**排期调整（2026-10-20，用户确定）**：本阶段移至全部阶段之后；**不设隔离授权环境 A/B 判别实验**，仅真机观察（"最后再试"）。

1. 前置条件：Phase 1 native 输入链路上线（含 capability 验收与默认切换）后，随客户真机日常使用观察，不需单独安排复现环境。
2. 观察点：单次点击/拖动是否复现 26s 量级退化（对照 Phase 5 登记的替换前基线：点击 26.235s / 拖动 27.495s）；native 内建分段计时是否回到健康机器 ~1s 量级。
3. 若复现：先按最小证据采集流程取证再重启/关闭进程（系统可用/已提交内存、分页与磁盘延迟、CPU；Runtime/Provider/Chrome/PowerShell 进程私有内存、句柄、线程、CPU/IO），再重新评估归因——客户重启恢复是临时现象，不据此关闭内存/进程状态排查。
4. 前置工具热点（列职位 3 次 snapshot 14.030s、列/选职位 40s+ 量级）由 Phase 5 分段计时覆盖，随本阶段真机观察一并核对是否收敛。

## 读取链路 PowerShell 迁移（2026-10-21 用户决策：不长期维护双通道）

已替代的输入回退（win-click.ps1 通道）连同旧测试、doctor 检查、打包引用待清理；读取链路脚本迁 Node 后再删对应 ps1 与 BossPhysicalInput.cs。进度：

| 脚本 | 状态 | 说明 |
|------|------|------|
| cv-wheel.ps1（简历滚动） | ✅ 已迁 native（2026-10-21） | 共享包 wheel 序列（1:1 移植：每格守卫+MarkInputStarted+负数 uint wrap 收绑定层）；真机 BOSS 实测 6 次滚动全 native（单次 ~1.25s），ps1 已删 |
| cv-segdiff.ps1（同画面确认） | ✅ 已迁 Node（2026-10-21） | imageOps.ts 零依赖 PNG 编解码+像素比较；GDI+ 降采样实证复刻（97.5% 字节级、100% ±1/255）；双通道对照 sameView 布尔/数值全一致（含 0.0098-vs-0.01 边界），ps1 已删 |
| cv-stitch.ps1（拼接对齐） | ✅ 已迁 Node（2026-10-21） | overlap/mis/stitched 与 PS 精确相等（mis 进云端契约全程一致），拼接 PNG 逐字节一致；7 段全链路 ~600ms 优于 ps1 秒级，ps1 已删 |
| visual-region-compare.ps1（恢复核验） | ✅ 已迁 Node（2026-10-21） | regionExactEqual ARGB 零容差比较（复用 imageOps 基建）；20 场景双通道全一致（含 PS [int] 绑定银行家舍入实证），ps1 已删 |
| 输入回退清理（win-click.ps1 等） | ✅ 已完成（2026-10-21） | win-click/cv-wheel 双通道删除，WinMouseClicker 纯 native（fail-loud），doctor 改原生能力探测，打包/旧测试清理 |

**CLI 主链路（筛选/点击/滚动/简历读取/同画面核验）PowerShell 专用脚本已清零**（2026-10-21）。仍保留 window-experiment.ps1 + BossPhysicalInput.cs + BossWindowExperiment.cs + powerShell.ts 封装：它们是**视觉恢复窗口分支（visualRecovery captureWindow/clickWindow）、视觉诊断（visualDiagnoseClient）与 CLI 诊断命令的生产依赖**，非仅历史参考；待窗口截图/点击能力迁 native 后一并清理（2026-10-21 用户确认口径）。全量测试 926/937（7 失败为既知 manifest 存量）。真机部署验证待用户指示。外部评审两项 P1 已修复（2026-10-21）：①首行 Up/Average/Paeth 滤镜 prevRow 按全 0（原空 Buffer 致 NaN 参与运算，真机 CDP 首行 Paeth 截图曾 7560 像素解码错、sameView 误判）；②CRC 表索引补 & 0xff（原全部 chunk CRC 错，Pillow UnidentifiedImageError 会使云端识别前置失败）。python 标准库独立 fixture 回归 + node:zlib.crc32 独立校验 + Pillow 端到端读图验证 + 红绿验证，全量 931/942。

## 行为与安全边界

1. 不删除或削弱输入守卫；不自动重放效果未知的外部写动作（发消息/打招呼）；验证保护（设备效果、计费回执）按证据分类收窄，不为恢复便利伪造 observed/成功。
2. 任务权威仍是 AgentRunner，遵循 [Runner/Desktop/Runtime 共同契约](../system/runner-desktop-runtime-integration-contract.md)：不新增本地 Agent loop；新输入通道登记 capability 版本与生产/消费兼容；修改共同接口先登记版本与唯一写入者。
3. **与恢复批次的衔接**：恢复批次（Phase 6～10）已于 2026-10-20 提交（dd634717，未部署），Phase 2/3/4 触及的恢复链路代码（`domain_recovery.py`、`local_recovery.py`、`local_wait.py` 等）以此为基线，可直接基于 HEAD 开发；实施前仍需复核对端（Runtime/插件）配套能力是否随部署就位。
4. 密钥/凭据不进日志与回复；图像诊断不含简历正文；真机性能复现与重放须用户另行授权，不在客户会话自动重跑。
5. 未获用户明确授权不提交、不部署；开发进度实时更新本表并同步 `docs/ideas.md` 索引行。

## 交付边界

- 本计划不含恢复批次已覆盖的出口修复（end-waiting、unknown 分层、页面核验），不重复登记。
- Phase 1 涉及 BOSS 插件与 Runtime 双端发布，需配套客户端打包与升级节奏；旧客户端无新 capability 时保持 PowerShell 路径行为不变。
- 性能根因结论以 Phase 1 实测与 Phase 6 真机观察为准；客户重启恢复是临时现象，不得据此关闭内存/进程状态排查。
