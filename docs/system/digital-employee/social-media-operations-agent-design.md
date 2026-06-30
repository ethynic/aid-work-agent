# 社媒内容运营智能体与聚合平台系统设计

> 版本：v1.0  
> 日期：2026-06-29  
> 状态：设计完成，待开发  
> 首期平台：微信公众号、微信视频号  
> 前置调研：[社媒内容运营智能体与聚合平台可行性调研](../../research/social-media-operations-agent-platform-research.md)

## 1. 设计目标

建设一个多租户社媒内容运营平台，让运营人员和社媒运营智能体共同完成：

1. 维护每周发布计划；
2. 从租户授权的数据源获取素材并记录来源；
3. 生成平台无关的内容母版；
4. 将母版适配成各社媒平台的内容版本；
5. 由用户审核平台版本；
6. 审核通过后立即发布、定时发布或进入人工发布流程；
7. 汇总各平台运营数据，由 AI 生成复盘和下一周期建议。

首期实现微信公众号的官方 API 发布闭环，以及微信视频号的“生成发布包—人工发布—人工确认/数据导入”辅助闭环。

### 1.1 长期扩展目标

后续可能接入新浪微博、小红书、抖音、快手、今日头条、X、Instagram 等平台。新增平台时应满足：

- 不修改内容计划、母版、审核、发布任务和数据看板的核心状态机；
- 不在核心服务中增加 `if platform == ...` 分支；
- 只新增平台连接器、平台内容规格、指标映射和必要配置；
- 同一账号的能力由运行时探测结果决定，而不是仅由平台名称决定；
- 既支持官方 API 自动发布，也支持人工辅助、数据导入等降级能力。

## 2. 范围与假设

### 2.1 首期范围

- 多租户社媒账号绑定和能力探测；
- 周发布计划和内容日历；
- 租户素材选择、来源及授权记录；
- 内容母版和平台版本；
- AI 生成、改写与平台适配；
- 强制人工审核和版本冻结；
- 微信公众号草稿、立即/定时发布、状态回查和基础数据同步；
- 微信视频号发布包、人工发布确认和运营数据模板导入；
- 聚合数据看板、审计日志和失败告警；
- 社媒运营智能体及其确定性业务工具。

### 2.2 暂不实现

- 视频号 Cookie 托管、非官方接口或网页 RPA 发布；
- 无审核全自动发布；
- 评论、私信、点赞、关注等互动运营；
- 自研视频生成和剪辑引擎；
- 抓取未授权的第三方内容；
- 首期直接开发微博、小红书、抖音等连接器。

### 2.3 关键假设

- 首期能取得至少一个已认证微信公众号用于真实接口验证；
- 视频号通用内容发布 API 尚未确认，必须按辅助发布能力设计；
- 一个租户可以绑定多个平台、同平台多个账号；
- 同一内容母版可以派生多个平台版本，平台版本分别审核、分别发布；
- AI 不直接持有平台凭证，也不能绕过审核调用发布接口。

## 3. 架构决策

### 3.1 采用六边形架构和平台连接器

核心领域只依赖连接器协议，不依赖任何平台 SDK：

```text
┌──────────────────────────────────────────────────────────────┐
│ Web 聚合平台 / Social Operations Agent                       │
└──────────────────────────┬───────────────────────────────────┘
                           │ Application Services
┌──────────────────────────▼───────────────────────────────────┐
│ Planning │ Asset │ Content │ Review │ Publish │ Analytics    │
└──────────────────────────┬───────────────────────────────────┘
                           │ Connector Ports
┌──────────────────────────▼───────────────────────────────────┐
│ Connector Registry / Capability Resolver / Credential Vault │
└──────────────┬───────────────────────┬───────────────────────┘
               │                       │
┌──────────────▼────────────┐ ┌────────▼───────────────────────┐
│ WeChat Official Connector │ │ WeChat Channels Assisted       │
│ 官方 API 发布 + 数据同步   │ │ 发布包 + 人工确认 + 数据导入   │
└───────────────────────────┘ └────────────────────────────────┘
```

未来每个平台以独立连接器包接入：

