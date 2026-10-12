# 数据分析 analyze_data 成本优化开发计划

> ideas 编号：20261011-0948（工具分区）
> 关联复盘：[数据分析智能体单次 392 积分消耗复盘](../incidents/analysis-agent-cost-392-credits-incident.md)（生产同类根因，根因分析与计费口径均出自该复盘）
> 目标：同类重任务（多次 analyze_data、200+ 内层调用）token 消耗降 50% 以上，完整任务目标 ≤90 积分

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 0 | 基线重放：agent1 仿真环境 e2e 直驱重放同一对话，落 obs_traces/obs_spans/chat_records 基线指标 | ✅ 已完成 2026-10-12 | 基线场：163,569 外层 prompt tokens / 7 轮 / 17.7 分钟（obs_traces 不落库限制仍在，信号以 JSONL + chat_records 兜底） |
| Phase 1 | 改进点 1：analyze_data 服务端批量选表灌入 + schema 目录前置（模型零逐表加载，load_table 仅兜底） | ✅ 已完成 2026-10-12 | agent1 重放：预加载 49/49、手动 load_table 0、6 轮 / 7.6 分钟 / 62.4 积分（chat_records 全口径，缓存命中 87%） |
| Phase 2 | 改进点 2：append 纵向堆叠工具（label_columns 一次标注） | ✅ 已完成 2026-10-12 | agent1 重放：append 1 次带标签堆叠 36 表，merge 冒充 UNION 0 次 |
| Phase 3 | 改进点 3：跨调用强制复用（产物 lineage + load_table 拦截提示 + INJECT_LIMIT 50，另含导出意图短路/条件预加载） | ✅ 本地重放验证通过 2026-10-12 | lineage 闭包判定 + load_table 软拦截（二次放行）+ INJECT_LIMIT 50 + 导出类调用跳过 49 表预加载；24 例新单测全过。本地重放（session_replay_20261012_192910）：与本地第 2 场同需求文本对照，追加调用合计 79.2 -> 25.5 积分（-68%），第 3/4 次调用内层 6 次工具全部 load_output 零重加工；注册表 50/50 条目含 upstreams（persons2 -> personRank、cmc3 -> kpi -> kpi2 链完整）；缓存命中 88.5% 无回归。详见「三、验证方式」Phase 3 重放记录 |
| Phase 4 | ~~改进点 4：内层循环思考分级（机械轮关思考）~~ ❌ 已撤回（2026-10-12，见 §2.4 撤回说明）+ 追加 A：describe 瘦身与 memo 缓存 + 追加 B：同构查询合并提示词（见 2.7/2.8） | ✅ 验证通过 2026-10-12（run4） | 思考分级因 DeepSeek 混合思考校验不可预测撤回；追加 A/B + rc 真值回传经 run4 验证：主分析 62.7 -> 33.6 积分（-46.4%）、新发 prompt 299k -> 152k、describe 27 -> 18 次、整体 -13.2%（见 §2.9）。依据本地第 3 场成本解剖：completion 占全场积分约 2/3、describe 27 次 ≈150k 新发 prompt、六国切片 18 次同构 query |
| Phase 5 | 改进点 5：预算护栏软/硬闸 + MAX_ITERATIONS 200（护栏只停止不压缩历史，保缓存命中） | 📋 待开发 | — |
| Phase 6 | 改进点 6：删除「每次只调用一个工具」鼓励并行 | 📋 待开发 | — |

## 一、背景

仿真环境 trace `tr_runner_7f6851f4d0ee4f939d606d34a09cde03` 单任务消耗 **310.77 积分 / 约 1468 万 tokens**，与生产 392 积分事件（tr_4c3a88d134ea4235，见[复盘](../incidents/analysis-agent-cost-392-credits-incident.md)）根因一致：消耗大头在 `analyze_data` 内层 AnalysisAgent 循环（生产事件约 95% 消耗在内层，216 次 LLM 调用，单次 prompt 中位 76k、最大 157k token）。两个结构性根因：

