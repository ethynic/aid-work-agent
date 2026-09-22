# Agent Coordinator Core

> **Frozen（2026-09-22）**：统一 Agent Run 架构不再允许 Desktop 拥有本地业务 Coordinator/第二套 Run 状态机。本包只作为 DC0 依赖审计输入；纯 reducer/端口可迁入新的 AgentClient/RunProjection 边界，其余删除。禁止继续实现旧 `agent/next`、outcome、审批或回合推进。见 `docs/plans/plan-desktop-client-p1-implementation.md`。

本目录曾用于无 UI、无 Electron 的 Desktop 回合协调核心边界。当前只能用于识别依赖和迁出
仍有价值的纯 reducer/端口，不能成为新 Desktop 的运行依赖。

允许依赖协议生成类型和 `local-tool-host-core` 的端口；禁止依赖 renderer、Electron 与平台凭证实现。
