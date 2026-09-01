# Agent 用户可见中间消息（verbose）设计

> 状态：📋 待开发  
> 首版设计：2026-08-18  
> 架构复核重写：2026-08-31  
> 独立设计审计修订：2026-08-31（三轮审计，全部阻断项已闭环，可交付开发）  
> 产品决策收敛：2026-08-31（删除 LLM 候选路径，改为 Tool/Skill/System 全确定性文案）  
> 产品决策变更：2026-09-01（删除 system watchdog 兜底，verbose 仅由策略产生）  
> 关联计划：[plan-agent-intermediate-feedback.md](../plans/plan-agent-intermediate-feedback.md)  
> 关联现状：[微信客服等待提示计划](../plans/plan-wecom-kf-waiting-indicator.md)

## 1. 最终方案

新增独立 Agent 事件 `verbose`，用于长任务期间向最终用户发送一句有意义的等待提示。

- `progress / tool_start / tool_result / llm_call`：技术执行事件，继续默认隐藏。
- `verbose`：用户可见中间消息，例如“正在生成报价单，这可能需要一点时间，请耐心等待”。
- `response`：最终回复，现有语义不变。

MVP 明确收敛为：**每轮最多一条 verbose**。

原因：一条开始提示已经解决长时间静默问题，同时避开渠道回复额度、限流、多消息乱序和异步 dispatcher 竞态。多阶段提示、ETA、工具内部阶段上报、子智能体内部透传均不进入 MVP。

关键约束：

1. verbose 文案只来自 Tool/Skill 的确定性策略（Skill metadata / Tool get_user_feedback / delegate 固定文案），LLM 不生成、选择或改写文案；无系统兜底（2026-09-01 产品决策：system watchdog 已删除，任何无策略 turn 都不产生 verbose）。
2. Agent 编排层在有效 tool calls 解析完成后判定长任务；不修改 LLM system prompt，也不读取 tool-call assistant content 作为 verbose。
3. verbose 不进入 Agent messages，不新增独立 `chat_messages/channel_messages` 行，只附着在最终 assistant 的 `metadata.verboseMessages`。
4. verbose 发送失败为 best-effort，不能改变工具执行或最终回复。
5. verbose 不调用 `session_queue.mark_responding`，不改变现有 cancel/merge/pending 语义。
6. 第三方渠道一轮最多一条纯文本提示；微信客服硬上限也是一条。
7. 默认全局启用（2026-09-01 产品决策，替代原"首版部署全局默认关闭"的灰度策略）；`force_disabled=true` 仍可一键全局紧急关闭。

## 2. 目标与非目标

### 2.1 目标

- `travel-quote`、Excel 模板生成、文档生成、子智能体委派等长任务在最终结果前给出等待提示。
- Web、企微、微信客服、钉钉、飞书、企微个人 RPA 共享同一事件和策略。
- 短问答、短工具及任何无策略命中的 turn（无论多慢）都不产生额外提示；无系统兜底（2026-09-01 产品决策删除 system watchdog）。
- 中间提示不进入下一轮 LLM 上下文。
- 被 session queue 合并的 follower 不误发提示。
- 能追踪提示来源、首条耗时和渠道送达结果。

### 2.2 非目标

- 不做 token 级 LLM 流式输出。
- 不显示工具名、参数、命令、路径、reasoning 或内部异常。
- MVP 不做多阶段提示、百分比和数字 ETA。
- MVP 不做 skill 子进程内部实时阶段协议。
- MVP 不做钉钉/飞书卡片原地更新。
- 不重构 session queue 的 pending 模型。
- 不引入新队列中间件或新数据表。

## 3. 当前架构与已验证事实

### 3.1 Web

```text
POST /api/chat/stream
  -> src/main.py:event_generator
  -> Agent.process_message（async generator）
  -> make_event
  -> SSE
  -> frontend/web/api/agent.ts
  -> useAgent.ts 会话级状态
  -> MessageItem.vue
```

- SSE 已原样发送 Agent 事件。
- `useAgent.ts` 已按 session 隔离执行状态。
- `MessageItem.vue` 在 assistant 正文为空时已有“对方正在输入中”占位。
- 最终 assistant 已通过 metadata 保存 `progressMessages`。
- 当前带 tool calls 的 assistant content 会进入内存工具上下文，但 DB 持久化时被清空；本功能不得读取、改写或复用该 content，verbose 与它完全无关。

