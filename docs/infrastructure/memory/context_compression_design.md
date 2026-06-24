# 会话内上下文压缩设计方案（Mid-Term Memory）

> 版本: v2.0 | 创建: 2026-06-23 | 最后更新: 2026-06-24 | 状态: 设计完成，待开发
>
> **v2.0 变更**（按用户反馈重大修订）：
> - **触发方式从「同步压缩」改为「异步压缩」**（业界担忧的竞态用「同一 session 已有压缩进行中就丢弃新信号」解决）
> - **触发阈值从 60% 调到 70%**（用户指定，留更多缓冲避免撑爆）
> - **TAIL 保留区从 20 条调到 30 条**（可配置）
> - **消息数软阈值从 60 条调到 150 条**（双触发兜底，避免海量短消息场景漏触发）
> - 失败兜底：异步失败 N 次后，主流程**同步降级**保证对话继续
> - 明确「50 条上下文」目标**包含所有消息**（user/assistant/tool_calls/tool_result 都算）
>
> 反向关联：本方案是 [memory_design.md](./memory_design.md) Phase 2.2「中期记忆 — 会话内上下文压缩」的细化实现。
> 业界调研依据：[context_compression_research.md](../../research/context_compression-research.md)

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

详细调研见 [context_compression_research.md](../../research/context-compression-research.md)。

| 决策点 | 业界主流 | 本方案采纳 | 备注 |
|--------|---------|-----------|------|
| 压缩策略 | Summary Buffer（LangChain/Claude Code/LangGraph 共识） | ✅ 摘要 + 缓冲混合 | 单纯滑窗会丢用户偏好等远期语义 |
| 触发方式 | 同步（业界共识） | ⚠️ **异步**（按用户指定，见 §1.1） | 用户方案解决了竞态，比业界更合适 |
| 触发阈值 | 60-95% token | **70% token 或 150 条消息**（可配置） | 双触发，token 主、消息数兜底 |
| TAIL 保留 | Claude Code ~33K token | **30 条消息**（可配置） | 用户指定 |
| 工具消息 | 工具名+参数保留；工具结果差异化处理 | ✅ | 见 §3.3 工具结果分级策略 |
| 压缩存储 | 原消息不删除，可追溯 | ✅ | 见 §4 数据模型 |
| 摘要结构 | 结构化字段（用户事实/决策/待办）优于流水账 | ✅ | 见 §3.4 摘要 prompt |
| 用户感知 | B 端透明 | ✅ 完全透明 | 与用户需求一致 |
| 失败兜底 | 降级 | ✅ 异步失败 N 次后同步降级 | 见 §6.2 |

### 1.1 ⚠️ 关于「同步 vs 异步」的设计选择

**业界共识**：同步压缩——异步会引入「压缩未完成时用户已发新消息」的竞态。

**本方案选择**：**异步压缩**（用户指定）。理由：

1. **压缩可能耗时较长**（LLM 摘要调用 + 大量消息预处理），同步会显著拖慢用户请求响应。
2. **业界担忧的竞态可以用工程手段解决**：
   - 同一 session 有压缩进行中 → **丢弃**新的压缩信号（不排队，因为下一轮请求会再次检查）
   - 压缩完成后用**原子事务**写入：summary 表 + 原消息 `compacted=true` 标记要么全成功要么全失败
   - 压缩期间用户新发的消息天然落在 TAIL 保护区，**不会被压缩进程吃掉**
3. **降级路径同步兜底**：如果异步压缩连续失败 N 次（可配置，默认 3 次），主流程在下一次请求前检测到这个状态，**同步**执行硬截断降级（不调 LLM），保证对话不阻塞。

**异步流程图**（用户描述的完整流程）：

