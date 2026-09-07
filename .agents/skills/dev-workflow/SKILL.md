---
name: dev-workflow
description: 对本项目非平凡代码开发按风险安排开发、独立测试和 CodeReview；用于新功能、bug 修复、重构或用户明确要求开发流程。纯文档、typo 和不改变业务行为的局部样式修改不触发完整流程。
metadata:
  author: aid-work-agent
  version: "2.0.0"
---

# 项目开发流程

先读取 [开发流程规范](../../../.claude/rules/dev_workflow.md)，按行为影响和失败后果选择验证级别，再执行对应流程。

该文档是风险分级、角色分工、测试范围、并行条件和结果复用的唯一详细来源，不在此复制步骤。主控者可承担开发角色；用户明确要求的流程优先。

提交授权及分支操作遵循 [AGENTS.md](../../../AGENTS.md#git-提交规范)。使用本 skill 不代表获得提交或推送授权。
