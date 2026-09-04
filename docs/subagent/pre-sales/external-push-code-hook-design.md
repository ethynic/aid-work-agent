# 售前推送代码级兜底方案（事后异步推送钩子）

> **2026-09-04 状态更新**：本方案的代码实现 `external_push_10605.py`（10605 硬编码）已被 `src/services/recap/tasks/external_push.py`（租户文档驱动 + LLM http_api 工具循环）取代——recap 触发机制保留，推送调用序列改由 LLM 按租户 pre-sales-api.md 文档执行。本文档保留作为 recap 触发机制的设计依据，10605 专属的代码化推送逻辑描述已过时。

> 日期：2026-09-04
> 触发：9732230b 提示词驱动的「每轮问答推送」实测不生效。trace tr_3e7123a2f23a41de（qwen3.8-flash）与 tr_d07191f6a8674932（DeepSeek-v4-flash）交叉验证：两个模型的 system prompt 均含推送指令（位于前 3.5% 位置）、工具齐备、无异常，但都未发起推送调用。换模型无效，属提示词驱动方案的固有缺陷。
> 前置文档：[external-push-redesign-plan.md](./external-push-redesign-plan.md)（2026-09-03 提示词版方案，本方案继承其全部业务规则）
> 机制文档：[recap-mechanism-design.md](../recap-mechanism-design.md)（本方案的推送已归入 recap 机制，作为第一个任务实例）
> 关联：ext/10605-售前咨询接口文档.md §12、subagents/pre-sales/SUBAGENT.md、.claude/rules/billing_audit.md

---

## 1. 问题定义与方案思路

### 1.1 现状缺陷

| # | 缺陷 | 证据 |
|---|------|------|
| D1 | 模型不遵守推送指令（主因） | 2026-09-04 全天 8 轮对话仅 1 轮发起推送；qwen3.8-flash 与 deepseek-v4-flash 行为一致 |
| D2 | 推送崩溃连累主回复 | 10:45 轮 skill_execute files 崩溃（bbcb0961 已修），异常向上击穿会话队列 processor |
| D3 | 推送耗时计入客户等待 | 推送流程 5~6 步工具调用（加载文档→查客户→建/改客户→建跟进记录），全部串行在回复之前 |

### 1.2 方案思路

把推送从「LLM 自觉执行的多步工具链」改为「**代码在回复送达后异步执行的固定流程**」：

```
客户消息 -> Agent 主循环回答（不变）-> 回复发送给客户（客户等待到此结束）
                                        |
                                        v 异步（asyncio.create_task，不阻塞队列）
                              推送任务 pre_sales_push：
                                1. 幂等检查（同轮防重）
                                2. LLM 生成对话摘要（单轮无工具调用，独立计费）
                                3. delegate_login 取 client_token（复用现有缓存）
                                4. 调 10605：客户查重 -> 建/改客户 -> 每轮跟进记录
```

LLM 只负责一次性生成摘要内容（这是它擅长的单轮文本任务），**「要不要推、按什么顺序推」的决策全部收归代码**。

### 1.3 非目标（明确不做）

- 不改动 Agent 主循环与回答链路
- 不迁移「在线电商客服（11022）实时查询」场景——查询类工具调用模型遵守良好，另行设计（意图预取，见前置文档遗留议题）
- 不做租户间通用的推送框架抽象——当前仅 10605 一个外部系统，硬编码规格 + 预留配置位
- **不重复实现 recap 运行时**——任务调度、幂等、故障隔离由 recap 机制统一提供（见机制文档），本方案只定义推送适配器本身

**归入 recap 机制**：本方案的推送任务是 recap 机制的第 1 个适配器（任务名 `external_push`）。SUBAGENT.md 声明方式从原计划的自由章节降级为结构化配置：

```yaml
recap:
  tasks:
    - name: external_push
      when: every_round
      enabled: true
```

---

## 2. 业务规则继承（全部来自 2026-09-03 已确认方案，不重复决策）

以下规则原写在 10605 接口文档 §12 供 LLM 阅读执行，本方案改为**代码实现**，语义不变：

