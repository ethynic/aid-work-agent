# 子智能体 Prompt 管理 — 第一优先级开发计划

> 对应设计文档：[prompt-lifecycle-design.md](./prompt-lifecycle-design.md)
> 对应调研报告：[prompt-version-management-research.md](../research/prompt-version-management-research.md)
> 创建日期：2026-06-02
> 更新日期：2026-06-02（重新定位为子智能体 Prompt 管理，融入子智能体定义管理）
> 状态：Phase 0 已完成

---

## 范围说明

本开发计划围绕**子智能体的 Prompt 管理**展开，核心认识是：

> **子智能体 = 定义部分（YAML 配置） + System Prompt（可版本化）**

- **定义部分**：name、description、capabilities、tools、skills 等配置，通过子智能体管理界面修改，支持 LLM 智能推荐工具/技能
- **System Prompt 部分**：Markdown body，纳入版本管理，支持编辑 → 提交版本 → 对比 → 回滚
- **租户定制 Prompt（extra_md）**：独立的第三层，同样纳入版本管理

**不在本计划范围内**：
- §九 A/B 测试（二期）
- §十 效果评估 Pipeline（二期）
- 系统模板版本化（三期）
- 技能 Prompt 版本化（二期）

---

## Phase 0：Prompt 内容优化（P0 ~ P3） ✅ 已完成

> 改动范围：仅修改模板文件和工具代码，不改 agent.py 核心逻辑

- [x] 修正"透明化"矛盾：subagent_base.md 和 master_agent.md 改为"专业沟通"
- [x] 精简 http_api usage_guide：去掉与 JSON Schema 重复的参数说明
- [x] 评估 {available_tools_list}：结论保留（token 消耗低，去掉收益不大）

---

## Phase 1：版本管理数据库 + 基础服务层

> 设计文档参考：§三（数据模型）、§六（运行时 Prompt 解析流程）、§七（API 设计）
> 目标：建立 Prompt 版本管理的数据库表和后端服务
> 预计工期：1 周

### 阶段 1.1：数据库表创建

> 前置依赖：无
> 关键文件：`deploy/init-postgres.sql`、`deploy/db_update.sql`

- [ ] **1.1.1 在 deploy/db_update.sql 添加 prompt 相关表**
  - 添加以下表（全部 IF NOT EXISTS）：
    - `prompt_registry` — Prompt 注册表
    - `prompt_versions` — Prompt 版本（不可变）
    - `prompt_labels` — 标签（环境部署）
    - `prompt_drafts` — 草稿
  - 字段和索引按设计文档 §3.2 定义
  - 不添加外键约束（遵循 database_dev.md 规范）
  - [ ] 未开始

- [ ] **1.1.2 在 deploy/init-postgres.sql 添加相同的建表语句**
  - 保证全新部署环境也能创建完整表结构
  - [ ] 未开始

- [ ] **1.1.3 验证 _apply_db_updates() 能自动执行新增的 SQL**
  - [ ] 未开始

### 阶段 1.2：PromptRegistryService 服务层

> 前置依赖：1.1
> 新建文件：`src/prompts/prompt_registry_service.py`

- [ ] **1.2.1 实现 PromptRegistryService 核心类**
  - `register_prompt()` — 创建注册记录
  - `get_prompt()` — 获取注册信息
  - `get_prompt_by_scope()` — 按 scope 查找
  - `list_prompts()` — 分页列表
  - [ ] 未开始

- [ ] **1.2.2 实现版本管理方法**
  - `commit_version()` — 创建新版本（自增 version + content_hash 去重）
  - `get_version()` / `list_versions()` / `diff_versions()`
  - [ ] 未开始

- [ ] **1.2.3 实现草稿管理方法**
  - `get_draft()` / `save_draft()`（UPSERT）/ `delete_draft()` / `commit_draft()`
  - [ ] 未开始

- [ ] **1.2.4 实现标签管理方法**
  - `get_label()` / `set_label()`（含高危 Prompt staging 校验）/ `delete_label()` / `list_labels()`
  - [ ] 未开始

### 阶段 1.3：PromptResolver 运行时解析层

> 前置依赖：1.2
> 新建文件：`src/prompts/prompt_resolver.py`、`src/prompts/prompt_cache.py`

- [ ] **1.3.1 实现 PromptResolver.resolve()**
  - 降级策略：production label → latest label → latest_version → None（降级到文件系统）
  - [ ] 未开始

- [ ] **1.3.2 实现 PromptCache 缓存层**
  - 使用现有 RedisClient，缓存 content（TTL 10min）和 label→version 映射（TTL 5min）
  - [ ] 未开始

- [ ] **1.3.3 在 PromptResolver 中集成缓存**
  - resolve() 先查缓存，miss 时查数据库并回填
  - [ ] 未开始

### 阶段 1.4：后端 CRUD API

> 前置依赖：1.2
> 新建文件：`src/api/prompt_management.py`

