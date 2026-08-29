# 微信客服：员工-客户对话可见性方案

> 日期：2026-08-29（v2 重写。v1 的顺序保证论证有误，入库时机设计已重做；其余保留修正）
> 关联：[95013 错误调查与修复方案](wecom_kf_95013_conversation_end_investigation.md)
> 目标：解决「智能体看不到企业微信员工与微信客户之间对话内容」的问题

---

## 1. 背景与现状

### 1.1 现状

微信客服（wecom_kf）人工接待期间（`service_state=3`）的对话，双方消息**均未入库**：

| 消息来源 | sync_msg origin | 当前处理 | 是否入库 |
|---------|----------------|---------|---------|
| 客户消息 | 3 | 走完整 agent 处理链路，但 state=3 时在 `channel_routes.py` 各人工期分支 `continue` 跳过 | ❌ 否 |
| 员工消息 | 5 | 过滤（v1 前直接丢弃；当前工作区已改为收集入库，见 §5） | 🔧 部分 |
| 系统事件 | 4 | 过滤 | ❌ 否（无需） |

**结论**：人工接待期间客户问了什么、员工回复了什么，智能体完全不可见。会话转回智能体（重新接入）后，智能体缺少人工期上下文，无法衔接。

### 1.2 为什么现在才暴露

