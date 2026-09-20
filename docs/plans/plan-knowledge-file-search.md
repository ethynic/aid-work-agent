# 知识库文件搜索工具（knowledge_file_search）开发计划

> **背景**：2026-09-20 生产案例——租户管理员在数字员工提示词的「租户定制需求」中按文件名引用知识库文档（【数据分析助手·融合知识库.md】），LLM 忠实执行却只能用 `read` 工具拿标题当路径连猜 3 次（`数据分析助手·融合知识库.md` / `storage/tenants/{user_id}/...` / `knowledge/...`），全部失败后自纠改走 `analyze_data` 才成功。追踪无错误、回复正常，但浪费 3 轮工具调用且 F12 露出报错。
>
> **根因**：知识库文档的 LLM 可见名是 `documents.title`（原始上传文件名），落盘名是 `storage/tenants/{tenant_id}/knowledge/kb_{uuid12}.md`，二者无关联。`read` 只能读文件系统、不能按标题定位知识库文档；现有 `knowledge_base_search` 是语义检索（按内容相关度），不能按文件名精确定位。
>
> **临时方案**（已定）：联系租户管理员修改提示词措辞，去除按文件名读取的指令。
>
> **长期方案**（本计划）：新增 `knowledge_file_search` 工具，LLM 按原始文件名（模糊/精确）定位知识库文档，返回可直接喂给 `read` 的 `file_path`，形成「文件名定位 → 分页读取」闭环。

## 一、需求定义

| 项 | 说明 |
|----|------|
| 工具名 | `knowledge_file_search`（与现有 `knowledge_base_search` 命名风格对齐；前者按文件名定位文档，后者按内容语义检索） |
| 输入 | `file_name`（必填，支持模糊匹配）、`exact`（可选，默认 false）、`source_type`（可选过滤） |
| 输出 | 命中文档列表：`doc_id` / `title` / `file_path` / `file_size` / `file_type` / `source_type` / `summary` / `created_at` / 共享来源标注 |
| 使用方式 | LLM 先用本工具拿到 `file_path`（相对路径），再调用现有 `read` 工具分页读取内容（`read` 的路径校验允许项目根目录内的 `storage/tenants/...`，无需改动） |
| 不做什么 | 不返回全文（`raw_text` 单文档可达数十 KB，塞进工具结果会挤爆上下文）；不做内容语义检索（已有 `knowledge_base_search`）；不新增下载接口 |

## 二、与现有检索入口的关系（代码重用）

| 入口 | 检索维度 | 租户范围 | 使用方 |
|------|---------|---------|--------|
| `POST /api/knowledge/search_documents`（`src/knowledge/api.py` -> `KnowledgeService.search_documents`） | 内容语义检索（向量 + FTS5 + RRF，返回段落） | 本租户（global_view 放开），不含共享范围 | 知识库前端页面 |
| `knowledge_base_search` 工具 | 同上（同一 HybridRetriever，平行实现） | 本租户 + 共享范围（模式 A） | LLM |
| `knowledge_file_search`（本计划） | 文件名 -> 文档级定位（title 匹配，返回元数据 + file_path） | 本租户 + 共享范围 | LLM |

本工具与 `search_documents` 不重叠也不替代：语义检索回答「哪段内容相关」，按文件名搜索回答「有没有这个文件、路径在哪」。不能复用 `search_documents` 本身（输入输出不同），但复用其分层与部件，原则：**领域逻辑下沉 `KnowledgeService`，工具只做上下文适配薄壳**。

## 三、设计要点

### 3.1 分层与代码重用

