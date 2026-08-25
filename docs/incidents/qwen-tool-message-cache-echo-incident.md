# 线上故障复盘：qwen3.7-flash 工具结果缓存数组化导致回复回显 JSON

> 日期：2026-08-19
> 环境：测试环境 agent2.aidingyi.cn
> 影响：after-sales 会话历史页显示智能体回复为一长段 JSON（tool_result 序列化），非自然语言
> 根因：`src/llm/providers/base.py` 显式缓存"末尾标记"逻辑把 **tool 消息**的纯字符串 content 改写成多模态 content 数组，违反 OpenAI 兼容规范；qwen3.7-flash 对非法格式**概率性**按 Anthropic 语义回显工具结果
> 关联文档：[plan-qwen3-7-flash-context-cache-optimization.md](../plans/plan-qwen3-7-flash-context-cache-optimization.md)、[deepseek-v4-flash-replacement-research.md](./deepseek-v4-flash-replacement-research.md)

---

## 一、故障现象

测试人员在历史会话页面（https://agent2.aidingyi.cn/t/tenant_49a0f3d1706a/chat/after-sales）发现：会话 `session_ed8492421cc9` 中智能体回复消息（created_at **2026-08-19T14:35:02**）显示为**很长一段 JSON**，而不是自然语言文本。

API 返回的 4 条消息：

| 序号 | role | 内容 |
|------|------|------|
| 1 | user | 问：女款纯棉短袖T恤 价格100以内 商品信息 |
| 2 | assistant | tool_calls（knowledge_base_search） |
| 3 | tool | 知识库检索结果（多条 SKU 明细 JSON） |
| 4 | assistant | **一整段 tool_result 序列化 JSON（应为自然语言推荐）** |

出问题的 assistant 回复是 **Anthropic 风格的 tool_result 格式**：

```json
[{"id": "chatcmpl-9531", "tool_call_id": "call_4028e3a9c67d4b3fa752f8",
  "type": "tool_result", "function": {"name": "knowledge_base_search"},
  "result": "[{\"text\": \"| 59.9 | 72小时内发货 | 100%重磅纯棉...\"}]"}]
```

该 JSON 被当作最终回复存入 `chat_messages`（消息 id 7227）并推给前端，历史页显示为 JSON。

## 二、排查过程

### 2.1 定位到出问题的 LLM 调用

- LLM 日志在 `log/llm/llm_invoke_logs_20260819.jsonl`，无 session_id 字段，按**工具参数关键词**（"女款纯棉短袖T恤"）与**时段**（14:33–14:35）定位到 **api3 容器（8004 端口）**。
- 该会话**只有 2 次 LLM 调用**：

| 时间 | 消息数 | 末尾 role | 结果 |
|------|--------|-----------|------|
| 14:33:52 | 2（system+user） | user | 正常 |
| 14:35:02 | 4（system+user+assistant tool_calls+tool） | **tool** | **出问题** |

### 2.2 定位到 tool 消息被数组化

出问题那次的 `request.messages[-1]`（tool 消息）content 是**多模态数组**而非纯字符串：

```json
{"role": "tool", "tool_call_id": "call_4028e3a9c67d4b3fa752f8",
 "content": [{"type": "text", "text": "<大段工具结果JSON>",
              "cache_control": {"type": "ephemeral"}}]}
```

### 2.3 根因确认：缓存"末尾标记"逻辑的回归

`src/llm/providers/base.py` `_format_messages` 的显式缓存逻辑（commit `e48d744f`，2026-08-17「末尾标记」策略）**无条件把最后一条消息**的 content 数组化并打上 `cache_control`。当 agent 工具轮消息数组以 **tool 消息**结尾时，就把 tool 消息的纯字符串 content 改写成 `[{"type": "text", "text": ..., "cache_control": ...}]`。

**违反 OpenAI 兼容规范**：tool 消息的 content **必须是纯字符串**，不能是 content 数组。qwen3.7-flash 收到非法格式后，把 content 数组按 **Anthropic 语义**（cache_control 是 Anthropic 生态写法）解读为 tool_result 并**回显**，从而污染了最终回复。

