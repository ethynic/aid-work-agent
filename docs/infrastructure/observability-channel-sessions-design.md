# 追踪查看页面整合 channel_sessions — 方案设计文档

> 关联设计：[可观测性与质量保障设计](./observability-design.md)
> 关联开发计划：[可观测性与质量保障开发计划](./observability-dev-plan.md)
> 关联调研：[渠道集成设计](../channel/feishu-dingtalk/channel_integration.md)、[微信客服设计](../channel/wecom_kf/wecom_kf_design.md)
> 设计日期：2026-06-15
> 状态：✅ 已落地（A/B/C/E 阶段完成，D 阶段端到端验证待补，2026-07-07）

---

## 一、背景与问题

### 1.1 现状

`/portal/monitoring` 追踪查看页面（`TraceBrowser.vue`）以 session 为基本单位展示对话全链路。后端 API `GET /api/monitor/sessions`（`src/api/monitor.py`）通过聚合 `obs_traces` 表的 `session_id` 维度生成会话列表。

**问题**：当前页面仅能看到走 Web SSE 聊天路径的会话（来自 `chat_sessions`），看不到第三方渠道的会话（含微信客服 wecom_kf、企微会话 wecom、钉钉 dingtalk、飞书 feishu）——后者存储在 `channel_sessions` 表中。

### 1.2 根本原因

| 调用路径 | 入口 | 是否接入 TraceCollector |
|---------|------|----------------------|
| Web SSE 聊天 | `main.py:1523` `agent.process_message()` | ✅ 接入（在 `main.py:event_generator` 中旁路收集） |
| 企微会话（wecom） | `channel_routes.py:417` → `process_message_sync()` | ❌ 未接入 |
| **微信客服（wecom_kf）** ⭐ 实际在用 | `channel_routes.py:1409` → `process_message_sync()` | ❌ 未接入 |
| 钉钉/飞书 | `channel_routes.py:562` → `process_message_sync()` | ❌ 未接入 |
| 定时任务 | `scheduler/executor.py:133` → `process_message_sync()` | ❌ 未接入 |

所有调用路径最终都汇聚到 `Agent.process_message()`（`src/core/agent.py:1401`），这是唯一的请求处理入口。

### 1.3 三条关键事实（让"零渠道改造"成为可能）

调研中发现三条隐藏事实，使方案 C 得以成立：

#### 事实 1：所有渠道调用方都已通过 `record_service` 标识了来源

`SessionRecordManager.start_record()` 已经携带 `source_type` 参数（`src/services/session_record.py:90`）。各渠道调用方**早就传了**：

| 调用方 | 代码位置 | source_type |
|--------|---------|------------|
| Web 聊天 | `main.py:1481-1486` | 默认 `'chat'` |
| 企微会话 | `channel_routes.py:388-394` | `channel_type`（如 `"wecom"`） |
| 租户企微 | `channel_routes.py:530-536` | `"wecom"` |
| **微信客服** ⭐ | `channel_routes.py:1378-1384` | `"wecom_kf"` |
| 钉钉/飞书 | `channel_routes.py:530` 附近 | `channel_type`（`"dingtalk"`/`"feishu"`） |

#### 事实 2：`record_service` 携带 trace 所需的全部上下文

`SessionRecordService` 实例的属性：`session_id` / `tenant_id` / `user_id` / `user_message` / `source_type` / token 用量 / model / provider 等。**这正是 TraceCollector 需要的所有字段**。

#### 事实 3：Agent 内部已经能直接拿到 record_service

`src/core/agent.py:1840` 已有现成代码：

```python
_record = getattr(self, '_explicit_record_service', None) \
          or SessionRecordManager.get_current_record()
```

`_explicit_record_service` 在 `process_message_sync:2452` 中被赋值（所有非 Web 路径走这条）；`SessionRecordManager.get_current_record()` 是 thread-local 单例（Web SSE 路径用这条）。**两条路径都覆盖**。

---

## 二、目标与范围

### 2.1 目标

1. **架构层（核心）**：将 TraceCollector 接入到 `Agent.process_message()` 内部，从 `record_service` 自动读取所有 trace 上下文（含 source_type）。
2. **渠道层**：**零改造**。所有现有渠道（含微信客服）和未来新增渠道自动产生 trace，无需调用方做任何 trace 相关修改。
3. **展示层**：追踪页面同时显示所有来源的 session，能区分渠道来源。

