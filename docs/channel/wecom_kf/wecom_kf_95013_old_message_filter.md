# 微信客服 95013 (conversation end) 旧消息重放根因调查与 30 分钟过滤方案

> 本文档为 2026-08-28 生产 `wecom_kf 95013` 报错的**独立重查报告**，与既有文档 `wecom_kf_95013_conversation_end_investigation.md` 并存，不依赖其结论（该文档多轮修改后定位有误，见 §3）。
>
> 核心诉求：超过 30 分钟的旧消息**没有回复价值**——微信侧客户不可能等 30 分钟，长时间无响应会让客户认定系统出错。因此本文档的核心方案是从源头丢弃旧消息，同时消除其触发的 95013 报错刷屏。

---

## 1. TL;DR

**一句话根因**：账号 B 的本地同步 cursor 因 3 天 TTL 到期丢失，导致 `sync_msg` 不带 cursor 触发微信 3 天保留窗口**全量重放**；去重表 TTL 仅 5 分钟拦不住跨天旧消息；22 条 08-25 已结束会话（remote=4）的旧消息进入发送前校验，恢复点 3 的 `(0,4)` 合并分支对已结束会话调用 `trans_service_state(1)`，而微信规则**状态 4 禁止 API 变更状态**，必然返回 95013，共 22 次。

**三层根因叠加**（缺一不可）：

| 层 | 根因 | 代码位置 |
|----|------|---------|
| ① | cursor TTL 3 天（与微信消息保留期一致，但这是**本地游标**，不该耦合）→ 到期丢 cursor | `cursor.py:16` `CURSOR_TTL = 259200` |
| ② | 消息去重 TTL 仅 5 分钟 → 拦不住 3 天前旧消息重放 | `idempotency.py` `ttl_seconds=300` |
| ③ | 恢复点 3 的 `(0,4)` 合并分支对已结束会话调 `trans(1)` → 必然 95013 | `channel_routes.py:2275-2300` |

**业务影响**：本次事件**无消息丢失**。新消息（`AZJrfSNh5PWJ1UgB8nuK1iM29M`）正常 AI 回复；22 条旧消息本就是 08-25 已处理过的重复消息，丢弃无害，但每条触发一次注定失败的 `trans_service_state`（95013 刷屏 + 无意义 API 调用 + 消息被 `continue` 丢弃不落库）。

**修复优先级**：

- **P0（核心，本文档重点）**：预处理阶段丢弃超过 30 分钟的旧消息，从源头消除 95013
- **P0（辅助）**：cursor TTL 长期化，避免 cursor 丢失触发全窗口重放
- **P0（兜底）**：恢复点 `(0,4)` 分支拆分，remote=4（已结束）不调 `trans(1)`
- **P1**：去重窗口延长（防重放的最后一道闸）

---

## 2. 现象

2026-08-28 13:24 前后，生产环境 `aid-work-agent.log` 出现 44 条含 `errcode=95013` 的日志：

- **22 条 ERROR**：`src/channels/wecom_kf/api_client.py:395`「微信客服变更会话状态失败: errcode=95013, errmsg=conversation end...」
- **22 条 WARNING**：`src/saas/api/channel_routes.py:2296`「远程状态切回智能助手失败: ... errcode=95013」

> **口径澄清**：44 次报错 = 22 次 ERROR + 22 次 WARNING，两者对应**同一次** `trans_service_state(1)` 调用失败（api_client 记 ERROR，channel_routes 恢复点记 WARNING）。实际 `trans` 失败 **22 次**，不是 44 次。

同时期 `sync_msg` 一次性拉回 25 条消息，其中 24 条被丢弃（2 条 enter_session 事件正常处理），仅 1 条新消息走了 AI 回复。

---

## 3. 独立调查证据链

### 3.1 报错来源定位：恢复点 3，不是恢复点 1/2

`channel_routes.py` 有三处会对 `(0,4)` 状态调 `trans_service_state(1)` 的恢复点：

| 恢复点 | 代码位置 | 进入条件 | 本次是否触发 |
|--------|---------|---------|-------------|
| 1 | `:2136` | 本地 state=3 **且** 有 `exit_human_timeout_failed_at` 标记 | ❌（本地 state=1，且无标记） |
| 2 | `:2194` | 本地 state=3，无标记，远程校准发现 (0,4) | ❌（本地 state=1，不进此分支） |
| 3 | `:2275` | 发送前校验，remote_state != 1 **且** remote_state in (0,4) | ✅（22 次全来自此处） |

