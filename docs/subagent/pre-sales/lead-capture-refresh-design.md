# 留资线索动态刷新设计（lead_refresh）

> **关联文档**：
> - [lead-capture-design.md](lead-capture-design.md) — 留资功能总体设计（#59）
> - [external-push-redesign-plan.md](external-push-redesign-plan.md) — 售前外部推送重构（#60）
> - [recap-mechanism-design.md](../recap-mechanism-design.md) — Recap 机制（#62）
>
> **状态**：设计完成，待开发。登记于 `docs/ideas.md` #64。

---

## 1. 背景与目标

售前咨询智能体（pre-sales，wecom_kf 渠道）已支持留资：客户触发留资后，`record_lead_capture` 工具在 `bs_lead_capture_leads` 写入一条线索，`demand_summary` 记录的是**留资时点**的需求快照。

留资后客户大概率继续交流——既可能与智能体继续对话，也可能经"转人工"与企微员工交流。这些消息均已准实时（1-2s）落入 `channel_messages`，但线索行不再更新，"外部接待客户 > 留资线索"页面无法反映客户最新状态。

**目标**：留资后，随对话进展持续刷新线索的三类信息：

| 信息 | 说明 |
|------|------|
| 客户意向度 | 高 / 中 / 低（基于最新交流由 LLM 判定） |
| 客户需求 | 客户有多方面需求时分条概括（列表） |
| 人工服务归属 | 最近一次转人工的企微员工（姓名 / id） |

**非目标（本期不做）**：
- 人工期对话推送到第三方系统（推送仍由 external_push 在智能体轮次承担；人工期推送列开放问题 §9.5，已于 2026-09-16 Phase 2 实现 `external_push_human`）
- 线索 stage 状态机变更（仍由现有工具 / 管理端 PATCH 维护）

---

## 2. 现状关键事实（设计依据）

| # | 事实 | 位置 |
|---|------|------|
| F1 | `bs_lead_capture_leads` 已有 `demand_summary`（留资快照）、`transferred_to`（**当前无人写入，可复用**）、`session_id`、`customer_user_id` 列 | `src/saas/db/tables.py:166-187` |
| F2 | `LeadCaptureDB` 仅有 `create` / `get_by_id` / `list_by_tenant` / `update_stage` / `delete` / `stats`，**无字段级分析回写方法** | `src/saas/db/lead_capture_db.py` |
| F3 | 留资时 `record_lead_capture` 工具在 `channel_sessions.metadata` 写入 `lead_capture={stage, lead_id, ...}`——这是"会话 → 线索"的现成关联键 | `src/tools/lead_capture/record_lead_capture.py` |
| F4 | recap 非定时任务：回复送达（send_ok）时 `trigger_recap` 构造 `RecapPayload` rpush 到 Redis `RECAP_QUEUE` → `background_runner` 独立进程轮询消费 → `_run_tasks` 按 `RECAP_TASK_ADAPTERS` 注册表分发，任务级异常吞掉 | `src/channels/session.py:1391`、`src/services/recap/runner.py:149/257`、`src/background_runner.py:82` |
| F5 | 幂等键 `recap_task:{tenant_id}:{task_name}:{round_message_id}` SET NX TTL 24h | `src/services/recap/runner.py:23` |
| F6 | payload 序列化携带 `task_config`，background 进程消费**不依赖 agent 实例**——意味着无需调起智能体也能触发 recap 任务 | `runner.py:198-204` |
| F7 | 有后台 LLM 分析先例：external_push 适配器 `chat_lite` + `record_background_llm_usage(source="pre_sales_push")` 计费 | `src/services/recap/tasks/external_push.py:472` |
| F8 | 人工期消息识别约定：客户消息 `metadata.source=customer_human` / `customer_ended`，员工消息 `metadata.source=servicer`（content 加 `[人工客服] ` 前缀），转接 marker `role=system` + `metadata.kind=transfer_to_human_marker` | `src/saas/api/channel_routes.py:2185+`、`src/tools/transfer_to_human.py:128/143` |
| F9 | 人工期客户消息有**统一落库收口** `_persist_kf_context_customer_message`（channel_routes 内 5 处调用均汇入） | `src/saas/api/channel_routes.py` |
| F10 | 转人工时员工身份（servicer_userid）当前只写入 `channel_sessions.metadata.transferred_to`，**未同步到线索表** | `transfer_to_human.py:128`、`channel_routes.py:3021` |
| F11 | 「新会话 / 清空会话」隐藏命令会物理删除非 converted 线索并移除 metadata.lead_capture（#63） | `src/core/hidden_commands.py` |

