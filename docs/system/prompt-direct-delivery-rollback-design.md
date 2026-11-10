# 提示词直达生效与一键回退 — 设计文档

> 设计日期：2026-11-10
> 状态：设计定稿（9 项决策已逐条与陆伟确认），待开发
> 开发计划：[plan-prompt-direct-delivery-rollback.md](../plans/plan-prompt-direct-delivery-rollback.md)
> 关联：[Prompt 全生命周期管理](prompt-lifecycle-design.md)（本设计为其标签/草稿机制的下线与简化）

---

## 一、背景

2026-09-30 宏陶陶瓷（tenant_29304e2196e8）事故：租户 9/30 三次提交"不能创作"提示词（v15–v17，commit_message=租户前台编辑），但会话最后一轮 LLM 上下文仍是旧提示词。生产库证据链：

| 时间 | 事件 |
|---|---|
| 9/08 14:45 | v1 提交，production 标签创建指向 v1 |
| 9/15–9/30 | v3–v17 共 15 版提交，**标签未随保存移动** |
| 10/13 15:05 | 标签首次移到 v23（updated_at 全程唯一一次变更） |
| 10/14–15 | 实例行迁移（migration_20261014），v23 写入 `tenant_agent_instances.extra_prompt` |

根因：运行时解析链（`prompt_resolver.resolve`）优先读 production 标签，而当时部署的保存路径提交了新版本但未移标签，标签自 9/08 起脱节 35 天，期间 20 个新版本全部不生效。

现状（已部分缓解）：租户定制已改走实例行直读（`_load_extra_md` 优先读 `tenant_agent_instances.extra_prompt`，无缓存），下一条消息即生效；但 subagent 模板 scope 仍走标签链，且 `POST /api/prompts/{scope_id}/versions`（tenant_commit_version）仍保留"只建版本不移标签"的事故同款路径。

第二层问题（提示词送达修复后仍存在）：**历史上下文惯性**——提示词更新后，长会话历史中的旧称呼/旧说法与新约定竞争，模型倾向延续上下文既有模式，需常驻裁决声明压制。

## 二、目标

1. **保存即生效**：运行时不存在第二指针，"改了但没送达"从机制上不可能发生。
2. **一键回退上一版**：带内容预览的二次确认，回退走保存链、版本链完整留审计。
3. **常驻裁决声明**：静态文案压制历史上下文惯性，前缀缓存零失效。
4. **机制下线**：标签层、staging 校验、草稿流、无调用方端点全部移除；`prompt_labels`/`prompt_drafts` 表保留不 drop。

## 三、设计决策（2026-11-10 逐条确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 运行时标签层 | 彻底删除；resolve 直接 注册表→最新版→内容 |
| 2 | 死代码 | prompt_management.py 整文件下线；两表保留不 drop；staging/HIGH_RISK_SCOPES 删除 |
| 3 | 回退语义 | 上一版内容走同一条保存链存为新版本（"最新即生效"不变式，回退可再撤销） |
| 4 | 回退入口 | 租户侧 TenantAgentCustomizeDialog + 平台侧 AgentDefinitionManager 两处 |
| 5 | 管理后台版本 UI | 编辑器改一步保存（删草稿流）；版本列表保留只读折叠历史 |
| 6 | 惯性声明机制 | 静态常驻，全部子智能体（DB+文件），MASTER 不加；无任何动态检测逻辑（避免前缀缓存失效） |
| 7 | 声明文案 | 见 §4.5（定稿） |
| 8 | 产品引导 | 保存前二次确认（"保存后提示词将立即生效，是否保存？"）警告立即生效；保存成功仅提示"新提示词已生效" |
| 9 | 回退确认 | 弹窗展示上一版完整内容预览后确认执行 |

## 四、详细设计

### 4.1 运行时解析（src/prompts/prompt_resolver.py）

- `resolve()`：删除 production/latest 标签查找（现 L104–112），改为 `PromptVersionDB.get_latest(prompt_id)` 直接取最新版内容。降级链简化为：registry → 最新版 → None。
- `PromptCache`：删除 label 相关方法（get/set_label_version、invalidate_label）与 LABEL_TTL；`invalidate_prompt` 仅清理 content 键。
- tenant_extra 运行时路径不变：`prompt_sources._load_extra_md` 实例行优先，无实例行时 resolver 兜底（即读最新版）。
- 生效时延：`commit_version` 已含 content 缓存失效，保存后下一次 LLM 调用即时生效。

### 4.2 保存链去标签

- `src/api/subagent_extra.py` `save_extra_md`：删除 set_label 调用，保留 commit_version + `_sync_instance_extra_prompt` 双写。
- `SubagentDefinitionService.update_system_prompt`：删除 set_label（保留 dedup guard）。
- sections 分段（如 `xitongtishici`）本就直读无版本，不在本次范围。

### 4.3 回退

