# Runner 中断后前端自动停止与播放按钮恢复

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 调研与方案（前后端链路还原、根因定位） | ✅ 完成（2026-11-12） | 后端状态机与 resume API 已就绪，缺口集中在前端状态归约 |
| Phase 2 | 前端改造（runnerResumable 谓词 + 状态归约 + 播放按钮） | ✅ 完成（2026-11-12） | 5 文件改造完成；组件与 composable 测试通过 |
| Phase 3 | 测试与独立验证（vitest + 独立验证智能体） | ✅ 完成（2026-11-12） | 目标测试与 composables 目录全绿；全量 vitest 12 个失败均为基线既有（stash 复验一致）；npm run build 通过；独立验证通过（无 P0-P2），P3-1（播放按钮锚定与 durableTask 漂移）已修复——播放改锚 resumableTask 行并新增多行锚定测试，P3-2（resume 受理后停止按钮重现）为既有任务卡语义仅记录 |
| Phase 4 | agent2 事故日志核验（只读） | ✅ 完成（2026-11-12） | 见下「事故核验记录」 |

## 事故核验记录（agent2，只读）

对照截图时间 2026-11-11 14:43 查证（runner_582c62abb1b04cd39754ef2181bf1069）：

| 时间 | 事实 | 证据 |
|---|---|---|
| 14:35:59 | 任务接单（普通任务，非持续） | agent_runners.accepted_at，与截图用户消息时间一致 |
| 14:43:04 | agent2 发版停止信号 | /var/www/agent2/log/agent/aid-work-agent_20261111.log:43018 |
| 14:43:13–16 | 任务 interrupted + verification 等待（截图气泡文案） | agent_runner_events seq 217–220；气泡 waitingNotice 仅由 kind=verification 投影 |
| 14:43:16→15:14:22 | 31 分钟无任何控制请求 | agent_runner_controls 该任务 0 行——证实当时 UI 无可用恢复路径，文本被 toast 拦截 |
| 15:14:22–23 | 用户点停止 → cancel → cancelled | 事件 seq 221–224（与截图停止按钮状态吻合） |

结论：与方案根因分析完全一致——中断事实已实时到达前端，但 canStop/canResume 归约使界面既不"停"也不可恢复，用户唯一出路是破坏性取消。本次改造（interrupted+verification 可恢复、播放按钮、任务卡扩展）正对该链路。

## 背景

2026-11-11 agent2 发版（SIGTERM）导致 runner worker 优雅停止，前端会话中的运行任务被中断。用户反馈：

- 中断后前端没有"自动停下来"——红色停止按钮一直显示，看起来仍在处理中；
- 需要用户手动点击停止按钮（实际是 cancel，破坏性）才能继续输入；
- 输入"继续执行"会弹 toast「原任务需要恢复核对，本次输入已保留。」，无恢复入口；
- 期望：中断后自动停止展示、停止按钮变为播放按钮（点击恢复原任务）、也可直接输入新内容发送接续。

## 根因（调研结论）

服务端链路完好：`RunnerWorker.stop()`（src/services/agent_runner/worker.py:290）→ `interrupt_attempt(public_verification=…)` → `status='interrupted'` + `snapshot.waiting={'kind':'verification','question':文案}`（execution_repository.py:364-383）→ SSE/轮询推到前端，气泡正常渲染。

缺口全在前端三个状态归约：

1. `canStop`（frontend/web/composables/useAgent.ts:324-330）把所有非终态行当可停 → 中断后停止按钮不消失，点击即 cancel 已中断任务。
2. `sendMessage` 的 `canResume`（useAgent.ts:1235）要求 interrupted 且无等待，而发版中断必带 verification 等待 → 文本发送被 toast 拦截。后端 `control_repository.py:157-189` 本身接受 interrupted+verification 的裸 resume（assert_wait 仅校验 guard/armed-sleep）。
3. `DurableTaskStatus` 任务卡仅覆盖 continuous/timer 行，非持续任务中断无任何恢复/停止入口。

continuous 任务服务端已有自动恢复（recovery_repository.py next_continuous_candidates），本改动覆盖手动恢复场景并统一交互。

## 方案

2026-11-13 补充：继续入口沿用，点击后立即收起，待服务端确认再次中断或可重试失败后恢复。续跑定位和原会话核对由 Runner 处理。详见 [Runner 检查点恢复设计](../system/agent-application-architecture-design.md#58-runner-按检查点继续执行2026-11-13待开发) 和 [开发计划](plan-runner-server-skill-interruption-recovery.md)。

纯前端 5 文件，不动 wire schema（已交付客户端兼容原则）：

1. `frontend/web/api/runner.ts`：新增共享谓词 `runnerResumable(row)`——
   - `cancel_requested / resume_requested / local_recovery` → false（local_recovery 必须走"核对并继续"卡片）；
   - waiting → 仅 timer / continuous_guard（现状）；
   - paused → 无 wait（现状）；interrupted → 无 wait（现状）**或 verification 等待（新增）**；timer/guard 维持 wait_id+target_execution_id 要求。
2. `useAgent.ts`：
   - `durableTask` computed 追加非 continuous 的 paused/interrupted 非终态行 → 任务卡显示（含"停止任务"，保留放弃出口）；
   - `controlDurableTask` resume 门改用谓词（wait_id/target 传参逻辑不变）；
   - `canStop` 排除 resumable parked 行（由播放按钮接管），其余非终态行照旧；
   - 新增导出 computed `resumableTask`；
   - `sendMessage` parked 段 canResume 复用谓词（canReply 优先、resume_requested/local_recovery 分支不变）。
3. `DurableTaskStatus.vue`：canResume 复用谓词；interrupted 文案并入"继续任务"。
4. `ChatInput.vue`：新 prop `canResume`、emit `resume`；`!isProcessing && canResume` 时在原停止按钮位置显示播放按钮（主色圆形+三角 SVG，title"继续上次任务"，无 confirm）；发送按钮逻辑不变。
5. `ChatContainer.vue`：传 `:can-resume`、`@resume="controlDurableTask('resume')"`。

## 效果

- runner 中断 → isProcessing 自动落 false（原有）+ 停止按钮消失（本次）→ 界面自动停下来；
- 停止按钮位置变为播放按钮 → 点击恢复原任务；
- 输入新内容发送 → 自动转为 resume+answer 接续原任务；
- 持续任务仍由服务端自动恢复兜底。

## 验证

- `cd frontend && npm run build`
- `cd frontend && npm run test`：useAgentRunner.test.ts（interrupted+verification → resume 转发、canStop、local_recovery 分支）、ChatInput 用例（播放按钮渲染与事件）、DurableTaskStatus.test.ts（interrupted 继续任务）。
- agent2 只读日志核验（对照截图时间 2026-11-11 14:43 前后）；部署由用户明确授权后另行执行。