```
用户提问 → 进入 _process_message_impl
              │
              ├─ 重建 memory from DB（含 compacted 过滤）
              ├─ 检查是否需要触发压缩（§3.1）
              │    │
              │    ├─ 需要压缩 → 检查 session 是否有进行中的压缩任务
              │    │    │
              │    │    ├─ 有 → 跳过本次信号（不阻塞主流程）
              │    │    └─ 无 → 异步派发压缩任务（fire-and-forget），立即返回
              │    │
              │    └─ 不需要 → 继续
              │
              ├─ 检查是否存在「连续失败的异步压缩」→ 是则同步降级
              ├─ 追加新 user 消息
              └─ 进入 LLM 调用循环

异步压缩任务（后台 worker / asyncio task）：
   ├─ 加分布式锁（session_id 维度）
   ├─ 读取当前 session 完整消息
   ├─ 分段（HEADER + COMPRESS + TAIL）
   ├─ 工具结果预处理
   ├─ 调用摘要 LLM（独立超时）
   ├─ 失败重试 N 次（可配置）
   ├─ 原子事务：① 写 chat_context_summaries ② UPDATE 原消息 compacted=true
   └─ 释放锁，记录成功/失败状态
```

---

## 2. 总体架构

### 2.1 主流程与异步压缩的协作

```
┌────────────────────────────────────────────────────────────────────────┐
│  Agent._process_message_impl (用户请求主线程)                            │
│                                                                          │
│  ① 重建 memory from DB（含 compacted=true 过滤）                          │
│       │                                                                  │
│  ②【新增】压缩状态检查                                                    │
│       │                                                                  │
│       ├─ 检查 token/消息数是否达阈值                                       │
│       │    │                                                              │
│       │    ├─ 达阈值 + session 无进行中压缩任务                             │
│       │    │    → 异步派发压缩任务（fire-and-forget）                       │
│       │    │                                                              │
│       │    ├─ 达阈值 + session 已有压缩任务进行中                           │
│       │    │    → 跳过信号（不阻塞，下一轮再检查）                            │
│       │    │                                                              │
│       │    └─ 未达阈值 → 继续                                              │
│       │                                                                  │
│  ③【新增】失败兜底检查                                                     │
│       │    └─ session 存在「连续失败次数 ≥ N」→ 同步执行降级硬截断            │
│       │                                                                  │
│  ④ 追加新 user 消息                                                       │
│  ⑤ _build_messages 组装 LLM 输入（自动过滤 compacted，注入 active summary） │
│  ⑥ LLM 调用循环                                                           │
└────────────────────────────────────────────────────────────────────────┘

                                  ↓ (异步, 独立任务)

┌────────────────────────────────────────────────────────────────────────┐
│  CompressionTask (后台 asyncio task / worker thread)                     │
│                                                                          │
│  T1. 加分布式锁 (session_id, source_type) - TTL 5min                     │
│  T2. 锁竞争失败 → 标记任务已跳过, 退出                                     │
│  T3. 读 session 当前完整消息列表                                           │
│  T4. 分段: HEADER (前3) / COMPRESS (中间) / TAIL (末30)                   │
│  T5. COMPRESS 区工具结果预处理（按工具类型差异化）                          │
│  T6. 调用摘要 LLM (独立超时 30s, 支持重试 M 次)                             │
│       ├─ 成功 → 进入 T7                                                    │
│       └─ M 次都失败 → 记录失败次数 + 触发告警 + 退出（下次主流程会同步降级）   │
│  T7. 原子事务（缺一不可）：                                                 │
│       ├─ INSERT chat_context_summaries (新 active)                         │
│       ├─ UPDATE 旧 active summary → status='superseded'                    │
│       └─ UPDATE chat_messages SET compacted=true WHERE id IN (...)        │
│  T8. 释放分布式锁                                                          │
│  T9. 清空失败计数（成功）                                                   │
└────────────────────────────────────────────────────────────────────────┘
```

**关键设计**：主流程**从不等待**压缩任务完成。主流程只做两件事：
1. 决定是否派发压缩任务（fire-and-forget）
2. 决定是否同步降级（仅当连续失败 N 次时）

### 2.2 调用点

主流程插入位置：`Agent._process_message_impl` 中，**重建 memory 之后、追加新 user 消息之前**（`agent.py:1759` 与 `agent.py:1886` 之间）。

