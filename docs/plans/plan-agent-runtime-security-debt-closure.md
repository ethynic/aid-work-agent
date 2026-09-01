# Agent 运行时安全债收尾开发计划

> 日期：2026-08-31
>
> 状态：✅ 已完成（2026-09-01，三智能体流程：开发 → 测试 → CodeReview 全部通过）
>
> 设计依据：[Agent 运行时安全债收尾核对与修复设计](../system/agent-runtime-security-debt-closure-design.md)

## 1. 成功标准

- 已落地的 `X-Tenant-Id` 和 scheduled task IDOR 防护不回退。
- `tests/unit/tools/test_scheduled_task_tool.py` 从当前 4 通过/11 失败恢复为 15/15 全绿。
- `obs_traces.total_cost` 的所有运行时写入满足只增不减。
- 新测试能稳定复现旧实现的“高值可能被低值覆盖”语义，并在修复后通过。
- 无数据库迁移、无新依赖、无计费行为变化。

## 2. 实施步骤

### Phase A：冻结现状与修复测试基线（预计 1～2 小时）

- [x] 记录当前 `master` 包含 `500c4991`、`d517e859`、`ec177152`。
- [x] 不修改 `ManageScheduledTaskTool` 生产逻辑。
- [x] 给 11 个需要进入业务分支的旧工具用例补显式 `tenant_id`。
- [x] 为 pause/resume/cancel/view_logs 的 mock 调用补充或保留 tenant/user 参数断言。
- [x] 运行定时任务工具单测，确认 15/15 全绿。

检查点：能够说明 IDOR 已修复，剩余只是测试夹具漂移；若业务代码必须放宽 tenant 才能让测试通过，立即停止，说明修法错误。

### Phase B：`total_cost` 单调写入（预计 2～3 小时）

- [x] `_do_persist()` ON CONFLICT 使用 `GREATEST(COALESCE(existing, 0), COALESCE(EXCLUDED, 0))`。
- [x] pending 重放 UPDATE 使用 `GREATEST(COALESCE(total_cost, 0), %s)`。
- [x] `update_total_cost()` 直接 UPDATE 使用相同表达式。
- [x] `_remember_pending_total_cost()` 在锁内按最大值合并。
- [x] `SessionRecordService.save()` 的内存 trace 回填按最大值合并。
- [x] 保持 rowcount、commit、失败降级日志和缓存有界淘汰行为不变。

检查点：逐一列出五个写入/合并点，确认不存在仍可用较低值直接覆盖的运行时入口。

### Phase C：意图测试与回归（预计 1～2 小时）

- [x] 更新现有 SQL 断言，不再接受裸 `total_cost = EXCLUDED.total_cost` 或裸覆盖 UPDATE。
- [x] 新增 pending 高值不被低值覆盖、较高值可继续推进测试。
- [x] 新增内存 trace 高值不回退测试。
- [ ] 在可用时增加 PostgreSQL 顺序模拟乱序写集成测试；环境不可用时显式 skip。**未做**：本地无观测库连接（`LOGS_DATABASE_URL` 未配置），仓库亦无观测库集成测试先例（`tests/integration/conftest.py` 只管业务库），SQL 结构与进程内逻辑已由单测强制覆盖。
- [x] 运行定向测试：

```powershell
pytest -q tests/unit/tools/test_scheduled_task_tool.py
pytest -q tests/unit/test_trace_total_cost.py
pytest -q tests/unit/api/test_scheduled_task_identity.py tests/unit/api/test_scheduled_task_log_boundary.py
```

- [x] 运行 scheduler、trace/session_record 相邻回归和 `python -c "import src.main"`。

**实际测试结果（2026-09-01）**：

| 范围 | 结果 |
|---|---|
| `tests/unit/tools/test_scheduled_task_tool.py` | 15 passed（修复前基线 4 passed / 11 failed） |
| `tests/unit/test_trace_total_cost.py` | 12 passed（10 个既有用例调整 + 2 个新增单调性用例） |
| `tests/unit/api/test_scheduled_task_identity.py` + `test_scheduled_task_log_boundary.py` | 8 passed |
| scheduler + trace/session_record/monitor 相邻回归 | 169 passed |
| `tests/unit/mid_term/`（引用 session_record） | 367 passed |
| `tests/unit/tools/` 全目录 | 1690 passed / 12 failed / 2 skipped —— 12 个失败经干净 HEAD worktree 基线比对全部为存量（基线 23 failed），0 新增 |
| `python -c "import src.main"` | exit 0 |
| 单调性判别力验证 | stash 生产改动回旧实现后 5 项单调性/SQL 测试全部失败，恢复后全绿 |

检查点：报告准确的通过、失败、skip 数；任何存量失败也要单独列出，不得写“全绿”。

### Phase D：文档和状态收口（预计 30 分钟）

- [x] 更新本计划勾选状态和实际测试结果。
- [x] 更新 `agent-runtime-safety-hardening-design.md` 的成本单调性与测试门禁状态。
- [x] 从 `docs/ideas.md` 移除本待办并在 `docs/ideas_finished.md` 登记完成结果。
- [ ] 按用户指令决定是否提交；没有“提交代码”指令时禁止提交。**等待用户指令**。

## 3. 风险与回退

| 风险 | 控制 |
|---|---|
| 只改 UPSERT，直接回填仍能回退 | 五个写入/合并点清单逐项检查 |
| 为修旧测试放宽租户要求 | 禁止修改生产 tenant fail-closed 逻辑 |
| `GREATEST` 遇历史 NULL | 两侧使用 `COALESCE(..., 0)` |
| 高估值无法在运行时下调 | 明确接受；历史纠错走独立审计脚本，不复用运行时回填 |
| 测试只检查 SQL 字符串 | pending/内存行为测试必做；可用时补真实 PG 行为测试 |

## 4. 预计工作量

总计约半天。IDOR 业务修复已经在 `master`，不再按“另半天”重复开发；它只需要约 1～2 小时清理测试基线。`total_cost` 单调加固及验证约半天内完成，二者零外部依赖。
