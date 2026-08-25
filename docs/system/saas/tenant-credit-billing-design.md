# 租户积分充值与计费 - 设计与开发计划

> 编号：#37（系统功能）
> 状态：📋 待开发
> 创建日期：2026-07-16
> 关联：[docs/system/saas/multi-tenant-saas-design.md](./multi-tenant-saas-design.md)、[docs/system/database_system_table.md](../database_system_table.md)、[LLM 计费接入设计](./llm-billing-integration-design.md)（Embedding/ASR/视频提示词/background_runner/对话内后台 LLM 计费接入）

---

## 1. 背景与目标

### 1.1 业务目标

为租户建立预付费积分（credit）充值与消费机制：

- **充值**：平台管理员在管理后台手动给租户充值，按"金额（元）→ 积分"转化（默认 1 元 = 10 积分，可手动调整）。充值记录不可修改，仅可删除（删除回扣余额）。未来对接在线支付时，支付完成自动生成充值记录。
- **消费**：智能体每轮对话消耗积分。积分用量由 token 成本价 × 用量系数（默认 100）向上取整得出。
- **余额查询与账单**：租户管理员可查看积分余额、用量明细、充值记录。
- **余额报警**：余额 ≤ 0 红色报警并阻止使用；余额 ≤ 100 绿色提醒（每用户每天一次）。

### 1.2 与现有订阅计费的关系

项目已存在 `subscriptions` / `payment_orders` 表与 `/api/saas/billing/*` 订阅计费骨架（`src/saas/api/subscriptions.py`），但**无预付费余额/扣费机制**。本期走"预付费积分充值"路线，与订阅并行：

- `payment_orders` 表预留复用（Phase 5 在线支付时作为订单载体）
- `subscriptions` 表本期不动
- 新增 `tenant_recharges` 表记录充值流水，新增 `tenants.credit_balance` 字段记余额

### 1.3 设计原则

- **稳定可预测**：扣费链路同步执行，失败则对话失败（不静默放过）
- **数据可审计**：充值流水不可改、扣费记录随 `chat_records` 永久留存
- **并发安全**：余额扣减用原子 UPDATE，不加锁
- **本期最小可用**：手动充值 + 同步扣费 + 余额报警，在线支付留 Phase 5

---

## 2. 现状分析

### 2.1 已具备的能力（可直接复用）

| 能力 | 位置 | 说明 |
|------|------|------|
| token 用量采集 | `chat_records` 表 | 已有 `prompt_tokens` / `completion_tokens` / `cached_input_tokens` / `model` / `tenant_id` / `session_id` / `created_at` 字段 |
| token 单价表 | `token_cost_prices` | 字段 `model_name`(UNIQUE) / `input_price_per_m` / `output_price_per_m`，**无 tenant_id**（全平台统一价），无 `model_code` |
| 写入服务 | `src/services/session_record.py:74` | `chat_records` 写入入口，本期扣费在此挂载 |
| 平台侧 token 统计页 | `frontend/src/components/saas/PlatformTokenUsage.vue` + `/api/admin/token-usage` | 平台管理员视角，本期不动 |
| 租户侧 token 用量页 | `frontend/src/components/saas/TenantTokenUsage.vue` + `/api/saas/reports/token-details` | 列「输入Token (M)」「输出Token (M)」，本期改造为「消耗积分」 |
| 平台管理员菜单 | `frontend/src/components/saas/PortalLayout.vue:201-221` `portalMenu` 数组 | 新增「租户充值」菜单入口 |
| 租户管理 API | `src/saas/api/tenant_mgmt.py`（prefix `/api/saas/tenants`） | 平台管理员对租户的 CRUD 已具备 |

### 2.2 缺失的能力（本期新建）

| 能力 | 缺失内容 |
|------|---------|
| 租户余额 | `tenants` 表无 `credit_balance` 字段 |
| 充值流水 | 无 `tenant_recharges` 表 |
| 扣费记录 | `chat_records` 无 `credit_cost` 字段 |
| 用量系数配置 | `configs/config.yaml` 无 `billing` 段 |
| 充值管理 API | 无 `/api/saas/billing/recharges/*` |
| 租户余额/账单 API | 无 `/api/saas/billing/balance` / `/usage` / `/recharges` |
| 余额报警 | 登录/新会话/提交问题三入口无余额检查 |

### 2.3 ⚠️ 字段命名差异（重要）

