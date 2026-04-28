# 租户模式上传文件隔离方案

## 背景

当前企业知识库文件上传后存放在 `uploads/knowledge/` 目录下，没有租户隔离。需要实现租户模式下按 `{tenant_id}` 隔离文件存储，演示模式维持原目录。

## 目录结构

```
uploads/
├── knowledge/                    # 演示模式（无租户）
│   ├── kb_xxxx.docx
│   └── ...
├── knowledge/{tenant_id}/        # 租户模式
│   ├── kb_xxxx.docx
│   └── ...
├── file_xxxx.docx                # 演示模式通用上传
├── {tenant_id}/                  # 租户模式通用上传
│   ├── file_xxxx.docx
│   └── ...
└── wecom/                        # 企业微信媒体（不变）
```

## 需要租户隔离的上传点（3 处）

| # | 文件 | 当前路径 | 租户模式路径 |
|---|------|----------|-------------|
| 1 | `src/knowledge/api.py:95` | `./uploads/knowledge/` | `./uploads/knowledge/{tenant_id}/` |
| 2 | `src/main.py:259,445` | `./uploads/` | `./uploads/{tenant_id}/` |
| 3 | `src/tools/file/register_download_tool.py:102` | 复制到 `uploads/` | 复制到 `uploads/{tenant_id}/` |

## 无需租户隔离的上传点（2 处）

| # | 文件 | 原因 |
|---|------|------|
| 4 | `src/channels/wecom/media.py` | 企业微信按企业天然隔离 |
| 5 | `src/saas/services/skill_resolver.py` | 已按 `storage/tenants/{tenant_id}/skills/` 隔离 |

## 实施步骤

### 步骤 1：documents 表增加 tenant_id 列

**文件**: `src/db/database.py` (init_postgres_tables)

- 在 `CREATE TABLE documents` 中增加 `tenant_id TEXT` 列
- 增加索引 `idx_documents_tenant ON documents(tenant_id)`

### 步骤 2：知识库上传路径隔离

**文件**: `src/knowledge/api.py`

- 导入 `get_current_tenant_id`
- `upload_document` 中根据 `get_current_tenant_id()` 决定 upload_dir：
  - 有 tenant_id → `./uploads/knowledge/{tenant_id}/`
  - 无 tenant_id → `./uploads/knowledge/`（演示模式）

### 步骤 3：知识库服务增加 tenant_id

**文件**: `src/knowledge/service.py`

- `upload_document()`: 增加 `tenant_id` 参数，INSERT 时写入 `tenant_id`
- `list_documents()`: 按 `tenant_id` 过滤
- `count_documents()`: 按 `tenant_id` 过滤
- `search_documents()`: 按 `tenant_id` 过滤（传给 HybridRetriever）
- `upload_path` 改为方法 `get_upload_path(tenant_id)`，不再固定初始化
- `delete_document()`: 删除后清理空目录

### 步骤 4：通用文件上传隔离

**文件**: `src/main.py`

- `upload_file()`: 根据 `get_current_tenant_id()` 决定保存目录
- `delete_uploaded_file()`: 删除后清理空目录
- `_get_file_info()`: 磁盘扫描时也搜索租户子目录

### 步骤 5：下载注册工具隔离

**文件**: `src/tools/file/register_download_tool.py`

- 导入 `get_current_tenant_id`
- `execute()` 中根据 tenant_id 决定 `dest_path`

## 注意事项

1. **数据库**: 新增列对已有数据无影响（tenant_id 为 NULL 表示演示模式数据）
2. **Nginx**: 无需修改，路径仍在 `/uploads/` 下
3. **前端下载**: 通过 API 端点下载（file_path 从 DB 读），无需改前端
4. **空目录清理**: 删除文件后，如果父目录为空则删除目录，避免长期运行留下空目录
