---
关联想法: wecom_kf 售前咨询客户留资（手机号 / 员工微信二维码）
关联设计: docs/channel/wecom_kf/lead-capture-design.md
关联设计(上游): docs/channel/wecom_kf/wecom_kf_design.md
状态: 📋 待开发
创建日期: 2026-08-21
---

# 微信客服（wecom_kf）售前咨询客户留资 — 开发计划

> 反向关联：业务逻辑设计见 [lead-capture-design.md](./lead-capture-design.md)。本计划给出代码落地阶段的挂载位置、表结构、图片管线与统计报表实现方案。
>
> **本次开发决策（2026-08-21 已确认）**：
> 1. **留资状态机挂载**：`channel_sessions.metadata.lead_capture`（JSONB），与转人工 `service_state` 同模式；判定由智能体提示词驱动，代码只记录结果 + 防止重复（见 §3.2）
> 2. **销售线索表**：新建 `bs_wecom_kf_leads`（用户确认；§3.1 标注与既有 `bs_customer_followup_leads` 的关系）
> 3. **员工二维码图片管线**：复用 ImageRef 管线，`file_id` 存客服账号配置（见 §3.4，含 TTL 风险提示）
> 4. **统计**：不做定时汇总，做**留资统计报表**页面，按时间段统计，模式复用「外部接待客户」-「引流统计」Tab（见 §3.5）
> 5. **二维码**：不区分微信/企微，单一「员工二维码」字段

---

## 一、需求映射

| 设计文档章节 | 开发需求 | 落地 Phase |
|------------|---------|-----------|
| §五 有效客户判定 | 判定由租户管理员改提示词决定，代码不介入 | Phase 1（仅记录） |
| §五.3 客户主动发起 | 最高优先级信号，提示词控制，代码不设门槛 | Phase 1 |
| §六 留资方式选择 | 手机号/二维码两条话术，提示词 + 工具配合 | Phase 1 + 2 |
| §七 二维码配置 | 客服账号下上传员工二维码 | Phase 2 |
| §八 留资数据闭环 | 线索记录 + 即时通知 + 统计报表（替代定时汇总） | Phase 1 + 3 |
| §九 老客户识别 | 已留资会话不重复引导 | Phase 1（防重复） |
| §十 边界情况 | 拒绝/中途离开/重复留资/未配置二维码等 | 各 Phase |

---

## 二、Phase 划分

```
Phase 1（留资记录能力）→ Phase 2（员工二维码 + 留资话术）→ Phase 3（线索管理 + 统计报表）
```

**Phase 1 即可上线"留手机号"场景**（仅改提示词 + 记录能力），Phase 2 上线"引导加员工微信"，Phase 3 上线运营侧管理。

---

## 三、详细设计

### 3.1 销售线索表 `bs_wecom_kf_leads`

**⚠️ 重要发现**：项目已存在 `bs_customer_followup_leads` 表（`src/skills/lead-management-1.0.0`，客户跟进智能体的线索主表），字段高度匹配（`phone`/`source`/`stage`/`assigned_to`/`status`/`external_id`），`source` 枚举已含 `referral`。用户决策**新建**本表。二者取舍：

- **新建（当前决策）**：数据独立、不依赖 lead-management skill 是否启用，但存在两张线索表数据割裂风险。
- **复用（备选建议）**：直接写 `bs_customer_followup_leads`，`source='wecom_kf'` + `external_id=customer_user_id`，数据统一、少维护一张表。若后续决定复用，仅需调整 INSERT 目标表，工具与 API 逻辑不变。

> 建议在 Phase 1 开工前与用户最终确认一次；本计划主体按「新建」设计。

**表结构**（新建，登记 `deploy/init-postgres.sql` + `deploy/db_update.sql`）：

