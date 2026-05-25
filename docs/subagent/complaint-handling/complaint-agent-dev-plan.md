# AI投诉处理专员 — 开发计划

> 创建: 2026-05-25 | 基于 v1.0 设计文档

## Phase 1: 基础设施 ✅

| 任务 | 状态 | 文件 | 说明 |
|------|------|------|------|
| 1.1 通用情绪分析服务 | ✅ 已完成 | `src/services/sentiment_service.py` | SentimentService + SentimentResult |
| 1.2 通用分类服务 | ✅ 已完成 | `src/services/classification_service.py` | ClassificationService + ClassificationResult |
| 1.3 通用案例匹配服务 | ✅ 已完成 | `src/services/case_matching_service.py` | CaseMatchingService (MVP: LLM方案) |
| 1.4 通用通知服务 | ✅ 已完成 | `src/services/notification_service.py` | NotificationService + email渠道 |
| 1.5 Services模块更新 | ✅ 已完成 | `src/services/__init__.py` | 导出所有新服务 |
| 1.6 配置文件更新 | ✅ 已完成 | `configs/config.yaml` | 新增 notification 配置节 |

## Phase 2: 投诉智能体核心 ✅

| 任务 | 状态 | 文件 | 说明 |
|------|------|------|------|
| 2.1 投诉主表 | ✅ 已完成 | `deploy/db_update.sql` | 4张表 + init_tables() |
| 2.2 complaint-core SKILL.md | ✅ 已完成 | `src/skills/complaint-core-1.0.0/SKILL.md` | 技能定义 |
| 2.3 complaint-core CLI工具 | ✅ 已完成 | `src/skills/complaint-core-1.0.0/scripts/complaint_tool.py` | 11个CLI命令 |
| 2.4 SUBAGENT.md | ✅ 已完成 | `subagents/complaint-handling/SUBAGENT.md` | 智能体定义 + 系统提示词 |

## Phase 3: 前端页面与优化

| 任务 | 状态 | 文件 | 说明 |
|------|------|------|------|
| 3.1 后端投诉API | ✅ 已完成 | `src/api/complaint_handling.py` | list/detail/stats/interactions 4个端点 |
| 3.2 前端API模块 | ✅ 已完成 | `frontend/src/api/complaint.ts` | 类型化API函数 |
| 3.3 投诉列表页面 | ✅ 已完成 | `frontend/src/components/complaint/ComplaintList.vue` | 统计卡片+筛选+表格+分页+详情侧面板 |
| 3.4 投诉统计页面 | ✅ 已完成 | `frontend/src/components/complaint/ComplaintStats.vue` | 状态/分类/紧急程度分布+每日趋势 |
| 3.5 路由注册 | ✅ 已完成 | `frontend/src/main.ts` + `src/main.py` | 前端+后端路由均已注册 |
| 3.6 构建验证 | ✅ 已完成 | — | `npm run build` 通过 |
| 3.7 通知渠道扩展 | ⏳ 待开发 | — | 企微/钉钉通知 |

## 文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/complaint-handling/SUBAGENT.md` | 智能体定义 |
| `src/skills/complaint-core-1.0.0/SKILL.md` | 核心技能定义 |
| `src/skills/complaint-core-1.0.0/scripts/complaint_tool.py` | CLI工具 (11个命令) |
| `src/services/sentiment_service.py` | 通用情绪分析服务 |
| `src/services/notification_service.py` | 通用通知服务 |
| `src/services/classification_service.py` | 通用分类服务 |
| `src/services/case_matching_service.py` | 通用案例匹配服务 |
| `src/api/complaint_handling.py` | 投诉处理API路由 |
| `frontend/src/api/complaint.ts` | 前端投诉API模块 |
| `frontend/src/components/complaint/ComplaintList.vue` | 投诉列表页面 |
| `frontend/src/components/complaint/ComplaintStats.vue` | 投诉统计页面 |

### 修改文件

| 文件 | 说明 |
|------|------|
| `src/services/__init__.py` | 导出新增服务 |
| `configs/config.yaml` | 新增 notification 配置节 |
| `deploy/db_update.sql` | 新增4张投诉处理表 |
| `src/main.py` | 注册 complaint_handling 路由 |
| `frontend/src/main.ts` | 注册投诉页面路由（顶级 + 租户级） |