| 规则 | 原出处 | 代码化 |
|------|--------|--------|
| 查重键：`external_userid` 写入 `t_kehuxinxi.unionid`，精确查询命中走修改、未命中走创建 | 前置方案 §6.1 | `presales_10605.py` 固定实现 |
| 每轮问答 1 条跟进记录，同轮防重，失败重试 1 次后放弃 | 前置方案 §6.2 | 幂等键 + 重试逻辑 |
| 客户名称兜底：对话表明身份 > 微信昵称 > external_userid 前 8 位 | 前置方案 §6.3 | 摘要 prompt 提供昵称，代码兜底 |
| 委托登录 mobile = 归属员工手机号；token 缓存 TTL 23h；Code=-99 force_refresh | 前置方案 §4.3 | 复用 delegate_login.py 缓存逻辑 |
| 客户信息更新需带 `update_time` 防 Code:2；禁止命中后再创建 | §12 | 固定实现 |
| 推送失败不阻塞对话、不向客户暴露 | 前置方案 §7 | 异步任务异常全部吞掉 + 日志 |

**留资联动**：`record_lead_capture` 工具保留不动。其成功结果中的 `assignee_phone` 仍写入会话 metadata，供推送钩子读取（原「留资成功即推送」路径随本方案彻底移除）。

---

## 3. 模块设计

### 3.1 新增目录与文件

```
src/services/recap/
├── __init__.py                  # 导出 trigger_recap
├── runner.py                    # recap 运行时：配置读取、任务分发、幂等、故障隔离（机制文档定义）
└── tasks/
    ├── __init__.py              # 适配器注册表（name -> adapter class）
    └── external_push_10605.py   # 【本方案核心】推送适配器：委托登录复用 + 客户查重/建改 + 跟进记录创建
```

- 运行时（`runner.py`）的职责边界、幂等键、异常隔离约定见 [recap-mechanism-design.md](../recap-mechanism-design.md)，本方案不重复定义。
- `external_push_10605.py` 的登录部分**直接 import** `src/skills/pre-sales-api-1.0.0/scripts/delegate_login.py` 的缓存与请求函数（`_read_cache` / `_write_cache` / `_call_login_api` 为纯函数，可复用），不在两处维护登录逻辑；若技能侧后续重构，需同步。
- 10605 端点（login / 客户列表 / 创建 / 修改 / 跟进记录创建）**硬编码在 `external_push_10605.py` 模块顶部常量**，支持环境变量覆盖（`PRESALES_10605_BASE_URL` 等）。当前仅一个租户对接一个系统，不做租户级配置面；未来出现第二个系统时再引入结构化配置。

### 3.2 触发点与调用方

**位置（与原方案不同，已上移）**：`src/channels/session.py` 的 `ChannelSessionManager.process_and_persist`，`send_ok == True` 分支、`return` 之前（约 1385 行）。此为**全部渠道共用的单一收口**（wecom_kf / dingtalk / feishu / web 未来均自动获得 recap 能力），不再挂在 channel_routes.py 的 wecom_kf 专属路径上。

```python
# send 成功后（return 前）：
if send_ok:
    from src.services.recap import trigger_recap
    trigger_recap(
        agent=agent,                       # 直读 agent.subagent_config.recap 任务声明
        session_id=session_id,
        tenant_id=tenant_id,
        user_content=result.merged_input or user_content,   # 合并轮取合并后文本
        assistant_reply=response_text,
        round_message_id=user_message_id,  # 本轮落库的 channel_messages.message_id，幂等键
        record_service=record_service,
    )
```

**异步方式**：`trigger_recap` 内部 `asyncio.create_task`（自持引用 + done_callback 丢弃，仿照 channel_routes.py `_dingtalk_background_tasks` 模式）。外层本就运行在渠道回调触发的后台任务中，不再套线程/进程。

**触发条件**（runner 内校验，任一不满足直接返回）：

| 条件 | 来源 |
|------|------|
| agent 声明了 `recap.tasks` 且包含 `external_push` | `agent.subagent_config.recap`（文件版 SUBAGENT.md） |
| 系统开关开启 | `configs/config.yaml` `external_push.pre_sales.enabled`（默认 true） |
| `external_userid` 非空 | session_id 解析（`tenant_{tid}_wecom_kf_{open_kfid}_{external_userid}_{subagent}`） |

### 3.3 任务执行流程（external_push_10605.py 适配器）

