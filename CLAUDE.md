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
| [.claude/rules/dev_workflow.md](.claude/rules/dev_workflow.md) | **开发流程规范**：三智能体开发流程（开发→测试→CodeReview），非平凡任务必读 |
| [.claude/rules/billing_audit.md](.claude/rules/billing_audit.md) | **计费审计规范**：LLM/Embedding/ASR/视频调用点全量扫描 + 已计费五条件核对 + 工具入口 vs 渠道入口分离 + 同步/异步核对。新增计费调用点、修改计费函数、定期审计必读 |
| [.claude/rules/powershell.md](.claude/rules/powershell.md) | **PowerShell 脚本规范**：UTF-8 BOM、字符串插值用 `-f`、HTTP 用 curl.exe、外部 API 重试、写完强制 ParseFile 检查 |
| [.claude/rules/docs_style.md](.claude/rules/docs_style.md) | **文档写作规范**：箭头统一用 `->`（U+2192）、破折号统一用 `--`；编辑文档前先探测实际字符，避免视觉相同字符导致 Edit 失败 |
| [.claude/rules/knowledge_retrieval.md](.claude/rules/knowledge_retrieval.md) | **知识库检索规范**：所有搜索/读取知识库的工具、技能、独立 API 必须支持跨租户共享搜索（`load_shared_ranges` 三种模式 A/B/C），新增检索入口必读 |

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

