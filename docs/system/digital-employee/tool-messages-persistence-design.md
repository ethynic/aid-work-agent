# 工具消息持久化设计（事务性）

> **关联**：
> - 父功能：[数据分析智能体](../../ideas.md) #9，本设计是其上下文失效问题的根因修复
> - 上游设计：[数据分析工具返回结构优化](data-analysis-tool-result-redesign.md)
>
> **状态**：📋 待开发
> **创建日期**：2026-06-12

---

## 一、问题根因（已用证据链确认）

### 1.1 现象

主智能体第二次调用时不复用上次工具结果，重跑 `analyze_data`。

### 1.2 证据链

1. `chat_messages` 表中 `session_88a46ba67f11` 只有 8 条 user/assistant 消息，**0 条 tool 消息**。
2. `src/main.py:1620-1651` 持久化逻辑只存 user + assistant 两条，**不存 tool 消息**。
3. tool 消息（`role:"assistant"` with `tool_calls` + `role:"tool"` with `tool_call_id` + 工具结果 JSON）只进了进程内 `ShortTermMemory` 的 deque（`src/memory/short_term.py:55`）。
4. gunicorn 默认 `workers=3`（`deploy/gunicorn.conf.py:19`），请求轮询命中不同 worker 时，新 worker 的 memory deque 为空。
5. 新 worker 处理请求时 `_build_messages`（`src/core/agent.py:1079`）从 memory 重建 messages，**工具结果不存在**，主智能体 LLM 看不到上次产物。

### 1.3 影响范围

**所有工具调用结果跨 worker 都会丢失**，不只是数据分析。数据分析因产物重（图表/大表）最先暴露，其他工具（如 search、email）的上下文同样丢失，只是用户感知不明显。

### 1.4 为什么 P0 改造没解决

`data-analysis-tool-result-redesign.md` 改的是「工具返回什么结构」（conclusion + artifacts），但**没解决「工具返回能否被下一轮 LLM 看到」**。结构再好，跨 worker 丢了等于白搭。

---

## 二、设计目标

1. **事务性**（用户强约束）：tool 消息与最终 assistant 回复**作为一个事务写入**，要么全部成功，要么全部失败。不允许出现「工具调用存了但最终回复没存」的孤立状态。
2. **配对完整**：每条 `assistant(tool_calls)` 必须紧跟其 `role:tool` 响应消息，避免 `_build_messages` 因配对断裂而清理（`agent.py:1133-1151` 的清理逻辑会移除未配对 tool_calls）。
3. **跨 worker 一致**：消息从 DB 加载，不依赖进程内内存。
4. **向后兼容**：历史 session（无 tool 消息）继续可用；新 session 自动享受新机制。

---

## 三、设计方案

### 3.1 核心障碍与解法

**障碍**：`Agent.process_message` 是 async generator，对外只 yield 扁平事件（`tool_start` / `tool_result` / `response` 等），**不暴露结构化的 tool 消息序列**。`main.py` 即使想持久化也拿不到原始的 `tool_call_id` 和 `tool_calls` 配对。

**解法**：在 `process_message` 末尾追加一个 `tool_messages` 事件（或在 `complete` 事件中搭载），把本轮完整的 tool 消息序列（含配对的 assistant.tool_calls + role:tool）一次性吐给 `main.py`。

```python
# agent.py process_message 末尾，break 出循环后、return 前
tool_messages_for_persist = []
for m in messages[initial_len:]:  # 跳过历史，只取本轮新增
    if m.get("role") == "assistant" and m.get("tool_calls"):
        tool_messages_for_persist.append({
            "role": "assistant",
            "content": m.get("content", ""),
            "tool_calls": m["tool_calls"],
        })
    elif m.get("role") == "tool":
        tool_messages_for_persist.append({
            "role": "tool",
            "tool_call_id": m["tool_call_id"],
            "content": m["content"],  # 工具结果 JSON 字符串
        })

yield make_event("tool_messages", messages=tool_messages_for_persist)
```

`main.py` 收集这个事件，在 SSE 流结束后连同 user/assistant 一起事务写入。

### 3.2 事务持久化（核心改动）

**位置**：`src/main.py:1614-1652`，SSE 流结束后的 DB 写入段。

**当前逻辑**（两条独立 INSERT）：
```python
MessageDB.create(session_id, "user", full_message, user_metadata)      # 1620
MessageDB.create(session_id, "assistant", full_response, assistant_meta) # 1646
```