- `KnowledgeService` 新增 `search_documents_by_title(tenant_id, file_name, shared_ranges, exact, source_type, limit)`：分档排序 SQL + 可见性条件都写在 service 层，复用 `self._get_db_connection` 连接管理与现有回查风格
- 工具类 `knowledge_file_search_tool.py` 只做三件事：从 `current_tool_execution_context()` 读 tenant/subagent、加载共享范围、调用 service 方法并格式化结果（含 `read_hint` / owner 标注）
- 共享范围加载：`KnowledgeBaseTool._load_shared_ranges` 私有方法提为 `src/knowledge/retriever/tenant_range.py` 公共函数（与权威函数 `load_shared_ranges` 同模块），新旧两工具共用，消除漂移
- 可见性 SQL 直接复用 `build_tenant_range_conditions` + `build_active_document_condition`
- owner 标注复用 `_attach_owner_metadata` 同样规则（一并提为公共函数）
- 未来若前端需要「知识库按文件名筛选」，直接加 API 端点调用同一 `search_documents_by_title`，与 `search_documents` 并列（模式 C 先例：travel_quote retriever 的 `shared_tenant_ids` 覆盖参数）
- 不扩大范围：本次不重构 `knowledge_base_search` 工具走 service（其与 service 的平行实现是历史债，另行处理）

### 3.2 工具实现（catalog=True 自动发现）

- 新文件 `src/tools/knowledge/knowledge_file_search_tool.py`，继承 `BaseTool`，Pydantic `InputModel` 定义参数 schema，无需改 `agent.py`（assembly 自动装配）
- `description` 写明使用场景引导：**「当系统提示词或用户提到知识库中的某个文件（按文件名/标题引用）时，先用本工具定位文件拿到 file_path，再用 read 工具读取内容；知识库文档不能用 read 直接按标题读取」**——这句话是防止本案例复发的关键
- **双工具防误用——互引 description（主防线）**：`knowledge_file_search` 与 `knowledge_base_search` 并存，LLM 可能误用。两个工具的 description 各写一句对方负责什么：
  - `knowledge_file_search`：「仅按文件名/标题定位文档；不知道文件名、想按问题找内容时，改用 knowledge_base_search」
  - `knowledge_base_search`（同步补一句）：「按内容语义检索段落；已知文件名/标题、要读整个文件时，先用 knowledge_file_search 定位再 read」
- **误用兜底（自愈闭环）**：内容关键词误调本工具 -> title 0 命中 -> 返回引导文案指路 `knowledge_base_search`（见 3.4）；反向误调 `knowledge_base_search` 搜文件名 -> 语义检索通常仍能命中该文档段落，功能不丢。双向代价均为 1 次多余工具调用，无功能性损失

### 3.3 租户范围与共享（按 knowledge_retrieval.md 检查清单）

按「模式 A」实现，虽然只查 `documents` 元数据、不动 `chunks`，可见性规则必须与内容检索完全一致：

- `tenant_id` / `subagent_id` 从 `current_tool_execution_context()` 读取
- 共享范围经公共化的 `load_shared_ranges` 从 DB 读取（不信任 LLM/前端传入）
- 主智能体（`subagent_id` 为空）退化为仅本租户
- SQL 可见性条件由 service 层统一拼装（见 3.4）

### 3.4 匹配与排序（service 方法 `search_documents_by_title`）

```sql
-- 单条 SQL，分档排序（精确命中优先）
SELECT id, title, file_path, file_size, file_type, source_type, summary,
       created_at, tenant_id
FROM documents
WHERE ({tenant_range_sql}) AND ({active_condition})
  AND title ILIKE %s
ORDER BY
  CASE WHEN title = %s THEN 0                -- 精确命中（去扩展名亦视为精确）
       WHEN title ILIKE %s THEN 1           -- 前缀命中
       ELSE 2 END,
  created_at DESC
LIMIT 10
```

- 模糊模式：`title ILIKE '%{file_name}%'`；精确模式（`exact=true`）：`title = file_name`，同时尝试去掉扩展名后比对（用户常传「融合知识库」而 title 是「xxx.md」）
- `source_type` 透传过滤；不传时搜全部类型（含 `[数据表]` 类元数据文档，返回中带 `source_type` 供 LLM 自行分辨）
- 0 命中时返回 `success: true, results: [], count: 0` 并附提示文案，引导 LLM 改用关键词或 `knowledge_base_search` 语义检索，而不是报错

