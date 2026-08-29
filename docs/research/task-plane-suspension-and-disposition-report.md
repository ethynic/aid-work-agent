# Task Plane 暂停与已开发内容处置报告

> 日期：2026-08-28
>
> 状态：**决策生效；停止合并与部署**
>
> 适用分支：`feature/task-plane-phase0-1`
>
> 评估基线：`feature/task-plane-phase0-1@bdaecd09`

## 1. 决策

Task Plane 项目立即暂停。上述 feature 分支不得整体合并到 `master`，不得整体 cherry-pick，也不得作为后续 Task 设计的兼容基线。

本次暂停不是延期，也不是等待补充几个字段。原方案没有先定义 Session、Task、Execution 的权威边界，却在落地阶段采用了“一个原生 Session 自动创建并永久绑定一个 Task”的根假设。该假设无法正确表达：

- 同一 Session 内的多个独立目标；
- 与业务任务无关的闲聊、问答和一次性交互；
- 同一业务目标跨多个 Session、渠道和日期继续；
- 用户中途切换目标、回到旧目标或同时提出多个目标；
- 谁有权创建、拆分、合并、完成或纠正 Task。

因此，当前实现生成的 `enterprise_tasks` 不是可靠的业务事实。首条消息截取标题、Session 唯一绑定、每轮执行继承同一 Task 等逻辑会把不确定推断写成权威数据。这是领域模型错误，不是增加分类器、调整唯一索引或允许 `task_id` 为空即可修复的问题。

## 2. 处置原则

1. **Task 相关实现全部作废。** 即使 AgentRelease、Policy Shadow、Execution、outbox 等概念未来可能有独立价值，也不从本次错误实现中保留，避免旧契约成为新设计的事实标准。
2. **只选择性提取与 Task 无关、行为可确定的安全修复。** 每项必须相对 `master` 重新审查、独立测试和单独提交。
3. **禁止按提交或目录整体搬运。** feature 提交同时混合了 Task 运行时、DDL、契约、安全修复和结构调整；只能按最小 diff 人工提取。
4. **旧 Task 设计不承担兼容责任。** 未来若重新研究 Task，必须从真实业务场景和权威来源重新立项。

## 3. 删除与保留矩阵

### 3.1 DELETE：不得合并；仅在 feature 净化或已合并补救时删除

| 范围 | 明确对象 | 处置理由 |
|---|---|---|
| 运行时模块 | `src/task_plane/**`，包括 recorder、repository、persist、contracts、ids、policy_shadow、reconcile、API | 全部围绕错误 Task/Execution 归属和专属存储构建，不形成可复用基线 |
| 契约 | `contracts/task-plane/**` | Task/Execution 信封和事件定义以错误领域模型为前提 |
| 六张表 | `enterprise_tasks`、`task_session_links`、`agent_executions`、`agent_release_snapshots`、`policy_shadow_decisions`、`task_plane_outbox` | 不保留错误表名、关系、状态或数据作为未来兼容负担 |
| 现有表增量 | `chat_records.task_id`、`chat_records.execution_id` 及 `idx_chat_records_execution` | 关联对象没有可靠业务含义 |
| 配置 | `TaskPlaneConfig`、`settings.task_plane`、`configs/config.yaml` 的 `task_plane` 节、相关环境变量解析 | 仅服务已作废运行时 |
| 路由与启动接线 | Task Plane stats 路由、`src/main.py` policy shadow 接线及启动日志 | 不应暴露已作废能力或统计口径 |
| Agent 接线 | `src/core/agent.py` 的 recorder 构造、事件、失败、取消、完成接线 | 每轮对话不应自动产生权威 Task/Execution |
| 会话记录与 DB 接线 | `SessionRecordService` 的 `task_plane_ref`、`task_id`、`execution_id` 透传；`src/db/models.py` 中 `ChatRecordDB` 新增的 `task_id` / `execution_id` 方法签名、INSERT 列和参数 | 删除错误关联写入，恢复 master 的 ChatRecordDB 契约 |
| 专属语义 | Task/Execution 生命周期、outbox、AgentRelease 快照、Policy Shadow、coverage/stats、orphan reconcile | 即使名称可再利用，也不从本实现继承数据、状态机和契约 |
| 测试 | 所有 `test_task_plane_*`、`test_contracts_task_plane.py`、Task Plane 集成测试；结构测试中仅为 `src/task_plane` 设置的断言 | 测试固化了错误假设，不能用绿测证明领域正确 |
| DDL 与注释 | `deploy/init-postgres.sql`、`deploy/db_update.sql` 中 Task Plane 六表、列、索引及专属注释 | 从 feature 净化目标或已误合并的正式分支发布脚本删除；最新 master 若从未包含则无需做删除提交；已执行环境单独处理 |
| 已删除设计的代码引用 | 源码、`contracts/**`、测试、`deploy/**`、`configs/**` 中所有指向 `task-plane-phase0-1-landing-design.md` 或其他已删除 Task 设计/计划的注释、docstring、schema description 和路径文本 | 旧出处不能继续被工具、测试或开发者当作权威规范；feature 净化或已合并补救时一并清除 |
| 旧专属文档 | `enterprise-task-model-design.md`、`task-plane-phase0-1-landing-design.md`、`plan-task-plane-phase0-1.md` | 设计前提作废，本次直接删除，避免继续被引用 |

