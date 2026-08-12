# 未来架构与产品设想索引

> 本文件收录尚未进入当前开发主线的中长期调研、架构设计与开发计划，避免 `ideas.md` 因远期规划持续膨胀。
>
> 当前日期：2026-08-12。进入实际开发时，再将对应项目迁入 `ideas.md`；开发完成后按规范归档到 `ideas_finished.md`。

## Agent Desktop 下一代架构

| 主题 | 状态 | 文档 |
|---|---|---|
| Windows/macOS 独立 Desktop UI 与 Local Agent Coordinator | 📋 架构完成，Phase A 目录迁移已完成 | [客户端设计](system/desktop-agent-client-design.md) / [开发计划](system/desktop-agent-client-dev-plan.md) |
| Desktop 本地工具、服务端工具与远端 Runtime 分工 | 📋 调研完成 | [调研](research/desktop-agent-local-vs-server-tool-execution-research.md) |
| 第一方 CLI / MCP Provider 跨 Host 规范 | 📋 规范完成 | [架构规范](system/first-party-cli-mcp-provider-standard.md) |

## 企业 Agent 任务执行操作系统

| 能力面 | 状态 | 文档 |
|---|---|---|
| 七个能力面总体集成架构 | 📋 架构完成 | [总体架构](system/enterprise-agent-platform/enterprise-agent-platform-integration-design.md) |
| Task / Session / Execution / Artifact 统一任务模型 | 📋 设计完成 | [Task 模型](system/enterprise-agent-platform/enterprise-task-model-design.md) |
| 企业 Policy Engine | 📋 设计完成 | [Policy Engine](system/enterprise-agent-platform/enterprise-policy-engine-design.md) |
| Server / Desktop / Runtime 执行网络 | 📋 设计完成 | [Execution Fabric](system/enterprise-agent-platform/enterprise-execution-fabric-design.md) |
| 企业动作与业务结果证据 | 📋 设计完成 | [Evidence Ledger](system/enterprise-agent-platform/enterprise-evidence-ledger-design.md) |
| 企业记忆与知识治理 | 📋 设计完成 | [Memory & Knowledge](system/enterprise-agent-platform/enterprise-memory-knowledge-design.md) |
| Agent 发布评测与运营 | 📋 设计完成 | [Evaluation & Operations](system/enterprise-agent-platform/enterprise-agent-evaluation-operations-design.md) |

建议总体顺序：Contract → Task/Release/Policy → Collaboration → Evidence/Fabric → Memory → Evaluation → 行业生态。服务器继续权威管理租户、账号、计费、LLM、Agent Release 与企业策略；`DEVICE_OWNED` 会话的原设备、Coordinator 与 workspace 永不因跨端接续而变化。

## 企业多智能体协作体系

| 主题 | 状态 | 文档 |
|---|---|---|
| 当前子智能体实现与 Codex/WorkBuddy 差距 | 📋 调研完成 | [调研与现状审计](research/enterprise-multi-agent-collaboration-research.md) |
| Web/Desktop 共用 Multi-Agent Collaboration Fabric | 📋 架构完成 | [架构设计](system/enterprise-agent-platform/enterprise-multi-agent-collaboration-design.md) |
| 持久 Coordinator、并行协作、团队模板与双端 UI | 📋 待开发 | [开发计划](plans/plan-enterprise-multi-agent-collaboration.md) |

核心方向：用持久 root/child 责任树与 assignment DAG 取代“同步调用单个专家”，提供独立上下文、持久 mailbox、协作原语、预算、lease/fencing、证据化综合与用户干预；现有 `delegate_to_subagent` 仅作为兼容入口。

## 产品战略建议

| 主题 | 状态 | 文档 |
|---|---|---|
| AID Work Agent 跻身顶级企业 Agent 产品的真诚建议 | 📋 战略建议完成 | [真诚建议](research/aid-work-agent-honest-advice.md) |

重点包括：灯塔工作流、企业业务语义层、流程发现、Solution Pack、组织经验飞轮、价值实现、Agent Kernel、工程交付与企业智能体团队。