**旧文档定位错误**：`wecom_kf_95013_conversation_end_investigation.md` 曾断言报错来自恢复点 1/2 的 `(0,4)` 合并分支。但恢复点 1/2 的**前提是本地 state=3**（人工接待）；本次 22 条旧消息所属会话本地 state=1（智能助手），根本进不了恢复点 1/2，只有恢复点 3（发送前校验）会走到。生产日志行号 `channel_routes.py:2296` 证实了这一判断。

### 3.2 消息来源定位：3 天前的旧消息重放

微信 `sync_msg` 接口的 cursor 机制（见「微信客服发送消息接口官网说明.txt」）：

> 消息记录保存 **3 天**，可通过 cursor 分页拉取；不传 cursor 时从当前会话时间往前取 3 天窗口内全部消息。

本次 25 条消息 = **22 条 08-25 13:23 前的旧消息** + 2 条 enter_session 事件 + 1 条新消息（remote=0，正常走 AI 回复）。生产日志逐条 `msgid / send_time` 核对确认：

- 22 条旧消息 `send_time` 均在 08-25 13:20-13:23 区间（3 天前）
- 这 22 条全部 `remote_service_state=4`（会话已结束）
- 新消息 `AZJrfSNh5PWJ1UgB8nuK1iM29M` `send_time` 为 08-28 13:24，`remote=0`，`trans(1)` 成功，正常 AI 回复

### 3.3 cursor 丢失时间线（三层根因的诱因）

| 时刻 | 事件 |
|------|------|
| 08-25 13:23 后 | 账号 B 智能体对话后**回调缺失**，`sync_msg` 不再被触发，cursor 停滞不再刷新 |
| 08-28 13:23:45 | Redis key `wecom_kf_cursor:{open_kfid}` 达到 `CURSOR_TTL=259200`（3 天）**到期删除** |
| 08-28 13:24:03 | 微信回调触发 `sync_msg`，`get_cursor()` 返回空串 → 不带 cursor 请求 → 微信返回 3 天窗口内全部消息 |

### 3.4 去重为什么拦不住

`MessageDeduplicator`（`src/channels/idempotency.py`）TTL 仅 `300s`（5 分钟）。5 分钟窗口只能拦截**回调重试**（微信 45s 内未确认会重推），拦不住 **3 天前的跨天重放**。生产验证：`channel_message_dedup` 表中无这 22 条 msgid 的记录（早已过期清理），故全部通过去重进入后续处理。

### 3.5 消息为什么没入库

数据库核查：`channel_messages` 表中**查不到**这 22 条 msgid（生产库、开发库均无）。原因：恢复点 3 中 `trans(1)` 失败后 `continue`，消息在保存用户消息（`:2332`）之前就被丢弃，从未落库。这是旧消息处理缺口的直接体现（配合 #61 员工-客户对话可见性一并修复）。

---

## 4. 根因分析

完整故障链：

```
cursor 3 天 TTL 到期（账号 B 08-25 后无回调刷新）
  -> get_cursor 返回空，sync_msg 不带 cursor
  -> 微信返回 3 天保留窗口内全部 25 条消息（22 条 08-25 已处理旧消息重放）
  -> 去重表 TTL 5 分钟，拦不住 3 天前旧消息
  -> 22 条旧消息 remote=4（会话已结束），进入发送前校验（恢复点 3）
  -> (0,4) 合并分支调 trans_service_state(1)
  -> 微信规则：状态 4（已结束）禁止 API 变更状态 -> errcode=95013
  -> 失败 continue，消息丢弃，不落库
```

三个根因**各自单独存在都不会出事**，叠加才爆发：

- cursor 到期但无重放（微信有活动频繁刷新）→ 不会拉回旧消息
- 有重放但去重窗口够长 → 旧消息被去重拦下
- 有重放且有旧消息，但恢复点区分 0 和 4 → 已结束会话不调 trans，不会 95013

---

## 5. 修复方案

### 5.1 P0 核心：30 分钟旧消息过滤（本文档重点）

**决策依据**：超过 30 分钟的旧消息没有回复价值。微信侧客户不可能等 30 分钟，长时间无响应会让客户认定系统出错；且旧消息大概率是已结束会话（remote=4）的重放，回复大概率发不出（`trans(1)` 被拒 → 95013），白耗积分。

**过滤位置**：预处理循环内，`origin==3` 客户消息检查之后、消息类型检查（`should_process_kf_message`）之前。理由：

