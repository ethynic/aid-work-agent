# Agent 运行时安全债收尾核对与修复设计

> 日期：2026-08-31
>
> 状态：✅ 已完成开发（2026-09-01，三智能体流程通过；测试结果见[开发计划](../plans/plan-agent-runtime-security-debt-closure.md) Phase C）
>
> 依据：当前 `master` `5311f0a3`，已包含 `500c4991`、`1c815881`、`d517e859`
>
> 关联基线：[Agent 运行时安全加固设计](agent-runtime-safety-hardening-design.md)

## 1. 目的与结论

本设计核对原安全债清单中三项收尾事项，并给出可直接实施的最小修复方案。

| 核对项 | 当前结论 | 是否改业务代码 |
|---|---|---|
| `X-Tenant-Id` 验权 | 已进入 `master`，核心提交 `d517e859`，后续 `ec177152` 又补了管理员无效租户头拒绝 | 否 |
| `ManageScheduledTaskTool` 四个按 ID 操作无属主校验（IDOR） | 已进入 `master`，实际落地提交是 `500c4991`，早于 `d517e859` | 否；只修失效的旧测试基线 |
| `obs_traces.total_cost` 窄窗竞态使用 `GREATEST` 加固 | 未进入 `master`；当前所有写路径仍为直接覆盖 | 是 |

最终开发范围只有两部分：

1. 维护定时任务工具旧单测，使现有属主校验的完整测试文件恢复全绿。
2. 将 `obs_traces.total_cost` 的运行时投影改成单调写入，并增加能够证明“旧高值不会被迟到的低值或 0 覆盖”的测试。

不重做 `X-Tenant-Id`，不重构 scheduler，不修改计费权威链路，不做数据库迁移。

## 2. 现状审计

### 2.1 `X-Tenant-Id` 已完成

`d517e859` 已在当前 `master` 的祖先链中。当前中间件只在 Bearer token 完整认证后采纳 `X-Tenant-Id`，普通用户不能伪造其他租户，平台管理员代管目标租户时也会校验租户存在性。`ec177152` 对无效管理员租户头继续采用 fail-closed。

本轮不修改该链路，只把它视为另外两项核对的可信租户来源。

### 2.2 `ManageScheduledTaskTool` IDOR 已完成

#### 已落地的安全边界

`pause`、`resume`、`cancel`、`view_logs` 四个按 `task_id` 操作已具备以下防线：

1. 从 `ToolExecutionContext` 获取可信 `user_id` 和 `tenant_id`；SaaS 环境身份缺失时拒绝执行。
2. 读取任务时使用 `task_id + tenant_id + user_id` SQL 条件；越权与不存在统一返回“任务不存在或无权操作”，不泄漏对象存在性。
3. `pause`、`resume`、`cancel` 的最终写 SQL 再次携带 `tenant_id + user_id`，不是“先查后裸写”，因此不存在校验与写入之间的 TOCTOU 越权窗口。
4. `view_logs` 除任务属主校验外，日志查询本身也带 `task_id + tenant_id + user_id`。
5. `list` 按 `user_id + tenant_id` 过滤，遗留 `tenant_id=''` 行不会出现在具体租户视图中。

对应实现主要位于：

- `src/tools/scheduler/scheduled_task_tool.py`
- `src/scheduler/db.py`
- `tests/integration/test_scheduled_tasks_tenant.py`

#### 当前剩余问题：单测基线漂移

运行 `pytest -q tests/unit/tools/test_scheduled_task_tool.py` 的现状是 `4 passed, 11 failed`（此前与 10 个 `total_cost` 测试合跑时汇总为 `14 passed, 11 failed`）。失败不是属主校验失效，而是 11 个旧用例仍构造只有 `user_id`、没有 `tenant_id` 的工具上下文。在当前 SaaS 配置下，这些调用会按安全设计 fail-closed，导致用例在进入原断言路径前返回“租户上下文缺失”。

这会带来两个工程问题：

- 定时任务工具的文件级回归不是全绿，后续改动难以区分真实回归和旧夹具错误。
- 部分历史日志脱敏用例因未带租户而根本没有执行到 `view_logs` 分支，形成假覆盖。

