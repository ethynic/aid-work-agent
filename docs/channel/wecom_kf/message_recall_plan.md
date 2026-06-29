---
关联想法: wecom_kf 用户撤回消息处理（同批次剔除 + 跨批次标记 + 后台可见）
关联设计: docs/channel/wecom_kf/wecom_kf_design.md
关联设计(上游): docs/channel/wecom_kf/message-merge.md
状态: 📋 待开发
创建日期: 2026-06-29
---

# wecom_kf 用户撤回消息处理 — 开发计划

> 反向关联：合并机制背景见 [message-merge.md](./message-merge.md)。

## 1. 问题背景

### 1.1 现象

用户在微信侧发送消息 A → 撤回 A → 发送消息 B。系统日志显示 A 与 B 被合并处理，未对"撤回"做任何响应。

### 1.2 排查结论（已通过 tlog 确认）

微信**确实推送了撤回事件**，走 **sync_msg 路径**（不是顶层回调 event）。事件结构：

```json
{
  "msgid": "4XYm59AKqyxTqvcVJ7u4i3eeo2LK2iLae3rgiu83xNf9",  // 事件自身 ID
  "send_time": 1782740676,
  "origin": 4,                                                 // ← 被现有 origin!=3 过滤掉
  "msgtype": "event",
  "event": {
    "event_type": "user_recall_msg",
    "open_kfid": "wkS6oOTAAA3QeXgVrsC8MLVBaLoWPl3A",
    "external_userid": "wmS6oOTAAAhVHj3_umWgJFFlHZpWfZhA",
    "recall_msgid": "AZUS3nsFE5b48MhaA4NxaBg6gN"              // 被撤回的原消息 ID
  }
}
```

**现有代码两处缺口**（`src/saas/api/channel_routes.py`）：

| 位置 | 问题 |
|------|------|
| `_process_tenant_wecom_kf_messages` 遍历 `msg_list` 时 | `if msg.get("origin") != 3: continue` 在去重之前执行，撤回事件（origin=4）被直接跳过，从未进入任何处理分支 |
| `_merge_consecutive_user_messages` | 不感知撤回事件，被撤回的消息仍参与合并 |

**额外观察**：同一撤回事件被微信**多次推送**（后续操作触发新的 `kf_msg_or_event` 回调，每次 sync_msg 又把之前的事件条目拉一遍）。处理时必须按事件 `msgid` 去重，不能依赖 cursor 不重复。

### 1.3 影响范围

- **同批次撤回**：A 与撤回事件在同一 sync_msg 批次内到达。A 的文本被并入合并消息，LLM 误把已撤回内容当作有效输入。
- **跨批次撤回**：A 在上一批已处理并持久化到 `channel_messages`，撤回事件在下一批才到。A 已进入上下文重建，后续轮次 LLM 仍能看到 A。

## 2. 设计目标

| # | 目标 | 适用场景 |
|---|------|---------|
| G1 | 同批次内剔除被撤回的消息，不参与合并 | A 与撤回事件同批次 |
| G2 | 撤回事件去重，同一事件多次推送只处理一次 | 所有撤回事件 |
| G3 | 跨批次撤回：标记已持久化的消息，使其不进入 LLM 上下文重建 | A 已持久化，撤回事件后到 |
| G4 | 合并消息的部分撤回：从合并内容中剔除被撤回的段，保留其余段 | A 被并入合并消息后才收到撤回 |
| G5 | 后台"外部接待客户"与"会话追踪"视图仍可看到被撤回的消息（带"已撤回"标记） | 所有已撤回消息 |

## 3. 设计方案

### 3.1 数据模型变更

#### 3.1.1 `channel_messages` 表新增字段

```sql
-- 2026-6-29，channel_messages 增加撤回标记，支持 wecom_kf user_recall_msg 事件
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS is_recalled BOOLEAN DEFAULT FALSE;
ALTER TABLE channel_messages ADD COLUMN IF NOT EXISTS recalled_at TIMESTAMP;
```

- `is_recalled`：该消息是否被用户撤回
- `recalled_at`：撤回事件到达时间

> **合并消息的部分撤回**不新增列，通过 `metadata` 字段记录被撤回的子段，见 3.1.2。

#### 3.1.2 `channel_messages.metadata` 约定

持久化用户消息时，metadata 增加以下字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `wecom_msgid` | str | 单条消息：微信原始 msgid。用于跨批次撤回时按 `recall_msgid` 反查 |
| `merged_from_msgids` | list[str] | 合并消息：所有被合并的微信 msgid 列表（按顺序） |
| `merged_segments` | list[dict] | 合并消息：每段 `{msgid, text}`，用于部分撤回时重建内容 |
| `recalled_part_msgids` | list[str] | 合并消息中被撤回的子段 msgid |
| `original_content_before_recall` | str | 部分撤回时，保留撤回前的完整文本（供后台视图展示） |