## 三、为什么今天测了很多轮只有这一次中招（深入核实）

### 3.1 触发条件是"消息数组末尾是 tool 消息"

只有 **agent 工具调用后的下一轮 LLM 调用**（消息数组以 tool 消息结尾）才会走"把 tool 消息数组化"的路径；普通问答轮末尾是 user/assistant 文本消息，缓存标记正常落在文本上，从不触发。

### 3.2 数组化本身不必然回显——回显是概率性的

对今天所有容器 qwen3.7-flash 调用全量统计：

| 容器 | qwen3.7-flash 调用 | 末尾是 tool 消息（走了数组化路径） | 回显 |
|------|------|------|------|
| api2 | 31 | 13 | **0**（全部正常回复） |
| api3 | 2 | 1 | 1 |
| 合计 | 33 | 14 | 1（~7%） |

- api2 今天 **13 次**同样的数组化 tool 消息**全部正常**，仅 api3 那次回显。
- 用事故同规模大 tool 结果复测格式 A（bug 行为）**20+ 次均未复现**回显。
- **结论**：qwen3.7-flash 对"tool 消息 content 数组化"这个非法格式的处理**不稳定**——绝大多数时候"宽容"地照常理解，偶发按 Anthropic 语义回显工具结果（今天 ~7% 概率）。

### 3.3 只有"最后一次调用"的回显才会暴露

agent 工具调用后若还有后续轮次，这轮回显的 JSON 会被下一轮正常回复覆盖。**只有"工具调用后 agent 直接结束"**的那条会话，回显才成为最终回复。after-sales 这条恰好只有 2 次调用，第 2 次就是最后一轮，所以暴露了。

## 四、缓存成本影响核实（用户质疑：tool 消息不缓存 → 成本上升）

### 4.1 bug 行为下缓存覆盖整个数组

api2 那 13 次"末尾 tool"调用的 usage 显示：`cached_tokens ≈ prompt_tokens`（如 125263/123998）、**`non_cached=None`** —— bug 行为（标记在 tool 消息上）创建的缓存块覆盖**整个消息数组（含 tool 结果）**，工具结果几乎全按 **10% 命中**计费。

### 4.2 修复后工具轮 tool 结果退出缓存块

修复后（标记回退到前面可安全标记的文本消息），工具轮时新追加的 `assistant(tool_calls) + tool` 不在缓存块内，按 **non_cached（100% 原价）** 计费——相对比例从 10% 变 100%，**这是真实的相对退化**。

### 4.3 但绝对金额影响极小

- qwen3.7-flash 输入单价 0.2 元/百万 token。
- 一次工具轮 tool 结果按 ~5K token 算，差价 = 5000 × (100%−10%) × 0.2/1e6 ≈ **0.001 元/次**。
- 且工具轮**之后**的文本轮会重建全数组缓存（125% 创建一次），后续继续全命中。
- 对比旧策略（首条 system 标记，实测 `non_cached=223931` 全部按 100%），修复后**整体成本仍大幅优化**，仅工具轮略退化。

## 五、修复方案与验证

### 5.1 修复内容

`src/llm/providers/base.py` `_format_messages`（158–181 行）：缓存标记从"无条件标记最后一条消息"改为"**从后往前找第一条可安全标记的文本消息**"——跳过 `tool` 消息和 `assistant(tool_calls)` 消息，只标记 content 非空的纯文本 user/assistant 消息；找不到则不标记（保正确性优先于缓存收益）。

### 5.2 实测 4 种 tool 消息格式（2026-08-19）

对生产端点（dashscope.aliyuncs.com/compatible-mode/v1，qwen3.7-flash，enable_thinking=false）构造 agent 工具轮消息实测：

| 格式 | tool 消息写法 | 小结果 | 大结果（6.5K） | 8 次重复 |
|------|--------------|--------|--------------|---------|
| A（bug 行为） | 顶层 `tool_call_id` + content 数组 | 正常 | 正常 | 全正常（未复现回显） |
| B（官方推荐） | content 块内 `tool_call_id` + 数组 | 正常 | 正常 | 全正常 |
| C（纯字符串） | 纯字符串 content，无 cache_control | 正常 | 正常 | — |
| D（本次修复） | 纯字符串 tool + cache_control 落在 user 上 | 正常 | 正常 | — |

