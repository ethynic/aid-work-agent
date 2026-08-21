---
关联想法: 客户留资（售前咨询）——pre-sales 智能体 + lead-capture 技能
关联设计: docs/channel/wecom_kf/lead-capture-design.md
关联设计(上游): docs/channel/wecom_kf/wecom_kf_design.md
状态: 📋 待开发
创建日期: 2026-08-21
---

# 客户留资（售前咨询）— 开发计划

> 反向关联：业务架构见 [lead-capture-design.md](./lead-capture-design.md)。
>
> **v2.0 开发决策（2026-08-21 已确认）**：
> 1. **四层架构**：渠道=纯对话通道；pre-sales 智能体承载售前；lead-capture 技能可复用；record_lead_capture 工具做确定性动作
> 2. **新建 `pre-sales` 售前咨询智能体**（`subagents/pre-sales/`）
> 3. **`after-sales` 回归纯售后**（移除售前导购内容，Phase 4 与渠道账号切换联动）
> 4. **留资做成 `lead-capture` 技能**，pre-sales 跑通后可被旅游等智能体复用
> 5. **wecom_kf 渠道不实现留资业务逻辑**，仅注入会话状态上下文
> 6. **线索表单独新建** `bs_lead_capture_leads`（中性表名；若坚持渠道命名可 `bs_wecom_kf_leads`，实现相同）
> 7. 员工二维码不区分微信/企微，`kf_account.employee_qr_file_id` 存 ImageRegistry file_id
> 8. 统计做留资报表页（复用引流统计模式），不做定时汇总

---

## 一、需求映射

| 设计文档章节 | 开发需求 | 落地 Phase |
|------------|---------|-----------|
| §三 架构分层 | 渠道/智能体/技能/工具四层落地 | 各 Phase |
| §四.1 pre-sales 智能体 | 新建 `subagents/pre-sales/SUBAGENT.md` | Phase 2 |
| §四.2 after-sales 回归纯售后 | 清理 SUBAGENT.md 售前内容 | Phase 4 |
| §五 lead-capture 技能 | 新建 `src/skills/lead-capture-1.0.0/` | Phase 2 |
| §六 record_lead_capture 工具 | 新建工具（记录 + 防重复 + 返回二维码） | Phase 1 |
| §七 会话状态机 | `channel_sessions.metadata.lead_capture` 注入/读写 | Phase 1 |
| §八 有效客户判定 | 提示词驱动，代码不介入判定 | — |
| §十 员工二维码配置 | `kf_account.employee_qr_file_id` + 图片管线 | Phase 2 |
| §十一 数据闭环 | 线索表 + 即时通知 + 统计报表页 | Phase 1 + 3 |
| §十二 老客户识别 | 会话级防重复（Phase 1）+ 客户级识别（Phase 3） | Phase 1 + 3 |

---

## 二、Phase 划分

```
Phase 1（工具层：留资记录能力）
  → Phase 2（技能 + 智能体：lead-capture 技能、pre-sales 智能体、员工二维码）
  → Phase 3（运营侧：线索管理 + 留资统计报表页）
  → Phase 4（after-sales 回归纯售后 + 租户渠道账号切换）
```

**Phase 1 完成即可验证"留资记录"链路**；**Phase 2 完成售前场景整体可上线**（新建 pre-sales、挂技能、绑渠道账号）；Phase 3 补齐运营管理；Phase 4 拆分 after-sales（涉及生产渠道账号切换，独立排期）。

---

## 三、Phase 1：留资记录能力（工具层）

### 3.1 销售线索表 `bs_lead_capture_leads`

**决策确认**：单独新建，不与既有 `bs_customer_followup_leads`（lead-management 技能）混用。

表结构（登记 `deploy/init-postgres.sql` + `deploy/db_update.sql`）：