```sql
CREATE TABLE IF NOT EXISTS bs_wecom_kf_leads (
    id SERIAL PRIMARY KEY,
    lead_id TEXT UNIQUE NOT NULL,          -- lead_kf_<uuid12>
    tenant_id TEXT NOT NULL,               -- 租户隔离
    user_id TEXT,                          -- 留资时记录的渠道侧 customer_user_id（即 ensure_user_registered 的 user_id）
    customer_user_id TEXT,                 -- 微信侧 external_userid
    channel_chat_id TEXT,                  -- open_kfid（客服账号）
    kf_account_name TEXT,                  -- 客服账号名快照（账号改名不回写历史）
    contact_method TEXT,                   -- contact_method 枚举: phone | qr
    phone TEXT,                            -- 手机号（加密存储，见 §3.1.1），contact_method=phone 时必填
    contact_name TEXT,                     -- 客户姓名（可选，对话中抽取）
    demand_summary TEXT,                   -- 需求摘要（对话中抽取，可选）
    source TEXT DEFAULT 'wecom_kf_lead',   -- 固定来源标识
    stage TEXT DEFAULT 'new',              -- new | contacting | converted | abandoned
    assigned_to TEXT,                      -- 归属员工 user_id（= kf_account.tenant_user_id 快照）
    assignee_name TEXT,                    -- 归属员工姓名快照
    transferred_to TEXT,                   -- 若留资后转人工，记录 servicer_userid
    session_id TEXT,                       -- 产生线索的渠道会话
    lead_created_at TIMESTAMP,             -- 留资成功时间（= created_at，冗余便于按此排序）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_wkf_leads_tenant ON bs_wecom_kf_leads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_wkf_leads_created ON bs_wecom_kf_leads(created_at);
CREATE INDEX IF NOT EXISTS idx_wkf_leads_assigned ON bs_wecom_kf_leads(assigned_to);
```

**必需字段合规**（database_dev.md）：`bs_` 前缀 ✓、`tenant_id` ✓、`user_id` ✓、`created_at` 带默认值 ✓。

#### 3.1.1 手机号加密存储

手机号属敏感信息，按项目安全原则**加密存储**。复用 `src/core/credential_codec.py` 的加密能力（渠道配置敏感字段同款），字段存密文。列表/详情接口读取时解密返回（权限内），日志/统计不打印明文。

### 3.2 留资状态机挂载（核心决策）

**结论：状态机不放在 Agent 循环内部，而是「会话元信息持久化 + 工具读写」两层。**

#### 3.2.1 持久化位置：`channel_sessions.metadata.lead_capture`

与转人工完全同模式（`transfer_to_human.py` 用 `update_session(metadata={"service_state": 3, ...})`）：

```jsonc
// channel_sessions.metadata.lead_capture
{
  "stage": "captured",              // captured（已留资）| none（未留资，缺省）
  "lead_id": "lead_kf_xxxx",        // 已留资时的线索 ID
  "contact_method": "phone",        // phone | qr
  "captured_at": "2026-08-21 10:30:00",
  "session_id": "tenant_xxx..."     // 冗余，便于核对
}
```

**探索→判定→留资→完成 的阶段推进不落库**：判定标准由租户管理员改提示词决定（设计 §五），代码无法也不应做硬判定。代码只负责：
- **读**：消息进入时把 `lead_capture` 注入 `kf_context`，供 `record_lead_capture` 工具判断是否已留资
- **写**：留资成功时写入线索 + 更新 `metadata.lead_capture`

#### 3.2.2 新增工具 `record_lead_capture`（catalog=True，走 Catalog 自动发现）

```python
class RecordLeadCaptureInput(BaseModel):
    contact_method: str = Field(..., description="留资方式：phone（客户提供了手机号）| qr（客户选择添加员工微信）")
    phone: Optional[str] = Field(None, description="客户手机号，contact_method=phone 时必填")
    contact_name: Optional[str] = Field(None, description="客户姓名（对话中提取，可选）")
    demand_summary: Optional[str] = Field(None, description="客户基本需求摘要（对话中提取，可选）")
```

