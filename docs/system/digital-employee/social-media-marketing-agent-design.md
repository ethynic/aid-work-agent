# 社媒营销智能体设计（Social Media Marketing Agent）

> 日期：2026-07-21
> 状态：设计中（内容管理模块已落地骨架，广告管理与巡检商机模块待开发）
> 开发计划：[social-media-marketing-agent-dev-plan.md](social-media-marketing-agent-dev-plan.md)

> **实现状态图例**：✅ 已实现　🔧 部分实现　⬜ 未实现。每个模块的小节标注当前代码真实状态，以 `src/social_media/` 实际代码为准。

---

## 1. 智能体定位

社媒营销智能体是面向企业社媒营销全链路的**单一子智能体**，覆盖获客与营销的三种形态，由三大模块组成：

| 模块 | 营销形态 | 首期平台 | 一句话 |
|------|---------|---------|--------|
| **内容管理** | 自有阵地发布 | 微信公众号、微信视频号 | 把内容发到自己控制的账号 |
| **广告管理** | 付费流量 | 微信朋友圈广告（腾讯广告 Marketing API） | 花钱买流量，诊断优化投放 |
| **巡检商机** | 主动出击 | 知乎、小红书 | 到别人的平台主动找客户 |

三大模块**共享同一套核心**（账号中心、凭证安全、调度、人在环审核、数据归一化、统一文件存储、子智能体框架），通过**统一的社媒平台连接器**对接各平台。对用户呈现为「一个社媒营销智能体」，对话中可自由切换三块能力。

```text
┌─────────────────────────────────────────────────────────────┐
│              社媒营销智能体 (单一子智能体)                    │
├──────────────────┬───────────────────┬──────────────────────┤
│  内容管理模块     │  广告管理模块      │  巡检商机模块         │
│  自有阵地发布     │  付费投放优化      │  主动获客             │
├──────────────────┴───────────────────┴──────────────────────┤
│  统一社媒平台连接器（官方API型 / web操作型 / 辅助发布型）     │
├─────────────────────────────────────────────────────────────┤
│  共享核心：账号 · 凭证 · 调度 · 审核 · 数据归一化 · 文件存储  │
│  执行底座：browser 工具（web 操作型 + 辅助发布共用）          │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 整体架构

### 2.1 六边形 + 统一连接器

核心领域（计划/内容/审核/发布/数据/商机）只依赖连接器协议，不依赖任何平台细节。每个平台以独立连接器接入，连接器的实现形态有三种：

| 连接器形态 | 实现方式 | 适用平台 | 执行底座 |
|-----------|---------|---------|---------|
| **官方 API 型** | 调平台开放 HTTP API | 微信公众号、腾讯广告 | httpx 连接池 |
| **web 操作型** | agent 通过 browser 工具登录后操作平台 web 端 | 知乎、小红书 | browser 工具（#20） |
| **辅助发布型** | 生成发布包/数据导入模板，人工在平台完成 | 微信视频号 | browser 工具（人工接管） |

> **关键澄清**：web 操作型连接器**不是 RPA**。它是 agent 用 browser 工具以真实账号正常登录后，在平台 web 端做正常用户会做的操作（浏览、搜索、发帖、评论）。平台无法也无意区分操作主体是人还是 agent——这与项目企业微信个人账号接入（#29）的定位一致：在已授权真实账号上做正常操作。

### 2.2 复用与新增边界

| 能力 | 来源 | 三大模块共用情况 |
|------|------|----------------|
| 账号中心 + 凭证加密 | 内容管理已实现 | 三模块共用 `social_accounts` 表 |
| 统一文件存储 + 素材登记 | 项目 `src/core/storage.py` + 内容管理 `social_media_assets` | 内容素材、广告创意、巡检截图共用 |
| 调度（APcheduler + DB 原子领取） | 待集成（见 §13） | 定时发布、广告数据同步、巡检巡视共用 |
| 人在环审核 + 版本冻结 | 内容管理已实现 | 内容发布、广告花钱动作、巡检发布/接洽共用 |
| 数据归一化 + 指标快照 | 内容管理已实现（仅结构） | 内容指标、广告指标（不同 `metric_definition` 命名空间）共用 `social_metric_snapshots` |
| browser 工具 | #20 Phase 3 已实现 | web 操作型连接器、辅助发布共用 |
| 子智能体框架 | 项目 `subagents/` | 单一子智能体承载三模块职责 |

---

## 3. 合规立场（web 操作型平台）

**以真实账号正常登录后，在 web 端做正常用户会做的操作，是合规的。** 平台禁止并追究的是恶意爬取、批量注册、机器人轰炸、绕过限制，不是「用浏览器自动化代替手指在已登录真实账号上做正常操作」。

工程上仍需模拟正常用户行为，避免异常频率触发平台风控误伤：

- 真人节奏：操作间随机间隔、每日操作量不超正常活跃用户上限；
- 单账号不混用：一个托管账号只服务一个租户/一个用途；
- 内容质量优先：专业、有用、不像硬广（平台和用户都厌恶刷屏）；
- 异常即停：检测到验证码/限流/失败累积，暂停并转人工接管（复用 browser 工具人工接管能力）；
- 默认自动 + 关键人工：常规流程自动，发布/私信/花钱动作默认人工确认。

**红线（不做）**：多账号矩阵批量、绕过/破解登录验证、抓取他人登录态隐私数据、纯硬广刷屏。

---

## 4. 统一社媒平台连接器

### 4.1 连接器协议　🔧（基类与 Registry 已实现，CapabilityResolver 未实现）

```python
class SocialPlatformConnector(ABC):
    platform: str
    # 抽象方法（子类必须实现）
    async def validate_account(self, account) -> AccountCapabilities
    async def validate_variant(self, variant) -> ValidationResult
    async def prepare_payload(self, variant) -> PreparedPayload
    def map_error(self, error) -> PlatformError
    def normalize_metrics(self, records) -> list[NormalizedMetricRecord]
    # 可选能力（默认抛 CapabilityNotSupported）
    async def create_remote_draft(self, payload)        # 官方API型
    async def publish(self, payload, idempotency_key)   # 官方API型
    async def query_publish_status(self, external_id)   # 官方API型
    async def build_assisted_package(self, variant)     # 辅助发布型
    async def fetch_metrics(self, request)              # 官方API型