当前 feature 中已确认的旧设计引用分布包括：`configs/config.yaml`，`contracts/task-plane/**`，`deploy/{init-postgres.sql,db_update.sql}`，`src/task_plane/**`，Task Plane 专属单元/集成测试，以及混合文件 `src/api/scheduled_task.py`、`src/scheduler/db.py`、`src/knowledge/service.py`。前五类随 feature 净化/已合并补救删除对应 Task 内容；后三个保留候选只能提取独立安全逻辑，并移除旧设计路径与 Phase 出处注释。

### 3.2 RETAIN：只允许从 master 选择性提取

| 独立能力 | 候选改动 | 保留条件 |
|---|---|---|
| 定时任务隔离 | `deploy/{init-postgres.sql,db_update.sql}` 中仅 scheduled 表增量；`src/{api/scheduled_task.py,scheduler/db.py,scheduler/executor.py,tools/scheduler/scheduled_task_tool.py}` | 相对 master 重新审查回填、索引、API、工具和 DB 层；越权测试必须 fail-closed |
| 调度上下文安全 | `src/scheduler/executor.py`、`src/core/agent.py` 中仅请求级 tenant/user ContextVar 设置与恢复、tenant conflict fail-closed 的最小 diff | 不携带 Task 标识；并发、异常、取消和嵌套调用后上下文必须恢复 |
| 错误脱敏 | `src/scheduler/error_sanitizer.py` 及 `src/api/scheduled_task.py`、`src/scheduler/db.py` 的调用点 | 覆盖 `password/token/secret/api_key` 等多分隔符、引号、跨行和截断场景；不输出敏感值 |
| 知识库租户保护 | `src/knowledge/api.py`、`src/knowledge/service.py` | 每个入口独立验证对象归属，不能只依赖上游 |
| 可观测成本闭环 | `src/core/trace_collector.py`、`src/core/trace_persist.py`、`src/services/session_record.py` 中仅 `obs_traces.total_cost` 与 pending 回填的最小 diff | 只服务现有 observability；删除同文件中的 Task ref；以计费记录为权威 |
| 租户语义 | `src/models/user.py`、`src/channels/agent_user_builder.py`、`src/desktop_agent/turn.py` 以及 `src/core/agent.py` 中与请求身份有关的最小 diff | `None` 表示未知/缺失、`""` 仅表示明确公共域；证明与 Task 无关且符合现有权限模型 |
| 纯事件 helper | `src/core/agent_events.py` 及 `src/core/agent.py` 的三个通用 helper 调用/再导出 | 逐函数确认无 Task 依赖、行为等价、原测试覆盖充分 |
| Kernel 守卫 | `tests/unit/test_agent_structure_guard.py` 中 `agent.py` 行数只降不升、通用依赖方向部分 | 删除 `src/task_plane` 专属测试、断言和文案后重写为独立测试 |
| 浏览器测试隔离 | `tests/unit/tools/browser/test_phase3_human_control.py` 的 feature diff：显式传入空 `continuation_callback`，隔离其他测试安装的进程级 callback | 该改动不含 Task 字段，保护“恢复事件不持久化敏感页面正文”的独立断言；仅在最新 master 可复现全局污染时保留，否则恢复 master |
| 工具安全清单 | 工具 effect、幂等性、盲重试风险清单 | 作为独立安全审计资料，不归属于 Task/Plane，不据此自动执行或重试 |

以上“保留”是候选资格，不代表 feature 中的实现已经自动通过。每一项都必须重新 review，禁止整体 cherry-pick feature 提交。

重提任何 scheduled task、knowledge、observability、tenant context 或通用 helper 候选时，必须删除 diff 中“Task Plane Phase 0-A/0-B”“见 task-plane-phase0-1-landing-design.md”等旧项目出处注释，并改为该独立安全能力自身可验证的行为说明；不得仅因代码被保留而保留错误设计的来源关系。