```text
src/social_media/connectors/
├── base.py
├── registry.py
├── wechat_official/
├── wechat_channels/
├── weibo/          # 后续
├── x/              # 后续
└── instagram/      # 后续
```

### 3.2 复用与隔离边界

| 能力 | 核心复用 | 平台独立 |
|------|---------|---------|
| 周计划、日历 | 是 | 否 |
| 素材来源和版权记录 | 是 | 平台媒体约束校验 |
| 内容母版 | 是 | 否 |
| AI 生成编排 | 是 | 平台提示模板和规格 |
| 审核和版本冻结 | 是 | 可增加平台专属检查项 |
| 发布任务状态机 | 是 | 发布协议和错误映射 |
| 定时调度、幂等、重试 | 是 | 平台限流和可重试判断 |
| 运营数据存储和看板 | 是 | 原始指标拉取与标准指标映射 |
| 凭证加密、租户隔离、审计 | 是 | 授权流程和 token 刷新 |

### 3.3 不复用通用 Agent 定时任务执行器

现有 `src/scheduler/` 面向“到点重新运行一段 Agent 提示词”。社媒发布是高风险、不可随意重复的确定性动作，不能在到点后重新让 LLM 决定发布内容。

本功能只复用现有 APScheduler 的进程启动和系统级唤醒能力：

- 数据库中的 `social_publish_jobs` 是唯一事实源；
- APScheduler 每分钟唤醒一次 `PublishJobDispatcher`；
- Dispatcher 原子领取到期任务并执行冻结的发布快照；
- LLM 不参与发布执行；
- 多 Worker 通过数据库原子更新和 Redis 短租约避免重复领取；
- 即使调度进程重启，也能从数据库补领过期未执行任务。

## 4. 领域模型

### 4.1 聚合根与关系

```text
SocialAccount 1 ── N ContentVariant
ContentPlan 1 ── N ContentItem 1 ── 1 ContentMaster
ContentMaster 1 ── N ContentVariant
ContentMaster N ── N MediaAsset
ContentVariant 1 ── N ReviewRecord
ContentVariant 1 ── N PublishJob 1 ── N PublishAttempt
PublishedContent 1 ── N MetricSnapshot
```

### 4.2 主要对象

| 对象 | 职责 |
|------|------|
| `SocialAccount` | 租户绑定的平台账号、授权状态和实际能力 |
| `ContentPlan` | 周期、目标、受众、主题和负责人 |
| `ContentItem` | 日历中的单个待发布主题和计划时间 |
| `ContentMaster` | 平台无关的事实、结构、品牌要求和素材引用 |
| `MediaAsset` | 图片、视频、音频、文件及来源/授权元数据 |
| `ContentVariant` | 面向某账号和平台生成的具体内容版本 |
| `ReviewRecord` | 对不可变版本的审核结论 |
| `PublishJob` | 立即、定时或人工辅助发布任务 |
| `PublishAttempt` | 每次平台调用或人工交接的执行记录 |
| `PublishedContent` | 平台发布结果与外部内容标识 |
| `MetricSnapshot` | 某内容/账号在某统计时间点的原始和标准指标 |

### 4.3 内容版本不可变规则

- `ContentVariant.revision` 从 1 递增；
- 提交审核后，当前 revision 内容不可直接修改；
- 修改会创建新 revision，并使旧审批不再适用于新版本；
- `ReviewRecord` 必须绑定 `variant_id + revision + content_hash`；
- `PublishJob` 保存已批准 revision 的完整发布快照；
- 发布时不重新读取“最新版本”，避免审核后内容被替换。

## 5. 平台连接器协议

### 5.1 能力声明

```python
class PlatformCapability(str, Enum):
    ACCOUNT_OAUTH = "account_oauth"
    ACCOUNT_CREDENTIALS = "account_credentials"
    REMOTE_ASSET_LIST = "remote_asset_list"
    REMOTE_DRAFT = "remote_draft"
    API_PUBLISH = "api_publish"
    ASSISTED_PUBLISH = "assisted_publish"
    SCHEDULED_PUBLISH = "scheduled_publish"
    PUBLISH_STATUS = "publish_status"
    API_ANALYTICS = "api_analytics"
    DATA_IMPORT = "data_import"


@dataclass(frozen=True)
class AccountCapabilities:
    supported: frozenset[PlatformCapability]
    limits: dict[str, Any]
    detected_at: datetime
    expires_at: datetime | None
```

