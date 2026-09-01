# 客户端计费统一接入设计（boss cli / 协会信息收集 / 未来客户端）

> 状态：📋 设计完成待开发（2026-09-01 调研定稿）
> 关联：`docs/ideas.md` #42 租户积分充值与计费、#BOSS CLI 接入 Web Agent、LLM 调用计费缺失审计与修复计划
> 数据实证：agent2 库（aid_work_agent2@124.222.3.254:5433）2026-08-17 ~ 09-01 真实数据

## 1. 背景与问题

boss cli 的计费日志由服务端在 boss_* 代理工具成功时插入 `client_usage_logs` 台账；协会信息收集客户端的 LLM 计费由服务端代理（`/api/client/v1/llm/chat`）插入同一台账。用户反馈三个问题：

1. **租户归属**：很多计费日志看不出属于哪个租户；
2. **日志缺失**：保存的计费日志比实际调用少，且没存"调用了哪个命令、参数是什么"；
3. **展示割裂**：客户端部分的计费（boss 工具按次费、协会客户端 LLM 费）没有显示到「智能体计费消耗」页面（每日用量清单），需要一套未来客户端也能复用的标准接入方法。

## 2. 现状链路（实测 + 代码核实）

### 2.1 计费写入链路（共 3 条，全部同事务扣 `tenants.credit_balance`）

| 链路 | 台账表 | 写入点 | 触发时机 |
|------|--------|--------|---------|
| 智能体对话 | `chat_records` | `src/db/models.py:1095` ChatRecordDB.create | 每轮对话结束（session_record.save） |
| 协会客户端 LLM 代理 | `client_usage_logs` | `src/db/client_binding_db.py:284` record_llm_usage（×10 系数） | 客户端每次经服务端代理调 LLM |
| BOSS 本地工具按次 | `client_usage_logs` | `src/db/client_binding_db.py:362` record_tool_usage | **proxy_tool 轮询到成功终态时**（`src/local_tools/proxy_tool.py:152-158`） |

BOSS 计费行现状字段：`tenant_id` 有值；`binding_id` 固定哨兵 `'boss-local-runtime'`；**`session_id` 恒为 NULL**（record_tool_usage 支持，但 proxy_tool 不传）；命令名塞在 `model` 列；`detail` JSON 只有 `{invocation_id, device_id}`，**不含参数**；参数只存在 `local_tool_invocations.arguments_json`。

### 2.2 计费展示链路

- 每日用量清单 + 下钻详情：`src/saas/api/billing_balance.py` 的 `/api/saas/billing/usage`（:67）与 `/usage/daily-detail`（:212）**只查 `chat_records`**（:113/:130/:157、:260/:271）。
- 日均消耗：`src/saas/services/renewal.py:48-80` 用 `chat_records + client_usage_logs` 两表。
- 前端：`frontend/web/components/saas/TenantTokenUsage.vue`（路由 `/t/:tenant_id/token-usage`）。
- 客户端只能读自己余额：`GET /api/client/v1/credits`、`/credits/detail`（`src/api/client_routes.py:187-235`）。

→ **页面内部口径就不一致**：日均含客户端消耗，每日清单不含。

## 3. 问题定位（agent2 数据实证）

### 3.1 租户归属：脏数据 + 会话归属缺失

DB 层 `client_usage_logs.tenant_id` 近 14 天 **无一行 NULL**（写入侧 `_trusted_tenant_id` 缺失时根本不建 invocation，`proxy_tool.py:75-77` NO_IDENTITY 闸门）。用户感知的"没存租户"实为两类问题：

- **孤儿租户脏数据**：`tenant_t1`（单测硬编码租户，`tests/unit/local_tools/test_proxy_tool.py:31` 等）在 `tenants` 表不存在，却在 agent2 库留下 44 行、44 积分的 boss_tool 计费行（8/31 37 行 boss_greet + 9/1 7 行）。来源：仓库根 `.env` 的 `DATABASE_URL` 直指 agent2 库，本地联调/跑测试把测试租户写进了真实库。后果：任何租户的计费页面都看不到这 44 积分，余额也从未扣减（`UPDATE tenants` 影响 0 行，`balance_after=NULL`），且无任何告警。
- **会话/用户归属缺失**：boss_tool 行 `session_id` 全部为 NULL（实测 9/1 12/12、8/31 boss 行 42/42），无法回答"这次扣费是哪个用户哪次会话触发的"。客户端本机日志（agent-tool-runtime / boss-mcp）则完全没有 tenant 概念。