```sql
CREATE TABLE IF NOT EXISTS bs_lead_capture_leads (
    id SERIAL PRIMARY KEY,
    lead_id TEXT UNIQUE NOT NULL,          -- lead_lc_<uuid12>
    tenant_id TEXT NOT NULL,               -- 租户隔离
    user_id TEXT,                          -- 渠道侧 customer_user_id（ensure_user_registered 的 user_id）
    customer_user_id TEXT,                 -- 微信侧 external_userid（老客户识别）
    channel_chat_id TEXT,                  -- open_kfid（来源客服账号）
    kf_account_name TEXT,                  -- 客服账号名快照
    contact_method TEXT,                   -- phone | qr
    phone TEXT,                            -- 手机号（加密存储，见 3.1.1）
    contact_name TEXT,                     -- 客户姓名（对话中抽取，可选）
    demand_summary TEXT,                   -- 需求摘要（对话中抽取，可选）
    source TEXT DEFAULT 'lead_capture',    -- 固定来源标识
    stage TEXT DEFAULT 'new',              -- new | contacting | converted | abandoned
    assigned_to TEXT,                      -- 归属员工 user_id（= kf_account.tenant_user_id 快照）
    assignee_name TEXT,                    -- 归属员工姓名快照
    transferred_to TEXT,                   -- 留资后若转人工，记录 servicer_userid
    session_id TEXT,                       -- 产生线索的渠道会话
    lead_created_at TIMESTAMP,             -- 留资成功时间（= created_at，冗余便于排序/统计）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lc_leads_tenant ON bs_lead_capture_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_lc_leads_created ON bs_lead_capture_leads(created_at);
CREATE INDEX IF NOT EXISTS idx_lc_leads_assigned ON bs_lead_capture_leads(assigned_to);
CREATE INDEX IF NOT EXISTS idx_lc_leads_customer ON bs_lead_capture_leads(customer_user_id);
```

**合规**（database_dev.md）：`bs_` 前缀 ✓、`tenant_id` ✓、`user_id` ✓、`created_at` 默认值 ✓。

#### 3.1.1 手机号加密存储

手机号按项目安全原则**加密落库**：复用 `src/core/credential_codec.py`（渠道配置敏感字段同款）。列表/详情接口解密返回（权限内），日志/统计不打印明文。

### 3.2 `record_lead_capture` 工具

**新建** `src/tools/lead_capture/record_lead_capture.py`（`catalog=True`，Catalog 自动发现，无需改 `agent.py`）。

```python
class RecordLeadCaptureInput(BaseModel):
    contact_method: str = Field(..., description="留资方式：phone（客户提供了手机号）| qr（客户选择添加员工微信）")
    phone: Optional[str] = Field(None, description="客户手机号，contact_method=phone 时必填")
    contact_name: Optional[str] = Field(None, description="客户姓名（对话中提取，可选）")
    demand_summary: Optional[str] = Field(None, description="客户基本需求摘要（对话中提取，可选）")
```

**execute 逻辑**：
1. `get_kf_context()` → 无则返回渠道受限失败（与 `transfer_to_human` 渠道隔离同款，防非 wecom_kf 渠道误调用）
2. 校验 `contact_method=phone` 时 `phone` 非空（手机号格式校验，异常引导重提供）
3. **防重复**：读 `channel_session_manager.get_session(session_id).metadata.lead_capture`，已 `captured` → 拒绝「该客户已留资，勿重复引导」
4. 生成 `lead_id`，INSERT `bs_lead_capture_leads`（`assigned_to` 取 `kf_config.tenant_user_id`，`user_id` 取 `kf_context` 的租户侧用户）
5. `update_session(session_id, metadata={"lead_capture": {...}})` 写入状态
6. `contact_method=qr`：从 `kf_config.employee_qr_file_id` 读取员工二维码，返回 `images=[ImageRef]`（Phase 2 启用；无 file_id 返回失败「未配置员工二维码，请仅引导留手机号」）

**description（给 LLM）**：描述"收集到客户手机号或客户选择添加员工微信时调用"，注明"调用后提示客户客服会联系/可添加下方微信"，"该客户已留资过则不要重复调用"。

**注册评估**：若判断渠道无关智能体（如 web 端内部员工）会误调用，考虑 `catalog=False` 走 `control_set.py` 显式装配（与 `transfer_to_human` 的装配策略对齐）；Phase 1 默认 `catalog=True`，工具内渠道隔离兜底。

### 3.3 会话状态注入（渠道层唯一改动）

`src/saas/api/channel_routes.py` `_process_tenant_wecom_kf_messages` 中 `set_kf_context({...})`（约 :2248）追加 `lead_capture` 字段（从已读取的 `session_metadata` 取值，缺省 None）。**渠道层仅此一处改动，不实现任何留资业务逻辑。**

### 3.4 数据访问层

新增 `src/saas/db/lead_capture_db.py`：`WeComLeadDB`（命名中性，如 `LeadCaptureDB`）——`create` / `get_by_id` / `list_by_tenant`（分页，`created_at DESC`）/ `update_stage` / `find_by_customer`（客户级防重复用）/ `stats`（日期段统计，Phase 3）。

