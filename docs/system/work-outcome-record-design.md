# 工作成果记录功能设计

> 编号：47 ｜ 状态：📋 待开发 ｜ 创建日期：2026-07-29 ｜ 更新：2026-07-29（改为 cp 实时 + 定时复盘两层机制）
>
> 关联：[工作日报设计](../research/ai-agent-experience-daily-report-research.md)、[组织知识沉淀](../research/org-knowledge-sedimentation-research.md)

## 1. 背景与目标

### 1.1 业务价值

工作日报已经回答了"智能体今天做了多少事"。但**做了多少事 ≠ 做出了什么成果**：

- 租户公司在续费决策时，公司领导真正想看到的是**有长期价值的产出**--生成的报价单、修改的订单、给出的方案，而不是"今天对话数 200 条"。
- 智能体生成的文件（行程单、报价单、合同、对账单等）对租户公司是有长期价值的资产，但目前散落在各对话中，**无独立视图**、**无统计入口**、**续费时拿不出来**。
- 业务操作型成果（如电商客服调用第三方接口修改送货地址）连文件都没有，目前完全没有沉淀机制。

### 1.2 与工作日报的区别

| 维度 | 工作日报 | 工作成果 |
|------|---------|---------|
| 视角 | **过程统计**（对话数、积分、节省时间） | **产出沉淀**（生成了什么文件、做了什么操作） |
| 数据来源 | `chat_records` 表聚合 | cp 工具实时登记 + 半夜小模型复盘 |
| 粒度 | 一日一条 | **一次成果一条** |
| 时间维度 | 日/周/月 | 实时事件流 + 每日复盘补全，可任意时间范围检索 |
| 实时性 | 日报次日生成 | 文件型实时；操作/决策型次日复盘生成 |
| 价值 | 证明"智能体很忙" | 证明"智能体产出有价值" |

两者互补：日报给出过程指标，工作成果给出具体产出。续费复盘时两者结合才有完整画面。

### 1.3 设计目标

1. **普适性**：覆盖所有子智能体（文件输出型 + 业务操作型 + 决策建议型），不依赖每个子智能体单独配置。
2. **低延时**：文件型成果在 `cp` 工具调用时同步写入（无 LLM 调用），不拖慢智能体主响应。
3. **低遗漏**：文件型成果由 `cp` 实时记录兜底；非文件型成果由半夜小模型复盘补全。
4. **低噪音**：复盘任务用 LLM 判断什么算成果，避免每条对话都登记。
5. **租户隔离**：所有数据按租户隔离，普通用户看自己的，租户管理员看本租户的。
6. **可检索**：租户管理员和公司领导能按子智能体、时间、用户、成果类型筛选查看。

## 2. 核心难点分析：普适性定义"工作成果"

> 用户提出的核心问题：有些子智能体不输出文件，如何普适性地定义工作成果？同时担心 `record_work_outcome` 工具调用拖慢响应。本节给出方案选型。

### 2.1 方案1：LLM 实时自主调用 `record_work_outcome`（已否决）

让子智能体在产生重要成果时主动调用 `record_work_outcome` 工具。

**否决理由**：
- **拖慢响应**：每次成果都要多一次 LLM 工具调用循环，文件型场景尤为明显（cp 之后还要再调一次工具）。
- **不稳定**：LLM 在多步骤任务中容易忘记调用，漏记率高。
- **易滥记**：LLM 可能把普通工具调用也登记为成果。
- 工作成果记录并不要求实时性，没必要为它付出主响应延时的代价。

### 2.2 方案2：每子智能体 SUBAGENT.md 显式声明（已否决）

在每个 `subagents/<name>/SUBAGENT.md` 中增加 `work_outcomes` 字段，明确本子智能体的工作成果定义和触发时机，由 LLM 按声明调用工具。

**否决理由**：
- 仍然依赖 LLM 实时调用工具，没有解决延时问题。
- 维护成本高，每个子智能体都要写。
- 通用性弱，未来新增子智能体忘记声明就完全不记录。

### 2.3 推荐方案：两层混合机制（cp 实时 + 半夜小模型复盘）

| 层 | 角色 | 覆盖场景 | 实时性 |
|----|------|----------|--------|
| 层1 实时层 | `cp` 工具内嵌写入 | 所有文件型成果 | 实时（无 LLM 调用） |
| 层2 复盘层 | 每日半夜定时任务用小模型复盘 | 非文件型成果（action/decision/other） | 次日 02:30 生成 |

**核心思路**：

- **层1 解决"文件型成果实时记录"问题**：所有智能体输出文件都会调用 `cp` 工具注册下载，在 `cp` 内部同步写一条 `work_outcomes` 记录。无 LLM 调用，延时 <5ms，对主响应无感知。文件型成果是公司最关心的产出，必须 100% 覆盖且实时。
- **层2 解决"非文件型成果覆盖"问题**：业务操作型（修改订单、调整客户信息）和决策建议型没有文件，无法靠 `cp` 捕获。每天半夜 02:30 跑一次定时任务，扫描当天有对话但**未产生文件型成果**的会话，用 `DEEPSEEK_REPORT_MODEL_CODE` 小模型分析会话内容，判断是否产生了 action/decision 类成果并提取摘要。准确度不高也可接受--因为主要的文件型成果已经实时记录了。