- enter_session / 撤回事件处理在前（`channel_routes.py:1784-1931`），不受影响
- 旧消息（含旧图片/视频等不可处理类型）一律丢弃，**不回复、也不发文件类型提示**，避免对旧附件刷提示语
- 过滤发生在合并（`:2011`）之前，逐条判定 `send_time` 更精确（合并消息取 `group[-1]` 的 send_time，若在合并后过滤会误判整组）

**过滤代码**（插入 `channel_routes.py:1954` 之后）：

```python
# 跳过非客户消息（origin=3 是客户，origin=4 是接待人员）
if msg.get("origin") != 3:
    logger.debug(f"[wecom_kf] 跳过非客户消息: msgid={msg_id}, origin={msg_origin}")
    continue

# ===== 旧消息过滤：超过 30 分钟没有回复价值，直接丢弃 =====
# 场景：cursor 到期 / 回调缺失积压时 sync_msg 会返回 3 天窗口内全部消息，
# 旧消息多为已结束会话(remote=4)重放，回复发不出(trans(1) -> 95013)且客户早已离开。
# send_time 为 Unix 秒级时间戳。
send_time = msg.get("send_time")
if send_time:
    elapsed = time.time() - int(send_time)
    if elapsed > 1800:  # 30 分钟
        logger.info(
            f"[wecom_kf] 丢弃超过30分钟的旧消息: msgid={msg_id}, "
            f"send_time={send_time}, elapsed={elapsed:.0f}s"
        )
        continue
# ===== 旧消息过滤结束 =====
```

**效果**：22 条 08-25 旧消息在进入发送前校验前即被丢弃 → 不触发 `trans(1)` → 95013 从源头消失；新消息（30 分钟内）不受影响，正常处理。

**阈值配置化建议**：把 `1800` 提取为模块级常量 `OLD_MESSAGE_MAX_AGE = 1800`（或渠道配置 `wecom_kf.old_message_max_age`），便于按租户/账号调整，且文档与代码语义一致。

### 5.2 P0 辅助：cursor TTL 长期化

**问题**：`cursor.py:16` 的 `CURSOR_TTL = 259200`（3 天）与微信消息保留期（3 天）一致，这是**本地增量游标**，与消息保留期无任何耦合。TTL 一旦先于保留期到期，cursor 丢失 → 全窗口重放。

**修复**：`CURSOR_TTL` 改为 30 天（`2592000`）或不做 TTL 限制（本地游标仅记录"上次同步到哪"，理论上永久有效）。

```python
CURSOR_TTL = 2592000  # 30 天（本地增量游标，与微信消息保留期无关，应远大于 3 天）
```

同时更新 `cursor.py:1-5` 的模块 docstring 中「与微信客服消息保留期一致」的错误描述。

> 注意：即使 cursor 长期化，也应保留 5.1 的 30 分钟过滤作为兜底——异常场景（Redis 清空、误删、跨 worker 不一致）仍可能触发重放。

### 5.3 P0 兜底：恢复点 (0,4) 分支拆分

**问题**：恢复点 3 的 `(0,4)` 合并分支（`channel_routes.py:2275`）对**所有**非 1 状态统一调 `trans(1)`，但微信规则明确：状态 4（已结束）**禁止 API 变更状态**（见官网说明），必然 95013。

**修复**：拆分 0 和 4：

```python
if remote_service_state in (0, 4):
    if remote_service_state == 4:
        # 会话已结束，微信禁止变更状态（errcode=95013）。
        # 不调 trans、不跑 AI（回复大概率发不出，白耗积分）。
        # 消息落库保上下文（source=customer_ended，见 #61），同步本地状态。
        channel_session_manager.update_session(
            session_id=session_id,
            metadata={"service_state": 4},
        )
        logger.info(
            f"[wecom_kf] 会话已结束(remote=4)，跳过AI处理: session_id={session_id}"
        )
        continue
    # remote=0（未处理）：可切回智能助手，行为不变
    ...trans_service_state(1)...
```

恢复点 1/2（`:2136` / `:2194`）的 `(0,4)` 合并分支同样按此拆分，保证所有路径一致。配合 #60 已有方案：`session_status_change` 事件收到 state=4 时同步本地 state 并清理 `exit_human_timeout_failed_at` 标记。

**与 5.1 的关系**：5.1 从源头消除**旧消息重放**引发的 95013；5.3 兜底**新消息但会话恰好已结束**（客户在 30 分钟内发消息，但会话已被员工/48h 规则结束）的边界情况。