1. **逐表加工**：每次 `analyze_data` 各自从 `search_data_tables`（`src/tools/data_analysis/analysis_agent.py:957`）→ `load_table`（`analysis_agent.py:436` 分发至 `data_analyzer.py:115`）→ `describe` 开始，6 次调用各自重复表结构探索；模型逐表加载、逐轮取数，轮数即成本。
2. **上下文滞留**：内层对话历史随轮次线性膨胀、每轮全量重发（最大请求中 tool 消息约 70% + `tool_calls` 参数约 30%）；现有 `_compress_messages`（`analysis_agent.py:742`）只摘 preview 字段压不住，且压缩改写消息前缀会打掉前缀缓存命中——生产事件缓存命中 1137 万 token（命中率 86%）省了约 440 积分，缓存是成本模型的前提。

输出侧同样偏高：生产事件 completion 469k token 占积分 48%，deepseek 思考模式长输出是已知放大项（复盘 §3）。

## 二、改进方案（6 点）

### 2.1 服务端批量选表灌入 + schema 目录前置

- 现状：`analyze_data` 已支持 `tables_metadata` 入参预加载（`src/tools/data_analysis/smart_analysis_tool.py:40`）。服务端批量选表灌入的代码已在工作区落地（未提交改动）：表数 ≤ `FULL_LOAD_THRESHOLD=50` 全量预加载，超过时按需求语义检索 `SEMANTIC_SELECT_TOP_K=30` 张、检索失败退回不预加载（`smart_analysis_tool.py:21`、`:23`、`_batch_select_and_load`，`smart_analysis_tool.py:168`、`:202`）；首条 user message 注入「系统已预加载」schema 目录并声明清单内 search/load 属错误（`analysis_agent.py:700-716`，目录渲染复用 `_build_tables_info`，`analysis_agent.py:790`）。
- 方案：本阶段完成该点收尾与仿真重放验证——服务端（SmartDataAnalysisTool 调用入口）按用户需求**一次性批量选定**相关表并灌入内层，模型对目录内表**零逐表加载**即可规划分析；`load_table` 仅作为目录外兜底路径保留。
- 收益：消除每次 analyze_data 开头的 search/load/describe 探索轮（生产事件中 6 次调用各探索一遍）。

### 2.2 append 纵向堆叠工具（label_columns 一次标注）

- 现状：分析工具只有横向 `merge`（`analysis_tools_schema.py:357`），无纵向堆叠工具；多表/多分组结果纵向合并需模型多轮变通，每轮一次调用一次上下文重发。
- 方案：新增 `append` 工具：纵向堆叠多个 source（table_id / output_var / 文件路径），支持 `label_columns` 参数在一次调用中为各来源生成来源标注列；工具 schema 加入 `ANALYSIS_TOOLS`，分发接入 `_handle` 分支。
- 收益：多来源纵向合并从 N 轮压成 1 轮，同时是改进点 1「批量加工」的执行底座。

### 2.3 跨调用强制复用（产物 lineage + load_table 拦截提示 + INJECT_LIMIT 50）

- 现状：产物清单提示已存在（`_build_artifacts_info`，`analysis_agent.py:722`，"优先复用！"）但只是提示非强制；产物注入上限 `INJECT_LIMIT = 10`（`src/tools/data_analysis/analysis_artifacts.py:31`）偏小。
- 方案：
  1. **产物 lineage**：`analysis_artifacts` 记录每个 output_var 的上游（来源 table_id / 上游 output_var），可判定「某表的等价加工产物已存在」；
  2. **load_table 拦截提示**：内层 `load_table` 命中「该表已有等价产物」时，拦截返回提示并引导改用 `load_output(output_var)`（`_handle_load_output`，`analysis_agent.py:1130`），阻断重复加载-清洗-合并链路；
  3. **INJECT_LIMIT 10 → 50**：跨 analyze_data 注入完整产物目录，6 次调用共享前序结论。
- 收益：生产事件中 6 次 analyze_data 各自从零探索的重复消耗被结构性地消除。

### 2.4 内层循环思考分级（机械轮关思考）