```

`ConnectorRegistry`（已实现）按 `platform` 创建连接器并注入解密凭证；`CapabilityResolver`（未实现，当前内联在 `SocialMediaService`）负责调用前校验账号能力，后续抽出。

### 4.2 能力枚举　🔧（内容能力已实现，广告/web 能力待加）

```python
class PlatformCapability(str, Enum):
    # 内容能力（已实现）
    ACCOUNT_OAUTH, ACCOUNT_CREDENTIALS, REMOTE_ASSET_LIST, REMOTE_DRAFT,
    API_PUBLISH, ASSISTED_PUBLISH, SCHEDULED_PUBLISH, PUBLISH_STATUS,
    API_ANALYTICS, DATA_IMPORT
    # 广告能力（待加）
    ADS_OAUTH, ADS_ACCOUNT_TREE, ADS_REPORT, ADS_UPDATE_BID,
    ADS_UPDATE_BUDGET, ADS_UPDATE_TARGETING, ADS_PAUSE, ADS_LEADS,
    ADS_CONVERSION_CALLBACK
    # web 操作能力（待加）
    WEB_LOGIN_SESSION, WEB_SEARCH, WEB_READ_PAGE, WEB_POST_CONTENT,
    WEB_COMMENT, WEB_DM, WEB_INTERACTION_TRACKING
