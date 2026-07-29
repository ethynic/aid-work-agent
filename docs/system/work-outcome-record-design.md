# 工作成果记录功能设计

> 编号：47 ｜ 状态：📋 待开发 ｜ 创建日期：2026-07-29
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
| 数据来源 | `chat_records` 表聚合 | 子智能体主动登记 + 主循环自动捕获 |
| 粒度 | 一日一条 | **一次成果一条** |
| 时间维度 | 日/周/月 | 实时事件流，可任意时间范围检索 |
| 价值 | 证明"智能体很忙" | 证明"智能体产出有价值" |

两者互补：日报给出过程指标，工作成果给出具体产出。续费复盘时两者结合才有完整画面。

### 1.3 设计目标

1. **普适性**：覆盖所有子智能体（文件输出型 + 业务操作型 + 决策建议型），不依赖每个子智能体单独配置。
2. **低遗漏**：通过 LLM 自主判断 + SUBAGENT.md 显式声明 + 主循环自动捕获三层机制，保证关键成果不漏。
3. **低噪音**：避免每条对话都登记，只记录真正有价值的产出。
4. **租户隔离**：所有数据按租户隔离，普通用户看自己的，租户管理员看本租户的。
5. **可检索**：租户管理员和公司领导能按子智能体、时间、用户、成果类型筛选查看。

## 2. 核心难点分析：普适性定义"工作成果"

> 用户提出的核心问题：有些子智能体不输出文件，如何普适性地定义工作成果？方案1：相信 LLM；方案2：每子智能体单独说明。本节给出分析与选择。

### 2.1 方案1：相信 LLM 自主判断（纯通用方案）

在 `subagent_base.md` 加入通用要求，由 LLM 自主判断什么算工作成果。

**优点**：
- 零改造，所有子智能体（含未来新增）立刻覆盖。
- LLM 上下文感知强，能理解"修改送货地址"这种无文件的操作也算成果。

**缺点**：
- LLM 判断**不稳定**：不同模型（DeepSeek/Qwen/GLM）、不同温度、不同上下文，判断阈值会漂移。
- 容易"漏记"：LLM 在多轮长对话中容易忘记调用 `record_work_outcome`，特别是文件生成后忙着写正文时。
- 容易"滥记"：LLM 可能将普通工具调用（如读取文件、搜索知识库）也登记为成果，制造噪音。
- 不可解释：租户管理员问"为什么这条没记录"，无法回答。

### 2.2 方案2：每子智能体 SUBAGENT.md 显式声明（纯声明方案）

在每个 `subagents/<name>/SUBAGENT.md` 中增加 `work_outcomes` 字段，明确本子智能体的工作成果定义和触发时机。

**优点**：
- **精准可控**：声明即规范，LLM 按声明执行，行为可预测。
- 可解释：每条记录都能溯源到 SUBAGENT.md 的某条声明。
- 可审计：租户管理员能查看每个子智能体的"成果定义清单"。

**缺点**：
- **维护成本高**：每个子智能体都要写，遗漏场景时无任何兜底。
- 通用性弱：未来新增子智能体忘记声明，该子智能体就完全不记录。
- 无法覆盖跨子智能体的"主智能体直接交付"场景。

### 2.3 推荐方案：三层混合机制（显式声明 + LLM 自主兜底 + 自动捕获兜底）

| 层 | 角色 | 覆盖场景 | 重要性 |
|----|------|----------|--------|
| 层1 声明层 | SUBAGENT.md `work_outcomes` 字段 | 已声明的子智能体场景，按声明执行 | 主（精度） |
| 层2 通用层 | `subagent_base.md` 通用约束 + LLM 自主判断 | 未声明子智能体、跨边界场景 | 主（兜底） |
| 层3 捕获层 | Agent 主循环监测 `cp` 工具调用自动记录 | LLM 漏记文件交付场景 | 保险（最后兜底） |