为什么是这个位置：
- ✅ 之前：memory 已从 DB 完整重建，token 准确
- ✅ 之前：还没追加新消息，本轮用户输入不会进入压缩范围（避免「用户问 A，系统去压缩包含 A 的历史」的诡异时序）
- ✅ 之后：`_build_messages` 拿到的就是压缩后的新上下文

### 2.3 并发与去重（核心机制）

**问题场景**：
- 用户第 1 次提问，触发压缩信号，异步任务开始执行
- 压缩任务执行较慢（10-30 秒）
- 用户第 2 次提问，又触发压缩信号 —— 此时不能再次派发，否则会出现两个压缩任务操作同一份历史

**解决方案：基于 Redis 的 session 级压缩锁**

```python
# 主流程的信号派发逻辑
async def _try_dispatch_compression(self, session_id: str, source_type: str) -> None:
    lock_key = f"context_compression:running:{source_type}:{session_id}"
    # SETNX（仅当不存在时设置），TTL 5 分钟（兜底，防止任务卡死永远不释放）
    acquired = await redis_client.set(lock_key, task_id, nx=True, ex=300)
    if not acquired:
        # 已有任务在跑 → 静默跳过（不报错，不告警）
        logger.debug(f"Compression already running for {session_id}, skip signal")
        return
    # 异步派发任务（fire-and-forget）
    asyncio.create_task(self._run_compression_task(session_id, source_type, task_id))
```

**为什么用 SETNX 而非普通锁**：异步任务如果崩溃（worker 进程被 kill），普通锁会泄漏；SETNX + TTL 兜底保证最终一定会释放。

### 2.4 失败计数与同步降级触发

```python
# 主流程的失败兜底检查
async def _check_fallback_needed(self, session_id: str, source_type: str) -> bool:
    fail_count = await redis_client.get(
        f"context_compression:fail_count:{source_type}:{session_id}"
    )
    threshold = settings.memory.mid_term.max_consecutive_failures  # 默认 3
    return int(fail_count or 0) >= threshold

# 异步任务失败时递增计数
async def _run_compression_task(...):
    try:
        ...  # 摘要 LLM + 原子事务
        await redis_client.delete(f"context_compression:fail_count:{source_type}:{session_id}")
    except Exception as e:
        await redis_client.incr(
            f"context_compression:fail_count:{source_type}:{session_id}",
            expire=3600  # 计数器 1 小时窗口
        )
        logger.warning(f"Compression task failed: {e}")
```

主流程检测到失败计数 ≥ N 时，**同步**执行硬截断降级（§6.2），保证对话继续。降级成功后清空失败计数。

---

## 3. 核心机制

### 3.1 触发条件（双阈值，token + 消息数，任一满足即触发）

```python
def _should_compress(messages: list[dict], model_limit: int) -> tuple[bool, str]:
    """
    返回 (是否压缩, 原因)
    所有阈值均可通过 configs/config.yaml 配置。
    """
    # 阈值 1（主阈值）：token 数达到模型上限的 70%（可配置）
    token_count = count_tokens(messages)
    token_threshold = int(model_limit * settings.memory.mid_term.token_threshold_ratio)  # 默认 0.7
    if token_count >= token_threshold:
        return True, f"token_threshold({token_count}/{token_threshold}, {token_count*100//model_limit}%)"

    # 阈值 2（兜底）：消息条数达到 150（可配置）
    msg_threshold = settings.memory.mid_term.message_count_threshold  # 默认 150
    if len(messages) >= msg_threshold:
        return True, f"message_threshold({len(messages)}/{msg_threshold})"

    return False, ""
```

**为什么用双阈值**：
- 纯 token 阈值在「对话消息数极多但每条都很短」时（如客服频繁短回复、聊天闲谈），可能 token 还没到但 LLM API 调用的 per-message overhead 已经拖慢响应。
- 纯消息数阈值在「单条超大工具结果」时（如导出 10000 行 Excel、读大文件），可能没到 150 条就 token 爆。
- 两个条件**任一满足**即触发，互相兜底。默认值（70% / 150 条）均可配置。

