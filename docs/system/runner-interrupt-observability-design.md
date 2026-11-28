# 中断执行可观测性设计：trace 中断投影与渠道失败轮用户消息落库

> 日期：2026-11-25
> 背景：聊易私有云 trace `tr_runner_1c300ad9497b47aba48fad4865031e8c` 调查（wecom_kf 渠道，客户「重新出一版」未收到回复）。调查发现的两个结构性问题：① 系统中断被 trace 层标成「用户取消」；② 失败轮的用户消息在接待页历史完全不可见，加重 trace 与接待页的解读分歧。

## 1. 调查结论（机制链）

skill 600s 超时 kill（修复前 sticky fatal 语义，修复 08e40836 已于 2026-11-24 晚部署聊易）引发的完整链路：

```
skill_execute 600s 超时 kill（旧代码 observe_child_exit(None) -> UsageScope.fail sticky fatal）
  -> worker scope.check() 抛 CheckpointFailure
  -> worker except 分支 control.interrupt() + executions.interrupt_attempt
  -> agent_runners.status = 'interrupted'（此时无 cancel_requested，非用户取消）
  -> 渠道侧 ChannelRunnerAgent 轮询看到 interrupted（runner_agent.py）
     -> _cancel_and_wait(runner_id)（渠道语义：中断即取消收尾，不重交任务）
     -> repository.cancel -> cancel_requested = TRUE
     -> 抛 RunnerError('CHANNEL_EXECUTION_INTERRUPTED', 409)
  -> 渠道发送 22 字兜底话术「抱歉，处理您的消息时遇到了问题，请稍后重试。」
  -> claim 领取 interrupted 行（cancel_only = True）-> status='finalizing'
     + checkpoint.finalization_intent='cancel_only'
  -> worker finalizing 分支 execution_result(state, 'cancelled')
  -> finalizer 落终态 agent_runners.status='cancelled'
  -> worker._persist_trace(finalization_retry=True)
     -> trace_persist.update_completion(status='cancelled')
     -> obs_traces.status='cancelled'，error_message 被 CASE 清空
```

误导点：obs_traces.status='cancelled'，前端 SessionTraces 渲染为「**用户取消**」——而 wecom_kf 渠道根本没有用户取消入口。真正语义是「系统中断后渠道收尾取消」。

同时发现的落库缺口：`process_and_persist` 把 user 消息写入推迟到 success 批量写（P0-2 契约），processor 异常（error 路径）直接 return 不写任何消息 -> 该轮用户消息在 `channel_messages` 中**永久缺失**，接待页看不到客户问过这句话。本次事故中 09:55 的「重新出一版」即由此丢失。

trace 与接待页「矛盾」的另一半是同会话 11:10 的第二次「重新出一版」成功执行（trace `tr_428d1d18` completed），接待页显示的完成消息与 Word 文档来自那一次，与被查 trace 无关。

## 2. 改动一：obs_traces 中断投影（interrupted）

### 2.1 原则

- `agent_runners` 终态保持 `cancelled` 不动：终态机（settlement/claim/release_proof/终态校验 `{'completed','failed','cancelled'}`）深度耦合，渠道侧 cancel 收尾语义也确实成立；不为此引入新终态值。
- 只修正**观测投影**：obs_traces 是排查入口，应还原「执行被系统中断」的事实。

### 2.2 实现

1. `src/services/agent_runner/execution_repository.py` claim：cancel_only 收尾时在 checkpoint 记录收尾前状态：

```python
if cancel_only:
    checkpoint = {**checkpoint, "finalization_intent": "cancel_only",
                  "cancel_only_from_status": row["status"]}
```

   纯增量键，所有 `finalization_intent == 'cancel_only'` 等值判断不受影响（已核对全部 7 处使用点）。

2. `src/services/agent_runner/worker.py` `_persist_trace` finalization_retry 分支：终态 `cancelled` 且 `cancel_only_from_status == 'interrupted'` 时投影为 `interrupted`：

```python
trace_status = runner['status']
checkpoint = runner.get('checkpoint') or {}
if (trace_status == 'cancelled'
        and checkpoint.get('cancel_only_from_status') == 'interrupted'):
    trace_status = 'interrupted'
update_completion(..., status=trace_status, ...)
```

   `update_completion` 的 error_message CASE 对非 completed/cancelled 状态走 ELSE 保留分支 -> attempt 1（中断当轮）UPSERT 写入的 `EXECUTION_INTERRUPTED` 得以保留，不再被清空。

3. 前端状态标签补 `interrupted`：
   - `SessionTraces.vue` statusLabel/statusClass
   - `TraceDetail.vue` statusLabel（statusClass 已含 interrupted）
   - `TraceBrowser.vue` 状态筛选下拉

### 2.3 语义边界