能力按账号存储。相同平台的两个账号可能因认证状态、地区或权限集不同而拥有不同能力。

### 5.2 统一连接器接口

```python
class SocialPlatformConnector(ABC):
    platform: str

    @abstractmethod
    async def validate_account(
        self, account: SocialAccountContext
    ) -> AccountCapabilities: ...

    @abstractmethod
    async def validate_variant(
        self, variant: ContentVariantSnapshot
    ) -> ValidationResult: ...

    @abstractmethod
    async def prepare_payload(
        self, variant: ContentVariantSnapshot
    ) -> PreparedPayload: ...

    async def create_remote_draft(
        self, payload: PreparedPayload
    ) -> RemoteDraftResult:
        raise CapabilityNotSupported("remote_draft")

    async def publish(
        self, payload: PreparedPayload, idempotency_key: str
    ) -> PublishSubmission:
        raise CapabilityNotSupported("api_publish")

    async def query_publish_status(
        self, external_task_id: str
    ) -> PublishStatusResult:
        raise CapabilityNotSupported("publish_status")

    async def build_assisted_package(
        self, variant: ContentVariantSnapshot
    ) -> AssistedPublishPackage:
        raise CapabilityNotSupported("assisted_publish")

    async def fetch_metrics(
        self, request: MetricFetchRequest
    ) -> list[RawMetricRecord]:
        raise CapabilityNotSupported("api_analytics")

    @abstractmethod
    def map_error(self, error: Exception) -> PlatformError: ...

    @abstractmethod
    def normalize_metrics(
        self, records: list[RawMetricRecord]
    ) -> list[NormalizedMetricRecord]: ...
```

连接器可只实现账号实际支持的能力。核心服务必须先通过 `CapabilityResolver` 校验，再调用可选接口。

### 5.3 连接器注册

```python
connector_registry.register(
    platform="wechat_official",
    factory=WeChatOfficialConnector,
)
```

禁止在业务服务中直接实例化连接器。`ConnectorRegistry` 根据 `SocialAccount.platform` 创建连接器，并注入解密后的短生命周期凭证上下文。

### 5.4 平台内容规格

平台连接器通过 `ContentSpec` 声明：

- 支持的内容类型；
- 标题、摘要、正文和标签约束；
- 图片/视频格式、大小、比例、时长约束；
- 链接、话题、位置、原创声明等可选字段；
- 平台禁用字段和发布前检查规则；
- AI 适配提示模板版本。

规格既用于前端动态表单，也用于后端确定性校验。LLM 的输出不能替代规格校验。

## 6. 核心业务流程

### 6.1 周计划到内容生成

1. 运营人员创建周计划和日历条目；
2. `AssetService` 根据明确的数据源范围检索素材；
3. 智能体生成 `ContentMaster` 草稿，事实来源和素材引用必须可追踪；
4. 用户选择目标平台账号；
5. `ContentAdaptationService` 读取账号能力和平台规格；
6. AI 生成 `ContentVariant`，确定性校验器检查字段和媒体规格；
7. 校验失败时只返回问题，不自动删除或静默截断重要内容；
8. 用户修改后提交审核。

### 6.2 审核

```text
draft → pending_review → approved
                       └→ rejected → new revision → pending_review
```

- 首期每个平台版本需要一名具有审核权限的用户批准；
- 审核记录保存审核人、时间、结论、意见、revision 和 hash；
- 批准后可以创建发布任务；
- 版本发生任何内容变化，状态回到 `draft`；
- 平台能力或规格在审核后发生变化，发布前校验失败并进入人工处理，不自动改写已批准版本。

### 6.3 公众号 API 发布

```text
approved
  → create PublishJob(snapshot)
  → due
  → claim job
  → validate capability and snapshot
  → upload/reuse media
  → create/update remote draft
  → freepublish submit
  → submitted
  → poll status
  → published / failed / status_unknown
```

