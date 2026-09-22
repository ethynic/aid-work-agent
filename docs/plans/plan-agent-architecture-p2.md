# AID Work Agent 其他架构演进 P2 规划

> 日期：2026-09-22  
> 版本：v1.0  
> 优先级：P2  
> 来源：`AID_Work_Agent_架构评审与桌面端统一设计_v2.0.md` 中不属于整体架构 P0、也不属于桌面客户端 P1 的内容
>
> 上位文档：[统一 Agent Run P0 规划](./plan-unified-agent-run-lifecycle.md)、[桌面客户端 P1 规划](./plan-desktop-client-p1.md)

## 1. 结论：原文还有哪些内容未进入前两份文档

有，主要包括八类：

1. ZCode、Codex 的对照研究和外部参考来源；
2. 超出 P0 最小恢复范围的完整事件、检查点与长期保留能力；
3. Provider 独立发布、能力市场和版本生态；
4. browser 工具按 Execution Fabric 重新设计；
5. 复杂离线、系统级常驻和跨设备调度；
6. 多客户长期任务、发现/决策/执行分层和公平调度；
7. 更完整的审计、指标、评估和数据最小化治理；
8. 目录治理、依赖策略和长期 ADR/参考基线维护。

这些内容有价值，但不会直接阻塞当前 Web 与已售生产渠道的 Run 解耦，也不应成为桌面首发前的无界前置工作，因此统一进入 P2。

P2 不得推翻已经在 P0 固定的执行所有权、typed SessionRef、可信上下文、幂等、取消、
finalization、等待权限、事件 reset 和副作用恢复语义，也不得在桌面之外再建立第三套业务
运行中心。未来渠道统一通过 Channel Gateway/Connector 接入，未来设备能力统一通过 Device
API/Provider 接入，两类扩展不能互相替代或绕过各自信任边界。

## 2. P2-01 完整事件、检查点与长期恢复

P0 只要求关键事实可靠、断线可恢复、runner 崩溃后安全收敛。P2 在此基础上补充：

- 更细粒度的模型阶段和工具阶段检查点；
- 明确事件保留窗口、冷热分层、归档和删除策略；
- 高频文本 delta 的压缩、合并和有限重放；
- 在 P0 `reset_required + snapshot_seq` 基线上扩展冷热层；即使增加归档，也不能要求客户端或
  projector 为已清理的 best-effort progress 阻塞终态、补造事件或重跑 Run；
- 快照生成、校验、压缩和历史版本管理；
- ModelAttempt 超时、供应商结果未知和费用对账；
- 跨版本检查点升级与不兼容恢复策略；
- reconciliation 操作台、人工核验流程和积压 SLA；
- 大规模故障注入、混沌测试和恢复时间目标。

不追求任意 token 位置的通用无损恢复，也不把 Python 协程或生成器序列化为检查点。

### 退出条件

- 能说明每一种崩溃点恢复到哪里、是否会产生第二次外部请求；
- 游标过期、快照竞态和事件积压有稳定行为；
- reconciliation 有负责人、入口、证据和收敛期限，不是永久状态垃圾桶。

## 3. P2-02 Execution Fabric 与 browser 重构

当前 browser legacy 已在 P0 明确 `not_migrated`。如果重新立项，应按通用 Execution Fabric 设计，而不是恢复旧路线：

- browser invocation 使用 Run/ToolInvocation 身份；
- owner、claim、lease、fence、attempt 和 resume job 持久化；
- 画面流通过跨进程 relay/object channel，UI Hub 只做订阅投影；
- 人工接管是明确的 waiting/approval 对象，不依赖 HTTP worker 内 dict；
- 结果、证据和 effect 使用统一工具协议；
- 浏览器进程生命周期、泄漏检测和清理归执行器负责；
- `bs_browser_resume_jobs` 是否迁入通用 invocation attempt 由专项设计决定；
- 没有通过恢复和安全门禁前，不重新进入 RunService 工具矩阵。