| 场景 | cancel_only_from_status | obs_traces 投影 |
|------|------------------------|----------------|
| 系统中断（lease 失效/存储故障/skill fatal）-> 渠道收尾 | interrupted | **interrupted**（保留 EXECUTION_INTERRUPTED） |
| 用户取消 waiting/paused 任务 -> cancel_only 收尾 | waiting/paused | cancelled（不变） |
| 用户运行中取消（engine cancel 命令正常收尾） | （不经 claim cancel_only） | cancelled（不变） |
| 中断后用户又点了停止（cancel_requested 已置位） | interrupted | interrupted（执行事实优先，可接受） |

`_abandon` 路径（finalize_failed 重试耗尽放弃收尾）保留 checkpoint，投影同样生效。

## 3. 改动二：渠道 error 轮用户消息 + 失败占位落库

### 3.1 位置

`src/channels/session.py` `process_and_persist` 的 `result.status == "error"` 分支（processor 异常被 session_queue 转为 error 返回）。

### 3.2 实现

发兜底话术前，与成功路径同构地补写一个原子 batch：

- user 消息：merged 语义与成功路径一致（`result.merged_input` / `merged_from_msgids` / `merged_segments` / `merged_attachments_meta`，独立处理用 `user_content` + `user_metadata`），`created_at` 用入口时间 `user_received_at`；
- assistant 占位：`[本次回复生成失败]`（复用 `_ensure_last_not_orphan_user` 既有文案口径），避免末尾孤立 user / 连续 user；
- 整体经 `add_messages_batch_transactional` 原子写入，失败只记 warning 不影响兜底话术发送。

### 3.3 受理边界门控（runner_accepted）

error 结果不区分「受理前拒绝」与「受理后失败」——前者（如 `CHANNEL_ACTOR_FORBIDDEN`、`CHANNEL_SESSION_MISMATCH`）的消息**不得**写入会话历史（他渠道用户的消息会污染会话上下文）。因此以受理边界精确打标，不做错误码枚举（枚举对新增前置校验码脆弱，漏一个就污染历史）：

1. `runner_agent.py` `process_message_sync`：能进入轮询段即说明 runner 行已受理；新增 `except RunnerError` handler 给异常打 `runner_accepted = True` 后原样重抛。受理前拒绝在 `submit` 内抛出，不会经过该 handler。
2. `session_queue.py`：`EnqueueResult` 新增 `error_code` / `runner_accepted` 字段，processor 异常时从异常对象捕获（两处 error 构造点：processor 异常、cancel 重处理异常）。
3. `session.py` error 路径：`runner_accepted` 为真才补落库。

### 3.4 契约边界

- **enqueue 异常路径维持不写**：`session_queue.enqueue_and_process` 自身抛异常（基础设施故障）时保持既有 P0-2 契约（不写任何消息，`test_enqueue_exception_returns_error_status_no_write` 锁定）——此时 processor 未执行，且写库依赖的基础设施多半同样不可用。
- user 消息补落库后，下一轮上下文重建会看到「user -> [本次回复生成失败]」配对，对 LLM 语义诚实；recap 不触发（error 路径本来就 trigger 不了）。
- 撤回竞态由 `add_messages_batch_transactional` 既有的 recall_pending 逻辑兜底，无需额外处理。

## 4. 不改项

- `agent_runners` 终态枚举与状态机（见 §2.1）。
- 渠道侧 `runner_agent.py` 对 interrupted 行的 `_cancel_and_wait` 收尾语义（设计既定：不重交任务、不重放工具副作用）。
- skill 超时 sticky fatal 本身：已由 08e40836（超时走 child_timeout 非致命）修复，本设计只处理残余的中断形态（lease 失效、真实存储故障等）。
- monitor.py `error_count` 统计口径（仍按 status='failed' 计）；interrupted 行带 error_message，详情页错误标记逻辑（`status in {'failed','error'} or error_message`）已覆盖。

## 5. 验证

- 单测（2026-11-25 容器内全部通过）：
  - `tests/unit/channels/test_session_manager_persist.py`：error 轮落库（受理后失败独立/合并两形态 + 受理前拒绝不落库 + 落库失败不影响兜底话术），42 例绿；
  - `tests/unit/services/agent_runner/test_interrupt_trace_projection.py`（新增）：claim 记录 cancel_only_from_status（interrupted/waiting 两形态）+ _persist_trace 投影（interrupted 还原 / 用户取消与存量行不误伤），11 例绿；
  - `tests/unit/channels/test_channel_agent_runner_wiring.py`：真实入口三渠道回归，runner_failure / history_unavailable 更新为断言补落库 batch，foreign_actor 维持不落库断言，绿；
  - 回归：session_queue / message_recall / verbose_delivery / transfer_suppression / voice_asr / ask_segments / worker_trace_error_code / trace_persist / monitor 语义等 21+ 例绿。
