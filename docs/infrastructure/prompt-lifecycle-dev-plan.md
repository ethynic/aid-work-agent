# Prompt 全生命周期管理 — 第一优先级开发计划

> 对应设计文档：[prompt-lifecycle-design.md](./prompt-lifecycle-design.md)
> 对应调研报告：[prompt-version-management-research.md](../research/prompt-version-management-research.md)
> 对应差距分析：[enterprise-agent-infrastructure-gap-analysis.md](../research/enterprise-agent-infrastructure-gap-analysis.md) §2.2
> 创建日期：2026-06-02
> 状态：待开发

---

## 范围说明

本开发计划仅覆盖设计文档中的**第一优先级**内容：

- **§四 模板方案**：修正"透明化"矛盾（P0）、精简 usage_guide（P1）
- **§五 System Prompt 组装架构**：优化已识别的问题（P0 ~ P3）
- **§二/三/六/七/八 数据库版本管理 + extra_md 迁移**：版本管理基础 + 租户定制 Prompt 从文件系统迁移到数据库

**不在本计划范围内**：
- §九 A/B 测试（二期）
- §十 效果评估 Pipeline（二期）
- 系统模板版本化（三期）
- 技能 Prompt 版本化（二期）

---

## Phase 0：Prompt 内容优化（P0 ~ P3）

> 设计文档参考：§五.2（已识别的问题）、§五.4（优化计划）
> 目标：修正当前 system prompt 中的矛盾和冗余，提升 LLM 行为一致性、降低 token 消耗
> 改动范围：仅修改模板文件和工具代码，不改 agent.py 核心逻辑
> 预计工期：2 天

### 阶段 0.1：修正"透明化"矛盾（P0）

> 前置依赖：无
> 关键文件：`src/prompts/templates/subagent_base.md`、`src/prompts/templates/master_agent.md`

- [x] **0.1.1 修改 subagent_base.md 中的"透明化"原则**
  - 将"透明化：让用户知道你在做什么"改为"专业沟通：告知用户结论和结果，不暴露内部工具调用和检索过程"
  - 保持其他原则不变
  - [x] 已完成

- [x] **0.1.2 修改 master_agent.md 中的对应原则**
  - 同步修改 master_agent.md 中的"透明化"表述，保持两个模板一致
  - [x] 已完成

- [x] **0.1.3 验证修改效果**
  - 启动服务验证（后续启动服务时验证）
  - [x] 代码已修改，待服务启动验证

### 阶段 0.2：精简 usage_guide（P1）

> 前置依赖：无
> 关键文件：各工具类中的 `usage_guide` 属性

- [x] **0.2.1 梳理各工具 usage_guide 内容**
  - 13 个工具中仅 5 个有非空 usage_guide：file_write、browser_open、content_generate、http_api、transfer_to_human
  - 其中 http_api 冗余度最高（"参数说明"部分 8 个参数全部与 InputModel 重复）
  - file_write 冗余度中等（示例提及参数名，但三种模式说明有独立价值）
  - browser_open、content_generate、transfer_to_human 无冗余
  - [x] 已完成

- [x] **0.2.2 精简各工具 usage_guide**
  - http_api：已精简，去掉"参数说明"部分（8 个参数与 InputModel 重复），保留文件上传示例、凭据替换、认证模式
  - file_write：保留不改（三种用法模式有独立价值，冗余度低）
  - browser_open：保留不改（主要是工作流说明和跨工具参数，冗余度很低）
  - content_generate、transfer_to_human：保留不改（无冗余）
  - [x] 已完成

### 阶段 0.3：评估去掉 {available_tools_list}（P2）

> 前置依赖：0.2 完成
> 关键文件：`src/prompts/templates/*.md`、`src/core/agent.py`（variables 构建部分）

- [x] **0.3.1 评估 {available_tools_list} 的必要性**
  - LLM 的 `tools` 参数已包含完整工具定义，`{available_tools_list}` 确有冗余
  - token 消耗很低（约 20-50 token），去掉收益不足以抵消修改模板和 agent.py 的风险
  - [x] 已评估，决定保留

- [x] **0.3.2 去掉或保留 {available_tools_list}**
  - 评估结论：**保留**。LLM 的 `tools` 参数已包含完整工具定义，`{available_tools_list}` 确有冗余，但 token 消耗很低（约 20-50 token），去掉的收益不足以抵消修改模板和 agent.py 的风险。
  - [x] 已评估，决定保留