```
async def execute(payload):                    # 由 recap runner 调度，幂等已由 runner 完成
    1. 采集上下文（全部同步 DB 读，无 LLM）：
       - external_userid / open_kfid      <- session_id 解析
       - 微信昵称 / 头像 / 性别            <- channel_sessions.metadata（渠道侧已落库）
       - lead_capture 状态（手机号等）     <- channel_sessions.metadata.lead_capture
       - 归属员工手机号 assignee_phone     <- kf 配置 tenant_user_id -> users.phone
         （复用 record_lead_capture._resolve_employee_phone 同款查询）
       - 本轮问答内容                      <- 任务入参
    2. LLM 摘要（见 §3.4）；失败降级用「用户消息截断 200 字 + 回复截断 200 字」兜底
    3. 委托登录：调 delegate_login 复用函数取 client_token（缓存命中则 0 次 HTTP）
       -- mobile 缺失：记日志，放弃本轮（继承 §7 降级表）
    4. 客户查重（unionid = external_userid 精确查询）
       -- 未命中 -> 创建客户（xingming 按兜底优先级）
       -- 命中且本轮有新留资信息（手机号等非空且与已存不同）-> 修改客户（带 update_time）
       -- 命中且无新信息 -> 跳过客户写入
       -- 违例保护：查重命中后禁止走创建分支（前置方案实测的重复客户根因）
    5. 创建跟进记录：neirong=客户诉求摘要、kehuhuifuneirong=回复要点、
       genjindongzuo="微信咨询（AI）"、shijian=当天日期、客户字段回填自查重结果
    6. Code != 0 -> 对照错误码重试 1 次（-99 先 force_refresh 登录）；仍失败放弃
    7. 异常上抛由 runner 吞掉隔离（不阻塞对话），适配器内 tlog("售前推送", ...) 全链路留痕
```

每步 HTTP 调用 timeout 15s；适配器内串行执行，不嵌套 create_task。

### 3.4 LLM 摘要设计

| 项 | 设计 |
|----|------|
| 调用方式 | `llm_gateway.chat()`（`src/llm/gateway.py`），单轮、无工具、`temperature=0.2`、`max_tokens=300` |
| 输入 | system prompt（固定，输出 JSON：`{"customer_need": str, "reply_summary": str, "customer_name_hint": str}`）+ 一条 user 消息（本轮问答原文 + 客户昵称等元数据） |
| 模型 | 走租户/系统默认 provider 配置（`settings.llm`），不单独指定 |
| 计费 | `record_background_llm_usage(response.get("usage"), source="pre_sales_push")`，满足 billing_audit §3.5 条件 A；摘要调用有 SessionRecordService 上下文时累加本 record，否则独立落 `chat_records`（函数已内置双路径） |
| 上下文变量 | 调用发生在 `asyncio.create_task` 中，**不依赖** ContextVar 租户上下文；tenant_id 显式传参 |
| 失败降级 | 摘要 LLM 失败/超时 -> 用截断原文兜底（见 §3.3 步骤 3），推送流程继续 |

摘要 prompt 要点：只依据本轮对话，不编造；`customer_name_hint` 仅在客户明确自报姓名时填，否则空（由代码走昵称兜底）；输出 JSON 解析失败视为摘要失败走降级。

### 3.5 幂等与防重

| 层 | 键 | TTL | 归属 |
|----|----|-----|------|
| recap 任务级 | `recap_task:{tenant_id}:{task_name}:{round_message_id}`（Redis SET NX） | 24h | **recap runner 统一实现**（机制文档 §5），键用本轮落库的 user message_id（channel_messages.message_id），渠道无关、天然防同轮重入与回调重放 |
| token | 现有 `pre_sales_client_token:{tenant_id}:{mobile}` | 23h | 本适配器复用，不变 |

键前缀在 `src/core/cache_utils.py` `CacheKeys` 注册为 `RECAP_TASK_DEDUP`，并同步 `docs/system/cache_usage.md`。Redis 不可用时降级内存（`redis_client` 已内置），重启可能极小概率重推一条跟进记录，业务可接受（10605 侧跟进记录为追加型）。

### 3.6 失败与可观测性

| 场景 | 行为 |
|------|------|
| 幂等命中 | 静默返回 |
| 上下文缺失（external_userid/assignee_phone 等） | 放弃本轮，tlog + logger.warning |
| LLM 摘要失败 | 截断原文兜底，继续推送 |
| 10605 业务错误 Code != 0 | 重试 1 次（-99 先强刷 token）；仍失败放弃 |
| 网络/超时 | 同上 |
| 任务整体异常 | except 吞掉，logger.opt(exception=True).error（进 error 日志库） |

日志约定：`tlog("售前推送", ...)` 每步留痕（临时排查主题，问题闭环后删）；主日志每轮一条 INFO 汇总（成功/跳过/失败 + 耗时），不加 DEBUG 噪音。

