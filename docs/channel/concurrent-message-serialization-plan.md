# 渠道会话短时间多条消息串行处理改造计划

## 问题背景

### Web 聊天场景
用户发送消息后发送按钮禁用，直到 SSE 流式响应走完。不存在多条并发问题。

### 渠道聊天场景（企业微信、微信客服、钉钉、飞书等）
用户无法被禁止连续发送消息。存在两种典型场景：

**场景 A：意图变更**（新消息覆盖旧消息）
```
1. "我想去南京旅游"
2. "不，还是去北京吧"      ← 覆盖
3. "不不，先看看西安吧"      ← 再覆盖
```

**场景 B：意图补充**（新消息追加更多信息）
```
1. "我想去北京旅游"
2. "3 个人，两大一小"       ← 补充
3. "下个月出发"             ← 补充
```

**现有逻辑**：3 条消息分别触发 3 个独立的 `process_message` 调用，3 个 worker 并行处理，然后逐一返回回复。

**问题**：
1. **意图过时**：用户最终收到 3 条回复，前 2 条基于已经放弃的意图，体验尴尬
2. **消息历史混乱**：3 个并发写操作交叉写入 `channel_messages` 表（渠道消息走 channel_messages，不是 chat_messages）和短期记忆，可能导致消息顺序错乱
3. **DeepSeek 400 错误**：多 worker 竞态导致 `_build_messages` 加载到不完整的消息历史（assistant 带 tool_calls 但无对应 tool 消息），触发 API 报错

## 改造目标

同一会话（`session_id`）的消息必须**串行处理**。采用 **"先发后等 + 可撤销"** 策略：首条消息立即发给 LLM，短时间窗口内（2 秒）用户追加的消息**合并后重新请求**；若已开始推送 SSE 到渠道则**不取消**，新消息排队作为下一轮对话处理。

## 方案设计

### 核心思路

每个 `session_id` 维护一个消息锁 + 合并缓冲区 + 状态标记，通过 Redis 实现跨 worker 共享：

```
收到渠道消息
  → session_id = "xxx"
  → 检查该 session 是否正在处理中（Redis key: "session_lock:{session_id}"）
    → 否（空闲）：
        a. 加锁（SET NX，TTL 120s 防死锁）
        b. 使用用户输入作为本次处理内容
        c. 启动 2 秒合并窗口（"session_merge_window:{session_id}"）
        d. 开始发给 LLM
        e. 推送 SSE 到渠道时标记 "session_responding:{session_id}"
        f. 处理完成后释放锁
    → 是（正在处理）：
        a. 检查是否已开始推送 SSE（"session_responding:{session_id}"）
        b. 已开始推送 → 新消息加入 pending 队列（"session_pending:{session_id}"），排队等当前轮次走完
        c. 尚未推送 SSE → 设置取消标志（"session_cancel:{session_id}"）+ 更新合并缓冲区（追加新输入）
           等待旧请求退出后，用合并后的完整输入重新处理
```

**关键设计决策**：
- **首条消息不等待**：收到第一条消息立即发给 LLM，不延迟
- **2 秒合并窗口**：窗口期内到达的追加消息合并到原始输入，而非简单覆盖
- **取消阈值**：只有尚未推送 SSE 到渠道时才允许取消，避免渠道侧收到半条消息
- **浪费可接受**：被取消的 LLM 请求已消费的 token 视为改善体验的成本

### 状态机

```
[空闲] ──消息到达──→ [合并等待] ──2秒窗口到期/新消息到达──→ [处理中]
                       │                                       │
                  追加消息                                    新消息到达
                  （合并缓冲区）                                 │
                                          ┌────────────────────┴────────────────────┐
                                          │                                         │
                                    尚未推送 SSE                              已开始推送 SSE
                                          │                                         │
                                   取消 LLM 请求                             新消息加入 pending 队列
                                   合并 → 重新处理                             等当前轮次走完再处理
                                          │                                         │
                                   [重新处理] ──────────→ [处理完成] ←───────────────┘
                                                          │
                                                    有 pending 输入
                                                          │
                                                    [重新处理]
```

