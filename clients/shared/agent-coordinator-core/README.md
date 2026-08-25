# Agent Coordinator Core

无 UI、无 Electron 的 Desktop 回合协调核心边界。Phase B 只定义依赖端口，不实现回合循环、审批、取消或持久化。

允许依赖协议生成类型和 `local-tool-host-core` 的端口；禁止依赖 renderer、Electron 与平台凭证实现。