- `sync_msg` 能拉到员工消息（origin=5，带 `servicer_userid`）-- 官方文档已确认（[94670](https://developer.work.weixin.qq.com/document/path/94670)：「微信客户发送的消息、接待人员在企业微信回复的消息……可以通过该接口获取」；另「接待人员在企业微信客户端发送的消息」即 origin=5），代码也在 `log/temp/wecom_kf_servicer_msg.log` 验证过（如 10:14:41 RuiXiu 发送「你好」），但此前被过滤未入库；
- 转人工、人工期对话、结束会话再接入是近期才开通的完整链路，此前未暴露"上下文断裂"问题。

---

## 2. 方案设计

### 2.1 核心思路

在 sync_msg 处理中，把**人工接待期间（state=3）双方对话**持久化到 `channel_messages`，供智能体重新接入时通过既有上下文重建链路（`_load_channel_history`）读到。

**关键约束**：`to_llm_messages`（`src/memory/short_term.py:312`）只透传 `user` / `assistant` 角色给 LLM，`system` 及其它角色会被丢弃。因此**员工消息必须以 `user` 角色入库，并在 content 前缀标识说话人**，与既有 `[系统提醒]` 前缀模式（`short_term.py:341-345`）一致。

### 2.2 消息入库规范

| 消息 | role | content | metadata |
|------|------|---------|----------|
| 员工消息（origin=5） | `user` | 前缀 `[人工客服] ` + 原文 | `{"source": "servicer", "servicer_userid": ..., "msgid": ..., "open_kfid": ...}` |
| 人工期客户消息（origin=3, state=3） | `user` | 原文（不加前缀） | `{"source": "customer_human", "msgid": ..., "open_kfid": ...}` |
| 已结束会话积压客户消息（origin=3, state=4） | `user` | 原文（不加前缀） | `{"source": "customer_ended", "msgid": ..., "open_kfid": ...}` |

**说明**：
- 员工消息加 `[人工客服] ` 前缀，让 LLM 明确区分"客户发言"与"人工客服发言"，避免把员工消息误当客户提问（否则 LLM 会试图回复员工）。
- 客户消息不带前缀（正常客户发言），metadata 的 `source` 供前端展示 / 过滤区分。
- 员工姓名解析：MVP 用固定前缀 `[人工客服]`，`servicer_userid` 存入 metadata 供后续按企微 API 反查姓名；避免每条消息都调员工查询接口打满限频。
- 员工消息仅文本入库（语音/图片/文件跳过），与 `should_process_kf_message` 口径一致。

### 2.3 不触发 AI 处理

员工消息、人工期客户消息、已结束会话积压客户消息**只持久化，不触发 agent**。

### 2.4 去重与撤回

- 员工消息按 `msgid` 去重，去重 key 加 `servicer:` 前缀（`servicer:{msgid}`），与客户消息命名空间隔离，复用 `_get_tenant_dedup`。
- 客户消息复用第一段循环里既有的 `msgid` 去重，持久化函数内无需重复去重。
- 员工消息不参与客户撤回流程（`user_recall_msg` 事件只关联 origin=3 消息）。

### 2.5 会话归属

与客户消息同一会话：`get_or_create_session(channel_type="wecom_kf", channel_user_id=external_userid, tenant_id=..., subagent_id=subagent_type, channel_chat_id=open_kfid)`。`subagent_type` 在 `_process_tenant_wecom_kf_messages` 开头已解析。

---

## 3. 顺序保证（v2 重写，v1 的核心错误）

### 3.1 v1 为什么错

v1 设计为「第一段循环收集员工消息，本页客户消息全部入库后统一入库」，并声称可保证相对顺序。**该论证只保证员工消息晚于同批全部客户消息，不保证时间正序穿插**：人工期典型对话 C1 → S1 → C2 → S2（客户/员工交替），入库后变成 C1、C2、S1、S2 -- 员工的 S1 被挪到 C2 之后，LLM 重建上下文时会把 S1 的回答错配给 C2 的提问。

v1 给出的"立即入库会使员工消息 id 早于同批客户消息"的理由只对**页首**员工消息成立；对穿插场景方向恰好相反（推迟入库才制造乱序）。

### 3.2 v2 设计：按 send_time 穿插入库

结构背景：消息处理是两段循环--第一段循环遍历 `msg_list` 做过滤/去重/收集（客户消息进 `valid_msgs`，**此时尚未入库**），第二段循环遍历 `merged_msgs` 逐条持久化并触发 AI。这决定了员工消息不能在第一段循环里立即入库（会早于它所回复的客户消息）。

v2 方案：**员工消息队列 + 逐条穿插 flush**。

1. 第一段循环：origin=5 消息按序 append 进 `servicer_msgs_to_persist`（msg_list 按 send_time 正序，队列天然有序）；
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
- 客户消息的持久化（AI 路径在 `process_and_persist` 内、人工路径在 `continue` 前）都发生在本条消息处理过程中，即晚于循环体开头的 flush → 员工消息先于其后的客户消息入库 ✅；
- 队首比较保证员工消息晚于其之前的客户消息 ✅；
- 合并消息（`_merge_consecutive_user_messages`）只合并连续客户消息，中间夹有员工消息的客户消息不构成"连续"，merged 消息取首段 `send_time`，不影响顺序 ✅。

### 3.3 持久化函数

- `_persist_kf_servicer_message(msg, open_kfid, tenant_id, subagent_type)`：员工消息入库（§2.2 规范），异常吞掉不影响主流程，下轮重拉靠 `servicer:{msgid}` 去重防重复。
- `_persist_kf_context_customer_message(unified_msg, session_id, msg_id, open_kfid, tenant_id, source)`：客户消息入库（参数化 `source`，取 `customer_human` / `customer_ended`），替代 v1 的 `_persist_kf_human_customer_message`。
- **语音占位符过滤（v1 遗漏）**：人工期/已结束期客户语音消息 `unified_msg.text` 是占位符 `"[语音消息]"`（ASR 只在 AI 处理路径执行），必须过滤，否则污染上下文：

```python
text = getattr(unified_msg, "text", None)
if not text or text.startswith("[语音消息"):
    return  # 语音占位符不入库（人工期不做 ASR）
```

---

## 4. 覆盖所有"跳过 AI"的 continue 路径（v1 遗漏）

逐条核对第二段循环中会跳过 AI 处理、且消息尚未持久化的路径，**每条都必须先持久化再 continue**：

| 路径 | 位置（当前工作区） | v1 状态 | v2 要求 |
|------|------------------|---------|---------|
| 本地 state=3 + 超时失败标记，远程=3 | `channel_routes.py:2220` | ✅ 已补 | 保留 |
| 本地 state=3，远程=3（`should_exit_human` 判定不退出） | `channel_routes.py:2315` | ✅ 已补 | 保留 |
| 发送前校验：远程非 1 且非 (0,4)（含 3 待接入/人工等） | `channel_routes.py:2393-2403` | ❌ 缺失 | **补**（`source="customer_human"`，远程=3）/ 待接入池(2)按 `customer_human` 同样入库保上下文 |
| 恢复点 remote=4（积压消息） | `channel_routes.py:2224/2282/2367` 拆分后 | ❌ 缺失 | **补**（`source="customer_ended"`，见 95013 方案 §6.1） |
| 状态查询抛异常的 continue | `channel_routes.py:2258-2263 / 2326-2331` | ❌ 缺失 | P2 可不补（异常路径，频率低） |

---

## 5. 实施状态（当前工作区未提交改动）

| 项 | v1/v2 要求 | 当前工作区状态 |
|----|-----------|--------------|
| 员工消息收集（origin=5） | §3.2 第 1 步 | ✅ 已实施（收集进 `servicer_msgs_to_persist`） |
| 员工消息入库函数 | §3.3 | ✅ 已实施（`_persist_kf_servicer_message`） |
| 员工消息入库时机 | §3.2 穿插 flush | ❌ 当前实现为"循环后统一入库"（v1 错误设计），**需改为穿插 flush** |
| 人工期客户消息入库 | §4 前两条路径 | ✅ 已实施（`_persist_kf_human_customer_message`，需参数化 source + 补语音过滤） |
| 发送前校验路径入库 | §4 第三条 | ❌ 未实施 |
| 积压消息（remote=4）入库 | §4 第四条 | ❌ 未实施（依赖 95013 方案 §6.1 拆分） |
| 单测 | §7 | 🔧 已有基础用例，需补顺序/语音/新路径用例 |

---

## 6. 前端展示（可选增强，Phase 2）

外部接待客户页（`ExternalCustomerService.vue`）读取 `channel_messages` 时，若 `metadata.source` 为 `servicer` / `customer_human` / `customer_ended`，显示「人工客服」徽标等样式区分。当前不阻塞后端能力，可后置。

---

## 7. 测试计划

| 用例 | 覆盖点 |
|------|--------|
| 员工消息入库（text） | origin=5 → channel_messages 出现 role=user + `[人工客服] ` 前缀 + metadata.source=servicer |
| 员工消息不触发 AI | mock agent，确认未调用 process_and_persist |
| 人工期客户消息入库 | state=3 → channel_messages 出现 role=user + metadata.source=customer_human，未触发 AI |
| **穿插顺序（v2 新增）** | 同页 C1 → S1 → C2 → S2，入库顺序必须为 C1、S1、C2、S2 |
| **队尾员工消息（v2 新增）** | 员工消息晚于本页最后客户消息 → 循环后 flush，先于下页客户消息 |
| **语音占位符过滤（v2 新增）** | state=3 语音消息（text="[语音消息]"）不入库 |
| **发送前校验路径（v2 新增）** | 本地 state≠3、远程=3 → 持久化后 continue |
| 消息去重 | 同 msgid 员工消息重复拉取只入库一次（`servicer:` 前缀 key） |
| 上下文重建包含 | `_load_channel_history` 返回含 `[人工客服] ` 前缀消息 |
| 员工消息不误弹末尾 user | content 带前缀 ≠ current_user_input，不被 pop（`agent.py:1462-1465`） |

---

## 8. 影响与风险

- **不影响既有链路**：仅新增"持久化"，不改变 agent 触发逻辑；正常智能体对话消息流不受影响。
- **入库量增加**：人工接待期间消息也会入库，`get_messages` 条数变多，既有 `max_messages` 限制已覆盖。
- **员工消息仅文本**：图片/语音/文件跳过（MVP，与 `should_process_kf_message` 口径一致），后续可扩展。
- **批量积压场景**：`created_at` 为拉取时刻而非原始 `send_time`，但 `get_messages` 按 id 升序，穿插 flush 保证相对顺序，上下文正确性不受影响。

---

## 9. 关联修复

与 [95013 修复](wecom_kf_95013_conversation_end_investigation.md) 的依赖关系：
- 95013 方案 §6.1 拆分 `(0,4)` 分支后，remote=4 积压消息走本方案 `source="customer_ended"` 入库（§4 第四条）；
- §6.2 状态同步事件确保本地 state 及时更新，减少误入恢复分支；
- **注意**：95013 方案的 P0（§6.1/§6.2）当前尚未实施，本方案的第四条路径依赖其先行落地。
