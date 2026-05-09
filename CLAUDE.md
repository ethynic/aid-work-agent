## 项目概览

本项目是企业员工智能代理系统，通过对话式 AI 处理企业员工的日常任务。系统使用国内大模型提供商（Qwen 和 ZhipuAI），支持多渠道接入（企业微信、钉钉、飞书）。系统支持多租户，可承载数百并发用户。

**设计原则**：稳定性和可预测性优先于创造力。专业、简洁的回复。不确定时诚实承认。不幽默、不娱乐、不学术猜测。

**安全原则**：敏感信息必须加密，不以明文形式返回给用户。

## 开发规范

详细规范见 [.claude/rules/](.claude/rules/) 目录：

| 文档 | 内容 |
|------|------|
| [.claude/rules/backend_dev.md](.claude/rules/backend_dev.md) | 后端开发规范，日志、错误处理、异步/Gunicorn、API 命名规范 |
| [.claude/rules/frontend_dev.md](.claude/rules/frontend_dev.md) | 前端开发规范 |
| [.claude/rules/testing.md](.claude/rules/testing.md) | 测试目录结构、分层规则、Fixtures、运行命令 |
| [.claude/rules/architecture.md](.claude/rules/architecture.md) | 系统架构、核心组件、扩展点 |
| [.claude/rules/database_dev.md](.claude/rules/database_dev.md) | 数据库表开发规范，包括表分类、租户隔离要求、变更记录 |

**回答简洁**：每个响应不超过 5000 个 token
**代码输出**：只输出修改的部分，不要输出完整文件
**拒绝废话**：不要输出"让我们一步步分析"等开场白，直接给出解决方案
**不要复述问题**：直接回答，不要重复用户的问题
**避免长输出**：一次性输出 32000 个 token，会触发"output token maximum"错误

## Git 提交规范
**不要自动提交代码，仅当用户明确说“提交代码”才提交**
1. 提交前执行 `git fetch` 拉取远程最新代码
2. 检查是否有冲突，如有冲突先解决冲突再提交
3. 提交后立即 `git push` 推送到远程

## OpenSpec规范
本项目开始逐步采用 OpenSpec 进行规格驱动开发。详细指令见 `openspec/AGENTS.md`。