**改造后逻辑**（一个事务，N 条 INSERT）：

```python
# 伪代码
all_messages_to_persist = []
all_messages_to_persist.append(("user", full_message, user_metadata))
for tm in tool_messages_collected:  # 来自 tool_messages 事件
    # tm 已是 LLM 原生格式：role=assistant(含 tool_calls) 或 role=tool(含 tool_call_id)
    all_messages_to_persist.append((tm["role"], tm["content"], tm))
all_messages_to_persist.append(("assistant", full_response, assistant_metadata))

# 事务写入：要么全成功，要么全失败
MessageDB.create_batch_transactional(session_id, all_messages_to_persist)
```

**关键点**：
- 用 PostgreSQL 单连接 + 显式 `BEGIN/COMMIT/ROLLBACK`，保证原子性
- 失败时整体回滚，**不留下任何孤立消息**（user 也不存）——这是用户「成对出现」要求的体现
- `create_batch_transactional` 是 `MessageDB` 新增方法

### 3.3 role 字段值约定

chat_messages 表的 `role` 是 TEXT 无约束（已确认）。**复用 LLM API 原生消息格式**，不引入新 role 值：

| role 值 | 含义 | content | metadata |
|---|---|---|---|
| `user` | 用户输入（不变） | 用户原文 | attachments / progressMessages |
| `assistant`（最终回复） | LLM 最终文字回复 | **完整保留** | `{progressMessages, downloadableFiles?}` |
| `assistant`（调工具） | LLM 决定调用工具 | **空字符串**（丢弃中间思考，节省存储） | `{tool_calls: [...], reasoning_content?: "..."}` |
| `tool` | 工具执行结果 | 工具结果 JSON 字符串 | `{tool_call_id: "...", is_error?: bool}` |

**为什么不引入 `assistant_tool_calls` 新 role**：
- 与 LLM API 原生消息格式（OpenAI / DeepSeek / Qwen 一致）对齐，避免转换层
- 读取时按 `metadata.tool_calls` 是否存在区分，逻辑清晰
- 表结构最干净，不增加 role 枚举

**为什么 content 可以丢弃（存空字符串）**（关键研究结论）：
- assistant(tool_calls) 的 content 是 LLM 调用工具前的「中间思考陈述」（如「好的，我重新生成柱状图」「让我加载这个表」），属于辅助性文字
- LLM API（OpenAI / DeepSeek / Qwen）对 assistant(tool_calls) 消息的 content **允许为空字符串**，重建后喂给 LLM 不报错、不影响功能
- 重建时 LLM 只依赖 `tool_calls`（调了什么工具）+ 后续 `role:tool` 消息（工具返回什么），不依赖中间思考文字
- 丢弃 content 可节省 chat_messages 表存储（部分场景 content 可达数 KB）

**但 `reasoning_content` 必须保留**：
- DeepSeek 思考模式的 reasoning 是完整推理链条，丢失会导致多轮工具调用时模型行为漂移
- reasoning_content 存入 metadata（不占 content 列），重建时从 metadata 恢复

**注意**：仅 assistant(tool_calls) 场景丢弃 content。最终文字回复（无 tool_calls 的 assistant）的 content **必须完整保留**，那是给用户看的最终答案。

### 3.4 _build_messages 改造（从 DB 恢复）

**位置**：`src/core/agent.py:1079` `_build_messages`，或 memory 的加载入口。

**当前逻辑**：从 `self.memory.get_context(session_id)` 读 deque（进程内）。

**改造方案**：memory 命中时走原路径（兼容）；memory 为空（新 worker）时从 chat_messages 表加载，识别新 role 值并转换为 LLM 消息格式：

```python
# 伪代码：DB → LLM messages 转换
for row in db_messages:
    role = row["role"]
    meta = json.loads(row["metadata"]) if row["metadata"] else {}
    if role == "user":
        messages.append({"role": "user", "content": row["content"]})
    elif role == "assistant":
        msg = {"role": "assistant", "content": row["content"] or ""}
        if meta.get("tool_calls"):
            msg["tool_calls"] = meta["tool_calls"]      # 决定调工具的 assistant
        if meta.get("reasoning_content"):
            msg["reasoning_content"] = meta["reasoning_content"]
        messages.append(msg)
    elif role == "tool":
        messages.append({
            "role": "tool",
            "tool_call_id": meta["tool_call_id"],
            "content": row["content"],
        })
```

