# 企业微信客服上下文丢失调研报告

> **关联问题**：用户反馈通过企业微信客服（wecom_kf）与 agent 对话时，连续两次提问之间，上一次的 assistant 回复没有进入下次对话的上下文。具体表现为：上下文 #97（上次 user）与 #98（本次 user "可以的"）之间缺少了上次 assistant 的回复，导致 agent 误以为"可以的"是确认某些它根本不知道的内容，进而回答错乱。
>
> **登记**：本文档需登记到 `docs/ideas.md`「渠道集成」分区，并关联开发计划。

## 1. 问题现象

| 项 | 内容 |
|----|------|
| 渠道 | 企业微信客服（wecom_kf） |
| 上次 user 消息 | "保持 4 天，天眼可以去掉" |
| 本次 user 消息 | "可以的" |
| 现象 | 下次加载历史时，上次 user 与本次 user 之间无 assistant 回复 |
| 后果 | LLM 上下文错乱（连续两条 user），agent 胡乱推断"可以的"的指代对象 |

## 2. 数据流回顾

wecom_kf 消息处理流程（`src/saas/api/channel_routes.py::_process_tenant_wecom_kf_messages`）：

```
1. 收到微信消息 → 解析 → ensure_user_registered → 拿到 session_id
2. channel_session_manager.add_message(session_id, role="user", content=...)
   └─ 写入 channel_messages 表
3. agent = agent_router.get_agent(subagent_type, session_id, tenant_id)
4. record_service = SessionRecordManager.start_record(...)
5. response_text = await session_queue.enqueue_and_process(session_id, user_input, _processor)
   └─ 内部调用 agent.process_message_sync(...)
6. 异常 → continue（跳过保存 assistant 消息，但 user 消息已写入）
7. response_text 为空 → continue（同上）
8. 写入 tool_messages 序列（每条独立 add_message）
9. 写入最终 assistant 消息（add_message）
10. 通过 adapter.send_message 发送回复给用户
```

下次用户消息到达时：
- `agent.py::_rebuild_memory_from_db` 走 fallback：`_load_channel_history` → `channel_session_manager.get_messages`
- 拿到的历史按 `id ASC` 排序，最后一条若是当前 user 输入则移除
- 调用 `_reorder_messages_for_llm` 重建合法 LLM 消息序列

## 3. 根因分析

排查了所有可能让"上次 assistant 回复"缺失的路径，得到以下 5 类原因，按可能性排序：

### 3.1 主因：channel_messages 写入非事务（P0，最可能）

`channel_session_manager.add_message`（`src/channels/session.py:325`）每次调用都独立 `INSERT + COMMIT`。
当本轮需要写入 **N 条 tool_messages + 1 条 assistant 最终回复**（`channel_routes.py:2079-2112`）时，任意一条失败：

- tool_messages 已写入，但 assistant 最终回复写入失败（DB 抖动、连接超时）
- `_load_channel_history` 加载到末尾是 tool 消息
- `_reorder_messages_for_llm` 把这些 tool 视为**孤儿 tool**（无配对 `assistant(tool_calls)` 前驱），全部跳过

结果：历史里看到 `user(上次) → [孤立 tool 被丢弃] → user(本次)`，正是用户截图 #1 的现象。

> **验证手段**：查 chat_records 表，过滤出 `success=false` 或 `error_msg` 非空的记录。如果同一 session_id 在问题时刻附近有 DB 错误日志，基本可坐实。

### 3.2 次因：异常路径跳过 assistant 写入（P1）

`channel_routes.py:2062-2066`：

```python
except Exception as e:
    logger.error(f"[wecom_kf] Agent 处理异常: {e}")
    record_service.mark_error(str(e))
    SessionRecordManager.end_record()
    continue  # ← 跳过下方所有 add_message
```

如果 `enqueue_and_process` 抛异常，user 消息已写入，assistant 消息完全缺失。下次上下文里直接是 `user → user`。

> 但用户描述"上次问题有工具调用过程和最终结果"，所以这条不太符合现象。但仍是需要修的健壮性缺口。

### 3.3 次因：合并/取消导致 response_text 为空（P1）

`channel_routes.py:2057-2060`：

```python
if not response_text:
    SessionRecordManager.end_record()
    continue
```

`session_queue.enqueue_and_process` 在以下两种场景返回空串：
- 处理中态（允许取消）：旧请求处理合并后输入，本调用方返回空
- 处理中态（已推送）：新消息进入 pending，本调用方返回空

如果旧请求在合并重新处理过程中又被取消（比如 agent iteration 循环检测到 cancel 提前 return，见 `agent.py:1790-1792`），最终谁也没产生回复，但 user 消息已经写入了。

> 用户截图 #2 显示上次问题最终有完整回复，所以这条也不是本次主因。

### 3.4 边缘：`_load_channel_history` 移除最后一条 user 后窗口外溢（P2）

`agent.py:1068-1071`：

```python
if channel_msgs and channel_msgs[-1].get("role") == "user":
    last_content = channel_msgs[-1].get("content", "")
    if last_content == current_user_input:
        channel_msgs.pop()
```

`channel_session_manager.get_messages` 用 `LIMIT self.memory.short_term.max_messages + 1`，理论够用。但如果用户消息合并触发的连续两条 user，pop 一条后正好把上一轮 assistant 也排挤出窗口，会导致上下文截断。属于**长会话的边界**，不是本次主因。

### 3.5 边缘：内存清空时机（P3）

`agent.py:1555` 每次处理前 `self.memory.clear(session_id)`，再从 DB 重建。如果 DB 写入在 SSE 主路径（`src/main.py:1657` 的 `create_batch_transactional`）成功，但 channel_messages 的写入失败，下次重建走 `_load_channel_history` 时就是缺数据。但 wecom_kf **不走 SSE 主路径**（直接在 channel_routes 里跑），所以不影响。

