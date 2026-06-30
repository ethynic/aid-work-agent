# wecom_kf 消息合并处理机制

> 本文记录 wecom_kf（企业微信客服）渠道在「用户连续发送多条文本/语音消息」场景下的合并处理逻辑，以及开发过程中踩过的坑，供其他渠道（wecom / dingtalk / feishu / wecom_personal_rpa）借鉴。

## 1. 问题背景

企业微信客服有两条硬约束：

1. **5 条回复限额**：用户每发一条消息，企业可回复 5 条。如果用户快速连发 3 条消息，系统逐条回复会耗尽限额（errcode=95001）。
2. **上下文割裂**：连发的多条消息在语义上往往是一条完整诉求（如「我想去贵州旅游」+「七月初出发」+「三个人」），拆成多轮对话会让 LLM 误判意图。

因此必须把「短时间内同一用户连发的多条消息」合并为一条，作为单轮对话处理。

## 2. 合并的两层机制

系统采用**双层合并**：

| 层级 | 位置 | 触发条件 | 合并范围 |
|------|------|---------|---------|
| Layer A | `channel_routes.py:_merge_consecutive_user_messages` | 同一 sync_msg 批次内 | 同用户连续 **text+text** 消息 |
| Layer B | `session_queue.py:enqueue_and_process` | 跨批次 / 跨时间（2 秒窗口 + cancel/reprocess） | 任意类型的 **agent 输入文本**（含 ASR 后的语音文本） |

### 2.1 Layer A：同批次文本合并

`_process_tenant_wecom_kf_messages` 从微信 `sync_msg` 拉取一批消息后，先调用 `_merge_consecutive_user_messages` 把同一用户的连续文本消息用 `\n` 拼接为一条。

```python
def _merge_consecutive_user_messages(msg_list: list) -> list:
    """合并同一批次中同一用户连续发送的文本消息。"""
    # 仅 text+text 合并；voice/image/file 不合并
```

**设计限制**：Layer A 只合并 text+text。语音不参与 Layer A 合并 —— 因为语音在 channel_routes 后续才跑 ASR，此时还没有文本可拼接。

### 2.2 Layer B：跨批次合并 + 取消重处理

`session_queue.SessionMessageQueue` 是核心调度器，负责跨批次的合并。它采用「**先发后等 + 可撤销**」策略：

```
状态：空闲 → 处理中（未推送） → 处理中（已推送 SSE） → 空闲
```

#### 核心数据结构（Redis）

| Key | 用途 | TTL |
|-----|------|-----|
| `session_lock:{sid}` | 会话锁，标识当前处理该 session 的 worker | 120s |
| `session_cancel:{sid}` | 取消标志 | 10s |
| `session_merge:{sid}` | 合并缓冲区（结构化：text + attachments_meta） | 120s |
| `session_pending:{sid}` | 排队中的最新用户输入（已开始推送 SSE 后的新消息） | 30s |
| `session_responding:{sid}` | SSE 推送状态标记 | 10s |

#### 合并缓冲区结构（关键）

```json
{
  "text": "我想去贵州旅游\n[ASR识别结果] 七月初，发",
  "attachments_meta": [
    {"type": "voice", "media_id": "xxx", "file_name": "voice_xxx.wav", "local_path": "..."},
    {"type": "voice", "media_id": "yyy", "file_name": "voice_yyy.wav", "local_path": "..."}
  ],
  "timestamp": 1719336000.0
}
```

`text` 给 LLM 用，`attachments_meta` 给持久化用（含被取消方的附件，避免语音被并入前一条消息后 `local_path` 丢失）。

#### 三种状态的处理路径

**① 空闲态（首次请求）**
1. `acquire_lock` 获取会话锁
2. `set_merge(text, attachments_meta)` 初始化合并缓冲区
3. `_wait_merge_window` 等待 2 秒合并窗口，期间可能有追加消息
4. `get_merged_input` 读取合并后的完整输入
5. `processor(cancel_check, user_input_override=final_input)` 调用 agent
6. `_handle_cancel_and_reprocess` 检查是否被取消（处理期间有新消息）→ 循环重处理
7. `release_lock`

**② 处理中 + 允许取消（尚未推送 SSE）**
1. `set_cancel` 设置取消标志
2. `append_merge(new_text, new_attachments_meta)` 追加到合并缓冲区
3. `_wait_for_processing_end` 等待旧请求结束
4. 旧请求检测到取消后，用合并后的输入**重新处理**
5. 本调用方返回 `status="merged"`，不发送回复