转换后走原 `_build_messages` 的配对校验逻辑（line 1106-1190），孤立的 tool 消息仍会被跳过（但事务保证不会出现孤立）。

**memory 加载入口的选择**：
- 方案 i：改 `_build_messages`，命中 memory 用 deque，否则查 DB
- 方案 ii：改 `ShortTermMemory.get_context`，deque 为空时从 DB 恢复并填充 deque（后续命中走快路径）

倾向方案 ii——`get_context` 是唯一入口，改一处全场景生效，且首次加载后缓存进 deque，后续同 worker 内的请求不再查 DB。

### 3.5 事务边界与失败处理

| 场景 | 行为 |
|---|---|
| 全部 INSERT 成功 | COMMIT，消息可见 |
| 任一 INSERT 失败 | ROLLBACK，**所有消息（含 user）都不写入** |
| SSE 流中途中断（客户端断开） | 不进入持久化段（`full_response and not error_occurred` 判断） |
| Agent 抛异常 | `error_occurred` 置位，跳过持久化（保持现有行为） |
| tool_messages 事件未收到（兼容旧 agent） | tool_messages_collected 为空，退化为原两条 INSERT |

**用户「成对出现」要求的体现**：user + N 条 tool 相关 + assistant 最终回复，要么全在要么全不在。绝不会出现「只看到工具调用记录但看不到最终回复」或「只看到回复但不知道之前调了什么工具」。

### 3.6 数据膨胀控制

工具结果 JSON 可能很大（如数据分析的 artifacts 带 10 行 preview）。控制策略：

1. **单条 content 超过阈值（如 50KB）时截断**，metadata 里标记 `truncated: true` 并记录原始长度。LLM 看到截断后内容 + 提示「完整结果见 trace_id」。
2. **chat_messages 表本身不做归档**（已有 `chat_records` 表做用量统计/审计，职责分离）。
3. 监控：上线后观察 chat_messages 平均行数变化，必要时引入定期归档（P2，不在本次范围）。

---

## 四、改动清单

| # | 文件 | 改动 | 优先级 |
|---|---|---|---|
| 1 | `src/core/agent.py` `process_message` | 末尾 yield `tool_messages` 事件，搭载本轮 tool 消息序列 | P0 |
| 2 | `src/db/models.py` `MessageDB` | 新增 `create_batch_transactional(session_id, messages)` 方法，事务批量写入 | P0 |
| 3 | `src/main.py:1614-1652` | 收集 `tool_messages` 事件；持久化段改为调用 `create_batch_transactional` | P0 |
| 4 | `src/memory/short_term.py` `get_context` | deque 为空时从 chat_messages 表恢复（含新 role 识别），填充 deque | P0 |
| 5 | （可选）`src/core/agent.py` `_build_messages` | 加日志：DB 恢复了多少条 tool 消息，便于调试 | P1 |

---

## 五、确认事项（已确认）

1. **role 结构**：复用 LLM API 原生格式，`assistant`（metadata 里带 `tool_calls` 区分是否调工具）+ `tool`（metadata 里带 `tool_call_id`）。不引入新 role 值。
2. **content / reasoning_content 处理**：assistant(tool_calls) 的 content 存空字符串（丢弃中间思考，不影响功能）；reasoning_content 必须保留（存 metadata，DeepSeek 推理链不能丢）；最终回复的 content 完整保留。
3. **content 截断阈值**：50KB，超出截断并在 metadata 标记 `truncated: true`。
4. **历史数据**：不回填。旧 session 按 user/assistant 处理，丢失的工具结果无法重建。

---

## 六、验收标准

1. **场景一：单 worker 内连续对话**（回归）—— 行为不变，工具结果在 memory deque 中可用。
2. **场景二：跨 worker 对话**（核心修复）—— 第一次问「Top10 合同柱状图」生成图；切换 worker 后追问「把图给我下载」，主智能体从 DB 恢复 tool 消息，**直接复用 artifacts 中的 download_path 调 cp 工具**，不重跑 analyze_data。
3. **场景三：事务失败**—— 模拟持久化中间失败，确认 user/tool/assistant 全部未写入（`SELECT COUNT(*)` 为 0），无孤立消息。
4. **场景四：旧 session 兼容**—— 打开历史 session（无 tool 消息），对话正常，不报错。

验收时通过 `chat_messages` 表查询确认：每次完整对话后，消息条数 = 1 (user) + N (assistant_tool_calls + tool 配对) + 1 (assistant)，且无孤立记录。
