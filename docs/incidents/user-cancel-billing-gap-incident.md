# 用户取消会话计费缺失复盘

> **发现时间**：2026-09-22
> **关联事故**：[analysis-agent-cost-392-credits-incident.md](./analysis-agent-cost-392-credits-incident.md)（第 1 次尝试「用户手动取消」即未落账，18:36 – 18:54 约 18 分钟消耗全部丢失）
> **影响**：所有 web 端用户手动取消的对话轮次，`chat_records` 无落账记录，已完成的 LLM 轮次用量（token + 积分）全部丢失，租户余额未扣减。

---

## 1. 问题现象

- 用户在 web 对话中点击「停止生成」手动取消正在进行的回复，该轮次在 `chat_records` 表中没有任何记录（无 token 统计、无 credit_cost、租户积分未扣减）。
- 前端消息历史正常显示「已停止」标记（这是前端本地 `cancelledByUser` 状态渲染的，与后端落账无关），因此 UI 上看不出计费缺失。

## 2. 计费落账机制回顾

计费落账唯一入口是 `SessionRecordService.save()`（`src/services/session_record.py:330`）：

- 按累计的 `_llm_usages`（每轮 LLM 调用后在 `agent.py:2602` 累加）计算 `credit_cost`
- 同事务写 `chat_records` + 原子扣减 `tenants.credit_balance`
- 由 `SessionRecordManager.end_record()`（`session_record.py:574`）触发，内部调用 `record.save()`

web SSE 入口（`src/main.py` `event_generator`）在两条取消路径上的收尾行为不同：

| 路径 | 触发条件 | 收尾代码 | 是否落账 |
|------|---------|---------|---------|
| cancel_check 路径 | Redis 取消标记（`cancelled_session:{sid}`）被 agent 检查点看到，agent 正常 return | `main.py:1503-1507` `mark_error + end_record` | ✅ 正常计费 |
| CancelledError 路径 | SSE 客户端断连 → starlette 取消 `event_generator` 所在任务 | `main.py:1480-1489` `except asyncio.CancelledError` 块 | ❌ 落账代码不可达 |

## 3. 根因分析（两处叠加，缺一不触发）

### 3.1 前端先断连、后通知取消

`frontend/web/composables/useAgent.ts` `abortStreaming()`（useAgent.ts:559-583）的执行顺序：

```
state.sseManager.disconnect()   -- ① 先断开 SSE（TCP FIN 立即到达服务端）
POST /api/chat/{sid}/cancel     -- ② 再通知后端设置 Redis 取消标记
```

断连后，starlette 的 `listen_for_disconnect` 收到 `http.disconnect`，立即取消 `StreamingResponse` 的流式任务 → 后端进入 CancelledError 路径；随后才到达的 `/cancel` 请求只设置了 Redis 标记（TTL 300s），但消费该标记的 agent 循环已经死亡，**cancel_check 路径永远不会被走到**。

### 3.2 CancelledError except 块内「先 yield 后落账」，落账代码不可达

`src/main.py:1480-1489`（修复前）：

```python
except asyncio.CancelledError:
    logger.info(f"[SSE] Agent cancelled by user, session_id={session_id}")
    error_occurred = "Cancelled by user"
    try:
        yield f"data: {...cancelled...}\n\n"     # ① 先 yield
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    if SessionRecordManager.get_current_record():
        SessionRecordManager.get_current_record().mark_error("Cancelled by user")
        SessionRecordManager.end_record()        # ② 后落账（永远执行不到）
```

starlette 基于 anyio cancel scope 实现取消：任务被取消后，cancel scope 内**所有后续 checkpoint 都会再次抛出 CancelledError**。`yield` 本身不是 checkpoint，但 `yield` 把值交给 `stream_response` 后，其内部的 `await send(...)` 是 checkpoint → 立即再次抛出 CancelledError → 异常从生成器中传播出去，`except` 块在 ① 与 ② 之间被中断：

- ② 的 `mark_error + end_record()`（落账）永远执行不到
- 后续 `main.py:1609` 的取消消息落库分支（带 `cancelled: True` 标记写 `chat_messages`）同样执行不到

## 4. 为什么取消瞬间正在执行的那轮 LLM 用量拿不到

前端取消 → 后端 agent 循环内 `task.cancel()`（`agent.py:424`）中断在途的 `chat_with_tools` 调用，该轮 usage 无法返回。**可计费的只有取消前已完成轮次的累计用量**——修复后落账的是这部分，不是「整轮全量」。属于预期内的合理损耗。

## 5. 修复方案

两处都改，缺一不可：

### 5.1 后端：CancelledError 块先落账再 yield（`src/main.py`）

把 `mark_error + end_record()` 移到 `yield` 之前。`end_record → save()` 是同步数据库调用，在被取消的任务内仍可完整执行；落账完成后再尝试 yield cancelled 帧（失败无害）。

### 5.2 前端：abortStreaming 先通知取消、再断连（`frontend/web/composables/useAgent.ts`）

先 `POST /api/chat/{sid}/cancel`，成功（或失败但已尽力通知）后再 `disconnect()`。这样后端 agent 循环有机会在检查点看到 Redis 标记，走 cancel_check 路径正常收尾（落账 + 取消消息落库）；即使 agent 停在长工具执行中、断连抢先发生，也有 5.1 的落账兜底。

## 6. 验证要点

- [ ] 手动取消：`chat_records` 出现该轮记录，`credit_cost` = 已完成轮次用量，`usage_breakdown` 正常
- [ ] 手动取消：`chat_messages` 中该轮 user + assistant（`cancelled: True`）消息落库，历史页显示取消标记（走 cancel_check 主路径；仅当 agent 停在长工具执行中、断连抢先发生时，兜底路径只保证计费，取消消息可能不落库）
- [ ] 页面刷新/关闭触发的断连取消：`chat_records` 有落账（CancelledError 兜底路径）
- [ ] 正常完成的轮次计费不回归（cancel_check 路径与成功路径均不受影响）
- [ ] 余额扣减与 `chat_records.credit_cost` 一致
- [ ] 渠道侧（wecom 等）取消路径不回归（本次改动只涉及 web SSE 入口与前端 web）

## 7. 关联问题：菜单切换取消的澄清

排查中发现的历史疑问「点击左侧菜单（新会话/切换会话）导致当前会话被取消且无 confirm」：

- 自 `80e8ec6c`（2026-07-20，多会话后台流式）起，切换/新建会话**不再取消**正在进行的会话（`MenuSidebar.vue:1208` 注释、`useAgent.ts:118` `switchSession` 仅切换查看目标、`sessionStreams` 模块级状态池后台保活）
- 旧版「切换即终止 + confirm 弹窗」行为（`cf8045db`，2026-04-25）已随该重构整体移除
- 若线上仍能复现「切走即取消」，优先怀疑线上前端 dist 落后于 2026-07-20，核对部署版本即可
- 唯一仍会「无 confirm 取消」的场景是页面刷新/关闭（浏览器卸载，技术上无法弹 confirm），此时后端按客户端断连处理，修复 5.1 后该场景同样能正常落账