### 3.2 第三方渠道

```text
渠道回调 ACK
  -> 后台 route
  -> ChannelSessionManager.process_and_persist
  -> session_queue.enqueue_and_process
  -> Agent.process_message_sync(progress_callback=...)
  -> 事务持久化
  -> send_response
```

- `process_message_sync` 已把所有 Agent 事件传给 `progress_callback`。
- 渠道 callback 当前只消费文件和 tool 消息，尚未发送用户提示。
- `make_send_response` 已统一 `UnifiedResponse -> adapter.send_message`。
- 微信客服已有 route 外层 `_process_with_waiting_indicator`，但仅覆盖该渠道。

### 3.3 两个必须避免的生产陷阱

#### 陷阱 A：不能在 verbose 后调用 `mark_responding`

当前 session queue 的 responding/pending 语义是为“最终内容已经开始流式推送”设计的：

- 新消息进入 pending 后，旧 owner 会在同一个 `enqueue_and_process` 内继续执行 pending。
- pending 结果会覆盖原 `current.response_text`。
- `process_and_persist` 最终只持久化、发送最后一个返回结果。

如果只发了一条 verbose 就 `mark_responding`，用户此时追加 B：A 的最终结果可能被 B 覆盖而永不发送。并且 responding TTL 仅 10 秒，不适合作为长任务可见状态。

因此 MVP **完全不调用 `mark_responding`**。verbose 只表示“当前正在处理”；用户追加消息时保持现有 cancel/merge 重算语义。若未来要求“A final 必须先发送，再处理 B”，必须单独重构 session queue，不能夹带在本功能中。

#### 陷阱 B：企微个人 RPA 不使用 UnifiedResponse.message_id 做幂等

RPA 实际 outbox 幂等键来自 `set_reply_context(request_id=...)`。当前 pre_send 每次都写原始 `env.event_id`。如果 verbose 和 final 共用该值，后发消息会被去重。

因此所有出站发送需要唯一 `delivery_id`：

- verbose：`{event_id}:verbose:1`
- final：`{event_id}:final`

RPA 的 pre_send 必须把这个 delivery_id 写入 `set_reply_context(request_id=...)`。不能只修改 `UnifiedResponse.message_id`。

## 4. 事件协议

MVP 使用最小事件结构：

```json
{
  "type": "verbose",
  "eventId": "verbose_01J...",
  "data": "正在生成报价单，这可能需要一点时间，请耐心等待。",
  "source": "policy",
  "timestamp": 1788144000000
}
```

| 字段 | 约束 |
|---|---|
| `type` | 固定 `verbose` |
| `eventId` | 本轮唯一 |
| `data` | 单句、无换行、1～60 个字符 |
| `source` | `policy / system` |
| `timestamp` | `make_event` 毫秒时间戳 |

MVP 不包含 `phase`、`etaSeconds`、百分比或内部 execution id。未来扩展这些字段时必须保持可选和向后兼容。

## 5. 长任务判定：唯一解析契约

新增唯一入口：

```python
resolve_feedback_policy(
    tool_name: str,
    tool_args: dict,
    agent: Agent,
) -> Optional[LongRunningFeedback]
```

返回结构：

```python
@dataclass(frozen=True)
class LongRunningFeedback:
    start_message: str
```

解析顺序固定：

1. `skill_execute`：按 `tool_args["skill"]` 查询当前 Agent `skill_registry` 中该 Skill 的 `metadata.user_feedback`。
2. `delegate_to_subagent`：固定视为长任务，使用审核文案“正在交由专业数字员工处理，请耐心等待”。
3. 普通工具：调用可选 `tool.get_user_feedback(tool_args)`，允许 Excel 按 `task=fill_template` 动态判断。
4. 未命中返回 `None`，该 tool call 不产生 verbose。

解析必须发生在 `valid_tool_calls` 完成后、首个 `tool_start` 前。若一次 LLM 响应有多个 tool call，只要至少一个解析为长任务，本轮就有 feedback policy；采用第一个长任务的策略，不拼接多条文案。