- 现状：一次性关思考机制已在（`_disable_thinking_next` + `_lite_thinking_off_params`，`analysis_agent.py:256、:291-294`，空结论重试在用），但正常轮全部开思考。
- 方案（2026-10-12 实施定稿）：按轮分级——本轮全部工具属于机械集（`DATA_PROCESSING_METHODS` + describe/load_output，即纯数据搬运/统计，结果即事实无需推理规划）时，下一轮自动关思考，复用既有 `_disable_thinking_next` + `_lite_thinking_off_params`（同模型参数透传，不影响计费模型名）；交付轮（to_table/to_chart）、探索轮（load_table/search/list）与首轮保持思考开启；空总结重试的关思考兜底不变。
- 补充依据（本地第 3 场解剖，替代 llm_invoke_logs 统计）：主分析 completion 62,250 token 是积分大头（全场 completion ≈2/3 积分），38 轮中大多数为机械取数轮（query/append/calculate），关思考直接压该大头。
- 实施注意：
  - ~~先按复盘 §5.2 D-3 从 `llm_invoke_logs` 统计 reasoning_content 占比~~ 已由本地第 3 场逐轮账目替代确认（completion 占比 2/3）；
  - 关思考调用点的计费路由需对齐 `gateway.chat_no_thinking` 先例（commit bfec3ae9），避免计费模型错配复发（本实现为同模型参数透传，模型名不变，天然对齐）。
- **实施发现（2026-10-12，真实 API 探针共 16 例，scripts 临时探针在仓库外 temp 目录）**：DeepSeek 思考模式要求历史 assistant 轮回传 `reasoning_content`（rc）键——开思考请求 + 历史含关思考轮（响应无 rc）-> 400 invalid_request_error；关思考请求本身对 rc 完全免疫。机械轮关思考改动首次触发该校验（服务端当日收紧：19:46 前结束的基线场同结构 0 次 400，20:11 起必现），400 后 failover 到 qwen 既污染计费又毁成本对照。**探针 4（最小生产形状复现）修正了探针 1-3 的部分结论**：「任意一轮带 rc 即可通过」不成立——混合开/关思考历史中仅早期轮带真 rc 仍 400（V-A 复现 run2 生产形态：机械链全程关思考积累无 rc 历史，to_chart 交付轮后第一个开思考请求必 400，直到回到机械链才恢复，与 run2 场景 3 的 42/45/49 三连 400 后 52+ 成功完全吻合）；但**空串 rc 被接受**（V-B 200，服务端只验键存在，后续推理正常）。修复：agent 循环统一挂 rc 键——有真 rc 挂真值、无 rc（关思考轮/qwen failover 轮）挂空串占位；增量成本为 rc 作为新发 input 一次全价 + 后续轮 10% 缓存价；qwen 等其他 provider 基类 `_format_messages` 不转发 rc，空串键无副作用。run2 因此在 4 场中出现 8 次 400 污染后中止，rc 修复后的干净对照由 run3 承担。
- **❌ 撤回决定（2026-10-12 用户确认）**：run3（空串补齐版）仍出现 6 次 400，全部位于「关思考交付轮（to_table/to_chart，响应无 rc、挂空串）→ 下一轮开思考请求」处，且探针 V-B 同形状通过而生产同形状失败——服务端对混合思考历史的 rc 校验口径不稳定且仍在滚动收紧，空串占位防不住。结论：**DeepSeek 同一会话混合开/关思考不可靠**，机械轮关思考分级整体撤回，会话内全场统一开思考。保留项：① rc 真值回传挂载（官方契约，全开思考模式下仍必要——探针 2 V1 实测无回传也 400）；② 空总结重试的关思考兜底（既有事故修复，非本次引入）；③ 追加 A/B 与提示词两条（与混用无关）。completion 大头的压缩改由追加 A/B（减少轮数与 describe 体积）承担，重放对照由修复后的下一轮 run 承担。

### 2.5 预算护栏软/硬闸 + MAX_ITERATIONS 200