### 3.2 漏计费：计费挂在轮询侧，会话断开即漏（"保存的日志少了"的主因）

计费只发生在 `proxy_tool._dispatch_and_wait` 轮询循环内（`proxy_tool.py:152-158`）。用户关闭页面 / SSE 断开 / 会话取消 / 云端重启导致轮询协程死亡时，**设备侧照常执行成功，但永远无人计费**（TIMEOUT 时 `request_cancel` 对 running 状态无效，任务仍会跑完）。

对账实证（succeeded 且 effect∈{applied,none} 的可计费 invocation vs 实际计费行，仅真实租户）：

| 日期 | 租户 | 工具 | 成功调用 | 实际计费 | 漏 |
|------|------|------|---------|---------|-----|
| 09-01 | tenant_4f5fd42dfdea（招聘智能体-国腾） | resume_batch / select_job / filter 各 3 | 9 | 3 | **6** |
| 08-31 | tenant_4f5fd42dfdea | 同上各 1 | 3 | 0 | **3** |
| 08-25 | tenant_1dc997a1806b | 各工具合计 | 37 | 0 | **37** |
| 08-18 | tenant_1dc997a1806b | 各工具合计 | 26 | 0 | **26** |

截图注记"做筛选简历花了 14"与页面 8/31 只显示 9.28 的差异即此问题 + 问题 3.4 叠加。另：`state='unknown'`（结果无法确认）目前不收费，属已知豁口，本设计维持不收费（宁可少收不可错收），但补对账报表可见。

### 3.3 命令与参数未存

计费行无法回答"调了哪个命令、参数是什么"：命令名占用 `model` 列（语义错位），参数仅在 `local_tool_invocations.arguments_json`（如 `{"salary":"3-5K","educations":["本科"]}`），与台账无外键、页面上不可见，对账必须手工 join invocation 表。

### 3.4 展示口径：客户端消耗不上页面

`/usage` 与 `daily-detail` 只聚 `chat_records` → boss 工具费（8/31 全库 60.73 积分）与协会 LLM 费（如 official_profile 20.22）都不在每日清单/详情里，但余额照扣、日均照算。

## 4. 设计方案

### 4.0 总原则

1. **客户端永远不上报金额**：金额由服务端按价目表/用量计算。客户端只产生"事实"（调用了什么命令、什么参数、成功与否），计费与扣费是服务端职责。这是协会（代理模式）与 boss（动作模式）既成事实的共性，标准化后对未来客户端同样适用。
2. **统一台账**：所有客户端计费统一写 `client_usage_logs`（tenant_id 必有、binding_id 标识来源客户端、stage 标识动作类别、model 存命令或模型名、credit_cost>0 才算消耗、detail 存命令参数等事实）。遥测行（credit_cost=0，如 stage='log'）不进计费展示。
3. **计费与结果落库同事务**：计费点必须挂在"结果权威落库"处，与业务状态变更原子，杜绝轮询侧漏计费。

### 4.1 P1 计费时机迁移：write_result 同事务幂等计费（止漏，最高优先）

把 BOSS 工具计费从 `proxy_tool` 轮询侧迁到 `repository.write_result`（`src/local_tools/repository.py:441`，Runtime 回写结果的唯一入口，幂等）：

- `write_result` 落 succeeded 终态时，同事务内完成：查价目（`settings.boss_tool_billing.tool_credit_prices`，价格查询函数从 proxy_tool 抽到公共处）→ `UPDATE local_tool_invocations SET credit_cost=%s WHERE id=%s AND credit_cost IS NULL`（影响 0 行 = 已计费，幂等闸门）→ INSERT `client_usage_logs` → `UPDATE tenants SET credit_balance = credit_balance - %s` → 提交。任一步失败整体回滚，Runtime 收到失败后按既有重试重写 result（write_result 对已终态幂等返回），计费随事务自然重试，**不需要额外对账清扫任务**。
- `proxy_tool._bill_success` 退役为"读取已计费金额"：`_map_terminal` 后 invocation 行已带 `credit_cost`（write_result 写入），直接放进 `result.data.credit_cost` 返回给 LLM；不再发起第二次计费。`_tenant_credit_blocked` 余额预检保留。
- 弹层自愈专项费 `boss_overlay_heal` 无独立 invocation，保留 `record_tool_usage` 直记路径（`proxy_tool.py:397-407`）。
- `UPDATE tenants` 影响 0 行（租户不存在）时：不回滚工具结果（先出活再记账的原则不变），但必须 `logger.error` 告警"租户不存在，余额未扣减"，让 tenant_t1 类脏数据当场暴露。
- `state='unknown'` 维持不收费；对账报表中将 succeeded 未计费数固定为 0 作为验收标准。