### 3.5 返回的 file_path 与 read 衔接

- 直接返回 `documents.file_path` 原值（相对路径，如 `storage/tenants/923f70a485a1/knowledge/kb_98489e6135d1.md`）
- `read` 工具 `_resolve_path` 以项目根目录解析相对路径且校验在根目录内 → 无需改动 `read`；容器工作目录即项目根，链路成立
- 结果中附 `read_hint` 字段（如「可用 read(file_path=...) 分页读取此文件」），降低 LLM 理解成本

### 3.6 计费

纯 DB 元数据查询，无 LLM / Embedding / ASR 调用，**无计费点**。按 billing_audit.md §3 自查结论：不涉及。

## 四、开发任务

| # | 任务 | 涉及文件 | 说明 |
|---|------|---------|------|
| 1 | 提取公共函数 | `src/knowledge/retriever/tenant_range.py`、`src/tools/knowledge/knowledge_base_tool.py` | `_load_shared_ranges` 提为 tenant_range.py 公共函数、`_attach_owner_metadata` 同样公共化；`knowledge_base_search` 改为调用公共版，检索行为不变，仅补 description 互引句（3.2 防误用） |
| 2 | service 层新增按文件名搜索 | `src/knowledge/service.py` | `search_documents_by_title(tenant_id, file_name, shared_ranges, exact, source_type, limit)`：分档排序 SQL + `build_tenant_range_conditions` + `build_active_document_condition`，复用 `self._get_db_connection` |
| 3 | 实现 `knowledge_file_search` 工具薄壳 | `src/tools/knowledge/knowledge_file_search_tool.py` | InputModel + execute（读 ToolExecutionContext -> 加载共享范围 -> 调 service）+ read_hint + owner 标注 |
| 4 | 单元测试 | `tests/unit/tools/test_knowledge_file_search_tool.py` | 覆盖：精确/模糊命中、去扩展名匹配、0 命中引导文案、共享路径（subagent 命中共享范围）/ 无 subagent 退化本租户、active/过期文档不返回、LLM 传参容错 |
| 5 | 回归验证 | — | `knowledge_base_search` 公共化重构后行为不变；`pytest tests/unit/tools/ -k knowledge`；container/import 检查按 dev_workflow.md §5 |
| 6 | 文档更新 | `.claude/rules/knowledge_retrieval.md` §5 已实现入口表 | 登记本工具为模式 A 入口 |

## 五、风险与边界

| 风险 | 对策 |
|------|------|
| LLM 在 `knowledge_file_search` 与 `knowledge_base_search` 间误用 | 互引 description 明确分工（3.2，主防线）+ 0 命中引导文案自愈（3.4）；误用双向代价仅 1 次多余工具调用，无功能损失。上线后观察实际误用率，若偏高再评估合并或收紧工具可见范围 |
| LLM 拿 file_path 仍去读不存在的文件（file_path 落盘文件被清理） | `read` 失败自愈机制已有（warning + 报错自纠）；file_path 来自 DB 权威值，正常与磁盘一致 |
| title 相似文档多，噪声大 | 分档排序 + LIMIT 10 + 返回 summary 供 LLM 自选 |
| 跨租户越权 | 可见性 SQL 全部走 `build_tenant_range_conditions` + 共享范围交集，不信任 LLM 传入参数 |
| 主智能体滥用本工具读大文件挤上下文 | 工具只返回元数据不返回全文；`read` 有 2000 行上限与分页机制 |

## 六、验收标准

- 按文件名（含部分关键词、含/不含扩展名）能命中本租户知识库文档并拿到可用 file_path
- `read(file_path=返回值)` 能读到内容（集成人工验证一次）
- 共享知识库文档在子智能体上下文中可被搜到并带 owner 标注；主智能体看不到共享文档
- 生产复现场景：同样提示词 + 提问下，LLM 第一轮即调用 `knowledge_file_search` 定位文件，不再出现 3 连 read 失败
