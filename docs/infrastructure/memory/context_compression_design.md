# 会话内上下文压缩设计方案（Mid-Term Memory）

> 版本: v3.0 | 创建: 2026-06-23 | 最后更新: 2026-06-29 | 状态: 设计完成；同步路径已实现，后台定时任务待开发
>
> **v3.0 变更**（相对 v2.0 的架构回归 + 补漏机制）：
> - **触发方式从「异步 fire-and-forget」回归「同步 await」**：v2.0 曾选择异步（fire-and-forget + Redis SETNX 锁 + fail_count 连续失败降级链路），实践证明工程链路过重、竞态防护复杂。v3.0 回归业界共识的**同步压缩**——主流程在重建 memory 后、追加 user 消息前同步完成压缩，本轮 LLM 调用即享受压缩后上下文。
> - **新增「后台定时任务扫描」作为补漏机制**（§2.5）：主流程同步压缩是主力，定时任务周期性扫描 `chat_sessions.context_token_count` / `channel_sessions.context_token_count` 超阈值的 session 补压缩，兜底漏网场景（token 缓存为 0 的老 session、Agent 异常未触发同步压缩的 session）。**定时任务待 Phase 8 实现，本文档先完整描述设计。**
> - **删除 v2.0 异步专有机制**：`check_and_dispatch` / `check_fallback_needed` / `_try_acquire_task_lock` / `_run_compression_task` / Redis fail_count 全部移除；并发保护改由 DB 层部分唯一索引 `idx_ccs_session_active` + `session_queue` 串行化承担。
> - **v3.1 session 级 token 缓存**：`chat_sessions` / `channel_sessions` 新增 `context_token_count` 字段，主流程每次 LLM 调用后写入 `prompt_tokens + completion_tokens`，阈值判断优先读缓存跳过全量 `count_tokens`。
> - **失败兜底简化**：摘要 LLM 失败不再走跨请求的 fail_count，而是**同一次调用内**重试 M 次 → 仍失败 → 立即走 `_fallback_truncate` 硬截断降级（无 LLM 调用 < 200ms）。
> - 保留 v2.0 的：触发阈值 70%、TAIL 30 条、「50 条上下文」含所有消息口径。
> - **v3.2.2 阈值简化**：消息数软阈值 150 → 200；删除 `_PRECISE_CHECK_MSG_THRESHOLD` 精确检查（缓存=0 时直接跳过 token 判断等下一轮，不再全量 count_tokens）。
>
> 反向关联：本方案是 [memory_design.md](./memory_design.md) Phase 2.2「中期记忆 — 会话内上下文压缩」的细化实现。
> 业界调研依据：[context_compression_research.md](../../research/context_compression_research.md)

---

## 0. 背景与问题

### 0.1 现象

随着会话深入，单 session 的 LLM 上下文（messages 数组）持续膨胀：

| TraceID | 渠道 | messages 数 | 现象 |
|---------|------|------------|------|
| `tr_01a7697db1f24a69` | Web | 103 | 接近模型 token 上限，延迟显著上升 |

对 Web 渠道用户可以「新建对话」规避；但**企业微信客服、企微个人账号 RPA、钉钉、飞书**这类渠道，一个用户在系统里**永远只有一个 session**（绑定的外部 customer_id 唯一），所有对话都堆积在同一个 session 下，几天/几周后上下文必然爆。

### 0.2 既有相关实现（部分覆盖，但不够）

| 已有机制 | 位置 | 局限 |
|---------|------|------|
| ShortTermMemory 滑动窗口 | `src/memory/short_term.py:31` (`max_messages=100`) | 仅按消息条数，不按 token；满了直接丢老消息，**信息有损丢失** |
| Skill 完成后压缩技能段 | `src/core/agent.py:1253` (`_compress_skill_context`) | 只压缩单个技能执行段，不处理跨多轮的会话级膨胀 |
| 长期记忆（跨会话） | `src/memory/memory_summarizer.py` | 每日定时跑，**不解决会话内**的上下文膨胀 |
| DialogManager 中期记忆占位 | `src/memory/manager.py:132` (`save_conversation_summary`) | 接口存在但**零调用点**，纯占位代码 |

### 0.3 目标

一套**渠道无关**的会话内上下文压缩机制，作用范围：
- ✅ Web 渠道（SSE）
- ✅ 企业微信客服、企微个人账号 RPA、钉钉、飞书
- ✅ 主智能体（master）和被委派的子智能体（`delegate_to_subagent`）
- ❌ 不作用于 STANDALONE 子智能体（它们每次都是无状态执行，无历史）

**核心指标**（messages 数组口径说明）：
- 「messages 数组」**包含所有角色**：user、assistant（含 tool_calls 的也算一条）、tool（每个 tool_result 算一条）
- 单 session 的 messages 数组稳定控制在 ≤ 50 条（**含工具消息**）
- 上下文 token 数稳定 ≤ 模型上限的 70%
- 压缩对用户完全透明（前端展示不受影响）

---

## 1. 业界共识摘要（决策依据）

详细调研见 [context_compression_research.md](../../research/context_compression_research.md)。

| 决策点 | 业界主流 | 本方案采纳 | 备注 |
|--------|---------|-----------|------|
| 压缩策略 | Summary Buffer（LangChain/Claude Code/LangGraph 共识） | ✅ 摘要 + 缓冲混合 | 单纯滑窗会丢用户偏好等远期语义 |
| 触发方式 | 同步（业界共识） | ✅ **同步（主流程）+ 后台定时任务（补漏）** | v3.0 回归业界共识，见 §1.1 |
| 触发阈值 | 60-95% token | **70% token 或 200 条消息**（可配置） | 双触发，token 主、消息数兜底 |
| TAIL 保留 | Claude Code ~33K token | **30 条消息**（可配置） | 用户指定 |
| 工具消息 | 工具名+参数保留；工具结果差异化处理 | ✅ | 见 §3.3 工具结果分级策略 |
| 压缩存储 | 原消息不删除，可追溯 | ✅ | 见 §4 数据模型 |
| 摘要结构 | 结构化字段（用户事实/决策/待办）优于流水账 | ✅ | 见 §3.4 摘要 prompt |
| 用户感知 | B 端透明 | ✅ 完全透明 | 与用户需求一致 |
| 失败兜底 | 降级 | ✅ 同步重试 + 硬截断降级 | 见 §6.1 |

