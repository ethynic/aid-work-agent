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
