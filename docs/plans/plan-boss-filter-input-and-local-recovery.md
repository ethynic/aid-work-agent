# BOSS 筛选容错与本机操作恢复开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|---|---|---|---|
| Phase 1 | ~~服务端统一筛选参数~~ | ⛔ 已取消（2026-11-13） | 用户决策：服务端按工具写字段级归一化属补丁代码（写死），容错收敛到 CLI 侧；服务端归一化实现已回退未合入 |
| Phase 2 | BOSS CLI/MCP 容错与结构化零输入回执 | ✅ 完成（2026-11-13，CR 无 P0/P1，commit f9766b2a） | Phase 2：sanitizeBossFilterInput 单一规则服务 MCP z.preprocess/CLI filter/operation 三入口；全量 bounded 89 文件除基线预存 dom-snapshot-owner-scroll 外全绿；打包 stage 复刻验证通过。R5：公开 schema 放宽四处（experience/salary/educations/first_degree_only 接受 null、educations 项接受任意字符串；educations:null 按未填写静默省略不进证据）+ 回执 filter_evidence（version 1）+ 云端 _map_terminal 逐项豁免；provider_version 1.0.2、digest 0a61d517→fea55461、tool_declaration 不变；旧 CLI 无证据回执行为不变。P2 范围外：数字薪资、age min≥50 等云端 pydantic 即失败，Agent 凭明确校验错误自纠、不产生核验卡；若放宽将向旧 CLI 设备下发其 schema 拒绝的值，制造更差回归。端侧 CLI 更新由用户安排 |
| Phase 3 | 可信结束证据与 Runner 自动接续 | ✅ 完成（2026-11-16，CR 无 P0/P1，commit 43bf0207） | Runtime 纯成功路径附加 _runtime_completion v1（剥离插件同名字段、五类禁止路径零写入）+ 能力 runtime_boss_completion_v1；服务端四要素核验只解除占用（state/effect/计费/原始回执零改写）；迟到可信回执核定；真实 PG 集成 hold 释放 2 例 + 五分支矩阵全覆盖 + 变异 3/3；未知招呼不自动重发语义未动。R8（2026-11-16）：停稳接续语义补全——can_restore 与 worker 观察（_known）对完成判定已认证据（_runtime_completion）的 unknown 放行，未知效果作为事实交回原任务且不产生新派发；无证据 unknown（真未知）与未知招呼语义不动；依赖步骤（resume_next prepare_next）保持先核对并明确「上一步筛选结果未知，需先核对」文案。R9（2026-11-16）：停稳证明（verified_local_invocations）与自有回执完成证据同权——can_restore 与 worker 结果交接（_known，经 owner.root.envelope 随领取载入）对原业务回执丢失的 v3 停稳场景放行接续，未知效果作为事实交回原任务、未知发送零重发；依赖步骤（prepare_next）对停稳已核无回执的父行同权卡核对并给出同一文案；无停稳证明且无自有回执保持卡核验（兼容锚）。R10（2026-11-17）：停稳已核的 cancel_requested 原调用 durable 轮询不再空等租约——Worker checkpoint（owner envelope 随领取载入，非服务端查询）覆盖且无回执时立即把效果未知事实交回原任务（不等租约/deadline、不重放、原行零改写），已有回执按既有终态消费，无停稳证明保持等租约收敛（回归锚） |
| Phase 4 | 原会话一键停止并继续 | ✅ 完成（2026-11-16，CR 无 P0/P1，commit fad12754） | 服务端 verify-local 增 continue_after_stop（组合校验 422、chat 专属、协作取消在途原调用、BUSY 栅栏排除本 scope）；v3 停稳回执事务内服务端编排一条 accepted resume 控制（stop_continue.py，门不满足静默跳过）；public_view 投影 stop_and_continue（仅服务端可受理时）+ local_verification.continue_after_stop；Web 恢复卡主按钮「停止上一步并继续」+ 等待文案 + 输入附件保留。真实 PG 集成新用例 9/9（组合校验、运行中原调用受理+协作取消、停稳→自动 resume 单条投递、空闲受理+幂等回放、BUSY 栅栏与投影联动、scheduled/cancelled/detached 拒绝、在途 resume 不重复投递、closed 只解除占用）；useAgentRunner vitest +5、web build 通过。本机环境基线（干净 HEAD 复现，非本改动引入）：worker 子进程类 3 例不收敛（test_local_verification）、slot_supervisor 2 例（测试桩缺 kill）与 artifact symlink 1 例（WinError 1314 特权）；本机 PG 无 pgvector，隔离库仅过滤 vector 对象复刻 dev_test --isolated-db 机制。R8（2026-11-16）：stop_and_continue 投影与 verify-local 受理共用 stop_continue_admission（设备在线与停止能力 runtime_recovery_stop_v1 纳入投影、停止栅栏排除本 scope 原调用——运行中待协作取消的原调用不算忙、离线明确拒绝 LOCAL_VERIFICATION_DEVICE_OFFLINE）；晚回执竞态派发前复判——停止核验领取/started 前 scope 原调用已全部可信终态时按「无需派发」落终态（state=failed、effect=none、_stop_superseded 标记），verified_local_invocations 合并与 continue_after_stop 接续编排照常；前端补 DEVICE_OFFLINE 文案（useAgentRunner vitest 85/85）。R9（2026-11-16）：点击时 scope 已全部 completion-known（可信回执先到）→ continue_after_stop 不再「无可停调用即返回」，视为已停稳直接执行同一接续编排（verified 合并 + resume 投递，零输入与有输入同路径、同 request_id 幂等回放）；停止核验派发前复判扩展到 running 行，Runtime 在停止动作前以 started 幂等重发最后复判——回执已到不执行受管停止（不关受管 Chrome）、无回执照常停止（回归）。R10（2026-11-17）：停止前复判传输中断（响应未送达、状态未知）不再当作「继续停止」——沿既有重试机制有界重发 /started（claim_token 幂等，4 次尝试），明确响应后才分派（settled/401/403 不执行受管停止、明确放行照常停止），重试耗尽仍无明确响应不执行受管停止、按 stop-unproven 如实回传（宁可少关不误关，不产生假完成）。R13（2026-11-18）：恢复卡单主操作状态机（复审裁决）——服务端投影 resume_block（最近一次被拒接续的 credit/other 原因，取 reject_resume 持久化 error_code）与 stop_continue_block（不可受理原因码），停稳已证（scope 全部完成判定或停稳证明覆盖）时 stop_and_continue 保持投影（设备离线/栅栏忙不撤，与受理侧 settled 路径同语义）；前端五状态（停稳+阻塞→「继续原任务」+原因提示、同入口充值后重投；停稳+无阻塞→「继续原任务」无过时指引；未停稳可受理→「停止上一步并继续」；未停稳不可受理→无按钮+原因提示；接续在途禁用/已接续无卡），旧按钮组不再作为失败/暂不可用回退，旧服务端/scheduled/取消收敛/派发待核对行保持原投影渲染；真实 PG 新用例 4/4 红→绿（含 Worker 领取链积分拒绝→充值后同入口重投端到端），Web vitest 定向 104/104 |
| R12 | 复审修复：接续被拒后重按发起新接续，前端回退手工恢复入口 | ✅ 完成（2026-11-17，独立验证通过，commit ad4a3cdf） | 编排幂等键改接续尝试序数（第 1 次保留旧固定键兼容存量；在途 accepted 阻断、rejected 放行重投、consumed 放行支持再次中断自动接续）；前端三态（在途单按钮/停稳已核且卡片可见=被拒 → 完整动作+被拒提示/旧流程不变）；真实 PG 新用例红→绿（docker compose test-db）、单测 1399/0、Web 93/93、变异 3/3 |
| R9 | 复审修复：停稳证明/迟到回执统一消费（恢复检查/结果交接/停止执行三门）+ continue_after_stop 零输入直接接续 + /started 后停止前最后复判 | ✅ 完成（2026-11-17，独立验证通过，commit 7d8fdaf4） | 三门同源消费 verified_local_invocations 与自有回执证据（OR），零改写红线全过；真实 PG 定向 4 用例 2.19s 全绿 + 全文件 50 过/3 环境基线；变异 3/3；环境发现：本机 localhost→::1 黑洞每连接 ~10s，集成验证统一用 127.0.0.1 DSN |
| R8 | 复审修复：停稳接续语义补全 + 投影/受理统一 + 停止竞态治理 | ✅ 完成（2026-11-16，独立验证通过，commit ae684f0e） | 停稳后结束原工具等待、未知结果交回原任务（依赖步骤单独卡核对、未知招呼不重发、无证据 unknown 保持卡核验）；投影与受理共用 stop_continue_admission（排除本 scope、离线一致）；停止核验派发前复判原调用完成（superseded 拒发落终态、接续编排照常）。定向+集成 46/46（R8 8 例全过）、单测 1383/3 基线、Web 85/85、变异 4/4；留档：supersede 行锁理论 AB-BA 窗口（幂等+自愈） |
| R13 | 恢复卡单主操作状态机 | ⛔ 已回退 | 代码已撤回，由 Phase 5–7 无人值守恢复替代 |
| Phase 5 | CLI 异常与业务结果 | 📋 待开发 | 复用结构化回执，记录失败步骤与已知效果 |
| Phase 6 | 后台自主恢复与终态收尾 | 📋 待开发 | 安全重试、失败自动结束、按范围释放占用 |
| Phase 7 | 移除恢复按钮与端到端验收 | 📋 待开发 | 仅展示进度和结果，验证离开页面也能收尾并接新任务 |