端到端路径固定为：

```text
LLM 返回 tool_calls
  → Agent 校验得到 valid_tool_calls
  → resolve_feedback_policy(tool_name, args)
      ├─ 命中：make_verbose_event(source="policy") → Web/渠道旁路消费
      └─ 未命中：不产生业务提示
  → tool_start → tool execution
```

只有这一条路径，只写事件流，不写 Agent messages。（2026-09-01 产品决策：原「turn 超过 8 秒且无用户事件 → source="system" 兜底」分支已删除。）

普通工具默认：

```python
def get_user_feedback(self, tool_args: dict) -> Optional[LongRunningFeedback]:
    return None
```

Skill 使用现有 metadata：

```yaml
metadata:
  version: "2.0.0"
  user_feedback:
    long_running: true
    start_message: "正在生成报价单，这可能需要一点时间，请耐心等待。"
```

Skill metadata 只是策略来源，不天然可信。内置和租户自定义 Skill 的 `start_message` 都必须通过统一的 1～60 字、单句、路径/命令/敏感键检查；不合规时整体降级 fallback 文案。审核流程不能替代运行时校验。

首批策略：

- `travel-quote`：长任务（Skill metadata 定义）。
- Excel 工具仅 `fill_template` 的 AI 分析路径为长任务（工具 `get_user_feedback` 定义）；read/list/简单 export 不标记。（2026-09-01 用户裁决：excel-to-template 场景实际执行走 Excel 工具，提示语只定义在工具层，Skill metadata 不重复定义。）
- `delegate_to_subagent`：长任务，但 MVP 只显示父层开始提示，不透传子智能体内部阶段。
- Word/PPT/PDF 只登记真实稳定超过阈值的生成路径，不对整个工具类一刀切。

## 6. 内容来源与安全规则

### 6.1 确定性策略文案

业务 verbose 只允许来自：

- Skill registry 中独立保存的 `metadata.user_feedback.start_message`。
- 普通 Tool 的进程内 `get_user_feedback(tool_args)` 返回值。
- delegate 的代码级固定模板。

LLM 不参与 verbose：不增加相关 system prompt，不要求模型在 tool-call content 中说明等待，不读取或改写 assistant content。这样不同模型/provider 的 tool-call content 行为不会影响用户提示。

所有策略文案在产生事件前统一校验：单句、无换行、1～60 字，不含代码块、HTML、JSON、路径、命令、敏感键、数字 ETA 或百分比。不合规时整体替换为 fallback 文案。（实现裁决 2026-08-31：违规模板降级后的 fallback 文案仍记 `source="policy"`——触发原因是业务长任务策略，仅文案被整体替换。2026-09-01 产品决策：watchdog 删除后 `source="system"` 运行期不再产生，事件工厂白名单仅向后兼容保留。）

### 6.2 Agent loop 上下文隔离

- Skill loader 将 `metadata.user_feedback` 保存在编排侧 registry 字段中，不渲染进 Skill 正文或 Agent system prompt。
- Tool 的 `get_user_feedback` 是服务端方法，不进入 tool schema，不向 LLM 暴露。
- `verbose` 事件由 wrapper/编排层旁路产生，不执行 `messages.append(...)`。
- tool-call assistant content 保持原有语义，本功能既不从中提取 verbose，也不向其中写 verbose。
- `metadata.verboseMessages` 仅供 UI、渠道审计和历史展示，`_build_messages()` 必须忽略。
- 下一轮以及同一轮后续 LLM 调用均看不到 verbose 文案。

### 6.3 包装器与系统兜底的移除（2026-09-01 产品决策记录）

> 决策：**system watchdog（8 秒兜底）已彻底删除**。用户（产品负责人）2026-09-01 拍板："system watchdog 直接删除不需要，不需要它兜底，所有文案都在工具或者 skill 中指定。"

原因与影响：

