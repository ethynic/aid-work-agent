# 租户数据迁移方案

## 背景

测试环境（`aid_work_agent2`）和生产环境（`aid_work_agent`）在同一 PostgreSQL 实例（124.222.3.254:5433），库名不同。租户在测试环境上传知识库耗时4小时，试用满意后需在生产环境重新上传，浪费时间和 token。

## 核心设计决策

### UUID 稳定标识符

当前所有知识库表和业务表只有 `SERIAL` 数字型自增主键，跨库迁移时 ID 必然变化。方案是为每张表新增 `uuid TEXT UNIQUE` 列（格式 `{prefix}_{uuid4().hex[:12]}`，如 `doc_a1b2c3d4e5f6`），迁移时通过 UUID 重建主外键对应关系，比逐行 `RETURNING id` 更高效、可批量操作。

**UUID 带来的优势**：
- 跨库迁移可批量 INSERT，通过 UUID 查询新生成的数字 ID，解除逐行处理的依赖链
- Excel 导出只导出 UUID，导入时通过 UUID 匹配（存在则更新，不存在则新增）
- 为后续所有跨环境、跨租户数据操作提供稳定的行标识

**涉及的表及 UUID 前缀**：

| 表 | UUID 前缀 | 说明 |
|----|----------|------|
| `documents` | `doc_` | 文档 |
| `chunks` | `chunk_` | 文本块 |
| `bs_travel_quote_vehicles` | `tqv_` | 车辆价格 |
| `bs_travel_quote_meals` | `tqm_` | 餐标价格 |
| `bs_travel_quote_guides` | `tqg_` | 导游费用 |
| `bs_travel_quote_fees` | `tqf_` | 其他费用 |
| `bs_travel_quote_seasons` | `tqs_` | 旅游季节 |

`chunks_vec` 不需要 UUID（其 PK `chunk_id` 引用 `chunks.id`，通过 `chunks.uuid` 间接定位）。

### 迁移模式

- **replace** (`--mode replace`)：先 `DELETE` 目标租户在相关表中的全部数据，再 INSERT。结果：目标 = 源 完全一致。
- **merge** (`--mode merge`)：保留目标租户已有数据，根据 UUID 去重（已存在则跳过，不存在则新增）。结果：目标 = 源 ∪ 目标。

不支持更新（UPDATE）模式 —— 如需更新已有记录内容，用户应先用 replace 模式重新迁移。

---

## 实现计划

### 阶段一：数据库 Schema 变更（UUID 列）

**修改文件**：
- `deploy/init-postgres.sql` — 在 `CREATE TABLE` 语句中增加 `uuid TEXT UNIQUE` 列
- `deploy/db_update.sql` — 增量 ALTER TABLE 添加列 + 回填已有数据的 UUID

**修改内容**（以 documents 为例）：

```sql
-- db_update.sql 新增：
ALTER TABLE documents ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE documents SET uuid = 'doc_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_uuid ON documents(uuid);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS uuid TEXT;
UPDATE chunks SET uuid = 'chunk_' || substring(md5(random()::text || id::text), 1, 12) WHERE uuid IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_uuid ON chunks(uuid);

-- 5张旅游报价表同理
ALTER TABLE bs_travel_quote_vehicles ADD COLUMN IF NOT EXISTS uuid TEXT;
-- ...
```

**同时更新 Python 代码中的表初始化**：
- `src/db/database.py` 中 `_init_postgresql()` — 确保 `CREATE TABLE IF NOT EXISTS` 包含 `uuid` 列
- `src/skills/travel-quote/scripts/db.py` 中 `TABLE_DEFINITIONS` — 为5张旅游表增加 `uuid` 列

**同时更新数据插入代码**（创建新记录时生成 UUID）：
- `src/knowledge/service.py` — `add_document()` 和 `add_chunks()` 中生成 `uuid`
- `src/api/travel_quote.py` — 各创建接口中生成 `uuid`
- `src/skills/travel-quote/scripts/db.py` — 如有直接 INSERT 的代码

### 阶段二：迁移脚本核心逻辑

**新建文件**：`scripts/tenant_migrate_kb.py`

独立的 Python 模块，既可作为 CLI 脚本运行，也可被 API 导入调用。使用两个直接 `psycopg2` 连接（不通过应用连接池）。

**公开函数**：
```python
def run_migration(
    source_db: str,        # 源数据库名，如 "aid_work_agent2"
    target_db: str,        # 目标数据库名，如 "aid_work_agent"
    source_tenant: str,    # 源租户ID
    target_tenant: str,    # 目标租户ID
    tables: list[str],     # 要迁移的表组：kb / knowledge_categories / travel_quote
    mode: str,             # "replace" 或 "merge"
    source_storage: str,   # 源存储根路径，如 "/app/source_storage"
    target_storage: str,   # 目标存储根路径，如 "/app/storage"
    dry_run: bool = False,
) -> dict:
    """返回 {success, summary: {table: count}, errors: [...]}"""
```