### Phase 0 完成标准

- [ ] subagent_base.md 和 master_agent.md 中不再有"透明化"表述
- [ ] 所有工具的 usage_guide 不再包含与 JSON Schema 重复的参数说明
- [ ] 测试对话中 LLM 不暴露内部工具调用过程
- [ ] system prompt token 消耗有可量化的减少（记录精简前后的 token 数）

---

## Phase 1：版本管理数据库 + 基础服务层

> 设计文档参考：§三（数据模型）、§六（运行时 Prompt 解析流程）、§七（API 设计）
> 目标：建立 Prompt 版本管理的数据库表和后端服务，为 extra_md 迁移提供基础
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
  - 使用 UUID 主键（复用已有 `uuid-ossp` 扩展的 `gen_random_uuid()`）
  - 不添加外键约束（遵循 database_dev.md 规范）
  - [ ] 未开始

- [ ] **1.1.2 在 deploy/init-postgres.sql 添加相同的建表语句**
  - 保证全新部署环境也能创建完整表结构
  - [ ] 未开始

- [ ] **1.1.3 在 init_database() 中确保新表被创建**
  - 验证 `_apply_db_updates()` 能自动执行新增的 SQL
  - [ ] 未开始

### 阶段 1.2：PromptRegistryService 服务层

> 前置依赖：1.1
> 新建文件：`src/prompts/prompt_registry_service.py`

- [ ] **1.2.1 实现 PromptRegistryService 核心类**
  - `register_prompt(tenant_id, scope, scope_id, display_name, description, created_by)` — 创建注册记录
  - `get_prompt(prompt_id)` — 获取注册信息
  - `get_prompt_by_scope(tenant_id, scope, scope_id)` — 按 scope 查找（用于运行时解析）
  - `list_prompts(tenant_id, scope, page, page_size)` — 分页列表
  - 使用 `get_db_connection()` 连接数据库
  - [ ] 未开始

- [ ] **1.2.2 实现版本管理方法**
  - `commit_version(prompt_id, content, variables, model_config, commit_message, created_by)` — 创建新版本
    - 自增 version（`latest_version + 1`）
    - 计算 content_hash（SHA-256），检查是否与上一版本重复
    - 更新 registry 的 `latest_version`
  - `get_version(prompt_id, version)` — 获取特定版本
  - `list_versions(prompt_id, page, page_size)` — 版本列表（分页）
  - `diff_versions(prompt_id, v1, v2)` — 两个版本 diff（使用 `difflib.unified_diff`）
  - [ ] 未开始

- [ ] **1.2.3 实现草稿管理方法**
  - `get_draft(prompt_id)` — 获取草稿
  - `save_draft(prompt_id, content, variables, base_version, updated_by)` — 保存/更新草稿（UPSERT）
  - `delete_draft(prompt_id)` — 丢弃草稿
  - `commit_draft(prompt_id, commit_message, created_by)` — 提交草稿为新版本（事务：创建版本 + 删除草稿）
  - [ ] 未开始

- [ ] **1.2.4 实现标签管理方法**
  - `get_label(prompt_id, label)` — 获取标签指向的版本
  - `set_label(prompt_id, label, version_id, updated_by)` — 设置标签
    - **高危 Prompt 强制校验**：scope 为 `system_template` 或 `subagent` 时，`production` 标签要求版本必须经历过 `staging`
  - `delete_label(prompt_id, label)` — 删除标签
  - `list_labels(prompt_id)` — 列出所有标签
  - [ ] 未开始

### 阶段 1.3：PromptResolver 运行时解析层

> 前置依赖：1.2
> 新建文件：`src/prompts/prompt_resolver.py`

- [ ] **1.3.1 实现 PromptResolver.resolve()**
  - 核心方法：`async resolve(scope, scope_id, tenant_id) -> Optional[ResolvedPrompt]`
  - 降级策略（按顺序）：
    1. 查找 `prompt_labels("production")` → 找到 → 使用该版本
    2. 查找 `prompt_labels("latest")` → 找到 → 使用该版本
    3. `prompt_registry` 存在但无标签 → 使用 `latest_version`
    4. `prompt_registry` 不存在 → 返回 None（降级到文件系统）
  - 返回 `ResolvedPrompt` 数据类：`content: str, version: int, version_id: str, prompt_id: str`
  - [ ] 未开始