- 兜底文案与业务策略文案并存会造成语义冲突（用户可能先收到通用兜底、再收到业务提示前的重复等待语），且兜底无法表达业务语义；产品选择「无策略就不提示」的确定性体验。
- verbose 唯一来源是策略（§5 端到端路径）：无策略命中的 turn（无论多慢，包括慢首轮 LLM）不产生任何 verbose。
- `fallback_message` 配置保留，仅作 policy 文案违规时的整体降级模板（仍记 `source="policy"`）；原 watchdog 的 `source="system"` 发射路径、绝对 deadline 计时、`watchdog_race_lost` 等抑制原因全部移除。
- 事件工厂 `make_verbose_event` 的 source 白名单 `"policy" | "system"` 向后兼容保留，运行期只产生 `"policy"`。

wrapper 保留为事件过滤/观测出口（实现为 Agent 原始事件流外的 async iterator）：

```text
Agent.process_message
  -> iter_with_verbose_feedback(surface, config, observer)
  -> Web SSE / process_message_sync
```

确定性要求：

- producer task 负责消费原始事件并写内部 queue；wrapper 自身不产生任何 verbose，只透传。
- 「非 state.event 本体的 verbose 一律丢弃」：迟到重复/伪造事件被防御性拦截，每轮最多一条不依赖 producer 自觉。
- 终态事件（response / clarification / browser_human_required 等）到达即置 `response_started`，其后 state 拒绝再发。
- 被 session queue 合并的 follower 没有进入 Agent producer，因此不会误发。
- 正常、异常、取消都必须 cancel/await producer，不能遗留 task。
- `process_message_sync` 只有存在用户投递 callback 时才启用 wrapper；scheduler 等无用户表面不受影响。
- 包装入口显式接收 `surface="web" | "channel"` 和本轮配置。

## 7. 每轮状态

```text
PENDING
  | 合法 policy verbose（唯一触发源；watchdog 已删除）
  v
EMITTED
  | response / clarification / human-required / complete / error / cancel
  v
CLOSED
```

MVP 状态字段：

- `event: Optional[dict]`
- `emitted_at`
- `response_started`
- `closed`

一旦 `event` 非空，本轮拒绝任何后续 verbose。这里的“本轮”是一次 `enqueue_and_process/process_and_persist` 的 **owner 生命周期**，不是单次 `_processor` attempt：

- `VerboseFeedbackState` 与渠道 dispatcher 在 owner 生命周期创建一次并由所有重跑 attempt 复用。
- `process_and_persist` 创建该 state，并显式传给每次 `process_message_sync(..., feedback_state=state)`；sync 方法在收到外部 state 时禁止另建。
- session queue cancel/merge 导致 `_processor` 重跑时，文件/tool 临时结果可按现有逻辑清空，但 `event/emitted/sent` 状态不得清空。
- 因而 `A → verbose → 追加 B → A+B 重跑` 仍累计最多一条提示；旧 attempt 只写 trace，不再次面向用户发送。
- merged follower 不执行 owner `_processor`，dispatcher 必须惰性启动，不能仅因 follower 入队创建后台 task。

## 8. Web 实现

### 8.1 后端

- `/api/chat/stream` 使用 `process_message_with_feedback(surface="web")`。
- SSE 原样发送 `verbose`。
- `verbose` 单独收集到 `verbose_events`，不加入 `progress_events`。
- 最终 assistant metadata 增加 `verboseMessages`。
- 兼容 WebSocket/continuation 入口只透传原生 verbose；MVP 不为 browser continuation 新开 wrapper，避免扩大后台恢复语义。

### 8.2 前端

- 新增 `VerboseMessage` 类型和 `ChatMessage.verboseMessages`。
- `agent.ts` 增加 `onVerbose`。
- `useAgent.ts` 按 session 保存本轮最多一条 verbose。
- assistant 正文为空且处理中时，用 verbose 替换“对方正在输入中……”。
- response 开始后隐藏 verbose 状态行。
- verbose 不进入 debug 执行详情。
- 使用 `aria-live="polite"`。
- DB 历史可恢复 metadata，但默认不在完成消息下展开。

## 9. 第三方渠道 dispatcher

### 9.1 为什么不能在 callback 里直接发送

`process_message_sync` 会同步 `await progress_callback(event)`。如果 callback 直接等待 adapter 网络请求，工具启动会被渠道发送延迟最多 5 秒，也难以保证 final 与迟发提示的顺序。

### 9.2 确定性算法

每个 owner 生命周期至多惰性创建一个 `ChannelVerboseDispatcher`，所有 `_processor` 重跑 attempt 复用它：