同一专题可进一步统一 Server、Local Runtime 和其他受信执行节点的能力登记、健康状态、亲和性和调度，但不能把所有执行节点提升为业务 Run 所有者。

## 4. P2-03 Device Provider 与 Channel Connector 生态

在真实 Provider/Connector 数量和升级压力出现后，再推进，但保留两种独立端口：

- Device Provider 位于受信 Runtime 后面，领取 Invocation、执行本地动作并回传 evidence；
- Channel Connector 位于云端 Channel Gateway 边缘，只负责认证/解密/ACK、规范消息转换和
  平台 delivery，不执行 Agent 工具，也不拥有 Run。

不得为了“统一插件”把二者合并成一个拥有渠道 secret、设备密钥和业务 Run 权限的万能扩展。
在此前提下再推进：

- 稳定 Provider 接口、manifest、能力版本和健康检查；
- Runtime 核心不依赖具体微信、BOSS、browser 等业务 Provider；
- Provider 独立构建、签名、发布、回滚和最低 Runtime 版本；
- Connector 独立 capability profile、凭据引用、契约测试、灰度和最低 Gateway 版本；
- 第一方 CLI/MCP Provider 与 Runtime 的统一接入规范；
- 多 Provider 共用本地资源仲裁；
- 安装包可预装常用 Provider，但源码和版本生命周期解耦；
- 插件市场、第三方 Provider 审核和自动更新在确有需求时另行立项。

### 退出条件

- Runtime 能在没有任一特定业务 Provider 的情况下构建和测试；
- Provider 缺失、过旧、损坏或能力不足时明确拒绝，不偷偷降级到不安全路径；
- 一个 Provider 的发布不要求同步发布所有客户端和服务端组件；
- 一个新 Channel Connector 无需修改 AgentApplication，且不能绕过 durable receipt、wait binding、
  delivery outbox、route_epoch/draining 和持久预算规则。

## 5. P2-04 长期任务、多客户调度与公平性

原文的“约 100 位授权客户长期跟进”场景进入 P2，用于验证 Task 与 Run 的分离：

```text
发现层 → 任务层 → 云端决策层 → 设备执行层
```

- 发现层产生候选观察，不直接作业务决定；
- 任务层维护每位客户的水位、待处理状态和授权范围；
- 决策层根据稳定观察版本产生回复或等待决定；
- 执行层取得桌面资源，重新定位并复核目标后执行；
- 等待客户回复期间保留 Task，但不维持一个常驻模型循环，也不占用桌面锁；
- 新消息、人工回复、任务暂停或授权变化会使旧决策失效；
- 发现可以批量，云端决策按额度并发，真实桌面动作按冲突资源串行；
- 调度包含租户配额、优先级、公平性、冷却和防饥饿。

消息指纹、截图和 UI 坐标都不能直接当作永久业务身份。重名、目标不确定或水位不可靠时必须阻断发送。

### 退出条件

- 100 个等待型 Task 不产生 100 个常驻 Agent loop；
- 活跃客户不会无限占用设备；
- Provider 的识别实现变化不会迫使云端 Task 状态机重写；
- 决策到发送之间发生人工操作时，旧决定可靠失效。

## 6. P2-05 复杂离线、常驻与跨设备能力

桌面 P1 只定义 managed child 与 external Runtime 的基本生命周期。以下能力后置：

- 系统服务/daemon、开机启动和多用户会话；
- 长时间离线队列和有限离线授权；
- 多设备候选、设备亲和性和人工迁移；
- 跨设备接管前的证据核验与资源 fencing；
- Runtime 自动更新、灰度、回滚和密钥轮换自动化；
- 本地日志损坏、磁盘容量和长期证据保留治理。

离线能力不能演变为本地独立业务主持：新的模型决策、预算和权限仍由云端控制。已明确授权的有限动作也必须受有效期、参数摘要和本地安全条件约束。