**迁移流程**（利用 UUID 批量操作）：

**Phase 0**: 连接校验、统计数据量。

**Phase 1**: `knowledge_categories` — 按 `(tenant_id, source_type)` upsert。

**Phase 2**: 知识库三表（`kb`）：
1. replace 模式下，先 `DELETE FROM chunks_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE tenant_id = $target))`，然后逐级删除 chunks、documents
2. merge 模式下，查询源库目标租户全部 documents，排除目标库已存在 UUID 的文档
3. `INSERT INTO documents` 批量插入 → 查询 `SELECT id, uuid FROM documents WHERE tenant_id = $target AND uuid = ANY($uuids)` 构建 `{uuid: new_id}` 映射
4. `INSERT INTO chunks` 批量插入（通过文档 UUID→new_id 映射替换 doc_id）→ 同样查询构建 `{uuid: new_id}` 映射
5. `INSERT INTO chunks_vec` 批量插入（通过 chunk UUID→new_id 映射替换 chunk_id）
6. `text_vec` 由目标库已有触发器自动生成，无需干预

**Phase 3**: 旅游报价5表（`travel_quote`）：
- 每表类似流程，但无外键依赖，更简单
- replace 模式：先 `DELETE FROM {table} WHERE tenant_id = $target`，再批量 INSERT
- merge 模式：按 UUID 去重后 INSERT

**文件复制**：
- 遍历源 documents 记录，拼接 `file_path`
- 源文件路径：`{source_storage}/{relative_path}`
- 目标文件路径：`{target_storage}/{relative_path}`（其中 `tenant_id` 替换为目标租户）
- `shutil.copy2()` 保留时间戳
- 同时更新目标 `documents.file_path`

**CLI 接口**（简化参数）：
```
python scripts/tenant_migrate_kb.py \
  --source-db aid_work_agent2 \
  --source-tenant tenant_abc123 \
  --tables kb,knowledge_categories,travel_quote \
  --mode replace \
  --dry-run
```

`--target-db` 和 `--target-tenant` 有默认值（当前环境变量中的数据库和租户），但也可显式指定。

### 阶段三：后端 API

**新建文件**：`src/saas/api/tenant_migration.py`

```python
router = APIRouter(prefix="/api/saas/tenants", tags=["租户数据迁移"])

@router.post("/{tenant_id}/migration/preview")
async def preview_migration(tenant_id: str, request: MigrationPreviewRequest):
    """预览迁移数据量"""
    return run_migration(..., dry_run=True)

@router.post("/{tenant_id}/migration/execute")
async def execute_migration(tenant_id: str, request: MigrationExecuteRequest):
    """执行迁移"""
    result = await asyncio.to_thread(run_migration, ...)
    return result
```

**请求参数**：
```python
class MigrationRequest(BaseModel):
    source_db: str = "aid_work_agent2"
    source_tenant: str
    tables: list[str] = ["kb", "knowledge_categories", "travel_quote"]
    mode: str = "replace"  # "replace" | "merge"
    source_storage: str = "/app/source_storage"
```

在 `src/main.py` 中注册路由。

### 阶段四：管理后台 UI — 数据迁移 Tab

**修改文件**：`frontend/src/components/saas/TenantMgmt.vue`

在编辑弹框已有两个 tab（"基本信息"、"数字员工授权"）基础上，新增第三个 tab "数据迁移"。

**Tab 内容**：
- 源数据库（下拉选择：`aid_work_agent2` / 自定义输入）
- 源租户 ID（文本输入）
- 迁移内容（多选复选框）：
  - 知识库文档（documents + chunks + chunks_vec）
  - 知识库分类（knowledge_categories）
  - 旅游报价数据（5张业务表）
- 迁移模式（单选）：替换 / 合并
- 操作按钮：
  - "预览" — 调用 preview API，显示各表数据量统计
  - "执行迁移" — 调用 execute API，显示进度和结果

**新建文件**（可选，如内容复杂则抽取组件）：
- `frontend/src/components/saas/TenantMigration.vue`

**新建 API 文件**：
- `frontend/src/api/saasTenantMigration.ts`

### 阶段五：业务表 Excel 导出

**新建后端通用导出工具**：`src/api/export_utils.py`

```python
def export_table_to_excel(
    table_name: str,
    tenant_id: str,
    columns: list[str],       # 导出列（不含数字id，含uuid）
    filename: str,
    where_clause: str = ""
) -> BytesIO:
    """通用单表Excel导出"""
```

**新建导出 API**：`src/api/business_export.py`

为每个需要导出的业务表提供一个端点：
```
GET /api/v1/travel-quote/vehicles/export?tenant_id=xxx
GET /api/v1/travel-quote/meals/export?tenant_id=xxx
GET /api/v1/travel-quote/guides/export?tenant_id=xxx
GET /api/v1/travel-quote/fees/export?tenant_id=xxx
GET /api/v1/travel-quote/seasons/export?tenant_id=xxx
GET /api/v1/knowledge/attractions/export?tenant_id=xxx   -- 景点（从documents表，source_type=attraction_resource）
GET /api/v1/knowledge/hotels/export?tenant_id=xxx        -- 酒店（从documents表，source_type=hotel_resource）
```