1. 容量为 1 的 `asyncio.Queue`。
2. Agent callback 只校验、记录并 `put_nowait`，不直接调用 adapter。
3. 独立 dispatcher task 串行发送唯一一条纯文本 verbose。
4. Agent 完成后执行 `close_accepting()`。
5. 构造 DB batch 前，`await dispatcher.drain(timeout)`，冻结 delivery 状态；超时必须 cancel 并 await 实际发送 task，不能只停止等待。
6. 持久化完成后发送 final；因此 final 不会早于在途 verbose。
7. 异常/取消路径执行 `close_accepting -> cancel_and_await`。
8. 只有 adapter 返回 `True` 才记录 `delivery="sent"`；区分 `emitted/sent/failed/timeout/suppressed`。

dispatcher 不调用 `mark_responding`，也不持有覆盖整个 Agent 执行期的普通 Lock。

### 9.3 发送接口

`process_and_persist` 增加：

```python
send_verbose: Optional[Callable[[dict, str], Awaitable[StatusDeliveryResult]]] = None
verbose_feedback_config: Optional[VerboseFeedbackConfig] = None
```

第二个参数是唯一 `delivery_id`。新增 `make_send_verbose`，调用 adapter 的低优先级 `send_status_message(..., reserve_for_final=1)`，只构造纯文本，不携带文件、图片或 Markdown 长图。不能直接调用普通 `send_message`，否则 verbose 可能占用内部限流器的最后一个额度并使 final 被拒绝。

`BaseChannelAdapter` 增加默认能力契约：

```python
async def send_status_message(
    self,
    message: UnifiedResponse,
    *,
    reserve_for_final: int = 1,
) -> StatusDeliveryResult: ...
```

- adapter 必须在同一次限流判定中确认发送后至少还剩 `reserve_for_final` 个额度；不能用“先查询剩余额度、再普通发送”的 TOCTOU 两步实现。
- Redis 限流器用 Lua/事务原子完成“校验预留 + 扣减”；进程内 deque 在不包含 `await` 的临界区完成。
- 额度不足返回 `suppressed_rate_limit`，不发送、不占额度；不支持安全预留的 adapter 默认 suppress，而不是降级调用 `send_message`。
- final 继续走高优先级普通发送路径；测试必须把限流器置于只剩一个额度的边界，证明 verbose 被抑制且 final 成功。

渠道限制：

- 所有第三方渠道 MVP 每轮最多一条。
- 微信客服单次咨询最多 5 次回复，由 owner 级 `WeComKfReplyBudget(total=5)` 同时约束 verbose 与 final；不能只依赖“最多一条”的口头约定。
- adapter 原有限流和重试仍生效；verbose 失败不由 dispatcher 重试。

微信客服的可执行降级规则固定为：

1. verbose 发送前必须为 final 预留至少 1 次平台回复；成功后 budget 从 5 变为 4，失败/抑制不扣减。
2. final adapter 接收同一 budget；最终正文优先并至少保留 1 次发送。
3. 正文能单条纯文本发送则单条发送；超长、含表格/复杂 Markdown 时优先合成为 1 张长图，失败则截断为 1 条安全纯文本并附“内容较长，请在 Web 端查看”。不得无预算地自动分段。
4. 正文成功后，图片/文件按原顺序使用剩余额度；文件下载链接优先合并进正文，无法合并或超预算的资产不再发送，记录 `suppressed_reply_budget`。
5. 若 adapter 在发送 verbose 前无法保证 final 至少一次正文投递，则 suppress verbose。

这套规则保证“提示是可丢的，最终正文不可被提示挤掉”；它可能在极端多附件场景少发资产，但不能只发提示而没有 final。

### 9.4 RPA 唯一投递 ID

`make_send_response/make_send_verbose` 的发送上下文必须接受本次 delivery_id。企微个人 RPA：

- verbose pre_send：`set_reply_context(request_id=f"{event_id}:verbose:1", ...)`
- final pre_send：`set_reply_context(request_id=f"{event_id}:final", ...)`

必须以 outbox `dedup_key` 断言写测试，不能只断言 `UnifiedResponse.message_id`。

## 10. 持久化与 metadata 合并