- 接口受理后状态为 `submitted`，不能显示为 `published`；
- 有外部发布任务 ID 时定时轮询；
- 平台返回明确失败时记录标准错误码和脱敏原始响应；
- 超过最大回查时间进入 `status_unknown` 并告警；
- 重试前先查询已有外部任务，不能盲目再次提交；
- 平台不支持真正的幂等键时，以本地任务状态、外部任务 ID 和内容 hash 实现防重。

### 6.4 视频号辅助发布

```text
approved
  → build assisted package
  → ready_for_manual_publish
  → operator downloads/copies package
  → operator publishes in 视频号助手
  → operator records URL/time/external ID
  → manually_confirmed
```

发布包包含：

- 最终视频；
- 封面；
- 标题、描述、话题；
- 口播稿、字幕或分镜文件（如有）；
- 建议发布时间；
- 审核版本号和内容 hash；
- 操作检查清单。

人工确认必须由有发布权限的用户完成，系统记录确认人和外部链接。`manually_confirmed` 与 API 回查得到的 `published` 在界面上有明确来源标识。

### 6.5 数据同步

- API 平台：按连接器定义的时间窗口拉取原始指标；
- 辅助平台：导入标准模板，保存原始文件摘要和导入批次；
- 原始指标原样写入 `raw_metrics` JSON；
- 连接器映射为有限的标准指标；
- 看板同时显示平台原始名称、标准名称、数据截止时间和最后同步时间；
- 不同口径的指标不得直接相加；只有 `metric_definition` 一致时才聚合。

## 7. 发布状态机

```text
draft
  → scheduled
  → queued
  → publishing
      ├→ submitted → polling → published
      ├→ ready_for_manual_publish → manually_confirmed
      ├→ retry_wait → queued
      ├→ failed
      ├→ cancelled
      └→ status_unknown
```

允许转换必须集中定义在领域层。数据库更新使用条件更新：

```sql
UPDATE social_publish_jobs
SET status = 'publishing', lease_owner = ?, lease_expires_at = ?
WHERE job_id = ?
  AND status IN ('queued', 'retry_wait')
  AND scheduled_at <= CURRENT_TIMESTAMP;
```

更新行数不是 1 时，当前执行器不得继续发布。

## 8. 数据库设计

所有业务表属于租户业务表，必须包含 `tenant_id`。主键使用 UUID 文本，与现有系统保持兼容。敏感凭证不允许明文存储。

### 8.1 表清单

| 表 | 用途 |
|----|------|
| `social_accounts` | 平台账号、授权方式、密文凭证和能力 |
| `social_content_plans` | 周计划/活动 |
| `social_content_items` | 日历条目 |
| `social_content_masters` | 内容母版 |
| `social_media_assets` | 素材与授权元数据 |
| `social_content_asset_links` | 母版与素材关系 |
| `social_content_variants` | 平台内容版本 |
| `social_review_records` | 审核记录 |
| `social_publish_jobs` | 发布任务和冻结快照 |
| `social_publish_attempts` | 发布尝试与平台响应 |
| `social_published_contents` | 已发布内容映射 |
| `social_metric_snapshots` | 原始/标准运营指标 |
| `social_data_import_batches` | 视频号等人工数据导入批次 |

### 8.2 关键字段

#### `social_accounts`

```text
account_id, tenant_id, platform, display_name, external_account_id,
auth_type, credentials_encrypted, credential_key_version,
status, capabilities_json, capability_expires_at,
last_validated_at, created_by, created_at, updated_at
```

唯一约束：`(tenant_id, platform, external_account_id)`。

#### `social_content_plans`

```text
plan_id, tenant_id, name, period_start, period_end,
goal, target_audience, status, owner_user_id,
created_by, created_at, updated_at
```

#### `social_content_items`

```text
item_id, tenant_id, plan_id, topic, objective,
planned_at, timezone, owner_user_id, status,
created_at, updated_at
```

#### `social_content_masters`

```text
master_id, tenant_id, item_id, title, brief,
facts_json, source_refs_json, brand_constraints_json,
revision, content_hash, status, created_by, created_at, updated_at
```

#### `social_media_assets`

