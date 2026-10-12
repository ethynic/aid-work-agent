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
| Phase 3 | 改进点 3：跨调用强制复用（产物 lineage + load_table 拦截提示 + INJECT_LIMIT 50） | 📋 待开发 | — |
| Phase 4 | 改进点 4：内层循环思考分级（机械轮关思考） | 📋 待开发 | — |
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
- 方案：按轮分级——机械轮（`load_table` / `describe` 之后连续的纯取数轮）自动关思考，仅规划轮与总结/交叉结论轮开启，直接压 completion 大头。
- 实施注意：
  - 先按复盘 §5.2 D-3 从 `llm_invoke_logs` 统计 reasoning_content 占比，确认思考确为输出大头再改；
  - 关思考调用点的计费路由需对齐 `gateway.chat_no_thinking` 先例（commit bfec3ae9），避免计费模型错配复发。

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

## 三、验证方式

以 Phase 0 基线为对照，**每完成一项改进点**即同步代码到 agent1 仿真环境（sim.sh 默认附带代码同步，见[仿真环境设计](../system/simulation-env-design.md)），**重放同一对话**：e2e 容器内直驱（重放脚本 `scripts/e2e_dogfood_replay.py`，直驱方式参照 `tests/e2e/test_agent_full.py` 直接实例化 Agent），输入与基线完全一致，不经过渠道层。

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