- 现状：内层上限 `MAX_ITERATIONS = 50`（`analysis_agent.py:26`），生产事件单次 analyze_data 35 轮已逼近；无积分预算护栏，310/392 积分级消耗用户全程无感知。
- 方案：
  1. **MAX_ITERATIONS 50 → 200**：为「批量灌入 + 复用 + 并行」后的长任务留出轮数空间，避免正常任务被轮数截断废掉已消耗成本；
  2. **预算护栏**：内层循环（或其所属会话）累计积分超软闸经 SSE 推送提示，超硬闸停止循环并向调用方返回已得产物与停止原因（软/硬闸阈值 Phase 0 基线后定）。
- 关键约束：**护栏只停止、不压缩历史**。与复盘 §5.2 B「超限强制压缩」不同，本计划刻意不压缩——压缩改写消息前缀会使前缀缓存全量失效，而缓存命中（按 10% 计费）是成本兜底（复盘 §2：命中 86% 省约 440 积分）。停止时保留完整历史，用户确认续跑可继续命中缓存。

### 2.6 删除「每次只调用一个工具」鼓励并行

- 现状：内层执行原则第 2 条为「每次只调用一个工具」（`src/tools/data_analysis/analysis_tools_schema.py:89`），强制串行，轮数 = 工具数，轮数越多全量重发次数越多。
- 方案：删除该条，改为鼓励无依赖的工具调用在一轮内并行发起（多 tool_calls）；`_execute_tool` 单工具分发（`analysis_agent.py:419`）需支持一轮多 tool_calls 的顺序执行与逐个错误隔离。
- 实施注意：先小规模验证生产模型（deepseek/qwen）经网关的并行 tool_calls 稳定性，再全量放开；有依赖的调用仍串行，提示词写明判断标准。

### 2.7 追加改进 A：describe 结果瘦身 + memo 缓存（2026-10-12 新增）

- 依据（本地第 3 场解剖）：主分析新发 prompt 299k token 的最大单一来源是 describe——`DataAnalyzer.describe` 每列返回最多 20 个唯一值（`max_unique=20`）或 top 值×20，30 列单次 4-6k token，主分析 27 次 ≈150k。query/aggregate/append/calculate/merge 的结果已是 3 行预览（`_handle_data_method` 返回 preview=df.head(3)），非靶子。
- 方案：
  1. **结果截断**：`_handle_describe` 对每列 `unique_values` / `top_values` 截断到 `DESCRIBE_VALUE_CAP=8` 条；标量统计（dtype/non_null/unique_count/numeric_summary）全保留，模型仍知道完整基数；截断发生时附一次性提示「需要完整清单/精确计数用 query/aggregate 取数」。
  2. **memo 缓存**：同参数 describe 在本次分析内只算一次（命中结果带 `cached: true` 便于重放验证）；任何非 describe 工具成功执行后清空缓存（`_execute_tool` 收口——data_method/load_table/load_output 都会改变可描述的数据面），杜绝过期结构。
- 收益预估：主分析新发 prompt -80k~-120k token（约 7-10 积分），同时压缩工具消息体积使后续每轮全量重发变小（缓存命中率不降）。

### 2.8 追加改进 B：提示词——同构多组查询合并 + 禁止冗余 describe（2026-10-12 新增）

- 依据（本地第 3 场解剖）：六国 × 维度切片产生 18 次同构 query（ch_*/cn_*/cs_* 产物链），全部可用一次 aggregate（group_by 国别+渠道）表达；27 次 describe 中多数为列名确认，而工具结果与开局 schema 目录均已返回 columns 清单。
- 方案：`analysis_tools_schema.py` 执行原则追加两条——
  1. 同一维度的多组同构查询（逐国/逐渠道过滤 TopN、逐月统计等）必须合并为一次分组聚合（aggregate group_by）或 pivot，仅当分组聚合无法表达时才逐组查询；
  2. 列名已知时禁止用 describe 重复确认；describe 仅用于需要取值分布/统计摘要时，且同一数据源不重复 describe。