### 4.2 P2 台账字段补全：命令 + 参数 + 会话/用户归属

不新增列（`detail` JSONB + 既有列够用，免 DDL）：

- **session_id**：`src/core/agent.py:378-382` 给代理工具注入 `execution_args["_session_id"] = self.session_id`（值已在手，:384）；`desktop_agent/gateway.py` 同步补注入。`proxy_tool` 透传给 `record_tool_usage`（签名已支持，:369）。
- **detail JSON 扩展**：`record_tool_usage` 的 detail 从 `{invocation_id, device_id}` 扩为 `{invocation_id, device_id, command: tool_name, arguments: <截断脱敏后参数>, user_id}`。arguments 取 `local_tool_invocations.arguments_json`，截断规则沿用 `proxy_tool` 的 key_info 截断取向：整体序列化 ≤1000 字符，超长截断加省略标记。boss 参数含候选人姓名等个人信息，仅同租户管理员/平台管理员可见（与 chat_records 的 user_message 同权限级别），不脱敏内容只限长度。
- **日志行**：`BOSS工具计费` loguru 行补 `session=`、`user=` 字段（`client_binding_db.py:419`）。
- 管理后台客户端用量页（`/api/saas/client-usage-logs/list`）补显 command/参数摘要列。

### 4.3 P3 计费页面统一口径：chat + client 双表 UNION

- **`/api/saas/billing/usage`**：每日聚合改为 `chat_records UNION ALL client_usage_logs (credit_cost>0)` 子查询后按日 GROUP BY；返回列扩展：`credit_cost`（合计，口径对齐日均）、`chat_credit_cost`、`client_credit_cost`、`session_count`（chat DISTINCT session）、`client_call_count`、`message_count`。summary 同口径 UNION。过滤条件（date/model/session_id）映射到两表各自等价列（client 侧 model 列即命令名）。
- **`/usage/daily-detail`**：返回 `items` 合并两类行，新增 `usage_type: 'chat' | 'client'`；chat 行形状不变；client 行返回 `{time, usage_type, source: stage 中文映射（boss_tool→BOSS 工具 / official_profile→协会采集·官网…）, command: model 列, arguments: detail.arguments, credit_cost, session_id}`。token 三列与 breakdown 仅对 chat 行返回（维持 platform_admin 可见规则）。
- **前端 `TenantTokenUsage.vue`**：详情弹框加「类型」列（智能体对话 / 客户端调用）；client 行以「来源 + 命令 + 参数」替代「会话标题 + 消息」展示，参数超长折叠；每日清单可选增加「客户端调用」列。余额卡片、日均、预估天数逻辑不动（renewal 已是双表口径）。
- 兼容：`GET /api/client/v1/credits/detail` 不动（客户端侧已有自己的明细视图）。

### 4.4 P4 标准接入规范 + 通用上报端点（面向未来客户端）

未来客户端接入计费只有三条标准路径，按"客户端能否联网到服务端"与"动作由谁下发"选择，**禁止**新增第四条（各自写台账/自报金额）：

| 模式 | 适用 | 已有范例 | 计费方式 |
|------|------|---------|---------|
| A 代理模式 | 客户端需要 LLM 能力 | 协会信息收集 `POST /api/client/v1/llm/chat` | 服务端按 token 计费（×租户系数），响应回带 billing |
| B 动作模式 | 客户端执行服务端下发的动作 | boss cli（local tool invocation） | 服务端按价目表对 succeeded 终态计费（P1 迁移后） |
| C 上报模式 | 客户端本地自主执行、不经服务端下发（当前无实例，预留标准口子） | — | 客户端上报事实，服务端按价目计费 |

**C 模式标准端点**：`POST /api/client/v1/usage/report`（Bearer access_token，租户从 binding 解析，与现有 `/api/client/v1/*` 同认证）：