---

## 3. 总体架构

```
                    ┌─ 触发入口 A：智能体轮次 ─────────────────────────┐
                    │  process_and_persist send_ok → trigger_recap     │
                    │  （pre-sales recap.tasks 已含 lead_refresh）     │
客户消息 ──┤                                                      ├─► Redis RECAP_QUEUE
                    │                                                  │      │
                    └─ 触发入口 B：人工期客户消息 ────────────────────┘      ▼
                       _persist_kf_context_customer_message        background_runner 消费
                       落库后 enqueue_lead_refresh()（轻量入队，          │
                       不调起智能体，解决"人工期无 recap"缺口）           ▼
                                                              ┌─────────────────────┐
                                                              │ lead_refresh 适配器  │
                                                              │ 1 冷却检查（防抖）   │
                                                              │ 2 读 metadata.lead_ │
                                                              │   capture 定位线索  │
                                                              │ 3 采集中 channel_   │
                                                              │   messages 近 N 条  │
                                                              │ 4 chat_lite 分析    │
                                                              │ 5 回写线索行        │
                                                              └─────────────────────┘
```

**核心设计决策**：

| 决策 | 理由 |
|------|------|
| 复用 recap 框架新增适配器，**不在 channel 层写任何 lead_capture 业务逻辑** | 渠道层保持纯通道（#59 v2.0 四层解耦原则）；recap 自带队列 / 幂等 / 故障隔离 / 计费先例 |
| 触发与执行分离，两个入口都只做"轻量入队" | 入队是毫秒级 Redis 操作，不阻塞消息链路；人工期**无需调起智能体**（F6：background 消费只认 payload.task_config） |
| 分析读取**执行时刻**的 channel_messages 快照（非触发时 payload） | 防抖跳过的消息不丢失——下一条消息再触发时一并纳入；这是选择"拉模式"而非 external_push "推 payload"模式的关键原因 |
| 确定性数据（人工归属）事件驱动即时写，分析性数据（意向度 / 需求）LLM 异步写 | 转人工时点员工 id 是确定的，零 LLM 成本；意向度 / 需求需要语义分析 |
| 线索行写操作收敛在 `LeadCaptureDB` | 表的所有权不漂移，channel / recap 各层只调 DB 方法 |

---

## 4. 表结构变更

`bs_lead_capture_leads` 新增 6 列（`deploy/db_update.yaml` 增量 + `deploy/init-postgres.sql` + `src/saas/db/tables.py` 三处同步）：

```sql
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS intent_level TEXT;             -- 客户意向度：high/medium/low
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS intent_reason TEXT;            -- 意向度判定依据（一句话，供运营理解）
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS demand_points JSONB;           -- 客户需求分条（字符串数组）
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS servicer_name TEXT;            -- 最近一次转人工的企微员工姓名
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS last_human_transfer_at TIMESTAMP; -- 最近一次转人工时间
ALTER TABLE bs_lead_capture_leads ADD COLUMN IF NOT EXISTS last_analyzed_message_id TEXT;    -- 分析游标：最后参与分析的消息 id（Phase 2 增量用，本期一并建列）
```

**字段语义说明**：

