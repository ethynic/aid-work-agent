# 数据分析工具返回结构优化设计

> **关联**：
> - 父功能：[数据分析智能体](../../ideas_finished.md) #9
> - 工具设计：[smart-data-analysis-tool-design.md](smart-data-analysis-tool-design.md)
> - 工具开发计划：[smart-data-analysis-tool-dev-plan.md](smart-data-analysis-tool-dev-plan.md)
>
> **状态**：✅ 已实现（2026-06-12 上线，2026-07-20 归档）
> **创建日期**：2026-06-12

---

## 一、问题诊断（基于真实 trace 数据）

### 1.1 现象

用户测试数据分析智能体时发现：

1. 调用智能数据分析工具后，工具实际生成了图片（trace 中可见 `to_chart` 执行成功并产出 PNG 文件），但主智能体回复给用户的只有文本表格，未提及图表。
2. 用户追问「把图片放出来让我下载」时，主智能体**没有复用上一次分析的产物**，而是**重新调用了一次 `analyze_data` 工具**，重新跑完整分析流程，浪费 token 和时间。
3. 期望行为：工具结果上下文中已包含足够信息（图表路径），主智能体应当直接基于上次结果调用下载工具，而非重跑分析。

### 1.2 trace 还原

最近 3 次测试的 trace（来自 `obs_traces`，`source_type = 'data_analysis'`）：

| trace_id | 耗时 | token | 迭代 | 工具调用 | output 内容 |
|---|---|---|---|---|---|
| analysis_927d7d91 | 54s | 62788 | 7 | 14 | Markdown 表格（前10名合同） |
| analysis_db73349e | 48s | 59014 | 7 | 13 | Markdown 表格 |
| analysis_753fd91b | 37s | 53917 | 6 | 11 | Markdown 表格 |

以最完整的 `analysis_927d7d91` 为例，单次工具调用内部链路：

```
iter 0  search_data_tables("合同签约金额甲方客户") → 3张候选表
iter 1  load_table(874) + load_table(876)
iter 2  describe(874, [甲方名称, 合同总额(含税), ...])
iter 3  query(sort_by=合同总额, limit=510) → contracts_sorted.csv
iter 4  to_table(max_rows=10) → top10_table
iter 5  to_chart(bar) → 2026年签约金额前10名合同_xxx.png  ✅ 图确实生成了
iter 6  LLM 输出 Markdown 表格 + 文字总结          ← 工具返回主智能体
```

**关键证据**：
- `to_chart` 被调用且成功，图片文件路径为 `storage/analysis_charts/2026年签约金额前10名合同_20260612_135527.png`
- 但 `iter 6` LLM 的最终文字 summary 里**完全没有提到这张图**
- `_build_result` 返回的 `charts` 字段确实包含 file_path，但被主智能体 LLM 忽略

### 1.3 根因分析

`AnalysisAgent._build_result`（`src/tools/data_analysis/analysis_agent.py:515`）当前返回结构：

```python
{
    "success": True,
    "summary": "## 分析结果\n\n已完成...（一大段Markdown表格）",
    "tables": [{"output_var": "top10_table", "columns": [...], "rows": [...]}],
    "charts": [{"output_var": "top10_chart", "file_path": "...png", "title": "..."}],
    "steps": [11个步骤的完整描述],
    "intermediate_files": [...],
    "total_usage": {...},
    "iterations": 7,
    "trace_id": "..."
}
```

**四个结构性问题**（即「字段区分不清晰」的具体表现）：

| # | 问题 | 具体表现 |
|---|---|---|
| ① | **summary 抢戏** | `summary` 是 LLM 写的 Markdown，放在最显眼位置；主智能体读 tool result 时优先消费它，把 `charts`/`tables` 当成无关元数据 |
| ② | **字段语义混乱** | `tables` 既是「中间过程表」又是「最终结果表」；`steps` 里又重复出现一遍；`intermediate_files` 又是第三处冗余——同一个产物在返回值里出现 3 次 |
| ③ | **缺少「产物索引」** | 没有「本次分析产出了哪些可直接使用的文件（图表/CSV）」的明确清单；主智能体要下载图片，得自己从 `charts[0].file_path` 抠，且无法判断这个文件是否已就绪 |
| ④ | **summary 与结构化产物脱节** | LLM 写 summary 时不知道主智能体后续要怎么用，summary 里不会写「已生成图表：xxx.png，可通过 download 工具下载」 |