- 收益预估：轮数与新发 prompt 双降（18 次切片 query → 1-3 次 aggregate，理论省 10+ 轮）；与改进点 6（并行工具）正交。
- 实施说明：改进点 6 本次不动（用户指示仅 1+2+3），执行原则第 2 条「每次只调用一个工具」保持原文。

### 2.9 追加 A/B + rc 真值回传重放验证（run4，2026-10-12）

run4 = 撤回思考分级后的统一开思考代码，本地重放 3 场景（814s，44 轮，0×400、0 次真实 failover），对照本地第 3 场基线（同输入、同积分汇率）。按 chat_records 场次时间窗对账：

| 场次 | 基线 | run4 | Δ |
|------|------|------|---|
| 场景 1 六国狗粮主分析（优化主对象） | 39 轮 / 62.72 积分 | 13 轮 / 33.58 | **-46.4%** |
| 场景 2 | 7 轮 / 14.57 | 18 轮 / 32.12 | +120%（见下） |
| 场景 3 | 4 轮 / 11.01 | 4 轮 / 7.07 | -35.8% |
| 报告生成 2 笔 | 29.71 | 29.60 | 持平 |
| **合计** | **118.01** | **102.37** | **-13.2%** |

- 场景 1 明细：completion 64,842 -> 42,056（-35%）；新发 prompt 299k -> 152k（**-49%**）；缓存命中 88.5% -> 82.2%（轮数减少致前缀复用累积下降 + rc 真值进历史，新发绝对值仍大降）。describe 27 -> 18 次、单次值列表 20 -> 8 条；18 次同构切片 query 被 aggregate（×10）/pivot（×2）合并取代，逐国查询以 4 条并行批次完成——追加 A/B 机制特征均按预期出现。
- 场景 2 +120% 定性：模型路线方差，非改动回归——run4 走 aggregate+pivot+append 重路线并交付 4 图 4 表（18 轮 vs 基线 7 轮）。单场积分带宽 62-171（见 §三 多场取样结论），不可归因；列为观察项，不据此改提示词。
- 报告生成 2 笔两场恒定（积分 29.7/29.6、completion ~64.7k），与本次优化无关；**报告生成模板化为下一优化候选**（两项各 ~15 积分、completion 各 ~32k）。
- 结论：追加 A/B 验证通过。主分析 -46.4%，整体 -13.2%（且在场景 2 不利抽样下取得）；统一开思考 + rc 真值回传形状经 run4 全程验证稳定。

## 三、验证方式

以 Phase 0 基线为对照，**每完成一项改进点**即同步代码到 agent1 仿真环境（sim.sh 默认附带代码同步，见[仿真环境设计](../system/simulation-env-design.md)），**重放同一对话**：e2e 容器内直驱（重放脚本 `scripts/e2e_dogfood_replay.py`，直驱方式参照 `tests/e2e/test_agent_full.py` 直接实例化 Agent），输入与基线完全一致，不经过渠道层。**例外（2026-10-12 用户指示）**：改进点 4 + 追加 A/B 三项合并实现后合并重放一次（本地重放，对照本地第 3 场基线 118.0 积分 / 主分析新发 299k / completion 62,250），不再逐项重放。