### 2.2 范围

**包含**：
- `Agent.process_message()` 内部接入 TraceCollector，从 record_service 自动读取上下文
- 移除 `main.py:event_generator` 中现有的旁路收集代码
- `list_traced_sessions` API 增加 `source_type` 字段和筛选
- `get_trace_detail` API 增加 channel session 元信息补充
- 前端展示来源标签和渠道信息

**不包含**：
- 不修改 `chat_sessions` / `channel_sessions` 表结构
- 不重写 observability 主体设计（仅修正采集位置）
- 不修改任何 channel_routes.py 调用方（这是方案 C 的核心价值）
- 不新增告警/质量评估能力

### 2.3 不在本次范围（次要路径）

以下路径当前未调用 `SessionRecordManager.start_record()`，因此也不会产生 trace。这些是开发调试或后台路径，trace 价值低，本期不处理：
- Gradio 调试（`gradio_app.py:158`）
- CLI（`main.py:1856`）
- 定时任务（`scheduler/executor.py:133`）— 可后续单独补 `start_record(source_type='scheduler')`
- 旧版非 SSE 接口（`main.py:933`）— 历史遗留，不在主路径

未来若要让这些路径产生 trace，只需补 `start_record(...)` 调用即可，无需任何其他改动。

---

## 三、方案设计 — 方案 C（推荐）

### 3.1 核心思路：在 `process_message` 内部自动启动 TraceCollector

**思路一句话**：TraceCollector 在 `Agent.process_message()` 内部启动，自动从当前请求的 `record_service` 读取所有 trace 上下文。调用方无需传任何 trace 相关参数。

### 3.2 改造点 1：`Agent.process_message()` 内部接入

`process_message` 是 `AsyncGenerator`，原有 yield 逻辑不变，只在 yield 前后包一层收集。

```python
# src/core/agent.py - process_message 改造示意（仅标注 ★ 处为新代码）
async def process_message(
    self,
    user_input: str,
    session_id: str,
    user: Optional[User] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> AsyncGenerator[dict, None]:
    # ... 原有的初始化和澄清处理 ...

    # ★ 自动从 record_service 获取 trace 上下文
    from src.services.session_record import SessionRecordManager
    _record = getattr(self, '_explicit_record_service', None) \
              or SessionRecordManager.get_current_record()

    trace_collector = None
    if _record:
        try:
            from src.core.trace_collector import TraceCollector
            trace_collector = TraceCollector(
                session_id=_record.session_id,
                tenant_id=_record.tenant_id or '',
                user_id=_record.user_id or '',
                input_msg=_record.user_message or user_input,
                source_type=_record.source_type or 'chat',  # ★ 关键：自动来源
                subagent_id=getattr(self, '_subagent_id', None),
            )
        except Exception as e:
            logger.debug(f"Trace collector init skipped: {e}")

    try:
        # ... 原有的所有逻辑（澄清处理、主循环、LLM 调用、工具调用等） ...
        # 每个 yield 前：
        async for event in self._inner_iter(...):  # 原有迭代逻辑
            if trace_collector:
                trace_collector.on_event(event)   # ★ 旁路收集
            yield event

    except Exception as e:
        if trace_collector:
            trace_collector.on_error(str(e))      # ★ 错误记录
        raise
    finally:
        if trace_collector:
            trace_collector.on_complete(_record)  # ★ 完成持久化
```

**关键点**：

1. **保持 `process_message` 是 AsyncGenerator**：原 yield 逻辑不变，只在 yield 前加一行收集调用。
2. **不修改方法签名**：与方案 B（前版文档）不同，**不新增任何参数**。`source_type`、`tenant_id`、`user_id` 全部从 `_record` 自动读取。
3. **`_record` 为空时静默跳过**：Gradio/CLI/Scheduler 等无 record_service 的路径，不产生 trace，不影响业务。
4. **多个 yield 点统一处理**：`process_message` 中有多个 yield 点（澄清重委派、主循环、错误处理），改造时在每个 yield 前加一行 `if trace_collector: trace_collector.on_event(event)`。

### 3.3 改造点 2：移除 `main.py:event_generator` 中现有的旁路收集

`main.py:1490-1504` 现有 30 行旁路收集代码全部移除。改造后 `event_generator` 简化为：