**为什么用小模型而非主模型**：
- 复盘任务在半夜跑，不占用高峰期算力。
- 非文件型成果是"锦上添花"，准确度要求低于文件型。
- 与工作日报使用相同的 `DEEPSEEK_REPORT_MODEL_CODE` 配置，无需新增依赖。
- 小模型成本低，可以扫所有未登记会话。

### 2.4 source 字段区分来源

每条记录用 `source` 字段标识触发来源，便于后续分析和调优：

| source 值 | 含义 | 触发条件 |
|-----------|------|----------|
| `cp_realtime` | cp 工具实时登记 | LLM 调用 `cp` 交付文件时，cp 内部同步写入 |
| `scheduled_review` | 定时任务复盘提取 | 半夜 02:30 小模型分析会话内容后写入 |
| `manual` | 用户/管理员手动补录 | 后台管理界面手动添加（后续可选） |

租户管理员可在工作成果列表中按 source 筛选，观察复盘任务的提取质量，反向优化复盘提示词。

## 3. 整体架构

```
子智能体执行（agent.py 主循环）
    │
    ├─ 实时层（层1）：LLM 调用 cp 工具交付文件
    │   └─ cp 工具内部同步写入 work_outcomes 表（source=cp_realtime）
    │      └─ 不调用 LLM，不阻塞主响应
    │
    └─ 复盘层（层2）：每日 02:30 定时任务
        ├─ 扫描当日有对话但无文件型成果的会话
        ├─ 用 DEEPSEEK_REPORT_MODEL_CODE 小模型分析会话内容
        └─ 提取 action/decision/other 成果写入 work_outcomes 表（source=scheduled_review）

查询层（API + 前端）
    ├─ 普通用户：GET /api/work-outcomes/list?user_id=me
    └─ 租户管理员：GET /api/work-outcomes/list?（不限 user_id，按 tenant 过滤）
```

## 4. 数据模型

### 4.1 表结构

新表 `work_outcomes`（系统级共享表，不带 `bs_` 前缀，与 `work_daily_reports` 一致）：

```sql
-- ============== 工作成果记录功能（2026-07-29）==============
-- 记录子智能体产生的重要工作成果（文件交付/业务操作/决策建议）
-- 详见 docs/system/work-outcome-record-design.md
CREATE TABLE IF NOT EXISTS work_outcomes (
    id SERIAL PRIMARY KEY,
    outcome_id TEXT UNIQUE NOT NULL,                  -- wo_xxxxxxxx 格式
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,                            -- 触发成果的用户
    subagent_id TEXT,                                 -- 子智能体ID（主智能体直接交付时为 NULL）
    session_id TEXT NOT NULL,                         -- 会话ID（chat_sessions.session_id 或 channel_sessions.session_id）
    channel TEXT,                                     -- web/wecom/dingtalk/feishu/wecom_kf

    -- 成果内容
    summary TEXT NOT NULL,                            -- 一句话摘要，含业务对象和动作
    outcome_type TEXT NOT NULL DEFAULT 'other',       -- file/action/decision/other
    importance TEXT NOT NULL DEFAULT 'normal',       -- normal/high（预留，便于后续过滤）

    -- 文件关联（outcome_type=file 时必填）
    file_id TEXT,                                     -- cp 工具注册的 file_id
    file_name TEXT,                                    -- 面向用户的业务文件名（display_name）
    file_path TEXT,                                    -- 文件存储路径（用于后续清理/迁移）

    -- 业务扩展信息（如客户名、订单号、金额、行程天数等）
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- 来源与溯源
    source TEXT NOT NULL DEFAULT 'cp_realtime',     -- cp_realtime/scheduled_review/manual
    chat_record_id BIGINT,                            -- 关联 chat_records.id，便于反查对话上下文

    -- 复盘任务溯源（source=scheduled_review 时填写）
    review_batch_id TEXT,                             -- 复盘批次ID，便于追溯本次复盘的所有产出
    review_confidence REAL,                           -- 小模型判断置信度 0.0~1.0，便于后续过滤

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_work_outcomes_tenant_created
    ON work_outcomes(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_user_created
    ON work_outcomes(tenant_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_subagent_created
    ON work_outcomes(tenant_id, subagent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_session
    ON work_outcomes(session_id);
CREATE INDEX IF NOT EXISTS idx_work_outcomes_file_id
    ON work_outcomes(file_id) WHERE file_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_work_outcomes_review_batch
    ON work_outcomes(review_batch_id) WHERE review_batch_id IS NOT NULL;
```

### 4.2 字段设计要点

| 字段 | 设计理由 |
|------|----------|
| `outcome_id` | 对齐 `work_daily_reports.report_id` 的 `wdr_xxx` 风格，外部引用稳定 |
| `subagent_id` | 主智能体直接交付时为 NULL（理论上不应发生，但留余地） |
| `channel` | 区分 web/feishu/dingtalk/wecom/wecom_kf，便于按渠道统计 |
| `outcome_type` | file/action/decision/other 四类，覆盖用户提出的所有场景 |
| `importance` | 预留字段，便于后续按重要度筛选（暂不暴露给 LLM） |
| `file_id` + `file_name` + `file_path` | 文件交付三件套，对齐 `cp` 工具返回字段 |
| `metadata` | 业务上下文（客户名、订单号、金额等），JSONB 灵活扩展 |
| `source` | 区分 cp 实时登记 vs 定时任务复盘 vs 手动补录 |
| `chat_record_id` | 反查对话上下文，便于管理员追溯 |
| `review_batch_id` | 复盘批次 ID，便于追溯本次复盘任务提取的所有成果 |
| `review_confidence` | 小模型判断置信度，运维可按置信度过滤低质量成果 |