```

能力按账号实测存储。相同平台的两个账号可能因认证/权限不同而能力不同。

### 4.3 首期连接器清单

| 连接器 | 形态 | 模块 | 状态 |
|--------|------|------|------|
| `wechat_official` | 官方API型 | 内容管理 | 🔧 stub（仅能力声明+本地校验，无真实 HTTP） |
| `wechat_channels` | 辅助发布型 | 内容管理 | 🔧 stub（静态 checklist） |
| `tencent_ads` | 官方API型 | 广告管理 | ⬜ 待开发 |
| `zhihu_web` | web操作型 | 巡检商机 | ⬜ 待开发 |
| `xiaohongshu_web` | web操作型 | 巡检商机 | ⬜ 待开发 |

> **已知问题（待修）**：现有 `wechat_official` 连接器声明了 `API_PUBLISH/PUBLISH_STATUS/API_ANALYTICS` 能力，但未重写对应方法，调用即抛 `CapabilityNotSupported`——声明与实现矛盾。Phase 1 修复（见开发计划）。

---

## 5. 内容管理模块

维护周发布计划，从授权素材生成平台无关的内容母版，适配各平台版本，人工审核后发布到自有账号，汇总运营数据。

### 5.1 领域模型　✅（13 表全建）

```
SocialAccount 1──N ContentVariant
ContentPlan 1──N ContentItem 1──1 ContentMaster
ContentMaster 1──N ContentVariant
ContentMaster N──N MediaAsset
ContentVariant 1──N ReviewRecord
ContentVariant 1──N PublishJob 1──N PublishAttempt
PublishedContent 1──N MetricSnapshot
```

13 张表已建（`src/social_media/db.py`）：`social_accounts`、`social_content_plans`、`social_content_items`、`social_content_masters`、`social_media_assets`、`social_content_asset_links`、`social_content_variants`、`social_review_records`、`social_publish_jobs`、`social_publish_attempts`、`social_published_contents`、`social_metric_snapshots`、`social_data_import_batches`。字段与索引完整。

### 5.2 业务流程

```
周计划 → 素材选择（带来源授权）→ AI 生成内容母版 → 平台版本适配
  → 确定性规格校验 → 人工审核（绑定 revision + hash）→ 创建发布任务（冻结快照）
  → 调度执行（公众号 API 发布 / 视频号辅助发布）→ 状态回查 → 运营数据同步 → AI 复盘
```

- **内容版本不可变**：revision 递增，审核绑定 `variant_id + revision + content_hash`，发布任务保存已批准快照，发布时不重读最新版本。
- **审核**：`draft → pending_review → approved / rejected`；版本任何变化回到 draft。
- **AI 生成**：母版生成与平台适配由 LLM 完成，输出经确定性规格校验，不替代人工审核。

### 5.3 发布状态机　🔧（规则表已实现，未被调用）

```
draft → scheduled → queued → publishing
  ├→ submitted → polling → published
  ├→ ready_for_manual_publish → manually_confirmed
  ├→ retry_wait → queued
  ├→ failed / cancelled / status_unknown
