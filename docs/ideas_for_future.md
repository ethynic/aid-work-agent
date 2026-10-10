# 未来架构与产品设想索引

> 本文件收录尚未进入当前开发主线的中长期调研、架构设计与开发计划，避免 `ideas.md` 因远期规划持续膨胀。
>
> 更新日期：2026-10-10。进入实际开发时，再将对应项目迁入 `ideas.md`；开发完成后按规范归档到 `ideas_finished.md`。本索引不保留已撤销架构的开工方案。
>
> 2026-10-10 清理：移除「Desktop 本地/服务端工具分工调研」与「第一方 CLI / MCP Provider 跨 Host 规范」两行——前者的架构结论（云端 Run → Agent API/Device API）已由 AgentRunner 落地，剩余双文件执行器由桌面 v3 设计与 Phase 2/3 计划吸收（调研文档改在 research_index.md 登记）；后者已进入开发主线（BOSS/weixin-cli 两个实现，Runtime A1～A3 交付），由 ideas.md `20261008-runtime-plugin-host` 统一计划跟踪，规范文档保留为现行标准。

## 企业能力与团队协作长期设想

策略治理、业务证据、企业记忆、发布评测和团队协作仍可作为产品方向，但旧平台及协作蓝图已删除，不是已确认设计或待实施项目。各方向只有在真实客户问题、验收指标和产品结果明确后才能独立设计；不设置统一任务模型、第二套 Coordinator 或执行网络作为共同前置。

后续方案复用[现行 AgentRunner 架构](system/agent-application-architecture-design.md)。Agent 重构已完成；设备执行已授权工具，原渠道只接入对话服务。旧桌面所有权、状态机、字段和协议不构成兼容要求。

## 产品战略建议

| 主题 | 状态 | 文档 |
|---|---|---|
| AID Work Agent 跻身顶级企业 Agent 产品的真诚建议 | 📋 战略建议完成 | [真诚建议](research/aid-work-agent-honest-advice.md) |

重点包括：灯塔工作流、企业业务语义层、Solution Pack、组织经验飞轮、价值实现、决策与异常中心、Prompt 规则结构化、工程交付与企业智能体团队（2026-10-10 清理：删除已实现的 Kernel 拆分建议、无法落地的流程发现 Shadow 模式、过期 90 天路线与纯叙事章节，详见文档头部清理说明）。
