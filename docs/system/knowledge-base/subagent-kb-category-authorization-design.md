# 子智能体知识库栏目授权硬隔离与数据表移动兜底设计

> 日期：2026-09-20
> 状态：已实施（P1，2026-09-21）
> 关联：[租户知识库共享设计](tenant-knowledge-sharing-design.md)、[知识库检索规范](../../../.claude/rules/knowledge_retrieval.md)、[知识库文件搜索工具开发计划](../../plans/plan-knowledge-file-search.md)

## 1. 背景

2026-09-20 生产排查发现两个问题，同源但独立成题：

**案例 1：数据分析专家搜不到已注册的数据表**
- tenant_c148f4efb4dc 14:21 上传 Excel，2 张表 schema 注册成功（`source_type='data-analysis-metadata'`）；14:22 用户在知识库界面把这 2 条 `[数据表]` 文档移动到分类 `k_cf06ec9b497b`；15:19 用户提问「上个月各渠道销售总额」，`analyze_data` 内层 `search_data_tables` / `list_data_tables` 按 `source_type='data-analysis-metadata'` 过滤查到 0 张表，回复「找不到数据」。
- 根因：`move_documents`（`src/knowledge/service.py:770`）移动文档时直接覆盖 `source_type`，而数据表发现/加载三处 SQL 都硬性要求 `source_type='data-analysis-metadata'`。

**案例 2：数字员工授权栏目可被绕过（软引导）**
- 测试反馈：租户 tenant_e9b2fab93a2f 给「数据分析专家」只勾选一级栏目「数据分析」，但子智能体仍能读取「全部」分类下的文件。
- 根因：`subagent_knowledge_sources` 中本租户自有栏目项（`owner_tenant_id` 为空）只用于注入 system prompt（`src/core/agent.py:796-811`），提示 LLM「调用时必须传入正确的 source_type 参数」，属**软引导**；检索工具的 `source_type` 参数由 LLM 传参决定，不传时 `build_tenant_range_conditions` 退化为 `tenant_id = %s`，即本租户**全部分类**可见。跨租户共享侧才是硬隔离（精确 `(from_tenant_id, source_type)` 对）。

## 2. 现状机制梳理

### 2.1 source_type 的双重语义

`documents.source_type` 同时承担：
1. **知识库分类标识**：本租户顶级分类代号（`knowledge_categories.source_type`），未分类文档落 `'file'`（`service.py:557`）；前端「全部」是虚拟聚合视图，不是真实分类。
2. **特殊功能来源**：`'data-analysis-metadata'`（数据分析表 schema）、`'data-analysis-relations'`（表关系）等。

移动文档（`/api/knowledge/documents/move`）会把它从特殊来源覆盖成目标分类代号，反之也可能把普通文档移成任何分类。

### 2.2 授权数据模型：subagent_knowledge_sources

每租户每子智能体一行，`sources` JSON 数组混存两类项：

| 项类型 | 判别 | 语义 |
|---|---|---|
| 本租户自有栏目项 | `owner_tenant_id` 为空/缺失 | 「数字员工授权-知识库」弹框勾选的栏目，当前仅作提示词引导 |
| 跨租户共享项 | `owner_tenant_id` 非空 | 数字员工级共享启用清单，经 `load_shared_ranges` ∩ 租户级授权（`tenant_knowledge_shares`）后成为硬隔离的精确对 |

### 2.3 检索可见性现状（三入口）

| 入口 | 本租户侧 | 共享侧 | 栏目授权过滤 |
|---|---|---|---|
| `knowledge_base_search`（knowledge_base_tool.py） | LLM 传 `source_type` 则限栏目，否则全部 | 精确对 | ❌ 无 |
| `knowledge_file_search`（knowledge_file_search_tool.py） | 同上 | 精确对 | ❌ 无 |
| `analysis_agent`（search_data_tables / list_data_tables / load_table） | 硬编码 `source_type='data-analysis-metadata'` | 精确对 | ❌ 无（也不该有，见 §4.4） |

## 3. 授权语义（产品定义，2026-09-20 确认）

**知识库授权栏目为空 = 允许读取所有栏目（默认，兼容存量配置）；需要精细化管理时，至少勾选一个栏目，勾选后仅允许读取勾选栏目。**

本租户侧可见性矩阵：

| 自有授权栏目 | LLM 传 source_type | 本租户侧可见范围 | 说明 |
|---|---|---|---|
| 空（未配置） | 未传 | 全部栏目（含未分类 `'file'`） | 现状不变 |
| 空（未配置） | 传 X | 栏目 X | 现状不变 |
| [A, B] | 未传 | A + B | **新增：收窄为授权栏目集合** |
| [A, B] | 传 A | A | 正常 |
| [A, B] | 传 C（未授权） | 拒绝，返回可用栏目清单供 LLM 自纠 | **新增** |