verbose 不单独写消息行。构造最终 assistant batch 前，dispatcher 已 drain，`verboseMessages` 的 delivery 状态被冻结。

合并规则固定：

1. 浅复制调用方 `assistant_metadata`，保留未知字段。
2. `downloadableFiles` 合并内部提取结果，按 `file_id` 去重；系统结果覆盖相同 file_id。
3. `verboseMessages` 合并内部事件，按 `eventId` 去重；系统结果覆盖相同 eventId。
4. 其他同名系统字段以系统生成值为准。

示例：

```json
{
  "verboseMessages": [
    {
      "eventId": "verbose_01J...",
      "data": "正在生成报价单，这可能需要一点时间，请耐心等待。",
      "source": "policy",
      "timestamp": 0,
      "delivery": "sent"
    }
  ]
}
```

Web 端没有 adapter delivery，记录 `delivery="displayed"`；若无法可靠知道 DOM 是否实际渲染，可记录 `delivery="streamed"`，命名在实现前由契约测试冻结。

`metadata.verboseMessages` 不参与 `_build_messages`；verbose 从不进入内存 messages，tool-call assistant content 继续保持现有独立语义，因此同一轮后续调用和下一轮上下文均不会看到 verbose。

## 11. 配置与灰度

新增正式 Pydantic 配置模型，不能继续依赖 `Settings.Config.extra="allow"` 的无类型 agent 字典：

```yaml
agent:
  reply_style: human-like
  verbose_feedback:
    force_disabled: false
    enabled: true
    max_text_chars: 60
    delivery_timeout_seconds: 5
    fallback_message: "正在处理你的请求，复杂任务可能需要一点时间，请耐心等待。"
```

（2026-09-01 产品决策：`initial_delay_seconds` 字段已删除——watchdog 移除后 delay 无运行时含义；`fallback_message` 仅作 policy 文案违规降级模板。）

`force_disabled=true` 是最高优先级全局 kill switch，任何请求级、渠道级或旧配置都不能覆盖。未强制关闭时，配置优先级固定：

1. 全局 `force_disabled=true`：立即关闭全部新旧 verbose 发送。
2. 请求级显式配置（测试/内部调用）。
3. 渠道 `config.verbose_feedback`。
4. 微信客服旧 `waiting_indicator` 兼容映射：`enabled=false` 或 `delay<=0` 视为显式关闭；delay 非法/缺失视为启用（无运行时含义）；`message` 映射为 `fallback_message`（2026-09-01 起 delay 不再映射任何运行时字段）。
5. 全局 `agent.verbose_feedback`。
6. 代码默认值。

kill switch 在 owner 创建时及 dispatcher 真正发送前各读取一次，因此能抑制尚未发送的在途提示；若当前部署不支持配置热加载，运维修改后必须重启服务。普通本轮配置仍在 owner 开始时冻结。

首版（2026-09-01 产品决策更新：全局默认启用，原"默认关闭灰度"策略废止）：

- 全局默认 `enabled=true`；紧急回滚仅靠 `force_disabled=true`。
- 渠道可按部署配置单独关闭（渠道级 `enabled=false` 覆盖全局默认）。
- 其他渠道若未实现配置 UI，先使用全局配置。
- `ChannelFactory._build_adapter` 可把非敏感的 verbose_feedback 子配置统一挂到 adapter，避免每个 adapter 构造器重复实现。
- 微信客服新配置缺失时优先使用旧 waiting_indicator，已配置租户行为不倒退。

## 12. 可观测性

wrapper 事件产生在原始 Agent generator 外，不能只修改 `TraceCollector.on_event`。

统一 wrapper 接收 `feedback_observer`，policy verbose 由 wrapper 调用 observer 记录；渠道 dispatcher 再回写 delivery outcome。至少记录：

- source。
- time-to-first。
- delivery outcome。
- suppressed reason。
- 文本长度，不默认记录全文。

可使用 `VerboseFeedbackState` 在结束时一次性合并到 trace metadata，避免跨 task 并发更新多次。

## 13. 独立审计问题闭环