**本地重放（2026-10-12 起，用于开发迭代）**：`bash scripts/e2e_dogfood_replay_local.sh`——本地工作区代码直驱 + agent1(243) 仿真库经 SSH 隧道（`-L 10864:172.17.0.9:10864`）访问，表源文件镜像到 `<repo>/storage/`（脚本内做 `/app/storage/` 路径映射，不改产品代码），LLM 模型配置启动时强制对齐 agent1 .env。防呆：DATABASE_URL 未指向 `aid_work_agent1` 时拒绝执行（重放会写 chat_records）。**验收信号仍以 agent1 容器重放为准**（本地无 Redis 走内存降级、Python/pandas 版本与容器存在漂移）。已知口径差异（2026-10-12 实测澄清）：重放 JSONL / `REPLAY_RESULT` 的 tokens **只含外层 agent 轮**（本地场外层 236,769 vs chat_records 全口径 495 万 prompt tokens）；内层 AnalysisAgent 的 LLM 计费落账为匿名 `background_llm` 会话（chat_records 中无 `session_replay` 前缀记录），**真实消耗与积分以 chat_records 按场次时间窗汇总为准**。积分「汇率」差异：agent1 .env 有 `USAGE_FACTOR=50`（config.yaml 默认 100），本地重放必须 export 该变量（launcher 已加），否则按 2 倍系数计费（实测同一笔 (p=32932, c=7871, cached=6656) 本地计 11.58、agent1 同构笔计 7.18，价目表反算 ×100 / ×50 精确吻合）。另：中期记忆压缩（mid_term）走 `LITE_MODEL_CODE`（两边同为 deepseek/deepseek-flash），长任务会触发——本地场 2 笔共 22.7 积分（折算后），agent1 短场未触发。

**重放方差提示（2026-10-12 实测，5 场样本）**：同代码（md5 一致）同模型（deepseek-v4-flash）同数据同 USAGE_FACTOR=50 口径，单场重放存在**大幅行为分叉**，且分叉源不是环境差异而是外层模型的任务拆分随机性。五场对照：

| 场次 | analyze_data 次数（其中「导出整理」类） | 积分 | 耗时 | 外层轮数 |
|------|----------------------------------------|------|------|---------|
| agent1 历史 4 场 | 2（0-1） | 62.4-85.5 | ~7.6 min | 6 |
| agent1 第 5 场（session_replay_20261012_185322） | **6（4）** | **170.7** | 21.2 min | 13 |
| 本地第 1 场 | 3（1） | 137.7 | 17.3 min | — |
| 本地第 2 场（session_replay_20261012_183737） | 4（2） | 107.9 | 15.0 min | 15 |

关键事实：agent1 第 5 场自己跑出 6 次 analyze_data / 170.7 积分（历史均值的 2.4 倍），打破「agent1 稳定 2 次」假设——**「导出整理」类追加调用次数是成本第一驱动因子**：主分析 1 次约 40-60 积分，之后每次追加调用重新支付 1 次 49 表 preload（日志「开局预加载完成：49/49」次数 = analyze_data 次数）+ 全新内层循环，边际 20-40 积分/次；agent1 第 5 场连追 4 次导出调用（任务文本逐次急躁化：「只做导出动作」→「不要做任何数据分析」→「重要：不要纵向堆叠成一张表！」），行为与本地场同构。质量四场全部完整（总表+透视图+汇报文件），分叉只伤成本不伤质量。

结论：单场结果不可直接当验收信号（积分带宽 62-171，2.8 倍）；对比以改进机制特征（load_table 0 / append 使用 / 复用命中 / 导出类调用是否被拦截）为主、积分为辅且需多场取样。**Phase 3「跨调用强制复用」的收益上限因此上修**：若导出类追加调用被重定向到已有产物（load_output + 轻量导出，不再触发 preload + 完整内层），本五场样本可省 20-110 积分/场。

**Phase 3 本地重放记录（2026-10-12，session_replay_20261012_192910）**：4 次 analyze_data / 16.6 min / chat_records 全口径 118.0 积分 / 缓存命中 88.5%（无回归）/ 质量完整（总表 + 透视图 + 报告文件）。与本地第 2 场（Phase 1+2 代码，同一脚本对话、需求文本相同）按 analyze 时间窗对照：

| 调用 | 本地第 2 场（P1+2） | 本场（P3） | 说明 |
|------|---------------------|------------|------|
| 主分析 #1 | 27.9（105 内层工具） | 62.7（107） | 单场波动大，n=1 不归因 |
| 追加 #2（复用类） | 47.0（34 工具，5 min，describe/query 混入） | 12.2（26 工具，load_output 主导） | |
| 追加 #3（整理类） | 13.8（28 工具） | 5.9（6 工具全 load_output） | 零重加工 |
| 追加 #4（整理类） | 18.4（7 工具） | 7.4（6 工具全 load_output） | 零重加工 |
| 追加小计 | **79.2** | **25.5（-68%）** | Phase 3 目标行为达成 |
| 尾部交付 | 0.8 | 29.7 | 见下方新观察 |

