# 开发计划：agent_runners queue_order 序列自愈（提交入口）

> 关联复盘：[agent1 提交连续 500：queue_order 序列被 SQL 直插污染复盘](../incidents/analysis-agent1-runner-submit-500-uniqueviolation-incident.md)

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | `RunnerRepository.submit()` 序列自愈（UniqueViolation → setval 对齐 → 重试一次） | ✅ 完成（2026-10-13） | 代码重建完成（首轮产物意外丢失后按同一规格重建） |
| Phase 2 | 集成测试复刻事故场景 | ✅ 完成（2026-10-13） | `test_submit_realigns_lagged_sequence_after_direct_insert_pollution` |
| Phase 3 | 文档（本计划 / 复盘 / 索引 / database_system_table 条目） | ✅ 完成（2026-10-13） | — |
| Phase 4 | aid_test_* 隔离库回归（新增用例 + test_storage.py 既有用例） | ✅ 完成（2026-10-13） | agent1 服务器一次性容器 + aid_test_* 隔离库：test_storage.py 17 passed；自愈单测 3 passed；独立回归 145 passed（3 个失败 A/B 对照为 master 既有问题：recovery released_attempt / test_api wecom_kf 400 / channel pipeline 用例，另行跟进）；CR 无 P0/P1，4 条 P2 已处理（补负向单测、直插 ID 随机化、常量位置；TOCTOU 自限性记录在案） |
| Phase 5 | 提交推送 + agent1 部署 + 序列落后验收 | 🔧 进行中 | 提交/部署/验收执行中 |

## 背景

2026-10-13 11:08 agent1 仿真环境 `POST /api/chat/runners` 连续 17 次 500（约 4.5 分钟），根因是 10-12 某测试脚本 SQL 直插 `agent_runners` 17 行且显式指定 `queue_order=34~50`，`BIGSERIAL` 序列 `agent_runners_queue_order_seq` 不被直插推进仍停在 33，正常提交持续撞 `agent_runners_queue_order_key` 唯一约束；前端重试每次烧一号，到 51 才偶然自愈。完整证据链见复盘文档。

## 改动清单

1. `src/services/agent_runner/repository.py`
   - `submit()` 捕获 `psycopg2.errors.UniqueViolation` 且 `diag.constraint_name == 'agent_runners_queue_order_key'`：`rollback` → `setval` 对齐到 `GREATEST(COALESCE(max(queue_order),0), last_value)`（只前进不回退）→ 重试一次 `submit_in_tx`，记 warning 日志。
   - 其他唯一约束（幂等键等）照常抛出，幂等/所有权语义不变；`submit_in_tx` 仅被 `submit()` 调用，Web 与渠道提交共用此入口。
   - 实现注意：生产 `get_db_connection` 的 `CursorWrapper` 中 `conn.cursor()` 返回共享内部 cursor，自愈分支**不可用 `with conn.cursor()`**（退出关闭后重试会踩 closed cursor）。
2. `tests/integration/agent_runner_service/test_storage.py`
   - 新增 `test_submit_realigns_lagged_sequence_after_direct_insert_pollution`：正常提交 → 直插一行占用下一个号且不动序列（无 created 事件，与事故取证特征一致）→ 再次提交必须成功且 `queue_order` 跳过被占号、序列对齐 max → 后续提交不再依赖自愈。
2.1 `tests/unit/services/agent_runner/test_repository_sequence_selfheal.py`（CR 建议补充）
   - stub 连接复刻生产 `CursorWrapper` 共享 cursor 语义：queue_order 冲突 → rollback+setval+同 cursor 重试恰好一次；其他唯一约束原样抛出且不 setval；重试再冲突向上传播。
3. `docs/system/database_system_table.md`：`agent_runners` 条目补序列自愈说明。
4. `docs/incidents/`：复盘文档 + 索引登记。

## 验收标准

1. 隔离库（aid_test_*）：新增用例通过；`test_storage.py` 既有用例全部通过（幂等/所有权/租约语义无回归）。
2. agent1 部署后人为把 `agent_runners_queue_order_seq` setval 落后于 `max(queue_order)`，正常发消息应**一次成功**（不再连续 500），且应用日志出现序列对齐 warning、序列恢复 ≥ max。

## 待办（本任务外，另行立项）

- `UniqueViolation` 计数告警。
- 生产/仿真 Postgres 开 `log_statement=ddl`。
- 流程约定：测试脚本直插带序列的表，要么不指定自增列，要么插后补 setval（SQL 见复盘 §5）。
- 若找到肇事测试脚本：改造为不写 `queue_order` 列（或插后补 setval）。
