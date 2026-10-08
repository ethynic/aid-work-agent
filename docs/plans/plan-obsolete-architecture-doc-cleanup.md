# 过时架构文档清理计划与记录

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 文档和引用盘点 | ✅ 完成（2026-10-08） | 检索 521 个 Markdown 文件；核对现行 AgentRunner、兼容壳、渠道及桌面职责 |
| Phase 2 | 删除失效方案、清理引用并修订有效文档 | ✅ 完成（2026-10-08） | 删除 9 份方案及入口引用；修复 62 个移动/相对路径链接，移除 19 个缺失或歧义链接 |
| Phase 3 | 引用与差异验证、登记 | ✅ 完成（2026-10-08） | 538 个 Markdown（含隐藏规则）检查通过；被删文件仅在本清单留名；相对 .md 目标缺失 0、修改文件代码块异常 0；差异检查通过 |

## 清理依据

现行执行架构以 [AgentRunner 架构](../system/agent-application-architecture-design.md) 为准：Agent 重构已完成，Web/KF 对话使用独立 Runner，未接入渠道仅替换对话调用，保留原业务。桌面和 Runtime 的当前方案由各自现行文档维护，不恢复旧桌面 Coordinator 或第二套业务执行权威。

删除的是整体建立在已撤销架构前提上的方案和实施步骤。部分内容仍有效的运行说明、安全审计和实施证据保留并纠正边界；不会因文件较早、出现历史模块名或提到已退役数据库表就删除。

## 删除清单

以下只记录被删文件名，不提供可跟随的旧文档链接；原文可从 Git 历史追溯，不作为当前实施要求。

| 位置 | 文件 | 删除原因 |
|------|------|----------|
| 旧平台设计目录 | `enterprise-agent-platform-integration-design.md` | 总体设计仍要求 Desktop Local Coordinator 与 DEVICE_OWNED |
| 旧平台设计目录 | `enterprise-execution-fabric-design.md` | 桌面业务执行和本地直达工具的所有权模型已撤销 |
| 旧平台设计目录 | `enterprise-policy-engine-design.md` | 依赖旧 Coordinator、Remote Tool Gateway 及未成立的平台契约 |
| 旧平台设计目录 | `enterprise-evidence-ledger-design.md` | 旧执行链上的证据和审批实施协议不属于现行运行架构 |
| 旧平台设计目录 | `enterprise-memory-knowledge-design.md` | DEVICE_OWNED/本地完整会话权威及旧仓储前提已失效 |
| 旧平台设计目录 | `enterprise-agent-evaluation-operations-design.md` | 桌面 release 与执行权威模型已失效，不能直接按旧路线实施 |
| 旧平台设计目录 | `enterprise-multi-agent-collaboration-design.md` | 依赖桌面 Coordinator 和第二套协作执行体系 |
| 计划目录 | `plan-enterprise-multi-agent-collaboration.md` | 已明确撤销，仍保留 Phase 0–6 详细开工步骤 |
| 调研目录 | `enterprise-multi-agent-collaboration-research.md` | 旧 Agent/子智能体现状及直接导向撤销协作方案的结论已失效 |

旧平台设计目录为 `docs/system/enterprise-agent-platform/`；其中独立工具副作用审计保留，不递归删除目录。

## 引用及有效内容处理

- 从未来事项索引删除旧方案链接，保留产品方向的简短描述，不留下旧字段、状态机和协议作为实施前置。
- 战略研究保留产品判断；移除失效方案链接，修正旧桌面执行所有权，并明确旧 Agent 行数和 Kernel 建议的历史范围。
- 更新开发架构规则中的执行调用链，明确独立 Runner、AgentEngine、兼容壳及原渠道职责。
- 浏览器设计和计划仍包含可用的领域、人工协助及 legacy 兼容资料，保留并标明 Runner 与桌面现行边界。
- 保留安全审计、事故/验收记录、历史 DDL、已完成重构记录及本轮其他任务正在修改的桌面/Runtime 文档。
- 扩展检查包含隐藏开发规则；对明显错层级或已移动且目标明确的 `.md` 链接修正相对路径，不将钉钉手册错误指向飞书同名手册。缺失或歧义目标移除链接并注明旧文档已移除；真正归档的领域资料标明历史范围，正文代码示例中的 file_id、url 等占位符不按文件处理。

## 验证边界

本任务只修改文档及开发规则，没有运行产品测试、迁移、发布或提交。验证检查被删目标存在性及所有文本引用、新增链接/锚点、Markdown 代码块、索引登记和 `git diff --check`；不把这次检查称为全仓库既有链接全部有效。