```

`ALLOWED_TRANSITIONS` + `can_transition()` 已实现（`publishing/state_machine.py`），但当前未被任何代码调用——待 Phase 在发布执行链路接入强制校验。

### 5.4 公众号连接器（官方 API 型）

能力：账号凭证、远端素材、草稿、API 发布、定时发布（本系统调度）、状态回查、数据分析。`access_token` Redis 缓存并提前刷新，日志不输出 token。当前为 stub，待真实 HTTP 实现。

### 5.5 视频号连接器（辅助发布型）

能力：生成发布包（最终视频/封面/标题/描述/脚本/检查清单）、人工在视频号助手发布后回填链接确认、运营数据模板导入。不保存微信个人登录态。当前为 stub。

### 5.6 模块实现状态

| 项 | 状态 |
|----|------|
| 13 张数据表 | ✅ |
| 连接器协议/Registry | ✅（CapabilityResolver ⬜） |
| 公众号/视频号连接器真实 HTTP | ⬜（当前 stub） |
| 服务层（账号/计划/素材/内容/审核/发布任务 CRUD + 幂等） | ✅ |
| AI 内容生成（母版 + 平台适配） | ⬜ |
| 发布调度执行（Dispatcher/Executor/重试/回查） | ⬜ |
| 运营数据同步/导入/归一化 | ⬜（仅 overview 计数） |
| 21 个 API endpoint | ✅（缺 PATCH/retry/assisted-package/analytics sync/import） |
| 子智能体 | ⬜（`SUBAGENT.md.disabled`） |

---

## 6. 广告管理模块

接入腾讯广告 Marketing API，诊断朋友圈广告投放效果，给出可解释的优化建议，人在环确认后受控执行调价/调预算/暂停，接入留资线索并回传转化以反哺平台 oCPA 模型。

### 6.1 核心定位：建议与受控执行，不重造竞价

腾讯平台已有 oCPA 智能出价。本模块**不重新实现竞价、不做高频自动调价**，而是：诊断投放问题 → 给出带数据依据的建议 → 人审确认 → API 受控执行 → 转化回传。花钱动作永远人在环。

### 6.2 腾讯广告 API 能力边界

- 朋友圈广告全链路：推广计划(`CAMPAIGN_TYPE_WECHAT_MOMENTS`) → 广告组 → 创意 → 广告；
- 出价 CPM + oCPA 智能出价；定向（地域/年龄/性别/商圈）；日限额；投放周期 6h–30d；
- 数据洞察：广告主/广告组/广告层级报表（日/小时/定向），朋友圈专属小时报表（灰度）；
- 留资线索管理平台 + `user_action_sets` 转化回传 + 线索状态标记；
- OAuth 授权支持「第三方技术公司操作多广告主」——天然适配多租户 SaaS。

**关键约束**：一个推广计划下仅 10 个广告组、1 个创意、每广告组 1 条广告；审核通过后仅可改 `adgroup_name/targeting/bid_amount/daily_budget/configured_status/end_date/time_series`——换素材多需新建广告组。

### 6.3 领域模型　⬜（待建，复用部分现有表）

```
AdAccount(复用 social_accounts, platform=tencent_ads) 1──N AdCampaign
AdCampaign 1──N AdGroup 1──1 Ad
AdGroup N──N AdCreative(引用 social_media_assets)
AdGroup/Ad 1──N AdMetricSnapshot(复用 social_metric_snapshots, metric_definition=ads.*)
AdAccount 1──N AdLead 1──N LeadFollowup
OptimizationSuggestion 1──N AdAction 1──N AdActionAttempt
```

新增表：`social_ad_campaigns`、`social_ad_groups`、`social_ad_creatives`、`social_ad_ads`、`social_ad_leads`、`social_ad_lead_followups`、`social_ad_optimization_suggestions`、`social_ad_actions`、`social_ad_action_attempts`。账号/素材/指标快照/审核复用现有表。

### 6.4 投放诊断与策略建议

- 标准广告指标（`ads.*` 命名空间）：曝光/点击/CTR/CPM/CPC/消耗/转化/CPA/线索/有效线索/CPL/ROI；
- 诊断维度（规则，可配置）：创意疲劳、定向过窄/过宽、出价偏低/偏高、预算耗尽过早、时段错配、低效广告；
- 每条诊断产出：问题 + 数据依据 + 建议动作 + 置信度 + 风险等级；样本不足降级，不强建议；
- LLM 负责组织建议文案与复盘（数字必须来自指标快照，防幻觉），**不直接生成 AdAction**。

### 6.5 投放动作状态机（人在环 + 受控执行）

```
suggestion_created → suggestion_pending_review → suggestion_approved
  → action_queued → action_executing
      ├→ action_effective / action_retry_wait / action_failed / action_status_unknown
  → suggestion_rejected
