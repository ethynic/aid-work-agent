# Agent Coordinator Core

> **Frozen（2026-09-22）**：新架构不允许 Desktop 拥有本地业务 Coordinator/第二套 Run 状态机。本包只作为 DC0 依赖审计输入；纯 reducer/端口可迁入新的 AgentClient/RunProjection 边界，其余删除。禁止继续实现旧 `agent/next`、outcome、审批或回合推进。架构见 `docs/system/agent-application-architecture-design.md`（原桌面 P1 计划已于 2026-10-01 删除）。

本目录曾用于无 UI、无 Electron 的 Desktop 回合协调核心边界。当前只能用于识别依赖和迁出
仍有价值的纯 reducer/端口，不能成为新 Desktop 的运行依赖。

允许依赖协议生成类型和 `local-tool-host-core` 的端口；禁止依赖 renderer、Electron 与平台凭证实现。