**execute 逻辑**：
1. `get_kf_context()` → 无则返回渠道受限失败（复用转人工的渠道隔离模式）
2. 校验 `contact_method=phone` 时 `phone` 非空
3. **防重复**：读 `channel_session_manager.get_session(session_id)` 的 `metadata.lead_capture`，若 `stage=captured` 已存在 `lead_id` → 返回失败「该客户已留资，勿重复引导」，防止提示词失控反复索要
4. 生成 `lead_id`，INSERT `bs_wecom_kf_leads`（`assigned_to` 取 `kf_config.tenant_user_id`，客服账号快照）
5. `update_session(session_id, metadata={"lead_capture": {...}})` 写入状态
6. 返回成功；**可选**：返回客服账号的员工二维码 `ImageRef`（Phase 2 启用），供 Agent 直接随回复下发图片

**description（给 LLM）**：描述"收集到客户手机号或客户选择添加员工微信时调用"，明确"调用后提示客户客服会联系/可添加下方微信"，明确"该客户已留资过则不要重复调用"。

**注册**：按 architecture.md 扩展点，新增 `src/tools/wecom_kf_lead/` 目录，`catalog=True`，Catalog 自动发现，无需改 `agent.py`。系统提示词 `AGENT_TOOLS` 中是否显式列出由实现时评估（若渠道无关工具会误触发，则需在工具内做渠道隔离，与 transfer_to_human 同款）。

#### 3.2.3 上下文注入点：`channel_routes.py:2248`

`set_kf_context({...})` 处追加 `lead_capture`（从 `session_metadata` 读取，缺省 None）。`_process_tenant_wecom_kf_messages` 在调用前已能拿到 `session_metadata`（远程状态校验用它），无需额外查询。

### 3.3 老客户识别 / 防重复（Phase 1 内置）

- **会话级防重复**：`metadata.lead_capture.stage=captured` 已存在 → 工具拒绝重复留资（§3.2.2 第 3 步）
- **客户级识别**（Phase 3 增强）：按 `customer_user_id` 反查 `bs_wecom_kf_leads` 存在有效线索 → 留资话术提示词可据此不再引导。Phase 1 以会话级防重复为主，客户级可后续补。

### 3.4 员工二维码配置与图片管线（Phase 2）

#### 3.4.1 配置位置：`kf_account` 新增字段

`tenant_channel_configs.config.kf_account[]` 每项新增（**不区分微信/企微，单一字段**）：

```jsonc
{
  "open_kfid": "...",
  "name": "售前客服",
  "tenant_user_id": "绑定员工 user_id",
  "employee_qr_file_id": "file_xxx",      // 新增：员工二维码 ImageRegistry file_id
  "employee_qr_display_name": "小蔡老师微信二维码.png"  // 新增：展示名
}
```

#### 3.4.2 上传/注册链路（复用 ImageRef 管线）

1. **前端** `ChannelConfig.vue` 客服账号编辑弹窗新增「员工二维码」上传（图片文件）
2. **后端** 新增配置保存接口逻辑：接收图片 → 落盘 `storage/tenants/{tenant_id}/avatar/` → `ImageRegistry.register(source_path, tenant_id, user_id, display_name, source=..., usage=...)` → 得到 `file_id` → 写入 `kf_account.employee_qr_file_id`
3. **读取下发**：留资话术中 Agent 需要二维码图片时，经 `record_lead_capture`（Phase 2）或专用只读工具返回 `ImageRef`（`download_url=/api/files/{file_id}/download`）；wecom_kf `send_message` 的 `_send_image_file_as_image` 已支持按 `file_id` 读本地路径 → `upload_media` → image 消息，微信侧直接显示可长按识别
4. **回显**：管理端回显用现成 `/api/files/{file_id}/download` 端点

**⚠️ TTL 风险（必须处理）**：`ImageRegistry` 对 `source=tool_generated/web_fetch/user_upload` 的 `usage=inline/embedded` 图片有 24h `cleanup_temp` 清理机制。**员工二维码是长期配置资产，不能被临时清理**。处理方案：

- 注册时用 `source="knowledge_base"`（永久 TTL，`PERMANENT_TTL=-1` 不调 expire，cleanup 不清理该 source）——语义不完全贴切但满足"永久保留"，实现最简；
- 生命周期管理：客服账号删除/换图时显式清理旧 `file_id`（调用清理或覆盖注册）；
- 备选：`ImageRegistry` 增加 `source="channel_asset"`（永久）枚举。建议**先复用 knowledge_base 语义 + 显式清理**，避免扩枚举的连锁影响（image_asset 类型契约前后端同步）。