## 7. P2-06 多端体验与共享展示能力

P1 只要求 Desktop 与 Web 共用 AgentClient、RunProjection 和协议语义。微信客服及 Phase 0
盘点确认的已售、生产启用渠道，已由 P0 Phase 6 强制迁入统一 Run；P2 这里只处理经 Phase 0
盘点确认尚未生产启用的新渠道和高级跨渠道体验，不得把生产渠道基础可靠性后移。P2 可继续推进：

- 多端统一的等待、审批、reconciliation 和证据工作台；
- 移动端及经 Phase 0 盘点确认尚未生产启用的新渠道适配；新渠道必须只实现
  Connector + capability profile 并通过统一 Gateway contract tests；
- 更丰富的工具进度卡、Artifact 预览和运行时间线；
- 会话分支、协作观察和跨端接续；
- PlatformPorts 的新平台实现；
- 无障碍、国际化和弱网体验；
- Web Push、移动推送和通知偏好中心。

共享目标是协议解释和状态归并一致，不追求 Web 与 Desktop 页面组件数量最大化。

## 8. P2-07 企业治理、评估与运营

在 P0 已有的可信身份、计费关联和最小审计基础上，逐步建设：

- Policy Plane 的策略版本、模拟、解释和 kill switch；
- Evidence Ledger 的证据完整性、产物版本和补偿关系；
- Evaluation Runs、Golden Cases 和发布质量门禁；
- 模型、Prompt、Skill、Provider 与客户端版本的联合评估；
- 成本、质量、安全和采用效果的联合指标；
- `reconcile_required`、旧 fence 写入、结果待补传等专项告警；
- 渠道 drain 静默窗口、route epoch/claim、worker 积压、projector reset、回复预算与 unknown
  delivery 的治理面板和 SLA；
- 客户正文、截图、模型输入输出的分级、脱敏和保留策略；
- 审计导出、访问审批和合规删除。

建议重点指标包括队列等待、接纳到执行延迟、关键事件发布延迟、快照恢复率、幂等冲突、reconciliation 停留时长、旧代际拒绝量、Runtime 待回传量和未确认模型用量。

## 9. P2-08 架构治理与参考基线

外部参考只用于解释边界，不成为未经验证的实现事实。P2 维护：

- AID、ZCode、Codex 的固定 commit/版本基线；
- 代码观察、风险推断、设计决策三者的标签；
- ADR 状态、替代方案、后果、回滚和验收证据；
- 模块依赖规则与静态守卫；
- 静态阻断 `agent_run` 反向 import 具体渠道、具体渠道绕过 Channel Gateway、Desktop 打包
  Channel Connector，以及 Device Provider 绕过 Device API；
- 生成协议与消费者一致性检查；
- legacy 适配器的使用量、责任人和退出时间；
- 目录治理和 Provider 拆分的渐进计划。

不以文件变短或目录变整齐作为完成标准。验收看状态写入入口、依赖方向、副作用边界和运行事实是否真正唯一。

## 10. P2 实施顺序

| 顺序 | 主题 | 进入条件 |
| --- | --- | --- |
| 1 | 事件/检查点与 reconciliation 运营化 | P0 RunService 稳定并积累真实故障数据 |
| 2 | 企业治理与指标 | P0 的 Run、Invocation、ModelAttempt、notification 关联可靠 |
| 3 | Provider 独立发布 | Desktop P1 Runtime 接口稳定且出现多个真实 Provider |
| 4 | 多客户长期任务调度 | Task/Run 分离和至少一个设备写工具链路稳定 |
| 5 | browser/Execution Fabric 重构 | 有明确产品需求和恢复安全预算 |
| 6 | 复杂离线、常驻、跨设备 | 桌面正式使用后有可量化需求 |
| 7 | 多端高级体验 | 核心事实和通知契约稳定 |

P2 各专题独立立项，不要求按一份“大二期”整体交付。

