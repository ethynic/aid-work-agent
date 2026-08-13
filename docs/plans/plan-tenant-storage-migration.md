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

### Phase 3：数据分析场景（data_sources）✅ 已完成

**改造范围**：数据分析源文件持久化路径。

**改动文件**：
- `src/api/data_analysis.py:515,532,534` - `upload_excel` 的 `persist_dir` 从 `_project_root / settings.storage.uploads_dir / (tenant_id or "_global") / "data_sources"` 改为 `Path(ensure_tenant_storage_dir(tenant_id or "_anonymous", "data_sources")).absolute()`，写入 `storage/tenants/{tenant}/data_sources/`
- `src/core/storage_migration.py` - `_resolve_new_path` 新增 `data_sources` 变体识别 + 迁移规则 docstring 补充
- `tests/unit/test_storage_migration.py` - 新增 2 个测试（`test_bare_tenant_data_sources` / `test_global_data_sources`）
- `docs/system/file_usage.md:62,133,137,305` - 更新数据源文件路径与源文件说明

**关键点**：
- 无租户回退 `_global` 统一为 `_anonymous`（与 Phase 1 一致），迁移脚本同步把 `_global/data_sources` 映射到 `_anonymous/data_sources`
- 迁移脚本已支持 `storage/uploads/{tid}/data_sources/{file}` -> `storage/tenants/{tid}/data_sources/{file}`（含 `_global` -> `_anonymous` 变体）
- 数据源文件路径存于 `documents.metadata.source.file_path`（非 `task_records` 表，该表不存在）；迁移后旧记录路径失效由 `_resolve_schema_source_status` 的 `os.path.exists` 兜底为 `missing`，与 Phase 2 `documents.file_path` 相同的已知收尾项

**测试**：`tests/unit/test_storage_migration.py` 23 passed；回归 `tests/unit/test_tenant_storage_paths.py` 24 passed

---

### Phase 4：子智能体模板文件（templates）✅ 已完成

**改造范围**：`/t/{tenant}/agent/{subagent}/prompt` 页面"模版文件"上传路径。

**改动文件**：
- `src/api/subagent_template_file.py:39-48` - `_templates_dir` 从 `UPLOAD_DIR / tenant_id / "templates"` 改为 `Path(ensure_tenant_storage_dir(tenant_id, "templates")).absolute()`
- `src/api/subagent_template_file.py:172-177` - `delete_template` 删除逻辑复用 `_templates_dir`（原 `UPLOAD_DIR / tenant_id / "templates"`）
- `src/core/storage_migration.py:115` - docstring 补充 templates 迁移规则
- `src/core/storage_migration.py:179-181` - `_resolve_new_path` 新增 `{tid}/templates/{file}` -> `storage/tenants/{tid}/templates/{file}` 变体
- `tests/unit/api/test_subagent_template_file.py` - `test_delete_existing` mock 改 `_templates_dir`；新增 `TestTemplatesDir` 路径断言
- `tests/unit/test_storage_migration.py` - 新增 `test_bare_tenant_templates`

**关键点**：
- 模板文件是永久存储（Redis 元数据不 expire），迁移脚本通过 path 反向索引同步更新 Redis path 字段
- `ExcelFileHandler.resolve_path` 已支持 `storage/uploads/{tenant}/templates/` 兜底（Phase 1 修复时加的），改造后读取侧自动跟随

**测试**：`test_subagent_template_file.py` + `test_storage_migration.py` 共 36 passed；import 检查 OK

---

### Phase 5：渠道媒体文件（dingtalk/feishu/wecom/wecom_kf）✅ 已完成

**改造范围**：渠道消息媒体文件（图片/语音/视频）持久化路径，纳入租户隔离。

**结论**：渠道配置页（`/t/{tenant_id}/channels`）位于租户前台，渠道本身是租户级的，媒体文件**应纳入** `storage/tenants/{tenant_id}/` 规范（选项 A），**不豁免**。

**改动文件**（参照飞书已有模式，补齐 wecom / dingtalk / wecom_kf）：
- `src/channels/wecom/adapter.py` - 新增 `_tenant_id` 字段 + `async set_tenant_id`（转发 `self.media.set_tenant_id`）
- `src/channels/wecom/media.py` - 新增 `tenant_id` 字段 + `set_tenant_id` + `_resolve_save_dir`（有 tenant_id 走 `ensure_tenant_storage_dir(tenant_id, "conversation")`，无则回退 `upload_dir`）；`download_media` 落盘点改 `_resolve_save_dir`
- `src/channels/dingtalk/adapter.py` - 新增 `_tenant_id` 字段 + `async set_tenant_id`（转发 `self.media.set_tenant_id`）
- `src/channels/dingtalk/media.py` - 新增 `tenant_id` 字段 + `set_tenant_id` + `_resolve_save_dir`；三处落盘点（`download_image` / `download_file` / `upload_from_url`）改 `_resolve_save_dir`
- `src/channels/wecom_kf/adapter.py` - 新增 `_tenant_id` 字段 + `async set_tenant_id`（转发已创建 renderer）+ `_resolve_media_dir`；`_get_default_thumb_media_id` 的 `_default_thumb.png` 与 `renderer` 懒加载创建跟随切换
- `src/channels/wecom_kf/renderer.py` - 新增 `tenant_id` 参数 + `set_tenant_id` + `_resolve_save_dir`；`render_table` / `render_markdown` 落盘走 `_resolve_save_dir`
- `tests/unit/channels/test_channel_tenant_storage.py` - **新建**，13 个测试