### 1.4 主智能体重跑分析的根因

当用户追问「把图片放出来让我下载」时，主智能体重跑了 `analyze_data`。这不是主智能体 prompt 的问题，而是**工具返回的 JSON 结构没有把「可用产物」突出出来**——只要结构本身不清晰，LLM 就会倾向于「重新调用工具去确认」。**这是结构问题，不是 prompt 问题。**

---

## 二、设计目标

1. **产物可见性**：工具返回结构必须让主智能体 LLM 一眼看到「本次分析产出了哪些可直接使用的文件」。
2. **复用优先**：当上次分析结果已能回答用户当前问题时，主智能体应当直接复用，而非重跑。
3. **字段单一职责**：每个字段语义清晰，避免同一产物在多处重复出现。
4. **过程信息降权**：分析步骤、token 用量等过程信息不干扰主智能体决策，但保留可追溯。

---

## 三、设计方案

### 3.1 重构工具返回结构

将 `_build_result` 改为**三层清晰结构**：结论层 / 产物层 / 元信息层。

```python
{
    "success": True,

    # ===== 第 1 层：结论（给主智能体快速判断用）=====
    "conclusion": "已生成2026年签约金额Top10的表格和柱状图",

    # ===== 第 2 层：可用产物清单（主智能体可直接复用的东西）=====
    "artifacts": [
        {
            "id": "chart_top10",
            "type": "chart",                          # chart / table / data_file
            "title": "2026年签约金额前10名合同",
            "description": "按合同金额降序的Top10甲方柱状图",
            "download_path": "storage/analysis_charts/xxx.png",
            "format": "png",
            "ready_for_download": True                # 明确告知：可直接给用户下载
        },
        {
            "id": "table_top10",
            "type": "table",
            "title": "Top10 合同明细",
            "description": "甲方名称 + 合同金额，10行",
            "preview": [
                ["甲方名称", "合同总额(含税)"],
                ["新疆生产建设兵团...", 10215282],
                ["北方实验室（沈阳）...", 6049600],
                ["第十四师昆玉市...", 5900886]
            ],                                        # 表头 + 最多前 10 行，供主智能体直接回答
            "download_path": "storage/analysis_data/top10.csv",
            "format": "csv",
            "ready_for_download": True
        }
    ],

    # ===== 第 3 层：分析元信息（仅供必要时追溯，不鼓励主智能体读）=====
    "analysis_meta": {
        "iterations": 7,
        "duration_ms": 53994,
        "tokens_used": 62788,
        "tables_used": ["contract_info_2026"],
        "trace_id": "analysis_927d7d91"
    }

    # 失败时追加 "error" 字段
}
```

#### 关键设计点

**1. `conclusion` 替代 `summary`**

强制 LLM 写一句**包含产物指代**的结论，而非数据明细。System prompt 约束 LLM：「conclusion 必须说明产出了什么（图表/表格），不要重复数据明细」。

错误示例（禁止）：
```
## 分析结果
已完成2026年签约金额前10名合同的查询...
| 排名 | 甲方名称 | 合同金额 |
| 1 | 新疆生产建设兵团... | 10,215,282 |
| 2 | ... |
（一大段表格）
```

正确示例：
```
已生成「2026年签约金额Top10」柱状图，并输出对应的明细表格（10行）。可直接向用户展示。
```

**2. `artifacts` 是唯一的产物索引**

所有图表、表格、数据文件**只在这里出现一次**，不再在 `steps` / `intermediate_files` 里重复。每个 artifact 包含：
- `type`：`chart` / `table` / `data_file`，主智能体据此判断如何处理
- `download_path`：可直接传给 download/cp 工具的路径
- `ready_for_download`：明确旗标，告诉主智能体「这个文件已就绪，无需再生成」
- `preview`（仅 table 类型）：表头 + 前 3 行，让主智能体能直接回答简单数据问题

**3. `preview` 字段的作用**

