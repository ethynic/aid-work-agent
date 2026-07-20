# 数据源导入功能 — 开发计划

> 对应设计文档：[数据分析智能体设计文档](./data-analysis-subagent-design.md) §三（Excel 智能解析）、§四（数据连接器）、§五（Schema 审核与管理）
> 关联功能索引：[docs/ideas_finished.md](../../ideas_finished.md) 数字员工 / 子智能体 #9a
> 创建日期：2026-06-03
> 状态：✅ 已完成开发（2026-07-20 经用户真实使用验证）

---

## 范围说明

### 这是什么

数据源导入是一个**通用的知识库扩展功能**，核心能力：

1. **上传 Excel/CSV 文件** → 自动解析 Sheet → LLM 推理 Schema → 用户审核确认 → 存入知识库
2. **连接外部数据库** → 选择表导入 → LLM 推理 Schema → 用户审核确认 → 存入知识库
3. **管理已导入的表** → 查看/编辑/删除 Schema、管理表间关联关系

导入结果存储在知识库中（`source_type='data-analysis-metadata'`），可被配置了该 source_type 的子智能体检索使用。

### 独立性

本功能**不依赖数据分析智能体**。完成后，任何子智能体只要在 `SUBAGENT.md` 中配置：

```yaml
business_pages:
  - id: data-sources
    title: 数据源管理
    icon: "📊"
    route: /data-sources
knowledge_base:
  source_types:
    - data-analysis-metadata
```

即可在侧边栏看到"数据源管理"菜单入口，使用数据源导入功能。

### 不在本计划范围内

- `SmartDataAnalysisTool`（数据分析工具集）—— 见 [智能数据分析工具设计](./smart-data-analysis-tool-design.md)
- 子智能体定义（`SUBAGENT.md`）—— 等分析工具就绪后再创建
- `DataFrameStore`（DataFrame 缓存）—— 属于分析执行阶段，导入阶段只需存 Schema

---

## 路由设计

### 页面路由

| 路由 | 页面 | 说明 |
|------|------|------|
| `/data-sources` | `DataSourceManager.vue` | 数据源管理主页面（独立模式） |
| `/t/:tenant_id/data-sources` | 同上 | 数据源管理主页面（租户模式，嵌在 PortalLayout 中） |

路由在 `frontend/src/main.ts` 中注册，与现有 business_pages 路由模式一致（参考 travel-consultant 的 vehicles/attractions 等）。

### 菜单入口

通过子智能体的 `business_pages` 配置自动出现在 `MenuSidebar` 中，与现有业务数据页面（景点管理、客户管理等）同级的分组下显示。

---

## 开发阶段

### Phase 1：数据库 + 后端 API（预计 1 周）

> 目标：数据连接器 CRUD + Excel 上传解析 + Schema 保存/管理 API

#### 1.1 数据库表

| 文件 | 操作 |
|------|------|
| `deploy/db_update.sql` | 追加 `data_connectors` 表 DDL |
| `deploy/init-postgres.sql` | 追加相同 DDL |

`data_connectors` 表结构（来自设计文档 §4.2）：

```sql
CREATE TABLE IF NOT EXISTS data_connectors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL,
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,
    options JSONB,
    is_active BOOLEAN DEFAULT TRUE,
    imported_tables JSONB DEFAULT '[]',
    last_sync_at TIMESTAMP,
    created_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> 注意：Schema 数据不建独立表，复用 `documents` 表，`source_type='data-analysis-metadata'`。

#### 1.2 后端核心模块

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `src/services/data_analysis/__init__.py` | 新建 | 模块入口 |
| 2 | `src/services/data_analysis/sheet_parser.py` | 新建 | SheetParser — Excel/CSV 格式校验 + DataFrame 提取 |
| 3 | `src/services/data_analysis/schema_extractor.py` | 新建 | SchemaExtractor — LLM Schema 推理 + 跨表关联推断 |
| 4 | `src/services/data_analysis/db_connector.py` | 新建 | DatabaseConnector — SQLAlchemy 统一数据库连接器 |
| 5 | `src/services/data_analysis/crypto.py` | 新建 | 密码 AES-Fernet 加密/解密 |
| 6 | `src/api/data_analysis.py` | 新建 | FastAPI 路由：连接器 CRUD + 表导入 + Schema 管理 + 关联关系管理 |
| 7 | `src/main.py` | 修改 | 注册新 router |

#### 1.3 API 端点清单

**连接器管理：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data-analysis/connectors` | 创建连接器（含连接测试） |
| GET | `/api/data-analysis/connectors` | 列出连接器 |
| PUT | `/api/data-analysis/connectors/:id` | 更新连接器 |
| DELETE | `/api/data-analysis/connectors/:id` | 删除连接器 |
| POST | `/api/data-analysis/connectors/test` | 测试连接（创建前预览） |
| POST | `/api/data-analysis/connectors/:id/test` | 测试已保存的连接器 |
| GET | `/api/data-analysis/connectors/:id/tables` | 获取远程数据库表列表 |
| POST | `/api/data-analysis/connectors/:id/import` | 导入选中的表（DDL + 样本 → LLM Schema 推理） |