```python
# src/main.py - event_generator 改造
async def event_generator():
    # ... 现有的初始化代码（record_service 仍然创建）...

    # ❌ 删除：原 TraceCollector 接入代码（约 30 行）

    try:
        yield init_msg
        try:
            async for event in agent.process_message(
                user_input=full_message,
                session_id=session_id,
                user=agent_user,
                attachments=attachments,
                cancel_check=lambda: sse_manager.is_cancelled(session_id),
            ):
                # 现有 SSE 转发代码不变
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                # ❌ 删除：trace_collector.on_event(event)

                # 现有 record_service 累积逻辑不变
                event_type = event.get("type")
                if event_type == "response":
                    response_parts.append(event.get("data", ""))
                # ...

        except asyncio.CancelledError:
            # ❌ 删除：trace_collector.on_event({"type": "cancelled"})
            # ... 现有错误处理不变 ...

        except Exception as e:
            # ❌ 删除：trace_collector.on_error(str(e))
            # ... 现有错误处理不变 ...

        # ❌ 删除：trace_collector.on_complete(record_service)

        # ... 现有的完成处理不变 ...

    finally:
        # ... 现有清理代码不变 ...
```

**注意**：`record_service` 的创建（`main.py:1481`）保留不动，它是 trace 上下文的来源。

### 3.4 改造点 3：`process_message_sync` 无需改动

`process_message_sync` 已经在 `agent.py:2452` 设置了 `self._explicit_record_service = record_service`，因此 `process_message` 内部能自动读到。**channel_routes.py 三处调用方、scheduler、旧版非 SSE 接口均无需任何改动**。

### 3.5 改造点 4：展示侧 API

#### `list_traced_sessions` 增加 source_type 字段和筛选

```python
# src/api/monitor.py
class SessionSummary(BaseModel):
    session_id: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_count: int = 0
    total_tokens: int = 0
    error_count: int = 0
    last_trace_at: Optional[str] = None
    first_input: Optional[str] = None
    source_type: Optional[str] = None     # ★ 新增
    subagent_id: Optional[str] = None     # ★ 新增


@router.get("/sessions", response_model=SessionListResponse)
async def list_traced_sessions(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    tenant_id: Optional[str] = Query(None),
    time_range: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None, description="来源：chat/wecom/wecom_kf/dingtalk/feishu"),  # ★ 新增
    search: Optional[str] = Query(None),
):
    ...
    if source_type:                                      # ★ 新增
        where_clauses.append("source_type = %s")
        params.append(source_type)

    cur.execute("""
        SELECT
            session_id, tenant_id, user_id,
            COUNT(*) as trace_count,
            SUM(total_tokens) as total_tokens,
            COUNT(*) FILTER (WHERE status = 'failed') as error_count,
            MAX(created_at) as last_trace_at,
            (array_agg(input ORDER BY created_at ASC))[1] as first_input,
            MIN(source_type) as source_type,             -- ★ 新增
            MIN(subagent_id) as subagent_id              -- ★ 新增
        FROM obs_traces
        {where_sql}
        GROUP BY session_id, tenant_id, user_id
        ORDER BY MAX(created_at) DESC
        LIMIT %s OFFSET %s
    """, ...)
```

#### `get_trace_detail` 增加 channel 元信息补充

`source_type` 为渠道类型时，从业务库 `channel_sessions` 查询补充：

```python
if trace.source_type in ('wecom', 'wecom_kf', 'dingtalk', 'feishu'):
    try:
        from src.db.database import get_db_connection
        with get_db_connection() as biz_cur:
            biz_cur.execute("""
                SELECT title, username, channel_type, channel_user_id, channel_chat_id
                FROM channel_sessions WHERE session_id = %s
            """, (trace.session_id,))
            ch = biz_cur.fetchone()
            if ch:
                trace.channel_info = {
                    "title": ch.get("title"),
                    "username": ch.get("username"),
                    "channel_type": ch.get("channel_type"),
                    "channel_user_id": ch.get("channel_user_id"),
                    "channel_chat_id": ch.get("channel_chat_id"),
                }
    except Exception as e:
        logger.warning(f"Failed to load channel info: {e}")
```

### 3.6 改造点 5：前端

#### TraceBrowser.vue（会话列表）

新增列「来源」：

| source_type | 标签 | 颜色 |
|------------|------|------|
| `chat` | Web | `info` |
| `wecom` | 企微 | `success` |
| `wecom_kf` | 企微客服 | `success` |
| `dingtalk` | 钉钉 | `primary` |
| `feishu` | 飞书 | `warning` |