**`model_limit` 来源**：复用 `configs/config.yaml` 中已有的 `llm.model_code` → 模型元数据（通常 128K 或 256K）。

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
- **TAIL 默认 30 条**（用户指定），给异步压缩任务足够的「安全窗口」—— 即使压缩执行 30 秒，用户在此期间发的几条消息也落在 TAIL 内，不会被压缩进程吃掉。
- TAIL 的边界要对齐到「完整的 user+assistant+tool_calls+tool_results 块」，**不能把工具链拦腰截断**（OpenAI/Anthropic 规范要求 tool_call 后必须紧跟 tool_result）。如果第 30 条恰好是 `assistant(tool_calls)` 而第 31 条是 `tool(result)`，TAIL 向后扩展直到工具链闭合。

**默认配置**（所有数字可配置）：
```yaml
memory:
  mid_term:
    header_keep: 3              # 头部保留消息数（必须是连续的完整 user+assistant 对）
    tail_keep: 30               # 尾部保留消息数（按工具链边界对齐后可能略多于 30）
    token_threshold_ratio: 0.7  # token 主阈值比例（占模型上限）
    message_count_threshold: 150  # 消息数兜底阈值
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
    message_count_threshold: 150     # 消息数兜底阈值（含工具消息）

    # 分段保留
    header_keep: 3                   # 头部保留消息数
    tail_keep: 30                    # 尾部保留消息数（按工具链边界对齐）

    # 异步任务
    task_lock_ttl_sec: 300           # 压缩任务锁 TTL（兜底防泄漏）
    max_consecutive_failures: 3      # 连续失败多少次后触发同步降级
    fail_count_window_sec: 3600      # 失败计数窗口

    # 摘要 LLM
    summary_max_tokens: 1500
    summary_llm_retry: 2             # 摘要 LLM 调用重试次数
    summary_llm:
      provider: deepseek             # 走便宜模型
      model: deepseek-chat
      timeout_sec: 30

    # 工具结果预处理
    large_tool_result_truncate_chars: 2000
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
    message_count_threshold: int = 150
    # 分段保留
    header_keep: int = 3
    tail_keep: int = 30
    # 异步任务
    task_lock_ttl_sec: int = 300
    max_consecutive_failures: int = 3
    fail_count_window_sec: int = 3600
    # 摘要 LLM
    summary_max_tokens: int = 1500
    summary_llm_retry: int = 2
    summary_llm: SummaryLLMConfig = SummaryLLMConfig()
    # 工具结果预处理
    large_tool_result_truncate_chars: int = 2000

class MemoryConfig(BaseModel):
    short_term: ShortTermMemoryConfig
    mid_term: MidTermMemoryConfig = MidTermMemoryConfig()  # 新增
    long_term: LongTermMemoryConfig
```

> **关于 `trigger_mode`**：v1.0 设计中有此字段，v2.0 移除。本方案**只支持异步模式**（用户指定），同步路径仅作为「连续失败兜底」内建，不需要单独配置。

---

## 5. 模块设计

### 5.1 新增 `src/memory/mid_term.py`