**Excel 上传：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data-analysis/upload` | 上传 Excel/CSV → SheetParser 校验 → LLM Schema 推理 → 返回 Schema 供前端审核 |

> 注意：此接口**不直接写入知识库**。LLM 推理的 Schema 返回给前端，用户审核确认后调用 `POST /schemas` 写入。

**Schema 管理：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data-analysis/schemas` | 保存 Schema（用户确认后调用 → 写入知识库） |
| GET | `/api/data-analysis/schemas` | 列出已保存的 Schema |
| PUT | `/api/data-analysis/schemas/:doc_id` | 更新 Schema（重新向量化） |
| DELETE | `/api/data-analysis/schemas/:doc_id` | 删除 Schema |

**关联关系：**

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/data-analysis/relations/infer` | LLM 自动推断关联关系（返回建议，不直接保存） |
| POST | `/api/data-analysis/relations/batch` | 批量保存关联关系 |
| GET | `/api/data-analysis/relations` | 列出所有关联关系 |
| POST | `/api/data-analysis/relations` | 添加关联关系 |
| DELETE | `/api/data-analysis/relations` | 删除关联关系 |

#### 1.4 阶段验收标准

- [x] 连接器 CRUD 可用，密码加密存储
- [x] 连接测试返回数据库版本信息
- [x] Excel/CSV 上传 → SheetParser 校验 → LLM Schema 推理 → 返回 JSON
- [x] Schema 保存接口写入 documents + chunks + chunks_vec
- [x] Schema 更新接口触发重新向量化
- [x] 关联关系推断、保存、列表、删除均可工作
- [x] 所有接口带租户隔离（tenant_id 过滤）

---

### Phase 2：前端页面（预计 1.5 周）

> 目标：数据源管理页面 + 数据连接器管理弹窗 + Schema 审核弹窗 + 关联关系管理

#### 2.1 前端文件

| # | 文件 | 操作 | 说明 |
|---|------|------|------|
| 1 | `frontend/src/api/dataSource.ts` | 新建 | API 层（连接器 + Schema + 关联关系 + 上传） |
| 2 | `frontend/src/pages/DataSourceManager.vue` | 新建 | 数据源管理主页面 |
| 3 | `frontend/src/main.ts` | 修改 | 注册路由 `/data-sources` 和 `/t/:tenant_id/data-sources` |

> 页面遵循 [page_patterns.md](../../.claude/rules/page_patterns.md) 规范，使用 BaseTable / BaseModal / BaseButton / BaseInput / BaseSelect 组件。

#### 2.2 页面结构

`DataSourceManager.vue` 包含以下区域（标签页切换）：

```
┌─────────────────────────────────────────────────┐
│  AppHeader: 数据源管理                            │
├─────────────────────────────────────────────────┤
│                                                 │
│  [已注册数据表]  [数据连接器]  ← 标签页切换       │
│                                                 │
│  ┌─ 已注册数据表（默认标签页）──────────────────┐ │
│  │ [搜索表名]              [+ 上传 Excel/CSV]   │ │
│  │                                            │ │
│  │ 序号 | 表名 | 来源 | 行×列 | 关联 | 操作    │ │
│  │  1  | 销售明细 | Excel | 200×6 | 1条 | 编辑 删除 │
│  │  2  | 客户表   | MySQL | 1.2k×15 | 0条 | 编辑 删除 │
│  └────────────────────────────────────────────┘ │
│                                                 │
│  ┌─ 数据连接器标签页 ─────────────────────────┐  │
│  │ [+ 新建连接器]                              │  │
│  │ 序号 | 名称 | 类型 | 主机 | 状态 | 表数 | 操作│  │
│  │  1  | ERP库 | mysql | 10.0.0.1 | ● | 5 | ...│  │
│  └────────────────────────────────────────────┘  │
│                                                 │
│  [管理关联关系] ← 独立弹窗按钮                   │
└─────────────────────────────────────────────────┘
```

#### 2.3 核心交互流程

**Excel 上传流程：**

```
点击"上传 Excel/CSV" → 文件选择
  → POST /upload → 返回解析结果（Sheet 列表 + 每个 Sheet 的 Schema）
  → 打开 Schema 审核弹窗（逐个 Sheet 审核）
    → 用户确认/修正列信息、表描述
    → POST /schemas → 写入知识库
  → 刷新已注册数据表列表
