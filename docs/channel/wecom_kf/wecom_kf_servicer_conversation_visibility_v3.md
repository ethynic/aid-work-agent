# 微信客服：员工-客户对话可见性方案（v3）

> 日期：2026-08-29
> 状态：**待实施设计**（v2 已作废，作废原因见 §10）
> 关联：[95013 旧消息过滤方案](wecom_kf_95013_old_message_filter.md)（已落地 f07cb2ad）
> 目标：解决「智能体看不到企业微信员工与微信客户之间人工接待期对话」的问题

---

## 1. 背景与现状

微信客服（wecom_kf）人工接待期间（`service_state=3`）的对话，双方消息**均未入库**：

| 消息来源 | sync_msg origin | 当前处理 | 是否入库 |
|---------|----------------|---------|---------|
| 客户消息 | 3 | 走完整 agent 链路，但 state=3 / remote=4 时在 `channel_routes.py` 各分支 `continue` 跳过 | ❌ 否 |
| 员工消息 | 5 | `channel_routes.py:1956` `origin != 3` 直接 `continue`（仅 `_tlog` 埋点） | ❌ 否 |
| 系统事件 | 4 | 过滤 | ❌ 否（无需） |

**结论**：人工接待期间客户问了什么、员工回复了什么，智能体完全不可见。会话转回智能体后，上下文重建链路（`ChannelSessionManager.is_channel_session` + `_load_channel_history` 读 `channel_messages`）读不到人工期对话，无法衔接。

**与 95013 修复的关系**（已落地）：95013 方案已引入「30 分钟旧消息过滤」（`channel_routes.py:1960-1976`，`OLD_MESSAGE_MAX_AGE=1800`）和「恢复点 (0,4) 拆分」（remote=4 不调 trans、走 `update_session` + `continue`）。本方案的「已结束会话积压客户消息落库」就是在这些 remote=4 分支的 `continue` 前补落库，无需再依赖其它前置改动。

---

## 2. 方案设计

### 2.1 核心思路

在 sync_msg 处理中，把**人工接待期间（state=3）双方对话**持久化到 `channel_messages`，供智能体重新接入时通过既有上下文重建链路读到。

**关键约束**：`to_llm_messages`（`src/memory/short_term.py:312`）只透传 `user` / `assistant` 角色给 LLM，`system` 及其它角色会被丢弃（仅 `_skill_summary` 会转成 `[系统提醒]` + user 角色）。因此**员工消息必须以 `user` 角色入库，并在 content 前缀标识说话人**，与既有 `[系统提醒]` 前缀模式一致。

### 2.2 消息入库规范

| 消息 | role | content | metadata |
|------|------|---------|----------|
| 员工消息（origin=5） | `user` | 前缀 `[人工客服] ` + 原文 | `{"source": "servicer", "servicer_userid": ..., "msgid": ..., "open_kfid": ...}` |
| 人工期客户消息（origin=3, 远程=3/2/0 切回失败） | `user` | 原文（不加前缀） | `{"source": "customer_human", "msgid": ..., "open_kfid": ...}` |
| 已结束会话积压客户消息（origin=3, 远程=4） | `user` | 原文（不加前缀） | `{"source": "customer_ended", "msgid": ..., "open_kfid": ...}` |

**说明**：
- 员工消息加 `[人工客服] ` 前缀，让 LLM 明确区分「客户发言」与「人工客服发言」，避免把员工消息误当客户提问（否则 LLM 会试图回复员工）。
- 客户消息不带前缀，正常客户发言。
- `source` 仅用于前端展示与事后排查，**不影响 LLM 上下文重建**（三种都是 role=user）。实现时若想极简，`customer_human` 与 `customer_ended` 可合并为单一 `customer` 值；保留区分是为前端徽标（§7）与审计。
- 员工姓名解析：MVP 用固定前缀 `[人工客服]`，`servicer_userid` 存入 metadata 供后续按企微 API 反查姓名；避免每条消息都调员工查询接口打满限频。
- 员工消息仅文本入库（语音/图片/文件跳过），与 `should_process_kf_message` 口径一致。