### 1.1 关于「同步 vs 异步」的设计选择

**业界共识**：同步压缩——异步会引入「压缩未完成时用户已发新消息」的竞态（详见 [context_compression_research.md](../../research/context_compression_research.md) 「同步 vs 异步」节）。

**版本演进**：
- **v1.0**：同步压缩（早期草案）
- **v2.0**：改为异步（fire-and-forget + Redis SETNX 锁 + fail_count 连续失败降级），理由是同步会拖慢用户请求
- **v3.0**：**回归同步**（本版本）

**v3.0 为什么回归同步**：
1. **简单可靠，业界共识**。绝大多数生产系统（LangGraph、Claude Code、OpenAI Assistants）都用同步——压缩作为对话流的一个节点，完成后再继续 LLM 调用。
2. **v2.0 异步链路过重**。fire-and-forget + SETNX 锁 + fail_count + 跨请求同步降级触发，链路长、竞态防护复杂，工程维护成本高。
3. **同步的「拖慢用户请求」可控**。摘要 LLM 有独立超时（默认 30s）+ 重试 M 次；重试耗尽后立即走硬截断降级（无 LLM 调用 < 200ms）。最坏情况只阻塞达阈值的那一轮请求，且降级路径极快。
4. **本轮即享压缩后上下文**。同步完成后立即重建 memory，本轮 LLM 调用拿到的就是压缩后的上下文；异步方案则要等到下一轮才能看到效果。
5. **竞态天然消失**。同步在主流程内执行，配合 `session_queue` 串行化 + DB 层部分唯一索引，无需额外锁机制。

**v3.0 的补漏机制**：同步方案在 Agent 异常未触发压缩、或 `context_token_count` 缓存为 0 的老 session 上可能漏触发，因此**新增后台定时任务扫描**（§2.5）兜底这些场景。定时任务待 Phase 8 实现。

**同步流程图**（对应 `agent.py` `_run_compression_phase` 的真实行为）：

```
用户提问 → 进入 _process_message_impl
              │
              ├─ 重建 memory from DB（含 compacted 过滤）
              ├─【同步压缩阶段】检查是否需要触发压缩（§3.1）
              │    │
              │    ├─ 未达阈值 → 继续
              │    │
              │    └─ 达阈值 → 同步执行压缩（compress_now）：
              │         ① 读 session 当前完整消息
              │         ② 分段（HEADER + COMPRESS + TAIL）
              │         ③ 工具结果预处理
              │         ④ 调用摘要 LLM（独立超时 30s，重试 M 次）
              │            ├─ 成功 → 原子事务：写 summary + 标记原消息 compacted=true
              │            └─ M 次都失败 → 同步硬截断降级（_fallback_truncate，无 LLM）
              │         ⑤ 重新加载 memory（本轮即享压缩后上下文）
              │
              ├─ 追加新 user 消息
              └─ 进入 LLM 调用循环
```

---

## 2. 总体架构

### 2.1 主流程同步压缩

```text
┌────────────────────────────────────────────────────────────────────────┐
│  Agent._process_message_impl (用户请求主线程)                            │
│                                                                          │
│  ① 重建 memory from DB（含 compacted=true 过滤）                          │
│       │                                                                  │
│  ②【同步压缩阶段】_run_compression_phase                                  │
│       │                                                                  │
│       ├─ check_threshold：读 context_token_count 缓存 / 全量 token 判断   │
│       │    │                                                              │
│       │    ├─ 未达阈值 → 跳过，继续                                         │
│       │    │                                                              │
│       │    └─ 达阈值 → 同步执行 compress_now（await，阻塞本轮请求）：        │
│       │         │                                                        │
│       │         ├─ T1. 读 session 当前完整消息列表（_load_messages）        │
│       │         ├─ T2. 分段：HEADER (前3) / COMPRESS (中间) / TAIL (末30)  │
│       │         ├─ T3. COMPRESS 区工具结果预处理（_preprocess_tool_results）│
│       │         ├─ T4. 调用摘要 LLM (_call_summary_llm，独立超时 30s)       │
│       │         │    ├─ 成功 → 进入 T5                                      │
│       │         │    └─ 重试 M 次仍失败 → _fallback_truncate 硬截断降级      │
│       │         │       （无 LLM 调用，标记 fallback_used=true）            │
│       │         └─ T5. 原子事务（_persist_atomically，缺一不可）：           │
│       │              ├─ INSERT chat_context_summaries (新 active)          │
│       │              ├─ UPDATE 旧 active summary → status='superseded'     │
│       │              └─ UPDATE chat_messages/channel_messages              │
│       │                 SET compacted=true（按 source_type 分流）          │
│       │                                                                  │
│  ③ 重新加载 memory（本轮即享压缩后上下文）                                  │
│  ④ 追加新 user 消息                                                       │
│  ⑤ _build_messages 组装 LLM 输入（自动过滤 compacted，注入 active summary） │
│  ⑥ LLM 调用循环                                                           │
└────────────────────────────────────────────────────────────────────────┘
```

**关键设计**：主流程**同步等待**压缩完成。压缩成功后立即重新加载 memory，本轮 `_build_messages` 拿到的就是压缩后的新上下文（active summary 注入 + compacted 消息过滤）。这是 v3.0 相对 v2.0 异步方案的核心收益——**本轮即享压缩后上下文**，无需等到下一轮请求。

**异常隔离**：`_run_compression_phase` 全程 `try/except`，压缩链路任何异常（读消息、调 LLM、写事务）都只记日志、返回 None，**绝不影响主对话流程**——即使压缩完全失败，用户请求仍按未压缩上下文正常响应。

### 2.2 调用点

主流程插入位置：`Agent._process_message_impl` 中，**重建 memory 之后、追加新 user 消息之前**。压缩通过 `_run_compression_phase` 同步调用 `compress_session`（内部 `check_threshold` → `compress_now`）。

