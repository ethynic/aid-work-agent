# 企业多智能体协作能力开发计划

> 日期：2026-08-12
>
> 状态：📋 待开发
> 设计：[企业多智能体协作架构](../system/enterprise-agent-platform/enterprise-multi-agent-collaboration-design.md)

## 1. 实施策略

采用“先替换运行地基，再开放高级能力”的渐进路线。现有 Web、渠道和数字员工入口保持兼容；每个阶段都能独立上线、灰度和回滚。禁止直接在现有 executor 上开启多层并发。

## 2. Phase 0：契约与安全基线（1.5～2 周）

- 建立 `contracts/agent-collaboration/`，定义 AgentRun、Instance、Assignment、Message、Event、Budget、ContextPackage、ResultEnvelope。
- 将 `tenant_id/task_id/session_ref/execution_id/agent_release_id/policy_revision/protocol_version` 设为必填。
- 定义命令幂等、event seq、mailbox seq、ACK、lease epoch 和 fencing token。
- 固化 `single_delegate/parallel_specialists/pipeline/maker_checker/map_reduce` 策略枚举。
- Python/TypeScript schema 生成或 conformance tests，禁止两端手写漂移。
- 建立 feature flags：租户白名单、最大并发、最大深度、动态组队开关。

验收：跨语言 golden fixtures 全绿；篡改 tenant/release/budget、重复命令、旧 epoch 结果全部拒绝。

## 3. Phase 1：持久化单成员兼容运行（2～3 周）

- 新建服务端运行表、event/outbox、Coordinator service 和 worker lease。
- 将 `delegate_to_subagent` 适配为 `spawn + assignment + wait`，行为与当前用户体验一致。
- 删除“进程内 task 是权威”的依赖；worker 重启可重新 claim 或 reconcile。
- 把澄清迁移为可寻址 `InputRequest`，支持多个成员并存。
- 冻结每次运行的 AgentRelease、工具 capability 和模型策略。
- 保留旧 Redis key 只读兼容期，完成中的旧任务自然排空，不迁移运行态。

验收：双 worker、强杀 worker、Redis 清空、重复 claim、取消竞态、迟到结果、租户隔离测试通过；现有子智能体回归不变。

## 4. Phase 2：并行 root 与协作原语（2～3 周）

- 实现 `spawn/send/follow_up/wait/interrupt/list/publish`。
- root 非阻塞等待，成员完成/消息/输入事件唤醒 Coordinator。
- 默认并发 3、深度 1；接入租户/用户/Task/团队预算和公平队列。
- 建立 artifact-first 结果协议与 root synthesis record。
- 为失败、超时、预算耗尽和成员无响应定义收敛策略。

验收：三成员并行的 wall time 明显低于串行；成本硬上限无越界；消息至少一次投递但业务无重复。

## 5. Phase 3：团队模板与企业治理（2～3 周）

- 新建 AgentTeamTemplate 管理：负责人、成员、策略、预算、成功标准。
- 支持草稿、版本、评测、发布、租户授权、灰度和回滚。
- 接入 Policy PDP、审批义务、action ticket、Evidence Ledger 和计费。
- 首批内置模板：深度调研团队、方案制作+独立复核团队、批量资料处理团队。
- 子级 spawn 开放至深度 2，但只能在模板/Policy 上限内。

验收：模板 release 可复现；子级不能扩大数据、工具或节点权限；团队成本完整归集到 Task/租户。

## 6. Phase 4：Web 协作界面（2～3 周）

- 在 `frontend/web` 增加共享 team store、event reducer 和 contracts client。
- 对话流加入折叠协作卡片；详细面板展示责任树、依赖、状态、产物、证据和成本。
- 支持 steer、follow-up、interrupt、cancel、input/approval 响应。
- 网络重连执行 cursor replay、gap 检测和 snapshot resync。
- 保留旧单行进度展示作为降级路径。

验收：刷新、断网重连、事件重复/乱序、多澄清、运行中取消均保持一致；无团队时 Web 行为和性能不回归。

## 7. Phase 5：Desktop 同构协调（3～4 周，依赖 Desktop Coordinator）

- `frontend/desktop` 复用 contracts、team reducer 和展示组件，不复用 Web 页面容器。
- 在 Local Agent Coordinator 实现同构 event store、mailbox、lease/fencing 和恢复状态机。
- `DEVICE_OWNED` AgentRun 固定 owner device；Web/移动端只通过 Relay 发送命令。
- Agent member 与 tool execution node 解耦；成员可分别调用 Server、本机或远端 runtime 工具。
- 关窗、睡眠、断网、强杀、更新时执行 quiesce/reconcile。

验收：Desktop 离线可继续策略允许的本地协调；跨端接续不迁移环境；本地恢复后不重复外部副作用。

## 8. Phase 6：评测驱动优化（持续）

- 建立单智能体 vs 团队 A/B 和 Golden Tasks。
- 按任务类型学习是否组队、选何模板，而不是让模型无约束生成组织结构。
- 优化上下文包、模型分层和成员复用，减少重复 token。
- 只在评测门禁通过后逐步开放动态选成员和更深层级。

## 9. 数据迁移与兼容

- 现有 `SUBAGENT.md` 转为 AgentDefinition 草稿，发布时生成不可变 AgentRelease。
- `SubagentTaskRecord` 不原地扩表；新运行进入新模型，旧记录按原 TTL 排空。
- `SubagentExecutionContext` 与 `SubagentTaskRecord` 重叠模型在 Phase 1 后废弃。
- `delegate_to_subagent` 至少保留两个发布周期，内部使用新 Coordinator。
- Standalone specialist 继续作为“单专家直接会话”，可由用户显式升级为团队，但不能自动改变原会话 owner。

## 10. 发布门禁

- P0：tenant 不可空、持久 Coordinator、lease/fencing、预算硬限制、Policy 权限收窄。
- P0：未知副作用不重试，Coordinator/worker 崩溃恢复测试通过。
- P1：Web/Desktop 协议 conformance、事件 replay、可观测与成本归集完整。
- P1：团队质量相对单智能体基线有统计意义提升，否则不默认启用。
- P1：任何阶段均可按租户关闭团队功能并回退单智能体新运行。

## 11. 建议投入顺序

先完成 Phase 0～2，得到可靠的“可并行、可通信、可恢复”内核；再做团队模板和 UI。第一批不要追求大量角色，重点做 3 个真实企业任务模板并用结果证明价值。预计首个可用闭环 6～8 周，Web 企业版 10～13 周；Desktop 同构能力随 Desktop Coordinator 再增加 3～4 周。