该问题只需要修测试输入，不应放宽生产代码的 tenant 要求。

## 3. `total_cost` 竞态问题

### 3.1 数据语义

`obs_traces.total_cost` 是从 `chat_records`/billing 计费结果派生的技术观测投影，不是扣费权威。默认值为 0，真实成本在 `SessionRecordService.save()` 完成计费后回填。

同一 trace 的运行时成本写入应满足不变量：

```text
new_stored_cost = max(existing_stored_cost, incoming_cost)
```

即正常运行时只允许 `0 → 真实成本` 或较小值向较大值推进，不允许迟到的快照把已落地真实成本回退为 0 或更小值。

### 3.2 当前写入链路

当前有四类相关写入/合并点：

1. `_do_persist()` 的 `INSERT ... ON CONFLICT DO UPDATE`：冲突时使用 `total_cost = EXCLUDED.total_cost`。
2. `_do_persist()` 消费 pending 补丁：使用 `SET total_cost = %s`。
3. `update_total_cost()` 直接回填已存在行：使用 `SET total_cost = %s`。
4. `_remember_pending_total_cost()`：同一 `trace_id` 直接覆盖字典值。

`SessionRecordService.save()` 对内存 `trace.total_cost` 也直接赋值。

这些操作各自是幂等赋值，但不具备乱序安全性。

### 3.3 可触发的窄窗

典型风险时序如下：

```text
T1  worker 持有较早的 trace 快照，total_cost=0
T2  计费完成，update_total_cost 将数据库更新为 0.42
T3  延迟/重复的 UPSERT 命中同一 trace_id
T4  ON CONFLICT 用 EXCLUDED.total_cost=0 覆盖数据库 0.42
```

pending 的重复登记/重放也存在同类问题：较低的迟到值可能覆盖较高值。该窗口概率低，但一旦发生会让观测成本少计；因为这是观测投影，业务扣费不受影响，但监控和技术成本分析会失真。

现有 10 个 `test_trace_total_cost.py` 用例覆盖先写、后写、pending、重复同值和失败降级，但只断言 `total_cost = %s` / `total_cost = EXCLUDED.total_cost`，没有验证单调性。

## 4. 修复设计

### 4.1 SQL 写入统一单调化

三个数据库更新点必须全部改，不能只改 UPSERT：

```sql
-- ON CONFLICT 分支
total_cost = GREATEST(
    COALESCE(obs_traces.total_cost, 0),
    COALESCE(EXCLUDED.total_cost, 0)
)

-- 直接回填和 pending 重放
SET total_cost = GREATEST(COALESCE(total_cost, 0), %s)
```

使用 `COALESCE` 是为了兼容历史可空行；不修改字段定义，也不需要迁移数据。

`updated_at` 保持现有行为。即使传入值没有提升成本，回填尝试仍可更新技术时间戳；本轮不引入条件 UPDATE，以免改变 `rowcount` 的“记录存在”语义和 pending 判定。

### 4.2 进程内 pending 合并也保持单调

`_remember_pending_total_cost(trace_id, incoming)` 在锁内按最大值合并：

```text
pending[trace_id] = max(pending.get(trace_id, 0), incoming)
```

有界淘汰规则保持不变：超过 `_PENDING_TOTAL_COST_MAX` 仍按现有插入顺序丢弃最旧 trace。更新已有 key 不应改变顺序，避免顺便修改缓存策略。

### 4.3 内存 trace 同步保持同一语义

`SessionRecordService.save()` 回填内存 trace 时使用当前值与 `credit_cost` 的最大值，确保 worker 在读取共享 trace 对象时也不会看到回退值。

这一步不是数据库正确性的唯一依赖；数据库三处 `GREATEST` 仍是最终防线。

### 4.4 输入和纠错边界

- 本轮不新增金额格式、负数、NaN 校验；入参来自既有 billing 计算结果，保持最小改动。
- 运行时路径采用只增不减语义，因此不承担“人工纠正历史高估值”。如需下调历史观测成本，应使用独立、显式、可审计的数据修正脚本。
- 不修改 `chat_records.credit_cost`、租户余额或任何实际扣费逻辑。

## 5. 测试设计

### 5.1 定时任务工具测试收口