为什么是这个位置：
- ✅ 之前：memory 已从 DB 完整重建，token 准确
- ✅ 之前：还没追加新消息，本轮用户输入不会进入压缩范围（避免「用户问 A，系统去压缩包含 A 的历史」的诡异时序）
- ✅ 之后：压缩成功后立即重新加载 memory，`_build_messages` 拿到的就是压缩后的新上下文

### 2.3 并发保护（无独立锁机制）

v3.0 删除了 v2.0 的 Redis SETNX session 级压缩锁，并发保护由两道既有防线承担，**无需额外引入锁**：

**防线 1：`session_queue` 串行化**
- `src/core/session_queue.py` 已把同一 `(source_type, session_id)` 的消息**串行化**处理（P0 修复），同一 session 的并发请求天然排队，主流程内同步执行压缩不存在并发竞争。

**防线 2：DB 层部分唯一索引 `idx_ccs_session_active`**
- `chat_context_summaries` 表上 `CREATE UNIQUE INDEX ... ON (source_type, session_id) WHERE status = 'active'` 保证同一 session 同时只能有一个 active summary。
- 即使出现极端并发（多 worker + session_queue 失效），两次 `INSERT ... status='active'` 也只有一条能成功，另一条事务回滚，不会产生双 active 状态。

**结论**：同步执行 + session_queue 串行 + DB 唯一索引，三层保证下不存在「两个压缩任务操作同一份历史」的竞态，这是 v3.0 回归同步后能彻底删除锁机制的根本原因。

### 2.4 失败处理（同一次调用内重试 + 降级，无跨请求 fail_count）

v3.0 删除了 v2.0 的 Redis fail_count 连续失败降级机制。失败处理在**同一次 `compress_now` 调用内**完成闭环：

```
compress_now 调用摘要 LLM
   │
   ├─ 成功 → 原子事务持久化 summary + compacted 标记
   │
   └─ 失败/超时 → 重试（默认 2 次，可配置）
        │
        ├─ 重试中某次成功 → 进入原子事务
        │
        └─ 重试耗尽仍失败 → 同一次调用内立即走 _fallback_truncate 硬截断降级：
             ① 提取关键骨架（user/assistant 原文截断 + 工具调用名+参数）
             ② 拼成结构化伪摘要（标记 fallback_used=true, llm_tokens_used=0）
             ③ 原子事务持久化（与成功路径相同的 _persist_atomically）
             ④ 返回成功（降级路径），主流程照常重新加载 memory
```

**对比 v2.0**：v2.0 单次失败只记 fail_count，要累计 3 次才触发同步降级，期间多轮请求都在「无压缩 + fail_count 累加」状态。v3.0 在单次调用内重试 + 降级一气呵成，**不存在 fail_count 中间态**，逻辑更简单、状态更可控。详细失败分类见 §6。

### 2.5 后台定时任务扫描（补漏机制，待 Phase 8 实现）

> **状态：待实现。** 本节为完整设计描述，定时任务尚未编码，主流程同步压缩是当前唯一已实现的压缩路径。

**定位**：主流程同步压缩是主力；定时任务**只兜底漏网 session**，不承担主力职责。漏网场景包括：
1. `context_token_count` 缓存为 0 的老 session（字段上线前的存量 session，主流程的 `check_threshold` 会回退全量 token 计算但仍可能漏）；
2. Agent 异常中断、未走到同步压缩阶段就退出的 session；
3. 单次会话内消息数暴涨、单轮同步压缩后仍超阈值的 session（下一轮才会再次同步触发，定时任务可提前补刀）。

**扫描逻辑**：

```python
# 伪代码（待实现）
async def scan_and_compress():
    """定时任务周期执行：扫描 context_token_count 超阈值的 session 补压缩"""
    threshold_ratio = settings.memory.mid_term.token_threshold_ratio  # 0.7

    for table, source_type in [
        ("chat_sessions", "web"),
        ("channel_sessions", "chat"),   # 企微/钉钉/飞书等渠道
    ]:
        # 复用主流程同一阈值口径：context_token_count 超过 model_limit × 70%
        sessions = db.query(f"""
            SELECT session_id, context_token_count
            FROM {table}
            WHERE context_token_count > %s   -- = model_limit × threshold_ratio
              AND context_token_count > 0     -- 排除缓存为 0 的（这些定时任务也判不准，留给主流程）
        """, [model_limit * threshold_ratio])

        for row in sessions:
            # 复用 check_threshold（内部已含「压缩中/已压缩」状态判断，天然去重）
            await service.compress_session(
                session_id=row.session_id,
                source_type=source_type,
                force=False,
            )
```

**关键设计**：
- **复用 `compress_session` / `check_threshold`**：定时任务不自己实现压缩逻辑，直接调主流程同一入口；`check_threshold` 内部已判断「该 session 是否已有 active summary / 是否正在压缩」，天然完成去重。
- **只扫 `context_token_count > 0` 的 session**：缓存为 0 的 session 定时任务判不准 token，留给主流程同步压缩处理（主流程会回退全量计算）。
- **注册位置**：接入既有 `src/scheduler/manager.py`（APScheduler `BackgroundScheduler`），用 `IntervalTrigger` 周期触发（建议 5-10 分钟）。注意 scheduler 已有 Redis 分布式锁，多 worker 只跑一个实例。

**与主流程的关系**：两者调同一 `compress_session` 入口，靠 `check_threshold` 的状态判断去重，互不冲突——最坏情况是同一 session 被主流程和定时任务同时尝试，DB 唯一索引保证只生成一个 active summary，另一个事务回滚（§2.3 防线 2）。

---

## 3. 核心机制

### 3.1 触发条件（双阈值，token + 消息数，任一满足即触发）

```python
def _should_compress(messages: list[dict], model_limit: int) -> tuple[bool, str]:
    """
    返回 (是否压缩, 原因)
    所有阈值均可通过 configs/config.yaml 配置。
    """
    # 阈值 1（主阈值）：context_token_count（最后一次 LLM 的 input+output）达到模型上限的 70%
    token_threshold = int(model_limit * settings.memory.mid_term.token_threshold_ratio)  # 默认 0.7
    if cached_tokens > 0 and cached_tokens >= token_threshold:
        return True, f"token_threshold({cached_tokens}/{token_threshold}, {cached_tokens*100//model_limit}%)"

    # 阈值 2（兜底）：消息条数达到 200（可配置）
    msg_threshold = settings.memory.mid_term.message_count_threshold  # 默认 200
    if msg_count >= msg_threshold:
        return True, f"message_threshold({msg_count}/{msg_threshold})"

    return False, ""
```