- 前端：vue-tsc typecheck 通过（SessionTraces / TraceBrowser 标签）。
- 既有失败（与本改动无关，2026-11-24 已登记为未改动模块既有问题）：test_durable_sleep_boundaries 版本门 6 例 + test_phase8 combo2 1 例（测试桩缺 envelope 属性）+ test_kf_account_referral 2 例（容器无 PG 连接池，环境性）。
- 真机：聊易下次部署后，构造 skill 超时场景核对 obs_traces.status='interrupted' 且 error_message=EXECUTION_INTERRUPTED，接待页历史含失败轮用户消息与失败占位。

## 6. superseded 投影：渠道新消息取代在途执行（2026-11-26 扩展）

### 6.1 背景（聊易 web_c trace tr_runner_2f41da / tr_runner_356b58 调查）

两条 trace 均显示「用户取消」但并非用户点停止：用户在执行中途又发了新消息（「？」/「咋么不回复我了」），web_c 渠道 `ChannelRunnerAgent.submit` 按设计取消在途 runner 后受理新消息（`runner_agent.py`，设计 §4.3「普通新消息取消旧 runner 后受理」）。取消走 `repository.cancel`（cancel_requested=TRUE -> engine 优雅收尾或 cancel_only claim），obs_traces 照抄终态 cancelled，被前端标成「用户取消」——排查误导。

根因放大器：页面刷新/微信切后台回收重进后，前端丢失执行中感知（`submitting` 复位、输入框可用、不接管在途 runner），用户误以为卡住再发新消息触发 supersede。取消轮的用户消息在 web_c 已于 submit 前落库（`web_c.py chat` -> `_add_user_message`，先于 `agent.submit`），历史无缺口；其他渠道（deferred persist）的取消轮用户消息缺口另议，不在本扩展范围。

### 6.2 实现

1. 取消来源标记：`POST /v1/runners/{id}/cancel` 接受可选 body `{reason}`（`RunnerCancelRequest`，contracts.py）；`manager.cancel` / `repository.cancel(reason)` 写入 `checkpoint.cancel_reason`（queued 直取与 running 分支，jsonb_set + COALESCE 兜底 NULL checkpoint，与 automatic 标记合成单表达式避免 PG 多重赋值覆盖）。
2. 跨保存保留：`execution_repository._preserve_server_checkpoint` 保留 `cancel_reason`（engine 周期性 save_checkpoint 整体覆盖 checkpoint，不保留则标记丢失）。
3. 观测投影：`worker._project_trace_status`（新模块级函数）统一口径——cancelled + cancel_reason=superseded_by_new_message -> `superseded`；cancelled + cancel_only_from_status=interrupted -> `interrupted`（§2 逻辑收编同函数）；用户停止（无 reason）保持 cancelled。`_persist_trace` 两个分支（finalization_retry 的 update_completion 与非 retry 的 collector.on_complete）均消费投影后状态。
4. 展示语义：`trace_semantics.is_interrupted_trace` 状态集补 superseded（与 cancelled 同类，渠道轮次展示归类不变）；前端 SessionTraces/TraceDetail 标签「被新消息取代」、TraceBrowser 筛选项。
5. 渠道标记：`runner_agent._cancel_and_wait(runner_id, reason=None)`；submit 两条 supersede 路径（waiting 非 clarification 取消 + active 循环取消）传 `superseded_by_new_message`。用户停止（cancel_check）、中断收尾、WAIT_UNSUPPORTED 等不传。

### 6.3 web_c 刷新接管（配套前端修复）

- 后端：`web_c.py` 新增 `GET /{tenant}/{config}/sessions/{session_id}/active-runner`（归属校验 + Runner `GET /v1/sessions/channel/{sid}/runners` 透传），返回 running/finalizing/queued 的活跃 runner；waiting 不返回（反问已落历史，回复走新消息 reply 通道，接管会挡输入框）。
- 前端：`WebCChat.vue` 新增 `adoptActiveRunner`——`openSession`（含 onMounted ext 首会话与 switchSession）与 uid 模式 `restoreStoredSession` 恢复会话后查询活跃 runner 并恢复观察态（submitting/streamingActive 置位、observeRunner 接管、停止按钮可用）。查询失败静默跳过，行为不回归。

### 6.4 语义边界

| 场景 | 标记 | obs_traces 投影 |
|------|------|----------------|
| 渠道新消息取代在途执行（supersede） | cancel_reason=superseded_by_new_message | **superseded** |
| 用户主动停止（web_c 停止按钮 / 渠道 cancel_check） | 无 reason | cancelled（不变） |
| 系统中断收尾（§2） | cancel_only_from_status=interrupted | interrupted（不变） |
| 用户取消 waiting/paused | 无 reason | cancelled（不变） |

