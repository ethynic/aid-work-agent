# 数据分析智能体单次 392 积分消耗复盘（trace tr_4c3a88d134ea4235）

> **时间**：2026-09-20 19:08:10 – 19:41:30（生产 243）
> **会话**：`session_b4d61dc66e06`，租户 `tenant_923f70a485a1`，子智能体 `data-analysis`
> **任务**：狗粮出口销售、订单、供应商三块数据打通，六个市场 4—6 月经营分析报告 + 图表 + 汇报课件
> **结果**：完成，消耗 392.38 积分；同日同会话另有一次 TypeError 崩溃损失 8.22 积分

---

## 1. 事件经过

用户同一问题共尝试三次：

| 时间 | 尝试 | 结果 | 损失 |
|------|------|------|------|
| 18:36 – 18:54 | 第 1 次 | 用户手动取消 | 未落账 |
| 18:57 – 18:58 | 第 2 次 | `CreatePlanTool` TypeError 崩溃中断 | 8.22 积分 |
| 19:08 – 19:41 | 第 3 次（本 trace） | 完成 | 392.38 积分 |

第 3 次：外层 11 轮迭代、33 分钟，其中 6 次 `analyze_data` 合计约 30 分钟（占 trace 时长 90%）。

## 2. 计费核实：392.38 算得没错

`chat_records` id=4884：prompt **13,191,601**（缓存命中 **11,373,184**，命中率 86%）+ completion **469,447**。

按 deepseek-v4-flash 价目（输入 2 / 缓存 0.04 / 输出 8 元每百万）× `usage_factor=50`：

| 分项 | token | 积分 | 占比 |
|------|-------|------|------|
| 非缓存输入 | 1,818,417 | 181.84 | 46% |
| 缓存输入 | 11,373,184 | 22.75 | 6% |
| 输出 | 469,447 | 187.78 | 48% |
| embedding | 194 | 0.01 | — |
| **合计** | | **392.38** | |

缓存命中省了约 440 积分（若 1319 万全按输入价）。**计费逻辑无问题，问题是消耗量本身**。

## 3. 消耗根因：analyze_data 内层循环

- 外层 11 轮仅约 37 万 prompt token（`agent_session_logs` jsonl 汇总），**约 95% 消耗在 6 次 `analyze_data` 内层 AnalysisAgent**。
- 时间窗内共 **216 次 LLM 调用**（`llm_invoke_logs` 实测），单次 prompt 中位 **76k**、P90 137k、最大 **157k** token。
- 对最大请求（157k）拆解消息构成：**tool 消息 117KB（约 70%）+ assistant.tool_calls 参数 46KB（约 30%）**，system prompt 仅 4.5KB——大头是内层对话历史随轮次线性膨胀，每轮全量重发。

**现有压缩为什么压不住**（`analysis_agent.py` `_compress_messages`）：

1. 只摘除早期 tool 消息里的 `preview` / `preview_rows` / `preview_columns` 三个字段，**其余字段（columns 名单、hint、artifacts 摘要等）原样保留**；
2. `assistant.tool_calls` 的 arguments **完全不压缩**，46KB 全量重发；
3. 保留最近 16 条消息（8 轮），阈值 50KB——在 35 轮/次的任务里触发太晚、削得太多余；
4. 压缩日志是 `logger.debug`，生产主日志不可见（本次 grep 0 条），**压缩是否生效无法从日志确认**，观测缺口。

另：输出 469k token（占积分 48%）偏高，内层每轮生成分析文本+代码，且 deepseek 思考模式偏长是已知问题（代码已有 `_empty_summary_retried` 空结论兜底，见 [analysis-agent-empty-conclusion-incident.md](analysis-agent-empty-conclusion-incident.md)）。

## 4. 过程中的报错（3 类）

| 级别 | 报错 | 位置 | 影响 | 状态 |
|------|------|------|------|------|
| P1 | `CreatePlanTool.execute() got multiple values for keyword argument 'user_query'`，第 2 次尝试整轮崩溃 | `agent.py` create_plan 特殊处理分支：LLM 在 tool_args 里带 `user_query` 与注入参数重名 | 白耗 8.22 积分 + 一次失败体验 | 已修 **fc24dfa9（2026-09-21）**，**生产 HEAD 仍 46f64f48（9-20），未部署** |
| P3 | `cp` ×2 失败：LLM 猜测路径 `/app/report/dogfood_export_report.html`、`dogfood_export_deck.html` 不存在 | 19:41:23，路径校验拦截 | Agent 改用 `write` 自行生成，自我恢复，无损失 | 无需修复（防御按预期工作） |
| P3 | `use_skill` span failed（5ms） | skill 未命中即失败 | 无实质影响 | 无需修复 |