新增筛选下拉「来源」。

#### TraceDetail.vue（trace 详情）

基本信息区，当 `trace.channel_info` 存在时显示渠道信息块：

```
来源: 🟢 企微客服
渠道用户: 张三 (wmxxxxx)     会话标题: 客户咨询
```

---

## 四、对 observability-design.md 的修正

本设计是对 [observability-design.md §3](./observability-design.md) 的修正。需要在原文档中标注：

| 原章节 | 原描述 | 修正 |
|-------|------|------|
| §3.1 架构 | "不修改 agent.py，通过事件流的 type 字段识别并记录每个步骤" | 改为：在 `Agent.process_message()` 内部接入 TraceCollector，从 `record_service` 自动读取上下文，所有调用方零改造自动覆盖 |
| §3.4 集成 | "在 `src/main.py` 的 `event_generator()` 中，只需在现有 `async for event` 循环前后各加一行" | 改为：在 `Agent.process_message()` 内部接入，`main.py:event_generator` 中的旁路代码移除 |

**修正理由**：原方案的"调用方旁路收集"导致每个新调用方都要各接一遍，违反 DRY，且事实上已造成 4 个渠道未接入。下沉到 `process_message` 内部 + 复用 `record_service` 是更合理的架构。

---

## 五、方案对比与选型理由

| 维度 | 方案 A（原 obs-design） | 方案 B（前版本文档） | **方案 C（本方案·推荐）** |
|------|----------------------|------------------|----------------------|
| TraceCollector 接入位置 | `main.py` 调用方旁路 | `Agent.process_message` 内部 | `Agent.process_message` 内部 |
| source_type 来源 | 调用方各传 | 调用方传新参数 | **从 record_service 自动读** |
| 渠道零改造 | ❌ 4 个渠道各改一次 | ❌ 4 个渠道各改一次 | ✅ **真正零改造** |
| 新加渠道成本 | 需手动接入 trace | 需传 source_type 参数 | **零成本**（只要按规范 start_record） |
| 与 chat_records 一致性 | 易不一致 | 易不一致 | ✅ 同源 |
| 代码改动量 | 中（每个调用方） | 大（每个调用方 + agent） | **小**（仅 agent + 删除 main 旁路） |
| 已有事实支撑 | - | - | ✅ 所有渠道已 start_record |

**结论**：方案 C 在改动量、未来扩展性、代码一致性三个维度全面胜出。

---

## 六、影响面

### 6.1 后端

| 文件 | 改动 | 风险 |
|------|------|------|
| `src/core/agent.py` | `process_message` 内部接入 TraceCollector（约 15 行新增），从 record_service 自动读取上下文 | 中：`process_message` 多个 yield 点需统一处理 |
| `src/main.py` | `event_generator` 移除旁路收集代码（约 30 行删除） | 低 |
| `src/saas/api/channel_routes.py` | **无改动** ✅ | 无 |
| `src/scheduler/executor.py` | 无改动（不产生 trace） | 无 |
| `gradio_app.py` / `src/main.py:1856` | 无改动（不产生 trace） | 无 |
| `src/api/monitor.py` | `list_traced_sessions` 增加 source_type 字段和筛选；`get_trace_detail` 增加 channel_info | 低 |
| `src/core/trace_collector.py` | 无改动（已支持 source_type） | 无 |

### 6.2 前端

| 文件 | 改动 |
|------|------|
| `frontend/src/api/monitor.ts` | `SessionSummary`、`TraceDetail` 类型增加字段；`getTracedSessions` 参数增加 `source_type` |
| `frontend/src/components/saas/TraceBrowser.vue` | 表格新增「来源」列；筛选区新增「来源」下拉 |
| `frontend/src/components/saas/TraceDetail.vue` | 基本信息区条件展示 channel_info |

### 6.3 数据库与配置

- **无表结构变更**。复用已有 `obs_traces.source_type` 字段（已存在）。
- **无新增配置**。

---

## 七、验证方案

### 7.1 采集侧

1. 启动服务后，**逐渠道**发送测试消息：
   - Web 聊天发一条
   - **微信客服发一条** ⭐ 重点验证
   - 企微会话发一条（如有配置）
   - 钉钉/飞书发一条（如有配置）