### 5.4 P1：去重窗口延长

把 `MessageDeduplicator` 的 `ttl_seconds=300` 延长到 1 小时（`3600`）或更长，作为重放的**最后一道闸**。注意权衡：窗口过长会占用 `channel_message_dedup` 表空间（消息量大时），且对「同一 msgid 一周后由用户重新发送」的极端场景有误判风险。当前 5.1 + 5.2 已消除主要路径，此项可延后。

### 5.5 P2（可选）：新消息优先处理

同一批 `valid_msgs` 内按 `send_time` 升序处理已是现状（微信按时间返回），如需更激进可在合并前对批次排序，保证最新消息最先得到响应。此项非必需，仅在出现「旧消息积压拖慢新消息响应」时启用。

---

## 6. 代码修改点汇总

| 文件 | 位置 | 改动 |
|------|------|------|
| `src/saas/api/channel_routes.py` | `:1954` 后 | **新增 30 分钟旧消息过滤**（§5.1，P0 核心） |
| `src/channels/wecom_kf/cursor.py` | `:16` | `CURSOR_TTL` 3 天 → 30 天（§5.2，P0） |
| `src/saas/api/channel_routes.py` | `:2275`、`:2136`、`:2194` | `(0,4)` 分支拆分，remote=4 不调 trans（§5.3，P0） |
| `src/channels/idempotency.py` | 构造参数 | `ttl_seconds` 300 → 3600（§5.4，P1，可选） |

---

## 7. 测试建议

- **旧消息过滤**（核心）：
  - 正常路径：新消息（<30 分钟）正常进入 AI 处理
  - 边界：恰好 30 分钟（`elapsed == 1800`）不被过滤；30 分钟 + 1 秒被过滤
  - 异常路径：`send_time` 缺失 / 为 0 / 非数字 → 不崩溃，按不过滤处理（`if send_time:` 短路）
  - 合并前置：同一用户 2 条连续文本，其中 1 条超 30 分钟 → 超时那条被丢弃，剩余单条独立处理，不误合并
  - 事件不受影响：enter_session / 撤回事件（send_time 可能为旧值）不被 30 分钟过滤拦截
- **恢复点拆分**：remote=0 仍切回智能助手并继续 AI；remote=4 跳过 AI 但消息落库（配合 #61）、同步本地状态；remote=2/其他 走原有不允许发送分支
- **cursor TTL**：mock Redis 验证 set 时 `ex=2592000`；到期后 get 返回空、sync_msg 不带 cursor（结合过滤兜底验证不刷屏）
- **回归**：`tests/unit/` 全量 + `channels` 目录相关单测（wecom_kf adapter、idempotency）

---

## 8. 结论与遗留问题

**结论**：本次 95013 是「cursor TTL 到期 → 3 天窗口重放 → 去重拦不住 → 已结束会话调 trans」三层叠加的连锁故障。核心修复是**丢弃超过 30 分钟的旧消息**（无回复价值 + 消除 95013），辅以 cursor 长期化与恢复点拆分，三重防线可杜绝同类刷屏。

**遗留问题**：

- 账号 B 08-25 后**回调缺失的根因**尚未定位（为何 sync_msg 未被触发）——若回调机制本身有缺陷，单纯延长 cursor TTL 只是推迟问题复发。需单独排查回调注册/接收链路（可参考 #60 的 sync_msg 定时兜底方案）。
- 旧消息（remote=4）**消息落库**依赖 #61（员工-客户对话可见性）的 `source=customer_ended` 方案，两文档需协同落地。
- 48h 无活动会话自动结束规则下，客户在会话结束后重新发消息，remote 会回到 0（可正常切回智能助手）——此场景由 §5.3 的 remote=0 分支覆盖，需真机验证。

---

## 关联文档

- 旧报告：[wecom_kf_95013_conversation_end_investigation.md](./wecom_kf_95013_conversation_end_investigation.md)（定位有误，本文档为准）
- 员工-客户对话可见性：[wecom_kf_servicer_conversation_visibility.md](./wecom_kf_servicer_conversation_visibility.md)（#61）
- 微信客服发送消息接口官网说明：[微信客服发送消息接口官网说明.txt](./微信客服发送消息接口官网说明.txt)（service_state 状态机 / sync_msg cursor 机制原文）
- ideas.md 登记：#60「微信客服 95013 (conversation end) 错误修复」