**核心思路**：
- 层1 解决"精度问题"：声明过的子智能体按声明执行，行为可预测可解释。
- 层2 解决"覆盖问题"：未声明的子智能体（如电商客服、售后）由 LLM 自主判断，至少不会零记录。
- 层3 解决"漏记问题"：文件交付是公司最关心的成果，必须有保险。Agent 主循环检测到 `cp` 工具被调用且本次会话尚未登记，自动写一条 `source="auto_captured"` 的记录。

**为什么需要层3**：即使 SUBAGENT.md 声明清晰、subagent_base.md 要求明确，LLM 在多步骤任务中仍可能"先生成文件、再调用 cp、然后忘记 record_work_outcome 就直接回复用户"。这种漏记在旅游报价这种"生成 + 交付 + 解释"的三步场景下尤其常见。层3 用确定性的代码逻辑保证文件交付绝不漏记。

### 2.4 source 字段区分来源

每条记录用 `source` 字段标识触发来源，便于后续分析和调优：

| source 值 | 含义 | 触发条件 |
|-----------|------|----------|
| `agent_active` | LLM 主动调用 `record_work_outcome` | LLM 按 subagent_base.md / SUBAGENT.md 指引登记 |
| `auto_captured` | 主循环自动捕获 | LLM 调用 `cp` 但未登记，主循环代为写入 |
| `manual` | 用户/管理员手动补录 | 后台管理界面手动添加（后续可选） |

租户管理员可在工作成果列表中按 source 筛选，发现 LLM 漏记率（auto_captured 占比），反向优化提示词或 SUBAGENT.md 声明。

## 3. 整体架构

```
子智能体执行（agent.py 主循环）
    │
    ├─ LLM 自主判断（层2）：完成重要成果后调用 record_work_outcome 工具
    │   └─ 工具写入 work_outcomes 表（source=agent_active）
    │
    ├─ SUBAGENT.md work_outcomes 声明（层1）：加载到子智能体上下文
    │   └─ LLM 按声明触发 record_work_outcome
    │
    └─ 主循环 cp 调用监测（层3）：检测到 cp 工具调用且本次会话未登记文件
        └─ 自动写入 work_outcomes 表（source=auto_captured）

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
    source TEXT NOT NULL DEFAULT 'agent_active',     -- agent_active/auto_captured/manual
    chat_record_id BIGINT,                            -- 关联 chat_records.id，便于反查对话上下文

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
| `source` | 区分 LLM 主动登记 vs 主循环自动捕获 vs 手动补录 |
| `chat_record_id` | 反查对话上下文，便于管理员追溯 |

### 4.3 SQL 变更登记

按 [database_dev.md](../../.claude/rules/database_dev.md) 规范：
- `deploy/init-postgres.sql` 新增 `CREATE TABLE IF NOT EXISTS work_outcomes`（全新环境初始化）
- `deploy/db_update.sql` 追加增量变更条目：

```sql
-- 2026-07-29 新增工作成果记录表，记录子智能体产生的重要工作成果
CREATE TABLE IF NOT EXISTS work_outcomes ( ... );
-- 索引同上
```

## 5. 工具设计：`record_work_outcome`

### 5.1 工具实现

新工具文件：`src/tools/system/record_work_outcome.py`（新建 `system` 子目录）

```python
"""记录工作成果工具

供子智能体在产生重要工作成果时调用，将成果记录到 work_outcomes 表。
"""
from typing import Any, Dict, Optional
from datetime import datetime

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class RecordWorkOutcomeInput(BaseModel):
    summary: str = Field(
        ...,
        description=(
            "工作成果摘要，一句话描述本次成果的核心内容，必须包含业务对象和动作。"
            "示例：'为张总生成贵州5日行程单（含3个景点、2家酒店）'、"
            "'修改客户李明的订单收货地址为上海市浦东新区'、"
            "'给出客户投诉处理建议：先退货再补偿200元'"
        ),
    )
    outcome_type: str = Field(
        "file",
        description=(
            "成果类型。file=生成并交付了文件；"
            "action=完成重要业务操作（调用第三方接口、修改订单/客户信息等）；"
            "decision=给出重要决策建议或方案结论；other=其他重要成果"
        ),
    )
    file_id: Optional[str] = Field(
        None,
        description="若 outcome_type=file，传 cp 工具返回的 file_id",
    )
    file_name: Optional[str] = Field(
        None,
        description="若 outcome_type=file，传面向用户的业务文件名（cp 的 display_name）",
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="业务扩展信息（客户名、订单号、金额等），可选",
    )


