# 租户附件存储路径规范改造计划

> **规范依据**：[`.claude/rules/backend_dev.md` §租户附件存储规范](../../.claude/rules/backend_dev.md)
>
> **目标**：把全项目所有 `storage/uploads/{tenant}/{user}/` 旧路径统一改造为 `storage/tenants/{tenant_id}/{scene}/` 新规范，并通过一次性迁移脚本把历史文件搬到新路径。

## 背景

`.claude/rules/backend_dev.md` 第 430 行起规定：所有租户产生的附件（上传、生成、导出等）必须存放到 `storage/tenants/{tenant_id}/` 下，按业务场景建子目录（conversation / knowledge / export / report / avatar / temp / templates / data_sources 等）。

历史代码大量使用 `storage/uploads/{tenant_id}/{user_id}/`、`storage/uploads/{tenant}/templates/`、`storage/uploads/dingtalk/` 等非规范路径，需分阶段改造。

## 分阶段计划

### Phase 1：主入口 + 文档工具 conversation 场景 ✅ 已完成

**改造范围**：主入口 + 5 个文档工具的 conversation 场景路径解析。

**改动文件**：
- `src/main.py` - `_get_tenant_upload_dir` 改用 `ensure_tenant_storage_dir`；新增 `TENANTS_STORAGE_DIR`；`_get_file_info` 备份扫描覆盖新旧目录
- `src/tools/file/cp_tool.py` / `write_tool.py` - `_resolve_upload_dir` 走 `storage/tenants/{tid}/conversation/`
- `src/tools/word/word_lib.py` / `pdf/pdf_lib.py` / `excel/excel_lib.py` - `resolve_path` 新路径优先 + 旧路径备份 + 路径穿越守卫
- `src/tools/excel/excel_process_tool.py` - `_resolve_output_dir` 改造
- `tests/unit/tools/test_excel_process_tool_template.py` - 断言更新（user_id 不再进路径）

**测试**：`tests/unit/test_tenant_storage_paths.py`（21 个测试，含 3 个路径穿越用例）

**提交**：`5a8d4fa feat(storage): 租户附件存储路径规范改造 Phase 1 + 一次性迁移脚本`

---

### 一次性迁移模块 ✅ 已完成

**改造范围**：容器启动时自动把 `storage/uploads/` 下的旧文件搬到 `storage/tenants/{tid}/{scene}/`，同步更新 Redis 元数据。

**改动文件**：
- `src/core/storage_migration.py` - **新建**，迁移逻辑模块
  - 5 种旧路径变体识别（含 `_anonymous` 回退、knowledge 场景、渠道目录跳过）
  - 幂等：目标已存在 + 大小相同跳过；大小不同加 `_migrated_{uuid8}` 后缀
  - 多 worker 并发：`pg_try_advisory_lock(789012)`，acquire 返回 `(True, conn)` 持有 lock 的连接，release 用同一连接 unlock 后归还
  - Redis 元数据同步：扫 `uploaded_file:*` 建 path -> key 反向索引，迁移后更新 path 字段
  - 异常隔离：迁移失败 warning 不阻塞启动；Redis 不可用降级；单文件失败不阻塞后续
- `src/main.py` - lifespan 加 step2b 调用迁移（init_database 之后，master_agent 构造之前）

**迁移规则**：

| 旧路径 | 新路径 |
|--------|--------|
| `storage/uploads/conversation/{file}` | `storage/tenants/_anonymous/conversation/{file}` |
| `storage/uploads/tenant_{tid}/user_{uid}/{file}` | `storage/tenants/{tid}/conversation/{file}` |
| `storage/uploads/tenant_{tid}/knowledge/{file}` | `storage/tenants/{tid}/knowledge/{file}` |
| `storage/uploads/tenant_{tid}/{file}` | `storage/tenants/{tid}/conversation/{file}` |
| `storage/uploads/dingtalk/`、`wecom_kf/` | 跳过（渠道目录，由渠道系统自管） |

**测试**：`tests/unit/test_storage_migration.py`（19 个测试，含 advisory lock 契约测试）

**提交**：`5a8d4fa`（与 Phase 1 同一次提交）

---

### excel_process file_id 解析修复 ✅ 已完成

**Bug 背景**：Agent 传 `file_paths: ["file_xxx"]`（file_id）给 excel_process 工具，工具直接把 file_id 当磁盘路径用，报 "文件不存在"。