```python
class ContextCompressionService:
    """会话内上下文压缩服务（异步触发模式）"""

    def __init__(self, ...):
        self._settings = settings.memory.mid_term
        self._redis = get_redis_client()

    # ========== 主流程调用 ==========

    async def check_and_dispatch(
        self,
        session_id: str,
        source_type: str,
        subagent_id: Optional[str],
        tenant_id: Optional[str],
        user_id: Optional[str],
        current_messages: list[dict],
    ) -> None:
        """
        主流程入口（非阻塞）：
        1. 检查是否达阈值
        2. 检查是否有进行中的压缩任务 → 有则跳过
        3. 检查是否需要同步降级 → 是则同步执行降级
        4. 否则 fire-and-forget 派发异步任务
        """
        ...

    async def check_fallback_needed(self, session_id: str, source_type: str) -> bool:
        """主流程调用：检查是否需要同步降级"""
        ...

    def get_active_summary(self, session_id: str, source_type: str) -> Optional[str]:
        """_build_messages 调用：读取当前 active 摘要"""
        ...

    # ========== 内部方法 ==========

    def _should_compress(self, messages: list[dict], model_limit: int) -> tuple[bool, str]:
        """双阈值判断（token 70% 或 消息数 150）"""
        ...

    async def _try_acquire_task_lock(
        self, session_id: str, source_type: str
    ) -> Optional[str]:
        """SETNX 抢锁，返回 task_id 或 None（已被占）"""
        ...

    async def _run_compression_task(
        self,
        session_id: str,
        source_type: str,
        subagent_id: Optional[str],
        tenant_id: Optional[str],
        user_id: Optional[str],
        task_id: str,
    ) -> None:
        """异步任务主体：读消息 → 分段 → 摘要 → 原子事务 → 释放锁"""
        ...

    def _split_messages(self, messages: list[dict]) -> tuple[list, list, list]:
        """分 HEADER / COMPRESS / TAIL 三段（TAIL 对齐工具链边界）"""
        ...

    def _preprocess_tool_results(self, compress_section: list[dict]) -> list[dict]:
        """按工具类型应用差异化截断策略"""
        ...

    async def _call_summary_llm_with_retry(
        self, existing_summary: Optional[str], new_messages: list[dict]
    ) -> Optional[str]:
        """调用摘要 LLM，独立超时 + 重试 M 次"""
        ...

    async def _persist_atomically(
        self,
        session_id, source_type, summary_text, compressed_ids,
        original_tokens, compressed_tokens, llm_usage, ...
    ) -> str:
        """
        原子事务（缺一不可）：
          ① INSERT chat_context_summaries (status='active')
          ② UPDATE 旧 active summary → status='superseded'
          ③ UPDATE chat_messages SET compacted=true, compacted_by=summary_id WHERE id IN (...)
        三步任一失败 → 整个事务回滚
        """
        ...

    def _fallback_truncate(self, compress_section: list[dict]) -> str:
        """同步降级路径：摘要失败时，硬截断中间段，保留头尾要点（无 LLM 调用）"""
        ...

    async def _sync_fallback(
        self, session_id: str, source_type: str, messages: list[dict]
    ) -> None:
        """主流程触发的同步降级（异步失败 N 次后）"""
        ...
```

### 5.2 Agent 集成点

`src/core/agent.py` 的 `_process_message_impl`，在重建 memory（`agent.py:1759`）之后、追加新 user 消息（`agent.py:1886`）之前插入：

```python
# === [新增] 上下文压缩 ===
if settings.memory.mid_term.enabled:
    compression_service = get_compression_service()  # 单例
    current_messages = self.memory.get_context(session_id)

    # 1) 检查是否需要同步降级（异步任务连续失败时）
    if await compression_service.check_fallback_needed(session_id, source_type):
        # 同步执行降级（无 LLM 调用，纯硬截断）
        await compression_service._sync_fallback(
            session_id, source_type, current_messages
        )
        await self._reload_memory_from_db(session_id)
    else:
        # 2) 检查并派发异步压缩任务（fire-and-forget）
        await compression_service.check_and_dispatch(
            session_id=session_id,
            source_type=source_type,
            subagent_id=self.subagent_id,
            tenant_id=tenant_id,
            user_id=user_id,
            current_messages=current_messages,
        )
# === [新增结束] ===
```

**注意**：因为是异步，主流程**不等待**压缩完成，所以不需要 reload memory。当前请求仍用未压缩的上下文（但因为 token 已经超过阈值，本轮 LLM 调用会正常——只是接近上限），下一轮请求时压缩已完成，memory 重建会自动看到新状态。

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

## 6. 失败处理与降级（v2.0 重写：异步优先 + 同步兜底）

### 6.1 异步任务失败（单次）

异步压缩任务中，摘要 LLM 调用失败时的处理：

```
异步任务执行中
   │
   ├─ 摘要 LLM 调用 → 失败/超时 30s
   │    │
   │    ├─ 重试（默认 2 次，可配置）
   │    │
   │    └─ 重试耗尽仍失败 → 单次任务失败处理：
   │         ① Redis fail_count += 1（TTL 1 小时窗口）
   │         ② 释放分布式锁
   │         ③ logger.warning + 告警通道
   │         ④ 任务退出（不写入 summary，不更新 compacted）
   │
   └─ 任务失败后，主流程下一轮请求的行为：
        - 如果 fail_count < max_consecutive_failures（默认 3）
          → 继续尝试派发新的异步任务
        - 如果 fail_count >= max_consecutive_failures
          → 进入同步降级（见 §6.2）
```