```text
asset_id, tenant_id, storage_file_id, asset_type,
mime_type, file_size, checksum, source_type, source_uri,
license_type, license_owner, license_expires_at,
status, metadata_json, created_by, created_at
```

素材文件复用项目统一文件存储，表中只保存 `storage_file_id`，不新增第二套物理文件系统。

#### `social_content_variants`

```text
variant_id, tenant_id, master_id, account_id, platform,
content_type, revision, content_json, content_hash,
spec_version, prompt_version, status,
created_by, created_at, updated_at
```

唯一约束：`(tenant_id, variant_id, revision)`。`content_json` 保存平台字段，但公共检索字段仍单独建列，避免所有查询依赖 JSON。

#### `social_review_records`

```text
review_id, tenant_id, variant_id, variant_revision,
content_hash, decision, comment, reviewer_user_id, reviewed_at
```

#### `social_publish_jobs`

```text
job_id, tenant_id, account_id, variant_id, variant_revision,
content_hash, publish_mode, scheduled_at, timezone,
status, idempotency_key, publish_snapshot_json,
external_task_id, retry_count, max_retries, next_retry_at,
lease_owner, lease_expires_at, last_error_code, last_error_message,
created_by, created_at, updated_at
```

唯一约束：`idempotency_key`。索引：

- `(status, scheduled_at)`；
- `(tenant_id, account_id, created_at)`；
- `(external_task_id)`；
- `(lease_expires_at)`。

#### `social_publish_attempts`

```text
attempt_id, tenant_id, job_id, attempt_no, trigger_type,
request_summary_json, response_summary_json,
status, platform_error_code, error_category,
started_at, completed_at, duration_ms
```

请求和响应只保存脱敏摘要，不保存 access token、AppSecret 或完整敏感负载。

#### `social_published_contents`

```text
published_id, tenant_id, job_id, account_id, variant_id,
platform, external_content_id, external_url,
confirmation_source, published_at, confirmed_by, created_at
```

#### `social_metric_snapshots`

```text
snapshot_id, tenant_id, account_id, published_id,
metric_date, metric_definition, normalized_metrics_json,
raw_metrics_json, source_type, collected_at, import_batch_id
```

唯一约束：`(tenant_id, account_id, published_id, metric_date, metric_definition)`，重复同步使用 upsert。

### 8.3 删除与保留

- 平台账号默认软删除，删除后立即禁用凭证并取消未执行任务；
- 已审核版本、发布快照、审核和发布审计不可级联硬删除；
- 素材删除前检查引用；已发布快照引用的素材进入只读保留；
- 运营数据按租户数据保留策略清理；
- 数据库变更同步更新 `docs/system/database_system_table.md`。

## 9. 应用服务

| 服务 | 主要职责 |
|------|---------|
| `SocialAccountService` | 绑定、解密上下文、验证、能力刷新、解绑 |
| `ContentPlanService` | 周计划和日历 |
| `AssetService` | 素材登记、来源授权、引用和校验 |
| `ContentGenerationService` | 母版生成和事实来源组织 |
| `ContentAdaptationService` | 按账号能力和平台规格生成版本 |
| `ReviewService` | 提交、批准、驳回、版本 hash 校验 |
| `PublishService` | 创建发布任务、取消、人工确认 |
| `PublishJobDispatcher` | 原子领取到期任务 |
| `PublishExecutor` | 调用连接器、重试、回查和落库 |
| `AnalyticsService` | API 同步、文件导入、指标归一化 |
| `SocialAuditService` | 记录关键业务动作 |

服务调用必须显式接收 `tenant_id` 和当前用户上下文，DAO 层查询再次强制租户过滤。

## 10. 社媒运营智能体设计

### 10.1 职责

智能体负责：

- 根据周计划提出选题；
- 在授权素材范围内查找素材；
- 生成内容母版；
- 调用平台适配服务创建平台版本；
- 汇总校验问题；
- 提交用户审核；
- 对历史运营数据生成复盘建议。

智能体不负责：

- 解密或查看平台凭证；
- 自行批准内容；
- 直接调用第三方平台 HTTP API；
- 修改已批准快照；
- 将接口受理解释为发布成功。

### 10.2 确定性工具

