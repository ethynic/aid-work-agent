# Runner 终态事务永久失败致 30 秒重试风暴（243 生产）复盘

> 2026-10-30 周巡检发现并当日止血（14:00），代码修复已落地工作区、待发版。涉及 `runner_d3658d3de61a4cde96763b7d4ece6222`（`tenant_91c41b1796ad`，会话 `session_2008979c7deb`）。
> 发现来源：server-patrol 仓库 `records/2026-10-30-周巡检.md` §2.2。

## 1. 结论速览

| 项 | 结论 |
|----|------|
| 用户感知 | 对应任务在视图中永远停在「结束中」；worker 日志每约 30 秒一条 ERROR |
| 根因 | 该 runner 的会话行与 `chat_records` 行已被删除 → finalizer 的 `write_history` 抛 `FINALIZE_SESSION_OWNER_CHANGED`；而 worker 异常分支对 `finalizing` 行只调用 `interrupt_attempt`（对 `finalizing` 是 no-op），行没有终态出口，租约过期后被 `acquire()` 反复领取，形成无限重试 |
| 影响面 | 单 runner；7 天 3627 条错误，其中 1809 条为本 runner 的 `attempt stopped`，10-30 当天每小时稳定 118–119 条；容器 healthy、内存无异常增长，但持续占用 claim/租约并推高 `aid-runner-api` CPU |
| 数据影响 | 无数据损坏；finalize 是单事务，每次失败整体回滚，usage 回执一直未 applied，**未重复扣费**；但该轮从未进入终态，也未计费 |
| 止血 | 2026-10-30 14:00 事务内置 `failed` / `settled`、释放 claim 与未应用回执；之后日志 0 条，`attempt` 停在 2465 |
| 修复 | finalizer 增加有限重试 + 孤儿放弃（`finalize_failed` / `_abandon`）；确定性错误（归属/结算类）立即放弃，其余重试 5 次后放弃 |

## 2. 时间线

```
10-29 起     该 runner 每约 30 秒失败一次并重新入队（周巡检 7 天窗口内累计 1809 次）
10-30 12:43  周巡检（server-patrol）在 243 上发现 runner-worker 错误风暴
10-30 中午   初判为 INVALID_TOOL_ARGUMENTS（误判，该异常 7 天仅 1 次且与本 runner 无关）
10-30 中午   抓完整 traceback，确认每次失败均为 CheckpointFailure: FINALIZE_SESSION_OWNER_CHANGED
10-30 14:00  备份原行后事务内终结为 failed，释放 claim 与回执
14:00:05.649 最后一条 attempt stopped（attempt=2465）
14:00:07.430 finished_at（之后两个 worker 容器均无该 runner 日志）
```

## 3. 证据链

1. **日志**：`aid-runner-worker` 中 `AgentRunner attempt stopped runner=runner_d3658d3d…` 按约 30 秒节奏出现，`attempt` 单调递增至 2465；`aid-runner-worker1` 同期无该 runner 记录。
2. **异常栈**：`CheckpointFailure: FINALIZE_SESSION_OWNER_CHANGED`（`CheckpointFailure.authoritative_storage_failure = True`，`str(error)` 即错误码）。
3. **数据库**：该行 `status='finalizing'`、`attempt=2465`、`lease_until` 已过期、对应 `agent_runner_session_claims` 仍在、`agent_runner_usage_receipts` 中回执 `applied=false`。
4. **代码路径**：
   - `src/services/agent_runner/transaction_repository.py:12-19`：`write_history` 找不到会话行或归属不一致即 `raise CheckpointFailure('FINALIZE_SESSION_OWNER_CHANGED')`。
   - `src/services/agent_runner/execution_repository.py:340-342`：`interrupt_attempt` 对 `finalizing` 直接 `return row`——设计上认为「终态意图已落库，只需重试 finalizer」，但失败路径没有任何尝试次数的上限。
   - `src/services/agent_runner/execution_repository.py:94`：`acquire()` 会重新领取租约已过期的 `finalizing` 行（只要 claim 还在）。

## 4. 根因链条