class RecordWorkOutcomeTool(BaseTool):
    """记录工作成果

    在产生重要工作成果时调用。1 次成果 1 条记录。
    """

    name = "record_work_outcome"
    description = (
        "记录一次重要工作成果，便于租户公司长期沉淀和续费复盘。\n\n"
        "适用场景：\n"
        "- 生成并交付了文件（行程单、报价单、合同、对账单、报表等）\n"
        "- 完成重要的业务操作（修改订单、调整客户信息、对接第三方系统等）\n"
        "- 给出重要的决策建议或方案结论\n\n"
        "调用时机：完成上述动作后立即调用，1 次成果 1 条记录。\n"
        "不适用：普通闲聊、问询、工具调用失败、临时中间步骤（如读取文件、搜索知识库）。"
    )
    usage_guide = ""
    display_name = "记录工作成果"
    category = "system"
    InputModel = RecordWorkOutcomeInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        summary = (kwargs.get("summary") or "").strip()
        if not summary:
            return {"success": False, "error": "缺少 summary（工作成果摘要必填）"}

        outcome_type = kwargs.get("outcome_type") or "file"
        if outcome_type not in ("file", "action", "decision", "other"):
            return {"success": False, "error": f"非法 outcome_type: {outcome_type}"}

        # 文件型成果必须传 file_id
        if outcome_type == "file" and not kwargs.get("file_id"):
            return {
                "success": False,
                "error": "outcome_type=file 时必须传 file_id（cp 工具返回的 file_id）",
            }

        # 从工具执行上下文获取租户/用户/会话/渠道/子智能体信息
        ctx = self._get_execution_context()
        if not ctx.get("tenant_id") or not ctx.get("user_id"):
            return {"success": False, "error": "缺少租户/用户上下文"}

        try:
            from src.reports.work_outcome_db import WorkOutcomeDB
            outcome = WorkOutcomeDB.create(
                tenant_id=ctx["tenant_id"],
                user_id=ctx["user_id"],
                subagent_id=ctx.get("subagent_id"),
                session_id=ctx["session_id"],
                channel=ctx.get("channel"),
                summary=summary,
                outcome_type=outcome_type,
                file_id=kwargs.get("file_id"),
                file_name=kwargs.get("file_name"),
                file_path=kwargs.get("file_path"),
                metadata=kwargs.get("metadata") or {},
                source="agent_active",
                chat_record_id=ctx.get("chat_record_id"),
            )
            logger.info(
                f"工作成果已记录: tenant={ctx['tenant_id']}, user={ctx['user_id']}, "
                f"type={outcome_type}, outcome_id={outcome['outcome_id']}"
            )
            return {
                "success": True,
                "outcome_id": outcome["outcome_id"],
                "message": "工作成果已记录",
            }
        except Exception as e:
            logger.error(f"记录工作成果失败: {e}", exc_info=True)
            return {"success": False, "error": f"记录失败: {e}"}

    def _get_execution_context(self) -> Dict[str, Any]:
        """从 Agent 注入的上下文获取租户/用户/会话信息

        Agent 主循环在调用工具前会通过 hasattr 钩子注入 tenant_id、user_id、
        session_id、channel、subagent_id 等上下文（参考 word_process_tool 双轨模式）。
        """
        # 实现细节：通过 ContextVar 或实例属性获取，参考现有工具
        from src.tools._helpers import get_tool_execution_context
        return get_tool_execution_context()