---

## 四、Phase 2：lead-capture 技能 + pre-sales 智能体 + 员工二维码

### 4.1 lead-capture 技能

**新建** `src/skills/lead-capture-1.0.0/SKILL.md`（参照 `lead-management` / `after-sales-core` 技能结构）：

```markdown
---
name: lead-capture
description: 客户留资技能：收集客户手机号或引导添加归属员工微信，形成销售线索。
  适用于服务 C 端客户的智能体（售前咨询、旅游咨询等）。
---

# 客户留资技能

## 何时使用
- 客户主动表达购买意向 / 主动索要联系方式
- 客户需求字段（预算/数量/地区/交付时间）覆盖度达标
- 客户主动询问"怎么联系 / 加微信 / 留电话"

## 留资引导流程
1. 判断是否已留资（若本会话已收集过，勿重复）
2. 自然话术引出留资：留手机号 or 添加员工微信（两条都给客户自选）
3. 收集客户手机号 / 确认客户选择加微信 → 调用 record_lead_capture 工具
4. 工具返回成功 → 告知客户"客服专员会尽快联系"

## 话术模板
（留手机号话术 / 添加员工微信话术）
```

**关键**：技能以 SKILL.md 策略为主，**核心动作走 `record_lead_capture` 工具**（子进程脚本无法访问会话上下文）。`init_script` 留空（loader 对 `init_script` 为 Optional）。

**挂载**：pre-sales 智能体 `skills.allowed` 加 `lead-capture`；后续旅游等智能体同样在 `skills.allowed` 加入即可复用。

### 4.2 pre-sales 智能体

**新建** `subagents/pre-sales/SUBAGENT.md`（参照 `travel-consultant` / `after-sales` 格式）：

```yaml
---
name: 售前咨询顾问
description: 面向 C 端客户的售前咨询：产品讲解、需求挖掘、价格/优惠咨询、促成留资
version: 1.0.0
author: system
capabilities:
  - pre_sales_consultation
  - lead_capture
triggers:
  keywords:
    - 多少钱 / 价格 / 优惠 / 怎么买 / 下单 / 咨询产品
tools:
  inherit: true
skills:
  allowed:
    - lead-capture
---
（正文：售前咨询人设 + 留资引导规范 + 调用 record_lead_capture 的指引）
```

**渠道绑定**：租户管理员在渠道配置「客服账号」弹窗把售前账号 `subagent_type` 选为 `pre-sales`（`channel_routes` 按 `kf_config.subagent_type` 路由，无需改代码）。

### 4.3 员工二维码配置 + 图片管线

#### 4.3.1 配置字段

`tenant_channel_configs.config.kf_account[]` 每项新增（**不区分微信/企微，单一字段**）：

```jsonc
{
  "open_kfid": "...",
  "name": "售前客服",
  "tenant_user_id": "绑定员工 user_id",
  "employee_qr_file_id": "file_xxx",       // 新增：员工二维码 ImageRegistry file_id
  "employee_qr_display_name": "员工二维码.png"  // 新增：展示名
}
```

#### 4.3.2 上传/注册链路（复用 ImageRef 管线）

1. 前端 `ChannelConfig.vue` 客服账号编辑弹窗新增「员工二维码」上传
2. 后端 `wecom_kf_account.py` 保存逻辑：接收图片 → 落盘 `storage/tenants/{tenant_id}/avatar/` → `ImageRegistry.register(..., source=..., usage=...)` → `file_id` → 写入 `kf_account.employee_qr_file_id`
3. 下发：`record_lead_capture(qr)` 返回 `ImageRef`；wecom_kf `send_message._send_image_file_as_image` 已支持按 file_id → upload_media → image 消息，微信侧直接显示可长按识别
4. 回显：管理端用 `/api/files/{file_id}/download`

#### 4.3.3 ⚠️ TTL 风险（必须处理）

`ImageRegistry` 对 `tool_generated/web_fetch/user_upload` 的 `inline/embedded` 图有 24h `cleanup_temp` 清理。**员工二维码是长期资产，不能被清**：
- 注册用 `source="knowledge_base"`（`PERMANENT_TTL=-1` 永久，cleanup 不清该 source），语义不完全贴切但实现最简
- 换图/删号时显式清理旧 file_id
- 备选：`ImageRegistry` 增加 `source="channel_asset"`（永久）枚举（需前后端 type 契约同步，成本高，暂缓）