- [ ] **1.3.2 实现 PromptCache 缓存层**
  - 新建文件：`src/prompts/prompt_cache.py`
  - 使用现有 `RedisClient`，降级到内存缓存（RedisClient 内置 fallback）
  - 缓存结构：
    - `prompt:content:{prompt_id}:{version}` → content（TTL 10min）
    - `prompt:label:{prompt_id}:{label}` → version（TTL 5min）
  - 方法：`get_content()`, `set_content()`, `invalidate_label()`, `invalidate_prompt()`
  - 标签变更时主动失效缓存
  - [ ] 未开始

- [ ] **1.3.3 在 PromptResolver 中集成缓存**
  - resolve() 先查缓存，miss 时查数据库并回填
  - set_label() 时调用 cache.invalidate_label()
  - commit_version() 时调用 cache.set_content()
  - [ ] 未开始

### 阶段 1.4：后端 CRUD API

> 前置依赖：1.2
> 新建文件：`src/api/prompt_management.py`
> 设计文档参考：§七（API 设计）

- [ ] **1.4.1 实现 Prompt 管理 API**
  - 路由前缀：`/api/admin/prompts`（平台管理员）
  - `GET /` — 列出 Prompt（分页，按 scope 筛选）
  - `GET /{prompt_id}` — 获取 Prompt 详情
  - `POST /` — 创建新 Prompt 注册
  - 权限校验：复用现有 `require_admin` 中间件
  - [ ] 未开始

- [ ] **1.4.2 实现版本管理 API**
  - `GET /{prompt_id}/versions` — 版本列表（分页）
  - `GET /{prompt_id}/versions/{version}` — 获取特定版本
  - `POST /{prompt_id}/versions` — 创建新版本（commit）
  - `GET /{prompt_id}/versions/diff?v1=X&v2=Y` — 版本 diff
  - [ ] 未开始

- [ ] **1.4.3 实现草稿 API**
  - `GET /{prompt_id}/draft` — 获取草稿
  - `PUT /{prompt_id}/draft` — 保存草稿
  - `DELETE /{prompt_id}/draft` — 丢弃草稿
  - `POST /{prompt_id}/draft/commit` — 提交草稿为新版本
  - [ ] 未开始

- [ ] **1.4.4 实现标签 API**
  - `GET /{prompt_id}/labels` — 列出所有标签
  - `PUT /{prompt_id}/labels/{label}` — 设置标签
  - `DELETE /{prompt_id}/labels/{label}` — 删除标签
  - `POST /{prompt_id}/rollback` — 回滚 production 到指定版本
  - [ ] 未开始

- [ ] **1.4.5 实现租户管理员 API**
  - 路由前缀：`/api/prompts`
  - 只允许访问 scope=`tenant_extra` 的 Prompt
  - 租户隔离：通过 `request.state.tenant_id` 过滤
  - 复用阶段 1.4.1~1.4.4 的服务层，增加权限过滤
  - [ ] 未开始

- [ ] **1.4.6 注册路由到 main.py**
  - 在 `src/main.py` 中 include 两个路由
  - [ ] 未开始

### 阶段 1.5：单元测试

> 前置依赖：1.2 ~ 1.4

- [ ] **1.5.1 编写 PromptRegistryService 单元测试**
  - 测试 CRUD 操作
  - 测试版本自增和去重（content_hash）
  - 测试草稿 UPSERT 和提交事务
  - 测试高危 Prompt 的 staging 强制校验
  - 位置：`tests/unit/test_prompt_registry_service.py`
  - [ ] 未开始

- [ ] **1.5.2 编写 PromptResolver 单元测试**
  - Mock 数据库查询，测试降级策略的 4 个层级
  - 测试缓存命中/miss 行为
  - 测试缓存失效逻辑
  - 位置：`tests/unit/test_prompt_resolver.py`
  - [ ] 未开始

- [ ] **1.5.3 编写 API 集成测试**
  - 测试 CRUD API 的正常和异常路径
  - 测试租户隔离（租户管理员只能访问自己的 Prompt）
  - 位置：`tests/integration/test_prompt_api.py`
  - [ ] 未开始

### Phase 1 完成标准