## 4. 核心结论

| # | 根因 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | channel_messages 多条写入非事务 | tool 序列与最终 assistant 可能不一致 | P0 |
| 2 | 异常/空回复路径跳过 assistant 写入 | 历史里出现连续 user | P1 |
| 3 | 缺少"连续两条 user"兜底清洗 | 错乱上下文直接进 LLM | P1 |

**用户怀疑"没插入到数据库"是有可能的**：场景就是 3.1 中 tool_messages 已写入、最终 assistant 写入失败，或 3.2/3.3 中 assistant 整段没写入。

## 5. 优化方案

### 方案 A：事务化 channel_messages 写入（P0，必做）

参考 SSE 主路径 `src/main.py:1627-1657` 的 `MessageDB.create_batch_transactional` 模式，给 `channel_session_manager` 增加 `add_messages_batch_transactional`：

```python
def add_messages_batch_transactional(
    self, session_id: str, tenant_id: str, messages: List[Dict]
) -> Optional[List[Dict]]:
    """事务性批量写入。所有消息要么全成功要么全回滚。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            for msg in messages:
                cursor.execute("""INSERT INTO channel_messages ... VALUES (...)""", (...))
            cursor.execute("""UPDATE channel_sessions SET last_message_at = ... WHERE session_id = ...""", ...)
            conn.commit()
            return messages
        except Exception:
            conn.rollback()
            logger.error(f"batch insert failed", exc_info=True)
            return None
```

`channel_routes.py:2079-2112` 改为构造 list 后一次性写入：

```python
batch = []
for tm in tool_messages_collected:
    if tm.get("role") == "assistant" and tm.get("tool_calls"):
        batch.append({"role": "assistant", "content": "", "metadata": {...}})
    elif tm.get("role") == "tool":
        batch.append({"role": "tool", "content": tc, "metadata": {"tool_call_id": ...}})
batch.append({"role": "assistant", "content": response_text, "metadata": assistant_metadata})

created = channel_session_manager.add_messages_batch_transactional(
    session_id, tenant_id, batch
)
if created is None:
    logger.error(f"[wecom_kf] 批量写入 channel_messages 失败，session={session_id}")
    # 不阻断回复发送（已经算出来了），但要记 error
```

### 方案 B：异常/空回复路径补齐 user 消息回滚或兜底（P1）

异常分支（`channel_routes.py:2062-2066`）和空回复分支（2057-2060）中，**至少保证 channel_messages 末尾写入一条 assistant 占位**（如 `"[本次回复生成失败，请重试]"`），避免末尾是 user。

可选更激进方案：异常时**回滚本次写入的 user 消息**（用 message_id 删除），但这会丢失审计数据，不推荐。

### 方案 C：_reorder_messages_for_llm 兜底防止连续 user（P1，最重要）

在 `agent.py:_reorder_messages_for_llm` 输出阶段增加清洗：

```python
# 兜底：合并连续的 user 消息，仅保留最后一条（避免上下文错乱）
cleaned = []
for msg in messages:
    if (msg["role"] == "user" and cleaned and cleaned[-1]["role"] == "user"):
        # 跳过前一条 user（已经错乱了，保留最新的）
        logger.warning(f"后端日志：检测到连续 user 消息，丢弃较早的一条")
        cleaned.pop()
    cleaned.append(msg)
return cleaned
```

或更保守的做法：保留两条但用一条 `assistant: "[上一次回复生成失败]"` 分隔。

### 方案 D：channel_messages 写入失败时降级告警（P2）

`add_message` 单条写入失败目前是静默异常。建议捕获后通过 TraceCollector 或 logger.error 明确记录，便于事后定位。

## 6. 落地清单

> **排序原则**：根治优先于兜底。先解决"产生连续 user 的根因"，再加兜底防御残留。

| 序号 | 改动 | 文件 | 优先级 | 说明 |
|------|------|------|--------|------|
| 1 | `add_messages_batch_transactional` | `src/channels/session.py` | **P0-1** | 写入一致性基础（独立问题） |
| 2 | wecom_kf 处理路径改批量写入 | `src/saas/api/channel_routes.py:2079-2112` | **P0-1** | 配合 1 落地 |
| 3 | 推迟 user 消息写入到合并决策之后（E1） | `src/saas/api/channel_routes.py:1984` + `src/core/session_queue.py` | **P0-2** | 根治连续两条 user 源头 |
| 4 | `_reorder_messages_for_llm` 连续 user 兜底 | `src/core/agent.py:1152-1229` | **P0-3** | 兜底：历史脏数据 + 未改造渠道 + 极端 race |
| 5 | 异常/空回复路径兜底写入 assistant 占位 | `src/channels/...` 各渠道统一 | P1 | |
| 6 | `add_message` 失败明示日志/告警 | `src/channels/session.py:325` | P2 | |
| 7 | cancel_check 在 LLM streaming 中也检查 | `src/core/agent.py` | P1 | 降低 cancel 延迟 |

## 7. 补充：连续 user 消息合并机制失效场景分析

> 用户反馈：日志中观察到微信客服渠道连续两条 user 消息，LLM 当时确实理解了两条合并的内容，但 channel_messages 里仍是两条独立 user。怀疑合并机制有失效场景，需要排查。

### 7.1 现有合并机制全景

wecom_kf 有**两层**合并：

| 层 | 位置 | 时机 | 范围 |
|----|------|------|------|
| L1 | `channel_routes.py:1634 _merge_consecutive_user_messages` | 同一个 sync_msg 批次内 | 同一批次拉到的多条消息 |
| L2 | `session_queue.enqueue_and_process` | 跨批次，基于 Redis 状态机 | 同 session_id 任意时刻到达 |