### 关键数据结构（Redis）

| Key | Value | TTL | 说明 |
|-----|-------|-----|------|
| `session_lock:{sid}` | `{worker_pid}:{timestamp}` | 120s | 会话锁，标识当前处理该 session 的 worker |
| `session_cancel:{sid}` | `1` | 10s | 取消标志，旧请求检测到此标志后退出 |
| `session_merge:{sid}` | JSON `{original_text, timestamp}` | 5s | 合并缓冲区，存储原始用户输入 |
| `session_pending:{sid}` | JSON `{input, attachments, timestamp}` | 30s | 排队中的最新用户输入（已开始推送 SSE 后的新消息） |
| `session_responding:{sid}` | `1` | 10s | SSE 推送状态标记，标识已开始推送响应到渠道 |

### 实现位置

#### 1. 渠道入口层（`src/main.py` SSE 路由 / 渠道回调）

新增 `SessionMessageQueue` 调度器，在渠道收到消息后、调用 `agent.process_message` 前介入：

```python
class SessionMessageQueue:
    """渠道会话消息队列调度器"""

    async def enqueue(self, session_id: str, user_input: str,
                      attachments=None) -> AsyncGenerator[dict, None]:
        """将消息加入队列并等待处理完成

        - 如果该 session 空闲：加锁 + 立即发给 LLM + 启动 2 秒合并窗口
        - 如果该 session 正在处理中：
            - 已开始推送 SSE → 新消息加入 pending 队列，排队等待
            - 尚未推送 SSE → 设置取消标志 + 更新合并缓冲区（追加输入）
        - 处理完成后检查是否有 pending 输入，有则循环
        """

    def mark_responding(self, session_id: str) -> None:
        """标记已开始推送 SSE 到渠道（取消阈值）"""

    def mark_idle(self, session_id: str) -> None:
        """标记推送完成，恢复空闲状态"""

    def is_cancel_allowed(self, session_id: str) -> bool:
        """是否允许取消：只有尚未推送 SSE 时才允许取消"""

    def merge_input(self, session_id: str, new_text: str) -> None:
        """追加新文本到合并缓冲区"""

    def get_merged_input(self, session_id: str, original_text: str) -> str:
        """获取合并后的完整用户输入"""
```

#### 2. Agent 层（`src/core/agent.py`）

`cancel_check` 机制已存在（`sse_manager.is_cancelled(session_id)`），需要扩展为从 Redis 读取取消标志：

```python
# process_message 的 cancel_check 参数改为支持多来源：
async for event in self.process_message(
    ...,
    cancel_check=lambda: (
        sse_manager.is_cancelled(session_id) or
        session_queue.is_cancelled(session_id)  # 新增 Redis 取消标志检查
    ),
)
```

#### 3. SSE 推送状态标记

在 SSE 路由推流时设置状态，作为取消阈值判断依据：

```python
# SSE 路由侧（src/main.py）
async def handle_sse(session_id, ...):
    session_queue.mark_responding(session_id)  # 标记已开始推送
    try:
        async for event in agent.process_message(...):
            yield event
    finally:
        session_queue.mark_idle(session_id)  # 推送完成，释放锁
```

取消检查时读这个标记：

```python
# SessionMessageQueue
def is_cancel_allowed(self, session_id: str) -> bool:
    """是否允许取消：只有尚未推送 SSE 时才允许取消"""
    return not self.responding_sessions.get(session_id, False)
```

#### 4. 取消后重新处理

旧请求检测到取消标志后退出 `process_message`，在 `finally` 块中检查 `session_merge:{sid}` 合并缓冲区，如果有更新输入，不释放锁，用合并后的完整输入重新调用 `process_message`。

```python
# 退出前检查
if session_queue.is_cancelled(session_id) and session_queue.is_cancel_allowed(session_id):
    merged_input = session_queue.get_merged_input(session_id, original_text)
    if merged_input != original_text:
        # 用合并后的输入重新处理
        async for event in self.process_message(session_id, user_input=merged_input, ...):
            ...
```