### 4.4 即时通知（Phase 2 配套）

留资成功 → 通知归属客服。复用既有通知机制（企微应用消息 / 管理后台待办，实现时确认现有 `notification` 服务）。非工作时间自动转次日待办。

---

## 五、Phase 3：线索管理 + 留资统计报表页

### 5.1 后端 API

`src/saas/api/external_customers.py` 新增（或独立 `lead_capture.py` 路由）：

```python
@router.get("/lead-stats")        # 留资统计：总留资 / 手机号 vs 二维码 / 客服账号分组
@router.get("/leads")             # 线索列表（分页，created_at DESC，日期段/客服账号/阶段筛选）
@router.get("/leads/{lead_id}")   # 线索详情（含解密手机号）
@router.patch("/leads/{lead_id}") # 更新阶段（new→contacting→converted/abandoned）
```

**统计口径**（对齐引流统计 `CustomerReferralDB.referral_stats`）：
- 总留资数 / 按 `contact_method` 分组 / 按 `assigned_to` 分组 + ratio
- 过滤基准 = `bs_lead_capture_leads.lead_created_at`
- 日期段：`start_date` / `end_date`（含当日，SQL `< 次日` +1 天）
- 权限：普通用户（引流员工）仅见 `assigned_to == 自己` 的线索与统计（复用 `_resolve_visible_kf_ids` 隔离模式）

### 5.2 前端

**入口**：「外部接待客户」页（`ExternalCustomerService.vue`）新增「留资线索」Tab（与引流统计并列），内部两区块：
- **留资统计**：日期段选择（复用 `rangePresets` 7d/30d/custom）+ 统计卡片 + 客服账号分组表
- **留资列表**：BaseTable（客户/手机号/留资方式/客服账号/时间/阶段），支持阶段流转

API 封装走 `getAuthHeader()`（自动带 X-Tenant-Id）。

---

## 六、Phase 4：after-sales 回归纯售后

### 6.1 SUBAGENT.md 清理

`subagents/after-sales/SUBAGENT.md`：
- 身份定位从"兼顾售前导购与订单售后"改为**纯售后**
- 删除「售前导购服务规范」章节（需求挖掘/产品讲解/活动推介/促单引导/下单辅助等）
- **保留**：手机号核验（场景 1/2/3，这是售后查订单必需的身份核验，与销售留资语义不同）、订单/退换货/工单流程
- `triggers` 去掉售前关键词（若含），`business_pages` 保留售后工单/退换货

### 6.2 渠道账号切换（租户配合）

- 租户管理员把售前客服账号 `subagent_type` 从 `after-sales` 切到 `pre-sales`
- 切换后售后账号继续用 `after-sales`
- **风险**：after-sales 改动影响生产售后行为，必须三智能体流程 + 回归 + 真机验证

---

## 七、数据库变更登记

| 文件 | 变更 |
|------|------|
| `deploy/init-postgres.sql` | 新增 `bs_lead_capture_leads` 建表 |
| `deploy/db_update.sql` | 追加 2026-08-21 增量：`CREATE TABLE IF NOT EXISTS bs_lead_capture_leads ...` |

`kf_account.employee_qr_file_id` 为 `tenant_channel_configs.config` JSONB 内字段，无表结构变更。

---

## 八、测试计划

### Phase 1（工具层）
- 单测 `tests/unit/tools/test_record_lead_capture.py`：
  - 工具 schema/name/description
  - `phone` 缺省报错；手机号格式异常
  - 成功写入 `bs_lead_capture_leads`（assigned_to 快照、手机号密文断言）
  - 二次调用（metadata 已 captured）→ 拒绝防重复
  - 非 wecom_kf 渠道（无 kf_context）→ 渠道受限失败
- 单测 `tests/unit/db/test_lead_capture_db.py`：CRUD + 日期段统计 + 权限过滤
- 回归：`test_wecom_kf_adapter.py`、channels 目录、`external_customers` 集成

### Phase 2（技能 + 智能体 + 配置）
- SKILL.md 加载：`SkillRegistry` 发现 `lead-capture`（无 init_script 加载正常）
- `pre-sales/SUBAGENT.md`：loader 解析、`/chat/pre-sales` 路由、skills.allowed 生效
- 渠道配置：上传→落盘→注册→file_id 写入；换图旧 file_id 清理
- `record_lead_capture(qr)` 返回 ImageRef → send_message 图片下发（mock upload_media）
- 前端：`cd frontend && npm run build` 0 错误；员工二维码上传回显

