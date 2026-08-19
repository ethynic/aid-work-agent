# Task Plane Phase 0+1 开发计划

> 日期：2026-08-19
> 设计文档（权威实现方案）：[task-plane-phase0-1-landing-design.md](../system/enterprise-agent-platform/task-plane-phase0-1-landing-design.md)
> 人力假设：1 名后端开发；总量约 2.5~4 周
> 流程：非平凡任务走三智能体流程（开发 → 独立测试 → CodeReview）

所有实现细节（DDL、函数签名、接入点行号、状态映射、测试清单）以设计文档为准，本计划只管顺序、验收与门禁，不重复方案。

## Phase 0-A：安全债修复（2-3 天，可独立先发）

| # | 任务 | 依据 |
|---|---|---|
| A1 | `deploy/db_update.sql` + `init-postgres.sql` 加 §4.3 安全债 DDL（scheduled_tasks/logs 租户列 + 回填 + 索引），测试环境验证回填覆盖率（现存行 tenant 全部非 ''或确属已删用户） | 设计 §4.3 |
| A2 | `src/scheduler/db.py`、`src/api/scheduled_task.py`、`src/scheduler/executor.py` 租户条件改造 | 设计 §10.1 |
| A3 | 知识库三处对象级租户条件（service.delete_document / get_document_chunks / download 裸 SQL） | 设计 §10.2 |
| A4 | obs total_cost：trace_persist 参数化 + TraceRecord 字段 + save() 回填 UPDATE | 设计 §10.3 |

**DoD**：`tests/integration/test_scheduled_tasks_tenant.py`、`test_knowledge_tenant_guard.py` 新增通过；scheduler/知识库/obs 相邻既有测试全绿；生产回填前先在测试库演练 UPDATE 行数并记录。

## Phase 0-B：契约与守卫（1-2 天）

| # | 任务 | 依据 |
|---|---|---|
| B1 | `contracts/task-plane/` 三文件（两个 schema + README 变更规则）+ `src/task_plane/contracts.py` + `test_contracts_task_plane.py` | 设计 §5 |
| B2 | `tests/unit/test_agent_structure_guard.py`（agent.py ≤ 4284 + task_plane 不 import 具体工具类） | 设计 §15 |
| B3 | 工具 effect/幂等清单：`docs/system/enterprise-agent-platform/tool-effect-inventory.md`——从 `src/tools/` 注册表枚举全部业务工具，按「只读=none / 有副作用=applied / 不可确认=unknown」三分类 + 幂等键 + 重试策略三列；每个工具必须非空，缺信息的标 `unknown+待补` | 设计 §1.1.5 |

**DoD**：清单覆盖注册表 100% 工具；守卫测试进 CI。

## Phase 1-A：表与仓储（2-3 天）

| # | 任务 | 依据 |
|---|---|---|
| A1 | §4.1 六表 + §4.2 chat_records 增量的双文件 DDL（db_update 幂等块，注释带设计文档链接） | 设计 §4 |
| A2 | `src/task_plane/`：ids.py / repository.py（§5.1 全部函数）/ persist.py 队列 | 设计 §5 |
| A3 | `tests/unit/test_task_plane_repository.py` + `tests/integration/test_task_plane_dual_write.py`（仓储层先行，recorder 未接也能跑落库断言） | 设计 §13 |

**DoD**：集成测试在真实 PG 通过；所有 SQL 断言带 tenant_id。

## Phase 1-B：录制器与接线（3-4 天）

| # | 任务 | 依据 |
|---|---|---|
| B1 | recorder.py 全量（事件映射表 + outbox 生成 + 构造期同步 UPSERT + 终态异步落库） | 设计 §7 |
| B2 | agent.py 接线（≤20 行）+ `_resolve_session_ref` helper + task_plane_ref 回注 | 设计 §6.1/6.2 |
| B3 | session_record.py save() 两处（关联列 + total_cost 回填） | 设计 §6.4 |
| B4 | TaskPlaneConfig + yaml 节 + env 覆盖 + main.py 路由注册 stats 端点 + api.py | 设计 §6.6/§11 |
| B5 | `tests/unit/test_task_plane_recorder.py` 全事件行覆盖 | 设计 §13 |

**DoD**：门禁 §1.3 全项（关开关零差异、开开关双写可见、断 PG 降级、结构守卫、全量回归绿）。三智能体流程必走。

## Phase 1-C：策略影子与对账（2 天）

| # | 任务 | 依据 |
|---|---|---|
| C1 | policy_shadow.py + main.py 四处接线 + reason_code 分流 | 设计 §6.3/§8 |
| C2 | reconcile.py + scheduler 注册 `job_task_plane_reconcile`（03:40） | 设计 §6.5/§9 |
| C3 | stats 端点补 shadow 计数与 coverage 差值 | 设计 §11 |

**DoD**：`test_task_plane_policy_shadow.py` 通过；测试环境实跑验证 stats 数据合理。

## Phase K：工具调用分发 seam 抽取（3-5 天，可延后，不阻塞以上）

| # | 任务 | 依据 |
|---|---|---|
| K1 | agent.py 工具循环通用执行分支（LOCAL_REQUIRED / 通用 execute / ToolSuspension / tool_result 三种收口，当前 :3325-3530）移入 `src/core/tool_call_dispatcher.py`；特殊分支留 agent.py 改为调用 dispatcher；**行为零变更** | 设计 §15 P4 |
| K2 | 抽取后 agent.py 行数应净降（更新结构守卫基线） | 原则 P2/P5 |

**DoD**：现有 agent 全量回归 + Desktop D1 挂起相关用例绿；CR 确认无行为 diff（对比事件序列输出）。

## 关键检查点

1. **Phase 0-A 发版后**：确认生产回填无异常，再继续。
2. **测试环境开双写 3 天**：stats coverage 差值 <1%、degraded=0、无性能回归（P95 响应对比）→ 才开生产。
3. **Phase 1 全部完成后（go/no-go）**：评估是否立即启动多智能体协作体系 Phase 0-2（其两个前置已由本切片解决，见设计 §14.3）。

## 明确不做（防蔓延）

上位 Phase 2-4 的全部内容（Artifact 映射、scheduler Execution、Step 表、v1 Task API、DEVICE 会话、Task 工作台）——见设计 §1.2 裁剪表，期间不接受"顺手加上"。