```

### 5.2 工具注册

在 `src/core/agent.py` 的 `_register_builtin_tools()` 中注册（参考 `TransferToHumanTool` 在 line 472-473 的注册方式）：

```python
from src.tools.system.record_work_outcome import RecordWorkOutcomeTool
self.tool_registry.register(RecordWorkOutcomeTool())
```

同时在 `AGENT_TOOLS` 列表中添加 schema 定义（参考 `transfer_to_human` 的 schema 风格）。

### 5.3 上下文注入机制

参考 `word_process_tool` / `ppt_process_tool` 已有的"双轨租户注入"模式：Agent 主循环在调用工具前通过 `hasattr` 钩子检测工具是否需要上下文，注入 `tenant_id`、`user_id`、`session_id`、`channel`、`subagent_id`。

`src/tools/_helpers.py` 新增 `get_tool_execution_context()` 函数，统一从 ContextVar 读取。

## 6. 提示词设计：`subagent_base.md` 增强

在 `subagent_base.md` 的"文件交付规则"段后增加"工作成果记录规则"段（保持与现有硬约束平行的位置）：

```markdown
## 工作成果记录规则（必须遵守）

每当产生**重要工作成果**时，**必须紧接着调用 `record_work_outcome` 工具记录**，
便于租户公司长期沉淀和续费复盘。1 次成果 1 条记录。

### 必须记录的场景

1. **文件交付**：生成 Word/Excel/PPT/PDF 等文件并调用 `cp` 注册下载后，
   必须紧接着调用：
   ```
   record_work_outcome(
     summary="为XX生成XX文件（含XX等关键内容）",
     outcome_type="file",
     file_id="<cp 返回的 file_id>",
     file_name="<业务文件名>"
   )
   ```
2. **重要业务操作**：调用第三方系统接口完成实质性业务变更
   （修改订单、调整客户信息、修改送货地址、创建工单、回传广告转化数据等）：
   ```
   record_work_outcome(
     summary="<操作了XX的XX，从X改为Y>",
     outcome_type="action"
   )
   ```
3. **决策建议输出**：用户请求决策建议且你给出了明确方案：
   ```
   record_work_outcome(
     summary="<针对XX问题给出建议：XX>",
     outcome_type="decision"
   )
   ```

### 不需要记录的场景

- 普通闲聊、问询
- 工具调用失败、未完成业务动作
- 临时中间步骤（如读取文件、搜索知识库、调用 transfer_to_human 等）

### summary 写法

一句话描述成果内容，**必须包含业务对象和动作**：
- ✅ "为张总生成贵州5日行程单（含3个景点、2家酒店）"
- ✅ "修改客户李明的订单收货地址为上海市浦东新区"
- ✅ "给出客户投诉处理建议：先退货再补偿200元"
- ❌ "生成文件"（缺少业务信息）
- ❌ "完成操作"（缺少具体内容）
- ❌ "给用户建议"（缺少决策内容）

### 多成果场景

一次对话产生多个成果（如同时生成行程单和报价单）时，**每个成果独立调用一次** `record_work_outcome`，
不要合并成一条。
```

## 7. 子智能体 SUBAGENT.md 增强（可选/推荐）

### 7.1 `work_outcomes` 字段定义

在 SUBAGENT.md 的 YAML frontmatter 中增加可选字段 `work_outcomes`，声明本子智能体的工作成果类型：

```yaml
work_outcomes:
  declared: true                          # 是否已声明工作成果定义
  types:
    - type: file
      name: 行程单
      triggers:
        - "生成行程 HTML 长图"
        - "调用 cp 交付行程单文件"
      required_fields: [file_id, file_name]
    - type: file
      name: 报价单
      triggers:
        - "生成报价 Excel 文件"
        - "调用 cp 交付报价单"
      required_fields: [file_id, file_name]
    - type: action
      name: 行程调整
      triggers:
        - "客户确认调整行程后更新报价"
      required_fields: [summary]
    - type: decision
      name: 行程建议
      triggers:
        - "客户询问行程建议且给出方案"
      required_fields: [summary]
```

### 7.2 声明加载机制

`SubagentExecutor` 加载 SUBAGENT.md 时，将 `work_outcomes` 注入到子智能体 system_prompt 的 `{subagent_constraint_section}` 占位符中：

```
## 本子智能体的工作成果定义