## 5. 建议

### 5.1 P0 — 部署积压修复

生产 HEAD 停在 9-20 15:21，fc24dfa9（create_plan TypeError 修复）等已积压多日。不部署则同类任务每次首轮都概率性崩溃。

### 5.2 P0 — analyze_data 内层成本优化（细化）

目标：同类重任务（6 次 analyze_data、200+ 内层调用）token 消耗降 50% 以上。按投入产出排序：

**A. 压缩策略修补（改动小，先做）** — `analysis_agent.py`

1. **历史 tool 消息整条摘要化**：超过保留窗口的 tool 消息，不再只摘 `preview` 字段，整条替换为一行摘要（`{tool_name}, output_var, row_count, columns 名单, _compressed: true`）。columns 名单是后续轮次引用的必要信息，保留；其余全删。
2. **assistant.tool_calls 历史参数裁剪**：历史轮的 `tool_calls[].function.arguments` 替换为 `<compressed>`（保留函数名和 tool_call_id 以维持消息结构合法）。实测该项占上下文 30%。
3. **保留窗口 16 条 → 8 条**，压缩阈值 50KB → 20KB（按 `sum(len(json.dumps(m)))` 现有口径）。
4. **补观测**：压缩日志升 `logger.info` 并带主题（如 `tlog("分析上下文压缩", ...)`），可确认生效。

**B. 内层上下文 token 硬上限（防恶化兜底）**

每轮 LLM 调用前用上一轮 `usage.prompt_tokens` 估算当前上下文规模，超过上限（建议 60k token）时无论是否到窗口边界，强制执行 A-1/A-2 的整条摘要化，直到降回限内。杜绝 157k 峰值再现。

**C. 减少内层轮数（结构优化，收益最大但要动交互协议）**

1. **跨 analyze_data 复用探索结论**：6 次 analyze_data 各自从 search/load/describe 开始，表结构探索重复发生。可让 analyze_data 接受调用方传入的「已加载表 metadata + 上次结论摘要」注入首条 user message（`tables_metadata` 参数已存在，主循环把前次 `artifacts` 与 summary 传下去即可）。
2. **合并同构任务**：计划步骤「六市场各自分析」被拆成 4—5 次 analyze_data；六市场数据若在同一张表（仅市场维度不同），应单次加载 + pivot 一次完成。在 data-analysis 子智能体的 system prompt 中加引导：同构多分组分析优先单表聚合，不逐市场起循环。

**D. 思考与模型分层（单价侧优化）**

1. **机械步骤关思考**：`load_table`/`describe` 之后的取数轮不需要推理，可沿用 `_lite_thinking_off_params`（空结论重试已用）对连续 N 轮纯工具调用关闭 thinking，仅总结与交叉结论轮开启。直接压 completion 469k 的大头。
2. **内层探索轮用低价模型**：探索/取数轮走 qwen3.8-flash（0.8/2.7 元每百万，输入价 2/5），总结轮走 deepseek-v4-flash。AnalysisAgent 需支持双 gateway 或按轮切换模型参数。
3. **验证前先量化**：从 `llm_invoke_logs` 抽 216 条按轮次统计 completion 中 reasoning_content 占比，确认 thinking 是否真为输出大头，避免盲改。

**E. 预算护栏（体验侧）**

单会话累计积分超软上限（建议 100）时经 SSE 推送提示；超硬上限暂停并请用户确认是否继续。防止用户对 392 积分级消耗无感知。

### 5.3 P1 — 观测补齐

`obs_spans` 本次只落了 1 条 `llm_call`（外层最后一轮 53k token），内层 200+ 调用无法按 span 归因——AnalysisAgent 的 `self._spans` 已收集全部内层 llm/tool span，但未持久化到日志库。把内层 span 写入 `obs_spans` 后，此类成本异常可自动归因到具体 analyze_data 调用，不再需要人工拆 jsonl。

## 6. 排查命令与数据来源

```bash
# trace 主记录 / 步骤明细（aid_work_logs）
sudo docker exec aid-postgres psql -U aid_user -d aid_work_logs \
  -c "SELECT * FROM obs_traces WHERE trace_id='tr_4c3a88d134ea4235';"

# 计费明细（aid_work_agent，usage_breakdown 含单价快照）
sudo docker exec aid-postgres psql -U aid_user -d aid_work_agent \
  -c "SELECT prompt_tokens, completion_tokens, cached_input_tokens, credit_cost, usage_breakdown FROM chat_records WHERE id=4884;"

# 外层逐轮 token（agent/agent_session_logs_YYYYMMDD.jsonl，含 usage 字段）
# 内层全量调用（llm/llm_invoke_logs_YYYYMMDD.jsonl，按时间窗过滤）
```