```json
{
  "client_ref_id": "uuid（幂等键，唯一约束防重复上报）",
  "kind": "action",
  "command": "weixin_send_message",
  "arguments_summary": {"target": "张三", "length": 42},
  "quantity": 1,
  "occurred_at": "2026-09-01T12:00:00+08:00",
  "session_id": "可选，客户端本地会话标识",
  "detail": {"任意事实字段，≤2000 字符"}
}
```

- payload **不含金额字段**（服务端按 `client_type × command` 价目表计算，价目进 `settings` 客户端价目配置区）；
- 幂等：`client_ref_id` 建唯一索引（detail JSON 或新列），重复上报返回首次结果；
- 落库：`client_usage_logs`，`stage = '<client_type>_<kind>'`、`model = command`、`binding_id` 为真实 binding，同事务扣减余额；
- 批量：支持 `{reports: [...]}` 数组（≤100 条/批），部分失败逐条返回；
- 该端点为增量开发，A/B 模式客户端不受影响。

**新客户端接入清单**（写进 clients/README）：选模式 → binding 激活拿 access_token（已有 `/api/client/v1/activate`）→ A/B/C 之一对接 → 价目表配置 → 计费页面自动可见（P3 后无需前端改动）。

### 4.5 孤儿租户防御与存量清理

- **写侧防御**（随 P1）：`UPDATE tenants` 影响 0 行即 error 告警（见 4.1）。
- **联调隔离**（流程）：本地开发/测试连接 agent2 真实库是 tenant_t1 污根因，测试需连本地 PG 或专用测试库（`deploy/cleanup-task-plane-aid_work_agent2.sql` 已有同思路）；可选加运行时护栏——非 saas.enabled 环境拒绝写 client_usage_logs 计费行。
- **存量清理**：删除 agent2 库 `tenant_t1` 的 44 行 boss_tool 计费行（附对账截图留档），或按需补建租户——执行前需用户确认，本设计不擅自删数。
- **客户端侧日志**（可选，低优先）：runtime claim 响应回带 tenant_id，本机日志行补租户，便于客户端侧排查（服务端台账不受影响）。

## 5. 实施计划

| Phase | 内容 | 主要改动文件 | 优先级 |
|-------|------|-------------|--------|
| P1 | write_result 同事务幂等计费；_bill_success 退役；租户缺失告警 | `src/local_tools/repository.py`、`src/local_tools/proxy_tool.py`、`src/db/client_binding_db.py`、价目函数抽取 | P0（直接在漏钱） |
| P2 | session_id/user_id/命令参数入台账 + 日志行补全 | `src/core/agent.py`、`src/desktop_agent/gateway.py`、`src/local_tools/proxy_tool.py`、`src/db/client_binding_db.py`、`src/saas/api/client_usage_mgmt.py` | P1 |
| P3 | 计费页面双表 UNION + 详情弹框双类型行 | `src/saas/api/billing_balance.py`、`frontend/web/components/saas/TenantTokenUsage.vue`、`frontend/web/api/billing.ts` | P1 |
| P4 | usage/report 通用上报端点 + 幂等键 + 接入清单文档 | `src/api/client_routes.py`、`src/db/client_binding_db.py`、`clients/README.md` | P2（无实例前可缓） |
| 清理 | agent2 tenant_t1 存量行处理（待确认） | SQL 脚本 + 留档 | 随 P1 |

测试要点：P1 幂等（同 invocation 重复 write_result / 轮询与落库并发只扣一次）、SSE 中断后 result 晚到仍计费、余额原子性；P2 参数截断、desktop 链路 session 注入；P3 两表口径与日均一致（对账 SQL：页面日合计 == chat+client 两表日合计）、credit_cost=0 遥测行不进清单；P4 幂等重放、越权（跨租户 token）拒绝。

## 6. 验收口径

1. 对账 SQL：`succeeded 可计费 invocation 数 == client_usage_logs(boss_tool) 计费行数`（agent2 近 30 天回补后成立，此后每日成立）；
2. 计费页面任一日消耗 == `chat_records + client_usage_logs(credit_cost>0)` 当日合计，与日均口径一致；
3. 任一 boss 计费行可在详情页直接看到：租户、用户、会话、命令、参数、消耗、余额；
4. tenant_t1 类孤儿租户写入时产生 error 告警。