**改动文件**：
- `src/tools/excel/excel_lib.py`
  - 新增模块级 `_resolve_path_via_redis(file_id)`：查 Redis `uploaded_file:{file_id}` 的 path 字段
  - `ExcelFileHandler.resolve_path` 兜底扩展：原路径 -> Redis 元数据 -> `storage/tenants/{tenant}/conversation/` -> `storage/uploads/{tenant}/templates/`（新增）-> `storage/uploads/{file}`
- `src/tools/excel/excel_process_tool.py`
  - 新增 `_resolve_file(file_path)` 方法
  - 在 7 个 handler 入口（read/to_md/modify/format/fill_template/merge/convert）调用
- `tests/unit/tools/test_excel_process_resolve_file.py` - **新建**，11 个测试

**待提交**（截至 2026-08-13，尚未 push）

---

### Phase 2：知识库场景（knowledge）✅ 已完成

**改造范围**：知识库文档上传/存储路径。

**改动文件**：
- `src/knowledge/service.py` - `KnowledgeBaseService.__init__` 删除 `base_upload_path` 字段及 `knowledge_upload_path` 自定义死代码分支；`_get_upload_path` 整体改用 `ensure_tenant_storage_dir(tid, "knowledge")`，无租户走 `_anonymous`
- `src/api/travel_quote.py:45-62` - `_save_upload_to_storage` 从 `Path(settings.storage.uploads_dir) / tenant_id / "knowledge"` 改为 `Path(ensure_tenant_storage_dir(tenant_id, "knowledge"))`
- `tests/unit/test_tenant_storage_paths.py` - 末尾新增 `TestKnowledgeUploadPath` 测试类（5 个测试）
- `docs/system/file_usage.md:235` - 清理 `knowledge_upload_path` 文档残留

**关键点**：
- 知识库文件量大，迁移脚本（一次性迁移模块）已支持 `tenant_{tid}/knowledge/{file}` -> `tenants/{tid}/knowledge/{file}`，Phase 2 无需扩展
- 向量库元数据（`documents.file_path`）未触碰，旧记录的 `file_path` 失效由 `delete_document` 的 `os.path.exists` 静默兜底，业务无影响；Phase 6 收尾时统一处理
- `ExcelFileHandler.resolve_path` 已在 Phase 1 加过 `storage/tenants/{tenant}/knowledge/` 兜底，读取侧无需改

**测试**：`tests/unit/test_tenant_storage_paths.py` 全套 24 passed（19 原有 + 5 新增）

**三智能体流程**：
- 测试智能体：新功能 24 passed，unit 回归全绿，启动安全检查 OK；集成测试 `test_knowledge_endpoints.py` 4 failed + 3 errors 为预先存在的 `total_chunks` vs `total_chunk` DB schema 问题，与 Phase 2 无关
- CodeReview 智能体：无 P0/P1 问题，可安全提交

---

### Phase 3：数据分析场景（data_sources）🔧 未开始

**改造范围**：数据分析源文件持久化路径。

**待改文件**：
- `src/api/data_analysis.py:515,532,536` - `persist_dir = _project_root / settings.storage.uploads_dir / (tenant_id or "_global") / "data_sources"` 改为 `storage/tenants/{tenant}/data_sources/`

**关键点**：
- 无租户时回退 `_global`，需统一为 `_anonymous`（与 Phase 1 一致）
- 数据分析任务可能引用旧路径，需检查 `task_records` 表

**迁移规则补充**：`storage/uploads/{tenant}/data_sources/{file}` -> `storage/tenants/{tenant}/data_sources/{file}`（迁移脚本需新增此变体）

---

### Phase 4：子智能体模板文件（templates）🔧 未开始

**改造范围**：`/t/{tenant}/agent/{subagent}/prompt` 页面"模版文件"上传路径。

**待改文件**：
- `src/api/subagent_template_file.py:40,45-46,175-176` - `_templates_dir` 从 `UPLOAD_DIR / tenant_id / "templates"` 改为 `storage/tenants/{tenant}/templates/`

**关键点**：
- 模板文件是永久存储（Redis 元数据不 expire），迁移后必须同步更新 Redis path 字段
- `ExcelFileHandler.resolve_path` 已支持 `storage/uploads/{tenant}/templates/` 兜底（Phase 1 修复时加的），改造后读取侧自动跟随
- `subagent_template_file.py:175-176` 删除逻辑用 `glob(f"{file_id}*")` 扫旧目录，需同步改新目录