```

**数据库表导入流程：**

```
数据连接器标签页 → 点击"导入" → 打开远程表列表弹窗
  → GET /connectors/:id/tables → 显示表列表（带已导入标记）
  → 勾选表 → 点击"导入"
    → POST /connectors/:id/import → LLM Schema 推理
    → 打开 Schema 审核弹窗（逐个表审核）
    → 用户确认后保存
  → 刷新已注册数据表列表
```

**Schema 审核弹窗（Excel 和数据库共用）：**

```
┌─ Schema 审核 ──────────────────────────────────┐
│  📊 销售明细表（来源：销售报表.xlsx / Sheet1）  │
│  共 200 行，6 列                                │
│                                                │
│  表名: [销售明细表          ] ← 可编辑          │
│  描述: [记录各区域各产品线的销售额...]           │
│                                                │
│  [列信息]  [关联关系]  ← 标签页                 │
│                                                │
│  列名   | 语义名   | 类型    | 描述 | 枚举值    │
│  区域   | [销售区域] | text ▾ | [...] | 华东,... │
│  销售额 | [销售金额] | decimal | [...] |        │
│                                                │
│  [跳过]                   [确认入库]            │
└────────────────────────────────────────────────┘
```

#### 2.4 阶段验收标准

- [x] 路由注册正确，租户模式和独立模式均可访问
- [x] Excel/CSV 上传 → Schema 审核 → 保存到知识库全流程可用
- [x] 数据连接器 CRUD + 连接测试 + 远程表列表 + 导入全流程可用
- [x] 已注册数据表列表展示、编辑 Schema、删除
- [x] 关联关系管理（自动推断 + 手动添加/编辑/删除）
- [x] 所有 API 调用带 `X-Tenant-Id` header（使用 `getAuthHeader()`）
- [x] 页面遵循 Base* 组件 + 语义 token 颜色规范
- [x] 前端构建无错误

---

### Phase 3：集成测试 + 配置到子智能体（预计 0.5 周）

> 目标：端到端测试 + 文档更新

#### 3.1 测试

| 测试类型 | 范围 |
|---------|------|
| 后端单元测试 | SheetParser 格式校验、SchemaExtractor JSON 解析、DatabaseConnector DDL 拼装、crypto 加密/解密 |
| 后端集成测试 | 连接器 CRUD API、Schema 保存/更新 API、关联关系 API |
| 前端构建 | `npm run build` 通过 |

#### 3.2 文档更新

| 文件 | 更新内容 |
|------|---------|
| `docs/ideas.md` | 更新 #9 数据分析智能体状态为 🔧 部分完成，注明数据源导入部分完成 |
| `docs/system/digital-employee/data-analysis-subagent-design.md` | 更新最近更新日期 |

#### 3.3 阶段验收标准

- [x] 后端单元测试通过
- [x] 后端集成测试通过（`tests/integration/test_data_analysis_integration.py` 覆盖上传→解析→入库→分析全链路）
- [x] 前端构建通过
- [x] 端到端手动验证：上传 Excel → 审核 Schema → 查看已注册表 → 编辑关联关系（2026-07-20 用户真实使用验证通过）
- [x] 文档已更新

---

## 文件变更总览

| # | 文件 | 操作 | Phase |
|---|------|------|-------|
| 1 | `deploy/db_update.sql` | 追加 data_connectors DDL | 1 |
| 2 | `deploy/init-postgres.sql` | 追加 data_connectors DDL | 1 |
| 3 | `src/services/data_analysis/__init__.py` | 新建 | 1 |
| 4 | `src/services/data_analysis/sheet_parser.py` | 新建 | 1 |
| 5 | `src/services/data_analysis/schema_extractor.py` | 新建 | 1 |
| 6 | `src/services/data_analysis/db_connector.py` | 新建 | 1 |
| 7 | `src/services/data_analysis/crypto.py` | 新建 | 1 |
| 8 | `src/api/data_analysis.py` | 新建 | 1 |
| 9 | `src/main.py` | 修改（注册 router） | 1 |
| 10 | `frontend/src/api/dataSource.ts` | 新建 | 2 |
| 11 | `frontend/src/pages/DataSourceManager.vue` | 新建 | 2 |
| 12 | `frontend/src/main.ts` | 修改（注册路由） | 2 |
| 13 | `docs/ideas.md` | 更新状态 | 3 |

---

## 风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| LLM Schema 推理不稳定 | 返回格式不一致导致解析失败 | Prompt 加 strict JSON 约束 + 异常捕获兜底 |
| 数据库连接器兼容性 | 特定数据库方言不支持 | 首期仅 MySQL/PostgreSQL，按需扩展 |
| Excel 大文件内存占用 | 影响服务稳定性 | 文件大小限制 + 行数限制 |
| Metadata 知识库与文档知识库混搜 | 表文档和普通文档混淆 | 检索时用 `source_type` 过滤 |
| 密码加密密钥管理 | 密钥泄露风险 | 环境变量存储，不入代码仓库 |