> 注：token 阈值用 session 表的 `context_token_count` 缓存值（最后一次 LLM 调用的 prompt+completion token），不调 `count_tokens` 全量计算。缓存=0（新 session 首轮 / Agent 异常未写入）时跳过 token 判断，等下一轮写入缓存后再判。

**为什么用双阈值**：
- 纯 token 阈值在「对话消息数极多但每条都很短」时（如客服频繁短回复、聊天闲谈），可能 token 还没到但 LLM API 调用的 per-message overhead 已经拖慢响应。
- 纯消息数阈值在「单条超大工具结果」时（如导出 10000 行 Excel、读大文件），可能没到 200 条就 token 爆。
- 两个条件**任一满足**即触发，互相兜底。默认值（70% / 200 条）均可配置。

**`model_limit` 来源**：`_get_model_limit` 从 `settings.llm.<provider>.model` 读 model_code，查 `_MODEL_CONTEXT_LIMITS` 映射表（现役主力 deepseek-v4-pro / deepseek-v4-flash，均 512K）。

### 3.2 分段保留策略（保留首尾，压缩中间）

压缩任务执行时（异步），把当前 session 的对话消息（不含 system prompt）分三段：

```
┌──────────────────────────────────────────────────────────────────┐
│  HEADER 保护区（默认 3 条，可配置）← 最早几条 user/assistant，保留原文  │
├──────────────────────────────────────────────────────────────────┤
│  COMPRESS 区（中间所有消息）       ← 进入 LLM 增量摘要                 │
├──────────────────────────────────────────────────────────────────┤
│  TAIL 保护区（默认最近 30 条，可配置）← 最近对话，保留原文（含未完成工具链）│
└──────────────────────────────────────────────────────────────────┘
```

**HEADER 保护区的作用**：保留用户进入会话时的初始意图和上下文（如「我是做外贸的客户」「这次任务是查 5 月报价」），这些是**跨多轮都成立**的设定，绝不能被摘要丢失。

**TAIL 保护区的作用**：
- 最近轮次的细节（包括尚未完成的工具调用链）必须保留，否则当前任务无法继续。
- **TAIL 默认 30 条**（用户指定），保留足够的最近上下文窗口，让本轮 LLM 调用有完整近期信息可用。
- TAIL 的边界要对齐到「完整的 user+assistant+tool_calls+tool_results 块」，**不能把工具链拦腰截断**（OpenAI/Anthropic 规范要求 tool_call 后必须紧跟 tool_result）。如果第 30 条恰好是 `assistant(tool_calls)` 而第 31 条是 `tool(result)`，TAIL 向后扩展直到工具链闭合。

**默认配置**（所有数字可配置）：
```yaml
memory:
  mid_term:
    header_keep: 3              # 头部保留消息数（必须是连续的完整 user+assistant 对）
    tail_keep: 30               # 尾部保留消息数（按工具链边界对齐后可能略多于 30）
    token_threshold_ratio: 0.7  # token 主阈值比例（占模型上限）
    message_count_threshold: 200  # 消息数兜底阈值
```

### 3.3 工具结果差异化处理（关键细节）

在 COMPRESS 区内，按工具类型对 `role: tool` 消息做不同处理：

| 工具类型 | 示例 | 处理方式 |
|---------|------|---------|
| **大输出工具** | `file_read`、`search_documents`、`export_excel`、`pandas_analyze` | 结果按 2000 字符硬截断，超出部分转「[原文已存档，可重新调用工具获取]」 |
| **状态类工具** | `email_list`、`customer_search`、`list_orders` | 结果整体进入摘要，保留关键 ID 列表 |
| **指令类工具** | `create_plan`、`use_skill`、`clarify` | 工具名 + 参数保留原文，结果截断为「执行成功/失败」标记 |
| **写入类工具** | `send_email`、`word_process`、`text_file_writer` | 工具名 + 关键参数（收件人、文件路径）保留，结果丢弃 |

**实现方式**：在 `src/tools/` 各 BaseTool 子类上新增可选属性 `compression_strategy: Literal["truncate", "summarize", "keep", "drop"]`，默认 `summarize`。压缩服务读取此属性决定预处理方式。

**预处理**发生在 LLM 摘要之前，目的是把工具结果 token 量从动辄数万降到几千，**让摘要 LLM 调用本身的成本可控**。

### 3.4 结构化摘要 Prompt（避免叙事漂移）

不采用「一段流水账」式摘要，而是用结构化字段：

```text
你是对话摘要助手。请把以下对话历史压缩成结构化摘要，保留长期有效的信息。

输出格式（严格遵守）：

## 用户与背景
- 用户的身份、任务背景、长期约束

## 关键事实与决策
- 已确认的事实、已做的决策（含决策原因）

## 已完成的任务
- 已经做完的事情，含产出物路径（重要！）

## 进行中的事项
- 尚未完成的任务、待跟进的待办

## 关键文件与资源
- 涉及的文件路径、订单号、客户 ID 等（必须保留原文）

## 用户偏好
- 沟通风格、格式偏好等

要求：
1. 每个字段只写必要条目，不要扩写
2. 文件路径、ID、URL 必须保留原文，不要改写
3. 总长度不超过 {max_tokens} tokens

已有摘要（如有，请合并增量信息）：
{existing_summary}

需要总结的新对话：
{new_messages}
```

**结构化的好处**：
- 增量合并时不会出现「摘要的摘要越漂越远」（每次都是结构化字段合并）
- 审计时可按字段查询
- 重复字段会被 LLM 自然去重

### 3.5 注入位置：作为对话开头的 user 消息

压缩完成后，下次组装 messages 时，把摘要作为**第一条 user 消息**注入：