| 字段 | 写入方 | 说明 |
|------|--------|------|
| `intent_level` / `intent_reason` / `demand_points` | lead_refresh 适配器 | LLM 分析产出；枚举值以 `src/saas/models/enums.py` 新增 `LeadIntentLevel` 为准（high/medium/low） |
| `transferred_to`（**复用现有列**） | `transfer_to_human` 工具 | 存 servicer_userid；现状无人写入（F10），本次赋予明确语义 |
| `servicer_name` | `transfer_to_human` 工具 | 员工姓名；数据源不保证可得时为空（§9.2） |
| `demand_summary` | **保持不变** | 仍是留资时点原始快照，前端可新旧对照；分析结果不覆盖它 |
| `last_analyzed_message_id` | lead_refresh 适配器 | 本期仅记录不参与增量裁剪，为 Phase 2 预留 |

---

## 5. 详细设计

### 5.1 触发入口 A：智能体轮次（复用现有钩子，改动 0 行代码）

`process_and_persist` 在 send_ok 时已调用 `trigger_recap`（`session.py:1394`），只需在 `subagents/pre-sales/SUBAGENT.md` 的 `recap.tasks` 增加：

```yaml
recap:
  tasks:
    - name: lead_refresh
      when: every_round
```

适配器内部发现 `session.metadata.lead_capture` 不存在（未留资）时静默 no-op，因此该任务对所有轮次安全。

### 5.2 触发入口 B：人工期客户消息（解决"转人工后无 recap"缺口）

`src/services/recap/runner.py` 新增轻量入队函数：

```python
def enqueue_lead_refresh(tenant_id: str, session_id: str, round_message_id: Any) -> None:
    """人工期消息落库后的线索刷新入队（不依赖 agent 实例，异常吞掉不阻断消息链路）"""
    # 构造最小 RecapPayload：task_config=[{name: "lead_refresh", when: "every_round"}]
    # user_content / assistant_reply 留空——lead_refresh 是拉模式，执行时自采 DB
    # subagent_name 从 session_id 解析（复用 _subagent_name_from_session）
    # rpush RECAP_QUEUE；Redis 不可用时直接放弃（下一条消息会再触发，无需进程内降级）
```

`_persist_kf_context_customer_message` 落库成功后 fire-and-forget 调用（`asyncio.create_task` 包裹同步函数 + 异常吞掉），单点改动覆盖全部 5 处调用。

**范围说明**：转人工目前仅 wecom_kf 渠道支持（`get_kf_context` 渠道隔离），入口 B 仅落在 wecom_kf 处理分支；其他渠道转人工上线后按同模式接入。

**幂等说明**：入口 B 每条客户消息的 message_id 不同，`recap_task:{tenant}:lead_refresh:{message_id}` SET NX 恒放行——真正的频控不在幂等键，而在适配器的冷却检查（§5.3.1）。

### 5.3 lead_refresh 适配器（`src/services/recap/tasks/lead_refresh.py`）

#### 5.3.1 冷却检查（防抖，成本控制核心）

```
key: lead_refresh_cooldown:{tenant_id}:{lead_id}
操作: SET NX EX 300   （5 分钟冷却）
```

- 占坑成功 → 执行分析；占坑失败（冷却中）→ 本次跳过
- **跳过不丢数据**：分析读执行时刻的消息快照，被跳过期间的消息由下一次触发（冷却到期后客户任一消息再触发）一并覆盖
- LLM 调用或回写失败 → 删除占坑键，允许下一条消息重试

#### 5.3.2 执行流程

```
1. 读 channel_sessions.metadata.lead_capture → 无则 no-op 返回
2. LeadCaptureDB.get_by_id(lead_id, tenant_id) → 线索不存在（已被隐藏命令删除 / converted）则 no-op
3. 冷却占坑（§5.3.1）
4. 采集消息：channel_messages 按 session_id 升序取最近 50 条
   role IN ('user', 'assistant')，排除 is_recalled；
   人工期消息的 metadata.source（customer_human / servicer / customer_ended）
   转换成"[人工接待]"阶段标记拼入分析输入
5. 构造分析输入：线索既有信息（demand_summary / stage / contact_name）+ 消息序列
   + 上次 demand_points（供分条对比与合并）
6. await llm_gateway.chat(...) 要求 JSON 输出：
   { "intent_level": "high|medium|low",
     "intent_reason": "...",
     "demand_points": ["...", "..."] }
7. 解析失败 → tlog 留痕 + 保留旧值直接返回（删占坑键）
8. LeadCaptureDB.update_analysis(lead_id, tenant_id, intent_level, intent_reason,
   demand_points, last_analyzed_message_id=本批最大 message_id)
   同步 DB 调用经 asyncio.to_thread 包裹
9. tlog("lead_refresh", ...) 记录前后对比（意向度变化、需求条数变化）
```