保留项测试也必须重新归档：可参考 `tests/integration/test_scheduled_tasks_tenant.py`、`tests/integration/test_knowledge_tenant_guard.py`、`tests/unit/tools/test_scheduled_task_tool.py`、`tests/unit/channels/test_agent_user_builder.py` 和 `tests/unit/test_models.py`；`tests/unit/test_task_plane_phase0a.py`、`tests/unit/test_task_plane_review_fixes.py` 混有 Task 专属断言，不能原名或整文件保留，只能把独立安全用例重写到所属模块测试中。`tests/unit/tools/browser/test_phase3_human_control.py` 的 7 行 feature diff 已核实为 callback 测试隔离，不含 Task 关联；是否提取仍以最新 master 上可复现性为准。

## 4. Commit 提取策略

- 不合并 `feature/task-plane-phase0-1`。
- 不 cherry-pick `bdaecd09` 或该分支上的任何混合提交。
- 从最新 `master` 新建独立分支，使用 `git diff master...feature -- <精确文件>` 辅助查看，然后按最小逻辑块人工应用。
- 每个保留项独立提交；提交信息不得使用 “Task Plane Phase 0-A” 等已作废项目名。
- 每项执行开发、独立测试、CodeReview；发现与 Task 接线耦合时先去耦，不为复用旧代码保留兼容层。

## 5. 数据库处置

feature 尚未合并 `master`，所以生产环境不得因本报告自动执行任何 `DROP`。

如果测试库、开发库或其他环境已经执行六表 DDL：

1. 先盘点环境、表是否存在、是否含非测试数据；
2. 另写显式、可审阅的清理迁移；
3. 明确删除顺序、备份/导出需求和回滚说明；
4. 仅在确认目标环境后执行；
5. 禁止把自动 `DROP TABLE` 混入常规应用启动或生产初始化脚本。

旧表、ID、事件和测试数据不迁移到未来 Task 方案。

## 6. 暂停期间规则

- 不设计或开发 Task UI。
- 不开发对话 Task 识别器、分类器或自动抽取器。
- 不继续细化 Task、Session、Execution、Artifact 的统一抽象。
- 不以 Agent 返回成功、一次工具调用或 Session 边界推导业务 Task。
- 不以当前六表、契约、状态或 ID 作为其他项目的前置依赖。
- 协作、Evidence、评测等项目只能使用自身已经明确的事实对象；不得宣称 Task 前置已解决。

未来只有在具备真实客户场景、明确权威来源、完成标准、归属边界、纠错机制和生命周期后，才可以另立新项目。新项目从零命名和建模，旧表/契约不构成兼容负担。

## 7. 后续动作必须分离

本报告只做决策和文档处置，不在当前任务修改运行时代码。

### A. Master 安全候选重提取

1. 以最新 `master` 新建只用于运行时安全加固的分支。
2. 逐项、按最小 diff 重新实现或提取 §3.2 候选，删除旧 Task 出处注释并完成独立测试与 CodeReview。
3. 该分支不得合并 feature、不得搬运 Task runtime，也不得承担 feature 净化或六表清理。
4. 确认最终提交只包含经验证的独立安全修复。

### B. Feature 冻结或弃置

`feature/task-plane-phase0-1` 保持冻结/弃置，不合并到 `master`，不作为安全修复分支。默认无需为已弃置分支继续投入清理工作。

若出于审计归档确需把 feature 净化为“不含 Task runtime”的历史分支，必须另建独立动作，删除 §3.1 的 runtime、contracts、tests、DDL、配置、路由、wiring、旧开关以及指向已删除设计的所有源码/契约/测试/部署注释；该净化结果仍不得整体合并到 `master`。

只有在 Task 代码曾经被合并到其他正式分支或部署环境时，删除 Task runtime 才属于该分支/环境的补救任务，不能混入 A 的 master 安全候选重提取。

### C. 已执行 DDL 的环境清理

1. 先盘点测试、开发及其他环境是否执行过六表 DDL、是否存在需要保留的数据。
2. 对确认需要清理的环境单独编写、审阅和执行显式清理迁移。
3. 不自动 DROP 生产，不把环境清理迁移混入 A 的安全修复提交，也不以 B 的 feature 净化代替数据库处置。

## 8. 结论

本阶段真正可转化的价值是若干确定性的运行时安全修复和工程约束，而不是 Task 模型。Task 相关开发全部停止并按 DELETE 清单处置；后续不为沉没成本继续维护错误抽象。