### 4.3 SQL 变更登记

按 [database_dev.md](../../.claude/rules/database_dev.md) 规范：
- `deploy/init-postgres.sql` 新增 `CREATE TABLE IF NOT EXISTS work_outcomes`（全新环境初始化）
- `deploy/db_update.sql` 追加增量变更条目：

```sql
-- 2026-07-29 新增工作成果记录表，记录子智能体产生的重要工作成果
CREATE TABLE IF NOT EXISTS work_outcomes ( ... );
-- 索引同上
```

## 5. cp 工具增强设计（层1 实时记录）

### 5.1 设计原则

**核心约束**：cp 工具的成果记录逻辑必须**同步、低延时、不阻塞主流程**。

- **不调用 LLM**：直接拼装 summary 写入 DB，延时 <5ms。
- **失败不阻塞 cp 主流程**：成果记录失败只记 warning 日志，cp 的文件复制和下载注册照常返回成功。
- **去重**：单次 cp 调用只写一条 work_outcomes，不重复。
- **不依赖 LLM 上下文**：cp 工具拿不到摘要等业务语义，summary 用 `display_name` 作为最低质量底线。

### 5.2 cp 工具改造点

修改 `src/tools/file/cp_tool.py`，在 `register_download=True` 且成功注册下载后，追加一次 `work_outcomes` 写入：

```python
# src/tools/file/cp_tool.py 内部
async def execute(self, source_file_path: str, file_path: Optional[str] = None,
                  register_download: bool = True, display_name: Optional[str] = None,
                  **kwargs) -> Dict[str, Any]:
    # ... 原有文件复制、下载注册逻辑 ...

    result = {
        "success": True,
        "file_id": file_id,
        "file_path": str(target_path),
        "display_name": display_name or target_path.name,
        # ... 其他原有字段 ...
    }

    # ===== 新增：实时登记工作成果（层1）=====
    if register_download and file_id:
        try:
            await self._record_work_outcome(result, **kwargs)
        except Exception as e:
            # 失败不阻塞 cp 主流程
            logger.warning(f"cp 工具实时登记工作成果失败（不影响主流程）: {e}", exc_info=True)

    return result


async def _record_work_outcome(self, cp_result: Dict, **kwargs) -> None:
    """cp 内嵌的工作成果实时登记"""
    from src.reports.work_outcome_db import WorkOutcomeDB
    from src.tools._helpers import get_tool_execution_context

    ctx = get_tool_execution_context()
    if not ctx.get("tenant_id") or not ctx.get("user_id"):
        # 无租户/用户上下文（如系统调试场景），跳过登记
        return

    display_name = cp_result.get("display_name", "未命名文件")
    WorkOutcomeDB.create(
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        subagent_id=ctx.get("subagent_id"),
        session_id=ctx["session_id"],
        channel=ctx.get("channel"),
        summary=f"交付文件：{display_name}",  # 最低质量底线，复盘任务不会覆盖
        outcome_type="file",
        file_id=cp_result.get("file_id"),
        file_name=display_name,
        file_path=cp_result.get("file_path"),
        metadata={"source_tool": "cp"},
        source="cp_realtime",
        chat_record_id=ctx.get("chat_record_id"),
    )
    logger.debug(
        f"工作成果实时登记: file_id={cp_result.get('file_id')}, "
        f"session={ctx.get('session_id')}"
    )
```

### 5.3 上下文注入机制

参考 `word_process_tool` / `ppt_process_tool` 已有的"双轨租户注入"模式：Agent 主循环在调用工具前通过 `hasattr` 钩子检测工具是否需要上下文，注入 `tenant_id`、`user_id`、`session_id`、`channel`、`subagent_id`、`chat_record_id`。

`src/tools/_helpers.py` 新增 `get_tool_execution_context()` 函数，统一从 ContextVar 读取。cp 工具与本设计共用此机制。

### 5.4 为什么不优化 summary

`cp` 工具拿不到业务上下文（如"客户名"、"行程天数"），强行从 kwargs 推断会让 cp 工具承担不该有的业务依赖。`交付文件：xxx` 的 summary 质量已足够支撑列表展示和续费复盘--业务细节由 `file_name` 自身描述（如"贵州5日行程单.pdf"已经表达了客户和内容）。

**复盘任务不覆盖 cp 实时记录**：cp_realtime 类记录的 summary 即使粗糙也不替换，避免覆盖实时登记的事实。复盘任务只针对"无文件型成果"的会话产出 action/decision 类记录。

## 6. 定时任务设计：每日复盘（层2）

### 6.1 任务定位

**目标**：补全非文件型工作成果（action/decision/other），覆盖 cp 工具无法捕获的场景。

**不做什么**：
- 不重新分析已产生文件型成果的会话（cp 已实时登记，复盘不再介入）。
- 不重写 cp_realtime 类记录的 summary。
- 不在白天高峰期跑，避免占用主响应算力。