**为什么单次失败不立即降级**：LLM 调用偶发失败很常见（网络抖动、模型限流），立即降级会丢失摘要质量。给 N 次重试机会。

### 6.2 连续失败 → 同步降级兜底

当异步任务连续失败 N 次（默认 3 次）后，主流程在下一次请求前检测到这个状态，**同步**执行硬截断降级，保证对话继续：

```
主流程 _process_message_impl
   │
   ├─ 重建 memory
   ├─【新增】check_fallback_needed(session_id, source_type)
   │    │
   │    └─ fail_count >= 3 → True
   │
   ├─ 进入同步降级路径（_sync_fallback）：
   │    1. 加分布式锁（同步等待，超时 5s）
   │    2. 读 session 当前完整消息
   │    3. 分段（HEADER + COMPRESS + TAIL）
   │    4. 跳过 LLM 调用，直接用 _fallback_truncate 提取「关键骨架」：
   │       - 所有 user 消息原文（截断到 500 字符）
   │       - 所有 assistant 最终回复原文（截断到 500 字符）
   │       - 所有工具调用名 + 参数（不含结果）
   │    5. 拼成结构化但简短的伪摘要
   │    6. 原子事务：写 summary（标记 fallback_used=true, llm_tokens_used=0）
   │                  + 更新原消息 compacted=true
   │    7. 清空 fail_count（让后续请求恢复尝试异步压缩）
   │    8. 释放锁
   │
   ├─ 重新加载 memory（含新的 active summary + compacted 过滤）
   └─ 继续 LLM 调用循环
```

**同步降级的关键保障**：
- **耗时极短**（无 LLM 调用，纯本地处理）< 200ms
- **不阻塞用户请求**（仅 200ms 额外开销，远小于一次 LLM 调用）
- **保证对话继续**（即使摘要质量稍差，也比撑爆 token 导致 LLM API 报错强）

### 6.3 并发请求冲突（同一 session）

虽然 `src/core/session_queue.py` 已经把同一 session 的消息**串行化**处理（最近的 P0 修复），但异步压缩任务与主流程的并发仍需保护：

- 压缩任务对 `(session_id, source_type)` 加 Redis SETNX 锁（TTL 5 分钟兜底防泄漏）
- 锁失败（已有任务在跑）→ 主流程的 `check_and_dispatch` 静默跳过信号派发
- 异步任务执行中即使 worker 崩溃，TTL 到期锁自动释放，不会永久卡死

### 6.4 数据库写入失败（原子事务保障）

`_persist_atomically` 失败时（写 `chat_context_summaries`、更新旧 summary 状态、更新 `compacted=true` 三步任一失败），整个事务回滚，**memory 保持压缩前状态**：

- 原消息 `compacted` 标记未被设置 → 下次请求仍会读到这些消息
- 没有新的 active summary → `_build_messages` 不会注入摘要
- fail_count += 1，达到阈值后走同步降级（§6.2）

**事务三步必须全部完成才算压缩成功**（用户强调「缺一不可」），任一步失败整个事务回滚。

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

| 指标 | 来源 | 含义 |
|------|------|------|
| `context_compression_signal_total{source_type}` | Counter | 主流程发出的压缩信号次数（含被去重跳过的） |
| `context_compression_signal_skipped_total{reason}` | Counter | 信号被跳过次数（已有任务在跑 / 未达阈值） |
| `context_compression_task_total{result}` | Counter | 异步任务完成数（result=success/fallback/failed） |
| `context_compression_sync_fallback_total` | Counter | 同步降级触发次数 |
| `context_compression_task_duration_seconds` | Histogram | 异步任务总耗时（含 LLM 调用） |
| `context_compression_sync_fallback_duration_seconds` | Histogram | 同步降级耗时（应 < 200ms） |
| `context_compression_ratio` | Histogram | 压缩比 distribution |
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