用户原始需求中提到 `token_cost_prices.input_price_per_m` / `output_price_per_m`，**字段名正确**。但需求中暗示用 `model_code` 关联——**实际表中是 `model_name`**，关联方式为：

```sql
chat_records.model = token_cost_prices.model_name
```

读取代码见 `src/db/models.py:1270、1278`（`LEFT JOIN token_cost_prices tcp ON cr.model = tcp.model_name`）。本期沿用此关联。

---

## 3. 数据模型设计

### 3.1 `tenants` 表新增字段

```sql
-- deploy/init-postgres.sql: tenants 表 CREATE 内追加
credit_balance INTEGER NOT NULL DEFAULT 0

-- deploy/db_update.sql: 增量
-- 2026-7-16，tenants 表新增 credit_balance 字段，记录租户积分余额
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS credit_balance INTEGER NOT NULL DEFAULT 0;
```

- 整数积分，单位"分"（非货币分，是 credit 分数）
- 允许透支为负数（对话中扣完不中断，下一轮入口拦截）
- 同步更新 `src/saas/db/tables.py` 中 `tenants` 表定义

### 3.2 新建表：`tenant_recharges`（租户充值记录）

```sql
-- deploy/init-postgres.sql 新增
CREATE TABLE IF NOT EXISTS tenant_recharges (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    amount_yuan NUMERIC(10,2) NOT NULL,          -- 充值金额（元）
    credits INTEGER NOT NULL,                    -- 转化积分
    rate INTEGER NOT NULL,                       -- 兑换系数（credits / amount_yuan，默认 10）
    source TEXT NOT NULL DEFAULT 'manual',       -- manual / online_payment
    payment_order_id TEXT,                       -- 关联 payment_orders.order_id，manual 时为 NULL
    operator_id TEXT,                            -- 平台管理员 user_id（manual 必填）
    operator_name TEXT,                          -- 平台管理员姓名（冗余，便于审计）
    remark TEXT,                                 -- 备注
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_tenant_recharges_tenant_id ON tenant_recharges(tenant_id);
CREATE INDEX idx_tenant_recharges_created_at ON tenant_recharges(created_at DESC);
```

- **不可修改**：API 层不提供 PUT 接口
- **删除即回扣**：DELETE 时 `tenants.credit_balance -= credits`（原子 UPDATE）
- 透支允许：删除后余额可能为负，由下一轮入口拦截

### 3.3 `chat_records` 表新增字段

```sql
-- deploy/init-postgres.sql: chat_records 表 CREATE 内追加
credit_cost INTEGER NOT NULL DEFAULT 0

-- deploy/db_update.sql: 增量
-- 2026-7-16，chat_records 表新增 credit_cost 字段，记录该轮对话消耗的积分
ALTER TABLE chat_records ADD COLUMN IF NOT EXISTS credit_cost INTEGER NOT NULL DEFAULT 0;
```

- 历史数据 `credit_cost = 0`（不追溯重算）
- 同步更新 `src/db/models.py:968` `ChatRecordDB` 类

### 3.4 配置项：用量系数

```yaml
# configs/config.yaml 新增 billing 段
billing:
  usage_factor: 100  # 用量系数，token 成本价 × 系数 = 积分用量（向上取整）
```

- 同步 `src/config/settings.py` 增加 `billing_usage_factor` 字段，默认 100
- 系数变更只影响新对话，历史 `chat_records.credit_cost` 不变

### 3.5 表分类

| 表 | 分类 | 说明 |
|----|------|------|
| `tenants` | 系统表 | 已有，本期加字段 |
| `chat_records` | 系统表 | 已有，本期加字段 |
| `tenant_recharges` | 业务数据表（`bs_` 规则豁免） | 充值流水，平台级而非子智能体级，**不带 `bs_` 前缀**（参考 `subscriptions` / `payment_orders` 命名惯例） |

> 注：`tenant_recharges` 不属于"子智能体业务数据"范畴（属于 SaaS 平台级计费表），沿用 `subscriptions` 命名风格，不带 `bs_` 前缀。但需包含 `tenant_id` 字段实现租户隔离。

---

## 4. 计费扣费链路

### 4.1 算法

```
token_cost = prompt_tokens × tcp.input_price_per_m + completion_tokens × tcp.output_price_per_m
credit_cost = math.ceil(token_cost × usage_factor)
```