#### 5.3.3 计费（billing_audit §4.1）

```python
from src.services.session_record import record_background_llm_usage
record_background_llm_usage(response.get("usage"), source="lead_refresh")
```

background_runner 进程内执行，遵守 runner 既有约定（`runner.py:266` 已清理 SessionRecord ContextVar），`record_background_llm_usage` 无 record 时自动降级独立落 `chat_records`（source_type=background_llm）。

#### 5.3.4 判定标准（一期固定）

意向度判定标准一期写死在适配器 system prompt（通用标准：明确购买信号 / 主动要报价与周期 → high；持续追问产品细节 → medium；仅寒暄或已明确拒绝 → low）。租户级自定义判定标准列为开放问题 §9.4。

### 5.4 人工服务归属回写（`src/tools/transfer_to_human.py`）

转人工成功（transfer marker 写入后）追加：

```
若 session.metadata.lead_capture 存在且线索未被删除：
  asyncio.to_thread(LeadCaptureDB.update_transfer_info,
                    lead_id, tenant_id, servicer_userid, servicer_name)
全程 try/except，失败仅 tlog 留痕，不影响转接主流程
```

多次转人工时**最后写赢**（字段语义即"最近一次"）。与适配器写入的字段完全不重叠，无并发冲突。

### 5.5 `LeadCaptureDB` 新增方法（`src/saas/db/lead_capture_db.py`）

```python
@staticmethod
def update_analysis(lead_id, tenant_id, intent_level, intent_reason,
                    demand_points, last_analyzed_message_id) -> bool
    # UPDATE 指定字段 + updated_at=CURRENT_TIMESTAMP，返回是否命中行

@staticmethod
def update_transfer_info(lead_id, tenant_id, transferred_to, servicer_name) -> bool
    # 同步写 transferred_to / servicer_name / last_human_transfer_at=CURRENT_TIMESTAMP
```

仿照现有 `update_stage`（L199）实现风格，均带 tenant_id 过滤。

---

## 6. 边界与异常

| 场景 | 行为 |
|------|------|
| 未留资的会话（metadata.lead_capture 缺失） | 适配器 no-op，入口 B 照常入队但消费即退出（成本 ≈ 0） |
| 线索已被「新会话」命令删除（#63，非 converted 物理删除） | get_by_id 未命中 → no-op |
| 线索 converted | 仍刷新（converted 客户继续交流同样有价值），是否停止刷新列开放问题 §9.6 |
| LLM 输出非法 JSON / 字段越界 | 保留旧值，tlog 留痕，删占坑键待重试 |
| intent_level 非法值 | 校验后才入库，非法值视为解析失败 |
| Redis 不可用（入口 B） | 放弃入队不降级（下一条消息再触发）；入口 A 沿用 runner 既有进程内降级 |
| 多 worker 并发 | background_runner 单进程消费无并发写；同一 lead 的两个排队任务被冷却键 + 幂等键双重收敛 |
| 消息被「清空会话」软删（#63 之前的历史行为） | 采集 SQL 排除 is_recalled / status 异常行 |

---

## 7. 改动清单

