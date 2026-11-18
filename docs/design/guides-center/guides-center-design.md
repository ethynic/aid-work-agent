# 教程文档中心设计（docs/guides + Portal「使用教程」）

## 1. 背景与目标

系统使用教程/手册/指南类文档（部署运维、渠道接入、客户端安装、开发环境工具，约 30 篇）此前散布在 `deploy/`、`docs/ops/`、`docs/channel/*/`、`docs/tools/`、`docs/infrastructure/`、`docs/system/`、`clients/release/` 等 8+ 个目录，无统一入口。本设计将教程类文档**物理迁移**到 `docs/guides/` 四个分类子目录集中管理，并在 Portal 平台管理后台新增「使用教程」页面：自动解析文档目录（左侧文档目录树 + 右侧文档内标题 TOC），复用现有 markdown 渲染栈漂亮渲染。

非目标（v1 不做）：全文搜索、文档内图片服务（现有教程均无相对图片引用）、历史版本、租户侧入口。

## 2. 文档集中管理

### 2.1 分类结构

```
docs/guides/
├── manifest.yaml      # 分类显示名与排序（后端读取）
├── README.md          # 收录规范（什么文档进 guides、约定）
├── deployment/        # 部署与运维（deploy/ 通用指南 + docs/ops/ + 租户迁移手册）
├── channels/          # 渠道接入（钉钉/飞书/企微/企微客服手册）
├── clients/           # 客户端安装与使用（安装手册、打包手册）
├── development/       # 开发环境与工具（容器化环境、MCP、Skill 指南、工具规范）
└── operations/        # 管理操作（portal/租户管理员操作教程，含文档内截图 images/）
```

收录规范见 `docs/guides/README.md`：放入对应子目录即出现在文档中心，无需注册；标题取正文首个一级标题。

**不迁移**：环境实例档案（`docs/deploy/生产环境部署.md`、济南诺度系列、`服务器部署现状.md`、`生产发布备忘.md`、`deploy-eas.md` 内含凭据）、`docs/deploy/redis|image-gen/README.md`（与同目录 compose 强耦合）、设计/计划/调研/复盘类文档。

### 2.2 引用更新

迁移后全仓更新引用（`*.md/*.py/*.yml/*.sh/*.ps1`）：

- 仓库根风格引用（AGENTS.md、CLAUDE.md、README.md、规则文件、compose 注释、打包脚本注释、代码内文档路径）
- 留守文档 → 迁移文档的相对链接（`docs/deploy/服务器部署现状.md` 19 处等）
- 迁移文档 → 留守文件的相对链接（按旧目录解析后改写为新相对路径）
- 运行时依赖：`tests/unit/weixin_marketing/test_manual_sql_safety.py` 按 Path 读取 rollout 手册，同步改路径
- 历史记录文档（`docs/plans/plan-docs-cleanup-20261012.md`）保留旧路径叙述不改

## 3. 后端 API（src/api/guides.py）

| 端点 | 说明 |
|------|------|
| `GET /api/guides/catalog` | 扫描 `docs/guides/**.md`，按 manifest.yaml 分组；标题取首个 `# `；返回 path（相对 guides 根）/updated_at/size |
| `GET /api/guides/doc?path=` | 返回单篇 title/content/updated_at |

- 鉴权：`get_current_user` → 401；`is_platform_admin` → 403（复用 `src/api/admin_reports.py` 模式）
- 路径校验：resolve 后必须仍在 guides 根内、后缀 `.md`、拒绝反斜杠，否则 404
- 不落库、无新增 settings；guides 根按仓库根定位（与 settings.py 解析 config.yaml 同款方式）

### 3.1 Docker 内容源

`.dockerignore` 排除了 `docs/` 与 `*.md`，生产容器原本读不到文档。在文件末尾追加 `!docs/guides` 与 `!docs/guides/**`（.dockerignore 按最后匹配生效，白名单必须在所有排除规则之后）。

## 4. 前端页面（components/saas/GuidesCenter.vue）

三栏布局（跟随 portal 页面既有约定：AppHeader + Base* 组件 + 语义 token）：

- **左**：分类文档树（manifest 分组，当前文档高亮）；窄屏（<lg）折叠为顶部下拉
- **中**：文档标题/更新时间 + `MarkdownPreview`（`fontSize="base"`，复用 `utils/markdown.ts` 的 marked+hljs+DOMPurify）
- **右**：自动 TOC——渲染后从 `.markdown-body` 提取 h2/h3/h4 注入锚点 id，树形缩进 + 点击平滑滚动 + scrollspy 高亮；窄屏（<xl）通过右下角「目录」按钮开抽屉

文档内 `.md` 相对链接做拦截，按当前文档路径解析后在 viewer 内路由跳转；`#anchor` 滚动到对应标题；外链保持新标签打开。

路由：`portalRoutes.ts` 挂 `{ path: 'guides/:doc_path(.*)?', name: 'portal-guides' }` 支持深链；`PortalLayout.vue` menuItems 增加「使用教程」。

## 5. 测试

- 后端 `tests/unit/test_guides_api.py`（无 DB 依赖，放 unit 目录避开 integration conftest 的 PG autouse 跳过）：401/403 鉴权、catalog 结构与真实迁移文档、doc 读取、9 种非法路径（穿越/反斜杠/非 md/不存在/绝对路径）404
- 前端 `web/__tests__/components/saas/GuidesCenter.test.ts`：目录分组渲染、默认文档加载、TOC 提取与锚点 id、侧栏路由跳转、`.md` 链接拦截、加载失败提示
- `routes.test.ts` 内联快照同步（含此前提交时漏更的 system-health/dashboard/runner-alerts 补齐）

## 6. 已知边界与后续候选

- 文档内相对图片不可显示（当前教程无此需求；需要时后端加受控静态端点）
- 全文搜索、移动端目录常驻、目录排序自定义 → v2 候选
- 本轮验证在宿主机完成（当前仓库无可挂载的运行容器）；portal 登录后的真实页面冒烟留待有容器/联调环境时补充