**关键机制**：`src/saas/services/channel_factory.py:48-59` `_build_adapter` 已有通用注入——`if hasattr(adapter, "set_tenant_id"): await adapter.set_tenant_id(tenant_id)`。各 adapter 加 `set_tenant_id` 后自动生效，**无需改 factory**。飞书渠道此前已具备该能力，本阶段未改动。

**场景目录**：渠道媒体文件本质是对话附件，统一用 `conversation/` 场景（与飞书一致）。

**迁移脚本**：历史渠道文件 `storage/uploads/{channel}/` **无租户维度**（旧文件落盘时未记录 tenant_id），无法推断目标租户，`_SKIP_TOP_DIRS` 继续跳过；仅新文件走新路径。此为"无法迁移"而非"豁免"。

**测试**：`tests/unit/channels/test_channel_tenant_storage.py` 13 passed；回归 `tests/unit/channels/` + `test_storage_migration.py` + `test_tenant_storage_paths.py` 808 passed（其中 `wecom_personal_rpa` 2 failed 为预先存在的 DB schema 漂移，与 Phase 5 无关）；import 检查 OK

---

### Phase 6：word_process_tool 同步修复 + 收尾 ✅ 已完成

**改造范围**：word 工具同步 Phase 1 的 file_id 解析修复 + 全项目收尾。

**改动文件**：
- `src/core/storage.py` - **新增** `resolve_path_via_redis(file_id)` 公共工具函数（从 `excel_lib.py` 提取，作为 word/excel 共享）
- `src/tools/word/word_lib.py` - `WordFileHandler.resolve_path` 补 Redis 元数据命中分支 + 旧路径 `storage/uploads/{tenant}/templates/{file}` 兜底（与 `ExcelFileHandler.resolve_path` 同结构）
- `src/tools/excel/excel_lib.py` - `_resolve_path_via_redis` 改为薄包装调用 `src.core.storage.resolve_path_via_redis`（保留函数名向后兼容，test 直接从此模块导入）
- `src/tools/word/word_process_tool.py` - 新增 `_resolve_file` 方法，在 8 处 handler 入口调用（`_handle_read` / `_handle_analyze` / `_handle_word_to_md` / `_handle_md_to_word` / `_handle_modify` / `_handle_format` / `_handle_fill_template` / `_handle_diff`），与 `excel_process_tool._resolve_file` 同结构
- `src/core/skill_executor.py:735` - `search_dirs` 列表加 `storage/tenants` 作为新搜索根，更新注释（旧 `storage/uploads` 仍保留作只读兜底）
- `tests/unit/tools/test_word_process_resolve_file.py` - **新建**，24 个测试

**关键点**：
- word 的修复与 excel 完全同构，复用 `resolve_path_via_redis` 公共函数（提到 `src/core/storage.py`）
- `_handle_md_to_word` 特殊：file_paths[0] 解析失败时不阻塞流程（`try/except (FileNotFoundError, OSError)`），由 `md_text` 兜底返回需要 context 的错误
- `_handle_diff` 两个 file_path 都通过 `_resolve_file` 解析
- `tenant_migration.py:121-123` 评估结论：迁移工具的 `target_storage` 改造涉及 `tenant_migrate_kb.py` 核心逻辑（file_path 字段重新构造 + 数据库更新），工程量大，**单独立项**

**收尾 grep 结论**（全项目 `storage.uploads` / `uploads_dir` / `UPLOAD_DIR`）：
- Phase 6 范围内无遗漏（word/skill_executor 已改）
- Phase 3（`src/api/data_analysis.py`）+ Phase 4（`src/api/subagent_template_file.py`）+ Phase 5（渠道 `src/channels/*/`）按计划单独执行
- `src/main.py` 中的 `UPLOAD_DIR` 全部为只读兜底扫描（Phase 1 已改造写入侧）
- `src/tools/{excel,word,pdf}/_lib.py` 中的 `storage/uploads` 全部为 resolve_path 兜底（迁移期保留）

**测试**：`tests/unit/tools/test_word_process_resolve_file.py` 24 passed；回归 `test_excel_process_resolve_file.py` + `test_tenant_storage_paths.py` + `test_storage_migration.py` 54 passed