- 单价单位：元/百万 token（`_per_m` 后缀）
- `cached_input_tokens` 本期不计入（用户明确暂不计算）
- `token_cost_prices` 无匹配记录时，`credit_cost = 0`（不阻断对话，记 warning 日志）

### 4.2 挂载点

**位置**：`src/services/session_record.py:74` `chat_records` 写入处

**改造**：

```python
import math
from src.config.settings import create_settings
from src.db.models import ChatRecordDB, TokenCostPriceDB  # 假设已有 ORM

# 写入 chat_records 前/同步：
def calculate_credit_cost(prompt_tokens, completion_tokens, model):
    settings = create_settings()
    usage_factor = settings.billing_usage_factor  # 默认 100
    tcp = TokenCostPriceDB.get_by_model_name(model)  # 新增查询方法
    if not tcp:
        logger.warning(f"计费：模型 {model} 未配置单价，credit_cost=0")
        return 0
    token_cost = (
        prompt_tokens * tcp.input_price_per_m
        + completion_tokens * tcp.output_price_per_m
    )
    return math.ceil(token_cost * usage_factor)

# 写入 chat_records 时：
credit_cost = calculate_credit_cost(prompt_tokens, completion_tokens, model)
ChatRecordDB.create(..., credit_cost=credit_cost)

# 原子扣减余额（同事务或紧随其后）：
db.execute(
    "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s",
    (credit_cost, tenant_id)
)
```

### 4.3 并发安全

- `UPDATE tenants SET credit_balance = credit_balance - %s` 是原子操作，多用户同时对话不会丢更新
- **不使用** `SELECT FOR UPDATE`（无必要，且会引入死锁风险）
- 扣费失败（如数据库异常）时：对话已完成的 token 已消耗，记 error 日志，不回滚对话结果；余额可能不扣，由对账脚本兜底（Phase 5 考虑）

### 4.4 余额透支策略

- 扣费时不检查余额（允许透支到负数）
- 入口检查（登录/新会话/提交问题）拦截余额 ≤ 0 的租户
- 优点：对话进行中余额用完不中断，用户体验好；下一轮入口拦截

---

## 5. 充值管理（平台管理员）

### 5.1 后端 API

新增路由文件：`src/saas/api/billing_recharges.py`，prefix `/api/saas/billing/recharges`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/list` | 充值记录列表，支持 `tenant_id` / `date_from` / `date_to` 筛选，分页，按 `created_at DESC` |
| POST | `/` | 创建充值记录（手动），同步 `tenants.credit_balance += credits` |
| DELETE | `/{recharge_id}` | 删除充值记录，同步 `tenants.credit_balance -= credits`（回扣） |
| GET | `/stats` | （可选）汇总：总充值金额、总积分、最近 7 天趋势 |

**POST 请求体**：

```json
{
  "tenant_id": "t_xxx",
  "amount_yuan": 1000.00,
  "credits": 10000,
  "rate": 10,
  "remark": "首次充值"
}
```

- `credits` 若未传，按 `amount_yuan × rate` 自动计算
- `rate` 默认 10
- `source = 'manual'`，`operator_id` / `operator_name` 从当前登录的平台管理员取

**DELETE 校验**：

- 二次确认由前端 `confirm()` 完成
- 后端删除时事务内回扣余额：`DELETE FROM tenant_recharges ... + UPDATE tenants SET credit_balance = credit_balance - credits`
- 不校验余额是否够扣（允许变负）

**权限**：仅 `platform_admin` 角色可访问（复用现有 `require_platform_admin` 依赖）

### 5.2 前端页面

**新增组件**：`frontend/src/components/saas/TenantRecharge.vue`

- **列表页**：`BaseTable` 展示租户名 / 充值金额 / 转化积分 / 兑换系数 / 来源 / 操作人 / 备注 / 创建时间 / 操作（删除）
- **新增弹框**：`BaseModal`（size=`md`）
  - 字段：租户（`BaseSelect`，必填）、充值金额（元，`BaseInput` number）、兑换系数（默认 10）、转化积分（自动算 = 金额 × 系数，可手动改，`BaseInput` number）、备注（`textarea`）
  - 金额或系数变化时，积分自动重算（除非用户手动改过积分）
- **菜单注册**：`PortalLayout.vue` 的 `portalMenu` 数组新增 `{ key: 'recharge', label: '租户充值', icon: '...' }`
- **路由注册**：`frontend/src/router/portalRoutes.ts` 新增 `/portal/recharge` -> `TenantRecharge.vue`

---

## 6. 余额查询与用量明细（租户管理员）

### 6.1 后端 API

新增路由文件：`src/saas/api/billing_balance.py`，prefix `/api/saas/billing`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/balance` | 返回当前租户积分余额 + 预估可用天数（近 7 天日均消耗） |
| GET | `/usage` | 用量明细列表，支持日期/会话/模型筛选，分页，返回 `credit_cost` 聚合 |
| GET | `/recharges` | 本租户充值记录列表（只读），分页 |