### 2.3 不触发 AI

员工消息、人工期客户消息、已结束会话积压客户消息**只持久化，不触发 agent**。

### 2.4 去重

- 员工消息按 `servicer:{msgid}` 去重，复用 `_get_tenant_dedup`（TTL 3600），与客户消息 `msgid` 命名空间隔离。
- 客户消息复用第一段循环既有的 `msgid` 去重（`channel_routes.py:2007-2011`），持久化函数内无需重复去重。
- 员工消息不参与客户撤回流程（`user_recall_msg` 事件只关联 origin=3 消息）。
- DB 层 `channel_messages.message_id` 是 `msg_{uuid}` 唯一键，真正的业务去重在 Redis 层，二者不冲突。

### 2.5 会话归属

与客户消息同一会话：`get_or_create_session(channel_type="wecom_kf", channel_user_id=external_userid, tenant_id=..., subagent_id=subagent_type, channel_chat_id=open_kfid)`。`subagent_type` 在 `_process_tenant_wecom_kf_messages` 开头已解析。

---

## 3. 员工消息与客户消息统一的旧消息过滤（v2 遗漏，本版新增）

**问题**：现有 30 分钟旧消息过滤（`channel_routes.py:1960-1976`）位于 `origin != 3` 检查（`:1956`）**之后**，只保护客户消息。员工消息（origin=5）在 `:1956` 就 `continue` 了，而员工消息收集逻辑必须放在 `:1956` 之前，导致**员工消息完全不经过 30 分钟过滤**。

**后果**：cursor 丢失全量重放时（95013 同款场景），3 天前的旧员工消息也会被全量拉回，`servicer:{msgid}` 去重 TTL 仅 1 小时拦不住 → 旧员工消息重复入库污染上下文。

**修复**：提取公共过滤函数，客户消息与员工消息共用同一口径：

```python
def _is_stale_kf_message(msg: dict) -> bool:
    """send_time 超过 OLD_MESSAGE_MAX_AGE 视为过期（无回复价值）"""
    send_time = msg.get("send_time")
    if not send_time:
        return False
    try:
        return (time.time() - int(send_time)) > OLD_MESSAGE_MAX_AGE
    except (TypeError, ValueError):
        return False
```

现有客户消息过滤（`:1960-1976`）改为调用该函数（行为不变）；员工消息收集点同步应用：

```python
# 位于 channel_routes.py:1955 origin 检查处
if msg.get("origin") != 3:
    # 员工消息（origin=5）：收集入库，但同样过滤 30 分钟旧消息
    if msg.get("origin") == 5 and not _is_stale_kf_message(msg):
        servicer_msgs_to_persist.append(msg)
    continue
```

---

## 4. 顺序保证

### 4.1 为什么不能「循环后统一入库」

若把员工消息全部推迟到本页客户消息入库后统一写，人工期典型对话 C1 → S1 → C2 → S2 会变成 C1、C2、S1、S2 -- 员工的 S1 被挪到 C2 之后，LLM 重建上下文时会把 S1 的回答错配给 C2 的提问。**必须按 send_time 穿插入库**。

### 4.2 设计：员工消息队列 + 逐条穿插 flush

结构背景：消息处理是两段循环 -- 第一段循环遍历 `msg_list` 做过滤/去重/收集（客户消息进 `valid_msgs`，**此时尚未入库**），第二段循环遍历 `merged_msgs` 逐条处理并触发 AI。这决定了员工消息不能在第一段循环里立即入库（会早于它所回复的客户消息）。

1. 第一段循环：origin=5 消息按序 append 进 `servicer_msgs_to_persist`（msg_list 按 send_time 正序，队列天然有序；已过 §3 旧消息过滤）。
2. 第二段循环**每处理一条客户消息之前**，把队首 `send_time` 早于当前客户消息的员工消息逐条出队入库：