你已被声明以下工作成果类型，**触发时必须调用 record_work_outcome**：

1. **行程单**（file）：生成行程 HTML 长图、调用 cp 交付行程单文件时
2. **报价单**（file）：生成报价 Excel 文件、调用 cp 交付报价单时
3. **行程调整**（action）：客户确认调整行程后更新报价时
4. **行程建议**（decision）：客户询问行程建议且给出方案时

未在上述列表中的场景，按 subagent_base.md 通用规则判断。
```

### 7.3 各子智能体声明建议

| 子智能体 | 建议声明的 work_outcomes |
|---------|-------------------------|
| travel-consultant | file:行程单、file:报价单、action:行程调整、decision:行程建议 |
| trade-specialist | file:报价单、file:对账单、action:客户跟进、action:邮件发送 |
| contract-archive-review | file:合同审查报告、decision:风险提示 |
| order-processing | action:订单创建、action:订单修改、action:订单取消 |
| customer-followup | action:客户跟进记录、decision:跟进建议 |
| after-sales | action:售后处理、decision:退款建议 |
| complaint-handling | action:投诉登记、decision:处理方案 |
| social-media-operations | file:内容文案、action:发布任务、action:广告操作 |
| competitor-research | file:竞品分析报告、decision:策略建议 |

未声明的子智能体（如未在上表中的新子智能体）走层2 LLM 自主判断。

### 7.4 声明的可演进性

`work_outcomes.declared: true/false` 用于运维统计：
- `true`：该子智能体已声明，按声明执行
- `false` 或缺失：该子智能体走层2 LLM 自主判断

运维可在管理后台查看"已声明 vs 未声明"的子智能体列表，推动未声明的子智能体逐步补充声明。

## 8. 自动捕获兜底机制（层3）

### 8.1 触发逻辑

在 `src/core/agent.py` 的工具执行后处理逻辑中加入：

```python
# 工具执行完毕后检查
if tool_name == "cp" and tool_result.get("success"):
    file_id = tool_result.get("file_id")
    session_id = current_session_id

    # 检查本次会话是否已经主动登记过该 file_id
    if not WorkOutcomeDB.exists_by_file_id(tenant_id, file_id, session_id):
        # LLM 漏记，主循环自动捕获
        WorkOutcomeDB.create(
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            session_id=session_id,
            channel=channel,
            summary=f"交付文件：{tool_result.get('display_name', '未命名文件')}",
            outcome_type="file",
            file_id=file_id,
            file_name=tool_result.get("display_name"),
            file_path=tool_result.get("file_path"),
            metadata={"auto_captured_reason": "llm_not_called_after_cp"},
            source="auto_captured",
            chat_record_id=chat_record_id,
        )
        logger.info(f"工作成果自动捕获: file_id={file_id}, session={session_id}")
```

### 8.2 设计要点

1. **去重**：先查 `exists_by_file_id`，避免 LLM 已登记 + 主循环又自动登记导致重复。
2. **降级摘要**：自动捕获时 summary 用 `display_name`，质量低于 LLM 主动写的。这是预期行为：用质量换覆盖。租户管理员能在列表中按 `source=auto_captured` 筛选，发现漏记率，反向优化提示词。
3. **仅捕获文件型**：层3 只捕获 `cp` 调用（文件交付）。action/decision 类型无法自动判断，依赖层1+层2。
4. **不影响主流程**：自动捕获失败（DB 异常等）只记日志，不阻塞主对话流程。

### 8.3 性能考量

`exists_by_file_id` 查询走 `idx_work_outcomes_file_id` 索引，单次查询 <1ms。每次 `cp` 调用多一次查询和可能的一次写入，性能影响可忽略。

## 9. 后端 API 设计

新文件：`src/api/work_outcomes.py`

### 9.1 路由列表

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/work-outcomes/list` | 列表查询（支持分页/筛选） | 普通用户看自己；租户管理员看本租户 |
| GET | `/api/work-outcomes/stats` | 统计（按子智能体/类型/时间） | 同上 |
| GET | `/api/work-outcomes/{outcome_id}` | 详情 | 自己或管理员 |
| DELETE | `/api/work-outcomes/{outcome_id}` | 删除（仅记录拥有者或租户管理员） | 鉴权 |
| POST | `/api/work-outcomes/{outcome_id}/feedback` | 反馈质量（好/差） | 自己或管理员（预留） |

