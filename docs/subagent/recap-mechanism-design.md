# 子智能体 Recap 机制设计（轮后异步沉淀任务）

> 日期：2026-09-04
> 定位：子智能体通用的「轮后任务」运行机制；`external_push`（10605 推送）是第一个任务实例。
> 第一个实例的落地细节：[pre-sales/external-push-code-hook-design.md](./pre-sales/external-push-code-hook-design.md)
> 关联：subagents/*/SUBAGENT.md（配置载体）、src/channels/session.py（触发点）、.claude/rules/billing_audit.md

---

## 1. 背景与定位

### 1.1 问题

子智能体的 SUBAGENT.md 目前只有 `tools` / `skills` / `context` 等**对话期**配置，没有表达「每轮问答结束后要做的沉淀类动作」的位置。第一例是 pre-sales 向 10605 的每轮推送：用提示词驱动模型自行执行多步工具链，实测 qwen3.8-flash 与 DeepSeek-v4-flash 均不可靠（2026-09-04 trace tr_3e7123a2f23a41de / tr_d07191f6a8674932），且推送崩溃会连累主回复、推送耗时计入客户等待。

### 1.2 Recap 定义

**Recap = 每轮问答结束、回复送达之后，由代码异步执行的沉淀类任务。**

| 特征 | 约定 |
|------|------|
| 触发 | 事件驱动：每轮问答（用户一条消息 -> 回复送达）成功后，固定触发一次 |
| 时机 | 回复送达之后异步执行，**不阻塞、不延迟客户可见的回复** |
| 决策者 | **代码**，不是模型——模型不负责「要不要做、按什么顺序做」 |
| 模型参与 | 任务内部可以用 LLM 做单轮文本生成（如摘要），但必须是普通的单轮调用，不依赖模型自主发起工具链 |
| 失败 | 任务间故障隔离；单个任务失败不影响回复、不影响其他 recap 任务 |
| 与中长期记忆的边界 | recap 由**单次对话轮**触发；中长期记忆整理由 background_runner **周期**触发。两者调度、幂等、失败策略独立，不合并 |

### 1.3 命名说明

- 配置键与产品概念用 **recap**（复盘，取自 Claude Code 的 recap 隐喻，团队熟悉、易扩展）。
- 实现层术语为 **post-turn async task**（轮后异步任务），代码注释/类名用 `recap` 保持一致。
- 注意与 Claude Code 的「上下文压缩回顾（context compaction recap）」区分：本机制与上下文压缩无关。

### 1.4 与「回答必需的查询」的分工（不迁移到 recap）

查询类动作（查商品、查订单、查知识库）不查就答不了，模型遵守良好且必须发生在回复之前——**不属于 recap**，保留为 LLM 工具调用 + 未来高频意图代码预取（另行设计）。recap 只收编「事后做也不影响本次回答」的沉淀类动作。

---

## 2. 目标与非目标

**目标**：

1. SUBAGENT.md 声明式配置 recap 任务，新增任务 = 新增适配器文件 + SUBAGENT.md 一行
2. 全渠道统一收口：wecom_kf / dingtalk / feishu / web 任何一个渠道的轮次结束都能触发
3. 幂等、故障隔离、可观测由运行时统一提供，适配器只写业务逻辑
4. 适配器内 LLM 调用统一计费模式

**非目标**：

- 不做任务间编排/DAG（recap 任务彼此独立，无依赖关系）
- 不做跨进程持久化队列（进程崩溃丢任务可接受，见 §7 Q1）
- 不做管理后台可视化配置（先文件版，DB 化见 §7 Q2）

---

## 3. 配置规范（SUBAGENT.md frontmatter）

在 `context` 之后新增 `recap` 块：

```yaml
recap:
  tasks:
    - name: external_push        # 任务适配器注册名（tasks 注册表键，必填）
      when: every_round          # 触发时机，当前仅支持 every_round（必填）
      enabled: true              # 缺省 true
```

规则：

- 列表顺序即执行顺序（当前任务彼此独立，顺序仅影响调度先后）
- 未知 `name` 静默跳过 + logger.warning（旧配置兼容：子智能体没有 recap 块 = 无 recap 任务，行为与现状一致）
- frontmatter 解析沿用既有正则分割，**第二个 `---` 必须顶格**（architecture.md 已有陷阱警示），recap 块内禁止出现顶格 `---`

### 3.1 DB 覆盖智能体的限制（明确取舍）

`subagent_definitions` 表（DB 定义覆盖文件版）**没有** recap 对应字段——DB 覆盖的智能体读不到 recap 配置，等于无 recap 任务。当前 pre-sales 是文件版智能体，不受影响。DB 化扩展（加 jsonb 字段 + 管理后台编辑）独立排期，见 §7 Q2。

---

## 4. 架构设计

### 4.1 模块结构

```
src/services/recap/
├── __init__.py            # 导出 trigger_recap
├── runner.py              # 运行时：配置读取、条件校验、幂等、任务分发、故障隔离、汇总日志
└── tasks/
    ├── __init__.py        # 适配器注册表：RECAP_TASK_ADAPTERS = {"external_push": ExternalPush10605Adapter, ...}
    └── external_push_10605.py   # 第一个适配器（推送方案文档定义）
```

### 4.2 适配器契约

每个适配器实现统一接口（轻量协议，不引入抽象基类继承链）：

```python
class ExternalPush10605Adapter:
    name = "external_push"

    @staticmethod
    async def execute(payload: RecapPayload) -> None:
        ...
```

`RecapPayload`（dataclass，runner 构造）：

| 字段 | 说明 |
|------|------|
| `tenant_id` / `session_id` | 租户与会话 |
| `subagent_name` | 子智能体名（session_id 末段解析） |
| `round_message_id` | 本轮落库的 user message_id（幂等键组成部分） |
| `user_content` | 本轮用户消息（合并轮取 `merged_input`） |
| `assistant_reply` | 本轮回复文本 |
| `task_config` | 该任务在 SUBAGENT.md 的配置项（when/enabled 等，适配器自行解读） |
| `record_service` | 本轮 SessionRecordService（可能为 None；用于 LLM 计费累加判断） |

适配器约定：HTTP timeout 15s、内部串行、不嵌套 create_task、异常可上抛（runner 统一兜底）、业务留痕用 `tlog("<任务主题>", ...)`。

### 4.3 触发点（全渠道单一收口）

`src/channels/session.py` 的 `ChannelSessionManager.process_and_persist`，`send_ok == True` 时、`return` 之前：

```python
if send_ok:
    from src.services.recap import trigger_recap
    trigger_recap(
        agent=agent,
        session_id=session_id,
        tenant_id=tenant_id,
        user_content=result.merged_input or user_content,
        assistant_reply=response_text,
        round_message_id=user_message_id,   # 本轮批量写入产生的 user 消息 id
        record_service=record_service,
    )
```

要点：

- `trigger_recap` 内部 `asyncio.create_task`（自持引用 + done_callback 丢弃，仿 channel_routes.py `_dingtalk_background_tasks` 模式），外层本就运行在渠道回调的后台任务中
- **仅 `send_ok == True` 触发**：回复没送达的轮次视为失败轮，不做沉淀（避免给客户没收到答复的轮次生成跟进记录）
- `agent` 实例直接传入：recap 配置从 `agent.subagent_config.recap` 读取（文件版 SUBAGENT.md 已加载进该对象），无需二次查 registry
- `process_and_persist` 内部触发点位于 `mark_idle` / `finish_processing` 之后，不影响会话状态机

### 4.4 Runner 执行流程

```
def trigger_recap(agent, ...) -> None:
    tasks = 解析 agent.subagent_config.recap（无 recap 块 -> 直接返回）
    if not tasks: return
    payload 骨架构造（轻量，不查 DB——DB 采集是适配器自己的事）
    asyncio.create_task(_run_tasks(tasks, payload))

async def _run_tasks(tasks, payload):
    for task in tasks:                          # 串行分发，任务间故障隔离
        if 系统级开关(task.name) 为关: 跳过
        if not Redis SET NX recap_task:{tenant_id}:{task.name}:{round_message_id} TTL 24h:
            跳过（幂等：同轮重入 / 回调重放）
        try:
            adapter = RECAP_TASK_ADAPTERS.get(task.name)
            if adapter is None: logger.warning(...); continue
            tlog("recap", f"task={task.name} start, round={payload.round_message_id}")
            await adapter.execute(payload)
            tlog("recap", f"task={task.name} done")
        except Exception as e:
            logger.opt(exception=True).error(f"[recap] task={task.name} 失败（不影响对话）: {e}")
            # 不重试、不上抛——下一轮问答自然产生新的 recap
```

主日志每轮至多一条 INFO 汇总（任务列表 + 结果），过程留痕走 tlog 主题（如「recap」「售前推送」），避免主日志噪音。

### 4.5 幂等与防重

| 层 | 键 | TTL | 说明 |
|----|----|-----|------|
| 任务级 | `recap_task:{tenant_id}:{task_name}:{round_message_id}`（Redis SET NX） | 24h | round_message_id 为本轮 channel_messages.message_id，渠道无关、单调递增、天然防重放 |
| 任务内部缓存 | 适配器自管（如 `pre_sales_client_token:{tenant_id}:{mobile}` TTL 23h） | — | 属适配器实现，机制不感知 |

键前缀在 `src/core/cache_utils.py` `CacheKeys` 注册为 `RECAP_TASK_DEDUP`，同步 `docs/system/cache_usage.md`。Redis 不可用时降级内存（`redis_client` 内置降级），重启可能极小概率重做一次任务，业务可接受（推送类为追加型写入、查重类天然幂等）。

---

## 5. 适配器内 LLM 调用的统一约定（计费）

recap 适配器允许使用 LLM（摘要、评分、分类等），统一约束：

1. **调用形态**：`llm_gateway.chat()` 单轮、无工具、temperature ≤ 0.3、max_tokens 由适配器定（摘要类 300）
2. **计费**：紧邻调用处 `record_background_llm_usage(response.get("usage"), source="recap_<task_name>")`，满足 billing_audit.md §3.5 条件 A；函数内置双路径（有 record_service 上下文累加、无则独立落 `chat_records`，source_type=background_llm）
3. **降级**：LLM 失败/超时/输出解析失败 -> 适配器走各自的降级逻辑（如推送摘要降级为截断原文），**不得因 LLM 失败整体放弃任务**（除非该任务的价值本身就是 LLM 输出）

---

## 6. 第一个实例：external_push（10605 推送）

完整的业务规则、执行流程、测试计划见 [pre-sales/external-push-code-hook-design.md](./pre-sales/external-push-code-hook-design.md)。此处仅列其在机制中的挂接方式：

- SUBAGENT.md：`recap.tasks` 中声明 `- name: external_push`
- 适配器：`src/services/recap/tasks/external_push_10605.py`，`execute()` 内完成上下文采集 -> LLM 摘要（计费）-> 委托登录（复用技能脚本缓存）-> 10605 查重/建改/跟进记录
- 系统级开关：`external_push.pre_sales.enabled`（configs/config.yaml + settings.py 同步）

## 7. 未来任务示例（仅示意，不做提前实现）

| 任务名 | 触发 | 产物 |
|--------|------|------|
| `satisfaction_check` | every_round | LLM 判断本轮情绪信号，负向时写预警表/通知 |
| `customer_profile_sync` | every_round / on_new_info | 客户画像字段增量同步第三方 CRM |
| `lead_scoring` | on_lead_capture | 留资轮结束后 LLM 打分写 `bs_lead_capture_leads` |
| `conversation_digest` | N 轮累计 | 每 N 轮生成会话摘要写入 metadata，供下轮上下文压缩 |

新增一个任务的完整动作：`tasks/<name>.py` 实现适配器 + 注册表加一行 + 目标 SUBAGENT.md 加一行配置。

## 8. 实施步骤

| # | 内容 | 文件 |
|---|------|------|
| 1 | CacheKeys 注册 `RECAP_TASK_DEDUP` | src/core/cache_utils.py |
| 2 | recap 运行时（runner + 注册表 + RecapPayload，含单测：配置解析/幂等/隔离/未知任务名） | src/services/recap/ |
| 3 | process_and_persist 触发点接线 + 集成测试（send_ok 才触发、merged 轮取 merged_input、异常不影响返回值） | src/channels/session.py、tests/integration/ |
| 4 | （与推送方案联做）external_push_10605 适配器 | 推送方案文档 §6 |

## 9. 决策点

| # | 问题 | 当前倾向 |
|---|------|---------|
| Q1 | 进程崩溃丢失当轮 recap 任务 | 接受：下一轮问答自然产生新 recap；如需强可靠再加 Redis 队列 + background runner 消费（P2） |
| Q2 | recap 配置 DB 化（`subagent_definitions` 加 jsonb 字段 + 管理后台编辑） | 暂不做：pre-sales 为文件版智能体已满足；待出现第一个「DB 覆盖 + 需要 recap」的智能体时再加 |
| Q3 | recap 任务并发控制（同一会话连续快速多轮） | 不做专门控制：任务轻且幂等，乱序完成无业务影响；出现实际冲突再加 per-session 串行队列 |