```python
messages = [
    {"role": "user", "content": f"[📋 之前对话摘要]\n{summary_text}"},
    {"role": "assistant", "content": "好的，已了解之前对话的要点。"},
    *recent_messages,  # TAIL 保护区
]
```

**为什么用 user + assistant 对**而不是单独 user：
- 单独 user 后直接跟 user 会触发 `_reorder_messages_for_llm` 的「连续 user 兜底」，可能被合并丢失
- user+assistant 对让 LLM 自然理解这是历史信息回顾

**为什么不用 system role**：现有 system prompt 已经很长（含工具定义、技能指南、用户画像），再加一段摘要会让 system 过载；且不同模型对 system message 长度有限制。

---

## 4. 数据模型

### 4.1 新增表 `chat_context_summaries`

```sql
CREATE TABLE IF NOT EXISTS chat_context_summaries (
    id SERIAL PRIMARY KEY,
    summary_id TEXT UNIQUE NOT NULL,          -- csum_xxxxxxxx 格式
    session_id TEXT NOT NULL,                 -- chat_session.session_id 或 channel_session.session_id
    source_type TEXT NOT NULL,                -- 'chat' / 'wecom_kf' / 'dingtalk' / 'feishu' / 'wecom_personal_rpa'
    tenant_id TEXT,                           -- 租户隔离
    user_id TEXT,
    subagent_id TEXT,                         -- 关联的子智能体（NULL 表示主智能体）

    -- 摘要内容
    summary_text TEXT NOT NULL,               -- 完整结构化摘要文本
    summary_version INTEGER NOT NULL DEFAULT 1, -- 该 session 第几次压缩（递增）

    -- 压缩元数据
    compressed_message_ids BIGINT[] NOT NULL, -- 被压缩的 chat_messages.id 列表（可追溯）
    compressed_message_count INTEGER NOT NULL,
    original_token_count INTEGER NOT NULL,
    compressed_token_count INTEGER NOT NULL,  -- 摘要 + TAIL 的 token 数
    compression_ratio REAL NOT NULL,          -- compressed / original

    -- 模型与调用信息
    llm_provider TEXT,                        -- 'qwen' / 'zhipu' / 'deepseek'
    llm_model TEXT,
    llm_tokens_used INTEGER,                  -- 摘要 LLM 调用消耗

    -- 状态
    status TEXT NOT NULL DEFAULT 'active',    -- 'active' / 'superseded' / 'rolled_back'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    superseded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ccs_session_active
    ON chat_context_summaries (session_id, source_type, status)
    WHERE status = 'active';
```

**关键设计**：
- `compressed_message_ids` 是 BIGINT 数组，**完整记录**哪些原消息被合并进来，支持审计与回滚。
- 同一个 session 同时只有一条 `status='active'` 的摘要（由 `_idx_ccs_session_active` 部分索引保证）。
- 每次新压缩产生新行，旧行置为 `superseded`，**永不删除**。

### 4.2 chat_messages 表新增字段

```sql
-- 在 deploy/init-postgres.sql 和 deploy/db_update.sql 同步添加
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;  -- summary_id
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted BOOLEAN DEFAULT FALSE;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS compacted_by TEXT;
```

**作用**：被合并进摘要的消息**物理保留**，只标记 `compacted=true`。`MessageDB.list_by_session` 默认过滤掉 `compacted=true`（前端展示和 Agent 重建 memory 都跳过），但保留物理行供审计/回滚。

### 4.3 配置新增

`configs/config.yaml`：

```yaml
memory:
  short_term:
    max_messages: 100          # 已存在
    ttl: 3600                  # 已存在
  mid_term:                    # 新增
    enabled: true

    # 触发条件（双阈值，任一满足即触发）
    token_threshold_ratio: 0.7       # token 主阈值：占模型上下文上限的比例
    message_count_threshold: 200     # 消息数兜底阈值（含工具消息）

    # 分段保留
    header_keep: 3                   # 头部保留消息数
    tail_keep: 30                    # 尾部保留消息数（按工具链边界对齐）

    # 摘要 LLM
    summary_max_tokens: 1500
    summary_llm_retry: 2             # 摘要 LLM 调用重试次数（重试耗尽走硬截断降级）
    summary_llm:
      provider: deepseek             # 走便宜模型
      model: deepseek-chat
      timeout_sec: 30

    # 工具结果预处理
    large_tool_result_truncate_chars: 2000

    # 后台定时任务扫描（Phase 8 待实现，§2.5）
    # background_scan:
    #   enabled: false               # 默认关闭，Phase 8 上线后开启
    #   interval_sec: 600            # 扫描周期，默认 10 分钟
    #   batch_size: 50               # 单次扫描最多处理 session 数
```

`src/config/settings.py`：

```python
class SummaryLLMConfig(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    timeout_sec: int = 30

class MidTermMemoryConfig(BaseModel):
    enabled: bool = True
    # 触发条件
    token_threshold_ratio: float = 0.7
    message_count_threshold: int = 200
    # 分段保留
    header_keep: int = 3
    tail_keep: int = 30
    # 摘要 LLM
    summary_max_tokens: int = 1500
    summary_llm_retry: int = 2
    summary_llm: SummaryLLMConfig = SummaryLLMConfig()
    # 工具结果预处理
    large_tool_result_truncate_chars: int = 2000
    # 后台定时任务扫描（Phase 8 待实现）
    background_scan_enabled: bool = False
    background_scan_interval_sec: int = 600

class MemoryConfig(BaseModel):
    short_term: ShortTermMemoryConfig
    mid_term: MidTermMemoryConfig = MidTermMemoryConfig()  # 新增
    long_term: LongTermMemoryConfig
```

> **关于 `trigger_mode`**：v1.0/v2.0 设计中曾有此字段（同步/异步切换）。v3.0 已移除——本方案**只支持同步模式**（主流程同步压缩为主），定时任务补漏作为独立的后台扫描机制，不需要 trigger_mode 配置。

---

## 5. 模块设计

### 5.1 新增 `src/memory/mid_term.py`