**③ 处理中 + 不允许取消（已开始推送 SSE）**
1. `set_pending(text)` 加入 pending 队列（**pending 只存 text，丢附件**）
2. 等待旧请求结束
3. 旧请求完成后检测 pending 并处理

## 3. 循环重处理（核心难点）

### 3.1 问题

最初的实现中，`_handle_cancel_and_reprocess` 只重处理一次。当用户连发 3 条以上消息时：

```
msg1 → idle 分支持锁处理
  ├─ msg2 在窗口内到达 → set_cancel + append_merge → merge="msg1\nmsg2"
  ├─ msg1 窗口结束，processor 被立即 cancel（空响应）
  ├─ _handle_cancel_and_reprocess 读取 merge="msg1\nmsg2"，clear_merge，启动 reprocessor
  ├─ msg3 在 reprocessor 期间到达 → set_cancel + append_merge → merge="msg3"
  ├─ reprocessor 的 cancel_check 检测到 cancel，返回空响应
  └─ _handle_cancel_and_reprocess 直接返回 ("", "msg1\nmsg2")  ← msg3 丢失！
```

**结果**：msg3 完全丢失，下一轮上下文只有 "msg1\nmsg2" + 空 assistant 响应。这是用户报告的「上下文缺漏」根因。

### 3.2 修复：循环重处理

把 `_handle_cancel_and_reprocess` 改为**循环**：

```python
while iteration < max_iterations:  # 最多 10 轮防死循环
    clear_merge()
    clear_cancel()
    response = processor(cancel_check, user_input_override=current_merged_input)
    # 重处理后若被取消，检查是否有新合并输入
    if not is_cancelled(session_id):
        break
    new_merged_input = get_merge_buffer()
    if new_merged_input == current_merged_input:
        break  # 取消标志存在但无新内容
    current_merged_input = new_merged_input  # 继续下一轮重处理
```

每次重处理前若有 `cancel + 新合并输入`，则用新输入重新跑 processor；重处理后再次检查，直到无 cancel 或无新合并输入为止。

### 3.3 关键细节

- **clear_merge 必须在重处理前调用**：防止重处理期间新消息进入合并缓冲区后又被旧输入重复处理
- **clear_cancel 必须在重处理前调用**：重新处理是新一轮完整处理，不应继承上一轮的取消状态，否则新 processor 会在 `cancel_check` 时立即返回空响应
- **`user_input_override` 透传**：processor 闭包绑定的是原始输入，必须通过 override 把合并后的完整输入传给 processor，否则合并内容被丢弃

## 4. 附件元数据合并（第二个核心难点）

### 4.1 问题

语音消息的附件元数据（含 `local_path`、`media_id`、`file_name`）用于「外部接待客户」页面回放。最初实现中，合并缓冲区只存纯文本，被取消方的附件元数据直接丢弃：

```
msg1 voice1 (附件 voice1) → idle 分支持锁处理
msg2 voice2 (附件 voice2) → cancel + append_merge("voice2 ASR 文本")
                              ← voice2 的附件元数据丢失！
msg1 重处理 → 持久化时 user_to_write="voice1 ASR\nvoice2 ASR"
              attachments=[voice1]  ← 只剩 1 个语音链接
```

「外部接待客户」页面无法回放 voice2。

### 4.2 修复：结构化合并缓冲区

把合并缓冲区从 `{"text": str}` 升级为 `{"text": str, "attachments_meta": List[Dict]}`：

```python
def append_merge(self, session_id, new_text, new_attachments_meta=None):
    existing = redis_client.get(key)
    if existing:
        merged = original + "\n" + new_text
        merged_meta = list(existing_meta) + (new_attachments_meta or [])
    else:
        merged = new_text
        merged_meta = list(new_attachments_meta) if new_attachments_meta else []
    self.set_merge(session_id, merged, merged_meta)
```

`EnqueueResult` 增加 `merged_attachments_meta` 字段，透传给 `process_and_persist`：

```python
# process_and_persist 中
if result.was_merged and result.merged_attachments_meta is not None:
    attachments_to_write = result.merged_attachments_meta  # 含被取消方的附件
else:
    attachments_to_write = user_attachments_meta
```

### 4.3 已知限制：pending 通道丢附件

`session_pending:{sid}` 只存 text，无法累积附件元数据。当**已开始推送 SSE 后**到达的含附件消息走 pending 路径时，附件元数据会丢失（仅影响持久化，不影响 LLM）。

此场景概率极低（需在 SSE 推送开始的毫秒级窗口内到达），且不影响对话上下文。代码中保留 `[pending 丢附件]` 告警 tlog 用于监控。