```text
list_social_accounts
get_social_account_capabilities
list_content_plans
create_content_master
search_authorized_assets
generate_platform_variant
submit_variant_for_review
get_review_status
create_publish_job
get_publish_job_status
get_social_analytics
```

`create_publish_job` 必须在工具服务端重新校验审核记录、内容 hash、账号能力和用户权限，不能只相信 LLM 参数。

### 10.3 触发方式

- 用户对话中显式调用社媒运营智能体；
- 聚合页面中“AI 生成/平台适配/数据复盘”按钮；
- 每周计划可创建“生成提醒”，但首期不自动生成并发布；
- 智能体输出结构化草稿，最终业务状态由应用服务决定。

## 11. API 设计

统一前缀：`/api/social-media`。

### 11.1 账号

```text
GET    /accounts
POST   /accounts
GET    /accounts/{account_id}
POST   /accounts/{account_id}/validate
POST   /accounts/{account_id}/refresh-capabilities
DELETE /accounts/{account_id}
```

账号响应只返回凭证掩码、授权状态和能力，不返回密文或明文凭证。

### 11.2 计划、内容与素材

```text
GET/POST       /plans
GET/PATCH      /plans/{plan_id}
GET/POST       /plans/{plan_id}/items
POST           /content-masters
GET/PATCH      /content-masters/{master_id}
POST           /content-masters/{master_id}/variants
GET/PATCH      /variants/{variant_id}
POST           /assets
GET            /assets
```

### 11.3 审核与发布

```text
POST /variants/{variant_id}/submit-review
POST /variants/{variant_id}/approve
POST /variants/{variant_id}/reject
POST /publish-jobs
GET  /publish-jobs
GET  /publish-jobs/{job_id}
POST /publish-jobs/{job_id}/cancel
POST /publish-jobs/{job_id}/retry
GET  /publish-jobs/{job_id}/assisted-package
POST /publish-jobs/{job_id}/manual-confirm
```

`POST /publish-jobs` 支持客户端 `Idempotency-Key`，服务端仍生成并校验业务幂等键。

### 11.4 数据

```text
POST /analytics/sync
POST /analytics/import
GET  /analytics/overview
GET  /analytics/contents
GET  /analytics/accounts
```

## 12. 前端聚合平台

路由建议：`/social-media`。

| 页面 | 能力 |
|------|------|
| `/social-media/accounts` | 平台账号、能力、健康状态、解绑 |
| `/social-media/calendar` | 周/月内容日历、状态和拖拽排期 |
| `/social-media/content/{id}` | 母版、素材、平台版本、差异和审核 |
| `/social-media/publishing` | 发布队列、人工发布、失败和重试 |
| `/social-media/analytics` | 账号/内容/平台数据和 AI 复盘 |

前端根据账号能力动态显示动作：

- 有 `API_PUBLISH`：显示立即发布/定时发布；
- 只有 `ASSISTED_PUBLISH`：显示生成发布包；
- 有 `API_ANALYTICS`：显示同步；
- 只有 `DATA_IMPORT`：显示导入；
- 不支持的动作不显示，后端仍必须拒绝越权调用。

关键状态必须使用文字加颜色，不能只依赖颜色区分。发布页明确展示“已批准、已排期、平台已受理、已发布、人工确认、状态未知”。

## 13. 首期连接器设计

### 13.1 微信公众号连接器

平台标识：`wechat_official`。

首期授权方式：

- 实施优先：租户管理员录入 `AppID/AppSecret`，凭证加密；
- 架构预留：`auth_type=oauth_component`，后续接微信开放平台第三方平台授权；
- 两种授权方式对上层暴露相同连接器接口。

实现能力：

- `ACCOUNT_CREDENTIALS`；
- `REMOTE_ASSET_LIST`；
- `REMOTE_DRAFT`；
- `API_PUBLISH`；
- `SCHEDULED_PUBLISH`（由本系统调度）；
- `PUBLISH_STATUS`；
- `API_ANALYTICS`（以实号能力探测为准）。

模块：

```text
wechat_official/
├── connector.py
├── client.py
├── auth.py
├── specs.py
├── content_mapper.py
├── media.py
├── publisher.py
├── analytics.py
└── errors.py
```