表格类 artifact 带 3 行预览。当用户问「前 3 名是谁」时，主智能体**直接从 `artifacts[].preview` 就能回答**，不需要重新跑分析。这是减少重跑的关键一招。

**4. `analysis_meta` 降权**

把 `iterations` / `tokens_used` / `trace_id` 等过程信息塞到最底层。主智能体 LLM 扫描 tool result 时不会优先关注这些字段，但需要追溯时（如调试、统计）仍可访问。

**5. 删除 `steps` 和 `intermediate_files` 字段**

这两个是过程产物：
- 对主智能体决策无价值（它不需要知道经历了 7 轮迭代）
- 完整的步骤信息已持久化到 `obs_spans`（trace 系统），需要追溯时查日志库即可
- 在返回值里保留只会干扰主智能体 LLM 的注意力

> **注意**：删除前需确认是否有其他消费方依赖这两个字段。`AnalysisAgent` 内部用 `_steps` 累积过程，这个内部状态保留，只是不再放进对外返回值。

### 3.2 配套修改 AnalysisAgent System Prompt

`src/tools/data_analysis/analysis_tools_schema.py` 的 `ANALYSIS_SYSTEM_PROMPT` 末尾追加「收尾规范」：

```markdown
## 收尾规范（重要！）

完成分析后，你的最终文字输出（即 conclusion）必须遵守：

1. 一句话说明本次分析产出了什么（「已生成XX图表」、「已查询出XX数据」）
2. 必须列出每个产物的标题和类型（不要列路径，路径系统自动管理）
3. 不要重复表格里的数据明细（用户能直接看到图表/表格）
4. 不要说「如需图表请告诉我」——你已经生成过了

错误示例（禁止）：写一大段 Markdown 表格列前10名
正确示例：「已生成「2026年签约金额Top10」柱状图，并输出对应的明细表格（10行）。可直接向用户展示。」
```

这样 LLM 写出来的 conclusion 会和 artifacts 对齐，主智能体读到的就是「结论 + 产物清单」的一致视图。

### 3.3 主智能体复用规范（需用户在智能体配置中添加）

这部分不能硬编码在代码里，需要用户在主智能体定义（截图中的智能体配置页面）的 system prompt 中添加。建议添加段落：

```markdown
## 数据分析工具结果复用（重要！节省 token）

调用 analyze_data 工具后，工具返回 { conclusion, artifacts, analysis_meta } 结构。

1. 首先读 conclusion 判断是否已满足需求
2. 然后检查 artifacts 数组：
   - 如果 artifacts 里已有 type=chart 的产物且标题匹配用户要的图 → 直接复用，不要重新调用 analyze_data
   - 如果 artifacts 里已有 type=table 的产物且 preview 已含答案 → 直接基于 preview 回答
3. 仅当 artifacts 不足以回答时，才重新调用 analyze_data
4. 强制交付（最高优先级）：给出最终回复前，必须把 artifacts 中所有 ready_for_download=true 的产物逐个调用 cp 工具注册给用户下载——图表 PNG 和表格文件都要交，一个都不能漏；用户没有主动要图，也必须先交图。只回复文字描述而不交付图表文件，视为本次任务未完成。
5. 用户主动要「下载图表」时：从 artifacts 找 type=chart 的 download_path 调用 cp 注册下载即可（通常第 4 步已交付，补交遗漏的即可），不要重新分析
```

> 2026-09-10 更新：原第 4 条把 cp 图表定义为"用户索要才做"，导致生产环境（tenant_128a10da9e2c，2026-09-08）出现"分析生成了 2 张图但主智能体只交付 xlsx 表格、用户追问'有图吗'才补交"的事故。现将交付改为强制动作（新增第 4 条），并同步在 workflow 分区明确"文件类含图表 PNG"。若提示词约束仍偶发漏交，下一步方案是在 `smart_analysis_tool.py` 中代码级自动注册 artifacts 交付。

### 3.4 可选增强：会话级 Artifact 缓存（P2，后做）

即使主智能体不重新调 `analyze_data`，但用户换了个相关问题（如「那前 5 名呢」），现在还是会重跑全流程。可加一层会话级 artifact 缓存：

