"""BOSS 直聘会话场景包（boss.chat_reply.v1）。

B1.3 交付**注册骨架**（默认关闭）：常量、配置门控、可注册的最小场景描述器
（spec 校验/决策钩子/适配器/绑定查询面均为 fail-closed 占位）与受信注册点。
完整场景逻辑（BossTaskSpecPayload 分派、受限决策钩子、确定性渲染、频控双闸门、
候选人绑定与话术版本表、绑定管理 API、结算与投影）随 B2 交付（设计 §5，
计划 §6/§8）；Runtime/Provider 接线随 B3。

骨架 fail-closed 策略：boss_conversation.enabled 默认 false（配置缺失/损坏同
样按关闭处理）——组合根 ensure_registered 不会注册本场景描述器；即使配置误开，
spec_validator 占位拒绝一切任务创建，场景运行时成员不会被触达。
"""