单条消息只需 `wecom_msgid`；合并消息需 `merged_from_msgids` + `merged_segments`。

#### 3.1.3 撤回事件去重

复用 `MessageDeduplicator`，但用**撤回事件自身的 msgid**（即事件条目的 `msgid` 字段，非 `recall_msgid`）作为去重键，TTL 与现有消息去重一致（300s）。

> 不复用 `recall_msgid` 去重：理论上同一原消息可能被多次撤回/恢复（微信当前不支持恢复，但用事件 msgid 更语义正确）。

### 3.2 同批次剔除（G1 + G2）

在 `_process_tenant_wecom_kf_messages` 遍历 `msg_list` 时，**在 `origin != 3` 过滤之前**插入撤回事件处理分支：

```python
for msg in result.get("msg_list", []):
    msg_id = msg.get("msgid", "")
    msg_origin = msg.get("origin", "")
    msg_type = msg.get("msgtype", "")

    # 撤回事件：origin=4, msgtype=event, event.event_type=user_recall_msg
    if msg_type == "event" and msg.get("event", {}).get("event_type") == "user_recall_msg":
        # 事件去重（按事件自身 msgid）
        dedup = _get_tenant_dedup(tenant_id)
        if await dedup.is_duplicate(f"recall:{msg_id}"):
            continue
        recall_msgid = msg["event"]["recall_msgid"]
        external_userid = msg["event"]["external_userid"]
        # 记录本批次内的撤回，供后续剔除使用
        recalled_msgids_in_batch.add(recall_msgid)
        # 跨批次兜底：尝试标记已持久化的消息（见 3.3）
        await _mark_recalled_message(tenant_id, open_kfid, external_userid, recall_msgid)
        continue

    # 原有 origin 过滤
    if msg_origin != 3:
        continue
    ...
```

收集完 `recalled_msgids_in_batch` 后，在 `_merge_consecutive_user_messages` **之前**从 `valid_msgs` 中剔除被撤回的消息：

```python
if recalled_msgids_in_batch:
    valid_msgs = [m for m in valid_msgs if m.get("msgid") not in recalled_msgids_in_batch]
```

**注意**：剔除发生在去重之后、合并之前。被剔除的消息不进入 `valid_msgs`，因此不会触发回复（避免对已撤回消息回复耗 5 条限额）。

### 3.3 跨批次撤回标记（G3）

新增 `_mark_recalled_message(tenant_id, open_kfid, external_userid, recall_msgid)`：

1. 在 `channel_messages` 中按 `metadata->>'wecom_msgid' = recall_msgid` 或 `metadata->'merged_from_msgids' ? recall_msgid` 查找匹配行（限定 `tenant_id` + `session_id` 对应的 channel session）。
2. **单条消息命中**：
   - `UPDATE channel_messages SET is_recalled = TRUE, recalled_at = NOW() WHERE id = ?`
3. **合并消息命中**（`merged_from_msgids` 包含 `recall_msgid`）：
   - 走 3.4 的部分撤回重建逻辑。
4. **未命中**：消息可能尚未持久化（极少，因 user 消息在 `process_and_persist` 入口就写库），或已超 TTL 被压缩归档。记 `tlog` 并跳过。

> **会话定位**：通过 `external_userid` + `open_kfid` 反查 `channel_sessions` 拿到 `session_id`，再在该 session 范围内查 `channel_messages`。避免全表扫描。

### 3.4 合并消息的部分撤回重建（G4）

合并消息的部分撤回是最复杂的分支。策略：

1. 读出该行的 `metadata.merged_segments`（每段 `{msgid, text}`）。
2. 过滤掉 `recall_msgid` 对应的段，剩余段用 `\n` 重新拼接为新 content。
3. 更新：
   - `content = 新文本`
   - `metadata.recalled_part_msgids = 原列表 + [recall_msgid]`
   - `metadata.original_content_before_recall = 原content`（仅首次撤回时写入，避免覆盖）
   - `is_recalled = FALSE`（合并消息整体不标记撤回，因为还有有效段）
   - 若所有段都被撤回 → `is_recalled = TRUE`
4. 若该合并消息是**当前正在处理的会话**最近一轮 user 消息，且 agent 尚未回复：
   - 理想情况是取消当前 agent 调用，但成本高、窗口极小（撤回事件到达时 agent 多半已回复）。
   - **本期不处理**：仅标记持久化层，agent 当前轮次照常完成。后续轮次上下文重建时自然剔除。记 `tlog` 标注此场景。

### 3.5 上下文重建剔除（G3 的读取侧）