L2 状态机（`src/core/session_queue.py`）：

```
首条消息 A 到达：
  acquire_lock → set_merge(A) → 等待 2s 合并窗口 → 跑 processor(merged) → 写回复 → release_lock

A 处理中（已锁），B 到达：
  if is_cancel_allowed(session):   # 未 mark_responding
      set_cancel + append_merge(B)  # B 追加到合并缓冲区
      等 A 处理完
      A 的 _handle_cancel_and_reprocess 检测到 cancel + 合并输入不同 → 重跑 processor(A\nB)
      B 调用方返回 ""（不发送回复）
  else:  # 已 mark_responding（极少见窗口）
      set_pending(B)
      等 A 处理完
      A 释放锁前 has_pending → 跑 processor(B) → 返回 B 的独立回复
```

### 7.2 失效场景逐个排查

#### 场景 A：**channel_messages 写入时机错误**（核心问题，确定存在）

代码顺序（`channel_routes.py:1983-2051`）：

```python
# 1. 立即写 user 消息（先于合并决策）
channel_session_manager.add_message(role="user", content=user_content, ...)

# 2. 之后才进入 session_queue 决定合并/排队
response_text = await session_queue.enqueue_and_process(...)
```

**问题**：user 消息的写入**先于**合并决策。即使 L2 决定合并（cancel + append_merge + reprocess），channel_messages 里**仍然已经写了两条独立的 user(A) 和 user(B)**。

**最终 channel_messages 状态**：
```
user(A)
user(B)
[可能部分 A 的 tool_messages（cancel 时非事务写入的残留）]
assistant(A+B 合并回复)
```

**LLM 实际看到的输入**（当时）：`A\nB`（合并）
**历史存储**：`user(A) → user(B) → assistant`
**下次加载时 LLM 看到**：`user(A) → user(B) → assistant(A+B)` + 当前新输入

**结论**：存储与实际不一致。用户日志观察到的"连续两条 user 但 LLM 理解了"完全符合此场景。**这是合并机制最大的隐患**。

#### 场景 B：cancel_check 延迟生效导致 B 等待过长（潜在）

`agent.py:1790` 的 cancel_check 只在 iteration 循环边界检查。如果 A 处于：
- 首次 LLM 调用中（HTTP 请求已发出，等待响应，可能 3-10s）
- 工具执行中（外部 API 调用，可能更久）

cancel 不能立即生效，B 会等到 A 当前 iteration 步骤结束才能触发重跑。期间 channel_messages 状态是 `user(A) → user(B)`（无 assistant）。

**不会导致上下文错乱**，因为 A 的 processor 会 return，重跑会写正确的合并 assistant。但响应延迟会让用户体验差。

#### 场景 C：A 已完成但还在 send_message 推送瞬间（罕见但存在）

代码顺序（`channel_routes.py:2122-2125`）：

```python
session_queue.mark_responding(session_id)  # 标记已推送
send_result = await adapter.send_message(response)  # 实际推送
session_queue.mark_idle(session_id)  # 清除标记
```

`mark_responding` 与 `mark_idle` 之间的窗口内（几十毫秒到数秒，取决于微信 API 响应），B 到达会走 pending 分支，A+B 不合并，各自独立处理。channel_messages：`user(A) → user(B) → assistant(A) → assistant(B)`。

**不会导致上下文错乱**（独立处理是设计期望），但用户体感是"两条消息两条回复"。

#### 场景 D：A 的 tool_messages 已部分写入（非事务），cancel 后残留

如 3.1 主因所述，`channel_routes.py:2079-2103` 写 tool_messages 是非事务的。如果 cancel 触发时已经写了一部分：
- channel_messages: `user(A) → user(B) → tool(call_id_X) → tool(call_id_Y)`（无对应 assistant(tool_calls)）
- 重跑 processor(A\nB) → `user(A) → user(B) → tool → tool → assistant(tool_calls) → tool → tool → assistant(A+B 合并)`
- 下次加载：`_reorder_messages_for_llm` 把孤立 tool（call_id_X、call_id_Y 无配对 assistant(tool_calls)）丢弃
- 残留的乱序 tool 可能误导 LLM

### 7.3 用户日志现象对号入座

用户截图 #1：上下文 #97(上次 user) → #98(本次 user "可以的")，中间无 assistant。

**最符合的解释**：场景 A + 主因 3.1 的组合
- 上次用户连续发了"保持 4 天..."和另一条消息
- L2 合并机制触发，LLM 收到合并输入并正常回复
- channel_messages 写入了 `user(A) → user(B) → [部分 tool_messages 残留] → assistant(A+B)`
- 但 **assistant 那条写入失败**（主因 3.1：非事务），只剩 `user(A) → user(B) → tool(孤立)`
- 下次"可以的"加载历史：孤立 tool 被丢弃 → `user(A) → user(B) → user(可以的)`
- `_reorder_messages_for_llm` 当前不清洗连续 user → LLM 看到三条连续 user → 错乱

### 7.4 修复方案（在原方案上扩展）

| # | 改动 | 解决场景 | 优先级 |
|---|------|---------|--------|
| E1 | **写 user 消息推迟到合并决策之后**（场景 A 根治） | A | P0 |
| E2 | channel_messages 多条写入事务化（原方案 A） | D | P0 |
| E3 | cancel 触发后清理本批次已写入的残留消息（事务回滚的一种替代） | D | P1 |
| E4 | `_reorder_messages_for_llm` 连续 user 兜底（原方案 C） | A/B 残留 | P1 |
| E5 | 合并窗口期与 cancel_check 频率调优（缩短 LLM 调用内的 cancel 延迟） | B | P2 |

#### E1 详细方案：推迟 user 消息写入

