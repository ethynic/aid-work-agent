# 企业多智能体协作能力调研与现状审计

> 日期：2026-08-12
>
> 状态：调研完成
> 范围：Cloud Agent、Web、Desktop、移动/企业渠道、Server ToolExecutor、Local Tool Host、`agent-tool-runtime`

## 1. 结论

项目已经具备“专家型数字员工”，但尚不具备真正的“智能体团队”。当前 `delegate_to_subagent` 是单层、同步等待、进程内运行的专家调用：它能把一件事交给一个角色，却不能让多个有独立上下文的成员并行工作、互相通信、接受追加任务、被中断或在故障后恢复。

下一代能力不应继续扩展 `delegate_to_subagent` 参数，而应建立产品级 **Multi-Agent Collaboration Fabric（多智能体协作层）**。它必须同时服务 Web 和 Desktop，并与 Task、Policy、Execution Fabric、Evidence、Memory、Evaluation 体系对齐。

## 2. 外部参照能证明什么

### 2.1 Codex / OpenAI

OpenAI 官方多智能体协议采用 root 与 subagent 的层级运行模型。每个 subagent 有独立、受限的上下文，root 可并行创建成员，并使用 `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`interrupt_agent`、`list_agents` 协调，最终由 root 综合结果。官方也明确指出：并行独立工作流适合多智能体，严格串行或共享可变资源的任务未必适合，并行会增加 token 成本。

这说明领先方案的核心不是“多调用几次模型”，而是：

- 独立上下文和明确任务边界；
- 可寻址的智能体实例与层级；
- 非阻塞并行、邮箱通信和生命周期控制；
- 完整事件流、归因、重放与 tracing；
- 根智能体对冲突、证据和最终结果负责。

参考：[OpenAI Multi-agent documentation](https://developers.openai.com/api/docs/guides/responses-multi-agent)、[OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)。

### 2.2 WorkBuddy

WorkBuddy 公开产品文档把“专家”定义为人设、方法论和工具链的封装，把“专家团”定义为由负责人自动拆解、多个专家并行执行、负责人整合的团队。其公开资料可以用于比较产品形态，但不能据此推断其未公开的底层运行时实现。

本项目现有 `SUBAGENT.md` 更接近 WorkBuddy 的“专家”，距离“专家团”主要差在团队模板、负责人、任务拆解、并行协作、结果汇聚和用户可见性。

参考：[WorkBuddy 专家与专家团](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Expert-Center)、[WorkBuddy 产品概览](https://www.workbuddy.cn/docs/workbuddy/Overview)。

## 3. 当前代码审计

| 维度 | 当前实现 | 影响 |
|---|---|---|
| 调用模型 | `delegate_to_subagent` 一次选择一个 `subagent_name` | 只能委派，不能组队 |
| 运行所有权 | `SubagentExecutor` 用 `asyncio.create_task` 持有活跃执行 | worker 重启后不可恢复，其他 worker 不可接管 |
| 持久化 | `SubagentTaskRecord` 写 Redis，TTL 7200 秒 | 不是企业任务账本，过期后不可追踪 |
| 等待方式 | 主智能体 `wait_for_result` 每 0.5 秒轮询 | root 实际阻塞，难以并行协调 |
| 并发 | 每进程 `min(32, cpu_count * 2)` Semaphore | 无租户、团队、成本和公平性预算 |
| 层级 | 子智能体被强制禁止继续委派 | 无树、无受控递归、无负责人层级 |
| 通信 | 只有最终结果和一个澄清问题 | 无 mailbox、成员消息、追加任务、广播和 ACK |
| 澄清 | 每个会话保存单个 pending clarification；回答后重新委派 | 多成员会冲突，且不是恢复原上下文 |
| 上下文 | task description + context dict | 缺少成功标准、权限、预算、因果链、输入版本 |
| 结果 | 非统一 dict/summary | 无 evidence、artifact、confidence、冲突声明 |
| 定义版本 | 注册表加载文件/DB 当前值 | 一次团队运行未冻结 AgentRelease |
| 权限 | 各子智能体按配置过滤工具 | 没有每次运行的签名 capability/policy snapshot |
| 前端 | 展示“调用某子智能体”的进度文本 | 无团队树、成员状态、干预和成本视图 |
| Desktop | 没有共用协作契约 | 未来会形成第二套、不兼容的多智能体实现 |

### 3.1 特别风险

1. `tenant_id` 在 executor 中允许获取失败后继续为 `None`。多智能体并发会放大租户隔离风险，正式协议必须把 tenant 作为不可空的权威字段。
2. Redis 记录和本地 task 分离，取消只能可靠影响当前进程；不存在 lease、fencing token 或迟到结果拒绝机制。
3. `allow_delegation`、`delegatable_to` 已出现在配置中，但运行模式又强制禁止子级委派，属于“配置表达能力大于实际安全模型”。
4. `SubagentExecutionContext` 与 `SubagentTaskRecord` 语义重叠，继续叠加字段会加重双模型漂移。
5. Standalone specialist 是入口绑定的单智能体，不等于多智能体；二者必须保留清晰的产品概念。

## 4. 差距矩阵

| 能力 | 当前 | 目标 |
|---|---|---|
| 专家角色 | 已有 | 版本化 AgentDefinition / AgentRelease |
| 单次委派 | 已有 | 兼容适配器保留 |
| 并行成员 | 无 | 有界并行、按依赖调度 |
| 层级协作 | 无 | 有限深度 agent tree + assignment DAG |
| 成员通信 | 无 | 持久 mailbox + 幂等消息 |
| 追加/纠偏 | 无 | follow-up、steer、interrupt、resume |
| 团队模板 | 无 | 负责人、成员角色、协作策略和预算 |
| 上下文隔离 | 弱 | 默认最小上下文包、artifact-first 共享 |
| 恢复 | 无 | durable coordinator + lease/fencing/reconcile |
| 企业治理 | 弱 | 租户配额、Policy、审批、审计、计费 |
| 用户界面 | 单行进度 | Web/Desktop 同构团队运行面板 |
| 评测 | 无团队指标 | 团队 Golden Tasks、质量/成本/时延评测 |

## 5. 产品判断

多智能体不应成为“所有请求默认多开模型”的营销功能。它只在任务可拆成独立工作流、需要不同专业视角、需要独立复核或需要大量信息并行收集时启动。简单问答、严格顺序任务、单一 GUI 焦点操作继续使用单智能体。

企业场景的真正优势不是能开最多成员，而是：负责人可解释地拆解，成员在明确权限和预算内工作，执行可恢复，结果有证据，用户能干预，组织能复用优秀团队模板。