```python
for msg in merged_msgs:
    # 先入库 send_time 早于本条客户消息的员工消息，保证时间正序穿插
    while servicer_msgs_to_persist and (
        servicer_msgs_to_persist[0].get("send_time", 0) < msg.get("send_time", 0)
    ):
        _smsg = servicer_msgs_to_persist.pop(0)
        await _persist_kf_servicer_message(_smsg, open_kfid, tenant_id, subagent_type)
    ...  # 原有处理
```

3. 第二段循环结束后、cursor 保存前：flush 队列剩余（晚于本页最后一条客户消息的员工消息；跨页时它们早于下页首条客户消息，顺序仍正确）。

**正确性论证**：
- 客户消息的持久化（AI 路径在 `process_and_persist` 内、人工/已结束路径在 `continue` 前）都发生在本条消息处理过程中，即晚于循环体开头的 flush → 员工消息先于其后的客户消息入库 ✅；
- 队首比较保证员工消息晚于其之前的客户消息 ✅；
- 合并消息（`_merge_consecutive_user_messages`）只合并**连续**客户消息，中间夹有员工消息的客户消息不构成「连续」。`_build_merged_message` 用 `group[-1].copy()`，故 merged 消息的 `send_time` 取组内**末段**；因连续合并前提是组内无员工消息，首末段对 flush 比较结果等价，不影响顺序 ✅。

### 4.3 持久化函数

- `_persist_kf_servicer_message(msg, open_kfid, tenant_id, subagent_type)`：员工消息入库（§2.2 规范），异常吞掉不影响主流程，下轮重拉靠 `servicer:{msgid}` 去重防重复。
- `_persist_kf_context_customer_message(msg, session_id, open_kfid, tenant_id, source)`：客户消息入库（`source` 取 `customer_human` / `customer_ended`）。

两者都复用 `channel_session_manager.add_message(...)`（`src/channels/session.py:513`）做单条落库，内部已处理 `last_message_at` 更新与事务。

---

## 5. 「跳过 AI」continue 路径的落库规则（v2 逐行穷举改规则化）

v2 用表格逐行号穷举 continue 路径，行号随代码演进快速失效。本版改为**规则化**，按远程状态取值，不依赖行号：

> **凡在第二段循环中，因「会话不在智能助手接待状态」而 `continue` 的客户文本消息，统一在 `continue` 前落库**，`source` 按远程状态取值：

| 远程状态 | 场景 | source | 是否落库 |
|---------|------|--------|---------|
| 4（已结束） | 恢复点 1/2/3 的 remote=4 分支 | `customer_ended` | ✅ P0 |
| 3（人工接待） | 恢复点 1/2 判定仍在人工 | `customer_human` | ✅ P0 |
| 2（待接入池）/ 0（切回失败） | 不允许发送 / trans 失败 | `customer_human` | ✅ P0 |
| 查询状态抛异常 | 恢复点 1/2/发送前校验的 except 分支 | -- | P2 可不补（异常路径，频率低） |

**不落库（合理，维持现状）**：
- 隐藏命令（`channel_routes.py:2388`）：内部管理命令，内容敏感，不落库。
- 转人工关键词命中（`:2460`）与客服账号到期拦截（`:2467`）：均在 `process_and_persist` 之前 `continue`，属边界场景，P2 可选落库，本版不强制。

**实现建议**：落库调用统一封装为 `_persist_kf_context_customer_message`，在每个 `continue` 分支前调用一次即可；语音占位符过滤在该函数内统一处理（见下）。

### 语音占位符过滤（v2 已含，保留）

人工期/已结束期客户语音消息 `unified_msg.text` 是占位符 `"[语音消息]"`（ASR 只在 AI 处理路径执行），必须过滤，否则污染上下文：

```python
text = getattr(unified_msg, "text", None)
if not text or text.startswith("[语音消息"):
    return  # 语音占位符不入库（人工期不做 ASR）
```

---

## 6. 前端展示（可选增强，Phase 2）

外部接待客户页（`ExternalCustomerService.vue`）读取 `channel_messages` 时，若 `metadata.source` 为 `servicer` / `customer_human` / `customer_ended`，显示「人工客服」徽标等样式区分。当前不阻塞后端能力，可后置。

