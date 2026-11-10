# 提示词直达生效与一键回退 — 开发计划

> 关联设计：[prompt-direct-delivery-rollback-design.md](../system/prompt-direct-delivery-rollback-design.md)
> ideas 条目：20261110-0916（docs/ideas.md 数字员工 / 子智能体分区）
> 流程：高风险 → 三智能体（开发 → 独立测试 → CR）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 后端：运行时解析简化 + 保存链去标签 + 死代码下线 | 📋 待开发 | — |
| Phase 2 | 后端：回退服务与端点（平台 + 租户） | 📋 待开发 | — |
| Phase 3 | 常驻裁决声明注入 + 双路径核验 | 📋 待开发 | — |
| Phase 4 | 前端：两组件改造 + API 层 | 📋 待开发 | — |
| Phase 5 | 回归测试 + 文档收尾 | 📋 待开发 | — |

## Phase 1 — 后端解析与保存链

改动点（对应设计 §4.1/§4.2/§4.4）：

1. `src/prompts/prompt_resolver.py`
   - `resolve()` 删标签查找，直读 `PromptVersionDB.get_latest`
   - `PromptCache` 删 label 方法与 LABEL_TTL；`invalidate_prompt` 仅清 content
   - 删 `PromptResolver._resolve_label`
2. `src/api/subagent_extra.py`：`save_extra_md` 删 set_label（保留 commit + 实例行双写）
3. `src/services/subagent_definition_service.py`：`update_system_prompt` 删 set_label
4. 死代码：
   - 删 `src/api/prompt_management.py`；`src/main.py` 删 import/include_router
   - `prompt_registry_service.py` 删 set_label/delete_label/list_labels、HIGH_RISK_SCOPES/staging、草稿四方法
   - `src/db/prompt_db.py` 删 PromptLabelDB/PromptDraftDB 类（表保留不 drop）
   - `src/api/agent_definitions.py` 删 labels/draft 端点；`_get_prompt_content` 改读最新
   - 全库 grep `set_label|prompt_labels|PromptDraftDB|HIGH_RISK_SCOPES` 引用清零

验证：resolver/保存链相关单测 + 既有 prompt 测试改造。

## Phase 2 — 回退服务与端点

1. `PromptRegistryService.rollback_to_previous(prompt_id, created_by)`：取最新两版（确认 `list_versions` desc 排序），上一版 content → commit_version（dedup 幂等）；仅一版返回"无可回退版本"
2. 平台端点：`POST /api/admin/agent-definitions/{agent_id}/system-prompt/rollback`（挂 audit）
3. 租户端点：`POST /api/v1/subagents/{subagent_name}/extra/rollback`（回退后同步实例行）；预览端点 `GET /api/v1/subagents/{subagent_name}/extra/previous`

验证：rollback 单测（正常/仅一版/dedup/实例行同步）。

## Phase 3 — 常驻裁决声明

1. `src/prompts/templates/subagent_base.md` 末尾追加设计 §4.5 定稿文案（`---` 分隔）
2. 核验 Runner 与兼容 API（agent.py→同一执行内核）两路径 system prompt 均含声明、MASTER 不含

验证：单测断言两路径 prompt 内容；本地构造 MASTER/SUBAGENT 各一例。

## Phase 4 — 前端

1. `frontend/web/api/agentDefinitions.ts`：删 saveDraft/getDraft/commitDraft/labels，新增 rollbackSystemPrompt
2. `TenantAgentCustomizeDialog.vue`：保存前二次确认「保存后提示词将立即生效，是否保存？」；extra_prompt"回退上一版"（上一版全文预览确认框）；保存成功 toast"新提示词已生效"
3. `AgentDefinitionManager.vue`：编辑器一步保存（删草稿流，保存前同款二次确认）；版本列表改只读折叠"历史记录"（保留 diff）；加"回退上一版"按钮（同预览确认）
4. 租户 extra API 层补 previous/rollback 调用

验证：`cd frontend && npm run build`；回退/保存/历史折叠交互走查。

## Phase 5 — 回归与收尾

1. `./scripts/dev_test.sh <prompt 相关测试> -p no:cacheprovider -q` 全量回归（容器须运行当前工作区代码）
2. 测试环境真机验证宏陶场景：改提示词→下一条消息生效→回退→再生效
3. 文档收尾：本计划进度表、ideas.md 状态同步；提交关联文档行 `文档: docs/plans/plan-prompt-direct-delivery-rollback.md`
4. 部署由用户当场指示；发布验证覆盖"只升级服务端"组合（本次无 wire 变化，管理面前后端同仓库）