**`/balance` 响应**：

```json
{
  "credit_balance": 8500,
  "daily_avg_cost_7d": 500,
  "estimated_days_left": 17
}
```

**`/usage` 响应**（按日聚合，参考现有 `/api/saas/reports/token-details`）：

```json
{
  "items": [
    { "date": "2026-07-15", "credit_cost": 1200, "session_count": 15, "message_count": 42 },
    ...
  ],
  "total": 30
}
```

**权限**：`tenant_admin` / `regular_user` 均可查自己租户；`platform_admin` 带 `X-Tenant-Id` 可查指定租户

### 6.2 前端改造

#### 6.2.1 改造 `TenantTokenUsage.vue`

- 改名为「积分用量」
- **移除列**：`输入Token (M)` / `输出Token (M)`（按用户要求不对租户透出）
- **新增列**：`消耗积分`（取 `credit_cost`）
- API 从 `/api/saas/reports/token-details` 切换到 `/api/saas/billing/usage`（或原 API 增加返回 `credit_cost` 字段，前端切换显示列）
- 平台侧 `PlatformTokenUsage.vue` 在显示 token 明细 + 成本 的基础上，增加 `消耗积分`（取 `credit_cost`）

#### 6.2.2 新增「积分余额」展示

- 选项 A（推荐）：在租户管理员侧边栏顶部或「积分用量」页顶部用 `BaseCard` 展示余额 + 预估天数
- 选项 B：新增独立菜单「积分余额」-> 单卡片页
- 本期选 A，减少菜单膨胀

#### 6.2.3 新增「充值记录」页（租户只读）

- 路径：`/portal/recharge-records`（与平台管理员的 `/portal/recharge` 区分）
- 组件：`TenantRechargeRecords.vue`，`BaseTable` 只读列表
- 菜单：`PortalLayout.vue` `portalMenu` 新增（仅 `tenant_admin` 可见）

---

## 7. 余额报警（用户侧）

### 7.1 三入口检查

| 入口 | 检查时机 | 行为 |
|------|---------|------|
| 登录 | 登录成功后立即调 `/api/saas/billing/balance` | 余额 ≤ 0：红色报警 + 阻止进入主界面（或进入但禁用对话）；余额 ≤ 100：绿色提醒（每用户每天一次） |
| 打开新会话 | 点击「新建会话」按钮 | 余额 ≤ 0：红色报警 + 不创建会话；余额 ≤ 100：绿色提醒（每用户每天一次） |
| 提交问题 | 点击发送按钮，请求 `/api/chat/*` 前 | 余额 ≤ 0：红色报警 + 不发送；余额 ≤ 100：绿色提醒（每用户每天一次） |

### 7.2 报警文案

- **余额 ≤ 0（红色）**：「积分余额耗尽，数字员工无法工作」
- **余额 ≤ 100（绿色）**：「积分余额即将耗尽，请联系管理员尽快充值」

### 7.3 每用户每天一次的去重

**方案**：前端 `localStorage` 存 `credit_low_warn_{userId}_{YYYY-MM-DD}`

- 提醒展示后写入标记
- 当天再次触发时检查标记，存在则跳过
- 跨天自动失效（key 含日期）
- **红色报警不做去重**（每次都报，因为已阻断使用）

**优点**：无需后端表，实现简单
**缺点**：清缓存/换设备会重复提醒——可接受（绿色提醒本就是友好提示）

### 7.4 后端入口校验（防绕过）

前端拦截可被绕过（直接调 API），后端在以下位置加余额检查：

- `/api/chat/*` 提交问题的处理函数入口：`if tenant.credit_balance <= 0: return {"success": False, "error": "积分余额耗尽，数字员工无法工作"}`
- 渠道侧（wecom/dingtalk/feishu）消息入口同样加检查