### 9.2 列表查询参数

```python
@router.get("/list")
async def list_work_outcomes(
    request: Request,
    user_id: Optional[str] = Query(None, description="按用户筛选（管理员可用）"),
    subagent_id: Optional[str] = Query(None, description="按子智能体筛选"),
    outcome_type: Optional[str] = Query(None, description="file/action/decision/other"),
    source: Optional[str] = Query(None, description="agent_active/auto_captured/manual"),
    channel: Optional[str] = Query(None, description="web/wecom/dingtalk/feishu/wecom_kf"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    keyword: Optional[str] = Query(None, description="摘要关键词搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
```

### 9.3 权限模型

- **普通用户**：`user_id` 强制设为自己（忽略请求中的 `user_id` 参数）
- **租户管理员**：可查看本租户所有用户（`tenant_id` 过滤）
- **平台管理员**：通过 `X-Tenant-Id` header 指定目标租户

权限校验复用 `src/saas/permissions/checker.py` 的 `is_platform_admin` / `is_tenant_admin`，与 `work_reports.py` 完全一致。

### 9.4 统计接口

`GET /api/work-outcomes/stats` 返回：

```json
{
  "total": 156,
  "by_type": {"file": 89, "action": 42, "decision": 18, "other": 7},
  "by_subagent": {"travel-consultant": 78, "trade-specialist": 34, ...},
  "by_source": {"agent_active": 132, "auto_captured": 24, "manual": 0},
  "by_channel": {"web": 120, "wecom_kf": 30, "feishu": 6},
  "auto_capture_rate": 0.154,
  "time_range": {"start": "2026-07-01", "end": "2026-07-29"}
}
```

`auto_capture_rate` 是关键运维指标，反映 LLM 漏记率，用于反向优化提示词。

## 10. 前端页面设计

### 10.1 路由与菜单

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

### 10.2 页面组件

新组件：`frontend/src/components/reports/WorkOutcomes.vue`

遵循 [list-page-convention.md](../../.claude/rules/list-page-convention.md) 列表页规范：

**布局**：
- 顶部：搜索区（关键词 / 子智能体筛选 / 类型筛选 / 时间范围）+ 操作按钮区（导出，可选）
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
| 来源 | 80px | `source` 转中文徽章（主动=蓝/自动=灰） |
| 操作 | 100px | 详情 / 删除 |

**权限差异**：
- 普通用户：只看到自己的成果，不显示"用户"列
- 租户管理员：看到本租户全员的成果，额外显示"用户"列

### 10.3 详情弹框

点击"详情"打开 `BaseModal`（size=lg），展示：
- 完整成果摘要
- 文件信息（可下载）
- 业务元数据（metadata JSON 展示）
- 关联会话（点击跳转 `/chat/{session_id}`）
- 来源标识

### 10.4 API 封装

新文件：`frontend/src/api/workOutcomes.ts`，参考 `workReports.ts` 的封装风格，所有请求带 `getAuthHeader()`（含 `X-Tenant-Id`）。

### 10.5 页面元数据注册

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

## 11. 实施计划

### Phase 1：MVP 核心（必做，约 3-4 人天）

**目标**：跑通"LLM 主动登记 + 自动捕获兜底 + 列表查看"主流程