`access_token` 使用 Redis 按账号缓存并提前刷新；缓存值加密或限制访问，日志不输出 token。HTTP Client 复用连接池并显式关闭。

### 13.2 微信视频号辅助连接器

平台标识：`wechat_channels`。

首期能力：

- `ASSISTED_PUBLISH`；
- `DATA_IMPORT`。

账号绑定只记录租户账号标识、显示名称和管理链接，不保存微信个人账号密码或 Cookie。

模块：

```text
wechat_channels/
├── connector.py
├── specs.py
├── content_mapper.py
├── package_builder.py
├── import_schema.py
└── metrics.py
```

若未来取得官方发布 API：

- 在该连接器内部新增 API 发布策略；
- 能力探测返回 `API_PUBLISH/PUBLISH_STATUS/API_ANALYTICS`；
- 不改变核心领域表、API 和页面流程；
- 已存在的辅助发布能力继续作为降级路径。

## 14. 调度、并发与失败处理

### 14.1 调度

- 系统级 job 每分钟扫描 `scheduled_at <= now` 的任务；
- 时间统一以 UTC 存储，同时保存用户选择的 IANA timezone；
- `misfire_grace_time` 建议 10 分钟，超过后任务进入 `failed` 或人工确认，不擅自延后发布；
- 扫描批次限制数量，按 `scheduled_at, created_at` 排序；
- 同一账号的并发数和速率由连接器限制器控制。

### 14.2 重试分类

| 分类 | 示例 | 行为 |
|------|------|------|
| `transient` | 网络超时、平台 5xx、限流 | 指数退避并加随机抖动 |
| `auth` | token 过期 | 刷新一次后重试；仍失败则停用账号并告警 |
| `validation` | 内容或媒体不合规 | 不重试，退回人工修改 |
| `permission` | 接口无权限 | 不重试，刷新能力并提示管理员 |
| `duplicate_risk` | 提交超时且结果不明 | 先查询状态；无法确认则 `status_unknown` |
| `permanent` | 平台明确拒绝 | 不重试，记录错误 |

### 14.3 一致性

- 本地数据库与外部平台无法使用同一事务，采用状态机和补偿查询；
- 外部调用前写 `PublishAttempt=started`；
- 外部调用后在同一本地事务中更新 attempt、job 和外部任务 ID；
- 进程崩溃留下的 `publishing` 任务在租约过期后进入恢复流程；
- 恢复流程优先查询平台状态，禁止直接重复发布。

## 15. 安全与权限

### 15.1 权限

| 权限 | 建议角色 |
|------|---------|
| `social_account.manage` | 租户管理员 |
| `social_content.edit` | 运营人员、租户管理员 |
| `social_content.review` | 审核人、租户管理员 |
| `social_publish.execute` | 发布人、租户管理员 |
| `social_analytics.view` | 运营人员、审核人、租户管理员 |

审核人和发布人可由同一租户配置，但系统分别记录动作。

### 15.2 敏感信息

- 平台凭证使用项目统一的 `SecretCrypto`/密钥管理能力加密，密钥版本单独记录；
- API、日志、审计、异常和 Agent 上下文均不返回明文凭证；
- 数据库备份中的凭证仍为密文；
- 解绑时清除/吊销凭证并清理 token 缓存；
- 媒体访问使用短期签名 URL，禁止跨租户文件 ID 查询；
- 导入文件经过类型、大小、病毒和公式注入检查。

### 15.3 内容安全

- 所有素材必须有来源和授权状态；
- 事实性内容保留来源引用；
- 平台规则校验和租户敏感词校验是确定性步骤；
- AI 校验只能作为补充，不能取代人工审核；
- 记录发布快照 hash，支持事后追溯。

## 16. 可观测性与审计

### 16.1 指标

```text
social_publish_jobs_total{platform,status}
social_publish_duration_seconds{platform}
social_publish_retry_total{platform,error_category}
social_publish_status_unknown_total{platform}
social_connector_errors_total{platform,error_code}
social_analytics_sync_lag_seconds{platform}
social_due_jobs_count
```

