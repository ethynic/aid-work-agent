# Web Agent 回复分享开发计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| 需求与设计 | 完整回复独立展示，可选密码，复用现有渲染 | ✅ 完成（2026-10-22） | 已按用户澄清恢复简化设计，图片及附件为必需 |
| Phase 1 | 分享记录、读取、图片与文件资源接口 | ✅ 完成（2026-10-22） | 后端35项、隔离PostgreSQL 4项通过，支持纯附件与附件内图片 |
| Phase 2 | 共享回复组件、分享弹框和独立页面 | ✅ 完成（2026-10-22） | 相关组件54项通过，Web/Desktop构建通过，浏览器媒体及窄屏验收通过 |
| Phase 3 | 独立测试、CodeReview及交付 | ✅ 完成（2026-10-22） | 独立测试与CR通过，入口隔离导入通过；交付后用户授权提交，未部署 |

> 设计：[Web Agent 单条回复分享](../system/web-agent-reply-sharing-design.md)

## 范围与步骤

1. 新增分享系统表与 DAL，双轨 DDL 同步到 init-postgres.sql 与 db_update.yaml，并登记系统表说明。创建时只从可信源消息取数据，支持真实消息 ID 与 Web 最终回复投影 ID。
2. 实现可选密码读取与分享资源入口，完整保留正文、图片、内嵌图片、附件、工具产生的可见下载卡片；复用现有文件解析及签名，不建文件副本仓库或访客系统。
3. 抽取 ReplyContent，聊天与分享共用 Markdown、图集、附件及预览。新增复制链接弹框和 Web 独立路由，保持现有聊天与桌面能力。
4. 自测后安排独立测试与独立 CR，主控整合并核验最终代码。按[开发流程](../../.claude/rules/dev_workflow.md)与[测试规范](../../.claude/rules/testing.md)执行。

## 必测场景

| 场景 | 目标 |
|------|------|
| 文字/表格/代码/外链 | 与原回复展示一致，只显示选中回复 |
| 多图、内嵌图片、纯图片回复 | 位置正确，可放大及下载 |
| PDF、Office、HTML、Markdown与普通附件 | 沿用既有预览，下载正常，纯附件可分享 |
| 免密/密码访问 | 未登录可读；密码覆盖正文及资源 |
| 文件范围与归属 | 不越过选中回复的资源集合，不继承访客原登录租户身份 |
| 错误与回归 | 复制失败保留链接、文件缺失明示、窄屏正常、原聊天不退化 |

## 验证记录

- 开发前 Web build 通过；5 组组件基线 59 通过、7 失败，失败为已有 Office Excel/PPT 夹具在 jsdom 中超时。本轮没有将其计为通过，也未修改无关 Office 解析逻辑；实际浏览器中的 Office 预览另行验收通过。

| 验证 | 命令/范围 | 实际结果 |
|------|-----------|---------|
| 后端单元/API集成 | `scripts/dev_test.sh tests/unit/services/test_message_sharing.py tests/integration/test_message_share_api.py -p no:cacheprovider -q` | 独立测试35通过，无失败/跳过 |
| PostgreSQL持久化与迁移 | `scripts/dev_test.sh --isolated-db tests/integration/agent_runner_service/test_message_share_persistence.py -p no:cacheprovider -q` | 独立测试4通过，专属测试库自动销毁 |
| 前端相关回归 | Vitest：ReplyContent.sharing、MessageShareDialog、SharedReplyPage、MessageItem.sharing、MessageItem.verbose、MessageItem.runnerInterruption、DownloadFileCard、ImageGallery | 共54通过，含纯附件最终投影ID、密码门禁与匿名读取 |
| 两端构建 | `cd frontend && npm run build`、`npm run build:desktop` | 类型、依赖边界、生产构建及产物检查均通过；有既有大包体积提示 |
| 完整API入口加载 | 专属隔离PG环境导入 `src.main.app`，核对3条分享路由，不启动lifespan | 1通过；不启动调度器 |
| 浏览器 | 本机Vite + 隔离FastAPI夹具，使用真实分享路由/渲染/文件读取 | 免密、密码错误/正确、图片加载、Word排版、Excel切表、PPT翻页、PDF、HTML脚本、Markdown内嵌图与ZIP原始字节下载均通过 |
| 窄屏 | 375px与320px | 修复共用图集单图宽度溢出后，完整缩放，无横向溢出 |

独立测试还以临时探针核验真实 Runner 仅落 user 历史的纯附件回复、最终 result 优先于旧流式内容、归属隔离、HTML→Markdown/CSS→图片、缺失资源404、外租户拒绝、编码/实体/大小写与外链保留。独立CR报告的图片顺序、资源传递依赖、HTML/SVG隔离、旧NULL归属、外链按位置改写及无效URL容错均已修复并复核，最终无剩余阻塞问题。

开发完成；索引移动至 `docs/ideas_finished.md`。没有执行共享业务库迁移或服务器部署。上线时由用户授权后按项目既有数据库更新与发布方式执行，无新增管理流程。

2026-10-22 提交前同步：用户明确授权提交；已快进整合 `origin/master` 的私有云计费更新 `768db103`，保留其他任务的工作区修改。整合后重新运行后端35项、PostgreSQL 4项及完整入口导入1项，共40项通过；前端8组54项通过，Web/Desktop构建及产物检查均通过。仅提交本功能相关文件与索引行，不包含其他任务改动。