---

## 7. 测试计划

| 用例 | 覆盖点 |
|------|--------|
| 员工消息入库（text） | origin=5 → channel_messages 出现 role=user + `[人工客服] ` 前缀 + metadata.source=servicer |
| **员工消息旧消息过滤（v3 新增）** | origin=5 且 send_time 超 30 分钟 → 不入库、不收集 |
| 员工消息不触发 AI | mock agent，确认未调用 process_and_persist |
| 人工期客户消息入库 | 远程=3 → channel_messages 出现 role=user + metadata.source=customer_human，未触发 AI |
| 已结束会话积压入库 | 远程=4 → source=customer_ended，未触发 AI |
| **穿插顺序** | 同页 C1 → S1 → C2 → S2，入库顺序必须为 C1、S1、C2、S2 |
| **队尾员工消息** | 员工消息晚于本页最后客户消息 → 循环后 flush，先于下页客户消息 |
| **语音占位符过滤** | 人工期语音消息（text="[语音消息]"）不入库 |
| 消息去重 | 同 msgid 员工消息重复拉取只入库一次（`servicer:` 前缀 key） |
| 上下文重建包含 | `_load_channel_history` 返回含 `[人工客服] ` 前缀消息 |
| 员工消息不误弹末尾 user | content 带前缀 ≠ current_user_input，不被 pop |

---

## 8. 影响与风险

- **不影响既有链路**：仅新增「持久化」，不改变 agent 触发逻辑；正常智能体对话消息流不受影响。
- **入库量增加**：人工接待期间消息也会入库，`get_messages` 条数变多，既有 `max_messages` 限制已覆盖。
- **员工消息仅文本**：图片/语音/文件跳过（MVP，与 `should_process_kf_message` 口径一致），后续可扩展。
- **批量积压场景**：`created_at` 为拉取时刻而非原始 `send_time`，但 `get_messages` 按 id 升序，穿插 flush 保证相对顺序，上下文正确性不受影响。
- **逐条落库开销**：员工消息量小，`add_message` 每条开一次连接可接受；如单批员工消息过多再考虑批量写入。

---

## 9. 实施清单（本方案尚未落地）

| 项 | 内容 | 依赖 |
|----|------|------|
| 1 | 提取 `_is_stale_kf_message`，客户消息过滤改用之（§3） | 无 |
| 2 | 员工消息收集 + 30 分钟过滤（§3） | 项 1 |
| 3 | `_persist_kf_servicer_message`（员工消息入库） | 无 |
| 4 | `_persist_kf_context_customer_message`（客户消息入库，参数化 source + 语音过滤） | 无 |
| 5 | 第二段循环穿插 flush（§4.2） | 项 2/3 |
| 6 | 各 continue 路径落库（§5） | 项 4 |
| 7 | 单测（§7） | 项 1-6 |

---

## 10. 相对 v2 的修正清单

| # | v2 问题 | v3 修正 |
|---|---------|---------|
| 1 | §5 声称「员工消息收集/入库函数/人工期客户消息入库已实施」，实际代码中全部不存在 | 改为 §9「实施清单」，明确待实施 |
| 2 | 员工消息无 30 分钟旧消息过滤口径（交互漏洞） | 新增 §3，员工消息与客户消息共用过滤 |
| 3 | §4/§9 称 remote=4 落库依赖 95013 §6.1 拆分 | 95013 已落地拆分（f07cb2ad），依赖已满足，改为「在 remote=4 分支 continue 前补落库」 |
| 4 | §4 表格行号全部基于旧工作区 | §5 改规则化，不依赖行号；§3/§4 行号更新为当前工作区 |
| 5 | §3.2 称 merged 消息取「首段」send_time | 修正为「末段」（`group[-1].copy()`），并说明首末段对 flush 等价 |
| 6 | §4 逐行穷举 continue 路径，易失效 | §5 改按远程状态规则化 |