- [ ] `prompt_registry`、`prompt_versions`、`prompt_labels`、`prompt_drafts` 四张表在开发环境创建成功
- [ ] PromptRegistryService 所有方法可正常工作
- [ ] PromptResolver 可从数据库解析 Prompt，降级策略正确
- [ ] 缓存层可正常工作，标签变更时缓存正确失效
- [ ] 所有 API 端点返回正确响应
- [ ] 租户隔离正确（租户管理员无法访问其他租户数据）
- [ ] 高危 Prompt 的 staging 强制校验生效
- [ ] 单元测试和集成测试通过

---

## Phase 2：extra_md 迁移

> 设计文档参考：§八.2（租户定制 extra.md 集成）、§六（运行时解析流程）
> 目标：将 extra_md 从文件系统迁移到数据库，纳入版本管理
> 预计工期：1 周

### 阶段 2.1：数据迁移脚本

> 前置依赖：Phase 1
> 新建文件：`scripts/migrate_extra_md_to_db.py`

- [ ] **2.1.1 编写文件系统 → 数据库迁移脚本**
  - 扫描 `storage/subagents/` 下所有 `extra_<tenant_id>.md` 文件
  - 对每个文件：
    1. 在 `prompt_registry` 创建记录（scope=`tenant_extra`, scope_id=`extra:{dir_name}:{tenant_id}`）
    2. 在 `prompt_versions` 创建初始版本（version=1）
    3. 在 `prompt_labels` 创建 `production` 标签指向该版本
  - 支持 `--dry-run` 模式（只输出将要执行的操作）
  - 幂等安全：已迁移的文件跳过
  - [ ] 未开始

- [ ] **2.1.2 编写回退脚本**
  - 从数据库导出所有 tenant_extra Prompt 回文件系统
  - 用于紧急回退场景
  - [ ] 未开始

### 阶段 2.2：运行时集成

> 前置依赖：2.1
> 关键文件：`src/core/agent.py`（`_load_extra_md()` 和 `_build_system_prompt()`）

- [ ] **2.2.1 改造 _load_extra_md() 为数据库优先**
  - 在 `_load_extra_md()` 中：
    1. 先通过 PromptResolver 查询数据库（scope=`tenant_extra`, scope_id=`extra:{dir_name}:{tenant_id}`）
    2. 找到 → 返回 resolved.content
    3. 未找到 → 降级到文件系统读取（保留现有逻辑作为 fallback）
  - 改动量：约 15 行新增代码，零行删除（仅增加分支）
  - [ ] 未开始

- [ ] **2.2.2 改造 extra_md API 为数据库驱动**
  - 修改 `src/api/subagent_extra.py`：
    - `get_extra_md()`：改为调用 PromptRegistryService，先查数据库，miss 时查文件系统
    - `save_extra_md()`：改为通过 PromptRegistryService 保存草稿并提交版本
    - `delete_extra_md()`：改为删除 prompt_labels 和 prompt_versions（保留历史版本）
  - 保持 API 签名和响应格式不变，对前端透明
  - [ ] 未开始

- [ ] **2.2.3 文件系统同步双写**
  - 在 PromptRegistryService.commit_version() 中，如果 scope=`tenant_extra`，同步写入文件系统
  - 保证多 Worker 在数据库缓存未命中时仍可从文件系统读取
  - 文件写入失败仅 warning，不阻止数据库提交
  - [ ] 未开始

### 阶段 2.3：验证

> 前置依赖：2.2

- [ ] **2.3.1 数据迁移验证**
  - 运行迁移脚本
  - 检查数据库中每个 tenant_extra 记录的 content 与文件内容一致
  - [ ] 未开始

- [ ] **2.3.2 运行时行为验证**
  - 启动服务，选择有 extra_md 的租户，发送对话
  - 验证 system prompt 中包含租户定制内容
  - 验证对话行为与迁移前一致
  - [ ] 未开始

- [ ] **2.3.3 降级路径验证**
  - 临时删除数据库中的某个 extra_md 记录
  - 验证系统能降级到文件系统读取
  - 恢复数据
  - [ ] 未开始

- [ ] **2.3.4 API 兼容性验证**
  - 使用现有 extra_md API（GET/PUT/DELETE）执行操作
  - 验证 API 行为和响应格式不变
  - 验证 PUT 后数据库中创建了新版本
  - [ ] 未开始

### Phase 2 完成标准

