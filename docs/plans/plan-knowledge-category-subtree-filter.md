# 知识库多级分类「子级文档包含」统一开发计划

> 状态：📋 计划定稿待开发（2026-08-27）
> 背景：知识库页面文档分类已由 1 级扩展为任意级树形（`knowledge_categories.parent_id` + `documents.sub_category`，2026-08-25 落地），但分类文档数统计与文档列表过滤在「顶级分类」与「子分类」之间语义不统一：顶级分类显示/统计其下所有子级文档，而二级分类既不显示也不统计其下三级文档。
> 关联方案：[知识库能力增强设计](../system/knowledge-base/knowledge-base-enhancement-design.md)（本次为分类树形化的文档归属语义统一修复）

## 一、问题

### 1.1 现状（行为不一致）

| 操作 | 顶级分类（一级） | 子分类（二级及以上） |
|------|-----------------|---------------------|
| 点击分类，右侧文档列表 | ✅ 显示其下所有子级分类的文档 | ❌ 不显示其下子级分类的文档（如二级不显示三级文档） |
| 分类右侧文档数 `document_count` | ✅ 包含其下所有子级分类的文档数 | ❌ 不含其下子级分类的文档数（如二级不含三级文档） |

### 1.2 根因

数据模型：`documents.source_type` 恒为**顶级分类**代号，`documents.sub_category` 存**直接所属分类**代号（三级文档的 `sub_category` 就是三级分类代号，不记录中间二级），分类树关系在 `knowledge_categories.parent_id`。

因此现有查询天然不对称：

1. **文档数统计**（`src/knowledge/service.py:233-264` `list_categories`）：
   - 顶级分类：`COUNT(documents WHERE source_type = 顶级)` → 因所有子级文档都带顶级 `source_type`，天然包含全部子级 ✓
   - 子分类：`COUNT(documents WHERE sub_category = 本分类)` → 只匹配直接归属，不含后代 ✗
2. **文档列表过滤**（`src/knowledge/service.py:619-676` `list_documents` / `584-617` `count_documents`）：
   - 点顶级分类：前端只传 `source_type` → 包含全部子级 ✓
   - 点子分类：前端传 `source_type=顶级 AND sub_category=本分类`（`frontend/web/components/KnowledgeBase.vue:782-788` `handleCategorySelect`）→ 不含后代 ✗

## 二、方案（用户 2026-08-27 确认方案 A）

**统一语义：每一级分类都显示并统计其下所有子级（含自身）的文档。**

即「点击任意分类看到的文档数 = 该分类下含全部子级分类的文档总数」，与顶级分类现状对齐，全部收口到后端实现，前端零改动。

### 2.1 子级分类展开（核心）

新增辅助方法，对任意分类返回「自身 + 所有后代分类的 source_type」集合：

```python
def _get_category_subtree_source_types(self, tenant_id: str, category_source_type: str) -> List[str]:
    """返回 category_source_type 自身 + 其所有后代分类的 source_type 列表。
    供 list_categories / list_documents / count_documents 共用，保证三处语义一致。
    实现：一次 SELECT 该租户全部分类 (source_type, parent_id)，内存建树后 DFS 收集后代。"""
    with self._get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, source_type, parent_id FROM knowledge_categories WHERE tenant_id = %s",
            (tenant_id,)
        )
        rows = cursor.fetchall()
    by_id = {r["id"]: r for r in rows}
    # 以 id -> children 建树（children 存 source_type），DFS 收集自身+后代
    ...
```

分类表数据量小（单租户几十条），一次查询 + 内存递归，开销可忽略。

### 2.2 三处查询统一

| 位置 | 改动 |
|------|------|
| `list_categories` document_count | 子分类分支（`parent_id IS NOT NULL`）：`sub_category IN (自身 + 后代)`；顶级分支保持 `source_type = 顶级` 不变 |
| `list_documents` | `sub_category` 非空时展开为 `sub_category IN (自身 + 后代)` |
| `count_documents` | 同上，与 `list_documents` 保持一致 |

### 2.3 前端

**零改动**。`handleCategorySelect` 已对子分类传 `source_type=顶级 AND sub_category=本分类`，后端展开后天然包含后代文档。

### 2.4 设计决策：不引入 `all_descendants` 字段

用户补充意见：`knowledge_categories` 增加 `all_descendants`（所有子级）字段，分类增删改时重算本租户所有分类的该字段。

**结论：不采用。** 理由：

1. **无性能收益**：单租户分类量几十条，实时计算（2.1 的一次全表查询 + 内存建树）在 1ms 以下；`documents` 按 `sub_category IN (...)` 过滤已有 `idx_documents_sub_category(tenant_id, sub_category)` 索引，真正瓶颈不在分类展开。
2. **复杂度与一致性风险**：加字段需表结构变更（init-postgres.sql + db_update.sql + 存量回填）+ 增删改时重算。而重算本身同样是一次全表取 + 建树，与实时计算成本相同，只是把成本从读请求挪到写请求；且任一写入口漏重算即产生过期数据。
3. **实时计算天然一致**：无写路径维护负担、无迁移、无历史数据回填。

若未来某租户分类量达到数千级别且分类页高频访问，可再评估物化该字段，本计划不预埋。

## 三、改动点

| 文件 | 改动 |
|------|------|
| `src/knowledge/service.py` | 新增 `_get_category_subtree_source_types`；`list_categories` 子分类计数改 `sub_category IN (子树)`；`list_documents` / `count_documents` 的 `sub_category` 过滤展开为子树集合 |
| `tests/integration/test_knowledge_categories_tree.py` | 新增用例：二级分类 `document_count` 含三级文档；点二级分类文档列表含三级文档；点三级分类只含自身文档（三级无子级时 `IN (自身)` 等价现有行为） |

**无需改动**：前端（`KnowledgeBase.vue`）、`knowledge_categories` / `documents` 表结构、`deploy/init-postgres.sql`、`deploy/db_update.sql`。

## 四、验证方式

1. **集成测试**：`tests/integration/test_knowledge_categories_tree.py` 新增用例全绿 + 既有 9 用例回归（现有 `test_document_count_top_includes_subcategory_docs`、`test_list_documents_filter_by_sub_category` 语义保持不变）。
2. **真实页面核对**（部署后）：建 1→2→3 级分类，分别挂文档（顶级直接挂 + 二级挂 + 三级挂），核对：
   - 一级文档数 = 自身 + 二级 + 三级文档数，点一级列表显示全部
   - 二级文档数 = 自身 + 三级文档数，点二级列表显示自身 + 三级文档
   - 三级文档数 = 自身文档数
3. **交叉验证**：点分类后的文档数与列表实际条数一致（`document_count` 与 `list_documents` 的 `total` 对齐）。

## 五、风险与回退

- **语义变化**：二级分类文档数/列表从「仅自身」变为「含三级」，属本次修复预期，无历史数据迁移需求（查询逻辑改动，数据不变）。
- **一致性**：三处查询共用同一个展开方法，避免 `list_categories` 与 `list_documents` 口径分叉。
- **回退**：改动集中在 `service.py` 单文件查询逻辑，`git revert` 即回基线。

## 六、关联文档

- [知识库能力增强设计](../system/knowledge-base/knowledge-base-enhancement-design.md)（分类树形化设计，2026-08-25）
- 知识库能力增强开发计划（docs/ideas.md 条目 5，分类树形化部分）