2026-11-18 · 方案调整。Phase 2–4 和 R 系列完成记录用于追溯已实施基线。无人值守恢复以 Phase 5–7 为实施范围，替代 Phase 4 和 R13 的客户手工恢复交互。设计见[筛选参数容错](../design/recruiting/resume-job-matching-design.md#boss-筛选参数容错)、[无人值守恢复](../system/agent-application-architecture-design.md#boss-本机操作恢复)和[共同契约](../system/runner-desktop-runtime-integration-contract.md#boss-筛选容错与本机操作恢复共同检查点)。跨端回执、自动停止、并发恢复与会话收尾按高风险流程执行开发、独立测试和独立 CodeReview。

## Phase 1 · 服务端先兼容旧插件（已取消）

筛选参数容错统一在 Phase 2 的 CLI 入口完成。服务端消费 CLI 回执，已绑定的请求沿原身份和参数追溯。

## Phase 2 · 插件自身容错

- 在 MCP 严格校验前清洗可选筛选值。CLI `filter` 与 MCP 的共用 operation 使用同一份规则。全空以及确知动作前拒绝的情况走已有结构化回执。插件对必需动作目标、授权引用和发送语义沿原规则校验。
- 使用实际 MCP SDK 注册与调用进行回归，验证混合输入到达业务逻辑、空条件保持页面原样、同批有效条件按原语义生效。同步验收真实 `tools/list`、插件 manifest 与已交付 Runtime 导入。后端独立发布仍须验证旧 BOSS，不依赖本阶段升级。

## Phase 3 · 已停稳就继续

- 按共同契约由 Runtime 产生版本化结束事实，服务端在原受权回执路径消费，复用正常/迟到结果、Runner 持久接续和设备准入。可信结束后，旧调用的未知业务效果仍保留，依赖步骤先核对，其他步骤及原会话新动作可继续。
- 使用同一原调用分别验证可信正常结束且效果未知、Host 超时/崩溃、取消后子进程未停、无回执租约过期、迟到可信回执。只有能证明停稳的分支释放旧占用。回归未知招呼或消息在新旧 worker、重复提交和并发恢复下均不自动重发，迟到成功结算一次，其他会话与设备权限按原规则执行。

## Phase 4 · 一个按钮在原会话继续（历史基线）

- 复用现有取消、受管停止与接续入口，在服务端编排并按原调用范围持久化进度。Web 主按钮「停止上一步并继续」按服务端可用状态显示，等待和停止期间保留输入与附件。收到可信回执时自动推进，后台只读观察需要与前台业务依赖匹配。
- 验收停止按钮一次点击后，协作取消或受管停止完成即自动从安全步骤接续，或同会话可以直接发新指令。覆盖 Chrome 随受管进程关闭、页面观察失败、设备离线、另一任务占用、停止失败、晚回执、重复点击和刷新。宽屏、窄屏均核对状态、文案与输入框行为。

## Phase 5 · CLI 异常与业务结果

- 盘点实际出现的 BOSS 异常，先覆盖无效筛选输入、页面异常、登录失效及操作完成但回执缺失。CLI 和 MCP 共用业务实现，已知异常沿现有结构化回执说明失败步骤、原因和已知效果，部分完成保留逐项结果。用 Runtime 的退出与超时路径覆盖 CLI 来不及回传的故障。
- 从真实 MCP/CLI 入口验证可选空值保留同批有效条件、前置失败未产生操作、已发生输入后失去结果保持未知。复核已配置经验的请求如何生成。若能复现经验被丢弃，在实际参数生产链修正并用配置到派发的用例验收。缺失、略过的筛选条件不得满足后续依赖。

## Phase 6 · 后台自主恢复与终态收尾

- 复用 Worker checkpoint、原调用幂等、取消和停稳证明。在原授权范围内自动完成核对、安全重试或接续。沿既有调用、恢复期限确定有限等待和尝试上限并持久化，进程重启及重复领取不重置次数。已停稳而未接续时，由后台判断依赖与阻塞。只有可短时恢复且有安全执行依据时才再试。原设备离线、明确需补足积分或其他条件无法及时恢复时，收尾并给出具体原因。
- 复用 Finalizer 和终态通知。终结不可恢复的旧任务并原子释放会话占用。未确认停稳时只保留原调用的冲突保护，可信迟到回执可解除该范围。通知记录具体失败步骤、原因、已完成部分和处理办法，短任务也覆盖。按现有终态事件去重和投递配置执行。
- 用真实 PostgreSQL 和 Worker 验证可恢复故障自动成功、不可恢复故障终态、重复领取和 Worker 重启仍遵守同一重试上限；覆盖可信原回执先到和停止前复核、外发效果未知零重放、依赖续筛不误放行、独立候选人继续。验证终态后同会话新任务进入执行，同 Runtime 不冲突的任务正常接单，冲突请求给出明确原因并在停稳后恢复准入。

## Phase 7 · 移除恢复按钮与端到端验收

- 后台恢复和收尾验证通过后，将新网页恢复卡改为状态及结果展示，移除核验、停止、续跑和结束等待按钮。等待期间保留草稿和附件。已交付旧 Runtime/CLI 继续按其既有协议运行，旧 Web/API 路径仅用于存量兼容。
- 验收时关闭聊天页并让实际任务分别走自动恢复成功、部分完成和无法恢复。确认终态历史及通知准确，刷新页面不会触发新的恢复或重复通知。恢复失败后在原会话发同 Runtime 的非冲突任务，实际接单并获得回执。另测原设备不可达、回执丢失、设备重新上线和旧端能力缺失，确保这些状态没有把用户留在永久等待里。

## 验证与交付

- 后端按仓库 `scripts/dev_test.sh` 运行相关 unit 和真实 PostgreSQL 隔离 integration；BOSS 运行 `npm run build:main`、`npm run typecheck` 及项目定向测试；Runtime 运行相关 Host/Core 测试与类型检查；Web 运行定向交互测试及 `npm run build`。
- 发布组合覆盖仅更新服务端＋既有 Runtime/BOSS，以及新 Runtime/BOSS＋新服务端。验证 BOSS 实际接单、筛选、同会话下一条指令、回执及计费。Windows 隔离验收使用可控测试目标与受管 Chrome；agent2 部署须由用户当场明确指示。