把 `channel_session_manager.add_message(role="user", ...)` 从 `enqueue_and_process` 之前，**移到 processor 内部或 enqueue 返回后**。让 session_queue 先决定是否合并，再决定写几条 user：

```python
# 伪代码 - 改造 channel_routes.py
async def _processor(cancel_check):
    # agent 处理时已经把 user_content 作为输入，但写入 channel_messages 推迟到 enqueue 返回
    return await agent.process_message_sync(...)

response_text = await session_queue.enqueue_and_process(
    session_id=session_id,
    user_input=user_input,
    processor=_processor,
)

# enqueue 返回后，根据是否被合并决定怎么写
# 问题：enqueue_and_process 当前不暴露"是否被合并"的信息
```

**实施难点**：当前 `enqueue_and_process` 返回值只是 response_text，调用方无法知道：
- 这条消息是被合并（不发送回复，返回空）？
- 还是独立处理？
- 合并后的实际输入是什么？

**改造方向**（更彻底）：
1. 让 `enqueue_and_process` 返回一个结构体 `{response_text, merged_input, was_merged, was_pending}`
2. channel_routes 根据返回值决定写几条 user：
   - 独立处理：写 1 条 user（当前消息）
   - 被合并且本调用方不发送回复：**不写** user（已由合并方写入）
   - 被合并且本调用方是合并方：写 1 条 user（合并后的完整文本）

但这会改变合并语义（合并方写入的是合并文本，而非原始两条），需要在审计/展示层面权衡。

**更保守的方案（推荐）**：
- 保持现有写入时机（立即写原始 user 消息）
- 但在合并发生时，**用事务把已写入的 user(B) 更新为标记 "merged_into=A"** 或直接删除
- 通过 `enqueue_and_process` 的返回值/回调通知 channel_routes 做清理

#### E2 详细方案：见原方案 A（事务化批量写入）

#### E3 详细方案：cancel 时清理残留

在 `_handle_cancel_and_reprocess` 触发重跑前，给 channel_routes 一个回调清理本批次已写入的 tool_messages：

```python
# channel_routes.py
async def collect_files_callback(event):
    if event.get("type") == "tool_messages":
        tool_messages_collected.extend(event.get("messages", []))
        # 记录已写入的 message_id（add_message 返回值），便于 cancel 时清理
```

但当前 `add_message` 的写入发生在 `enqueue_and_process` **返回之后**（不是 processor 内部），所以 cancel 时 channel_messages 还没被写入 tool_messages。**E3 实际上不需要**，可以靠 E2 事务化解决。

#### E4 详细方案：见原方案 C

#### E5 详细方案：cancel_check 频率调优

在 LLM 调用的 streaming 循环中也检查 cancel（每 N 个 chunk 检查一次），缩短 cancel 响应延迟。这是性能优化，不在本次必须修复范围。

### 7.5 关于"排队"机制的补充确认

用户提到："应该是要等同一个渠道同一个 session 的另外一个会话完成后在处理，应该是要排队的"

**确认**：现有机制已经实现了排队（`session_pending:{sid}`），但触发条件是 `is_cancel_allowed == False`，即 `mark_responding` 已设置。而 wecom_kf 的 `mark_responding` 只在 `adapter.send_message` 调用前设置（`channel_routes.py:2122`），意味着**排队分支的触发窗口非常窄**（几毫秒到几秒）。

绝大多数场景下，A 还在 LLM/工具执行阶段，B 进来会走"cancel + 合并"分支，而不是排队。**这符合"宁可合并也不排队"的设计意图**（更快响应）。

但是当 A 已经生成完最终响应、正在推送时，B 进来会排队——这时 A+B 是独立处理两次，而不是合并。**这是合理的设计**，因为 A 的回复已经计算出来，丢弃重算代价高。

**真正的问题不是排队机制本身**，而是 **场景 A 的"立即写 user 消息"导致存储状态与合并语义不一致**。

### 7.6 总结：核心修复优先级

**排序原则**：根治优先于兜底。先解决"产生连续 user 的根因"，再加兜底防御残留与边界。否则兜底永远在救火，问题源头没断。

| 顺序 | 改动 | 解决问题 | 理由 |
|------|------|---------|------|
| **P0-1** | channel_messages 写入事务化（E2 / 原方案 A） | 主因 3.1 + 场景 D | 独立的写入一致性问题，与连续 user 无关，必须先做防止 tool/assistant 残留 |
| **P0-2** | 推迟 user 消息写入到合并决策之后（E1） | 场景 A 根治 | **根治连续两条 user 的源头**——不产生连续 user，就不需要兜底 |
| **P0-3** | `_reorder_messages_for_llm` 连续 user 兜底（E4 / 原方案 C） | 残留防御 | 兜底是最后保险，防御 P0-2 的边界：①已上线的脏历史数据；②其他渠道未同步改造；③合并 cancel 时序的极端 race condition |
| **P1** | cancel_check 在 LLM streaming 中也检查（E5） | 场景 B 体验优化 | 非必须，降低 cancel 响应延迟 |

P0 三项是必须做的最小集，顺序不能颠倒：P0-1 保证写入一致性 → P0-2 根治源头 → P0-3 兜底残留。

## 8. 落地方案：一次改造全渠道复用

> **核心诉求**：所有第三方渠道（wecom_kf / wecom / wecom_personal_rpa / dingtalk / feishu）共用一套处理路径，不为每个渠道重复改造。P0-1（事务化）和 P0-2（推迟 user 写入）必须**下沉到共享层**，而不是在每个渠道路由里改一遍。

### 8.1 现状盘点：5 个调用点的共性与差异

所有渠道处理路径都通过 `channel_session_manager` 单例 + `session_queue` 单例：