- `AnalysisAgent` 把本次 artifacts 按 `session_id` 缓存到 Redis（key：`data_analysis:{session_id}:artifacts`，TTL 30 分钟）
- 下次同 session 调用 `analyze_data` 时，先把缓存的 artifacts 注入到 `AnalysisAgent` 的初始 messages，让 LLM 知道「之前已经算过 contracts_sorted，可以直接 to_table(max_rows=5) 复用」
- 仅当缓存命中且 LLM 判断可复用时跳过重算

此为优化项，**P0+P1 完成后视效果决定是否做**。

---

## 四、实施优先级

| 优先级 | 改动 | 文件 | 预期效果 | 工作量 |
|---|---|---|---|---|
| **P0** | 3.1 重构 `_build_result` 返回结构 | `src/tools/data_analysis/analysis_agent.py` | 解决「字段混乱、产物不突出」 | 中（半天） |
| **P0** | 3.2 AnalysisAgent system prompt 加收尾规范 | `src/tools/data_analysis/analysis_tools_schema.py` | 让 conclusion 与 artifacts 对齐 | 小（半小时） |
| **P1** | 3.3 主智能体 prompt 加复用规范 | 智能体配置（用户操作） | 解决「重复调用 analyze_data」 | 小（我提供文本，用户配置） |
| **P2** | 3.4 会话级 artifact 缓存 | `analysis_agent.py` + Redis | 解决「换相关问题重跑」 | 大（1 天，可后做） |

---

## 五、确认事项（已确认）

1. **`preview` 行数**：表格类 artifact 的 preview 放**表头 + 前 10 行**（最多 10 行；实际行数不足 10 时按实际）。
2. **`steps` 和 `intermediate_files` 字段删除后过程信息可追溯**：trace 系统已完整记录。
   - `obs_traces` 表：每次 `analyze_data` 调用的 input/output/tokens/iterations/duration
   - `obs_spans` 表：每一轮 LLM 调用 + 每个工具调用（search/load/describe/query/to_chart 等）的完整 input/output
   - 通过 `trace_id`（如 `analysis_927d7d91`）即可还原整条链路
   - 删除返回值里的过程字段不影响追溯，只是不再塞给主智能体 LLM（它用不上，反被干扰）
3. **主智能体 prompt 配置**：已确认采用 `{role} + {workflow} + {tool_use}` 三段式结构，`{tool_use}` 已包含本文 3.3 节的复用规范。P0 完成后主智能体 prompt 无需改动。

   当前配置：
   - `{role}`：专业的数据分析师，根据用户需求选择合适工具并呈现结果
   - `{workflow}`：使用智能数据分析工具 / 只展示最终结果文件 / 文件注册下载 / 禁止透露内部过程 / 高展示要求时用 PPT skill
   - `{tool_use}`：本文 3.3 节的 artifacts 复用规范

   **待 P0 上线后视效果调整的小点**：`{workflow}` 第一条「使用智能数据分析工具进行分析」语气偏强，可能导致 LLM 倾向「任何数据问题都先调工具」。若观察到重跑未消除，在 `{tool_use}` 开头补一句「**先判断上次 artifacts 是否已能回答，不能回答时才按 workflow 调用工具**」。

---

## 六、验收标准

P0+P1 完成后，以下场景应当成立：

1. **场景一：图表已生成** — 用户问「Top10 合同柱状图」→ 工具返回后，主智能体直接展示图表（通过 artifacts 里的 download_path 注册下载），不再重跑。
2. **场景二：数据已在 preview** — 用户问「前 3 名是谁」→ 主智能体直接从 artifacts[].preview 读取并回答，不调用 analyze_data。
3. **场景三：用户要下载** — 用户说「把图表下载给我」→ 主智能体从 artifacts 找 type=chart 的 download_path，调用 cp/download 工具，不重新分析。
4. **场景四：确需重跑** — 用户换了完全不同的分析维度（如「按部门统计签约额」）→ 主智能体判断 artifacts 不满足，重新调用 analyze_data（这是正确行为）。

验收时通过 trace 观察：场景一/二/三 不应产生新的 `analysis_*` trace；场景四应产生新 trace。