- [ ] **1.4.1 平台管理员 API**（`/api/admin/prompts`）
  - Prompt CRUD + 版本 + 草稿 + 标签 API
  - [ ] 未开始

- [ ] **1.4.2 租户管理员 API**（`/api/prompts`）
  - 只允许 scope=`tenant_extra`，通过 tenant_id 隔离
  - [ ] 未开始

- [ ] **1.4.3 子智能体 System Prompt 版本管理 API**
  - 在 `src/api/admin_subagent.py` 中集成 Prompt 版本管理
  - 获取/更新子智能体 System Prompt 时，自动通过 PromptRegistryService 管理版本
  - [ ] 未开始

- [ ] **1.4.4 注册路由到 main.py**
  - [ ] 未开始

### 阶段 1.5：单元测试

- [ ] **1.5.1 PromptRegistryService 单元测试**
- [ ] **1.5.2 PromptResolver 单元测试**
- [ ] **1.5.3 API 集成测试**

### Phase 1 完成标准

- [ ] 四张表创建成功
- [ ] PromptRegistryService、PromptResolver、PromptCache 正常工作
- [ ] 所有 API 端点正确响应
- [ ] 租户隔离和高危 Prompt staging 校验生效
- [ ] 测试通过

---

## Phase 2：子智能体管理增强 + LLM 智能推荐

> 设计文档参考：§八.1（子智能体管理改造）
> 目标：改造子智能体编辑页面为两区分离（定义区 + Prompt 区），增加 LLM 智能推荐
> 预计工期：1.5 周
> 前置依赖：Phase 1

### 阶段 2.1：后端 — LLM 智能推荐工具/技能 API

> 新增 API，基于现有 ai-enhance 模式

- [ ] **2.1.1 实现 `POST /api/admin/subagents/suggest-config`**
  - 输入：子智能体 name、description、capabilities
  - 后端调用 LLM，传入系统可用工具列表（名称 + 描述）和技能列表（名称 + 描述）
  - LLM 返回推荐的 tools 和 skills 配置
  - 参考现有 `ai-enhance` API 的实现模式
  - [ ] 未开始

### 阶段 2.2：后端 — 子智能体 System Prompt 版本管理集成

> 将子智能体的 System Prompt 的读写与 PromptRegistryService 集成

- [ ] **2.2.1 修改子智能体创建/更新 API**
  - `POST /api/admin/subagents`（创建）：创建子智能体后，自动在 prompt_registry 注册一条 scope=`subagent` 记录，将 system_prompt 提交为 V1 并标记 production
  - `PUT /api/admin/subagents/{agent_id}`（更新）：如果 system_prompt 有变更，提交新版本
  - [ ] 未开始

- [ ] **2.2.2 改造 `_build_system_prompt()` 为数据库优先**
  - SUBAGENT/STANDALONE 模式：先从 PromptResolver 获取 System Prompt
  - 未找到 → 降级使用 subagent_config.system_prompt（文件系统）
  - 改动量：约 20 行
  - [ ] 未开始

### 阶段 2.3：前端 — 子智能体编辑页面两区分离改造

> 现有基础：`DigitalEmployeeManager.vue` 已有编辑功能
> 改造为定义区 + Prompt 区的两区布局

- [ ] **2.3.1 前端 API 层**
  - 新建 `frontend/src/api/prompts.ts`
  - 封装 Prompt CRUD + 版本 + 草稿 + 标签 API
  - [ ] 未开始

- [ ] **2.3.2 新增 PromptVersionHistory.vue 版本历史面板**
  - 展示版本时间线：版本号、提交时间、提交人、变更说明
  - 当前 production 版本高亮标记
  - 支持一键回滚
  - [ ] 未开始

- [ ] **2.3.3 新增 PromptDiffView.vue 版本对比视图**
  - 选择两个版本 diff 对比，差异行高亮
  - [ ] 未开始

- [ ] **2.3.4 改造 DigitalEmployeeManager.vue 编辑模式**
  - 编辑页面分为**定义区**（上半部分）和 **Prompt 区**（下半部分）
  - 定义区：现有 name、description、capabilities、tools、skills 编辑
    - 工具/技能配置区域增加"智能推荐"按钮（调用 suggest-config API）
  - Prompt 区：System Prompt Markdown 编辑器 + 右侧版本历史面板
    - 编辑后自动保存草稿
    - 提交新版本需填写变更说明
    - 高危 Prompt 强制 staging 流程
  - [ ] 未开始

- [ ] **2.3.5 子智能体列表增强**
  - 卡片展示当前 System Prompt 版本号、最后修改时间
  - [ ] 未开始

### 阶段 2.4：验证

- [ ] **2.4.1 LLM 智能推荐验证**
  - 创建子智能体，输入用途描述，点击"智能推荐"
  - 验证推荐的工具/技能合理
  - [ ] 未开始