#### 3.4.3 留资话术引用二维码（提示词层）

设计文档 §六"两条话术都给客户"：引导留手机号（纯文本）+ 引导加员工微信（下发二维码图片）。提示词示例（由租户管理员在智能体提示词中配置，代码不硬编码）：

> 当客户愿意添加微信时，调用 record_lead_capture（contact_method=qr）获取员工二维码图片并随回复下发，话术如"您也可以直接添加下方这位同事的微信，随时咨询"。

`record_lead_capture` 在 `contact_method=qr` 时返回 `images=[员工二维码 ImageRef]`，Agent 主循环自动把 `images` 并入回复推送（agent.py 既有能力）。

### 3.5 留资统计报表页（Phase 3，替代定时汇总）

**复用「引流统计」模式**（`ExternalCustomerService.vue` Tab2 + `CustomerReferralDB.referral_stats()`），新增留资维度统计。

#### 3.5.1 后端 API

`src/saas/api/external_customers.py` 新增（或独立 `wecom_kf_leads.py`）：

```python
@router.get("/lead-stats")        # 留资统计：总留资 / 手机号留资 / 二维码留资 / 客服账号分组
@router.get("/leads")             # 留资线索列表（分页，按 created_at DESC，支持日期段/客服账号/阶段筛选）
@router.patch("/leads/{lead_id}") # 更新线索跟进状态（stage: new→contacting→converted/abandoned）
@router.get("/leads/{lead_id}")   # 线索详情（含解密的手机号）
```

统计口径（对齐引流统计）：
- 总留资数 / 按 `contact_method` 分组 / 按 `assigned_to`（客服账号）分组 + ratio
- 过滤基准 = `bs_wecom_kf_leads.lead_created_at`
- 日期段：`start_date` / `end_date`（含当日，SQL `< 次日` 语义 +1 天），与 `referral_stats` 完全一致
- 权限：普通用户（引流员工）仅见 `assigned_to == 自己` 的线索与统计（复用 `_resolve_visible_kf_ids` 隔离模式）

#### 3.5.2 前端

**入口建议**：在「外部接待客户」页（`ExternalCustomerService.vue`）新增「留资线索」Tab（与引流统计 Tab 并列），内部两个区块：**留资统计**（日期段选择 + 统计卡片 + 客服账号分组表）+ **留资列表**（表格：客户/手机号/留资方式/客服账号/时间/阶段，支持状态流转）。复用该页既有 `rangePresets`（7d/30d/custom）、`BaseTable`、下钻交互。

> 备选：独立「留资管理」页面。因线索与外部客户强关联（同一批 C 端客户），建议**优先放外部接待客户页新 Tab**，后续量级增长再拆独立页面。

---

## 四、数据库变更登记

| 文件 | 变更 |
|------|------|
| `deploy/init-postgres.sql` | 新增 `bs_wecom_kf_leads` 建表语句 |
| `deploy/db_update.sql` | 追加 2026-08-21 增量：`CREATE TABLE IF NOT EXISTS bs_wecom_kf_leads ...` |

`tenant_channel_configs.config.kf_account[].employee_qr_file_id` 为 JSONB 内字段，无表结构变更。

---

## 五、测试计划

### Phase 1（后端）
- 单测 `tests/unit/tools/test_wecom_kf_lead_capture.py`：
  - 工具定义（schema/name/description）
  - `contact_method=phone` 无手机号 → 失败
  - 成功写入 `bs_wecom_kf_leads`（含 assigned_to 快照）
  - 二次调用（metadata 已 captured）→ 拒绝，防重复
  - 非 wecom_kf 渠道（无 kf_context）→ 渠道受限失败
  - 手机号加密落库断言（密文非明文）
- 单测 `tests/unit/db/test_wecom_kf_leads.py`：CRUD + 日期段统计 + 权限过滤
- 相邻回归：`tests/unit/channels`（wecom_kf adapter 不受影响）、`test_wecom_kf_adapter.py`

