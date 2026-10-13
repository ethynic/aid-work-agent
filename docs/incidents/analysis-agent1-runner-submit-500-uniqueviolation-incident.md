# agent1 提交连续 500：queue_order 序列被 SQL 直插污染复盘

> **发生时间**：2026-10-13 11:08–11:12（约 4.5 分钟）
> **影响环境**：agent1 仿真环境（129.211.65.243，容器 aid-agent-api1 / aid-runner-api1，库 aid_work_agent1）
> **影响范围**：租户 tenant_c148f4efb4dc 一名用户提交数据分析消息时，`POST /api/chat/runners` 连续 17 次 500；前端红 toast「任务暂时无法继续，请稍后重试。」+ 黄条「发送不稳定，已自动重试 N 次」，指数退避重试约 4.5 分钟后恢复。
> **修复**：[plan-runner-queue-order-sequence-selfheal.md](../plans/plan-runner-queue-order-sequence-selfheal.md)（提交入口序列自愈）

---

## 1. 事故经过

- 11:08:22 起，nginx 记录 `POST /api/chat/runners` 连续 17 次 500（11:08:22–11:12:47），第 18 次成功，成功行 `queue_order=51`。
- 用户侧表现：红 toast「任务暂时无法继续，请稍后重试。」+ 黄条「发送不稳定，已自动重试 N 次」，持续约 4.5 分钟后自动恢复。
- runner-api 应用日志对应错误为 `psycopg2.errors.UniqueViolation`，冲突约束名 `agent_runners_queue_order_key`（`agent_runners.queue_order BIGSERIAL UNIQUE` 的唯一约束）。

## 2. 排查与证据

| 证据 | 内容 | 结论 |
|------|------|------|
| 事故后序列状态 | `agent_runners_queue_order_seq.last_value = 53`，`max(queue_order) = 53` | 当前已对齐（偶然自愈的结果，非故障解除时状态） |
| 直插残留行 | `queue_order` 34–50 共 **17 行**，`accepted_at` 均为 2026-10-12（2026-10-13 复核仍可查到） | 表内值越过序列 |
| created 事件 | 上述 17 行中带 `agent_runner_events` `kind='created'` 事件的为 **0 行**；正常提交路径 `submit_in_tx` 必写 created 事件 | 绕过应用写入 |
| 请求日志 | nginx / 应用日志均无对应这 17 行的任何 HTTP 请求 | 非 API 路径产生 |
| 测试脚本排除 | `scripts/e2e_dogfood_replay.py` 为进程内直驱老链路、不插 `agent_runners` 行 | 肇事脚本非它；**肇事脚本本体至今未找到** |

## 3. 根因（已实锤）

1. 2026-10-12 某测试脚本**绕过应用，用 SQL 直插 `agent_runners` 表 17 行，且显式指定 `queue_order=34~50`**。
2. PostgreSQL 的 `BIGSERIAL` 序列 `agent_runners_queue_order_seq` **不会被显式列值推进**，仍停在 33。
3. 此后每次正常提交从序列领号（nextval → 34、35…）都撞表内已占用值 → `UniqueViolation(agent_runners_queue_order_key)` → 事务回滚 → runner-api 500。
4. 前端指数退避自动重试**每次消耗一个序列值**，直到第 18 次领到未被占用的 51 才成功——是**偶然自愈**（序列领号追上并越过表内最大值），不是故障解除。期间每个受影响提交都要经历同样的撞号循环。

即：一次性污染（17 行）× 每次重试只前进一号 = 用户侧约 4.5 分钟的持续失败。若污染行数更多或期间无重试流量，故障窗口会更长。

## 4. 修复方案（提交入口序列自愈）

`src/services/agent_runner/repository.py` 的 `RunnerRepository.submit()`（Web 与渠道提交共用入口）：

- 捕获 `psycopg2.errors.UniqueViolation` 且 `diag.constraint_name == 'agent_runners_queue_order_key'` 时：`rollback` → 执行
  `SELECT setval('agent_runners_queue_order_seq', GREATEST((SELECT COALESCE(max(queue_order),0) FROM agent_runners), (SELECT last_value FROM agent_runners_queue_order_seq)))`
  → 重试一次 `submit_in_tx`，并记 warning 日志（含对齐后值、scope、actor）。
- `GREATEST(max, last_value)` 保证序列**只前进不回退**；其余唯一约束（幂等五列键、`runner_id`/`record_id` 主键唯一）照常抛出，幂等/所有权语义不变。
- 效果：同类污染发生后，**第一次提交即自愈成功**，不再依赖前端重试烧号追赶。

集成测试 `tests/integration/agent_runner_service/test_storage.py::test_submit_realigns_lagged_sequence_after_direct_insert_pollution` 复刻事故：正常提交 → 直插一行占用下一个号且不动序列 → 再次提交必须成功且序列对齐、后续提交不再依赖自愈。

## 5. 遗留与预防（另行立项/约定）

1. **告警**：`UniqueViolation`（尤其 `agent_runners_queue_order_key`）计数告警，同类污染第一时间暴露而不是等用户报障。
2. **数据库审计**：生产/仿真 Postgres 开 `log_statement=ddl`，直插/改 schema 行为可追溯。
3. **流程约定**：测试脚本直插带序列自增列的表时，**要么不指定自增列**，要么插后补：
   ```sql
   SELECT setval('agent_runners_queue_order_seq', (SELECT COALESCE(max(queue_order),0) FROM agent_runners));
   ```
4. **肇事脚本**：若后续找到（特征：能连仿真库、显式写 `queue_order`、2026-10-12 执行过），按上述约定改造。