### 6.2 调度配置

在 `src/scheduler/manager.py` 注册定时任务，参考已有的"每日记忆总结"（02:00）和"渠道去重清理"（03:00）的注册方式：

```python
# src/scheduler/manager.py 内部
# ===== 工作成果复盘任务（每日 02:30）=====
self._scheduler.add_job(
    self._run_work_outcome_review,
    CronTrigger(hour=2, minute=30, timezone="Asia/Shanghai"),
    id="work_outcome_review_daily",
    name="工作成果复盘",
    max_instances=1,
    coalesce=True,
)
logger.info("后端日志：已注册工作成果复盘任务 (cron=02:30)")
```

**时间选择理由**：
- 02:00 是记忆总结任务，02:30 避开其峰值。
- 03:00 是去重清理，02:30 提前完成不冲突。
- 半夜低峰期，复盘任务调小模型不占用主响应算力。

### 6.3 复盘流程

新文件：`src/reports/work_outcome_review.py`

```python
async def run_daily_review(target_date: date) -> str:
    """每日工作成果复盘主入口

    Args:
        target_date: 复盘日期（默认昨天，便于 02:30 跑时覆盖前一自然日）

    Returns:
        review_batch_id: 本次复盘批次 ID（rb_yyyymmdd_xxxxxxxx）
    """
    batch_id = f"rb_{target_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    logger.info(f"工作成果复盘开始: batch_id={batch_id}, date={target_date}")

    # 1. 扫描当日所有有对话的会话，按 tenant_id 分组
    sessions = await _list_active_sessions_on_date(target_date)

    for tenant_id, tenant_sessions in group_by_tenant(sessions):
        # 2. 过滤掉已经产生 cp_realtime 文件型成果的会话
        #    （这些会话的文件型成果已经实时登记，复盘只关注未产生文件的会话）
        candidate_sessions = [
            s for s in tenant_sessions
            if not _has_file_outcome(s.session_id)
        ]

        # 3. 对每个候选会话，调小模型分析是否产生 action/decision 成果
        for session in candidate_sessions:
            try:
                outcomes = await _review_session_with_llm(session, target_date)
                for outcome in outcomes:
                    WorkOutcomeDB.create(
                        tenant_id=tenant_id,
                        user_id=session.user_id,
                        subagent_id=session.subagent_id,
                        session_id=session.session_id,
                        channel=session.channel,
                        summary=outcome["summary"],
                        outcome_type=outcome["outcome_type"],
                        metadata=outcome.get("metadata", {}),
                        source="scheduled_review",
                        chat_record_id=outcome.get("chat_record_id"),
                        review_batch_id=batch_id,
                        review_confidence=outcome.get("confidence", 0.5),
                    )
            except Exception as e:
                logger.error(
                    f"工作成果复盘失败: session={session.session_id}, {e}",
                    exc_info=True
                )

    logger.info(f"工作成果复盘完成: batch_id={batch_id}")
    return batch_id
```

### 6.4 会话筛选逻辑

```python
async def _list_active_sessions_on_date(target_date: date) -> List[SessionInfo]:
    """列出当日有对话的会话

    从 chat_records 表反查当日有消息的 session_id，
    关联 chat_sessions / channel_sessions 拿到 tenant_id / user_id / subagent_id / channel
    """
    # SQL 大致：
    # SELECT DISTINCT s.session_id, s.tenant_id, s.user_id, s.subagent_id, s.channel
    # FROM chat_records r
    # LEFT JOIN chat_sessions s ON r.session_id = s.session_id
    # WHERE r.created_at::date = target_date
    # UNION (channel_sessions 维度)
    ...


def _has_file_outcome(session_id: str) -> bool:
    """检查会话是否已经产生过文件型工作成果（cp_realtime 记录）

    有文件型成果的会话不再复盘，避免重复提取。
    注意：仍可能复盘出 action/decision 类成果（如先修改订单、再生成文件）。
    """
    return WorkOutcomeDB.exists_by_session_and_type(
        session_id=session_id, outcome_type="file"
    )
```

**优化点**：如果会话已经产生文件型成果，但 LLM 在文件交付前还做过重要 action（如"先调整了客户信息，再生成报价单"），复盘任务仍可以提取该 action 成果。判断条件是"会话不存在任何 cp_realtime 记录"过于严格，可能漏掉这种场景；判断条件是"会话不存在任何文件型成果"即可。

### 6.5 小模型调用提示词

通过 LLM Gateway 调用 `DEEPSEEK_REPORT_MODEL_CODE`（参考 `src/reports/generator.py` 中工作日报的调用方式）。

```python
async def _review_session_with_llm(session: SessionInfo, target_date: date) -> List[Dict]:
    """用小模型分析会话内容，提取非文件型工作成果"""
    # 1. 拉取会话消息（仅当日部分）
    messages = await _load_session_messages(session.session_id, target_date)
    if len(messages) < 2:
        return []  # 单条消息的会话不复盘

    # 2. 构造小模型 prompt
    system_prompt = REVIEW_SYSTEM_PROMPT
    user_prompt = _format_session_for_review(messages, session)

    # 3. 调用 DEEPSEEK 小模型（非流式，JSON 输出）
    from src.llm.gateway import LLMGateway
    gateway = LLMGateway()
    response = await gateway.chat(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model_code=settings.llm.report_model_code,  # DEEPSEEK_REPORT_MODEL_CODE
        temperature=0.1,  # 低温度保证稳定
        response_format="json",
    )

    # 4. 解析 JSON，过滤低置信度结果
    outcomes = _parse_review_response(response)
    return [o for o in outcomes if o.get("confidence", 0) >= 0.6]
```