| 步骤 | wecom_kf | wecom (租户) | wecom_personal_rpa | dingtalk | feishu |
|------|----------|--------------|---------------------|----------|--------|
| ① user 写入时机 | enqueue 前（1984） | enqueue 前（434） | enqueue 前（~440） | enqueue 前（推断） | enqueue 前（推断） |
| ② enqueue_and_process | ✓ | ✓ | ✓ | ✓ | ✓ |
| ③ 写 assistant 单条 | ✓ | ✓ | ✓ | ✓ | ✓ |
| ④ 写 tool_messages 序列 | ✓（独有） | ✗ | ✗ | ✗ | ✗ |
| ⑤ send_message | ✓ | ✓ | ✓ | ✓ | ✓ |

**关键差异点**：
1. **wecom_kf 多写了 tool_messages 序列**（其他渠道缺失，已是另一个 bug——上下文里 tool_calls 信息丢失）
2. **user_input 来源不同**（`message.text` / `user_text` / `unified_msg.text`）
3. **send_message 后 send_result 检查**（wecom_kf 检查，部分不检查）

**共性足以封装**：差异点用参数透传即可。

### 8.2 设计：`ChannelSessionManager.process_and_persist` 统一封装

在 `src/channels/session.py` 的 `ChannelSessionManager` 类上新增方法，把「user 写入 + enqueue + 持久化 + 发送回复」整体封装：

```python
class ChannelSessionManager:
    # ... 现有方法保持不变 ...

    async def process_and_persist(
        self,
        *,
        session_id: str,
        tenant_id: str,
        user_content: str,                      # 原始用户消息文本
        user_metadata: Optional[Dict] = None,   # 用户消息 metadata（msgid/open_kfid 等）
        user_attachments: Optional[List[Dict]] = None,
        message_type: str = "text",
        agent: "Agent",                         # 已路由好的 agent 实例
        record_service=None,
        # send 回调：渠道侧自行实现 send_message 逻辑（UnifiedResponse 构造 + adapter.send_message）
        send_response: Callable[[str, List[Dict]], Awaitable[bool]],
        # 可选：自定义 _processor 包装（极少用，默认走 agent.process_message_sync）
        processor_factory: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """
        统一渠道消息处理路径。覆盖：
        - P0-1：tool_messages + assistant 最终回复作为事务批量写入
        - P0-2：user 消息写入推迟到合并决策之后
        - 异常/空回复路径兜底（保证不出现连续 user）

        Returns:
            {
                "status": "success" | "merged" | "error",
                "response_text": str,   # 合并/排队时为空
                "downloadable_files": List[Dict],
            }
        """
```

**关键设计点**：

#### (1) user 写入推迟

把原本「在调用方 enqueue 之前立即写 user」改为「在 process_and_persist 内部，enqueue 返回后根据合并状态决定写法」：

```python
# 内部流程
result = await session_queue.enqueue_and_process(
    session_id=session_id,
    user_input=user_content,
    processor=_processor,  # 由 process_and_persist 内部构造
)

if result.status == "merged":
    # 被合并，本调用方无需写 user（合并方会写）
    # 但要保证：合并方写入的是合并后的文本，还是两条独立 user？
    # 见下方 (3) 的权衡
    return {"status": "merged", ...}

# 独立处理或排队处理：此时写一条 user
self.add_message(session_id, role="user", content=user_content, ...)
```

#### (2) 事务化批量写入（P0-1）

`process_and_persist` 在拿到 response_text 后，把 **tool_messages 序列 + 最终 assistant** 作为事务一次性写入：

```python
batch = []
for tm in tool_messages_collected:
    if tm.get("role") == "assistant" and tm.get("tool_calls"):
        batch.append({"role": "assistant", "content": "", "metadata": {"tool_calls": ..., "reasoning_content": ...}})
    elif tm.get("role") == "tool":
        batch.append({"role": "tool", "content": tc_str, "metadata": {"tool_call_id": ...}})
batch.append({"role": "assistant", "content": response_text, "metadata": assistant_metadata})

created = self.add_messages_batch_transactional(session_id, tenant_id, batch)
if created is None:
    logger.error(f"批量写入 channel_messages 失败 session={session_id}")
    # 不阻断回复发送（已计算出来），但要记 error，便于事后定位
```

#### (3) 合并语义的权衡（关键设计抉择）

当 L2 合并触发（A 和 B 合并为 `A\nB`）时，channel_messages 怎么写？三个选项：

**选项 X（保守）**：合并方写一条 user(合并后文本 `A\nB`)，被合并方不写
- 优点：存储与 LLM 实际输入完全一致，下次加载不会错乱
- 缺点：丢失原始两条 user 的独立结构

**选项 Y（折中）**：合并方写两条原始 user，但用 metadata 标记 `merged_into_group_id` 关联
- 优点：保留原始结构
- 缺点：复杂，需要 `_load_channel_history` 配合识别

**选项 Z（彻底根治）**：合并发生时，**删除被合并方已写入的 user 消息**，合并方写入合并后文本
- 实现难点：跨进程删除（被合并方可能在另一个 worker）

**最终抉择：选项 X**

理由（基于三表分工的澄清）：

| 表 | 粒度 | 用途 | 是否用于上下文重建 |
|----|------|------|------------------|
| chat_messages | 单条消息（role/content/metadata） | web 端上下文重建 + 前端展示 | ✅ |
| channel_messages | 单条消息（role/content/metadata） | 渠道端上下文重建 + 渠道侧展示 | ✅ |
| chat_records | 一行=一次完整交互（user_msg + assistant_msg + token 统计 + execution_details） | 计费/用量/性能监控/审计 | ❌ |