机制归因：主要生效杠杆是**产物目录注入（INJECT_LIMIT 50 + lineage 来源标注）**——追加调用看到全部产物后主动 load_output 复用（本场 19 次），第 3/4 次调用内层 6 次工具全部为 load_output，完全未重走「加载-清洗-合并」链路；注册表 50/50 条目含 upstreams（persons -> persons2 -> personRank、cmc2 -> cmc3 -> kpi -> kpi2 链完整）。**导出短路（export_skips=0）与 load_table 拦截（intercepts=0）本场未触发**：#2 需求含「分析结果」被分析词否决回正常路径（fail-soft 符合设计，代价小）；load_table 内层 0 次调用（预加载全覆盖），拦截无触发机会——两项保留作为纯导出文本/兜底场景的安全网。

新观察（Phase 3 范围外，登记为后续优化候选）：本场尾部 29.7 积分来自 2 笔一次性 LLM 生成调用（16.1 + 13.6）——外层 write 工具生成 HTML 报告等大文本文件，单笔 prompt 仅 1-2k 但 completion 巨大；若改为模板拼装或内层产物直出，可再省约 25 积分/场。

**信号先从重放日志核实**：`REPLAY_RESULT` 单行汇总（累计 prompt/completion tokens 与 llm/tool 调用数，`scripts/e2e_dogfood_replay.py:103`）、逐轮 `累计in/累计out` 进度（`scripts/e2e_dogfood_replay.py:62-63`）、`[AnalysisAgent][TOOL]` 内层工具调用打点（当前源码仅有 `[AnalysisAgent] ...` 系列标记与重放 JSONL 的 `tool_result` 事件，`scripts/e2e_dogfood_replay.py:73-79`；若基线重放无该标记则 Phase 0 补齐）；库表指标按下表核对（查询方式见复盘 §6）：

| 数据源 | 核对信号 |
|--------|---------|
| `obs_traces`（aid_work_logs） | trace 级总积分、总 token 相对基线的降幅 |
| `obs_spans`（aid_work_logs） | 内层 llm/tool span 按 analyze_data 调用归因的轮数与 token 分布。注意：复盘 §5.3 指出内层 span 尚未持久化到 obs_spans——若该项观测缺口未先补齐，以 `llm_invoke_logs` jsonl 兜底统计并在当项验证记录中注明 |
| `chat_records`（aid_work_agent） | prompt / completion / cached_input / credit_cost 分解，及缓存命中率不因本计划改动而下降 |

**信号即停**：重放不要求跑完整场对话——重放日志（`REPLAY_RESULT` / 累计 tokens / `[AnalysisAgent][TOOL]` 打点）与上表改进信号核实后即**提前终止重放**，不等整场结束，控制每项验证的仿真环境消耗。

**已知限制**：e2e 直驱路径 `LOGS_DATABASE_URL` 连接池未初始化（`src/db/database.py:298`），obs_traces 不落库（已记录待修）；修复前 obs_traces 类信号以重放日志与 `chat_records` / `llm_invoke_logs` jsonl 兜底。

判定基线：完整任务 ≤90 积分、同类重任务 token 降 50%+；单项改进以「重放对话积分/ token 相对 Phase 0 基线的降幅」为验收信号，任一项导致缓存命中率显著下降或分析结论质量回退即回退该项。

## 四、边界与风险

- 本计划只动 `analyze_data` 内层（AnalysisAgent / 分析工具 schema / artifacts），不改外层主智能体协议与 wire schema；若涉及跨组件共用接口，按 [Runner / Desktop / Runtime 集成契约](../system/runner-desktop-runtime-integration-contract.md)登记。
- 提示词改动（2.1 目录前置、2.3 拦截提示、2.6 并行鼓励）可能改变模型行为路径，每项独立重放验证，不合并上线。