## 5. 语音文件存储与上下文隔离

### 5.1 语音文件路径

语音文件统一存储到 `storage/tenants/{tenant_id}/conversation/{timestamp}_{media_id}.wav`，路径记录在 `channel_messages.attachments` JSON 列（user 消息行）。

### 5.2 上下文影响

**语音文件不影响上下文**。`agent._load_channel_history` 只读 `role/content/metadata`，不读 `attachments`；ASR 成功后 `agent_attachments_input = []`，语音 base64 不进 LLM。语音文件路径仅用于「外部接待客户」页面回放。

## 6. `[ASR识别结果]` 前缀机制

### 6.1 用意

`channel_routes.py` 在 ASR 成功后，对**短句（< 10 字）**语音自动加 `[ASR识别结果]` 前缀：

```python
# ASR 短句识别误差较高，告诉 LLM 来源是 [ASR识别结果] 以便 LLM 进入宽容模式
if asr_success and 0 < len(user_input) < 10:
    user_input = f"[ASR识别结果] {user_input}"
```

用意是让 LLM 知道这段文本来自短语音 ASR，识别误差较高，应进入宽容模式（容忍错别字、口音）。

### 6.2 长句不加前缀

长句（≥ 10 字）语音 ASR 准确率较高，无需宽容模式，不加前缀也无妨。

### 6.3 合并后的前缀保留

合并缓冲区累积的是各段的 `agent_user_input`（含前缀），所以合并后的 `user_to_write` 自然保留各段的前缀标记。例如：

```
合并输入："[ASR识别结果] 我想去贵州旅游\n[ASR识别结果] 七月初，发\n三个人"
                ↑ 短语音带前缀            ↑ 短语音带前缀      ↑ 文本无前缀
```

## 7. 踩过的坑

### 坑 1：合并缓冲区只存纯文本 → 附件丢失

**现象**：合并后的 user 消息只有 1 个语音附件，另一个语音文件已下载但 `local_path` 没入库。

**根因**：合并缓冲区结构是 `{"text": str}`，没有附件元数据字段。

**修复**：见 §4.2，升级为结构化合并缓冲区。

### 坑 2：重处理不循环 → 第 3 条消息丢失

**现象**：用户连发 3 条消息，第 3 条在上下文中消失。

**根因**：`_handle_cancel_and_reprocess` 只重处理一次，重处理期间到达的新消息被 `release_lock` 清掉。

**修复**：见 §3.2，改为循环重处理。

### 坑 3：reprocessor 被取消时返回空响应

**现象**：用户连发 3 条消息，只收到 1 条空回复。

**根因**：reprocessor 的 `cancel_check` 在迭代入口检测到 cancel 后 `return`，`response_parts=[]` 返回 `""`。

**修复**：循环重处理机制下，reprocessor 被取消后会进入下一轮重处理，最终能拿到非空响应。

### 坑 4：clear_cancel 时机错误 → reprocessor 立即返回空

**现象**：重处理时 processor 在第一次 `cancel_check` 就返回空响应。

**根因**：clear_cancel 在 reprocessor 之后调用，reprocessor 继承了上一轮的取消状态。

**修复**：clear_cancel 必须在 reprocessor **之前**调用。

### 坑 5：`final_input` vs `reprocessed_merged_input` 混用

**现象**：合并方持久化的是 `final_input`（合并窗口结束时读到的值），丢失了窗口结束后到达的消息。

**根因**：`_handle_cancel_and_reprocess` 透传回来的 `reprocessed_merged_input` 是重处理时读到的最新合并输入，比 `final_input` 更完整。

**修复**：持久化时必须用 `reprocessed_merged_input`，不能用 `final_input`。

### 坑 6：processor 闭包绑定原始输入

**现象**：重处理时 agent 收到的还是原始 `user_input`，合并内容被丢弃。

**根因**：processor 闭包绑定了 `agent_input_text`（原始输入），重处理时没有覆盖。

**修复**：processor 签名增加 `user_input_override` 参数，session_queue 通过 override 传入合并后的完整输入：

```python
async def _processor(cancel_check, user_input_override=None):
    effective_input = user_input_override if user_input_override is not None else agent_input_text
    # ...
```

### 坑 7：Layer A 不合并语音

**现象**：同批次内 voice+text 连发会产生多条独立消息。

**评估**：实际上 Layer B 的 cancel+reprocess 机制已经覆盖了同批次串行场景（`for msg in merged_msgs` 是 await 串行，msg1 持锁期间 msg2 走 cancel+append_merge 路径，msg1 重处理用合并输入）。所以 Layer A 不合并语音**不会**导致上下文割裂，也不会撞 WeCom 5 条限额（只有 1 次回复）。