**注意**：余额 ≤ 0 是硬阻断；余额为负也阻断（对话中扣完透支，下一轮拦）。

---

## 8. 风险与决策

| 风险/决策点 | 选择 | 理由 |
|------------|------|------|
| 余额存储方式 | `tenants.credit_balance` 字段（非实时聚合） | 性能优，并发安全靠原子 UPDATE |
| 扣费时机 | 同步，写 `chat_records` 同事务 | 保证一致性；扣费失败记日志不阻断对话 |
| 透支策略 | 允许透支，入口拦截 | 对话不中断，体验好 |
| 充值删除 | 硬删除 + 回扣余额 | 用户明确要求"不能修改，可删除" |
| 用量系数 | 配置文件，不进 DB | 本期无动态调整需求；Phase 5 若需按租户配置再扩展 |
| 每日提醒去重 | 前端 localStorage | 无需后端表，简单可靠 |
| token 单价表无 tenant_id | 沿用全平台统一价 | 当前业务无按租户定价需求 |
| `cached_input_tokens` | 本期不计费 | 用户明确暂不计算，Phase 5 再加 |

---

## 9. 开发任务拆分

### Phase 1：数据基础 + 扣费链路（核心）

**后端**：
- [ ] `deploy/init-postgres.sql` + `deploy/db_update.sql`：`tenants` 加 `credit_balance`、`chat_records` 加 `credit_cost`、新建 `tenant_recharges` 表
- [ ] `src/saas/db/tables.py`：同步 `tenants` 表定义
- [ ] `src/db/models.py`：`ChatRecordDB` 加 `credit_cost` 字段处理；新增 `TenantRechargesDB` ORM 类
- [ ] `configs/config.yaml` + `src/config/settings.py`：新增 `billing.usage_factor`
- [ ] `src/services/session_record.py`：挂载扣费逻辑（算 `credit_cost` + 写 `chat_records` + 原子扣 `tenants.credit_balance`）
- [ ] 单测：`tests/unit/test_credit_billing.py` 覆盖计费算法、单价缺失、扣费原子性

**验收**：手动跑一轮对话，检查 `chat_records.credit_cost` 写入正确、`tenants.credit_balance` 扣减正确。

### Phase 2：充值管理（平台管理员）

**后端**：
- [ ] `src/saas/api/billing_recharges.py`：`/api/saas/billing/recharges` 的 list / POST / DELETE
- [ ] `src/main.py`：注册新路由
- [ ] 单测：`tests/integration/test_billing_recharges_api.py` 覆盖创建/删除/回扣/权限

**前端**：
- [ ] `frontend/src/api/billing.ts`：新增 API 封装
- [ ] `frontend/src/components/saas/TenantRecharge.vue`：列表 + 新增弹框 + 删除
- [ ] `PortalLayout.vue`：菜单注册
- [ ] `portalRoutes.ts`：路由注册
- [ ] 前端构建：`cd frontend && npm run build` 必须 0 错误

**验收**：平台管理员能充值、列表显示、删除后余额回扣正确。

### Phase 3：余额查询 + 用量明细（租户管理员）

**后端**：
- [ ] `src/saas/api/billing_balance.py`：`/balance` / `/usage` / `/recharges`
- [ ] `src/main.py`：注册新路由
- [ ] 单测：`tests/integration/test_billing_balance_api.py`

**前端**：
- [ ] 改造 `TenantTokenUsage.vue`：移除 token 列，新增「消耗积分」列
- [ ] 新增「积分余额」卡片展示（顶部 `BaseCard`）
- [ ] 新增 `TenantRechargeRecords.vue`：租户只读充值记录
- [ ] 菜单 + 路由注册

**验收**：租户管理员能看到余额、用量明细（仅积分）、充值记录。

### Phase 4：余额报警（用户侧）

**前端**：
- [ ] `useAgent.ts` / 登录流程：三入口余额检查
- [ ] 红色报警组件（阻断操作）
- [ ] 绿色提醒组件（localStorage 去重）
- [ ] 调用 `/api/saas/billing/balance` 轻量接口

**后端**：
- [ ] `/api/chat/*` 入口加余额硬阻断
- [ ] 渠道侧消息入口加余额硬阻断

**验收**：余额 ≤ 0 三入口红色报警 + 阻断；余额 ≤ 100 绿色提醒每用户每天一次。

### Phase 5（可选，未来）：在线支付对接