共享侧不受自有授权影响，恒为 `load_shared_ranges` 精确对（数字员工级启用 ∩ 租户级授权，撤销立即生效）。

## 4. 设计

### 4.1 权威读取函数（tenant_range.py）

新增：

```python
def load_authorized_source_types(
    tenant_id: Optional[str],
    subagent_id: Optional[str],
) -> Optional[List[str]]:
    """读取本租户自有授权栏目集合（subagent_knowledge_sources 中 owner_tenant_id 为空的项）。

    返回 None 表示未配置（允许全部栏目，默认语义）；
    返回列表（可为空列表？否，空数组同样视为未配置）表示仅允许列表内栏目。
    仅子智能体 + 租户模式生效；主智能体 / 无租户上下文返回 None（不受限）。
    """
```

实现要点：
- 复用 `subagent_knowledge_sources` 现有数据，**零 schema 变更、零迁移**；
- 「空 = 全部」的判定：自有项数组为空（或该租户无此行）→ `None`；注意要先把共享项（`owner_tenant_id` 非空）剔除再判空；
- 单独函数而非塞进 `load_shared_ranges`，保持两个关注点分离（共享授权 vs 栏目授权），调用方按需组合。

### 4.2 工具层收口规则（两工具统一）

在 `knowledge_base_search` / `knowledge_file_search` 的 `execute` 内，读上下文拿到 `tenant_id` + `subagent_id` 后：

```
authorized = load_authorized_source_types(tenant_id, subagent_id)
if authorized is not None:            # 已配置精细授权
    if llm_source_type 未传:
        source_type 不再透传给 build_tenant_range_conditions，
        改为「授权集合 IN 过滤」：source_type = ANY(%s)
        （共享侧 shared_ranges 照旧精确对叠加）
    elif llm_source_type in authorized:
        正常透传
    else:
        拒绝并返回：{success: False, error: "栏目 X 未授权",
                     authorized_categories: [...], note: "请改用以上栏目重新检索"}
if authorized is None:                # 未配置，行为与现状完全一致
    现状逻辑不动
```

- 「传了未授权栏目」选择**显式拒绝**而非静默收窄：让 LLM 能感知边界并自纠，同时便于日志排查（检索日志已打印 source_type，追加 authorized 便于审计）；
- 拒绝属正常业务响应，不计错误日志级别，`logger.info` 即可。

### 4.3 生效入口与分期

| 分期 | 入口 | 说明 |
|---|---|---|
| P1 | `knowledge_base_search` / `knowledge_file_search` | 高频主入口，本方案核心 |
| P1 | `analysis_agent` 数据表发现 | 仅加「移动兜底」放宽（§4.4），**不套栏目授权**（豁免，见下） |
| P2 | 模式 B 直接 SQL 入口（attraction_search_tool / hotel_search_tool） | 单一固定分类工具，授权收窄对它们是「授权集合是否包含该分类」的布尔判断，逻辑简单但涉及旅游报价业务回归，单独排期 |
| P2 | 模式 C 独立 API（travel_quote 等） | 同上 |

### 4.4 数据表移动兜底（analysis_agent，豁免栏目授权）

**豁免理由**：数据表是数据分析功能的内置可见范围，凭标题前缀 `[数据表]` + `metadata.table_name/columns` 判定归属，不是用户栏目浏览行为；若套用栏目授权，「授权了数据分析栏目、但表被移动到其他栏目」会再次触发案例 1。共享侧仍维持精确对精度。

改动点（`src/tools/data_analysis/analysis_agent.py`）：

1. **`_query_data_tables_list`（list_data_tables）**：
   ```sql
   WHERE ( source_type = 'data-analysis-metadata' {tenant_sql}            -- 原范围，含共享租户
        OR ( tenant_id = %s AND title ILIKE '[数据表] %' ) )               -- 新增：本租户内被移动的表
   ```
   本租户侧按标题前缀兜底；共享侧不放宽（避免把来源租户未授权分类搜进来，符合 knowledge_retrieval.md 模式 B 陷阱约定）。