### Phase 2（后端 + 前端）
- 配置保存接口：上传→落盘→注册→file_id 写入；换图旧 file_id 清理
- 前端：`cd frontend && npm run build` 0 错误；员工二维码上传回显
- `record_lead_capture` 返回 ImageRef → send_message 图片下发路径（mock upload_media）

### Phase 3（后端 + 前端）
- `lead-stats`/`leads`/`patch` 接口单测 + 集成
- 前端 build + 留资 Tab 交互（日期段/下钻/阶段流转）
- 权限：普通用户隔离（复用引流统计隔离测试模式）

---

## 六、风险与注意事项

| # | 风险 | 应对 |
|---|------|------|
| 1 | **两张线索表割裂**（新建 vs 已有 `bs_customer_followup_leads`） | 开工前与用户最终确认；如需复用仅改 INSERT 目标表 |
| 2 | **员工二维码被 ImageRegistry 临时清理**（24h TTL） | 用 `source=knowledge_base` 永久语义 + 换图/删号显式清理（§3.4.2） |
| 3 | 提示词判定不可靠导致**重复留资/漏留资** | 代码侧防重复兜底（metadata.lead_capture）；漏留资属提示词质量问题，靠管理员调提示词 |
| 4 | `record_lead_capture` 被非 wecom_kf 渠道误调用 | 工具内 `get_kf_context()` 渠道隔离（与 transfer_to_human 同款） |
| 5 | 手机号明文泄露（日志/审计） | 加密落库、日志不打印、接口解密仅限权限内 |
| 6 | 客服账号未配置员工二维码 | `record_lead_capture(contact_method=qr)` 时若无 file_id → 返回失败 + 提示词仅引导留手机号（设计 §十） |
| 7 | 前端构建/路由 | 新增 Tab 不新增路由（ExternalCustomerService.vue 内部），避免路由挂载风险 |

---

## 七、测试与验收标准（每 Phase 完成门槛）

- [ ] 单测全绿：`./scripts/dev_test.sh <新测试> -p no:cacheprovider -q`
- [ ] 相邻回归通过（wecom_kf adapter / external_customers / channel_routes）
- [ ] 容器 import 检查：`docker exec aid-agent-api python -c "from <改动模块> import <新增符号>"`
- [ ] 前端 `cd frontend && npm run build` 0 错误
- [ ] 三智能体流程（开发→测试→CodeReview）通过
- [ ] 用户确认后 fetch + commit + push

---

## 八、涉及文件清单

| 文件 | 变更 |
|------|------|
| `src/tools/wecom_kf_lead/record_lead_capture.py` | 新增：留资记录工具 |
| `src/db/models.py`（或 `src/saas/db/` 新模块） | 新增 `WeComKfLeadDB`（CRUD + 统计） |
| `src/saas/api/channel_routes.py` | `set_kf_context` 注入 `lead_capture` |
| `src/saas/api/external_customers.py` | 新增 lead-stats / leads 接口 |
| `src/saas/api/wecom_kf_account.py` | 员工二维码上传/保存逻辑 |
| `deploy/init-postgres.sql` / `deploy/db_update.sql` | 建表 |
| `frontend/web/components/saas/ChannelConfig.vue` | 客服账号表单加员工二维码上传 |
| `frontend/web/components/saas/ExternalCustomerService.vue` | 新增「留资线索」Tab（统计 + 列表） |
| `frontend/web/api/*.ts` | 新增留资 API 封装（带 `getAuthHeader` 的 X-Tenant-Id） |

---

## 九、与现有功能的关系

- **转人工**：`transfer_to_human.py` 是本次状态机挂载的**参照实现**（metadata 持久化 + get_kf_context 渠道隔离 + system 标记消息），可直接复用其模式
- **引流归因**（`customer_referrals`）：引流统计是本次留资统计的**模式参照**；两类数据（归因 vs 留资）独立存储，通过 `customer_user_id` 可关联
- **lead-management skill**（`bs_customer_followup_leads`）：线索能力的既有实现，§3.1 已标注取舍