### 并发安全性

| 场景 | 处理 |
|------|------|
| 两个 worker 同时尝试处理同一 session | Redis `SET NX` 保证只有一个成功 |
| Worker 崩溃未释放锁 | TTL 120s 自动过期 |
| 取消标志写入后旧请求未检测到 | `cancel_check` 在每次 LLM 调用前和每个 tool 执行前都检查，最多延迟一轮操作 |
| pending 输入 / 合并缓冲区过期 | TTL 5-30s 自动清理 |
| 合并窗口期内同时有追加消息和 LLM 开始推送 | SSE 推送标记优先：一旦 `mark_responding` 设置，后续消息不再取消，改为排队 |

### 用户体验

| 场景 | 改造前 | 改造后 |
|------|--------|--------|
| 5 秒内发 3 条语音（意图变更） | 收到 3 条回复（前 2 条过时） | 收到 1 条回复（基于合并后的最新意图） |
| 首条消息立即回复 | 首条也参与合并等待，有延迟 | 首条立即发给 LLM，无延迟 |
| 处理 2 秒内补充信息 | 收到 2 条独立回复 | 旧请求取消，合并补充信息后重新处理，总耗时约 6 秒 |
| 处理快完成时补充信息 | 收到 2 条回复 | 已开始推送 SSE，不取消，新消息排队作为下一轮对话 |

## 开发计划

### Phase 1：Redis 消息锁 + 合并窗口 + 取消阈值（核心）

1. 新增 `src/core/session_queue.py` — `SessionMessageQueue` 类
   - Redis 会话锁（`session_lock`），不可用时降级为内存缓存
   - 合并缓冲区（`session_merge`）+ 2 秒窗口
   - 取消标志（`session_cancel`）
   - SSE 推送状态标记（`session_responding` / `is_cancel_allowed`）
   - Pending 队列（`session_pending`，已开始推送后的新消息排队）
2. 修改 `src/main.py` 渠道 SSE 路由 — 在调用 `process_message` 前通过队列调度
3. 修改 `src/channels/` 各渠道回调入口 — 同样通过队列调度
4. 扩展 `cancel_check` 支持 Redis 取消标志
5. SSE 路由推送流开始/结束时标记 responding/idle 状态
6. `process_message` 退出时检查合并缓冲区并重新处理
7. 语音消息场景：ASR 转写完成后的输入也走同一套队列，确保 ASR 延迟期间的追加消息能正确合并

### Phase 2：可选优化

- 监控与统计：记录取消频率、合并次数、token 浪费量等指标，用于评估优化效果

## 风险和注意事项

1. **Redis 依赖**：Redis 不可用时降级为内存缓存（进程内 dict + TTL），串行处理逻辑不变，单 worker 下依然有效。多 worker 环境下内存缓存无法跨进程共享，退化为各 worker 独立处理，不产生额外风险（记录 `logger.warning`）
2. **DB 保存竞态一并解决**：同一时刻只有一个 worker 在处理该 session，不存在交叉写入
3. **向后兼容**：Web 聊天场景不受影响（已有 SSE 取消机制），只影响渠道场景
4. **语音转文字场景特别注意**：语音消息到达时可能同时触发 ASR 转写和后续处理，需确保 ASR 完成后的文本输入也走同一套队列
5. **Token 浪费**：被取消的 LLM 请求已消费的 token 无法回收，视为改善体验的成本。**必须在 `process_message` 退出前记录已消费的 token 数**（含取消场景），记入 `chat_record` 表的对应字段，以便后续监控和分析
6. **渠道侧已推送不取消**：一旦 SSE 开始推流，后续追加消息不再取消，避免渠道侧收到半条消息的尴尬

## 关联文档

- 已修复：`_validate_tool_call_pairing` 兜底校验（commit `83981b9`），但治标不治本
- 渠道入口：`src/main.py` SSE 路由、`src/channels/wecom/`、`src/channels/wecom_kf/`
- Redis 客户端：`src/core/redis_client.py`
- 取消机制：`src/main.py` `sse_manager.is_cancelled(session_id)`