**结论**：不修。强行修需要把 `_merge_consecutive_user_messages` 改成 async 并内嵌 ASR 调用，与 `[ASR识别结果]` 前缀逻辑耦合，改动大、收益小。

## 8. 调试日志

开发期间使用 `tlog` 把调试日志单独写到 `log/temp/语音合并.log`，与主日志完全隔离。bug 修复后已清理，关键位置以注释形式保留 tlog 调用，未来调试时取消注释即可：

| 文件 | 位置 | 用途 |
|------|------|------|
| `session_queue.py:append_merge` | 合并缓冲区累积 | 调试附件元数据累积 |
| `session_queue.py:enqueue_and_process` | 空闲态处理 | 调试合并窗口与最终输入 |
| `session_queue.py:_handle_cancel_and_reprocess` | 循环重处理每轮 | 调试 msg1→msg2→msg3 三连发 |
| `session.py:process_and_persist._processor` | override 输入 | 调试 override 是否正确透传 |
| `session.py:process_and_persist` | 持久化用户消息 | 调试 user_to_write / attachments |
| `agent.py:_load_channel_history` | 加载历史 | 调试下一轮 LLM 实际看到的历史 |

**保留的运行时告警 tlog**：`session_queue.py` 中 `[pending 丢附件]` —— 标注 pending 通道无法累积附件元数据的设计限制，触发即说明该场景发生。

## 9. 其他渠道借鉴要点

### 9.1 哪些渠道需要合并

| 渠道 | 是否需要 | 原因 |
|------|---------|------|
| wecom_kf | ✅ 必须 | WeCom 5 条回复限额 + 上下文完整性 |
| wecom | ✅ 推荐 | 同上 |
| dingtalk | ⚠️ 视场景 | 钉钉机器人无硬性回复限额，但合并可避免多轮误判 |
| feishu | ⚠️ 视场景 | 同 dingtalk |
| wecom_personal_rpa | ✅ 推荐 | RPA 模拟人工操作，合并可减少 LLM 调用次数 |

### 9.2 复用 `session_queue` 的最小条件

`session_queue.SessionMessageQueue` 是渠道无关的通用调度器。任何渠道只要满足以下条件即可复用：

1. 调用 `channel_session_manager.process_and_persist`，传入 `user_content`（持久化文本）、`agent_user_input`（给 LLM 的文本，可能与 `user_content` 不同）、`user_attachments_meta`（附件元数据）
2. 通过 `make_send_response` 构造 `send_response` 回调
3. 渠道适配器实现 `send_message` / `send_text`

### 9.3 渠道特定注意点

- **wecom_kf**：语音需 ASR（阿里云），SILK 格式需先解码为 WAV；附件存 `storage/tenants/{tid}/conversation/`
- **wecom**：企业微信自建应用，语音自带 `recognition` 字段，无需 ASR
- **dingtalk/feishu**：无语音 ASR 需求，但文本合并逻辑可直接复用
- **wecom_personal_rpa**：RPA 场景下回复延迟较高，合并窗口可适当调大

### 9.4 合并窗口调优

`MERGE_WINDOW = 2` 秒是经验值。调整建议：

- 用户输入速度快（移动端）：2 秒合适
- 用户输入速度慢（PC 端打字）：可降到 1 秒，避免无意义等待
- 语音为主的场景：可保持 2 秒（语音输入通常连续）

调整位置：`src/core/session_queue.py:MERGE_WINDOW`

## 10. 相关代码

| 文件 | 关键函数 |
|------|---------|
| `src/core/session_queue.py` | `SessionMessageQueue.enqueue_and_process` / `_handle_cancel_and_reprocess` / `append_merge` |
| `src/channels/session.py` | `ChannelSessionManager.process_and_persist` / `make_send_response` |
| `src/saas/api/channel_routes.py` | `_merge_consecutive_user_messages` / `_process_tenant_wecom_kf_messages` / `_transcribe_voice_with_asr` |
| `src/core/agent.py` | `Agent._load_channel_history` / `process_message_sync` |
| `src/channels/wecom_kf/message.py` | `parse_kf_message` |

## 11. 相关文档

- [wecom_kf 设计文档](./wecom_kf_design.md)
- [wecom_kf 部署指南](./wecom_kf_deployment_guide.md)
- [并发消息串行化方案](../concurrent-message-serialization-plan.md)
- [上下文重建陷阱](../../research/context-reconstruction-pitfalls.md)