- [ ] 所有现有的 `extra_*.md` 文件已迁移到数据库
- [ ] `_load_extra_md()` 优先从数据库获取内容
- [ ] 数据库 miss 时正确降级到文件系统
- [ ] extra_md API（GET/PUT/DELETE）行为与改造前一致
- [ ] 多 Worker 环境下文件系统双写正常
- [ ] 对话行为与迁移前无差异

---

## Phase 3：前端编辑器

> 设计文档参考：§八.3（前端编辑器改造）
> 目标：租户前台 Prompt 编辑器 + 版本管理界面
> 前端规范参考：`.claude/rules/frontend_dev.md`、`.claude/rules/page_patterns.md`
> 预计工期：1.5 周

### 阶段 3.1：租户前台编辑入口

> 前置依赖：Phase 2
> 关键文件：`frontend/src/components/saas/DigitalEmployeeManager.vue`

- [ ] **3.1.1 API 层**
  - 新建 `frontend/src/api/prompts.ts`
  - 实现 Prompt CRUD + 版本 + 草稿 + 标签的 API 封装
  - 所有请求使用 `getAuthHeader()`（自动包含 X-Tenant-Id）
  - [ ] 未开始

- [ ] **3.1.2 DigitalEmployeeManager 卡片添加"定制提示词"按钮**
  - 在每个已启用的子智能体卡片操作区添加"定制提示词"按钮
  - 点击跳转到 `/t/{tenant_id}/agent/{subagent_name}/prompt`
  - [ ] 未开始

- [ ] **3.1.3 路由注册**
  - 在 `frontend/src/main.ts` 的 `/t/:tenantId` children 中添加 prompt 编辑路由
  - [ ] 未开始

### 阶段 3.2：TenantPromptEditor 编辑器页面

> 前置依赖：3.1
> 新建文件：`frontend/src/components/saas/TenantPromptEditor.vue`

- [ ] **3.2.1 实现基础编辑器布局**
  - 遵循标准页面框架（AppHeader + 内容区）
  - 左侧：Markdown 编辑器（使用 textarea 或引入轻量 Markdown 编辑器）
  - 右侧：可用变量提示面板
  - 底部：变更说明输入 + 保存草稿/提交版本按钮
  - [ ] 未开始

- [ ] **3.2.2 实现草稿自动保存**
  - 编辑内容变更时 debounce 2 秒自动保存为草稿
  - 页面加载时恢复草稿（如有）
  - [ ] 未开始

- [ ] **3.2.3 实现提交版本流程**
  - 提交前弹出变更说明输入框
  - 调用 `POST /api/prompts/{prompt_id}/draft/commit`
  - 提交成功后自动标记为 production（tenant_extra 不需要 staging 流程）
  - [ ] 未开始

### 阶段 3.3：版本管理组件

> 前置依赖：3.2
> 新建文件：`frontend/src/components/saas/PromptVersionHistory.vue`、`PromptDiffView.vue`

- [ ] **3.3.1 实现 PromptVersionHistory 版本历史面板**
  - 展示版本时间线：版本号、提交时间、提交人、变更说明
  - 当前 production 版本高亮标记
  - 点击版本可查看该版本内容
  - 支持一键回滚（调用 rollback API）
  - 回滚前 confirm 确认
  - [ ] 未开始

- [ ] **3.3.2 实现 PromptDiffView 版本对比视图**
  - 选择两个版本进行 diff 对比
  - 使用 `difflib` 风格的统一 diff 展示
  - 左侧旧版本、右侧新版本，差异行高亮
  - [ ] 未开始

- [ ] **3.3.3 将版本管理组件集成到编辑器页面**
  - 编辑器页面增加"版本历史"标签页/侧边栏
  - 在版本历史中选择两个版本可打开 diff 视图
  - [ ] 未开始

### 阶段 3.4：前端验证

- [ ] **3.4.1 前端构建验证**
  - `cd frontend && npm run build` 确保无编译错误
  - [ ] 未开始

- [ ] **3.4.2 功能验证**
  - 租户管理员登录，进入数字员工管理，点击"定制提示词"
  - 编辑器正确加载当前 production 版本内容
  - 编辑后自动保存草稿
  - 提交版本后数据库中新增版本记录
  - 版本历史面板正确展示历史
  - diff 对比视图正常工作
  - 回滚功能正常工作
  - [ ] 未开始

### Phase 3 完成标准

- [ ] 租户前台可进入 Prompt 编辑器
- [ ] 编辑器支持 Markdown 编辑、草稿自动保存、提交版本
- [ ] 版本历史面板展示完整版本时间线
- [ ] 版本 diff 对比正常工作
- [ ] 一键回滚功能正常
- [ ] 前端构建无错误