`REVIEW_SYSTEM_PROMPT` 大致内容：

```
你是一个工作成果复盘助手。给定一天内某会话的全部对话记录，判断智能体是否产生了
**非文件型**的重要工作成果（文件型已由系统实时登记，无需你提取）。

需要提取的成果类型：
- action：完成重要业务操作（调用第三方接口、修改订单/客户信息、回传广告数据等）
- decision：给出重要决策建议或方案结论
- other：其他有长期价值的重要成果

不需要提取的场景：
- 普通闲聊、问询
- 工具调用失败、未完成业务动作
- 临时中间步骤（如读取文件、搜索知识库）
- 已生成文件的操作（文件型成果已实时登记）

输出 JSON 格式：
{
  "outcomes": [
    {
      "summary": "一句话描述，必须包含业务对象和动作",
      "outcome_type": "action|decision|other",
      "metadata": {"key": "value"},  // 业务上下文，可选
      "chat_record_id": 12345,        // 关联的消息ID
      "confidence": 0.85               // 置信度 0.0~1.0
    }
  ]
}

如果该会话没有非文件型重要成果，返回 {"outcomes": []}。
```

### 6.6 性能与成本考量

| 指标 | 估算 |
|------|------|
| 每日候选会话数 | 假设租户 100 个、每租户日均 50 个会话，约 5000 个 |
| 已产生文件型成果的会话占比 | 估 30%，剩 3500 个候选 |
| 每会话 token 数 | 平均 2000 token（含上下文） |
| 总 token 消耗 | 3500 × 2000 = 700 万 token/天 |
| DEEPSEEK 小模型成本 | 约 ¥5~10/天（按当前 DeepSeek 定价估算） |
| 单次复盘任务执行时间 | 3500 会话 × 并发 5 × 2s/次 ≈ 25 分钟，02:30 启动 03:00 前完成 |

**优化手段**：
- 并发调用小模型（信号量限流 5）。
- 跳过消息数 <2 的会话。
- 跳过纯闲聊会话（小模型返回 `{"outcomes": []}` 时不再处理）。
- 单会话超时 30s，超时跳过不重试。
- 失败的会话只记日志，不阻塞整批任务。

### 6.7 复盘任务的可观测性