修改 `ChannelSessionManager.get_messages`（或新增 `get_messages_for_context` 方法），在查询时过滤 `is_recalled = FALSE`：

```sql
SELECT * FROM channel_messages
WHERE session_id = %s AND is_recalled = FALSE
ORDER BY created_at ASC
LIMIT %s
```

**仅 LLM 上下文重建路径过滤**。后台展示路径（外部接待客户、会话追踪）不过滤，见 3.6。

涉及读取点：
- `src/core/agent.py:_load_channel_history` — 调 `channel_session_manager.get_messages`
- `src/core/short_term.py:_load_from_db` — 渠道会话分支

> **合并消息部分撤回的读取**：因 3.4 已在写入时重建 content，读取侧无需特殊处理，过滤 `is_recalled=FALSE` 即可（部分撤回的合并消息 `is_recalled` 仍为 FALSE，content 已是剔除后的文本）。

### 3.6 后台视图可见（G5）

后台视图**不过滤** `is_recalled`，并展示撤回标记：

| 视图 | 后端 | 前端 |
|------|------|------|
| 外部接待客户（`ExternalCustomerService`） | 消息列表接口返回 `is_recalled` / `recalled_at` / `metadata.recalled_part_msgids` | 消息气泡加"已撤回"徽章；部分撤回时在对应段加删除线 |
| 会话追踪（Trace） | TraceCollector 已从 `channel_messages` 读取，确保不过滤 | trace 消息项加"已撤回"标记 |

> 需要先确认这两个视图的查询入口是否共用 `get_messages`。若共用，需为后台视图新增 `include_recalled=True` 参数的查询方法，避免影响 LLM 上下文路径。

## 4. 任务分解

### 阶段 1：数据模型与持久化（基础）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 1.1 | `channel_messages` 加 `is_recalled` / `recalled_at` 字段 | `deploy/init-postgres.sql`、`deploy/db_update.sql` | 0.05d | ⏳ |
| 1.2 | `ChannelSessionManager.add_message` / `add_messages_batch_transactional` 透传 metadata（已支持，确认 wecom_msgid 写入） | `src/channels/session.py` | 0.05d | ⏳ |
| 1.3 | wecom_kf 用户消息持久化时写入 `metadata.wecom_msgid`（单条）/ `merged_from_msgids` + `merged_segments`（合并） | `src/saas/api/channel_routes.py`、`src/channels/session.py` | 0.2d | ⏳ |
| 1.4 | 新增 `_mark_recalled_message`（单条消息标记分支） | `src/channels/session.py` 或 `src/saas/api/channel_routes.py` | 0.2d | ⏳ |

### 阶段 2：同批次剔除 + 事件去重（G1 + G2）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 2.1 | `_process_tenant_wecom_kf_messages` 在 origin 过滤前插入撤回事件识别分支 + 事件去重 | `src/saas/api/channel_routes.py` | 0.2d | ⏳ |
| 2.2 | 收集本批次 `recalled_msgids_in_batch`，合并前从 `valid_msgs` 剔除 | `src/saas/api/channel_routes.py` | 0.1d | ⏳ |
| 2.3 | 清理本次排查用的 tlog（`微信事件` 主题，保留为注释） | `src/saas/api/channel_routes.py` | 0.05d | ⏳ |

### 阶段 3：跨批次撤回标记（G3 + G4）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 3.1 | `_mark_recalled_message` 合并消息部分撤回分支：读 `merged_segments` → 重建 content → 更新 metadata | `src/channels/session.py` | 0.3d | ⏳ |
| 3.2 | `_mark_recalled_message` 单条消息标记分支（已在 1.4，此处联调） | 同上 | 0.05d | ⏳ |
| 3.3 | 未命中 / 当前轮次正在处理等边界场景的 tlog | `src/saas/api/channel_routes.py` | 0.05d | ⏳ |

### 阶段 4：上下文重建剔除（G3 读取侧）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 4.1 | `get_messages` 新增 `include_recalled` 参数，默认 `False`（LLM 上下文用） | `src/channels/session.py` | 0.1d | ⏳ |
| 4.2 | `_load_channel_history` 走 `include_recalled=False` | `src/core/agent.py` | 0.05d | ⏳ |
| 4.3 | `short_term._load_from_db` 渠道分支走 `include_recalled=False` | `src/core/short_term.py` | 0.05d | ⏳ |

### 阶段 5：后台视图可见（G5）

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 5.1 | 外部接待客户消息列表接口：返回撤回字段，查询时 `include_recalled=True` | 后端接口 + `src/channels/session.py` | 0.15d | ⏳ |
| 5.2 | 外部接待客户前端：已撤回徽章 + 部分撤回删除线 | `frontend/src/components/.../ExternalCustomerService.vue` | 0.25d | ⏳ |
| 5.3 | 会话追踪：确认 trace 读取入口不过滤 is_recalled，前端加撤回标记 | `src/core/trace_collector.py` + 前端 trace 组件 | 0.15d | ⏳ |