## 11. 原 v2.0 全章节归属审计

| 原章节 | 主要归属 | 说明 |
| --- | --- | --- |
| 文档说明、v2.0 关键修订 | P0 / P1 | Web 已上线、桌面未发布是两份计划共同前提 |
| 1. 执行摘要 | P0 | 统一运行事实和状态所有权 |
| 2. 产品约束与改造边界 | P0；桌面细节归 P1 | 兼容范围由 P0 冻结 |
| 3. 向 ZCode/Codex 学习 | P2 | 研究依据，不阻塞实施 |
| 4. 现有基础与代码观察 | P0 / P1；参考基线归 P2 | 安全与共享状态进 P0，桌面依赖清点进 P1 |
| 5. 目标架构 | P0；桌面进程实现归 P1 | 云端应用服务和两类 API 由 P0 权威定义 |
| 6. 对象、状态和生命周期 | P0 | Task/Session/Run/Invocation 统一语义 |
| 7. Agent 应用服务 | P0 | 包含 Web 收敛和 ConversationRepository |
| 8. 桌面协议与工具路径 | P1 | 桌面直接使用统一协议，删除旧 D1 |
| 9. 请求共享状态与租户隔离 | P0 | 属安全和数据隔离门禁 |
| 10. 命令、事件、检查点与恢复 | 最小可靠链路 P0；完整能力 P2 | P0 保证不丢任务和不重复副作用，P2 做高级恢复 |
| 11. Runtime 与桌面进程边界 | P1；复杂常驻归 P2 | 首发边界与未来系统服务分离 |
| 12. 共享客户端、平台能力和文件 | P1；多端高级体验归 P2 | Artifact 与授权基础仍受 P0 约束 |
| 13. 授权、计费、审计、可观测性 | 一致性与安全 P0；运营深化 P2 | finalization 在 P0，评估和长期指标在 P2 |
| 14. 多客户长期会话验证 | P2 | 作为 Task/Run 和调度能力的规模化场景 |
| 15. 实施路线 | 拆入 P0 / P1 / P2 | 不再保留混合总路线 |
| 16. 桌面冻结和发布验收 | P1 | 由桌面专项规划管理 |
| 17. ADR 与最终建议 | 按决策主题拆分 | P0 决策优先，桌面为 P1，长期演进为 P2 |
| 18. 源码和文档参考 | P2 | 固定版本后作为评审证据，不直接当作当前事实 |

结论：原文主题已经全部分配。P0、P1、P2 可以互相引用，但不能各自复制并演化一套 Run 状态机。

## 12. 原参考清单的保留方式

后续正式评审仍需固定并复核以下来源：

- AID：依赖边界脚本、旧 Desktop Agent 协议、Gateway、Runtime session task engine、request context、Agent 核心收敛原则、Web 入口、Desktop turn、Agent router、Agent core、Coordinator/Local Tool Host 接口、Runtime 包配置及 Tool ExecutionTarget；
- ZCode：架构规则、remote service access、architecture policy；
- OpenAI Codex：core 定位与 App Server 的 Thread/Turn/Item、命令和事件模型。

P2 不沿用浮动默认分支作为长期证据。每次形成缺陷单或 ADR 前记录仓库、commit、文件位置、触发条件和实际测试结果；外部项目的声明性规则不能被当成 AID 已实现能力。

## 13. P2 非目标

- 不重新讨论 P0 已冻结的 Run 所有权；
- 不为了“平台化”引入 Kafka、通用 BPM 或万能插件系统；
- 不把桌面、CLI、Runtime 或 browser 提升为平行业务调度中心；
- 不把 Channel Connector 提升为业务调度中心，也不把 Device Provider 与 Channel Connector
  合并成万能插件接口；
- 不为没有产品需求的能力提前建设复杂兼容层；
- 不通过大规模目录移动冒充架构完成；
- 不将所有 P2 专题捆绑为一次发布。