指标标签禁止包含 `tenant_id`、账号名、内容标题等高基数字段。

### 16.2 日志

日志包含 `request_id/tenant_id/job_id/account_id/platform`，但账号 ID 使用内部 ID，平台响应必须脱敏。错误日志遵循“后端保留详细信息、前端返回安全摘要”原则。

### 16.3 业务审计

至少记录：

- 账号绑定、验证、权限变化和解绑；
- 母版/版本生成和修改；
- 提交审核、批准、驳回；
- 创建、取消、重试发布任务；
- 人工发布包下载和人工确认；
- 数据同步、导入和导入回滚。

## 17. 测试策略

### 17.1 单元测试

- 状态机只允许合法转换；
- 修改内容使旧审批失效；
- 幂等键阻止重复任务；
- 能力解析拒绝不支持动作；
- 各连接器内容映射、错误映射和指标映射；
- 凭证和日志脱敏；
- 租户过滤；
- 重试分类和退避；
- 视频号人工确认不能伪装成 API 发布。

### 17.2 契约测试

所有连接器复用同一套契约测试：

```text
validate_account returns capabilities
validate_variant never mutates input
unsupported method raises CapabilityNotSupported
map_error returns stable category
normalized metrics retain raw source
credentials never appear in serialized result
```

新增平台必须通过连接器契约测试。

### 17.3 集成测试

- 公众号 Mock Server：token、素材、草稿、提交、轮询、限流、超时；
- 发布任务领取和多 Worker 防重；
- 进程在提交前/后崩溃的恢复；
- 视频号发布包和数据导入；
- 文件与数据库的租户隔离；
- API 权限和越权访问；
- 前端状态与后端真实状态一致。

### 17.4 真实账号验收

- 使用非生产测试公众号验证完整闭环；
- 发布低风险测试内容；
- 验证平台接口权限、配额、状态延迟和数据延迟；
- 视频号由运营人员完成一次人工发布与数据导入；
- 真实账号验证未执行的项目不得标记“已完成”。

## 18. 新增平台指南

新增平台时只执行以下步骤：

1. 确认官方授权、发布和数据能力；
2. 定义平台标识和 `ContentSpec`；
3. 实现 `SocialPlatformConnector`；
4. 实现平台授权和凭证刷新；
5. 实现内容映射、媒体上传、发布/辅助发布；
6. 实现错误分类和指标归一化；
7. 注册连接器；
8. 通过通用契约测试和平台集成测试；
9. 在前端平台目录增加图标、名称和授权入口；
10. 更新能力矩阵，不修改核心状态机。

如果某平台需要核心协议无法表达的新能力，先扩展通用 capability/port，并证明至少有两个平台或明确的领域需求需要它；不得把单平台字段直接塞入核心服务接口。

## 19. 首期验收标准

1. 微信公众号完成“计划—素材—母版—平台版本—审核—草稿—立即/定时发布—状态回查—数据同步”真实账号闭环。
2. 微信视频号完成“计划—平台版本—审核—发布包—人工发布—人工确认—数据导入”闭环。
3. 发布任务在重复请求、多 Worker、进程重启和外部超时场景下不产生可确认的重复发布。
4. 审核后修改内容必须重新审核。
5. 所有表、文件、API、缓存和任务按租户隔离。
6. 平台凭证不会出现在接口响应、日志、审计详情或 Agent 上下文。
7. 新增一个测试连接器不需要修改 Planning、Review、Publish、Analytics 核心服务。
8. UI 不把“平台已受理”“人工确认”显示为“API 已发布成功”。

## 20. 已知待确认项

1. 微信公众号首期使用直填凭证还是直接申请第三方平台授权。开发计划先支持直填凭证，但接口保留授权策略抽象。
2. 真实公众号可用的数据接口、指标范围和延迟。
3. 视频号是否存在面向目标租户的定向官方发布或数据权限。
4. 项目统一文件存储是否已有满足素材长期保留、签名访问和清理策略的完整实现。
5. 租户内审核角色是否复用现有角色，还是需要细粒度 RBAC 扩展。

以上待确认项不得通过猜测写入实现。Phase 0 验证后如结论改变，先更新本设计和开发计划。