实际上，6个旅游页面中，4个（vehicles/meals/guides/fees）对应 `bs_travel_quote_*` 表，2个（attractions/hotels）对应 `documents` 表（`source_type='attraction_resource'/'hotel_resource'`）。

**前端**：为6个旅游管理页面各添加"导出"按钮：

| 页面 | 组件 | API |
|------|------|-----|
| 车辆价格 | `VehicleManager.vue` | `/travel-quote/vehicles/export` |
| 餐标价格 | `MealManager.vue` | `/travel-quote/meals/export` |
| 导游费用 | `GuideManager.vue` | `/travel-quote/guides/export` |
| 其他费用 | `FeeManager.vue` | `/travel-quote/fees/export` |
| 景点门票 | `AttractionManager.vue` | `/knowledge/attractions/export` |
| 酒店房型 | `HotelManager.vue` | `/knowledge/hotels/export` |

导出 Excel 包含列：除 `id`（数字主键）外的所有业务列，**包含 `uuid`**。

**后续推广**：所有业务数据列表页都加入"导出"按钮，使用同一个 `export_table_to_excel` 工具函数。

### 阶段六：业务表 Excel 导入（方案B增强）

导入时通过 UUID 匹配：
- UUID 在目标表中已存在 → 更新该行数据
- UUID 不存在 → 新增行（由目标库 SERIAL 自动生成数字 ID）

此阶段为后续工作，本期先完成导出功能。

---

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `deploy/init-postgres.sql` | 修改 | CREATE TABLE 增加 uuid 列 |
| `deploy/db_update.sql` | 修改 | ALTER TABLE 增加 uuid 列 + 回填 |
| `src/db/database.py` | 修改 | `_init_postgresql()` 中表初始化含 uuid |
| `src/skills/travel-quote/scripts/db.py` | 修改 | TABLE_DEFINITIONS 增加 uuid 列 |
| `src/knowledge/service.py` | 修改 | 创建文档/chunk 时生成 uuid |
| `src/api/travel_quote.py` | 修改 | 创建记录时生成 uuid；新增导出端点 |
| `scripts/tenant_migrate_kb.py` | **新建** | 迁移核心逻辑（CLI + 可被API调用） |
| `src/saas/api/tenant_migration.py` | **新建** | 迁移 API 端点 |
| `frontend/src/components/saas/TenantMgmt.vue` | 修改 | 新增"数据迁移" tab |
| `frontend/src/components/saas/TenantMigration.vue` | **新建** | 数据迁移 tab 内容组件 |
| `frontend/src/api/saasTenantMigration.ts` | **新建** | 前端迁移 API 调用 |
| `src/api/export_utils.py` | **新建** | 通用 Excel 导出工具函数 |
| `frontend/src/components/travel/VehicleManager.vue` | 修改 | 新增"导出"按钮 |
| `frontend/src/components/travel/MealManager.vue` | 修改 | 新增"导出"按钮 |
| `frontend/src/components/travel/GuideManager.vue` | 修改 | 新增"导出"按钮 |
| `frontend/src/components/travel/FeeManager.vue` | 修改 | 新增"导出"按钮 |
| `frontend/src/components/travel/AttractionManager.vue` | 修改 | 新增"导出"按钮 |
| `frontend/src/components/travel/HotelManager.vue` | 修改 | 新增"导出"按钮 |

---

## 部署注意事项

1. **源存储目录可访问性**：生产容器需额外挂载测试环境的 storage 目录。在 `docker-compose.prod.yml` 中增加：
   ```yaml
   - /var/www/qb3_upload/agent2_storage:/app/source_storage
   ```
   使迁移脚本可在容器内通过 `/app/source_storage` 访问测试环境文件。

2. **UUID 回填**：`db_update.sql` 中的 UPDATE 语句使用 `md5(random()::text || id::text)` 生成伪随机 hex 字符串，在百万行级别可能需要几秒到几十秒，建议在低峰期执行。

3. **向量数据量**：`chunks_vec.embedding` 是 `vector(1024)` 类型，批量传输时使用 `execute_batch()` 分批处理，避免内存溢出。

---

## 验证方法

1. 在开发环境执行 `db_update.sql` 确认 UUID 列添加和回填成功
2. 运行 `python scripts/tenant_migrate_kb.py --dry-run` 验证数据统计正确
3. 用两个测试租户执行完整迁移，对比源/目标各表 COUNT(*)
4. 在目标环境测试知识库搜索，验证向量检索正常
5. 验证文件复制：检查目标 storage 目录下文件存在且可下载
6. 测试 Excel 导出：各旅游管理页面点击导出，检查文件内容和格式