- [ ] **2.4.2 两区分离编辑验证**
  - 修改定义区配置（工具/技能）→ 保存 → 即时生效
  - 修改 Prompt 区内容 → 提交版本 → 版本历史正确
  - diff 对比和回滚正常工作
  - [ ] 未开始

- [ ] **2.4.3 前端构建验证**
  - `cd frontend && npm run build` 无编译错误
  - [ ] 未开始

### Phase 2 完成标准

- [ ] LLM 智能推荐工具/技能功能正常
- [ ] 子智能体编辑页面分为定义区和 Prompt 区
- [ ] System Prompt 修改自动提交版本
- [ ] 版本历史、diff 对比、回滚功能正常
- [ ] 高危 Prompt staging 流程生效
- [ ] 前端构建无错误

---

## Phase 3：extra_md 迁移 + 租户前台编辑器

> 设计文档参考：§八.2（租户定制 extra.md 集成）、§八.3.2（租户前台编辑入口）
> 目标：将 extra_md 从文件系统迁移到数据库 + 租户前台编辑器
> 预计工期：1.5 周
> 前置依赖：Phase 1

### 阶段 3.1：extra_md 迁移

- [ ] **3.1.1 编写文件系统 → 数据库迁移脚本**
  - 扫描 `storage/subagents/` 下所有 `extra_<tenant_id>.md` 文件
  - 为每个文件创建 prompt_registry + prompt_versions + prompt_labels 记录
  - 支持 `--dry-run`，幂等安全
  - [ ] 未开始

- [ ] **3.1.2 改造 `_load_extra_md()` 为数据库优先**
  - 先查 PromptResolver，miss 时降级到文件系统
  - [ ] 未开始

- [ ] **3.1.3 改造 extra_md API 为数据库驱动**
  - `src/api/subagent_extra.py` 改为调用 PromptRegistryService
  - 保持 API 签名不变
  - [ ] 未开始

- [ ] **3.1.4 验证迁移和降级**
  - 数据迁移正确 + 降级路径正常 + API 兼容
  - [ ] 未开始

### 阶段 3.2：租户前台编辑器

- [ ] **3.2.1 DigitalEmployeeManager 租户卡片添加"定制提示词"按钮**
  - 点击跳转到 `/t/{tenant_id}/agent/{subagent_name}/prompt`
  - [ ] 未开始

- [ ] **3.2.2 新建 TenantPromptEditor.vue**
  - Markdown 编辑器 + 版本历史面板（复用 PromptVersionHistory.vue）
  - 草稿自动保存 + 提交版本（tenant_extra 直接标记 production）
  - [ ] 未开始

- [ ] **3.2.3 路由注册和前端构建验证**
  - [ ] 未开始

### Phase 3 完成标准

- [ ] extra_md 已迁移到数据库，降级路径正常
- [ ] 租户管理员可在线编辑定制 Prompt
- [ ] 版本管理闭环完整（编辑 → 提交 → 对比 → 回滚）
- [ ] 前端构建无错误

---

## 总体进度追踪

| Phase | 内容 | 预计工期 | 状态 |
|-------|------|---------|------|
| Phase 0 | Prompt 内容优化（P0~P3） | 2 天 | ✅ 已完成 |
| Phase 1 | 版本管理数据库 + 基础服务层 | 1 周 | ⬜ 未开始 |
| Phase 2 | 子智能体管理增强 + LLM 智能推荐 | 1.5 周 | ⬜ 未开始 |
| Phase 3 | extra_md 迁移 + 租户前台编辑器 | 1.5 周 | ⬜ 未开始 |

**总工期：约 4 周**

### 关键里程碑

| 里程碑 | 完成标志 | 对应任务 |
|--------|---------|---------|
| M0: Prompt 优化完成 | 透明化矛盾修复 + usage_guide 精简 | Phase 0 ✅ |
| M1: 版本管理可用 | 数据库表 + 服务层 + API 可工作 | Phase 1 |
| M2: 子智能体管理增强 | 两区分离编辑 + LLM 智能推荐 + 版本管理 | Phase 2 |
| M3: 租户编辑器可用 | extra_md 迁移 + 租户前台编辑器 | Phase 3 |

### 依赖关系

```
Phase 0 ─── ✅ 已完成
Phase 1 ─── 无前置，可立即开始
Phase 2 ─── 依赖 Phase 1（需要版本管理服务层）
Phase 3 ─── 依赖 Phase 1（需要版本管理服务层），可与 Phase 2 并行
```

### 风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据库与文件系统不一致 | 多 Worker 加载到不同 Prompt | 双写机制 + 降级测试 |
| 版本数量膨胀 | 数据库存储压力 | 版本保留策略（默认保留最近 100 个版本） |
| LLM 推荐不准确 | 工具/技能配置不合理 | 推荐结果供管理员确认，不自动保存 |
| 两区分离 UI 改动量大 | DigitalEmployeeManager.vue 改造影响现有功能 | 分步改造，先加 Prompt 区，再调定义区 |