2. 查询验证：
   ```sql
   SELECT source_type, COUNT(*) FROM obs_traces
   WHERE created_at > NOW() - INTERVAL '10 minutes'
   GROUP BY source_type;
   ```
   预期看到所有触发过的 source_type。
3. 每个 trace 的 `session_id` 格式正确（channel 的应是 `{tenant}_{channel}_{user}_{subagent}`）。
4. 验证 trace 的 `input`/`output`/`spans` 完整。

### 7.2 展示侧

1. `/portal/monitoring` 列表能看到所有渠道的会话
2. 来源筛选「企微客服」能筛出对应会话
3. 进入会话详情，能看到该 session 下所有 trace
4. trace 详情页能看到「渠道用户: xxx」信息

### 7.3 回归测试

- **Web SSE 路径不受影响**：发送一条 Web 消息，确认仍正常出现在列表中，`source_type = 'chat'`
- **渠道调用方零改动验证**：检查 `channel_routes.py` git diff，应无任何 trace 相关改动
- **不产生 trace 的路径不影响业务**：Gradio/CLI 调用应正常工作，仅 `obs_traces` 中无记录

---

## 八、开发计划

> 关联：[observability-dev-plan.md](./observability-dev-plan.md) Phase 1 的延伸

| # | 任务 | 文件 | 预估 | 状态 |
|---|------|------|------|------|
| 1 | `process_message` 内部接入 TraceCollector，从 record_service 自动读取上下文 | `src/core/agent.py` | 0.8d | ⬜ |
| 2 | `main.py:event_generator` 移除旁路收集代码 | `src/main.py` | 0.2d | ⬜ |
| 3 | `list_traced_sessions` API 增加 source_type 筛选和返回字段 | `src/api/monitor.py` | 0.3d | ⬜ |
| 4 | `get_trace_detail` API 增加 channel_info 补充查询 | `src/api/monitor.py` | 0.3d | ⬜ |
| 5 | 前端 monitor.ts 类型扩展 | `frontend/src/api/monitor.ts` | 0.2d | ⬜ |
| 6 | TraceBrowser.vue 新增「来源」列和筛选 | `frontend/src/components/saas/TraceBrowser.vue` | 0.3d | ⬜ |
| 7 | TraceDetail.vue 条件展示 channel_info | `frontend/src/components/saas/TraceDetail.vue` | 0.2d | ⬜ |
| 8 | 渠道端到端验证（重点：微信客服） | - | 0.5d | ⬜ |
| 9 | 更新 observability-design.md §3 的描述 | `docs/infrastructure/observability-design.md` | 0.2d | ⬜ |

**合计**：约 3.0 人天（比方案 B 的 4.3 人天减少 30%，因为不需要改任何调用方）。

---

## 九、风险与权衡

| 风险 | 应对 |
|------|------|
| `process_message` 内部改造复杂，多个 yield 和异常路径 | 仔细审计所有 yield 点和 finally 块；编写单元测试覆盖异常路径 |
| TraceCollector 异常影响主流程 | 全部 try/except 包裹，失败仅 debug 日志（沿用现有处理） |
| 子智能体委派场景下，子 agent 的 `process_message` 也会产生 trace，可能与父 trace 混淆 | 子智能体执行走 `execute_as_subagent`（不调用 `process_message`），不受影响。如果未来需要子 trace，单独设计 |
| record_service 为空时跳过 trace | 这是预期行为：Gradio/CLI/Scheduler 等次要路径默认无 trace，如需要单独补 start_record |
| `get_trace_detail` 跨库查询 channel_sessions 性能 | 单条 session_id 查询，主键索引，<5ms，可接受 |
| 老 channel session（接入前产生的）仍看不到 trace | 接入前的会话本就没有 trace，符合预期；用户从接入时间点开始可见 |

---

## 十、后续扩展（不在本期）

1. **次要路径接入 trace**：为 Scheduler/Gradio/CLI 补 `start_record(source_type=...)` 调用即可
2. **渠道消息在 trace 详情中可视化**：从 `channel_messages` 表拉取该 session 的完整对话历史，嵌入 trace 详情页
3. **未产生 trace 的 channel session 告警**：channel 用户发了消息但 obs_traces 中无记录，触发告警
4. **子智能体 trace**：在 `execute_as_subagent` 中接入子级 TraceCollector，与父 trace 通过 parent_trace_id 关联
5. **`source_type` 扩展**：未来可能新增 H5 嵌入、API 调用等来源，复用同一字段