```python
class ContextCompressionService:
    """会话内上下文压缩服务（v3.0 同步触发模式）"""

    def __init__(self, ...):
        self._settings = settings.memory.mid_term
        self._redis = get_redis_client()  # 仅用于 metrics/缓存，不再做锁

    # ========== 对外唯一入口 ==========

    async def compress_session(
        self, session_id: str, source_type: str, *, force: bool = False
    ) -> Optional[str]:
        """
        压缩入口（主流程 + 定时任务共用）：
        1. _resolve_session_meta：自动从 DB 解析 tenant/user/subagent 元数据
        2. check_threshold：达阈值则进入 compress_now（force=True 时跳过阈值）
        3. 返回 summary_id 或 None
        """

    # ========== 阈值判断 ==========

    async def check_threshold(
        self, session_id: str, source_type: str
    ) -> Optional[Dict[str, Any]]:
        """
        优先读 context_token_count 缓存（chat_sessions/channel_sessions），
        为 0 时回退全量 count_tokens；返回压缩决策信息或 None。
        """

    def _eval_threshold(self, cached_tokens: int, msg_count: int, model_limit: int) -> tuple[bool, str]:
        """双阈值判断（token 70% 或 消息数 200）；缓存=0 时跳过 token 判断"""

    def _get_model_limit(self) -> int:
        """读当前模型上限"""

    # ========== 执行压缩 ==========

    async def compress_now(
        self, session_id, source_type, subagent_id, tenant_id, user_id, trigger_reason, ...
    ) -> Optional[str]:
        """
        压缩主体（同步执行）：
        _load_messages → _split_messages → _preprocess_tool_results
        → _call_summary_llm（失败走 _fallback_truncate）→ _persist_atomically
        """

    async def _load_messages(self, session_id, source_type) -> list[dict]:
        """按 source_type 从 chat_messages / channel_messages 读消息"""

    def _split_messages(self, messages: list[dict]) -> tuple[list, list, list]:
        """分 HEADER / COMPRESS / TAIL 三段（TAIL 对齐工具链边界）"""
        ...

    def _drop_orphan_tool_messages(self, compress_section: list[dict]) -> list[dict]:
        """清理孤儿 tool 消息（避免摘要 LLM 报错）"""

    def _preprocess_tool_results(self, compress_section: list[dict]) -> list[dict]:
        """按工具类型应用差异化截断策略"""

    async def _call_summary_llm(self, existing_summary, new_messages) -> Optional[str]:
        """调用摘要 LLM，独立超时 + 重试 M 次；重试耗尽返回 None（触发降级）"""

    def _fallback_truncate(self, compress_section: list[dict]) -> str:
        """硬截断降级：提取关键骨架（user/assistant 原文 + 工具调用名），无 LLM 调用"""

    async def _persist_atomically(
        self, session_id, source_type, summary_text, compressed_ids,
        original_tokens, compressed_tokens, fallback_used, ...
    ) -> str:
        """
        原子事务（缺一不可，任一失败整体回滚）：
          ① INSERT chat_context_summaries (status='active')
          ② UPDATE 旧 active summary → status='superseded'
          ③ UPDATE chat_messages/channel_messages SET compacted=true, compacted_by=summary_id
             （按 source_type 分流：web→chat_messages，渠道→channel_messages）
        """

    # ========== 元数据 / 读取 ==========

    async def _resolve_session_meta(self, session_id, source_type) -> dict:
        """从 DB 解析 tenant_id/user_id/subagent_id（主流程不用传元数据）"""

    def get_active_summary(self, session_id: str, source_type: str) -> Optional[str]:
        """_build_messages 调用：读取当前 active 摘要"""

    async def get_session_tenant_id(self, session_id, source_type) -> Optional[int]:
        """管理后台手动压缩前校验租户归属（防越权）"""
```

**对外唯一入口**是 `compress_session(session_id, source_type, *, force=False)`——业务无关、渠道无关，内部自动从 DB 解析元数据 + 自动读 model_limit。主流程同步压缩、定时任务补压缩、管理后台手动压缩，三者都走这个入口。

### 5.2 Agent 集成点

`src/core/agent.py` 的 `_process_message_impl`，在重建 memory 之后、追加新 user 消息之前，封装为 `_run_compression_phase` 同步调用：

```python
# === [新增] 上下文压缩（同步） ===
if settings.memory.mid_term.enabled:
    compression_service = get_compression_service()  # 单例
    # 同步压缩：内部 check_threshold → compress_now，阻塞等待完成
    # 元数据由 service 内部 _resolve_session_meta 自动从 DB 解析
    await compression_service.compress_session(
        session_id=session_id,
        source_type=source_type,
        force=False,
    )
    # 压缩成功后重新加载 memory，本轮 _build_messages 即享压缩后上下文
    await self._reload_memory_from_db(session_id)
# === [新增结束] ===
```

**注意**：同步执行，主流程等待 `compress_session` 完成后立即 reload memory，本轮 LLM 调用拿到的就是压缩后的上下文。`_run_compression_phase` 全程 try/except，压缩任何失败都不影响主对话（见 §6.3）。

`_build_messages`（`agent.py:1125`）需要在 messages 数组开头注入 active 摘要：

```python
def _build_messages(self, session_id: str) -> list[dict]:
    context = self.memory.get_context(session_id)  # 已自动过滤 compacted=true

    # 新增：如果存在 active 摘要，注入到最前面
    active_summary = self._compression_service.get_active_summary(
        session_id, self._detect_source_type()
    )
    if active_summary:
        context = [
            {"role": "user", "content": f"[📋 之前对话摘要]\n{active_summary}"},
            {"role": "assistant", "content": "好的，已了解之前对话的要点。"},
            *context,
        ]

    return self._reorder_messages_for_llm(context)
```

### 5.3 子智能体（SUBAGENT 模式）

被委派的子智能体（`execute_as_subagent`，`agent.py:2660+`）目前**不读 history**（每次 fresh 执行），所以**天然不存在上下文膨胀问题**，**不需要压缩**。

但是委派任务本身（task_description）会带主智能体的上下文摘要，如果主智能体已经压缩过，这个 task_description 会自然包含「这是 X 任务，背景见摘要」的指引。

**STANDALONE 子智能体**（如数据分析、邮件助手，通过 URL 路由直接进入）：走 `_process_message_impl`，**自动享受压缩**。无需额外改造。

### 5.4 与既有 Skill 压缩的兼容