### Phase 3（运营侧）
- `lead-stats`/`leads`/`patch` 接口单测 + 集成；普通用户隔离测试（复用引流统计隔离测试模式）
- 前端 build + 留资 Tab 交互（日期段/下钻/阶段流转）

### Phase 4（after-sales 调整）
- SUBAGENT.md 解析 + 售前内容移除断言
- 相邻回归：after-sales 相关 skill 流程、渠道路由

**每 Phase 完成门槛**：单测全绿（`./scripts/dev_test.sh <新测试> -p no:cacheprovider -q`）+ 相邻回归 + 容器 import 检查 + 前端 build 0 错误 + 三智能体流程。

---

## 九、风险与注意事项

| # | 风险 | 应对 |
|---|------|------|
| 1 | 两张线索表割裂（`bs_lead_capture_leads` vs `bs_customer_followup_leads`） | 用户已确认单独新建；通过 `customer_user_id` 可关联，不产生依赖 |
| 2 | 员工二维码被 ImageRegistry 临时清理（24h TTL） | `source=knowledge_base` 永久语义 + 换图/删号显式清理（§4.3.3） |
| 3 | 提示词判定不可靠（重复/漏留资） | 代码侧防重复兜底（metadata.lead_capture）；漏留资靠管理员调提示词 |
| 4 | `record_lead_capture` 被非 wecom_kf 渠道误调用 | 工具内 `get_kf_context()` 渠道隔离；必要时 `catalog=False` 显式装配 |
| 5 | 手机号明文泄露 | 加密落库、日志不打印、接口解密限权限内 |
| 6 | 客服账号未配置员工二维码 | `record_lead_capture(qr)` 无 file_id → 失败，提示词仅引导留手机号 |
| 7 | after-sales 拆分影响生产售后 | Phase 4 独立排期，三智能体 + 真机验证，租户账号切换配合 |
| 8 | 子进程技能无法访问会话上下文 | 核心动作放工具（record_lead_capture），技能只做策略引导 |

---

## 十、涉及文件清单

| 文件 | 变更 | Phase |
|------|------|-------|
| `src/tools/lead_capture/record_lead_capture.py` | 新增：留资记录工具 | 1 |
| `src/saas/db/lead_capture_db.py` | 新增：`LeadCaptureDB`（CRUD + 统计） | 1 / 3 |
| `src/saas/api/channel_routes.py` | `set_kf_context` 注入 `lead_capture`（渠道层唯一改动） | 1 |
| `deploy/init-postgres.sql` / `deploy/db_update.sql` | 建表 | 1 |
| `src/skills/lead-capture-1.0.0/SKILL.md` | 新增：留资技能 | 2 |
| `subagents/pre-sales/SUBAGENT.md` | 新增：售前咨询智能体 | 2 |
| `src/saas/api/wecom_kf_account.py` | 员工二维码上传/保存 | 2 |
| `frontend/web/components/saas/ChannelConfig.vue` | 客服账号表单加员工二维码上传 | 2 |
| `src/saas/api/external_customers.py` | 新增 lead-stats / leads 接口 | 3 |
| `frontend/web/components/saas/ExternalCustomerService.vue` | 新增「留资线索」Tab | 3 |
| `frontend/web/api/*.ts` | 新增留资 API 封装（X-Tenant-Id） | 3 |
| `subagents/after-sales/SUBAGENT.md` | 清理售前内容，回归纯售后 | 4 |

---

## 十一、与现有功能的关系

- **转人工**（`transfer_to_human.py`）：`record_lead_capture` 的参照实现——metadata 持久化 + get_kf_context 渠道隔离 + system 标记消息，直接复用其模式
- **引流归因**（`customer_referrals`）：留资统计报表的模式参照；归因（客户从哪进）与留资（进来后如何转化）上下游关系，通过 `customer_user_id` 关联
- **lead-management 技能**（`bs_customer_followup_leads`）：既有线索能力，本次独立新建不混用，避免数据割裂风险
- **after-sales / travel-consultant 等智能体**：skills.allowed 挂载 `lead-capture` 即可复用留资能力
- **wecom_kf 渠道**：仅 `set_kf_context` 注入会话状态，不承载留资业务逻辑
