# 聊天附件 Excel/CSV 自动注册到知识库

> **状态**: 🔧 部分完成
> **关联功能**: 数据分析智能体 (#9)
> **创建日期**: 2026-06-09

---

## 背景

当前 `analyze_data` 工具只能分析已注册到知识库的表。用户在聊天中发送 Excel/CSV 附件时，需要先通过前端数据分析页面手动上传、确认 schema、保存，然后才能在对话中要求分析。这个流程割裂，用户期望直接在聊天中上传附件并完成分析。

## 方案

### 整体架构

新增 `upload_data_file` 工具，LLM 在检测到用户发送 Excel/CSV 附件并要求分析时自动调用：

```
用户消息 + 附件 → agent 注入文件路径到消息
  → LLM 识别为 Excel/CSV 数据分析请求
  → 调用 upload_data_file(file_path, analysis_intent)
  → 工具返回注册成功的表信息
  → LLM 调用 analyze_data(requirement="用户的分析需求")
  → AnalysisAgent 搜索知识库，发现新注册的表，加载并分析
```

### 重构：Schema 保存共享服务

从 `src/api/data_analysis.py` 提取 schema 保存逻辑为共享服务 `src/services/data_analysis/schema_saver.py`：

- `generate_schema_text()` — 生成 schema 描述文本（用于嵌入）
- `save_schema_to_knowledge()` — 完整保存流程（嵌入 → documents → chunks → 向量），新增去重逻辑

**去重逻辑**：插入前按 `tenant_id` + `metadata->>'table_name'` + `metadata->>'source_info'` 查询是否已存在同名同源 schema，存在则跳过并返回已有 doc_id。

### 新工具：UploadDataFileTool

**文件**: `src/tools/data_analysis/upload_data_tool.py`

| 属性 | 值 |
|------|------|
| name | `upload_data_file` |
| InputModel | `file_path: str`, `analysis_intent: str` |
| category | `data_analysis` |

**execute() 流程**：

1. 验证文件存在 + 扩展名（.xlsx/.xls/.csv）
2. 获取 tenant_id（从 `current_tenant_id` ContextVar）
3. `SheetParser.parse_file(file_path)` 解析（`asyncio.to_thread`）
4. 对每个 sheet 调用 `SchemaExtractor.extract_schema()` 推断 schema
5. 对每个 sheet 调用 `save_schema_to_knowledge()` 保存到知识库
6. 返回 `{success, tables: [{sheet_name, table_name, rows, columns}], message}`

### 修改文件

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `src/services/data_analysis/schema_saver.py` | 共享 schema 保存服务 |
| 新建 | `src/tools/data_analysis/upload_data_tool.py` | 附件数据文件注册工具 |
| 修改 | `src/api/data_analysis.py` | 重构 save_schema/update_schema 使用共享服务 |
| 修改 | `src/core/agent.py` | 注册新工具 |

### 复用组件

- `SheetParser`（`src/services/data_analysis/sheet_parser.py`）— Excel/CSV 解析
- `SchemaExtractor`（`src/services/data_analysis/schema_extractor.py`）— LLM schema 推断
- `TextEmbeddingV3Client` — 向量嵌入
- `get_db_connection` — 数据库连接
- `get_vector_db` — 向量存储