**关键澄清**：
- 上下文重建**只走 chat_messages / channel_messages**（因为需要 `user → assistant(tool_calls) → tool → assistant` 这种消息流）
- chat_records **从不参与上下文重建**，它存的是「这一问一答的统计数据」，不存中间 tool_calls 序列
- 因此"选项 X 会让审计看不到原始分条"的担心**不成立**——审计走 chat_records，每条原始 user 触发 LLM 时都会独立写一行 record，与 channel_messages 是否合并无关

**选项 X 实现要点**：

```python
# enqueue_and_process 改造后返回 EnqueueResult
@dataclass
class EnqueueResult:
    status: Literal["success", "merged"]   # success=独立处理 / merged=本调用方被合并掉
    response_text: str = ""                # 合并方最终响应
    merged_input: str = ""                 # 合并方实际跑的输入（"A\nB"），独立处理时 == 原 user_input
    was_merged: bool = False               # 是否是合并方

# process_and_persist 内部
result = await session_queue.enqueue_and_process(...)

if result.status == "merged":
    # 被合并方：不写 user，不写 assistant，直接返回
    return {"status": "merged"}

# 独立处理或合并方：写入 user（合并方写 merged_input，独立方写原 user_content）
user_to_write = result.merged_input if result.was_merged else user_content
self.add_message(
    session_id=session_id,
    role="user",
    content=user_to_write,
    metadata=user_metadata,
    attachments=user_attachments,
    tenant_id=tenant_id,
)

# 然后事务化写入 tool_messages + 最终 assistant（P0-1）
batch = [...]
self.add_messages_batch_transactional(session_id, tenant_id, batch)
```

**附件合并处理**：合并发生时，A 和 B 各自的 attachments 需要合并保留。在 enqueue_and_process 触发合并前，把 B 的 attachments 通过 Redis 合并缓冲区一并存入，合并方写入时统一拼到 user_to_write 的 attachments 列表里。

#### (4) 异常/空回复兜底

`process_and_persist` 内部统一处理：
- agent 抛异常 → 不写 user（user 还没写），但 record 标记 error
- response_text 为空（合并/排队） → 不写 user，不写 assistant
- 已经写过 user 但后续步骤失败 → 在 finally 中检查最后一条是否 user，若是则补一条 assistant 占位 `"[本次回复生成失败]"`附件 metadata 在合并场景下用 list 合并即可。

```python
# enqueue_and_process 改造后的返回值
@dataclass
class EnqueueResult:
    status: str           # "success" | "merged" | "pending_handled"
    response_text: str    # 实际响应文本
    merged_input: str     # 合并后的输入（如果有合并），独立处理时 == user_content
    was_merged: bool      # 本调用方是否是合并方
```

合并方（`was_merged=True`）写入 `merged_input` 作为单条 user；被合并方（`status="merged"`）不写 user。

#### (4) 异常/空回复兜底

`process_and_persist` 内部统一处理：
- agent 抛异常 → 不写 user（user 还没写），但 record 标记 error
- response_text 为空（合并/排队） → 不写 user，不写 assistant
- 已经写过 user 但后续步骤失败 → 在 finally 中检查最后一条是否 user，若是则补一条 assistant 占位 `"[本次回复生成失败]"`

### 8.3 session_queue 的契约改造

`enqueue_and_process` 当前返回 `str`（response_text）。改造为返回 `EnqueueResult` 结构体：

```python
@dataclass
class EnqueueResult:
    status: Literal["success", "merged"]
    response_text: str = ""
    merged_input: str = ""        # 合并方实际处理的输入
    was_merged: bool = False

async def enqueue_and_process(...) -> EnqueueResult:
    ...
```

**向后兼容**：旧代码（如果有）仍可解构 `result.response_text`。所有渠道调用方都改为读 `result.status` 和 `result.response_text`。

### 8.4 落地步骤

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `src/channels/session.py` | 新增 `add_messages_batch_transactional` + `process_and_persist` |
| 2 | `src/core/session_queue.py` | `enqueue_and_process` 返回 `EnqueueResult`（含 merged_input / was_merged） |
| 3 | `src/saas/api/channel_routes.py` | 5 个渠道调用点全部替换为 `channel_session_manager.process_and_persist(...)` |
| 4 | `src/saas/api/wecom_personal_rpa_routes.py` | 同上 |
| 5 | `src/core/agent.py` | `_reorder_messages_for_llm` 加连续 user 兜底（P0-3，独立改动） |
| 6 | 单元测试 | `tests/unit/channels/test_session_manager.py` 新增 `process_and_persist` 测试 |

### 8.5 不在本次范围内（后续）

- L1 合并（`_merge_consecutive_user_messages`）保持不变，只覆盖同一 sync_msg 批次
- cancel_check 在 LLM streaming 中检查（P1，体验优化）
- 历史脏数据回填脚本（可单独做，P0-3 兜底已能处理）

### 8.6 风险与回滚

| 风险 | 缓解 |
|------|------|
| process_and_persist 改造影响所有渠道 | ① 分渠道灰度（先 wecom_kf，再其他）；② 保留旧路径作为 fallback，开关切换 |
| 合并方写合并文本破坏审计 | 审计走 `chat_records` 表，不依赖 channel_messages 的 user 分条 |
| send_response 回调签名变更 | 各渠道改造时统一为 `(response_text, downloadable_files) -> bool` |

**回滚方案**：所有改动集中在 `ChannelSessionManager.process_and_persist` + `session_queue.enqueue_and_process`，回滚只需还原这两个方法 + 调用点还原。

## 9. 复盘补充：真正的病根是 chat_messages 残留劫持（2026-06-25）

> **背景**：§1~§8 的 P0 修复（事务化 + 连续 user 兜底 + 推迟 user 写入）落地后，生产环境仍报同样的症状——wecom_kf 会话"问着问着就忘记之前确认过的信息，反复问人数"。本次复盘在生产库直接取证，发现**真正的病根与 §3 完全不同**，P0 修复并未覆盖。