| # | 文件 | 改动 |
|---|------|------|
| 1 | `deploy/db_update.yaml` | 新增增量块（6 列 ALTER，幂等） |
| 2 | `deploy/init-postgres.sql` | CREATE TABLE 同步 6 列 |
| 3 | `src/saas/db/tables.py` | CREATE TABLE 同步 6 列 |
| 4 | `src/saas/models/enums.py` | 新增 `LeadIntentLevel` 枚举 |
| 5 | `src/saas/db/lead_capture_db.py` | 新增 `update_analysis` / `update_transfer_info` |
| 6 | `src/services/recap/tasks/lead_refresh.py` | **新文件**：适配器（冷却 / 采集 / 分析 / 回写 / 计费） |
| 7 | `src/services/recap/tasks/__init__.py` | `RECAP_TASK_ADAPTERS` 注册 `lead_refresh` |
| 8 | `src/services/recap/runner.py` | 新增 `enqueue_lead_refresh()` |
| 9 | `src/saas/api/channel_routes.py` | `_persist_kf_context_customer_message` 落库后触发入口 B |
| 10 | `src/tools/transfer_to_human.py` | 转接成功回写线索人工归属 |
| 11 | `subagents/pre-sales/SUBAGENT.md` | `recap.tasks` 增加 lead_refresh |
| 12 | `frontend/web/components/`（留资线索 Tab） | 详情弹框展示：意向度徽章（BaseBadge）+ 需求分条列表 + 人工归属；列表页加意向度列 |
| 13 | `docs/ideas.md` | 登记 #64 |

**测试**（`tests/unit/` + `tests/integration/`）：
- 适配器单测：未留资 no-op / 冷却跳过 / 正常回写 / JSON 解析失败保旧值 / converted 处理
- `LeadCaptureDB` 新方法单测
- 入口 B：mock `_persist_kf_context_customer_message` 后断言 enqueue 被调
- 转人工回写：lead_capture 缺失时零副作用

---

## 8. 通用性与扩展

- **跨智能体**：适配器只认 `session.metadata.lead_capture`（F3，任何智能体经 `record_lead_capture` 留资都会写入），对智能体身份零感知。其他智能体（如旅游咨询）启用本功能 = 在其 SUBAGENT.md `recap.tasks` 加一行 + 渠道侧人工期触发按需接入。
- **跨渠道**：入口 A 天然全渠道（recap 钩子是全渠道单一收口）；入口 B 一期仅 wecom_kf，其他渠道转人工上线后接入。
- **不影响现有 recap 任务**：新适配器独立注册，external_push 行为不变。

## 9. 开放问题

| # | 问题 | 一期处理 | 备注 |
|---|------|---------|------|
| 9.1 | demand_points 全量重算 vs 增量合并 | 近 50 条窗口全量 + 参考 old demand_points 让 LLM 合并去重 | Phase 2 用 `last_analyzed_message_id` 游标做真增量 |
| 9.2 | servicer_name 数据源 | 尽力而为：从租户客服账号配置（kf_account）按 userid 映射，映射不到为空 | 若无配置数据源，列长期为 NULL 不阻塞功能 |
| 9.3 | 人工期消息混入分析窗口的噪音 | 人工期消息带"[人工客服]"前缀 / 阶段标记，prompt 中说明区分 | 实测后再调 prompt |
| 9.4 | 意向度判定标准租户级可配置 | 一期固定通用标准写死适配器 prompt | 若运营反馈口径不一，二期仿 pre-sales-api.md 模式下沉租户配置 |
| 9.5 | 人工期对话是否推送第三方系统 | **Phase 2 已实现（2026-09-16）**：新适配器 `external_push_human`，节流推送（同 lead_refresh 冷却模式，5 分钟冷却按 session 维度）+ 推送时同步客户表转人工字段（zhuanrengongshijian / rengongkefuxingming，多次转人工记录最新）与跟进汇总摘要（genjinhuizongzhaiyao / zhuangtai）+ 不要求留资；入口 B `enqueue_lead_refresh` 更名 `enqueue_human_period_tasks`，task_config 含 [lead_refresh, external_push_human] 两任务；推送契约见 10605 文档 §12.6 | — |
| 9.6 | converted 线索是否继续刷新 | 继续刷新 | 若产品认为成单后无需跟踪，加一行过滤即可 |

## 10. 分期

- **Phase 1（本次）**：§4 DDL → §5 全部后端改动 → 测试 → 前端展示 → 部署真机验证
- **Phase 2（可选，视运营反馈）**：增量游标裁剪、servicer_name 补齐数据源、人工期对话推送第三方（✅ 2026-09-16 已实现，见 §9.5）、租户级判定标准
