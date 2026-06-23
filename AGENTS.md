## 项目概览

本项目是企业员工智能代理系统，通过对话式 AI 处理企业员工的日常任务。系统使用国内大模型提供商（Qwen 和 ZhipuAI），支持多渠道接入（企业微信、钉钉、飞书）。系统支持多租户，可承载数百并发用户。

**设计原则**：稳定性和可预测性优先于创造力。专业、简洁的回复。不确定时诚实承认。不幽默、不娱乐、不学术猜测。

**安全原则**：敏感信息必须加密，不以明文形式返回给用户。

## 开发规范

详细规范见 [.Codex/rules/](.Codex/rules/) 目录：

| 文档 | 内容 |
|------|------|
| [.Codex/rules/backend_dev.md](.Codex/rules/backend_dev.md) | 后端开发规范，日志、错误处理、异步/Gunicorn、API 命名规范 |
| [.Codex/rules/frontend_dev.md](.Codex/rules/frontend_dev.md) | 前端开发规范 |
| [.Codex/rules/testing.md](.Codex/rules/testing.md) | 测试目录结构、分层规则、Fixtures、运行命令 |
| [.Codex/rules/architecture.md](.Codex/rules/architecture.md) | 系统架构、核心组件、扩展点 |
| [.Codex/rules/database_dev.md](.Codex/rules/database_dev.md) | 数据库表开发规范，包括表分类、租户隔离要求、变更记录 |

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

## 文档登记规范

所有调研、设计、开发计划文档都必须在 `docs/ideas.md` 中登记，包括：
- 新增调研报告 → 登记到「调研报告索引」或对应功能分区
- 新增设计文档 → 在对应分区添加条目，关联设计文档链接
- 新增开发计划 → 在对应条目补充开发计划链接

**不得遗漏登记**，确保 `docs/ideas.md` 始终是项目所有文档的完整索引。

### 开发状态更新规范

- **开发完成时**：将条目从 `docs/ideas.md` 移动到 `docs/ideas_finished.md`，状态标记为 ✅ 已完成开发
- **部分完成时**：在 `docs/ideas.md` 中更新状态为 🔧 部分完成，并在说明中补充完成进度
- **开始开发时**：在 `docs/ideas.md` 中更新状态为 🔧 部分完成
- **新增开发内容时**：在 `docs/ideas.md` 对应分区添加新条目

## OpenSpec规范
本项目开始逐步采用 OpenSpec 进行规格驱动开发。详细指令见 `openspec/AGENTS.md`。