### 6.5 验证

- 单测：`tests/unit/services/agent_runner/test_interrupt_trace_projection.py` 扩展（superseded 投影双分支 / 用户停止不误伤 / _preserve_server_checkpoint 保留 / repository.cancel 三形态 SQL 与参数序），`tests/unit/test_web_c_channel.py` 扩展（supersede cancel 带 reason / waiting 分支 / 无活跃不取消 / active-runner 端点 4 例），合计 100 例绿。
- 回归：wiring / runner_agent_profile / worker_trace_error_code / trace_merge_semantics 31 例绿。
- 前端：vue-tsc + build 通过；webCIdentity 测试 12 例绿。
- 真机（部署后）：web_c 会话执行中刷新页面 -> 输入框禁用并恢复观察；执行中发新消息 -> obs_traces.status=superseded、TraceBrowser 筛选可见。

### 6.6 被取消端用户提示与历史附件重物化（2026-11-27 扩展）

多端同账号边缘场景（PC 与手机同时提问，第二端新消息 supersede 第一端在途执行）的两个体验缺口修复：

**1. 被取消端提示「已被您其他设备的新消息取代」（P1）**

- 后端：`public_view.public_runner` 白名单透出 `cancel_reason`——仅终态 `cancelled` 且
  `cancel_reason=superseded_by_new_message` 时暴露（与 `contracts.SUPERSEDED_CANCEL_REASON`
  同值，本模块保持零依赖内联字面量）；用户主动停止（无 reason）与其他取消原因不透出，防内部枚举泄漏。
  常量收口 `contracts.py`，worker.py 保留 import 别名。
- 前端：`WebCChat.vue` `refreshOnce` 检测 superseded 终态记挂起标记（`supersededNoticePending`）；
  `observeRunner` finally 在 `reloadMessages()` **之后**插入提示消息（reload 整体替换 messages，
  提示必须后置），渲染为居中轻提示「您在其他设备发送了新消息，此处的问题已被新消息取代，未生成回答」。
- 同端守卫：同端提交新消息先 `resetStreamState` abort 旧观察，in-flight 终态刷新凭
  `signal.aborted` 排除，避免同端误显示「其他设备」提示。`refreshOnce` 全调用点补传 `signal`。
- 已知边界：提示为会话内即时呈现，不落库——PC 刷新后仅见问题无回答（终态 runner 不被
  adoptActiveRunner 接管），属可接受的轻量实现。

**2. 历史附件跨执行重物化（P2）**

根因：历史消息从 DB 重载（`history._reload_memory_from_db`）只保留 content 文本，先前轮次
（含被取代的中断轮）用户上传的附件实体随旧工作区 `_cleanup_completed_resources` 清理删除，
新执行的模型历史里只剩「（见附件）」，只能答「找不到文件」。

- 元数据源：`HistoryRepository.read_user_attachments(session_id, limit)`——过滤口径与
  read_web / read_channel 完全一致（压缩窗口、租户归属、渠道 status/is_recalled），返回
  user 消息附件元数据（file_id/name/mime/type/size）；异常 fail-open 返回 `[]`。
- 重物化：`AttachmentAssembler.prepare` 新增 `history_attachments` 参数——与当前消息附件按
  file_id 去重（当前消息落库后元数据也在历史窗口内），经 `resolve_scoped_uploaded_file`
  租户域解析（`uploaded_file:{file_id}` Redis 元数据 + 存储扫描兜底）复制到当前工作区
  `history_files/` 子目录，files_context 追加「Historical attachments」块给出路径。
- 约束：best-effort（file_id 失效跳过不阻断装配，与 workspace_path_rewriter 失效标记同向）；
  上限 10 个文件 / 100MB；只物化不改历史原文（trace/审计依赖原文）；工作区创建抽公共方法
  `_create_session_workspace`（当前消息无附件但历史有附件时也创建）。
- 装配接线：`context_assembler.prepare` 在历史加载分支（非 SUBAGENT、非 zero_history）读取
  附件元数据并传入 prepare。
- 已知边界：`uploaded_file:{file_id}` Redis TTL 过期后无法恢复（对话附件 24h），此时该条
  静默省略，模型需请用户重新上传。

**验证**：单测 `tests/unit/test_runner_history_attachment_rematerialization.py` 13 例
（物化/去重/失效跳过/上限/fail-open/JSON 解析/public_runner 白名单 5 例）+
`test_runner_attachment_registration.py`、`test_runner_workspace_path_rewriter.py`、
`test_web_c_channel.py`、`test_runner_adapter_contract.py`、`test_background_runner.py`
回归 118 例绿；前端 vue-tsc（仅 desktop 既有 6 错，与本次无关）+ build 通过，
webC 相关 vitest 25 例绿。