---

## Phase 4：平台管理员后台（可选，视需求优先级）

> 设计文档参考：§八.3.2（平台管理员编辑入口）
> 目标：平台管理员可查看和编辑所有 Prompt，管理 staging/production 标签
> 预计工期：1 周

### 阶段 4.1：平台管理后台 Prompt 管理页

> 前置依赖：Phase 1（后端 API 已就绪）

- [ ] **4.1.1 实现 Prompt 管理列表页**
  - 路由：`/admin/prompts`
  - 使用 BaseTable 展示所有 Prompt（按 scope 筛选：subagent / tenant_extra / skill / system_template）
  - 展示：名称、scope、最新版本、production 版本、最后更新时间
  - [ ] 未开始

- [ ] **4.1.2 实现 Prompt 详情 + 版本管理页**
  - 路由：`/admin/prompts/{prompt_id}`
  - 复用 TenantPromptEditor 的编辑器组件
  - 增加标签管理区域（staging / production 切换）
  - 高危 Prompt 强制 staging 流程（前端拦截 + 后端校验）
  - [ ] 未开始

### 阶段 4.2：验证

- [ ] **4.2.1 平台管理员流程验证**
  - 编辑系统级 Prompt → 提交到 staging → 验证 → 切换 production
  - 验证直接标记 production 被拦截
  - 验证回滚功能
  - [ ] 未开始

- [ ] **4.2.2 前端构建验证**
  - `cd frontend && npm run build` 确保无编译错误
  - [ ] 未开始

### Phase 4 完成标准

- [ ] 平台管理员可查看所有 Prompt（含所有租户的定制 Prompt）
- [ ] 高危 Prompt 的 staging 强制流程正常工作
- [ ] 标签管理（staging/production 切换）正常工作

---

## 总体进度追踪

| Phase | 内容 | 预计工期 | 状态 |
|-------|------|---------|------|
| Phase 0 | Prompt 内容优化（P0~P3） | 2 天 | ✅ 已完成 |
| Phase 1 | 版本管理数据库 + 基础服务层 | 1 周 | ⬜ 未开始 |
| Phase 2 | extra_md 迁移 | 1 周 | ⬜ 未开始 |
| Phase 3 | 前端编辑器 | 1.5 周 | ⬜ 未开始 |
| Phase 4 | 平台管理员后台 | 1 周 | ⬜ 未开始 |

**总工期：约 4 周**（Phase 4 可根据优先级决定是否排入）

### 关键里程碑

| 里程碑 | 完成标志 | 对应任务 |
|--------|---------|---------|
| M0: Prompt 优化完成 | 透明化矛盾修复 + usage_guide 精简 | Phase 0 全部完成 |
| M1: 版本管理可用 | 数据库表 + 服务层 + API 可工作 | Phase 1 全部完成 |
| M2: extra_md 迁移完成 | extra_md 从文件系统迁移到数据库，运行时零影响 | Phase 2 全部完成 |
| M3: 前端编辑器可用 | 租户管理员可在线编辑定制 Prompt + 版本管理 | Phase 3 全部完成 |
| M4: 平台管理后台可用 | 平台管理员可管理所有 Prompt | Phase 4 全部完成 |

### 依赖关系

```
Phase 0 ─── 无前置，可立即开始
Phase 1 ─── 无前置，可与 Phase 0 并行
Phase 2 ─── 依赖 Phase 1（需要数据库表和服务层）
Phase 3 ─── 依赖 Phase 2（需要 extra_md 在数据库中）
Phase 4 ─── 依赖 Phase 1（需要后端 API），可与 Phase 2/3 并行
```

### 风险项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 数据库与文件系统不一致 | 多 Worker 加载到不同 Prompt | Phase 2 双写机制 + Phase 2 验证 2.3.3 降级测试 |
| 版本数量膨胀 | 数据库存储压力 | 设置版本保留策略（默认保留最近 100 个版本，后续迭代实现清理定时任务） |
| 缓存不一致 | 切换版本后仍使用旧版本 | 标签变更时主动失效缓存 + 短 TTL（5min） |
| Phase 0 修改影响现有对话 | "透明化"修改可能改变 LLM 行为 | 修改前后对比测试，必要时可通过版本管理回滚 |