```

安全护栏（租户可配）：单次出价调整 ±30%、日预算上限、单日花钱动作总金额/次数上限、暂停二次确认、强制人审不可关闭。工具层面不暴露直接执行接口，只生成待审建议。

### 6.6 留资跟进与转化回传

线索接入（线索管理平台转发优先，API 拉取兜底）→ PII 加密入库 → 去重质量评分 → 跟进状态流转（received→contacted→qualified/invalid→converted）→ 线索状态 + `user_action_sets` 回传腾讯反哺 oCPA。这是多数广告主没做好的差异化价值点。

### 6.7 模块实现状态

全部 ⬜ 未实现（领域模型、tencent_ads 连接器、诊断/建议引擎、动作状态机、护栏、留资回传、API、前端、工具）。

---

## 7. 巡检商机模块

到知乎、小红书等第三方专业平台，agent 主动发现需求商机、播种专业内容、留言接洽，把有效商机汇总到统一商机池交销售跟进。

### 7.1 三动作 + 一收口

| 动作 | 做什么 |
|------|--------|
| 需求雷达 | agent 巡视平台「求推荐 AI 工具/如何提效」类需求 → LLM 筛选商机 → 意向打分 |
| 专业内容播种 | AI 生成专业内容草稿 → 人审 → 受控发布到已登录账号 |
| 留言接洽 | 相关需求下专业评论/私信，引导接触 |
| 商机收口 | 雷达 + 内容互动 + 接触回复三类商机 → 统一商机池 → 分配销售 |

### 7.2 web 操作型连接器（agent web 交互，非 RPA）

通过 browser 工具（#20）以真实账号登录后操作平台 web 端。每平台一套连接器，结构一致：

```
{platform}_web/
├── selectors.py   # DOM 选择器（集中，易维护；优先语义定位）
├── flows.py       # 各动作的 browser 工具编排（navigate→find→snapshot→act）
├── parser.py      # 页面快照 → 结构化数据
└── connector.py   # 实现 SocialPlatformConnector，聚合上述模块
```

连接器接口（扩展可选能力）：`ensure_logged_in`（登录态校验/恢复，失效转人工重新登录，不破解）、`search`、`read_page`、`post_content`、`comment`、`send_dm`、`fetch_interactions`。

### 7.3 操作节奏与异常处理

- **操作节奏管理**：日配额（单账号日发布 2–3、评论 10–15、私信 3–5）、动作间随机间隔、只在正常活跃时段操作；
- **异常处理**：检测验证码/限流/失败累积 → 暂停该账号定时任务 → 转人工接管；DOM 改版致流程失效 → 连接器降级为只读 + 人工，不影响雷达与商机收口；
- **登录态托管**：Cookie/Session 加密存储（`bs_outbound_account_sessions`），单账号不混用，失效通知人工重新登录。

### 7.4 需求雷达

种子词巡视 → `search + read_page` 拉候选 → parser 结构化 → LLM 商机判别（相关判定 + 意向分 + 接触要点 + 风险标记，只基于原文防幻觉）→ 去重入库 → 高意向通知销售。

### 7.5 内容播种与留言接洽

- 内容播种：选题（雷达高价值问题）→ AI 草稿（平台调性适配，复用内容管理「母版→版本」机制）→ 规格校验 → 人审（默认）/自动 → 受控发布；专业优先、软广克制。
- 留言接洽：商机 → AI 个性化话术 → 人审 → 受控评论/私信；先评论后私信，私信必人工。

### 7.6 商机数据模型　⬜（待建）

```
bs_outbound_leads                 商机主表（来源平台/原文加密/意向分/状态/分配销售，去重指纹）
bs_outbound_lead_interactions     商机互动/跟进记录
bs_outbound_outreach_actions      我方接触动作（审计）
bs_outbound_account_sessions      托管登录态（Cookie 加密）
```

商机状态机：`new → contacted → qualified/invalid → converted`。商机表首期自包含，后续 `qualified/converted` 并入 CRM 智能体（#17）。

### 7.7 模块实现状态

全部 ⬜ 未实现（领域模型、知乎/小红书 web 连接器、雷达/播种/接洽服务、商机池、托管登录态、API、前端、工具）。

---

## 8. 统一数据模型

| 表组 | 模块 | 状态 |
|------|------|------|
| `social_accounts` | 三模块共用（platform 区分） | ✅ |
| `social_media_assets` / `social_content_asset_links` | 内容素材 + 广告创意 + 巡检截图共用 | ✅（asset_links ⬜ 无写入） |
| `social_content_*`（plans/items/masters/variants） | 内容管理 | ✅ |
| `social_review_records` | 内容审核 + 广告动作审核共用 | ✅ |
| `social_publish_jobs` / `_attempts` | 内容发布（jobs ✅，attempts ⬜ 无写入） | 🔧 |
| `social_published_contents` | 内容管理 | ✅ |
| `social_metric_snapshots` | 内容指标 + 广告指标（`metric_definition` 命名空间隔离） | ✅（⬜ 无写入） |
| `social_data_import_batches` | 视频号数据导入 | ✅（⬜ 无写入） |
| `social_ad_*`（9 表） | 广告管理 | ⬜ |
| `bs_outbound_*`（4 表） | 巡检商机 | ⬜ |

所有新表遵循 [database_dev.md](.claude/rules/database_dev.md)：业务表 `bs_`/`social_` 前缀、`tenant_id`/`user_id`/`created_at` 必备、DB 变更同步 `init-postgres.sql` + `db_update.sql` + `database_system_table.md`。

---

## 9. 子智能体与工具

单一子智能体 `social-media-marketing`（当前 `SUBAGENT.md.disabled`，三大模块跑通验收后启用），承载三模块职责：

- **内容管理**：选题建议、素材查找、内容生成与适配、审核交接、数据复盘；
- **广告管理**：投放诊断问答、策略建议、留资分析、复盘；
- **巡检商机**：需求雷达解读、商机推荐、接触话术、跟进辅助、获客复盘。

**禁止**：读取/导出凭证与 Cookie、绕过审核执行发布/花钱/留言、把「平台已受理」说成「已生效」、编造指标或商机原文。

**确定性工具**（服务端二次校验，不信任 LLM 参数）。高风险动作（发布、调价、留言、私信）**只生成草稿/建议**，经 UI/API 审核后由服务层受控执行——工具层面不暴露直接执行接口。

---

## 10. 安全与权限

- **凭证**：平台凭证、OAuth token、登录态 Cookie 统一 `SecretCrypto` 加密 + 密钥版本；不出现在 API/日志/审计/Agent 上下文；解绑即吊销清理。
- **权限**：`account.manage`（租户管理员）、`content.edit` / `content.review` / `publish.execute`（内容）、`ads_suggestion.review` / `ads_action.audit`（广告，建议人≠审核人）、`lead.view` / `lead.manage`（商机）。
- **租户隔离**：所有 DAO 强制 `tenant_id`，越权拒绝。
- **PII**：商机/线索正文含他人留资信息，加密存储、脱敏展示、导出需权限+审计。
- **审计**：账号绑定/解绑、内容审核、发布、花钱动作、接触动作、商机流转全记录。

---

## 11. 前端聚合平台

统一入口 `/social-media`，三大模块子区：

| 路由 | 模块 | 状态 |
|------|------|------|
| `/social-media`（聚合工作台） | 总览 | 🔧 `SocialMediaWorkbench.vue`（~20%，KPI/账号绑定/计划/发布队列/数据概览） |
| `/social-media/accounts` | 共用 | 账号中心（多平台账号、能力、健康、解绑） ⬜ |
| `/social-media/calendar` `/content` `/publishing` | 内容管理 | 日历、内容工作台、发布中心 ⬜ |
| `/social-media/analytics` | 内容+广告 | 数据看板 + AI 复盘 ⬜ |
| `/social-media/advertising/*` | 广告管理 | 账户、投放管理、诊断建议、ROI/CPA、留资中心 ⬜ |
| `/social-media/outbound/*` | 巡检商机 | 托管账号、雷达、商机池、内容播种、获客看板 ⬜ |

前端遵循项目规范（page_patterns/list-page/detail-page）。状态文字+颜色双表达，「平台已受理/待审/人工确认」≠「已生效」。页面元数据先 `planned`，验收后 `published`。

---

## 12. 测试策略

- **单元**：状态机合法转换、版本修改失效旧审批、幂等键防重、能力解析拒绝不支持动作、各连接器映射（内容/错误/指标）、凭证与日志脱敏、租户过滤、广告护栏、商机去重与意向打分。
- **连接器契约**（所有连接器复用）：`validate_account` 返回能力、`validate_variant` 不变更输入、不支持能力抛 `CapabilityNotSupported`、`map_error` 稳定分类、归一化指标保留原始、凭证不出现在序列化结果。
- **集成**：公众号 Mock Server（token/草稿/发布/轮询/限流/超时）、腾讯广告 Mock（OAuth/账户树/异步报表/投放动作/线索/回传）、知乎/小红书 Mock 站点（DOM 快照驱动：搜索/读/发/评论/私信/互动）、发布/花钱/接触动作的多 Worker 防重与崩溃恢复、租户隔离与越权。
- **真实账号验收**：公众号/视频号/腾讯广告/知乎/小红书分别完成真机闭环；未验证不标记完成。

---

## 13. 已实现能力总览

以 `src/social_media/` 实际代码为准（2026-07-21 核对）：

| 维度 | 状态 | 说明 |
|------|------|------|
| 数据模型 | ✅ | 13/13 表全建，字段与索引完整 |
| 连接器协议 | 🔧 | 基类 + Registry 已实现；CapabilityResolver 缺失（内联在 service） |
| 公众号/视频号连接器 | 🔧 | stub，仅能力声明 + 本地校验，无真实 HTTP（声明与实现矛盾，待修） |
| 服务层 | 🔧 | 单类 `SocialMediaService`，账号/计划/素材/内容/审核/发布任务 CRUD + 幂等真实可用 |
| 发布状态机 | 🔧 | 规则表 + 断言函数已实现，未被调用 |
| AI 内容生成 | ⬜ | 母版生成、平台适配未实现 |
| 发布调度执行 | ⬜ | Dispatcher/Executor 全无，APScheduler 未集成，QUEUED 之后不可达 |
| 运营数据分析 | ⬜ | 仅 overview 计数聚合；指标/导入/归一化未实现 |
| API 层 | 🔧 | 21 个真实 endpoint；缺 PATCH/retry/assisted-package/analytics sync/import |
| 前端 | 🔧 | 1 个聚合工作台（~20%），设计要求的独立页面未拆分 |
| 子智能体 | ⬜ | `SUBAGENT.md.disabled` 未启用 |
| 页面元数据 | 🔧 | `status: planned`（未进业务菜单） |
| 广告管理模块 | ⬜ | 完全未实现 |
| 巡检商机模块 | ⬜ | 完全未实现 |

**整体**：基础设施与数据模型已落地、内容管理业务流程半自动（人工审核交接 + 幂等创建任务）、平台真实集成与自动化未启动；广告与巡检两模块待开发。当前对生产环境无影响（页面 planned、子智能体 disabled）。

---

## 14. 待确认项

1. 公众号首期授权方式（直填凭证 vs 第三方平台 OAuth）及真实可用数据接口/指标/延迟。
2. 视频号是否存在面向租户的定向官方发布或数据权限。
3. 朋友圈小时报表 `TASK_TYPE_WECHAT_ADGROUP_HOURLY_REPORT` 灰度权限能否申请（拿不到降级日粒度）。
4. 腾讯广告首期接入主体（直客自用 vs SaaS 服务商），影响 OAuth 应用归属。
5. 巡检模块：公司可用的知乎/小红书真实账号（非新号）；browser 工具登录态持久化能力；小红书 web 端发布/私信可用性（可能部分仅 APP）。
6. 商机与 CRM 智能体（#17）的边界：`qualified/converted` 沉淀客户的时机与归属。
7. 项目统一文件存储是否满足素材长期保留 + 签名访问 + 清理策略。

> 以上不得通过猜测写入实现。各模块 Phase 0 验证后结论变化时先更新本设计。