**关键结论**：格式 A（bug 行为）实测 20+ 次正常却真实出过 1 次事故，证明**回显风险无法靠小样本排除**。qwen 官方文档（[ext/qwen-llm-context-cache.md](../../ext/qwen-llm-context-cache.md) §合并工具结果）虽然展示"块内 tool_call_id + cache_control"写法（格式 B），但同样无法排除风险。

**决策**：按项目「稳定性和可预测性优先于创造力」设计原则，**正确性优先，不冒回显风险**——采用格式 D/C（tool 消息保持纯字符串），接受工具轮缓存相对退化（绝对成本 ~0.001 元/次）。后续如需进一步压成本，正确方向是 [plan-tool-result-truncation.md](../plans/plan-tool-result-truncation.md)（Phase 2 工具结果截断），而非冒险用非标准格式。

### 5.3 测试结果

| 测试 | 结果 |
|------|------|
| `tests/unit/test_qwen_provider_thinking_cache.py`（含更新后的 `test_cache_on_agent_loop_tool_result`） | 18 passed |
| LLM 相邻回归（failover / max_tokens / subagent_llm_config） | 77 passed |

## 六、备选方案评估与 reviewer 第一手复核（2026-08-19）

### 6.1 第一手日志复核结论（独立于原排查过程）

reviewer 直连 api2/api3 容器复核原始日志，确认：

1. **回显确实来自模型本身**：事故调用（api3 14:35:02）`choices[0].message.content` 即那段 tool_result JSON（13140 字符），`finish_reason=stop`、无 tool_calls、`completion_tokens=9305`。排除"我方代码兜底写入"等替代解释。
2. **概率性成立**：api2 同日 31 次 qwen3.7-flash 调用中 13 次末尾 tool 消息同样数组化，全部正常；14 次中 1 次（~7%）与结论吻合。
3. **回显中的 `tool_call_id` 是模型编造的**：回显里的 `call_4028e3a9...` 与请求实际 id（`call_163d2d04...`）不一致，且该 id 在全部 5 个容器近三天日志中仅出现在事故响应自身。这排除了"跨会话缓存污染/缓存串号"的替代根因，印证"模型被非法格式搞混、按 Anthropic 语义幻觉输出"的机制解释。

### 6.2 备选方案一：官方"块内 tool_call_id"格式（格式 B）——否决

| | 格式 A（事故） | 格式 B（官方文档） |
|---|---|---|
| tool 消息 content | 数组 + 顶层 `tool_call_id` + `cache_control` | 数组 + 块内 `tool_call_id`，**无顶层 id** |
| OpenAI 兼容规范 | 违反 | 同样违反 |
| Anthropic 规范 | 不符合 | 也不符合（Anthropic 是 `type:"tool_result"` + `tool_use_id`） |

- 格式 B 是**只存在于 qwen 文档里的杂交格式**，两个生态都没有它；本次事故恰恰证明 qwen3.7-flash 对"数组 content 的 tool 消息"按 Anthropic 语义概率性出错，风险类别不变。
- 格式 B 仅实测 8 次正常——格式 A 当初 20+ 次正常仍出事故，小样本排除不了低概率 bug（本次复盘经验第 3 条）。
- 丢掉顶层 `tool_call_id` 后，若 qwen 某版本对块内 id 解析失败，工具结果会整体对不上号，是比回显更难排查的静默错误。
- 官方推荐它的场景是**并行工具调用合并**（20 块回溯窗口问题），并未对"串行工具轮末条标记"背书。

### 6.3 备选方案二：标记非末尾的 tool 消息——否决

思路：工具轮 2 把标记打到工具轮 1 的 tool 消息上（非末条），缓存块覆盖 `[sys..tool1]`，每轮只付增量，缓存效果≈bug 行为，长工具链从平方级重复计费降回线性。