既有 `_compress_skill_context`（`agent.py:1253`）处理的是**单个技能执行段**的压缩（执行完一个技能后，把技能内的工具消息压成一条摘要），与本方案是**互补关系**：

- 既有机制：**横向**压缩（单个技能内）
- 本方案：**纵向**压缩（跨多轮的会话级）

**兼容性**：技能压缩后产生的摘要消息，参与本方案的摘要时，会被视为普通 assistant 消息，自然纳入 COMPRESS 区处理，不会重复压缩或丢失。

---

## 6. 失败处理与降级（v3.0：同一次调用内闭环，无跨请求 fail_count）

### 6.1 摘要 LLM 失败 → 重试 + 同步降级

`compress_now` 调用摘要 LLM，失败处理在**同一次调用内**完成闭环：

```
compress_now
   │
   ├─ _call_summary_llm：摘要 LLM 调用 → 失败/超时 30s
   │    │
   │    ├─ 重试（默认 2 次，可配置）
   │    │
   │    ├─ 重试中某次成功 → 进入 _persist_atomically（写 summary + compacted）
   │    │
   │    └─ 重试耗尽仍失败 → 同一次调用内立即走 _fallback_truncate 硬截断降级：
   │         1. 分段（HEADER + COMPRESS + TAIL）
   │         2. 跳过 LLM 调用，提取「关键骨架」：
   │            - 所有 user 消息原文（截断到 500 字符）
   │            - 所有 assistant 最终回复原文（截断到 500 字符）
   │            - 所有工具调用名 + 参数（不含结果）
   │         3. 拼成结构化但简短的伪摘要
   │         4. 原子事务（_persist_atomically）：写 summary
   │            （标记 fallback_used=true, llm_tokens_used=0）+ 更新原消息 compacted=true
   │         5. 返回成功（降级路径），主流程照常 reload memory
   │
   └─ 无论 LLM 成功还是降级，都产生一个 active summary，对话继续
```

**对比 v2.0**：v2.0 单次失败只递增 fail_count、退出任务，要累计 3 次跨请求才触发同步降级，中间多轮请求处于「无压缩」状态。v3.0 在单次调用内重试 + 降级一气呵成，**不存在 fail_count 中间态**，逻辑更简单、状态更可控。

**降级的关键保障**：
- **耗时极短**（无 LLM 调用，纯本地处理）< 200ms
- **保证对话继续**（即使摘要质量稍差，也比撑爆 token 导致 LLM API 报错强）

### 6.2 数据库写入失败（原子事务保障）

`_persist_atomically` 失败时（写 `chat_context_summaries`、更新旧 summary 状态、更新 `compacted=true` 三步任一失败），整个事务回滚，**memory 保持压缩前状态**：

- 原消息 `compacted` 标记未被设置 → 下次请求仍会读到这些消息
- 没有新的 active summary → `_build_messages` 不会注入摘要
- 下次请求 / 定时任务会再次尝试压缩

**事务三步必须全部完成才算压缩成功**（用户强调「缺一不可」），任一步失败整个事务回滚。

### 6.3 主流程异常隔离（压缩失败不影响对话）

`_run_compression_phase` 在 `agent.py` 中被 **try/except 全程包裹**，压缩链路的任何异常（读消息、调 LLM、写事务、降级路径）都只记日志、返回 None，**绝不向主对话流程抛异常**：

```
_run_compression_phase (agent.py)
   │
   try:
       await compression_service.compress_session(...)  # check_threshold → compress_now
       if 压缩成功:
           await self._reload_memory_from_db(session_id)  # 本轮即享压缩后上下文
   except Exception as e:
       logger.warning(f"压缩阶段异常，跳过: {e}")  # 仅记日志
       # 不抛出，主流程继续用未压缩上下文响应
```

**关键设计**：即使压缩完全失败，用户请求仍按未压缩上下文正常响应，最坏情况只是接近 token 上限——不会因为压缩链路故障导致整个对话不可用。这是同步方案「可能阻塞」风险的最终安全网。

### 6.4 并发请求冲突（同一 session）

v3.0 删除了 v2.0 的 Redis SETNX 锁，并发保护来源（详见 §2.3）：

- `session_queue` 把同一 `(source_type, session_id)` 串行化，主流程同步压缩无并发竞争；
- DB 层部分唯一索引 `idx_ccs_session_active` 保证同 session 同时只有一个 active summary；
- 定时任务与主流程可能并发尝试同一 session，DB 唯一索引保证只生成一个 active summary，另一个事务回滚，不产生双 active。

---

## 7. 可观测性

### 7.1 Trace 集成

复用既有 `TraceCollector`（`src/core/trace_collector.py`），新增事件类型：

```python
# agent_events.py
@dataclass
class ContextCompressedEvent:
    event_type: str = "context_compressed"
    summary_id: str = ""
    compressed_message_count: int = 0
    original_token_count: int = 0
    compressed_token_count: int = 0
    compression_ratio: float = 0.0
    compression_duration_ms: int = 0
    fallback_used: bool = False
    trigger_reason: str = ""
```

在 trace 页面展示为一段独立的 span（紫色），让运维一眼看到「这次对话做了一次压缩」。

### 7.2 指标埋点

对应 `src/core/compression_metrics.py` 实际实现：

| 指标 | 类型 | 含义 |
|------|------|------|
| `context_compression_invocation_total{source_type, result}` | Counter | 压缩执行次数（result=success/fallback/skipped） |
| `context_compression_duration_seconds` | Histogram | 单次压缩总耗时（含 LLM 调用，降级路径应 < 200ms） |
| `context_compression_fallback_duration_seconds` | Histogram | 降级路径耗时（标记 fallback_used=true 的那次，应 < 200ms） |
| `context_compression_ratio` | Histogram | 压缩比 distribution（压缩后 token / 压缩前 token） |
| `session_active_messages` | Gauge | 各 session 当前活跃消息数（定时采样） |
| `session_active_summary_count` | Gauge | 各 session 当前已压缩段数（summary_version） |

### 7.3 管理后台页面