2. **`_fetch_single_table_metadata`（load_table）**：同样放宽——`(source_type = 'data-analysis-metadata' {tenant_sql}) OR (id = %s AND tenant_id = %s AND title ILIKE '[数据表] %')`，metadata 缺 `table_name`/`columns` 的普通同名文档靠 metadata 判定排除。
3. **`_handle_search_data_tables`（search_data_tables）0 命中 fallback**：语义检索受 `source_type` 过滤影响对被移动表失效，0 命中且 `list_data_tables`（放宽后）非空时，将放宽列表的表名/描述融入返回结果并附 hint，引导 LLM 直接 `load_table`。
4. **`schema_saver.save_schema_to_knowledge` 去重条件放宽**（`schema_saver.py:122-131`）：现按 `source_type='data-analysis-metadata' AND metadata->>table_name` 去重，被移动的表会绕过去重造成重复注册。改为 `title = '[数据表] ' || table_name AND metadata->>'source_info' = %s`（不再限定 source_type，租户内判重）。

**与栏目授权的关系**：`knowledge_file_search` 作为 LLM 层逃生通道照常受栏目授权约束；被移动出授权栏目的数据表由本节 analysis_agent 内部兜底覆盖，职责不重叠。

### 4.5 system prompt 注入更新（agent.py）

`_build_base_system_prompt` 注入「可用知识库」段落（agent.py:796-811）区分两种语义：

- 授权为空：`你可以检索本租户全部知识库栏目（未配置栏目授权）。`
- 授权非空：`你只能检索以下知识库栏目（其余栏目未授权，检索会被拒绝）：` + 清单；共享项照旧列出来源公司标注。

### 4.6 前端文案（数字员工授权-知识库弹框）

授权弹框顶部补一句说明：「不勾选任何栏目时，该数字员工可读取本租户全部知识库栏目；勾选后仅可读取勾选栏目（跨租户共享的栏目不受此限制）。」

## 5. 兼容性与边界

| 场景 | 行为 |
|---|---|
| 主智能体（`subagent_id` 为空） | `load_authorized_source_types` 返回 None，行为不变 |
| 无租户上下文（后台任务 / platform_admin 全局视图） | 同上，不受限 |
| 存量租户（sources 无自有项） | 全部视为「未配置」，行为与现状一致，零迁移 |
| 未分类文档（`source_type='file'`） | 授权为空时可读；精细授权时需显式勾选「未分类」对应的 file 栏目（弹框中该栏目与前端「不分类」展示对齐） |
| 移动后的 `[数据表]` 文档 | 数据分析智能体仍可发现/加载（§4.4 豁免）；知识库栏目视图中正常展示 |
| 重复上传同一 Excel | schema_saver 去重放宽后不再重复注册 |

## 6. 涉及文件清单

| 文件 | 改动 |
|---|---|
| `src/knowledge/retriever/tenant_range.py` | 新增 `load_authorized_source_types` |
| `src/tools/knowledge/knowledge_base_tool.py` | 授权收口（收窄 / 校验拒绝） |
| `src/tools/knowledge/knowledge_file_search_tool.py` | 同上 |
| `src/tools/data_analysis/analysis_agent.py` | 表发现/加载放宽 + 语义检索 fallback |
| `src/services/data_analysis/schema_saver.py` | 去重条件放宽 |
| `src/core/agent.py` | 「可用知识库」注入区分授权空/非空 |
| `frontend/web/components/saas/TenantMgmt.vue`（授权弹框） | 默认语义说明文案 |
| `.claude/rules/knowledge_retrieval.md` | 新增「本租户栏目授权」章节 + 检查清单项 |

## 7. 测试要点

- **单元**：`load_authorized_source_types`（无行 / 空自有项 / 仅共享项 / 混合项）；
- **单元**：两工具传参矩阵 5 例（§3 表逐行）——重点「授权非空 + 未传参收窄」「传未授权拒绝并返回可用清单」；
- **单元**：analysis_agent 放宽查询——被移动的 `[数据表]` 可被 list/load；metadata 缺 table_name 的同名文档不误报；
- **单元**：schema_saver 去重——同名同源表在 source_type 变更后仍判重跳过；
- **集成**：共享范围 × 栏目授权叠加（本租户收窄 + 共享精确对同时生效）；
- **回归**：授权为空的租户全部入口行为与现状一致（测试同学场景反向验证）；
- **端到端**：复现案例 1 全链路（上传 Excel → 移动 `[数据表]` 文档 → 提问分析 → 能出结果）。

## 8. 开放问题

1. 管理后台「数据分析-数据表」列表（`/api/data-analysis/schemas`，同样按 source_type 过滤）是否同步放宽？不放宽则被移动的表从管理页消失但仍可被智能体使用，视角不一致；建议 P1 一并放宽。
2. P2 期模式 B/C 入口（attraction/hotel/travel_quote）纳入栏目授权的时机。
3. ~~「拒绝 + 返回可用栏目」是否需要在拒绝信息中附带各栏目文档数？~~ 已决议（2026-09-20）：需要附带，按 active 文档数 `GROUP BY source_type` 一次 count，帮助 LLM 选择。