- 服务方法 `PromptRegistryService.rollback_to_previous(prompt_id, created_by)`：取最新两版（实施时确认 `list_versions` 排序为 desc），上一版 content → `commit_version()`（dedup 幂等：内容相同返回已有版本）；仅一版时返回明确错误"无可回退版本"。
- 端点（均沿用各自 router 既有鉴权，挂 audit）：
  - 平台：`POST /api/admin/agent-definitions/{agent_id}/system-prompt/rollback`
  - 租户：`POST /api/v1/subagents/{subagent_name}/extra/rollback`（回退后同步实例行）
- 预览数据源：
  - 平台：复用现有 `GET /{agent_id}/versions`（取上一版全文）
  - 租户：新增 `GET /api/v1/subagents/{subagent_name}/extra/previous`（返回上一版内容）

### 4.4 死代码清理清单

| 位置 | 动作 |
|---|---|
| `src/api/prompt_management.py` | 整文件删除（admin_router + tenant_router，前端零引用已核实） |
| `src/main.py` | 删除对应 import 与 include_router |
| `src/prompts/prompt_registry_service.py` | 删 set_label/delete_label/list_labels、HIGH_RISK_SCOPES/staging 校验、草稿四方法 |
| `src/db/prompt_db.py` | 删 PromptLabelDB、PromptDraftDB 类（表保留） |
| `src/api/agent_definitions.py` | 删 labels 端点（set_label 等）与 draft 三端点；`_get_prompt_content`（production 兜底 latest）改读最新 |
| 全库 grep | `set_label|prompt_labels|PromptDraftDB|HIGH_RISK_SCOPES` 引用清零（含测试） |
| `frontend/web/api/agentDefinitions.ts` | 删 saveDraft/getDraft/commitDraft/labels API；新增 rollback |

### 4.5 常驻裁决声明

`src/prompts/templates/subagent_base.md` 末尾追加固定段落（静态、一字不差，所有轮次与会话完全一致，前缀跨轮跨会话稳定，提供商缓存全量命中）：

> 【最高优先级说明】严格遵守系统提示词（角色设定与行为约束）的各项约定；当历史消息中的称呼、说法或做法与系统提示词不一致时，一律以系统提示词为准作答，不要延续历史中的旧表述。

- 覆盖范围：`subagent_base.md` 是全部非 MASTER 模式（DB 数字员工、文件子智能体、独立模式）的唯一基础模板（`prompt_sources.py` L126），Runner 与兼容 API（agent.py→同一执行内核）单点全覆盖。
- `master_agent.md` 不加（MASTER 无租户可编辑提示词，无惯性场景）。
- 无 hash 检测、无 Redis 会话状态、无动态拼接。

### 4.6 前端

- `TenantAgentCustomizeDialog.vue`：
  - 保存前二次确认：「保存后提示词将立即生效，是否保存？」，确认后才提交
  - 保存成功 toast："新提示词已生效"
  - extra_prompt 区新增"回退上一版"→ 确认框展示上一版全文 → POST rollback → 刷新表单 + toast"已回退"
- `AgentDefinitionManager.vue`：
  - system prompt 编辑器改一步保存（删草稿流交互），保存前同款二次确认
  - 版本列表改为只读折叠"历史记录"（保留 diff 查看），仅提供"回退上一版"按钮（同预览确认交互）
- UI 实现遵循既有 Base* 组件与布局规范，不引入视觉改版。

### 4.7 数据与兼容

- 无 schema 变更、无数据迁移；`prompt_labels`/`prompt_drafts` 表保留（存量标签行留存不再被读）。
- 删除的端点均无前端调用方（/api/admin/prompts、/api/prompts 已核实零引用）；labels/draft 端点的唯一使用方 AgentDefinitionManager 同仓库同发布。
- 不涉及已交付 Runtime/CLI 客户端（服务端管理面内部变更，无 wire 变化）。
- 宏陶等租户存量数据：实例行已于 migration_20261014 迁移完成，无需处理。

## 五、风险与验证

- **风险定级：高**（运行时解析变更影响所有智能体 system prompt 装配）→ 三智能体流程（开发→独立测试→CR）。
- 后端测试（`./scripts/dev_test.sh <相关测试> -p no:cacheprovider -q`）：
  - resolver：最新版直读、无标签路径、dedup、无 registry 返回 None
  - rollback：正常回退、仅一版报错、dedup 幂等、租户路径实例行同步
  - 保存链：subagent_extra / update_system_prompt 不再写标签、缓存失效即时
  - 常驻声明：非 MASTER system prompt 含声明、MASTER 不含；Runner 与兼容 API 两路径均覆盖
  - 既有 prompt/label 相关测试同步改造或删除
- 前端：`cd frontend && npm run build`；回退交互、保存 toast、历史折叠行为验证。
- 部署与真机验证：由用户当场指示；发布后在测试环境验证宏陶场景（改提示词→下一条消息生效→回退生效）。