### 9.1 取证（生产库 `aid_work_agent`，session=`tenant_ea24cd1a1097_wecom_kf_wmS6oOTAAArG8tJMx5wL-5R7n-nIMG6w_travel-consultant`）

| 表 | 条数 | 最新时间 | 内容 |
|----|------|---------|------|
| `chat_messages` | 4 | **2026-06-17**（8天前） | `你好`→`我是规划师`→`想去贵州3天`→`你们几个人` |
| `channel_messages` | 14 | 2026-06-25（当天） | 真实对话：3个人/8岁/黄果树/7月上旬 |

`channel_messages` 序列结构**完全干净**：14 条 user/assistant 严格交替，无 tool、无孤儿、无连续 user。§3 的所有根因（事务失败、孤儿 tool、连续 user）**都不适用**。数据总量仅 3.5KB，**也不是"消息太长被截断"**。

### 9.2 真正的病根：dispatcher 盲信 chat_messages

`src/core/agent.py::_process_message_impl` 的上下文重建逻辑（修复前）：

```python
db_messages = MessageDB.list_by_session(session_id, ...)   # 查 chat_messages
self.memory.clear(session_id)
if db_messages:        # ← 只要 chat_messages 非空，就永不查 channel_messages
    从 chat_messages 重建
else:
    从 channel_messages 重建（_load_channel_history）
```

**`if db_messages:` 假设"一个 session 要么用 chat_messages、要么用 channel_messages，不会两者都有"。但迁移期破坏了这个假设**：

1. 2026-06-16/06-17 早期 wecom_kf 代码把对话写进了 `chat_messages`（迁移到 `channel_messages` 之前的窗口期）
2. 迁移后新对话只写 `channel_messages`，但旧数据留在 `chat_messages` 没清
3. 同一 session_id（同用户+同渠道+同智能体）下，两张表都有了数据
4. dispatcher 查到 chat_messages **非空（4行）** → 不 fallback → 每轮只读到 06-17 的陈旧 4 行 + 当前消息
5. agent 上下文最后一句是"你们几个人"，于是反复问人数；channel_messages 里今天的真实对话**完全看不到**
6. **"一旦出错后续都错"**：这 4 行从不增长，每轮读同一份残缺数据——完美吻合用户现象
7. **web 端不中招**：web session 只写 chat_messages，自洽，不存在双表冲突

### 9.3 爆炸半径（生产取证）

`chat_messages` 中混入渠道会话残留共 **4 个 wecom_kf session、104 行**，全部来自 06-16/06-17 迁移窗口：

| session（channel_user 片段） | chat 行 | channel 行 | 状态 |
|----|----|----|----|
| `wmS6oOTAAArG8tJMx5wL...` | 4（06-17） | 14（06-25） | ❌ 正被劫持（channel 更新但被陈旧 chat 屏蔽） |
| `wmS6oOTAAAvD4RJ1...` | 50（06-17） | 0 | 🟡 仅 chat 残留（休眠，回访即触发） |
| `wmS6oOTAAAjZXGTg...` | 11（06-17） | 0 | 🟡 仅 chat 残留 |
| `wmS6oOTAAAk6rlje...` | 39（06-16） | 0 | 🟡 仅 chat 残留 |

> 关键原则（用户确认）：**web 端只从 chat_messages 组装，所有渠道只从 channel_messages 组装，读写都严格分离**。因此任何渠道 session_id 出现在 chat_messages 里都是污染。

### 9.4 修复（已落地）

**核心原则**：上下文重建必须先按**会话来源**分流，再决定读哪张表——渠道会话只读 `channel_messages`，web 会话只读 `chat_messages`。

判定器选用 `channel_sessions` 表成员（最权威、不依赖 session_id 字符串约定、无 SaaS web `tenant_` 前缀碰撞风险、无需维护渠道类型 marker 列表；渠道会话在 agent 处理前已由 `get_or_create_session` 登记入库）：

| 文件 | 改动 |
|------|------|
| `src/channels/session.py` | 新增 `ChannelSessionManager.is_channel_session(session_id)`：`SELECT 1 FROM channel_sessions WHERE session_id=%s` |
| `src/core/agent.py`（`_process_message_impl` 上下文重建） | 改为 `if is_channel: 读 channel_messages / elif db_messages: 读 chat_messages`；渠道会话不再查 chat_messages（`db_messages = [] if is_channel else ...`） |

**验证**：编译通过；判定器对真实数据判定正确（渠道→True 读 channel_messages，web→False 读 chat_messages）。代码修复**部署后即自动愈合**正在错乱的会话（is_channel=True → 读到 channel_messages 14 行真实对话，4 行陈旧 chat_messages 永不再读）。

### 9.5 残留数据处理（待执行）

代码修复后，4 个污染 session 的 chat_messages 残留**不再造成功能危害**（渠道会话不再读 chat_messages）。但 3 个休眠 session 的历史只躺在 chat_messages（channel_messages 为空），代码修复后这些历史对 agent 不可达。处理方案（二选一，需用户确认）：

- **方案 1（推荐，零数据风险）**：仅部署代码修复。正在错乱的会话自动愈合；3 个休眠 session 的陈旧历史被忽略（06-16/06-17 已结束的对话，低影响）。
- **方案 2（彻底归位）**：把这 4 个 session 的 chat_messages 行**迁移**到 channel_messages（保留 created_at 原值，按时间正序并入），再删除 chat_messages 残留。历史完整保留且归位正确。

### 9.6 潜在风险（防御性记录，本次不改）

`src/memory/short_term.py::_load_from_db`（`get_context` 缓存为空时自动恢复）仍只读 chat_messages。正常渠道流程中 `_rebuild_memory_from_db` 会先填充缓存，此路径不触发。但若将来出现"rebuild 被跳过 / 缓存中途过期"的极端路径，渠道会话可能再次被 chat_messages 劫持。后续可让 memory 层也感知会话来源，作为防御性收尾。