| 审计问题 | 修订结果 |
|---|---|
| P0：mark_responding 导致 A final 被 pending B 覆盖 | MVP 禁止调用 mark_responding，不改变 queue 语义 |
| P0：RPA message_id 不参与真实幂等 | 定义 delivery_id，并写入 RPA reply context request_id |
| P1：tool-call content 上屏带来模型不确定性与上下文污染风险 | 删除 LLM 候选路径；只允许 Tool/Skill/System 确定性文案 |
| P1：长任务元数据无统一解析 | 冻结唯一 resolver、解析顺序和返回结构 |
| P1：callback 直接发送会阻塞/乱序 | 容量 1 dispatcher queue，final 前 drain |
| P1：微信客服 5 次额度 | owner 级 reply budget；正文优先，长内容折叠，资产按预算降级 |
| P1：配置默认和灰度矛盾 | 默认 false；冻结优先级、旧配置映射和全局 kill switch |
| P1：delivery 持久化竞态 | batch 前 drain，冻结状态；明确 metadata 合并 |
| P1：watchdog 不进 TraceCollector | wrapper observer / 结束时 trace metadata |
| 决策变更（2026-09-01）：system watchdog 兜底与产品语义冲突 | 彻底删除 watchdog 路径与 `initial_delay_seconds` 配置；verbose 仅由策略产生，详见 §6.3 |
| 复审阻断：重跑会突破单条上限 | 状态与 dispatcher 改为 owner 生命周期共享，attempt 重跑不重置 |
| 复审阻断：verbose 占掉 final 限流额度 | adapter 低优先级发送原子预留 final 额度，不支持预留则抑制 |
| 复审阻断：watchdog 范围矛盾 | 曾明确任何超过 8 秒的 turn 均可触发 system 兜底；该兜底已于 2026-09-01 随 watchdog 一并删除 |
| 复审问题：普通开关不能全局回滚 | 新增最高优先级 `force_disabled` kill switch |

## 14. 验收标准

1. Web 调用 `travel-quote` 时，在最终文件前看到一条业务化提示，不出现 tool/skill 名、命令或路径。
2. Excel `fill_template` AI 路径有提示，read/list/简单 export 无提示。
3. 短问答/短工具及任何无策略命中的 turn（无论多慢）不产生任何 verbose（无系统兜底，2026-09-01 产品决策）。
4. Tool/Skill 模板含换行、路径、JSON、敏感键、数字 ETA 时，展示 system fallback 而不是原文。
5. 每轮最多一条 verbose；response 开始后不再产生提示。
6. 渠道提示失败/超时不影响工具、final 和落库。
7. verbose 没有独立 chat/channel message 行，同一轮后续及下一轮 LLM messages 均不含其正文；Skill feedback metadata 也不进入 prompt。
8. session queue 合并 follower 不发提示；A 发提示后追加 B 时按现有 cancel/merge 重算，最终回复不丢。
9. 企微个人 RPA verbose/final 使用两个不同 outbox dedup key，两者均可送达，且 outbox 与直推路径均保持 verbose → final 顺序。
10. 微信客服共享 5 次 reply budget：verbose 后 final 正文至少成功一条，超预算资产按规则抑制并可观测。
11. dispatcher 在 DB batch 前完成 drain，metadata delivery 与真实 adapter 结果一致。
12. 无策略的慢 turn 中技术事件持续产生也不产生任何 verbose；策略 verbose 始终先于首个 tool_start。
13. 并发、异常、取消后无遗留 producer/dispatcher task，无 Redis 锁泄漏。
14. 旧微信客服 waiting_indicator 配置继续生效且不重复发送。
15. session queue cancel/merge 多次重跑仍累计最多一条 verbose。
16. 内部限流只剩一个额度时 verbose 被抑制、final 正常发送。
17. `force_disabled=true` 时请求、渠道和旧 waiting indicator 均不能启用发送。

## 15. 后续增强

- 子智能体内部 verbose 实时透传；父取消时显式调用 `SubagentExecutor.cancel`。
- in-process 工具 `report_verbose`。
- skill 子进程 stderr NDJSON 阶段协议。
- Web 多阶段状态；渠道仅在支持原地更新的卡片上启用多阶段。
- 基于真实 P50/P90 的可信 ETA。
- 若产品要求提示后禁止取消，单独重构 session queue 的 turn-visible/pending 交接，不复用 responding。