在「可观测性」页面新增「上下文压缩」Tab：
- 列表展示最近的压缩记录（session、时间、压缩比、降级标记）
- 点击行展开：摘要内容、被压缩的原消息列表
- 操作：「回滚此次压缩」（把 compacted 标记清除，summary 置 superseded）

---

## 8. 与既有功能的边界

| 既有功能 | 关系 | 说明 |
|---------|------|------|
| ShortTermMemory 滑动窗口 | **保留但作用弱化** | `max_messages` 从 100 调整到 200（作为最后兜底），实际压缩由本方案主导 |
| Skill 完成后压缩 (`_compress_skill_context`) | **保留** | 横向技能内压缩，与本方案纵向会话压缩互补 |
| 长期记忆 (`memory_summarizer.py`) | **完全独立** | 跨会话的用户画像，每日定时跑，作用域完全不同 |
| `MemoryManager.save_conversation_summary` | **废弃** | 进程内 dict 占位，无持久化，本方案上线后删除该接口（避免概念混淆） |
| `DialogManager` | **不涉及** | 未启用，与本方案无关 |
| Trace 系统 | **集成** | 见 §7.1 |
| session_queue 串行化 | **依赖** | 保证同一 session 不会并发触发压缩 |

---

## 9. 分阶段开发计划

> 详细开发任务见 [context_compression_dev_plan.md](./context_compression_dev_plan.md)

| 阶段 | 内容 | 工期 | 验收 | 状态 |
|------|------|------|------|------|
| Phase 1 | 基础设施：表结构、配置、ContextCompressionService 骨架 | 2 天 | 表创建成功、配置加载、空实现不报错 | ✅ 已完成 |
| Phase 2 | 触发判断（双阈值 + token 缓存）+ 分段 + 摘要 LLM + 原子事务 | 3 天 | 单 session 触顶时压缩成功，事务原子性正确 | ✅ 已完成 |
| Phase 3 | 工具结果预处理 + 孤儿 tool 消息清理 + 失败重试 + 同步降级 | 2 天 | LLM 失败后走硬截断降级，对话不阻塞 | ✅ 已完成 |
| Phase 4 | Agent 同步集成（主智能体 + STANDALONE 子智能体） | 2 天 | web 渠道 + 至少 1 个第三方渠道联调通过 | ✅ 已完成 |
| Phase 5 | Trace 集成 + 指标埋点 + 管理后台页面 | 2 天 | 运维可看到压缩记录和指标 | ✅ 已完成 |
| Phase 6 | 回归测试 + 全渠道联调 + 性能调优 | 2 天 | 5 个渠道全部验证；同步降级 P95 < 200ms | 🔧 部分（联调进行中） |
| Phase 7 | 工具结果差异化策略（按工具类型分级，替代统一截断） | 2 天 | 不同工具走不同截断策略 | 🔧 部分（当前统一截断） |
| **Phase 8** | **后台定时任务扫描（§2.5 补漏机制）** | 2 天 | 定时扫描 `context_token_count` 超阈值 session 补压缩 | ⏳ **待开发** |

**总计：约 15 工作日**（Phase 1-5 已完成，Phase 6-7 部分，Phase 8 待开发）

> v2.0 的「Redis SETNX 锁 / 异步任务派发 / 失败计数」相关 Phase 在 v3.0 已删除，回归同步后这部分工作量并入 Phase 2/3。

---

## 10. 风险与未决事项

### 10.1 已知风险

| 风险 | 缓解措施 |
|------|---------|
| 摘要丢失关键文件路径 | Prompt 明确要求保留路径原文 + 关键文件单独建字段 |
| **同步压缩阻塞用户请求** | 摘要 LLM 独立超时 30s + 重试 M 次；重试耗尽走硬截断降级（< 200ms）；最坏只阻塞达阈值那轮，§6.3 异常隔离兜底 |
| 漏网 session 未压缩（缓存为 0 / Agent 异常） | **Phase 8 后台定时任务扫描** `context_token_count` 超阈值 session 补压缩（§2.5） |
| 多 worker 并发触发 | session_queue 串行化 + DB 部分唯一索引 `idx_ccs_session_active`（§2.3） |
| 摘要质量评估难 | 通过「用户重复问老问题」反向指标监控（见 §7.2） |
| 历史数据未压缩 | 新方案只对未来的对话生效；存量超长 session 可手动触发管理后台的「立即压缩」 |

### 10.2 未决事项

1. **摘要 LLM 选型**：默认 DeepSeek V4 关闭推理，但需要验证中文摘要质量是否够好。Phase 2 期间做 A/B 测试。
2. **是否需要「主动召回」机制**：当用户问「我们之前聊的那个酒店叫什么来着」时，单纯摘要可能不够，需要检索 `compacted=true` 的原消息。**本方案不包含此机制**，留作 V2 增强。
3. **租户级配置覆盖**：是否允许不同租户配置不同阈值。Phase 1-7 使用全局配置，租户级覆盖留作 V2。

---

## 附录 A：决策对照表（业界 vs 本方案 v3.0）

| 维度 | LangGraph | Claude Code | OpenAI Assistants | MemGPT | **本方案 v3.0** |
|------|-----------|-------------|-------------------|--------|-----------|
| 策略 | Summary + Remove | Summary + Microcompact | Truncate | Tiered Memory | **Summary Buffer** |
| 触发 | 开发者控制 | 95% token | 模型上限 | Agent 主动 | **双阈值（token 70% / 消息 200）** |
| 同步性 | 同步 | 同步 | 同步 | 异步工具 | **同步为主，后台定时任务补漏** |
| 工具消息 | 全摘要 | 局部压缩 | 截断 | 全保留 | **差异化（按工具类型）** |
| 存储 | SummaryMessage | 内部 | 替换 | 分层 | **独立表 + compacted 标记** |
| 可追溯 | ✅ | ✅ | ❌ | ✅ | **✅（永不删除原消息）** |
| 用户感知 | 透明 | 显式提示 | 透明 | 显式 | **透明** |
| 失败兜底 | 开发者实现 | 内部 | 无 | 内部 | **同次调用内重试 + 硬截断降级** |

## 附录 B：参考链接

完整参考见调研报告 [context_compression_research.md](../../research/context_compression_research.md) 第 4 节。