**迁移规则补充**：`storage/uploads/{tenant}/templates/{file}` -> `storage/tenants/{tenant}/templates/{file}`（迁移脚本需新增此变体）

---

### Phase 5：渠道媒体文件（dingtalk/feishu/wecom/wecom_kf）🔧 未开始

**改造范围**：渠道消息媒体文件（图片/语音/视频）持久化路径。

**待改文件**：
- `src/channels/dingtalk/adapter.py:67` + `media.py:41` - `./storage/uploads/dingtalk`
- `src/channels/feishu/adapter.py:67` + `media.py:87` - `./storage/uploads/feishu`
- `src/channels/wecom/adapter.py:61` + `media.py:37` - `./storage/uploads/wecom`
- `src/channels/wecom_kf/adapter.py:51` + `renderer.py:116,181` - `./storage/uploads/wecom_kf`

**关键点**：
- 渠道媒体文件**没有 tenant_id**（渠道消息在租户隔离之外），是否纳入 `storage/tenants/` 规范需讨论
- 选项 A：纳入规范，路径 `storage/tenants/_channel/{channel}/`（统一隔离）
- 选项 B：保持 `storage/uploads/{channel}/` 不动，规范豁免渠道目录（迁移脚本已跳过）
- **建议选 B**：渠道媒体文件由渠道系统自管，与租户附件语义不同
- 若选 B，本 Phase 仅需在 `.claude/rules/backend_dev.md` 补充"渠道目录豁免"说明，不动代码

---

### Phase 6：word_process_tool 同步修复 + 收尾 🔧 未开始

**改造范围**：word 工具同步 Phase 1 的 file_id 解析修复 + 全项目收尾。

**待改文件**：
- `src/tools/word/word_process_tool.py` - 8 处 `ctx.file_paths[0]` 直接当路径用（line 485/494/506/519/575/601/627/657），未调用 `WordFileHandler.resolve_path`。需补 `_resolve_file` 方法并在所有 handler 入口调用（与 excel_process 同结构）
- `src/tools/word/word_lib.py` - `WordFileHandler.resolve_path` 补 Redis 元数据命中分支（与 `ExcelFileHandler.resolve_path` 同结构）
- `src/core/skill_executor.py:735` - 检查 `Path.cwd() / settings.storage.uploads_dir` 是否需改造
- `src/saas/api/tenant_migration.py:121-123` - 租户迁移工具的路径返回值，检查是否需同步

**关键点**：
- word 的修复与 excel 完全同构，可复用 `_resolve_path_via_redis` 辅助函数（提到 `src/core/storage.py` 作为公共工具）
- 收尾时全项目 grep `storage.uploads` / `uploads_dir` / `UPLOAD_DIR`，确认无遗漏

---

## 当前状态总览

| 阶段 | 状态 | 提交 commit |
|------|------|------------|
| Phase 1 | ✅ 已完成 | `5a8d4fa` |
| 一次性迁移模块 | ✅ 已完成 | `5a8d4fa` |
| excel_process file_id 修复 | ✅ 已完成（待提交） | - |
| Phase 2 知识库 | ✅ 已完成 | - |
| Phase 3 数据分析 | 🔧 未开始 | - |
| Phase 4 模板文件 | 🔧 未开始 | - |
| Phase 5 渠道媒体 | 🔧 未开始（建议豁免） | - |
| Phase 6 word 同步 + 收尾 | 🔧 未开始 | - |

## 下次继续的入口

1. **优先级最高**：Phase 6 的 word_process_tool 同步修复（与 excel_process 同结构，可快速完成，修复同类 bug）
2. **优先级中**：Phase 2 知识库 + Phase 4 模板文件（影响实际业务，迁移脚本需补充变体）
3. **优先级低**：Phase 3 数据分析（单文件改造）+ Phase 5 渠道（建议豁免，仅补规范说明）

## 相关文档

- 规范：[`.claude/rules/backend_dev.md` §租户附件存储规范](../../.claude/rules/backend_dev.md)
- 规范：[`.claude/rules/database_dev.md` §租户附件存储](../../.claude/rules/database_dev.md)
- 规范：[`.claude/rules/backend_dev.md` §文件存储使用规范](../../.claude/rules/backend_dev.md)
- 工具函数：`src/core/storage.py`（`get_tenant_storage_path` / `ensure_tenant_storage_dir`）
- 迁移脚本：`src/core/storage_migration.py`