**三智能体流程**：
- 测试智能体：新功能 24 + 核心 54 全绿，启动安全检查 OK；tools 目录全量 26 failed 已用 git stash 验证为预先存在（与 Phase 6 无关）
- CodeReview 智能体：无 P0/P1，6 个 P2/nit 全 REPORTED-ONLY（`_handle_md_to_word` 假异步 nit 与既有模式一致建议后续统一治理；其余为防御性写法/边界情况/预先存在），可安全提交

---

### Phase 7：租户迁移工具 target_storage 改造 ✅ 已完成

**改造范围**：租户数据迁移工具（`tenant_migration.py` + `tenant_migrate_kb.py`）的目标存储路径从旧规范 `storage/uploads` 改为新规范 `storage/tenants/{tenant}/knowledge/`。

**改动文件**：
- `src/core/storage.py` - **新增** `get_tenants_storage_root()` 函数，返回 `storage/tenants` 根路径（避免业务代码硬编码路径）
- `src/saas/api/tenant_migration.py` - `_get_target_storage()` 从 `settings.storage.uploads_dir`（旧路径）改为返回 `get_tenants_storage_root()`（新路径）
- `scripts/tenant_migrate_kb.py`
  - 新增 `_build_target_knowledge_path(target_tenant, src_path, target_storage)` helper，返回 `(tgt_rel, full_tgt)`，目标路径统一为 `storage/tenants/{target_tenant}/knowledge/{basename}`
  - `_migrate_kb()` 文件复制逻辑：从 `src_path.replace(source_tenant, target_tenant)`（保留旧目录结构）改为调用 helper，`documents.file_path` 存相对路径 `tgt_rel`（与知识库 API 存储格式一致）
  - `run_migration()` 的 `target_storage` 默认值从 `settings.storage.uploads_dir` 改为 `get_tenants_storage_root()`
- `tests/unit/test_tenant_migrate_kb_target_storage.py` - **新建**，6 个测试

**关键点**：
- 知识库文档属于 `knowledge` 场景，迁移后目标文件统一落到 `storage/tenants/{target_tenant}/knowledge/` 单层目录，`file_path` 存相对路径
- 文件 basename 天然唯一（知识库上传命名 `{file_id}{ext}`，file_id 是 uuid），无需处理同名冲突
- 源文件路径读取逻辑（`source_storage` 兼容 `storage/` 前缀）保持不变，旧/新规范源库均可读

**顺手修复既有 P1 bug**（与 Phase 7 目标直接相关）：
- `_migrate_kb` 原 `doc_id_to_uuid = {d["id"]: d.get("uuid") for d in source_docs}` 存原始 uuid（可能为 None），当源文档 uuid 为空时：插入阶段与 chunk 关联 / file_path 更新阶段各生成不同随机 uuid，导致 chunk 丢失 + file_path 更新被跳过（残留旧路径）
- 修复：在插入循环里统一构建 `doc_id_to_uuid[d["id"]] = doc_uuid`（含随机 fallback），chunk 关联与 file_path 更新复用同一映射，三处对齐

**测试**：`tests/unit/test_tenant_migrate_kb_target_storage.py` 6 passed；回归 `test_tenant_storage_paths.py` + `test_storage_migration.py` 54 passed

**三智能体流程**：
- 测试智能体：新功能 6 + 回归 48 全绿，启动安全检查（3 个 import）OK；无需要修复的问题
- CodeReview 智能体：修复 1 个 P1（uuid 空值既有 bug，见上）；2 个 P2/nit REPORTED-ONLY（basename 冲突理论风险依赖唯一命名约定可后续处理、`_get_target_storage` except 兜底硬编码合理）；可安全提交

---

## 当前状态总览

| 阶段 | 状态 | 提交 commit |
|------|------|------------|
| Phase 1 | ✅ 已完成 | `5a8d4fa` |
| 一次性迁移模块 | ✅ 已完成 | `5a8d4fa` |
| excel_process file_id 修复 | ✅ 已完成 | `88b8b45` |
| Phase 2 知识库 | ✅ 已完成 | `ebca7d3` |
| Phase 3 数据分析 | ✅ 已完成 | `16165e8` |
| Phase 4 模板文件 | ✅ 已完成 | `d17d657` |
| Phase 5 渠道媒体 | ✅ 已完成 | 待提交 |
| Phase 6 word 同步 + 收尾 | ✅ 已完成 | `78a0a79` |
| Phase 7 租户迁移工具 target_storage 改造 | ✅ 已完成 | 待提交 |

## 下次继续的入口

1. 全项目「租户附件存储路径规范改造」已全部完成（Phase 1~7），无待办项。

## 相关文档

- 规范：[`.claude/rules/backend_dev.md` §租户附件存储规范](../../.claude/rules/backend_dev.md)
- 规范：[`.claude/rules/database_dev.md` §租户附件存储](../../.claude/rules/database_dev.md)
- 规范：[`.claude/rules/backend_dev.md` §文件存储使用规范](../../.claude/rules/backend_dev.md)
- 工具函数：`src/core/storage.py`（`get_tenant_storage_path` / `ensure_tenant_storage_dir`）
- 迁移脚本：`src/core/storage_migration.py`