| 任务 | 文件 |
|------|------|
| 数据库表创建 | `deploy/init-postgres.sql` + `deploy/db_update.sql` |
| DB 访问层 | `src/reports/work_outcome_db.py`（新建，参考 `WorkDailyReportDB`） |
| 工具实现 | `src/tools/system/record_work_outcome.py`（新建） |
| 工具注册 | `src/core/agent.py` `_register_builtin_tools` + `AGENT_TOOLS` |
| 上下文注入 | `src/tools/_helpers.py` 新增 `get_tool_execution_context` |
| 提示词增强 | `src/prompts/templates/subagent_base.md` 增加"工作成果记录规则"段 |
| 自动捕获 | `src/core/agent.py` 主循环 `cp` 工具后处理 |
| API 路由 | `src/api/work_outcomes.py`（新建）+ `src/main.py` 注册路由 |
| 前端页面 | `frontend/src/components/reports/WorkOutcomes.vue` + 菜单注册 + 路由注册 |
| 前端 API | `frontend/src/api/workOutcomes.ts` |
| 页面元数据 | `configs/page_metadata.yaml` |

**验收**：
- 旅游顾问生成行程单后，自动调 `record_work_outcome`
- 即便 LLM 忘记调，主循环自动捕获
- 用户能在 `/t/{tenant_id}/work-outcomes` 看到记录
- 普通用户看自己的，租户管理员看本租户的

### Phase 2：声明层 + 统计（推荐，约 2-3 人天）

**目标**：SUBAGENT.md 显式声明 + 统计接口

| 任务 | 文件 |
|------|------|
| SUBAGENT.md 加载 work_outcomes 字段 | `src/subagents/loader.py`（或对应加载器） |
| 注入到 system_prompt | `src/core/agent.py` 的 `{subagent_constraint_section}` 拼接 |
| 统计接口 | `src/api/work_outcomes.py` `/stats` 路由 |
| 前端统计卡片 | `WorkOutcomes.vue` 顶部增加统计区 |
| 各子智能体补充声明 | 9 个 SUBAGENT.md（按 §7.3 表） |

**验收**：
- 旅游顾问 SUBAGENT.md 声明 4 种工作成果类型
- LLM 按声明触发，行为可预测
- 统计页面显示 `auto_capture_rate`，反映 LLM 漏记率

### Phase 3：增强（可选，后续迭代）

- 工作成果导出（Excel/PDF 报告，便于续费时发给公司领导）
- 工作成果反馈（好/差评，用于 LLM 质量评估）
- 工作成果关联订单/客户表（business_pages 业务数据关联）
- 工作日报中嵌入"今日工作成果"摘要（与工作日报联动）
- 工作成果自动归档到组织知识库（与 [org-knowledge-sedimentation](../research/org-knowledge-sedimentation-research.md) 联动）

## 12. 风险与权衡

### 12.1 已识别风险

| 风险 | 缓解措施 |
|------|----------|
| LLM 在多步骤任务中忘记调用 `record_work_outcome` | 层3 自动捕获兜底，文件交付绝不漏记 |
| LLM 滥记（普通对话也登记） | 提示词明确"不适用场景"；通过 `auto_capture_rate` 监控异常 |
| 自动捕获的 summary 质量低 | 用 `display_name` 作为最低质量底线；后续可加 LLM 异步重写 |
| 表数据增长过快（每次 cp 都写一条） | `created_at` 索引 + 定期归档（6 个月前的数据可归档到冷存储） |
| SUBAGENT.md 声明遗漏 | 层2 通用约束兜底；运维界面统计未声明子智能体 |
| 跨子智能体成果归属争议 | 以"实际执行工具的子智能体"为准，主智能体直接交付时 subagent_id=NULL |

### 12.2 与现有功能的关系

| 现有功能 | 关系 |
|---------|------|
| 工作日报（`work_daily_reports`） | **互补**：日报是过程统计，工作成果是产出沉淀。Phase 3 可联动：日报嵌入"今日工作成果"摘要 |
| `cp` 工具 | **依赖**：层3 监测 `cp` 调用自动捕获；`file_id` 来自 `cp` 返回值 |
| `chat_records` 表 | **关联**：`work_outcomes.chat_record_id` 关联对话上下文 |
| `channel_sessions` 表 | **关联**：`work_outcomes.session_id` 同时支持 web 和第三方渠道会话 |
| 组织知识沉淀（调研中） | **演进**：工作成果可成为组织知识库的来源之一 |