修改 `tests/unit/tools/test_scheduled_task_tool.py` 中需要进入业务分支的旧用例，为 `ToolExecutionContext` 显式补 `tenant_id="tenant_a"`。涉及：

- 创建参数归一化 3 项；
- 并发会话身份隔离 1 项；
- 越权拒绝与异常脱敏 2 项；
- dry-run 凭据脱敏 2 项；
- `view_logs` 历史脱敏 3 项。

保留“缺租户必须拒绝”的专用用例不变。所有 mock 断言同步检查 DB 方法收到了 `tenant_id + user_id`，不能只为变绿而放宽断言。

验收：`tests/unit/tools/test_scheduled_task_tool.py` 15/15 全绿。

### 5.2 `total_cost` 单调性单测

在 `tests/unit/test_trace_total_cost.py` 增加或调整以下断言：

1. UPSERT 冲突 SQL 包含 `GREATEST` 和现有值/`EXCLUDED` 值，迟到的 0 不具有覆盖语义。
2. `update_total_cost()` SQL 使用 `GREATEST(COALESCE(total_cost, 0), %s)`，参数顺序保持不变。
3. pending 重放 SQL 同样使用 `GREATEST`。
4. 同一 trace 依次登记 `0.75 → 0.25 → 1.20`，pending 结果依次为 `0.75 → 0.75 → 1.20`。
5. 内存 trace 已为高值时，较低回填不降低它。
6. 既有双时序、失败降级、缓存有界性测试继续通过。

### 5.3 PostgreSQL 行为测试

若当前测试环境提供观测库连接，增加一个最小集成用例，顺序模拟乱序写而不依赖概率性线程调度：

1. 插入 trace，成本 0.42；
2. 用低值 0 或 0.10 走冲突 UPSERT，断言仍为 0.42；
3. 直接回填 0.20，断言仍为 0.42；
4. 回填 0.80，断言提升到 0.80；
5. 测试后按精确 `trace_id` 清理。

如 CI 没有观测库，该集成用例应按项目现有集成测试惯例显式 skip，不能静默伪通过；SQL 结构和进程内逻辑仍由单测强制覆盖。

## 6. 验收标准

以下条件全部满足才可把安全债标记完成：

1. `X-Tenant-Id` 与定时任务属主代码不被回退。
2. `ManageScheduledTaskTool` 四个按 ID 操作继续在最终 SQL 层携带 `tenant_id + user_id`。
3. 定时任务工具单测文件全绿，不再存在因缺 tenant 的旧夹具失败。
4. `total_cost` 的 UPSERT、直接回填、pending 重放、pending 内存合并和 trace 内存同步全部满足只增不减。
5. 现有 10 个成本闭环测试不回归，新增单调性测试可在旧实现上失败、在新实现上通过。
6. 定向测试、scheduler 相邻回归、observability/session_record 相邻回归和 `import src.main` 通过。
7. 不新增表、字段、配置、API 或外部依赖；不改变业务计费结果。

## 7. 预计改动清单

| 文件 | 预计改动 |
|---|---|
| `src/core/trace_persist.py` | 三处 SQL 单调写入；pending 最大值合并 |
| `src/services/session_record.py` | 内存 trace 成本最大值合并 |
| `tests/unit/test_trace_total_cost.py` | 单调性与 SQL 防回退测试 |
| `tests/unit/tools/test_scheduled_task_tool.py` | 旧用例补可信租户上下文和属主参数断言 |
| 可选观测库集成测试 | 数据库实际 `GREATEST` 行为验证 |
| `docs/system/agent-runtime-safety-hardening-design.md` | 开发完成后同步最终状态 |
| `docs/plans/plan-agent-runtime-security-debt-closure.md` | 开发过程中更新勾选状态 |

## 8. 明确非目标

- 不重写或再次实现 `X-Tenant-Id` 中间件。
- 不重写已经落地的 scheduled task tenant/user 隔离。
- 不把 `obs_traces` 变成计费权威。
- 不增加分布式锁、数据库锁或新队列；`GREATEST` 已能在单条 SQL 原子更新内解决乱序覆盖。
- 不借机清理 scheduler、trace 或测试目录的其他问题。