1. **上游触发**：用户会话（`chat_sessions`）及其 `chat_records` 在任务提交后被删除（删除入口待确认）。
2. **终态事务失败**：`finalize()` 先结算再写历史，`write_history` 的会话归属校验失败 → 抛 `FINALIZE_SESSION_OWNER_CHANGED`，整个事务回滚，行保持 `finalizing`。
3. **无终态出口（核心缺陷）**：`worker.py` 的 `except Exception` 分支看到 `row['status']=='finalizing'`，只做 `_preserve_duration` + `interrupt_attempt`；后者对 `finalizing` 是 no-op，于是什么都没变。
4. **无限重租**：租约过期后 `acquire()` 因 claim 仍存在而再次领取该行 → 立即调用 `finalize()` → 同样的错误 → 如此往复。由于错误是确定性的（会话行已删除），重试永远不可能成功。

## 5. 止血（2026-10-30 14:00，已执行）

1. 备份原行：`/root/runner_d3658d3d.backup.20261030.json`（`chmod 600`）。
2. 事务内完成：
   - `SELECT … FOR UPDATE` 锁定该 runner；
   - `DELETE FROM agent_runner_session_claims WHERE owner_runner_id = …`；
   - `UPDATE agent_runner_usage_receipts SET applied=TRUE, applied_at=clock_timestamp() WHERE runner_id = … AND NOT applied`；
   - `UPDATE agent_runners SET status='failed', settlement_status='settled', result.error_code='SESSION_NOT_FOUND', worker_id=NULL, lease_until=NULL, finished_at=clock_timestamp(), revision+1, view_revision+1`（仅当仍为 `finalizing`）。
3. 验证：`14:00:05.649` 之后两个 worker 容器均无该 runner 日志，`attempt` 停在 2465；全库 `status='finalizing'` 残留 0 行。

## 6. 代码修复（工作区已改，待发版）

| 位置 | 内容 |
|------|------|
| `src/services/agent_runner/finalizer.py` | 新增 `FINALIZE_DETERMINISTIC_ERRORS`、`FINALIZE_MAX_FAILURES=5`、`finalize_failed(attempt, error)` 与 `_abandon(...)` |
| `src/services/agent_runner/execution_repository.py` | `released_by()` 对带 `finalize_abandoned` 标记的终态行放宽 result 与 pending 的逐键比较（abandon 归一化了 output/images/error_code 且 completed 会降级 failed），证明字段核验不变，旧 worker 心跳可干净关闭 |
| `src/services/agent_runner/worker.py:629-656` | 异常分支：`finalizing` 行或存储类失败先调用 `finalizer.finalize_failed`；它返回非 `None` 表示已接管（记录重试或已放弃），其余情况保持原有 preserve/interrupt 与 stage→finalize 路径 |

`finalize_failed` 的行为：

1. `lock_runner` 失败（租约/归属已转移）→ 返回 `None`，交给调用方原有逻辑；
2. 行已不在 `finalizing` → 返回 `None`（不产生副作用）；
3. 错误属于 `FINALIZE_DETERMINISTIC_ERRORS`（`FINALIZE_SESSION_OWNER_CHANGED` / `FINALIZE_CLAIM_OWNER_CHANGED` / `RECORD_OWNER_MISMATCH` / `BILLING_TENANT_NOT_FOUND` / `USAGE_COST_REGRESSION`，重试不可能成功的确定性失败）→ 立即 `_abandon`；
4. 其余错误：checkpoint 记 `finalize_failures` / `last_finalize_error` 并提交，行留在 `finalizing` 等待下一次租约（`acquire()` 重领时原样保留 checkpoint，计数跨 attempt 累积）；累计达 5 次（约 2.5 分钟）→ `_abandon`。

`_abandon` 的语义（同一事务内）：

- 终态一律 `failed`，仅 `pending_finalization.status='cancelled'` 保留 `cancelled`；`credit_cost=0`，`error_code` 取原意图或本次错误码；
- `settlement_status='settled'`，关闭未完成的控制项，删除该 runner 的 claim，未应用回执标记 `applied`（不写 `record_id`，因为历史行可能已不存在）；
- 终态行写入 `checkpoint.released_attempt`（证明该 attempt 已终结，供 `released_by` 判定）与 `checkpoint.finalize_abandoned=<错误码>`，便于事后追溯；
- 公共投影与事件分发各自包一层 `SAVEPOINT`：它们的失败只回滚自身，不会把唯一的终态 UPDATE 一起回滚；
- 通过 `_after_commit` 照常做缓存失效与 trace 投影（记录不存在时安全跳过）。