---

## 4. 配套改造

### 4.1 SUBAGENT.md 改造

frontmatter 在 `context` 之后新增 `recap` 配置块（声明式，替代原提示词触发）：

```yaml
recap:
  tasks:
    - name: external_push
      when: every_round
      enabled: true
```

正文改造：

- **删除**「外部系统数据推送（每轮问答）」整章的触发要求与流程指引（模型不再负责推送）
- 保留一小节「推送说明（后台自动，无需你操作）」：告知模型推送由系统自动完成，**对话中不要向客户提及推送，也不要尝试调用 pre-sales-api 脚本做推送**
- pre-sales-api 技能保留：`use_skill` 仍可用于模型按需查询接口字段含义（低频、非强制）；`load_api_config.py` / `delegate_login.py` 脚本本体保留（适配器复用其登录函数）
- 10605 接口文档 §12 同步业务规则章节标注「已代码化，仅供人工与维护者参考」

### 4.2 配置开关

`configs/config.yaml` 新增（`settings.py` 同步字段，命名一致）：

```yaml
external_push:
  pre_sales:
    enabled: true          # 总开关，回滚手段：置 false 即恢复纯对话
    summary_max_tokens: 300
```

### 4.3 计费核对（billing_audit.md §3 自检声明）

- 新增 LLM 调用点 1 处：摘要生成 -> 紧邻调用 `record_background_llm_usage(usage, source="pre_sales_push")`（条件 A）
- 无新增 Embedding / ASR / 视频调用点
- 不双计：Agent 主循环计费不受影响；摘要调用独立于主循环 record（仅当推送任务恰好持有 record_service 上下文时累加，实际为独立任务无此上下文，走独立落库路径）

---

## 5. 测试计划

| 层 | 用例 |
|----|------|
| 单元（tests/unit/services/） | 幂等命中直接返回；条件校验（非 pre-sales 会话/开关关/无 external_userid 不触发）；摘要 JSON 解析失败降级；查重命中不创建（禁止重复客户）；命中 + 新手机号走修改且带 update_time；Code=-99 强刷后重试；重试 1 次仍败放弃；delegate_login 复用函数 mock |
| 集成 | channel_routes 触发点：process_and_persist success 后任务被调度（mock push 任务）；失败不影响响应返回 |
| 计费 | 摘要调用产生 `chat_records`（source_type=background_llm, source=pre_sales_push）或 record 累加 |
| 实测（服务器 agent2） | 小团长真实对话 3 轮：10605 侧每轮 1 条跟进记录、客户仅 1 条、昵称兜底、回复先于推送送达（tlog 时间戳验证）、客户感知零延迟 |

---

## 6. 实施步骤

| # | 内容 | 文件 |
|---|------|------|
| 1 | recap 运行时（runner + 注册表，含单测） | src/services/recap/runner.py、tasks/__init__.py |
| 2 | CacheKeys 注册 + config 开关 | src/core/cache_utils.py、configs/config.yaml、src/config/settings.py |
| 3 | 推送适配器 external_push_10605（含单测） | src/services/recap/tasks/external_push_10605.py |
| 4 | process_and_persist 触发点接线 + 集成测试 | src/channels/session.py |
| 5 | SUBAGENT.md 增加 recap 配置块 + 推送章节降级 + 10605 文档 §12 标注 | subagents/pre-sales/SUBAGENT.md、ext/10605-售前咨询接口文档.md |
| 6 | cache_usage.md / billing_audit.md 相关登记 | docs/system/cache_usage.md 等 |
| 7 | 三智能体流程（开发 -> 测试 -> CodeReview）-> 服务器 agent2 实测 | — |

> 步骤 1 属 recap 机制本体，实施细节以机制文档为准；本方案步骤从原 6 步调整为依赖机制先行。

## 7. 遗留决策点

| # | 问题 | 当前倾向 |
|---|------|---------|
| Q1 | 推送任务崩溃丢失（同 worker asyncio task，进程重启即丢） | 接受：下一轮问答会补一条跟进记录，业务连续性够用；如需强可靠再加 Redis 队列 + background runner 消费（P2） |
| Q2 | 历史已推送数据与新代码规则的一致性 | 10605 侧按 unionid 查重天然幂等，无需迁移 |
| Q3 | 电商客服（11022）意图预取 | 另行设计，不在本方案范围 |