- 复用 `payment_orders` 表作为订单载体
- 对接微信支付 / 支付宝
- 支付完成回调后自动生成 `tenant_recharges` 记录（`source = 'online_payment'`，`payment_order_id` 关联）
- 本期不实现，仅表结构预留

---

## 10. 验收清单

### 10.1 功能验收

- [ ] 平台管理员手动充值 1000 元 → 10000 积分，租户余额 +10000
- [ ] 删除该充值记录，租户余额 -10000
- [ ] 充值记录不可编辑（无 PUT 接口）
- [ ] 租户用户对话一轮，`chat_records.credit_cost` 正确写入，`tenants.credit_balance` 同步扣减
- [ ] 模型无单价时 `credit_cost = 0`，对话不中断
- [ ] 余额 ≤ 0：登录/新会话/提交问题三入口红色报警 + 阻断
- [ ] 余额 ≤ 100：绿色提醒，同用户同天第二次不重复
- [ ] 租户管理员看到余额、用量明细（仅积分列）、充值记录
- [ ] 租户管理员看不到「输入Token (M)」「输出Token (M)」列
- [ ] 平台管理员仍可在 `PlatformTokenUsage.vue` 看 token 明细 + 成本

### 10.2 非功能验收

- [ ] 并发对话不丢扣费更新（原子 UPDATE 验证）
- [ ] 扣费失败不阻断对话（记 error 日志）
- [ ] 前端 `npm run build` 0 错误
- [ ] 后端 `./scripts/dev_test.sh tests/unit/test_credit_billing.py tests/integration/test_billing_*.py` 全绿
- [ ] 启动安全：`docker exec aid-agent-api python -c "from src.saas.api.billing_recharges import router; from src.saas.api.billing_balance import router"` 无异常

### 10.3 文档登记

- [ ] `docs/ideas.md` 「系统功能」分区登记 #37 条目
- [ ] 开发完成后移动到 `docs/ideas_finished.md`

---

## 11. 积分用量 及 积分余额 的重算脚本

如果因为计算逻辑变更（例如增加了命中缓存单价计算）、或基础数据修改（例如token单价有误做了更正），导致 `chat_records.credit_cost` 和 `tenants.credit_balance` 有出入，可使用以下脚本重算：

```
-- 1. 重算 chat_records.credit_cost（指定租户）
--    注意：usage_factor=100 硬编码，若 configs/config.yaml 中 billing.usage_factor 不是 100，需替换
--    credit_cost 表是 chat_records，不是 token_cost_prices（后者是单价表，无 credit_cost 字段）
UPDATE chat_records cr
SET credit_cost = COALESCE(
    (
        SELECT
            CASE
                -- 单价全为 0 -> 0（与 Python 逻辑一致）
                WHEN COALESCE(tcp.input_price_per_m, 0) <= 0
                  AND COALESCE(tcp.output_price_per_m, 0) <= 0 THEN 0
                -- 区分缓存命中
                WHEN tcp.cached_input_price_per_m IS NOT NULL THEN
                    CEIL(
                        (GREATEST(cr.prompt_tokens - COALESCE(cr.cached_input_tokens, 0), 0) * tcp.input_price_per_m
                          + COALESCE(cr.cached_input_tokens, 0) * tcp.cached_input_price_per_m
                          + cr.completion_tokens * tcp.output_price_per_m) / 1000000.0 * 100
                    )::INT
                -- 不区分缓存命中
                ELSE
                    CEIL(
                        (cr.prompt_tokens * tcp.input_price_per_m
                          + cr.completion_tokens * tcp.output_price_per_m) / 1000000.0 * 100
                    )::INT
            END
        FROM token_cost_prices tcp
        WHERE tcp.model_name = cr.model
    ),
    0
)
WHERE cr.tenant_id = 'tenant_9eb3e45cab83';

-- 2. 重算 tenants.credit_balance = 充值累计 - 积分用量累计
--    credit_balance 允许透支为负（init-postgres.sql:502 注释），不加 GREATEST(0)
UPDATE tenants t
SET credit_balance =
    COALESCE((SELECT SUM(credits)   FROM tenant_recharges WHERE tenant_id = t.tenant_id), 0)
  - COALESCE((SELECT SUM(credit_cost) FROM chat_records   WHERE tenant_id = t.tenant_id), 0)
WHERE t.tenant_id = 'tenant_9eb3e45cab83';
```