| 阶段 | 内容 | 工期 | 验收 |
|------|------|------|------|
| Phase 1 | 基础设施：表结构、配置、ContextCompressionService 骨架 | 2 天 | 表创建成功、配置加载、空实现不报错 |
| Phase 2 | 触发判断 + 分段 + 摘要 LLM 调用 + 原子事务持久化 | 3 天 | 单 session 触顶时压缩成功，事务原子性正确 |
| Phase 3 | 工具结果差异化策略 + Redis SETNX 锁 + 异步任务派发 | 3 天 | 异步任务正确执行；同一 session 并发信号被去重 |
| Phase 4 | 失败计数 + 同步降级兜底路径 | 2 天 | 连续失败 3 次后同步降级触发，对话不阻塞 |
| Phase 5 | Agent 集成（主智能体 + STANDALONE 子智能体） | 2 天 | web 渠道 + 至少 1 个第三方渠道联调通过 |
| Phase 6 | Trace 集成 + 指标埋点 + 管理后台页面 | 2 天 | 运维可看到压缩记录和指标 |
| Phase 7 | 回归测试 + 全渠道联调 + 性能调优 | 2 天 | 5 个渠道全部验证；异步任务 P95 < 30s，同步降级 P95 < 200ms |

**总计：约 13 工作日**

---

## 10. 风险与未决事项

### 10.1 已知风险

| 风险 | 缓解措施 |
|------|---------|
| 摘要丢失关键文件路径 | Prompt 明确要求保留路径原文 + 关键文件单独建字段 |
| 异步压缩未完成时 token 撑爆 | TAIL 30 条 + 阈值 70% 留 30% 缓冲；压缩耗时 P95 < 30s，撑爆概率低 |
| 异步任务连续失败 | fail_count 达 3 次触发同步降级，保证对话继续 |
| 多 worker 并发触发 | Redis SETNX 锁 + 5min TTL 兜底 + session_queue 已串行化 |
| 摘要质量评估难 | 通过「用户重复问老问题」反向指标监控（见 §7.2） |
| 历史数据未压缩 | 新方案只对未来的对话生效；存量超长 session 可手动触发管理后台的「立即压缩」 |

### 10.2 未决事项

1. **摘要 LLM 选型**：默认 DeepSeek V4 关闭推理，但需要验证中文摘要质量是否够好。Phase 2 期间做 A/B 测试。
2. **是否需要「主动召回」机制**：当用户问「我们之前聊的那个酒店叫什么来着」时，单纯摘要可能不够，需要检索 `compacted=true` 的原消息。**本方案不包含此机制**，留作 V2 增强。
3. **租户级配置覆盖**：是否允许不同租户配置不同阈值。Phase 1-7 使用全局配置，租户级覆盖留作 V2。

---

## 附录 A：决策对照表（业界 vs 本方案 v2.0）

| 维度 | LangGraph | Claude Code | OpenAI Assistants | MemGPT | **本方案 v2.0** |
|------|-----------|-------------|-------------------|--------|-----------|
| 策略 | Summary + Remove | Summary + Microcompact | Truncate | Tiered Memory | **Summary Buffer** |
| 触发 | 开发者控制 | 95% token | 模型上限 | Agent 主动 | **双阈值（token 70% / 消息 150）** |
| 同步性 | 同步 | 同步 | 同步 | 异步工具 | **异步为主，同步降级兜底** |
| 工具消息 | 全摘要 | 局部压缩 | 截断 | 全保留 | **差异化（按工具类型）** |
| 存储 | SummaryMessage | 内部 | 替换 | 分层 | **独立表 + compacted 标记** |
| 可追溯 | ✅ | ✅ | ❌ | ✅ | **✅（永不删除原消息）** |
| 用户感知 | 透明 | 显式提示 | 透明 | 显式 | **透明** |
| 失败兜底 | 开发者实现 | 内部 | 无 | 内部 | **fail_count + 同步降级** |

## 附录 B：参考链接

完整参考见调研报告 [context_compression_research.md](../../research/context-compression-research.md) 第 4 节。