- `review_batch_id` 字段可追溯每批复盘的所有产出。
- `review_confidence` 字段可按置信度过滤低质量成果（运维可在管理后台隐藏 confidence<0.6 的记录）。
- 复盘任务执行日志写入主日志 + `log/temp/work_outcome_review.log`（按 [backend_dev.md 临时主题日志规范](../../.claude/rules/backend_dev.md#临时主题日志tlog)）。
- 复盘任务的执行状态（启动时间、完成时间、处理会话数、产出成果数）写入 `log/temp/work_outcome_review.log` 便于排查。

## 7. 后端 API 设计

新文件：`src/api/work_outcomes.py`

### 7.1 路由列表

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/work-outcomes/list` | 列表查询（支持分页/筛选） | 普通用户看自己；租户管理员看本租户 |
| GET | `/api/work-outcomes/stats` | 统计（按子智能体/类型/时间/来源） | 同上 |
| GET | `/api/work-outcomes/{outcome_id}` | 详情 | 自己或管理员 |
| DELETE | `/api/work-outcomes/{outcome_id}` | 删除（仅记录拥有者或租户管理员） | 鉴权 |
| POST | `/api/work-outcomes/review/run` | 手动触发复盘（仅平台管理员） | 平台管理员 |
| POST | `/api/work-outcomes/{outcome_id}/feedback` | 反馈质量（好/差） | 自己或管理员（预留） |

### 7.2 列表查询参数

```python
@router.get("/list")
async def list_work_outcomes(
    request: Request,
    user_id: Optional[str] = Query(None, description="按用户筛选（管理员可用）"),
    subagent_id: Optional[str] = Query(None, description="按子智能体筛选"),
    outcome_type: Optional[str] = Query(None, description="file/action/decision/other"),
    source: Optional[str] = Query(None, description="cp_realtime/scheduled_review/manual"),
    channel: Optional[str] = Query(None, description="web/wecom/dingtalk/feishu/wecom_kf"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    keyword: Optional[str] = Query(None, description="摘要关键词搜索"),
    min_confidence: Optional[float] = Query(None, description="最小置信度（仅复盘类记录有意义）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
```

### 7.3 权限模型

- **普通用户**：`user_id` 强制设为自己（忽略请求中的 `user_id` 参数）
- **租户管理员**：可查看本租户所有用户（`tenant_id` 过滤）
- **平台管理员**：通过 `X-Tenant-Id` header 指定目标租户

权限校验复用 `src/saas/permissions/checker.py` 的 `is_platform_admin` / `is_tenant_admin`，与 `work_reports.py` 完全一致。

### 7.4 统计接口

`GET /api/work-outcomes/stats` 返回：

```json
{
  "total": 156,
  "by_type": {"file": 89, "action": 42, "decision": 18, "other": 7},
  "by_subagent": {"travel-consultant": 78, "trade-specialist": 34, ...},
  "by_source": {"cp_realtime": 89, "scheduled_review": 67, "manual": 0},
  "by_channel": {"web": 120, "wecom_kf": 30, "feishu": 6},
  "review_stats": {
    "last_batch_id": "rb_20260728_a1b2c3d4",
    "last_batch_started_at": "2026-07-29T02:30:12",
    "last_batch_outcomes_count": 67,
    "avg_confidence": 0.78
  },
  "time_range": {"start": "2026-07-01", "end": "2026-07-29"}
}
```

`review_stats` 反映复盘任务的运行情况，便于运维监控复盘质量。

### 7.5 手动触发复盘

`POST /api/work-outcomes/review/run`（仅平台管理员）：

```json
// 请求
{
  "target_date": "2026-07-28"  // 可选，默认昨天
}

// 响应
{
  "success": true,
  "batch_id": "rb_20260728_a1b2c3d4",
  "message": "复盘任务已启动，预计 25 分钟后完成"
}
```

用于开发调试或补救漏跑的场景。

## 8. 前端页面设计

### 8.1 路由与菜单

新增路由 `/t/{tenant_id}/work-outcomes`，在 `MenuSidebar.vue` 的"工作日报"按钮下方增加"工作成果"按钮（与"工作日报"平级，均为所有租户用户可见）。

参考现有"工作日报"菜单的实现（`MenuSidebar.vue:89-107`），新菜单：

```vue
<!-- 工作成果入口：所有租户用户可见 -->
<button
  v-if="tenantId"
  @click="router.push(`/t/${tenantId}/work-outcomes`)"
  :class="[
    'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-sm',
    route.path === `/t/${tenantId}/work-outcomes`
      ? 'bg-primary-50 text-primary-700 font-medium'
      : 'text-gray-600 hover:bg-gray-50'
  ]"
>
  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"
       stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <!-- 奖杯/成果图标 -->
    <path d="M6 9H4.5a2.5 2.5 0 010-5H6M18 9h1.5a2.5 2.5 0 000-5H18M4 22h16M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 19.35 7 20.99 7 22h10c0-1.01-.85-2.65-2.03-2.79-.5-.23-.97-.66-.97-1.21v-2.34M12 2v8M8 2v4a4 4 0 008 0V2" />
  </svg>
  <span>工作成果</span>
</button>
```

### 8.2 页面组件

新组件：`frontend/src/components/reports/WorkOutcomes.vue`

遵循 [list-page-convention.md](../../.claude/rules/list-page-convention.md) 列表页规范：

**布局**：
- 顶部：搜索区（关键词 / 子智能体筛选 / 类型筛选 / 来源筛选 / 时间范围）+ 操作按钮区（导出，可选）
- 中部：`BaseTable` 表格（按 `created_at DESC` 排序）
- 底部：`BasePagination` 分页器

**表格列**：

| 列 | 宽度 | 说明 |
|----|------|------|
| 序号 | 60px | `seqNumber(index)` |
| 时间 | 160px | `created_at` 格式化 |
| 子智能体 | 120px | `subagent_id` 转中文名 |
| 类型 | 80px | `outcome_type` 转中文 + 颜色徽章（file=蓝/action=绿/decision=黄/other=灰） |
| 成果摘要 | 自适应 | `summary`（截断 + tooltip） |
| 文件 | 120px | `outcome_type=file` 时显示下载链接，否则显示 "-" |
| 来源 | 100px | `source` 转中文徽章（cp实时=蓝/复盘=紫/手动=灰） |
| 操作 | 100px | 详情 / 删除 |

**权限差异**：
- 普通用户：只看到自己的成果，不显示"用户"列
- 租户管理员：看到本租户全员的成果，额外显示"用户"列

### 8.3 详情弹框

点击"详情"打开 `BaseModal`（size=lg），展示：
- 完整成果摘要
- 文件信息（可下载）
- 业务元数据（metadata JSON 展示）
- 关联会话（点击跳转 `/chat/{session_id}`）
- 来源标识 + 复盘置信度（source=scheduled_review 时显示）
- 复盘批次 ID（source=scheduled_review 时显示，便于追溯同批次其他成果）

### 8.4 API 封装

新文件：`frontend/src/api/workOutcomes.ts`，参考 `workReports.ts` 的封装风格，所有请求带 `getAuthHeader()`（含 `X-Tenant-Id`）。

### 8.5 页面元数据注册

按 [architecture.md "添加业务页面"](../../.claude/rules/architecture.md) 规范，在 `configs/page_metadata.yaml` 注册：

```yaml
- page_id: work-outcomes
  title: 工作成果
  description: 查看智能体产生的工作成果（生成的文件、完成的业务操作、给出的决策建议）
  route: /work-outcomes
  icon: "🏆"
  domain: reports
  status: developing  # 开发完成后改为 published
```

如果 `reports` 域不存在，同时在 `domains` 列表中添加。

## 9. 实施计划

### Phase 1：MVP 核心（必做，约 3-4 人天）

**目标**：跑通"cp 实时登记 + 半夜小模型复盘 + 列表查看"主流程

| 任务 | 文件 |
|------|------|
| 数据库表创建 | `deploy/init-postgres.sql` + `deploy/db_update.sql` |
| DB 访问层 | `src/reports/work_outcome_db.py`（新建，参考 `WorkDailyReportDB`） |
| 上下文注入 | `src/tools/_helpers.py` 新增 `get_tool_execution_context` |
| cp 工具增强 | `src/tools/file/cp_tool.py` 内嵌 `_record_work_outcome` |
| 复盘任务实现 | `src/reports/work_outcome_review.py`（新建） |
| 复盘任务注册 | `src/scheduler/manager.py` 注册 02:30 cron |
| 小模型复盘提示词 | `src/reports/prompts/work_outcome_review.md`（新建） |
| API 路由 | `src/api/work_outcomes.py`（新建）+ `src/main.py` 注册路由 |
| 前端页面 | `frontend/src/components/reports/WorkOutcomes.vue` + 菜单注册 + 路由注册 |
| 前端 API | `frontend/src/api/workOutcomes.ts` |
| 页面元数据 | `configs/page_metadata.yaml` |

**验收**：
- 旅游顾问生成行程单后，cp 工具实时写入 work_outcomes 表（source=cp_realtime）
- 电商客服修改订单地址后，半夜 02:30 复盘任务提取出 action 类成果（source=scheduled_review）
- 用户能在 `/t/{tenant_id}/work-outcomes` 看到记录
- 普通用户看自己的，租户管理员看本租户的
- cp 工具主响应延时增加 <10ms（DB 写入 + 异常隔离）

### Phase 2：观测与运维（推荐，约 1-2 人天）

**目标**：复盘任务可观测 + 手动触发入口

| 任务 | 文件 |
|------|------|
| 复盘任务执行日志（tlog） | `src/reports/work_outcome_review.py` 接入 tlog |
| 统计接口 review_stats | `src/api/work_outcomes.py` `/stats` 路由增强 |
| 手动触发复盘 API | `POST /api/work-outcomes/review/run` |
| 前端统计卡片 | `WorkOutcomes.vue` 顶部增加统计区 |
| 前端筛选区来源筛选 | `WorkOutcomes.vue` 增加 source 下拉框 |

**验收**：
- 统计页面显示 `review_stats`，反映复盘任务运行情况
- 平台管理员可在管理后台手动触发复盘，便于补救漏跑
- 复盘任务日志在 `log/temp/work_outcome_review.log` 可查

### Phase 3：增强（可选，后续迭代）

- 工作成果导出（Excel/PDF 报告，便于续费时发给公司领导）
- 工作成果反馈（好/差评，用于 LLM 质量评估）
- 工作成果关联订单/客户表（business_pages 业务数据关联）
- 工作日报中嵌入"今日工作成果"摘要（与工作日报联动）
- 工作成果自动归档到组织知识库（与 [org-knowledge-sedimentation](../research/org-knowledge-sedimentation-research.md) 联动）
- 复盘任务置信度阈值可配置（管理后台可调整 `min_confidence` 过滤低质量成果）

## 10. 风险与权衡

### 10.1 已识别风险

| 风险 | 缓解措施 |
|------|----------|
| cp 工具实时登记失败影响主流程 | 失败只记 warning 日志，不阻塞 cp 主流程返回 |
| cp 实时登记的 summary 质量低（仅"交付文件：xxx"） | `file_name` 自身已表达业务内容（如"贵州5日行程单.pdf"），列表展示足够；复盘任务不覆盖 cp_realtime 记录 |
| 半夜复盘任务漏跑（服务器重启等） | Phase 2 提供手动触发 API；可加补跑逻辑（次日检测前一日是否漏跑） |
| 小模型误判（把普通对话登记为成果） | `review_confidence` 字段 + 默认过滤 confidence<0.6 的记录；运维可调整阈值 |
| 小模型漏判（漏掉真实成果） | 文件型成果由 cp 实时兜底，已 100% 覆盖；非文件型漏判可接受（"锦上添花"型） |
| 表数据增长过快 | `created_at` 索引 + 定期归档（6 个月前的数据可归档到冷存储） |
| 复盘任务占用小模型配额 | 与工作日报错峰（日报 02:00、成果 02:30）；并发限流 5；单会话超时 30s |
| 跨子智能体成果归属争议 | 以"实际执行 cp 的子智能体"为准（cp_realtime）；复盘任务以"会话绑定的子智能体"为准（scheduled_review） |

### 10.2 与现有功能的关系

| 现有功能 | 关系 |
|---------|------|
| 工作日报（`work_daily_reports`） | **互补**：日报是过程统计，工作成果是产出沉淀。Phase 3 可联动：日报嵌入"今日工作成果"摘要 |
| `cp` 工具 | **依赖 + 增强**：cp 内嵌写入 work_outcomes 表，主响应延时增加 <10ms |
| `chat_records` 表 | **关联**：`work_outcomes.chat_record_id` 关联对话上下文 |
| `channel_sessions` 表 | **关联**：`work_outcomes.session_id` 同时支持 web 和第三方渠道会话 |
| 工作日报定时任务 | **错峰**：日报 02:00、成果 02:30、去重清理 03:00，互不冲突 |
| `DEEPSEEK_REPORT_MODEL_CODE` 配置 | **复用**：与工作日报同款小模型配置，无需新增 |
| 组织知识沉淀（调研中） | **演进**：工作成果可成为组织知识库的来源之一 |

### 10.3 不做的事

- **不依赖 LLM 实时调用工具登记成果**：避免拖慢主响应，改为 cp 实时 + 半夜复盘。
- **不自动判断"重要性"**：是否记录由 cp 调用（层1）+ 复盘任务小模型（层2）决定，不做额外的"重要性评分"。
- **不替代工作日报**：工作日报仍按现有逻辑运行，工作成果是独立的产出视图。
- **不做复杂的成果分类树**：`outcome_type` 只保留 file/action/decision/other 四类，过于精细的分类会增加小模型判断负担。
- **不在主对话流程中阻塞**：cp 实时登记失败只记日志，不阻塞主对话；复盘任务在半夜跑，不影响主响应。
- **不在白天跑复盘任务**：复盘任务固定 02:30 跑，避免占用主响应算力。

## 11. 后续演进方向（不在本期范围）

1. **工作成果导出报告**：续费时一键导出"近 90 天工作成果清单"（PDF/Excel），含统计图表和典型成果展示。
2. **工作成果与业务数据关联**：如行程单关联 `bs_trade_specialist_*` 业务表，便于按业务对象检索。
3. **工作成果反馈循环**：租户管理员对成果打"好/差"分，反馈到 LLM 质量评估系统。
4. **工作成果自动归档**：N 天后的工作成果自动归档到组织知识库，成为公司资产。
5. **跨租户匿名 benchmark**：行业同类子智能体的工作成果产出对比（如旅游行业平均文件交付率），仅展示统计值不暴露原始数据。
6. **复盘任务置信度自学习**：根据用户反馈（好/差评）调整小模型提示词，提升后续复盘准确度。
7. **白天补跑机制**：检测到前一日复盘任务漏跑时，自动在白天低峰期补跑（如 12:30）。

---

## 附录 A：与工作日报的字段对比

| 维度 | `work_daily_reports` | `work_outcomes` |
|------|---------------------|------------------|
| 粒度 | 一日一条（按 user + date + type） | 一次成果一条 |
| 触发 | 定时任务 + 手动重生 | cp 实时登记 + 半夜复盘 |
| 数据来源 | `chat_records` 聚合 | cp 工具 + 复盘小模型 |
| 文件关联 | 无 | `file_id` + `file_name` + `file_path` |
| LLM 生成 | summary_text / highlights / suggestions | 仅 summary（一句话） |
| 实时性 | 次日生成 | 文件型实时；操作/决策型次日复盘 |
| 统计维度 | dialog_count / credit_cost / saved_minutes | by_type / by_subagent / by_source / by_channel |

## 附录 B：典型场景数据示例

### B.1 旅游顾问生成行程单（cp 实时登记）

```json
{
  "outcome_id": "wo_a1b2c3d4",
  "tenant_id": "t_001",
  "user_id": "u_zhang",
  "subagent_id": "travel-consultant",
  "session_id": "session_abc123",
  "channel": "web",
  "summary": "交付文件：贵州5日行程单.pdf",
  "outcome_type": "file",
  "file_id": "file_xyz789",
  "file_name": "贵州5日行程单.pdf",
  "file_path": "storage/tenants/t_001/conversation/file_xyz789.pdf",
  "metadata": {"source_tool": "cp"},
  "source": "cp_realtime",
  "chat_record_id": 12345,
  "review_batch_id": null,
  "review_confidence": null,
  "created_at": "2026-07-29T14:32:15"
}
```

### B.2 电商客服修改送货地址（半夜复盘提取）

```json
{
  "outcome_id": "wo_e5f6g7h8",
  "tenant_id": "t_001",
  "user_id": "u_li",
  "subagent_id": "after-sales",
  "session_id": "session_def456",
  "channel": "wecom_kf",
  "summary": "修改客户李明的订单（#20260729001）收货地址为上海市浦东新区XX路XX号",
  "outcome_type": "action",
  "file_id": null,
  "file_name": null,
  "metadata": {
    "customer_name": "李明",
    "order_id": "20260729001",
    "old_address": "北京市朝阳区XX",
    "new_address": "上海市浦东新区XX路XX号"
  },
  "source": "scheduled_review",
  "chat_record_id": 12346,
  "review_batch_id": "rb_20260728_a1b2c3d4",
  "review_confidence": 0.82,
  "created_at": "2026-07-29T02:34:42"
}
```

### B.3 决策建议类成果（半夜复盘提取）

```json
{
  "outcome_id": "wo_i9j0k1l2",
  "tenant_id": "t_001",
  "user_id": "u_wang",
  "subagent_id": "complaint-handling",
  "session_id": "session_ghi789",
  "channel": "web",
  "summary": "给出客户投诉处理建议：先退货再补偿200元",
  "outcome_type": "decision",
  "file_id": null,
  "file_name": null,
  "metadata": {
    "customer_name": "赵六",
    "complaint_type": "商品质量",
    "suggested_action": "退货+补偿200元"
  },
  "source": "scheduled_review",
  "chat_record_id": 12347,
  "review_batch_id": "rb_20260728_a1b2c3d4",
  "review_confidence": 0.75,
  "created_at": "2026-07-29T02:35:18"
}
```