### 12.3 不做的事

- **不自动判断"重要性"**：是否记录由 LLM（层2）+ 声明（层1）+ cp 调用（层3）决定，不做额外的"重要性评分"。
- **不替代工作日报**：工作日报仍按现有逻辑运行，工作成果是独立的产出视图。
- **不做复杂的成果分类树**：`outcome_type` 只保留 file/action/decision/other 四类，过于精细的分类会增加 LLM 判断负担。
- **不在主对话流程中阻塞**：所有记录操作（含自动捕获）失败只记日志，不阻塞主对话。

## 13. 后续演进方向（不在本期范围）

1. **工作成果导出报告**：续费时一键导出"近 90 天工作成果清单"（PDF/Excel），含统计图表和典型成果展示。
2. **工作成果与业务数据关联**：如行程单关联 `bs_trade_specialist_*` 业务表，便于按业务对象检索。
3. **工作成果反馈循环**：租户管理员对成果打"好/差"分，反馈到 LLM 质量评估系统。
4. **工作成果自动归档**：N 天后的工作成果自动归档到组织知识库，成为公司资产。
5. **跨租户匿名 benchmark**：行业同类子智能体的工作成果产出对比（如旅游行业平均文件交付率），仅展示统计值不暴露原始数据。

---

## 附录 A：与工作日报的字段对比

| 维度 | `work_daily_reports` | `work_outcomes` |
|------|---------------------|------------------|
| 粒度 | 一日一条（按 user + date + type） | 一次成果一条 |
| 触发 | 定时任务 + 手动重生 | 实时事件 |
| 数据来源 | `chat_records` 聚合 | 子智能体主动登记 + 主循环自动捕获 |
| 文件关联 | 无 | `file_id` + `file_name` + `file_path` |
| LLM 生成 | summary_text / highlights / suggestions | 仅 summary（一句话） |
| 统计维度 | dialog_count / credit_cost / saved_minutes | by_type / by_subagent / by_source / by_channel |

## 附录 B：典型场景数据示例

### B.1 旅游顾问生成行程单

```json
{
  "outcome_id": "wo_a1b2c3d4",
  "tenant_id": "t_001",
  "user_id": "u_zhang",
  "subagent_id": "travel-consultant",
  "session_id": "session_abc123",
  "channel": "web",
  "summary": "为张总生成贵州5日行程单（含3个景点、2家酒店）",
  "outcome_type": "file",
  "file_id": "file_xyz789",
  "file_name": "贵州5日行程单.pdf",
  "file_path": "storage/tenants/t_001/conversation/file_xyz789.pdf",
  "metadata": {
    "customer_name": "张总",
    "days": 5,
    "attractions": ["黄果树", "小七孔", "天眼"],
    "hotels": ["贵阳XX酒店", "荔波YY酒店"]
  },
  "source": "agent_active",
  "chat_record_id": 12345,
  "created_at": "2026-07-29T14:32:15"
}
```

### B.2 电商客服修改送货地址

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
  "source": "agent_active",
  "chat_record_id": 12346,
  "created_at": "2026-07-29T15:20:42"
}
```

### B.3 自动捕获（LLM 漏记）

```json
{
  "outcome_id": "wo_i9j0k1l2",
  "tenant_id": "t_001",
  "user_id": "u_wang",
  "subagent_id": "travel-consultant",
  "session_id": "session_ghi789",
  "channel": "web",
  "summary": "交付文件：贵州5日行程单.pdf",
  "outcome_type": "file",
  "file_id": "file_abc123",
  "file_name": "贵州5日行程单.pdf",
  "file_path": "storage/tenants/t_001/conversation/file_abc123.pdf",
  "metadata": {
    "auto_captured_reason": "llm_not_called_after_cp"
  },
  "source": "auto_captured",
  "chat_record_id": 12347,
  "created_at": "2026-07-29T16:05:18"
}
```