**为什么 completed 也降为 failed**：历史行没写成，公共记录里这轮就不存在「完成」这一事实；降级并带出 `error_code` 比显示「已完成但没有记录」更可解释。

## 7. 验证状态

- 已做（2026-10-30）：
  - `python3 -m py_compile` 通过；止血后线上日志与 DB 状态已核对（见 §5）。
  - 集成测试 `tests/integration/agent_runner_service/test_finalizer_abandon.py`（7 用例，隔离库 `--isolated-db` 全部通过）：归属类错误立即放弃、确定性结算错误（3 个参数化）立即放弃、可重试错误 5 次后放弃且 `finalize_failures` 1→4 递增、放弃后回执 applied 且 `released_by`/心跳干净关闭、无权限调用零副作用。
  - 回归：`test_storage.py` + `test_worker_released_heartbeat.py` + `test_recovery_storage.py`（41 通过）、`test_continuous_settlement.py` + `test_runner_event_settlement.py` + `test_incident_trace_retry.py`（13 通过 1 失败）、`test_worker.py`（10 通过 1 失败）。
- 遗留失败处置（stash 改动后复跑确认与本次修复无关，均为存量问题，2026-10-30 当日已修）：
  - `test_runner_event_settlement.py::test_real_terminal_event_fault_rolls_back_...`：`settle_pending_usage` 同步公开 `credit_cost`（376cfc33）后测试断言未更新。已改断言：`result` 除 `credit_cost`（0.1 -> 0.2）外与终态逐键相等。
  - `test_worker.py::test_two_real_workers_run_different_sessions_and_preserve_same_session_fifo`：460620f0（user 消息改用提交时刻）的副作用——重叠提交时 web 历史按 created_at 排序会出现 `user1, user2, 回复1, 回复2`。按产品决定保证回合块顺序：`write_history` 对 web 会话把主 user 消息时间戳钳制到会话已有消息最大时间之后（顺序提交场景仍显示真实发送时间，460620f0 目标不变）。

## 8. 遗留与后续建议

1. **上游根因**：删除会话/记录的入口为什么能删掉仍在运行的 runner 的会话行，需要单独确认。建议删除会话时先把运行中的 runner 置为取消，而不是留给 finalizer 事后发现孤儿。
2. **放弃路径不计费**：当前以「不能正确结算历史就不扣费」为安全默认。若业务要求按已产生的 token 计费，需要单独设计「无历史行仍结算」的路径，不要在本修复上直接加。
3. **监控缺口**：`finalizing` 行停留超过 N 分钟目前没有告警，只能靠周巡检发现（本次即如此）。建议加一条巡检/指标：`select count(*) from agent_runners where status='finalizing' and updated_at < now() - interval '10 minutes'`，持续 >0 即告警。本修复保证确定性错误不再无限重试，但 DB 级故障仍可能让行滞留。
4. **误判提醒**：定位这类问题不要只看错误计数排行，必须抓一次完整的 traceback；本次首轮因把 `INVALID_TOOL_ARGUMENTS`（7 天 1 次）当成主因，白跑了一段。

## 9. 排查备忘

```bash
# 生产 243
ssh -p 10167 ubuntu@129.211.65.243

# 重试风暴是否存在：同一 runner 的 attempt stopped 是否高频递增
docker logs --since 1h aid-runner-worker 2>&1 | grep -o "attempt stopped runner=[a-z0-9_]*" | sort | uniq -c | sort -rn | head

# 抓一次完整 traceback（确认错误码，别只看 ERROR 行）
docker logs --since 10m aid-runner-worker 2>&1 | grep -A 40 "attempt stopped" | head -60

# 滞留的 finalizing 行
docker exec aid-postgres psql -U aid_user -d aid_work_agent -c \
  "select runner_id,attempt,lease_until,updated_at from agent_runners where status='finalizing';"

# 某 runner 的 claim / 回执状态
docker exec aid-postgres psql -U aid_user -d aid_work_agent -c \
  "select * from agent_runner_session_claims where owner_runner_id='<runner_id>';"
docker exec aid-postgres psql -U aid_user -d aid_work_agent -c \
  "select phase,applied,applied_at from agent_runner_usage_receipts where runner_id='<runner_id>' order by created_at;"
```

判定要点：`status='finalizing'` + 租约过期 + claim 仍在 + 同一 runner 日志按固定间隔重复 = 本文场景；若错误码是归属类，直接按 §5 终结，不必等待。