### 9.7 教训

P0 修复（§3~§8）针对的是"写入侧"的一致性（事务化、连续 user），但**没有审计"读取侧"的分发逻辑**。读取侧的 `if db_messages:` 是一个被迁移期数据击穿的隐式假设。**数据迁移 + 同一主键跨表 = 永远要审计读取分发逻辑**，不能只靠 fallback。

## 10. 第二个病根：消息窗口裁剪方向错误（ORDER BY id ASC LIMIT 取最老 N 条）

> **背景**：§9 修复后，另一个 wecom_kf 会话（`...SMSdmHLipO4KsfioS98aBSw...`，无 chat_messages 残留、是干净会话）仍报"重复细化方案、遗忘上一轮"。生产 trace 取证发现**完全不同的根因**——长会话的窗口裁剪方向反了。

### 10.1 取证（生产库 trace `tr_e90d188ef17342e0` → `tr_d0730ed0adfb44d0`）

| trace | 时间 | 用户输入 | agent 行为 | prompt tokens |
|-------|------|---------|-----------|--------------|
| `tr_e90d188...` | 17:51:54 | "可以" | 细化方案 + 问"要不要生成 word？"（正常） | 145139 |
| `tr_d0730ed...` | 17:52:26 | "OK" | **又重复细化方案**（应为生成 word） | 145139 |

两个连续 trace 的 **prompt tokens 完全相同（145139）**，messages 各 100 条（窗口上限）。对比发现：**两个 trace 的 messages[0..98] 逐条相同**，只有 [99]（当前用户输入）不同。也就是说，trace1 的 assistant 回复（"细化+问生成 word？"）**完全没有出现在 trace2 的上下文里**。

channel_messages 实际存了（id 614~617）：`614(5天行程) → 615(可以) → 616(细化+问生成word) → 617(OK)`。615/616 已持久化，但 trace2 窗口 [98]=614、[99]=617，**615/616 被跳过**。

### 10.2 根因：`ORDER BY id ASC LIMIT N` 返回最老的 N 条

`src/channels/session.py::get_messages` 与 `src/db/models.py::MessageDB.list_by_session` 都是：

```sql
SELECT * FROM <messages> WHERE session_id=%s ORDER BY id ASC LIMIT N
```

`ORDER BY id ASC LIMIT N` 返回的是 **id 最小的 N 条（最老的 N 条）**，不是最近的 N 条。对话超过 N 条后，**最近的一轮 assistant 回复被整体切掉**，agent 看不到自己上一轮的提问，用户"OK/可以"失去指代 → 重复执行上一步。

生产快照验证（该 session id≤617 共 104 条）：

| 查询 | 返回范围 | 含 615？ | 含 616？ | 含 617？ |
|------|---------|---------|---------|---------|
| BUGGY `ORDER BY id ASC LIMIT 101` | 471..614（最老101） | ❌ | ❌ | ❌ |
| 正确 子查询最近101条再正序 | 474..617（最近101） | ✅ | ✅ | ✅ |

**影响范围**：web（`list_by_session`）与 channel（`get_messages`）**都有这个 bug**，是系统性的。web 端没暴露只是因为 web 对话很少超过 100 条。

### 10.3 修复

**① 裁剪方向**（两处，取最近 N 条再正序返回）：

```sql
SELECT * FROM (
    SELECT * FROM <messages> WHERE session_id=%s ORDER BY id DESC LIMIT N
) AS recent ORDER BY id ASC
```

- `src/channels/session.py::get_messages`（channel 路径，无 `before_message_id` 分支）
- `src/db/models.py::MessageDB.list_by_session`（web 路径，含/不含 roles 两个变体）

**② 截断边界对齐到 user**（关键，防 LLM API 400）：

裁剪方向改对后，窗口第一条可能落在 assistant（甚至 tool 结果）上。DeepSeek/OpenAI 兼容 API 要求序列首条（system 之后）必须是 user，否则报 400。在 `src/core/agent.py::_reorder_messages_for_llm` 末尾（web+channel 共用收口点）增加：丢弃开头的非 user 消息直到第一条 user。孤儿 tool 已在该方法上方被跳过不会出现在开头；连同其后的 tool 一起被丢弃，不产生新孤儿。

生产快照验证：修复后窗口含 615/616/617，leading-trim 裁掉开头 1 条非 user（id 474 assistant）对齐到 id 475(user)。trace2 现在能看到 616"问生成 word"，"OK"有指代。

### 10.4 关于窗口上限 N（已调至 200）

原默认 `ShortTermMemory.max_messages=100`。裁剪方向修对后 100 条已能正确工作，但为保留更长会话历史，已将上限调至 **200**（用户确认模型上下文支持 100 万 token，200 条绰绰有余）。三处默认值同步修改保持一致，避免默认值漂移：

- `configs/config.yaml` → `memory.short_term.max_messages: 200`（部署真实值）
- `src/config/settings.py::ShortTermMemoryConfig.max_messages` 默认 200（代码默认）
- `src/memory/short_term.py::__init__` 默认 200（类默认）

> 注意：重 tool 会话（如本次单条 tool 结果 12KB）200 条可能接近 30 万 token，仍在百万窗口内安全。

### 10.5 教训

"取最近 N 条"是滑动窗口的基本语义，但 `ORDER BY ASC LIMIT N` 是最常见的反模式（取了最老的 N 条）。**任何"窗口/最近N条"查询都要用子查询 `ORDER BY DESC LIMIT N` 再正序**。本 bug 从上线起就存在，只是短会话从不触发——长会话一上线就暴露。


