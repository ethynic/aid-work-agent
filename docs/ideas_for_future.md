# 未来架构与产品设想索引

> 本文件收录尚未进入当前开发主线的中长期调研、架构设计与开发计划，避免 `ideas.md` 因远期规划持续膨胀。
>
> 当前日期：2026-08-28。进入实际开发时，再将对应项目迁入 `ideas.md`；开发完成后按规范归档到 `ideas_finished.md`。

## Agent Desktop 下一代架构

| 主题 | 状态 | 文档 |
|---|---|---|
| Windows/macOS 独立 Desktop UI 与 Local Agent Coordinator | 📋 架构完成，Phase A 目录迁移已完成 | [客户端设计](system/desktop-agent-client-design.md) / [开发计划](system/desktop-agent-client-dev-plan.md) |
| Desktop 本地工具、服务端工具与远端 Runtime 分工 | 📋 调研完成 | [调研](research/desktop-agent-local-vs-server-tool-execution-research.md) |
| 第一方 CLI / MCP Provider 跨 Host 规范 | 📋 规范完成 | [架构规范](system/first-party-cli-mcp-provider-standard.md) |

## 企业 Agent 平台长期设想

> 2026-08-28：Task Plane 及统一 Task 模型已停止并作废。不得按原顺序继续开发；详见[暂停与处置报告](research/task-plane-suspension-and-disposition-report.md)。其余文档中的 Task 前提均不再有效，后续只能按各自已经明确的事实对象独立复核。

| 能力面 | 状态 | 文档 |
|---|---|---|
| **Task Plane Phase 0+1** | ⛔ 已停止，设计作废，不合并不部署 | [暂停与处置报告](research/task-plane-suspension-and-disposition-report.md) |
| 原七能力面总体集成架构 | ⚠️ 待复核，Task 主线已删除 | [总体架构](system/enterprise-agent-platform/enterprise-agent-platform-integration-design.md) |
| 企业 Policy Engine | ⚠️ 待独立复核，不得依赖 Task 前置 | [Policy Engine](system/enterprise-agent-platform/enterprise-policy-engine-design.md) |
| Server / Desktop / Runtime 执行网络 | ⚠️ 待独立复核，不得依赖 Task 前置 | [Execution Fabric](system/enterprise-agent-platform/enterprise-execution-fabric-design.md) |
| 企业动作与业务结果证据 | ⚠️ 待复核，关联模型与实施阶段暂停 | [Evidence Ledger](system/enterprise-agent-platform/enterprise-evidence-ledger-design.md) |
| 企业记忆与知识治理 | ⚠️ 待复核，跨模块关联暂停 | [Memory & Knowledge](system/enterprise-agent-platform/enterprise-memory-knowledge-design.md) |
| Agent 发布评测与运营 | ⚠️ 待复核，统一运行关联暂停 | [Evaluation & Operations](system/enterprise-agent-platform/enterprise-agent-evaluation-operations-design.md) |

原“Contract → Task/Release/Policy → Collaboration …”顺序失效。各能力必须按真实业务场景独立验证边界；不得把 Task 当作已解决前置。服务器继续权威管理租户、账号、计费、LLM 和企业策略；`DEVICE_OWNED` 会话的原设备、Coordinator 与 workspace 不因跨端接续而变化。

## 企业多智能体协作体系

| 主题 | 状态 | 文档 |
|---|---|---|
| 当前子智能体实现与 Codex/WorkBuddy 差距 | 📋 调研完成 | [调研与现状审计](research/enterprise-multi-agent-collaboration-research.md) |
| Web/Desktop 共用 Multi-Agent Collaboration Fabric | ⛔ 暂停，禁止按旧设计开工 | [暂停说明](system/enterprise-agent-platform/enterprise-multi-agent-collaboration-design.md) |
| 持久 Coordinator、并行协作、团队模板与双端 UI | ⛔ 原计划撤销 | [暂停计划](plans/plan-enterprise-multi-agent-collaboration.md) |

协作方向暂不定案。只有协作自身权威对象、生命周期、权限与真实客户场景独立重审后，才能重新立项；旧字段、状态机和协议不构成兼容要求。

## 产品战略建议

| 主题 | 状态 | 文档 |
|---|---|---|
| AID Work Agent 跻身顶级企业 Agent 产品的真诚建议 | 📋 战略建议完成 | [真诚建议](research/aid-work-agent-honest-advice.md) |

重点包括：灯塔工作流、企业业务语义层、流程发现、Solution Pack、组织经验飞轮、价值实现、Agent Kernel、工程交付与企业智能体团队。