否决理由：

- 该形态**就是格式 A 换了个位置**（顶层 id + 数组 content + cache_control），规范违规程度相同。
- **零样本**：bug 代码每次调用从原始消息重新格式化，数组化只出现在末条——"非末尾数组 tool 消息"在全部历史日志中一次都没发生过，无任何安全性证据。"发疯与末尾位置相关"只是猜想，14 个观测样本中位置与 outcome 无任何可分辨信号。
- 结论：若未来流量上升后该成本变得可观，本方案与格式 B 应**一起**进浸泡测试（数百次、含 6K+ 大结果与多轮链、统计回显率）；本方案保留了顶层 `tool_call_id`，届时比格式 B 更值得测。

### 6.4 成本量化修正与边界说明

- §4.3 按"单次工具轮 ~5K token"估 0.001 元/次偏乐观：**多轮连续工具链**（如 api2 当日 4->6->8->10->12 消息的链）下，每轮标记都回退到 user 消息，前面累积的 tool 结果每轮按 100% 重复计费（平方级）。按当日流量（14 次末尾 tool 调用、尾部平均 ~3K token）测算，修复的缓存损失约 **0.01 元/天**量级，决策结论不变。
- 官方文档约束：缓存标记须在距数组末尾 **20 个 content 块**以内才能命中。当前修复在"单轮连续 10+ 次工具调用"的会话中标记可能超出窗口，该次调用静默退化为无缓存（正确性无影响，成本上界=一次不缓存）。

### 6.5 agent 层兜底加固

作为模型侧异常的最后防线，主智能体与子智能体循环在收到"无 tool_calls 且 content 以 `[{"id":...` 开头且头部含 `"tool_result"`"的回复时，**重试一次 LLM 调用**；重试失败沿用原响应（保持与无加固时一致的失败语义）。检测函数 `Agent._is_tool_result_echo`（src/core/agent.py），单测 `tests/unit/test_agent_tool_result_echo_guard.py`。

## 七、复盘经验

1. **tool 消息 content 必须保持纯字符串**（OpenAI 兼容规范硬约束），任何把它改写成 content 数组的逻辑都是违规——缓存标记若要放在 tool 消息上，必须走官方文档明确推荐的"块内 tool_call_id"格式，且仍存在概率性回显风险。
2. **`cache_control: {type: "ephemeral"}` 是 Anthropic 生态写法**，不是 OpenAI 规范；qwen（百炼）为兼容 Anthropic 工具链而支持。带 cache_control 的 content 数组会被 qwen 按 Anthropic 语义解读（这正是回显成 Anthropic tool_result 格式的原因）。
3. **低概率 bug 无法靠小样本复现证明不存在**：格式 A 实测 20+ 次正常却真实回显过 1 次（今天 ~7%）。必须靠全量日志统计（14 次中 1 次）论证风险真实存在，再按"正确性优先"取舍。
4. **缓存标记的位置选择**：agent 循环中消息数组末尾可能是 tool 消息，标记必须从后往前跳过 `tool` / `assistant(tool_calls)`，落在 content 非空的纯文本消息上；找不到则宁可不标记，保正确性优先于缓存收益。备选方案（官方块内 tool_call_id 格式、非末尾 tool 标记）评估见 §6.2/§6.3，均因风险类别未变而否决。
5. **成本权衡要量化**：相对比例（10%→100%）看起来吓人，但绝对金额（~0.001 元/次）和整体对比（旧策略全量 100%）才是决策依据。多轮工具链的累积退化（§6.4）也需一并量化。

## 八、关联文档

- [qwen3.7-flash 上下文缓存优化方案](../plans/plan-qwen3-7-flash-context-cache-optimization.md)（引入"末尾标记"策略的原始方案，Phase 2 工具结果截断待做）
- [deepseek-v4-flash 平替模型调研](./deepseek-v4-flash-replacement-research.md)（qwen3.7-flash 选型依据）
- [qwen 官方上下文缓存文档快照](../../ext/qwen-llm-context-cache.md)（含"合并工具结果 + 块内 tool_call_id"官方写法）