### 阶段 6：测试

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 6.1 | 单元测试：撤回事件识别 + 事件去重 | `tests/unit/...` | 0.2d | ⏳ |
| 6.2 | 单元测试：同批次剔除（撤回事件在文本消息之后到达） | 同上 | 0.15d | ⏳ |
| 6.3 | 单元测试：单条消息跨批次标记 | 同上 | 0.15d | ⏳ |
| 6.4 | 单元测试：合并消息部分撤回重建 content | 同上 | 0.2d | ⏳ |
| 6.5 | 单元测试：`get_messages(include_recalled=False)` 过滤 | 同上 | 0.1d | ⏳ |
| 6.6 | 集成测试：真实微信撤回 → 后台视图可见 + LLM 上下文剔除 | 手工 | 0.2d | ⏳ |

### 阶段 7：文档同步

| # | 任务 | 涉及文件 | 预计 | 状态 |
|---|------|---------|------|------|
| 7.1 | 在 `message-merge.md` 增补"撤回事件处理"章节 | `docs/channel/wecom_kf/message-merge.md` | 0.1d | ⏳ |
| 7.2 | `docs/ideas.md` 登记并更新状态 | `docs/ideas.md` | 0.05d | ⏳ |
| 7.3 | 开发完成后将条目移至 `docs/ideas_finished.md` | `docs/ideas_finished.md` | 0.05d | ⏳ |

## 5. 总预计

约 **3 人天**。阶段 1-2（同批次剔除 + 事件去重）约 1 人天，可先行落地；阶段 3-5（跨批次 + 后台视图）约 1.7 人天；测试 + 文档约 0.3 人天。

## 6. 风险

| 风险 | 说明 | 缓解 |
|------|------|------|
| 撤回事件早于消息持久化 | 极少：user 消息在 `process_and_persist` 入口就写库，撤回事件通常晚到。但理论上同批次内撤回事件可能在消息写库前处理 | `_mark_recalled_message` 未命中时记 tlog，不报错；同批次剔除（3.2）已覆盖此场景 |
| 合并消息部分撤回重建 content 影响已发送的回复 | agent 已基于合并内容回复，撤回后重建 content 会让上下文与已发回复不一致 | 仅影响后续轮次上下文；本期接受此不一致，后续可评估是否在回复中标注 |
| 撤回事件多次推送 | 同一 `recall_msgid` 跨多次 sync_msg 重复到达 | 按事件自身 msgid 去重（3.1.3） |
| `merged_segments` 未写入的老数据 | 老的合并消息 metadata 无 `merged_segments`，无法部分撤回重建 | 降级：整条标记 `is_recalled=TRUE`（损失其他段），记 tlog |
| 后台视图与 LLM 上下文共用查询入口 | 若都走 `get_messages`，加 `include_recalled` 参数后需确保两条路径分别传 True/False | 4.1 显式参数化，5.1/5.3 后台路径显式传 True |
| 跨批次撤回时 agent 正在处理当前轮 | 撤回的是"最近一轮 user 消息"，agent 可能正在回复 | 本期不取消 agent 调用，仅标记持久化层；后续轮次自然剔除。记 tlog |

## 7. 未决问题

- **Q1**：合并消息全部段都被撤回时，是否需要在后台视图把整条标记为"已撤回"？目前设计 `is_recalled=TRUE`，后台视图按整条撤回展示。
- **Q2**：是否需要在前端"外部接待客户"页面提供"撤回的段"的原文查看入口（点击展开 `original_content_before_recall`）？取决于产品诉求，本期先做徽章 + 删除线。

## 8. 相关代码

| 文件 | 关键函数 |
|------|---------|
| `src/saas/api/channel_routes.py` | `_process_tenant_wecom_kf_messages` / `_merge_consecutive_user_messages` / `_mark_recalled_message`（新增） |
| `src/channels/session.py` | `ChannelSessionManager.add_message` / `add_messages_batch_transactional` / `get_messages`（新增 `include_recalled` 参数） |
| `src/core/agent.py` | `_load_channel_history` |
| `src/core/short_term.py` | `_load_from_db` |
| `src/core/trace_collector.py` | trace 读取入口 |

## 9. 相关文档

- [wecom_kf 消息合并处理机制](./message-merge.md)
- [wecom_kf 设计文档](./wecom_kf_design.md)
- [上下文重建陷阱](../../research/context-reconstruction-pitfalls.md)
