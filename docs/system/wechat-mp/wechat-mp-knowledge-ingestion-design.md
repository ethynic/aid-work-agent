# 微信公众号内容入知识库 设计文档

> 状态：2026-09-14 审阅修订，已按用户补充确认历史范围与三种获取入口（D9/D10）；外部接口能力仍需实测。
> 开发计划：[P1 开发计划](../../plans/plan-wechat-mp-knowledge-ingestion.md)。本次仅审阅文档，未开发或实测公众号。
> 关联：`docs/system/crawler/website-product-crawler-design.md`（feature/crawler-phase1 分支，网站爬虫，本设计的多数据源姊妹篇）

## 1. 背景与目标

为租户提供"把自有微信公众号内容接入知识库"的内置服务，使公众号文章（活动、产品、新闻）可被售前智能体等数字员工检索引用，支撑当季活动的智能售前问答。

**核心需求决策（已确认）**：

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 内容获取路径 | ~~一期只做凭据路径~~ **2026-09-14 修订（见 D11）**：WP0 实测证明群发文章不进 freepublish 集合也不进素材库，凭据路径只覆盖"发布"渠道；租户实际习惯是必推送（群发），凭据路径降为辅助 |
| D2 | 图片理解 | 用现有 provider 体系的多模态模型解析图片内容，**独立极简上下文**调用（不带 agent 任何无关上下文），**按张计费**，默认 1 积分/张，单价后台可配置。模型选型：优先复用平台已配置且经图片输入验证的模型，failover 仅限具备图片输入能力的模型；不能由“默认模型”推断多模态能力 |
| D3 | 分类 | 每租户知识库自动新建顶级分类「公众号内容」，其下由 LLM 自动建子分类（活动/产品/新闻等）并打标签 |
| D4 | 更新/删除/时效 | 跟随公众号增量更新；公众号侧删除 → 知识库**软删除**（标记后不参与检索，不物理删）；时效性内容（如活动结束）过期后自动排除检索 |
| D5 | 网站爬虫分支 | feature/crawler-phase1 独立开发不合并，后续按本设计 §10 的接入规约对齐 |
| D6 | 可观测性 | 与外部交互的每一次同步、每一篇文章处理都必须有结构化状态与失败原因可查；接入现有 loguru 日志 + `log_error` 表 + portal 错误日志页 + LLM 调用日志体系 |
| D7 | 分类创建时机 | **任务驱动惰性创建**：无同步任务运行不预建分类；首次同步产生首篇文章时幂等创建「公众号内容」分类并自动挂接售前智能体（与官网爬虫语义一致） |
| D8 | 软删除可见性 | 软删除文章**租户前台默认隐藏**（列表 API 默认过滤，管理端可审计查看），不参与检索但数据保留 |
| D9 | 历史范围（2026-09-14） | 接口能返回多少历史文章就获取多少，完整遍历可用分页，不额外限制历史日期或总篇数；接口未返回的历史不要求补采，不作为交付缺陷。内容解析仍按 P1/P2 分期。 |
| D10 | 获取入口（2026-09-14） | P1 同时支持租户后台定时任务、智能体主动获取最新内容、租户用户后台立即获取，统一同步管道。为完成"获取后能回答"的闭环，P1 包含基础知识来源挂接及租户可用的手动入口。 |
| D11 | 获取路径（2026-09-14 实测后定稿） | **双通道平级 + 托底清单源**：①**接口通道**（freepublish，自有号"发布"渠道文章，含定期对账）；②**回调+URL 通道**（服务器配置收 MASSSENDJOBFINISH 拿 ArticleUrl → URL 直采取正文，覆盖群发，已实测端到端成立）；③**手动粘贴 URL**（MVP 兜底，任意场景可用）；④**托底清单源**（见 D12）。内容获取只有"接口正文"和"URL 正文"两条抓取管道，统一进同一入库 service；清单来源（freepublish 列表/回调事件/手动粘贴/托底服务）全部可插拔。 |
| D12 | 托底清单源（2026-09-14） | 面向"非自有公众号"或"自有号不能配回调"的场景：采用社区方案自部署实例作为平台级"清单服务"——首选 **wechat-download-api**（GitHub 开源、FastAPI 同构技术栈；任意公众号管理员扫码登录公众平台后可拉**任意公众号**历史文章列表+正文；内置 TLS 指纹+代理池反风控；AGPL 3.0，独立部署调 API 使用，法务过目），备选 wewe-rss（微信读书接口）、Wechat2RSS（付费私有部署）、商业数据 API（新榜/极致了，合规评估后）。托底源产出的清单同样只回 URL，正文仍走统一的 URL 直采管道；平台侧扫码账号的风控成本由平台承担，需代理池。**可行性实验提前至 P1 并行 spike**（固定版本、3+ 公众号实测分页/去重/增量/登录过期恢复/限流；适配器契约：账号标识+URL+标题+发布时间+分页游标+完成状态；平台共享扫码账号需全局限速与公平排队），接入排期按实验结果定。 |

## 2. 技术前提与约束

### 2.1 微信公众平台 API 契约（官方核对见 §13.1）

- 本功能采用**发布能力接口**（freepublish），仅覆盖其返回集合，不将其描述为覆盖所有公众号历史的通用接口：
  - `POST /cgi-bin/stable_token`（或旧 `GET /cgi-bin/token`）→ access_token（7200s，需缓存与刷新）
  - `POST /cgi-bin/freepublish/batchget` → 已发布文章列表（offset/count 分页，`no_content=1` 拿摘要列表做增量 diff，`no_content=0` 拉正文）
  - 正文 `content` 字段为 HTML，图片为 `mmbiz.qpic.cn` CDN URL（`<img data-src="...">`）
- **前置条件（租户侧）**：按官方账号适用范围和发布能力权限说明接入（§13.1）；token 获取还需核对部署 IP 白名单。租户接入时通过验证按钮调用 token 与文章列表，存在消息时再用一条 article_id 验证详情接口；空列表不能验证详情响应结构。实际权限以账号实测为准。
- **能力边界（诚实声明）**：仅承诺导入该账号 freepublish 接口实际返回的已发布内容，不承诺覆盖全部历史群发、后台手工发布或下架内容。账号类型、认证与接口权限必须分别实测；列表缺失不直接等于删除。WP0 需用目标账号核对历史覆盖、多图文与删除行为，未通过前不启用自动软删除。

### 2.2 复用的现有底座

| 能力 | 复用点 |
|------|--------|
| 租户凭据存储 | `tenant_channel_configs` 表 + `/api/saas/channels/*` + `ChannelConfig.vue`（新增 channel_type=`wechat_mp`） |
| 敏感字段加密 | 现有 `src/channels/wecom_personal_rpa/secret_crypto.py` 的 Fernet；公共模块尚不存在，需要最小抽取并保留旧导入兼容，不复制密钥策略 |
| 调度 | `src/scheduler/manager.py` APScheduler + Redis 分布式锁（对齐 crawler `job_system_crawler_tick` 模式） |
| 知识库入库 | `TextChunker` / `TextEmbeddingV3Client` / `get_vector_db` / documents+chunks+chunks_vec 三表单事务 |
| 分类体系 | `knowledge_categories` 树（`parent_id`），文档落 `source_type`（顶级）+ `sub_category` |
| 多模态 | `LLMGateway` + 经能力验证的图片模型与 failover 路由；图片消息适配和调用日志是否保留 base64 必须在 P2 验证 |
| 计费 | `chat_records` + `token_cost_prices` + `calculate_*_credit_cost` + 同事务余额扣减，独立 `source_type` |
| 日志 | loguru 分片日志、`log_error` DB sink、portal `/portal/error-logs`、`llm_invoke_logs` JSONL |

## 3. 总体架构

> **2026-09-14 注**：本节时序图是早期接口通道视角。定稿架构以 D11/D12 与 §12 为准：**两条抓取管道**（`fetch_by_url` 直采 = P1 主；`fetch_by_api` freepublish = P3 补）+ **可插拔清单来源**（回调事件 / 手动粘贴 / freepublish 列表 / 托底清单服务），统一进 `WeChatMPSyncService`。§5 的 diff/删除细节适用于接口通道（P3）；URL 通道的删除感知走"URL 存活复核"（§13.2 删除实测）。

```
清单来源（可插拔）                     抓取管道（两条）
┌ 回调事件（群发 ArticleUrl）┐        ┌ fetch_by_url：mp 文章页直采（P1 主）┐
│ 手动粘贴 URL              │ ──URL──▶│ fetch_by_api：freepublish（P3 补）  │┐
│ freepublish 列表（P3）     │        └────────────────────────────────────┘│
│ 托底清单服务（P4）         │                                              ▼
└───────────────────────────┘              WeChatMPSyncService.process_article()
                        正文提取 → 图片转存/VL(P2) → 分类/时效(P3) → 拼正文
                        → TextChunker → embedding → 单事务落三表 → 计费
                                        ▼
              售前智能体 knowledge_base_search（过滤 active + 未过期）
```

**回调可靠接收（P1 必须）**：验签 → **同事务持久化事件与待处理 URL（pending）** → 返回 success → 后台按租户串行领取处理；事件幂等键去重；启动/崩溃恢复扫描 pending。数据库不可用时返回失败让微信重试，不确认未持久化的接收。

**并发规则（P1 统一）**：**租户级串行**——Redis 锁与数据库 running 唯一约束同粒度（租户），同租户多个触发（回调/手动/定时/工具）一律持久化排队，排队不丢；不允许"复用进行中的 run_id"冒充新 URL 已受理。

**模块**：新建 `src/wechat_mp/`（对齐 crawler 的 `src/crawler/` 独立模块模式）：

```
src/wechat_mp/
├── client.py        # WeChatMPClient：stable_token、batchget、get_article；httpx，限速重试
├── service.py       # WeChatMPSyncService：sync_tenant / process_article / diff / 软删除
├── content.py       # HTML 正文提取、图片下载转存、正文拼装
├── vision.py        # VL 图片描述（极简独立上下文）+ 按张计费
├── classify.py      # LLM 分类/标签/时效抽取（chat_lite 轻模型）
├── scheduler.py     # tick 注册、due 判定、Redis 锁
├── db.py            # bs_wechat_mp_* 三表（幂等 DDL，三处同步）
└── api.py           # /api/saas/wechat-mp/* 管理端点
```

知识库侧主要改动：`documents` 加 4 列、检索与列表/计数过滤、外部文档读写边界及入库事务适配（见 §7）；手动上传保持原行为。

**分层规则（2026-09-14 负责人确认）**：公众号读取、diff、入库、软删除、计费的全部能力只在 `WeChatMPSyncService`（service 层）实现；调度 tick、HTTP API、智能体工具/skill 均为**薄入口**——只做鉴权、参数解析、状态回传后直接调 service，禁止在工具/skill 侧复制任何抓取或入库逻辑。

## 4. 配置管理（回调为主，凭据为辅）

> P1 的核心配置是**服务器配置回调**；appid/secret 凭据属接口通道（P3）。两通道共用 `tenant_channel_configs` 的 `channel_type=wechat_mp` 配置记录。

- **回调配置字段（P1）**：config JSON 存 `callback_token`（建配置时服务端生成）、`encoding_aes_key`（用户从公众平台后台复制，按敏感字段 Fernet 加密入库、响应掩码）、`appid` 与 `original_id` **分开保存**（公众号原始 ID gh_xxx：用于事件 ToUserName 绑定校验；appid：用于 AES 解密接收方 AppID 校验与 P3 接口通道；两者不可互比）、`enabled`（默认 true）。回调 URL 为 `/api/wechat-mp/callback/{config_id}`，每配置独立 token。前端提供原始 ID 输入框与获取指引（公众平台后台"设置与开发→公众号设置"页可见）。
- **凭据字段（P3 接口通道）**：`secret`（同样 Fernet 加密）、`sync_interval_hours`（默认 6，正整数）。
- 同租户同 appid 禁止重复配置（数据库唯一约束）；appid 创建后不可原地改绑；删除配置只停止同步，保留文档与账本；重建同 appid 接续原 external_id 幂等键。
- **密钥轮换**：重置 callback_token / encoding_aes_key 递增凭据版本，旧版本事件拒收并记录；secret 变更撤销接口通道 verified。
- **验证状态三态分离**：`config_verified_at`（回调 URL 验证通过时间，仅凭据/配置变更撤销）、`last_event_at`（最近收到事件时间；低频发文账号不得以无事件判失效）、`last_error`（最近处理错误）。接口通道 verify（P3）：stable_token + batchget(count=1) + 存在消息时一条 getarticle 三步实测。
- 加密实现：当前 `ChannelConfigDB` 仅 RPA 类型分支加密；最小抽取 Fernet 原语为公共模块（保持旧导入兼容），wechat_mp 独立 codec（不带 RPA listen_mode 副作用），覆盖 create/update、单查/列表掩码、后台解密读取及 `update_config_field` 绕过保护；掩码回传保留旧值，敏感字段不允许清空。
- **共存说明（运营必覆盖）**：启用服务器配置后公众平台后台自动回复等被接管；一个公众号仅一个回调 URL，已有第三方占用时需中继转发方案。
- `ChannelConfig.vue` 增加表单（callback URL/token 展示与复制、AESKey 输入、appid、IP 白名单与服务器配置图文指引）；检查渠道枚举、启动扫描与 adapter 工厂调用点，内容源不进入收发消息链路。

## 5. 抓取与增量同步（D4 前半）

> **2026-09-14 注**：本节（5.1~5.3）为**接口通道（freepublish，P3）**的设计细节；P1 主链路是回调+URL 直采（§3 定稿架构）。其中并发锁、退避、错误分类等机制两通道共用，但锁粒度统一为**租户级串行**（见 §3），本节早期的"每配置"表述以 §3 为准。

### 5.4 URL 身份与规范化（URL 通道，P1）

- 分别保存三个字段：`original_url`（收到的原始链接）、`fetch_url`（实际抓取用）、`external_id`（规范身份）。
- **规范身份算法**：短链 `mp.weixin.qq.com/s/{token}` 以 path token 为身份；长链 `/s?__biz=..&mid=..&idx=..&sn=..` 仅保留 `__biz/mid/idx/sn` 四个定位参数组合为身份，**白名单外参数一律剔除**（scene/sessionid/subscene/clicktime/enterid/exportkey/uin/key/pass_ticket/devicetype/version/lang/ascene 等跟踪参数）；其他 mp 路径（如 `__biz` 缺失）拒收并提示。
- **短长链别名与收敛**：抓取后从页面 `var msg_link` / canonical 提取对方形态。**若受理阶段已分别为短链/长链建了两条文章行**，识别为同文后必须收敛：保留先成功入库（或先创建）的行为**主记录**；别名行标记 `status='alias'`、`master_article_row_id` 指向主记录，复核任务只跟主记录；别名行不再参与复核与检索。**同批次冲突处理**：`sync_items` 唯一键为 `(tenant_id, run_id, article_row_id)`，同批次同时含短链 A、长链 B 时**不做 item 迁移**——重复项的 item 标记 `skipped` 并在其 error_code/metadata 关联主 item，两条受理记录都保留；跨批次的 item 天然指向各自受理时的行，不受此限。**别名行已有 doc_id 的**：其 documents 行置 `status='deleted'` 且 metadata 记 `merged_into_doc_id`（检索自动隐藏，管理端可审计原因），不得只改 articles.status 留重复文档可被检索。判定证据不足（页面未给出可靠 canonical/msg_link）时保留 `unconfirmed` 状态各自独立，**不得仅凭任意 canonical 值合并**。别名查询限定 tenant_id。
- 跨来源重叠（P3 接口 combined 文档 vs URL 单篇）：接口文档 metadata 记录其 url 的规范身份，入库前查重；命中已存在 URL 文档时在 documents.metadata 互标 `related_doc_id`，不物理合并（保留各自来源语义），P3 定去重细则。
- 测试：同文不同 URL 撞键、不同子篇不撞键、长链跟踪参数剔除、非法 URL 拒收。

### 5.1 调度与并发

- 每 10 分钟扫描 enabled 且 verified 的配置，默认每 6 小时一次；定时、智能体主动获取、后台手动和单篇重试共用同一执行入口及 `(tenant_id, config_id)` 互斥。
- Redis 锁带随机 owner，30 分钟租约定期续期，仅 owner 可释放；失锁即停止后续调用与写入。数据库每配置至多一条 running run（部分唯一索引），事务提交时锁定 run 并检查 owner/状态，避免旧 worker 续写。
- run 保存 heartbeat_at；过期 run 只能在确认旧租约失效并取得新锁后标记 interrupted，再启动新 run。Redis 不可用时拒绝启动，不能无锁降级。多 worker 的 scheduler 主锁不是手动触发互斥的替代。
- 使用有并发上限的后台执行器，不按租户无限创建 daemon 线程；停机停止接新任务，超时中断留可恢复账本。due 使用最近尝试时间，失败与无余额均有退避，避免每 tick 重试；手动触发仍限流。
- 余额不足仅跳过需付费的内容处理，已验证可靠的源删除检查不应被余额阻断；记录 skipped_no_credit。每个付费单元前复查余额，启动预检不保证整轮费用，遵循现有余额扣减规则。

### 5.2 身份、增量与删除

1. `batchget(no_content=1)` 拉全分页，保存消息级 article_id、update_time、集合数量与完整性。**P1/P2 一条消息一篇文档**，单图文与多图文都固定 `item_key=combined`，external_id=`appid:article_id:combined`。不寻找子篇稳定 ID，不以数组下标作为持久身份；P3+ 有实际需求再另行设计拆分迁移。
2. 首次、源 update_time 与已成功处理版本不同、上轮失败、pipeline_version 升级或 deleted 后重新出现时拉正文。源时间与保存的 wx_update_time 比较，不能和 last_synced_at 比较。源变更信号可靠性须经 WP0 验证，否则采用周期正文核对。
3. `content_hash=sha256(保序子篇标题/正文/图片节点/删除标记及影响入库的元数据)`，以结构化序列编码避免拼接歧义。hash 相同且处理版本相同、旧文档存在时仅更新检查信息，不重复 embedding/计费；P1→P2 升级必须重新处理图片待处理项。
4. **删除处理**：完整有效消息详情中 `news_item[].is_deleted=true` 的子篇不参与合并；剩余子篇重建同一文档，全部明确为 deleted 则软删除整篇。空数组/字段缺失/类型异常不能当作全部删除，记异常保留旧版。原文未删但某子篇有删除标记时，若重建失败或余额不足，先将旧合并文档置 deleted 以排除已删内容，记录待重建原因，成功重建再恢复 active。此例外优先于通常的“更新失败保留可检索旧版”。
   **整条消息缺失防护**：分页失败、响应结构异常、重复页、总数漂移或权限变化时，不执行缺失删除。完整扫描仅标记 missing；连续两次可靠完整扫描仍缺失，并通过 WP0 确认的详情不存在信号复核后，才同事务软删除 documents 和源记录。接口不支持可靠删除判据时，保留 missing 待审计，不自动删除；空集合也走相同门禁。
5. 删除与处理状态分离：源状态 active/missing/deleted，处理状态 pending/success/sync_failed/deferred。普通更新失败保留上一成功版本文档；涉及源明确删除的例外按上条隐藏旧版；本次失败不能改写已提交 hash。重现时恢复文档 active；hash 未变可复用旧 chunks。
6. 单文章失败不阻断整轮；文章当前表保存最新状态，每次处理另写 run-item 历史。运行级失败记 failed，进程退出记 interrupted，不得把拉取失败或空结果误报 success。

### 5.3 微信客户端

- token 缓存键包含 tenant_id/config_id/凭据版本，TTL 根据 expires_in 扣安全余量，不固定假设 7000 秒；刷新使用账号级单飞锁。无效 token 仅刷新并重试一次，避免并发强刷风暴。
- 所有请求 timeout 15s、请求间隔至少 200ms；系统繁忙/限流/短暂网络错误有限指数退避并加抖动，设置单次运行总超时。权限/参数错误不盲重试。分页按返回数量推进，并检测重复页和无进展。
- 请求 URL 中的 access_token、响应正文与 httpx 异常均不得直接写日志；输出内部错误码及脱敏摘要。

## 6. 内容处理管道

### 6.1 HTML 正文提取（content.py）

- **P1 粒度（URL 通道）**：一个文章 URL = 一篇文档。回调多子篇时 ArticleUrlResult 逐子篇给独立 URL，**各成独立文档**（external_id=各自规范 URL），不做合并。
- 解析 js_content 容器为保序序列 `[{type:'text'|'image'}]`；去脚本/样式与已明确识别的非正文节点，不按主观"广告"规则删促销正文；图片描述（P2）插回原文位置。
- **P3 粒度（接口通道）**：freepublish 一条多图文消息合并为一篇文档（item_key=combined），未删除子篇按返回顺序拼接"子篇标题 → 正文"，子篇间明确分隔；分块保持子篇边界；metadata 保存各子篇顺序/标题/url/删除标记（顺序仅当次定位，不是稳定 ID）。
- 混合消息中文字子篇照常入库，纯图子篇保留占位记 deferred；P2 版本升级补解析。分类/时效（P3）作用于整篇文档时不得用某一子篇的时效排除整篇。
- 纯图片文章（正文文字 < 阈值如 20 字）是重点场景，完全依赖 §6.2 图片描述成为可检索内容。


- 解析 content HTML：去脚本、样式与已明确识别的非正文节点，不按主观“广告”规则删促销正文，提取正文结构化序列 `[{type:'text'|'image', ...}]`（保序，图片描述能插回原文位置上下文）。
- 图片处理：下载 `data-src` URL（带 Referer 头、限速、单张 ≤10MB、超时 15s）→ 转存 `storage/tenants/{tid}/knowledge/wechat_mp/{article_id}/img_{n}.{ext}` → 登记到文章 metadata（图片本地路径列表，供前端知识库文档详情展示）。
- 纯图片文章（正文文字 < 阈值如 20 字）是重点场景（活动宣传长图），完全依赖 §6.2 的图片描述成为可检索内容。

### 6.2 图片 VL 解析（vision.py，D2）

- 每张图独立一次 `LLMGateway.chat()` 调用，**不指定专用视觉模型**：
  - 仅选择经图片输入探针验证的已配置模型，failover 同样过滤能力；平台更换默认模型后重新验证。无可用图片模型时记录 deferred，不发送到纯文本模型。
  - 图片按现有项目惯例构造 OpenAI `image_url` base64 data URL content（下载与解码分别限制字节数和像素数；允许范围内再按模型限制压缩）。
  - 消息极简：单轮 user message = 固定解析指令 + 1 张图片。**不带** agent 上下文、会话历史、工具 schema（D2）。
  - 解析指令聚焦"转述图片中的文字信息与活动内容（时间/地点/优惠/产品名），不评价不发挥"。
  - 并发 Semaphore(3) 限流（对齐 crawler OCR 并发），单张失败重试 1 次后记 failed 继续。
- **计费（D2）**：每成功解析 1 张写一条 `chat_records`（`source_type='wechat_mp_image_parse'`），`credit_cost = ceil(price_per_call × usage_factor)`：
  - `token_cost_prices` 新增通用列 `price_per_call NUMERIC`（现有 asr_price_per_call 是 ASR 专用，不混用），种子行 `model_code='wechat_mp_image_parse'`，`price_per_call=0.01`（×usage_factor 100 = **1 积分/张**，默认值，运营改 DB 即可调价；单价管理 UI 暂不做）。
  - 同事务扣 `tenants.credit_balance`（复用 `ChatRecordDB.create` 原子扣减链路）；VL 实际 token 用量记入 `usage_breakdown` 供对账，但**收费按张不按 token**（价格可预期，用户明确要求）。
- 图片描述以 `[图片N: ...]` 形式插回正文序列对应位置。

### 6.3 分类、标签与时效（classify.py，D3 + D4 后半）

P1 创建固定「公众号内容/未分类」并完成基础售前知识来源挂接，以支持主动获取后检索；LLM 分类与时效属于 P3。其他智能体按既有工具和知识来源授权使用，不自动向所有智能体开放。

- 每篇文章一次 `chat_lite()` 轻模型调用（独立上下文，输入=标题+正文前 2000 字+图片描述摘要），JSON 输出：
  ```json
  {"category": "活动|产品|新闻|其他", "tags": ["春季促销", "瓷砖"], "expires_at": "2026-10-01|null", "reason": "..."}
  ```
- **分类落点（D3，已确认 2026-09-10）**：**惰性创建**——不在租户配置凭据时预建分类，而是在**首次同步实际产生首篇文章时**幂等执行：租户无「公众号内容」顶级分类则自动创建（名称固定，source_type 走现有自动代号 `k_xxx`），并**同时自动挂接到该租户的售前智能体**（参考 crawler 分支的 `SubagentKnowledgeSourceDB.append_own_source`（当前工作区未实现，需新增等价能力），单条 SQL 原子 jsonb 合并、幂等、只增不删）；LLM 返回的 category 映射为其下子分类（不存在则自动建），文档落 `source_type=公众号内容代号` + `sub_category=子分类代号`。租户已有分类树零影响；无同步任务/无文章的租户库中不出现任何残留分类（与官网爬虫的任务驱动创建语义一致）。
- **标签**：无标签机制现状下，tags 存 `documents.metadata.tags`（应用层 JSON 序列化，现列类型为 TEXT），同时把 tags 拼进 chunk 文本尾部（`[标签: 春季促销, 瓷砖]`）使其参与向量/全文检索——不新建标签表，最小改动。
- **时效（D4，P3）**：仅对有明确结束日期及原文证据的结果设置 `documents.expires_at`；相对/缺少年份日期不推断，校验失败为 NULL。租户时区的结束日按次日零点（排他边界）转换 UTC，同期提供管理端人工修正；人工覆盖值在源未变时不被重同步覆盖。检索 SQL 统一加 `status='active' AND (expires_at IS NULL OR expires_at > now())`。
- 分类调用计费走 `source_type='wechat_mp_classify'`（`record_background_llm_usage` 模式）。

## 7. 知识库集成与兼容性（核心约束）

### 7.1 documents 表加 4 列（幂等，三处 DDL 同步）

```sql
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS origin VARCHAR(32) NOT NULL DEFAULT 'manual_upload',
  ADD COLUMN IF NOT EXISTS external_id VARCHAR(128),          -- appid:article_id:item_key（实现时验证长度，超限改 TEXT）
  ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active',   -- active/deleted
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;              -- 时效排除
CREATE UNIQUE INDEX IF NOT EXISTS uq_documents_origin_external
  ON documents(tenant_id, origin, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_documents_status ON documents(status, expires_at);
```

### 7.2 兼容性保证清单

| 现有行为 | 影响 |
|----------|------|
| 手动上传 API / 批量上传 | **零改动**，origin 落默认 `manual_upload`，status 默认 active |
| 现有文档检索 | 默认值等价于现状（active + 永不过期），检索结果不变 |
| `chunks` / `chunks_vec` | 不加列不动结构，隔离照旧 JOIN documents |
| 跨租户共享 `tenant_range.py` | 不变；「公众号内容」是租户自己的顶级分类，共享与否走既有授权 |
| 售前智能体 / `knowledge_base_search` | 不变；仅在知识来源已授权挂接后可用；动态分类列表不能替代挂接 |
| 删除文档 API | 手动上传保留物理删除；外部源写操作限制见 §7.4 |
| 知识库 embedding 计费 | 外部源 embedding 复用 `_record_knowledge_embedding_billing`，source_type 区分 |

### 7.3 检索与访问过滤

`hybrid_retriever.py` 全文检索和 `vector_db.py` 向量检索的本租户、共享、全局分支均加 active/未过期条件；排序/LIMIT 前过滤。列表与 count_documents 使用同一过滤条件，响应模型同步扩展；`search_documents` 是搜索端点，不是列表实现。

租户侧文档详情、chunks 和下载票据也检查软删除可见性；platform_admin 可审计已删除内容，但普通检索不开放 deleted。expires_at 只限制检索，过期文章仍可管理查看。外部源保留原文 URL，P1 不承诺原始文件下载，无 file_path 时前端禁用下载并返回明确错误。

### 7.4 更新与计费语义

- HTML 提取、embedding 与可选摘要在事务外完成；同一连接的单事务更新 documents（含 raw_text/summary/metadata/total_chunks/updated_at）、先删 chunks_vec 再删 chunks、插入新 chunks/向量，并提交源表成功 hash、处理版本和 run-item 成功状态。失败保留旧文档和成功版本。不可调用各自提前 commit 的 helper 拼成“伪事务”。
- P1 summary 使用正文确定性截断，不调用未列入范围的 LLM 摘要；后续若启用摘要，应记录局部返回的 usage，不共享 KnowledgeService._last_summary_usage 可变状态。
- 当前 `_record_knowledge_embedding_billing` 的 source_type 固定为 knowledge_embedding，不能直接传新来源；P1 需最小参数化或明确适配，默认行为保持兼容。
- 业务提交后再计费（fail-open），chat_records 与余额扣减在计费事务内原子完成。run-item 保存 billing_status（pending/charged/failed/unknown）、计费关联标识与费用，run 的 credits_charged 按已成功扣款汇总。此顺序允许漏扣，不等于天然防双扣；响应未知记 unknown，P1 禁止自动补扣或重试扣款。未来补扣必须先实现数据库唯一的计费幂等键。计费失败不回滚内容，也不触发重新 embedding。
- P2 成功图片描述及计费结果按图片内容 hash+解析版本保存，以便文章后续失败后复用；不得只依赖整篇 hash 防止重试时重复按张收费。
- 手动上传的修改/删除行为保持；外部源文档在 P1 禁止通用编辑、移动和物理删除入口（返回明确业务错误，前端隐藏对应操作），统一从源同步管理，避免 doc_id 悬空和人工修改被覆盖。批量入口也检查。

## 8. 数据表设计（bs_ 前缀，对齐 crawler 规约；2026-09-14 二审定稿）

```sql
-- 文章当前状态（唯一当前态记录；批次任务归属见 sync_items）
CREATE TABLE bs_wechat_mp_articles (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  config_id TEXT,                          -- 可空：手动粘贴无配置
  external_id TEXT NOT NULL,               -- 规范身份（设计 §5.4）
  original_url TEXT, fetch_url TEXT,
  source_channel VARCHAR(16) NOT NULL,     -- callback/manual/freepublish
  title TEXT,
  publish_time TIMESTAMP, wx_update_time TIMESTAMP,
  content_hash VARCHAR(64),
  doc_id INTEGER,                          -- 关联 documents.id
  sub_category VARCHAR(64), tags JSONB,
  status VARCHAR(16) NOT NULL DEFAULT 'active',   -- active/missing/deleted/alias/unconfirmed
  master_article_row_id BIGINT,            -- alias 行指向主记录
  processing_status VARCHAR(16) DEFAULT 'pending',-- pending/success/sync_failed/deferred
  pipeline_version TEXT,
  next_retry_at TIMESTAMP,                 -- 失败退避
  error_message TEXT,                      -- 最近一次失败原因（脱敏后）
  image_count INT DEFAULT 0, image_parsed_count INT DEFAULT 0,
  last_synced_at TIMESTAMP, last_checked_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(tenant_id, external_id)
);

-- 回调事件收件箱（可靠接收：先落库再返回 success）
CREATE TABLE bs_wechat_mp_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  config_id TEXT NOT NULL,
  event_key TEXT NOT NULL,                 -- MsgID+Event 等幂等键
  run_id BIGINT,                           -- 关联受理批次
  payload JSONB,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending/done/failed
  received_at TIMESTAMP DEFAULT now(),
  processed_at TIMESTAMP,
  error_message TEXT,
  UNIQUE(tenant_id, config_id, event_key)  -- 组合键，跨配置不碰撞
);

-- 同步运行账本 + 执行队列（queued→running→终态；受理即建 queued run 与 pending items）
CREATE TABLE bs_wechat_mp_sync_runs (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  config_id TEXT,
  user_id TEXT,                            -- 操作者；后台触发可空
  created_at TIMESTAMP DEFAULT now(),
  agent_id TEXT, session_id TEXT,          -- agent 触发时记录可信运行时身份；不保存对话正文
  owner_token TEXT,
  heartbeat_at TIMESTAMP,
  trigger_type VARCHAR(16) NOT NULL,       -- callback/scheduled/manual/agent/retry/recheck
  status VARCHAR(32) NOT NULL,             -- queued/running/success/partial_failed/skipped_no_credit/failed/interrupted
  total_count INT, new_count INT, updated_count INT,
  deleted_count INT, skipped_count INT, failed_count INT,
  credits_charged NUMERIC(12,2) DEFAULT 0,
  error_message TEXT,
  started_at TIMESTAMP, completed_at TIMESTAMP
);
-- 租户级串行：每租户至多一条 running（与 Redis 锁同粒度）；queued 不限条数，受理即排队
CREATE UNIQUE INDEX uq_wechat_mp_runs_active
  ON bs_wechat_mp_sync_runs(tenant_id) WHERE status = 'running';

CREATE TABLE bs_wechat_mp_sync_items (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT,
  created_at TIMESTAMP DEFAULT now(),
  run_id BIGINT NOT NULL,
  article_row_id BIGINT NOT NULL,          -- 归属文章当前态记录
  action TEXT,                             -- new/update/delete/restore/check
  status TEXT,                             -- pending/running/success/failed/skipped/deferred/interrupted
  error_code TEXT,                         -- 固定原因码，不混存记录 ID
  duplicate_of_item_id BIGINT,             -- 同批次别名重复项关联主 item（status='skipped' 时）
  error_message TEXT,
  started_at TIMESTAMP, completed_at TIMESTAMP,
  billing_status TEXT DEFAULT 'pending',   -- pending/charged/failed/unknown/not_required
  billing_reference TEXT,
  credits_charged NUMERIC(12,2) DEFAULT 0,
  UNIQUE(tenant_id, run_id, article_row_id)
);
```

**队列语义（二审修订）**：`sync_runs` + `sync_items` 即执行队列，不另建队列表——受理时同事务建 queued run + pending items；回调事件写 events（pending）并关联 run_id；articles 只维护文章当前状态。worker 按租户串行领取 queued run；崩溃恢复扫描 queued/running（stale）run 与 pending events/items。两个批次含同一 URL：各有独立 run 与 item，后处理的 item 命中 hash 不变记 check；两个 run 分别独立收尾。事件在其关联 run 全部 item 终态后置 done。

索引：runs `(tenant_id, created_at DESC)` + 上述活跃部分唯一索引；articles `(tenant_id, processing_status, next_retry_at)`；events `(status, received_at)`；items `(tenant_id, run_id)`。所有按 ID 读写附带 tenant_id，doc_id INTEGER 对齐 documents.id；时间统一 UTC，微信 Unix 时间显式转换，API 输出带时区。

DDL 同步：`src/wechat_mp/db.py` 幂等初始化（接入实际启动注册点）+ `deploy/db_update.yaml` 递增批次 + `deploy/init-postgres.sql`；不再编辑旧 db_update.sql。系统表变更还要同步 `docs/system/database_system_table.md`。升级在新检索 SQL 上线前完成，验证空库与存量库重复执行。

## 9. 日志与可观测性（D6）

分层设计，运营排障路径从粗到细：

1. **运行账本（第一入口）**：`bs_wechat_mp_sync_runs` + `bs_wechat_mp_sync_items` 历史 + `bs_wechat_mp_articles` 当前状态 承载每次同步、每篇文章的状态与失败原因。前端两个页面：
   - portal 管理端：跨租户同步记录列表 + 文章明细（对齐 `CrawlerTaskManager.vue` 模式）
   - 租户后台：P1 在现有配置/管理页面提供“立即获取”、运行状态和失败原因；P3 再扩展独立中心页，不把手动入口推迟到 P3。
2. **ERROR 日志链**：同步/抓取/VL/计费异常 `logger.error`（module 绑定 `wechat_mp`）→ 现有 `error_log_sink` 脱敏落 `log_error` 表 → portal `/portal/error-logs` 页可查可标记处理。**错误信息写库前走脱敏白名单**（对齐 crawler `error_sanitizer` 模式，appid 仅作受控来源标识；secret/access_token 不进入账本或日志）。
3. **文件日志**：`log/agent/aid-work-agent_YYYYMMDD.log` 按 run 记录关键步骤（INFO：diff 结果计数；WARNING：单文章失败；ERROR：run 级失败），日志行带 `tenant_id + run_id + article_id` 上下文字段，可 grep 串联。
4. **LLM/VL 调用**：P2/P3 必须先确认日志脱敏：不记录文章全文、图片 base64、凭据或完整请求 URL，只保存模型、用量、耗时、run/item 标识；不能假设默认日志已经安全。VL 与分类调用经 LLMGateway 进入 `log/llm/llm_invoke_logs_*.jsonl`，计费进 `chat_records`（portal 积分用量页可按 source_type 对账）。
5. **会话与后台关联**：同步执行仍以 run/item 账本追踪；智能体触发的工具调用沿用现有会话工具观测，记录 run_id 关联，不将整个后台任务伪装为一直运行的会话调用。

已知小缺口（本设计不阻塞，登记后续）：`log_error` 表无 `tenant_id`/`channel` 结构化字段，跨租户过滤靠 module 文本——建议随后给 `log_error` 加 `tenant_id` 列（小改动，惠及所有渠道错误）。

## 10. 外部内容源接入规约（供 crawler 分支及未来数据源对齐）

本设计与 crawler 共同沉淀的**多数据源入知识库架构规约**，feature/crawler-phase1 保持独立开发，合并前按此对齐：

1. **独立模块**：每个数据源一个 `src/<source>/`，自带 client/service/scheduler/db/api；不污染 `src/knowledge/` 核心。
2. **知识库唯一契约**：外部源只通过「`documents.origin + external_id` + 复用 TextChunker/embedding/单事务落三表」入库；不绕开 documents 主表，不私建向量表。
3. **幂等与增量**：`(tenant_id, origin, external_id)` 唯一约束 + `content_hash` 指纹，hash 与处理版本均不变且文档存在时不重处理不计费。
4. **调度**：APScheduler tick + Redis 分布式锁 + running 第二道闸，注册进 `src/scheduler/manager.py`。
5. **计费**：每处理单元独立 `source_type` 的 `chat_records` + 同事务余额扣减 + fail-open 提交顺序及 unknown 扣款不自动重试（详见 §7.4，顺序本身不保证幂等）+ run 启动余额预检。
6. **错误与脱敏**：run/item 两级历史账本及文章当前状态 + error 脱敏白名单 + log_error 链。
7. **DDL 三处同步** + 单测/集成测试齐备 + 三智能体开发流程。

crawler 分支合并时的差异点（登记，不现在处理）：crawler 用私有 `bs_crawler_products.doc_id` 关联而本设计用 documents.origin/external_id 统一契约，合并时迁移；crawler 的 OCR（paddleocr 云）与本设计的图片解析（经能力验证的 provider 图片模型）是两种图片解析路线，保留各自选择权，归 `content.py` 同级策略。

## 11. 风险与开放问题

| 风险 | 应对 |
|------|------|
| 租户公众号未认证 / freepublish 接口无权限 | verify 交互前置暴露；运营话术明确前置条件 |
| IP 白名单变更（服务器迁移）导致全线 token 失败 | run 级错误原因明确报"IP 白名单"；deploy 文档登记 |
| 微信侧限频 | batchget 分页限速 + 失败退避；同步频率默认 6h |
| 活动结束时间 LLM 抽取错误 | expires_at 仅作检索排除，不删数据；P3 同期提供管理端修正；只接受有明确结束日期及原文证据的结果，相对/缺少年份日期不推断，租户时区次日零点（排他边界）转换 UTC，校验失败用 NULL |
| 长图 VL 解析质量 | 解析指令要求转述原文不发挥；单图超大时先压缩到 VL 模型限制内 |
| 大量图片租户成本 | 按张计费透明可查；content_hash 防重复解析；可在配置层加每文章图片上限（默认 30 张） |

~~开放问题~~（2026-09-10 已与负责人确认，转为正式决策 D7/D8）：
- **D7 分类创建与售前挂接时机**：任务驱动惰性创建——无同步任务运行时不预建任何分类；首次同步产生首篇文章时才幂等创建「公众号内容」分类并自动挂接售前智能体（与官网爬虫"有任务才建官网产品分类"的语义一致）。
- **D8 软删除可见性**：软删除（status='deleted'）文章在**租户知识库前台默认隐藏**（`GET /documents` 默认过滤 deleted，加 `include_deleted` 参数仅供管理端审计查看），不参与检索、不展示，但数据保留可审计。

## 12. 分期开发计划（概要，详细计划另立）

> 2026-09-14 按 D11/D12 定稿重排：回调+URL 直采为 P1 核心，接口通道（freepublish）P3 补齐，托底清单源 P4 接入。详细计划文档按此重写。

| Phase | 内容 | 预估 |
|-------|------|------|
| P0 | ✅ 实测完成（§13.2）：回调事件+ArticleUrl、URL 直采、freepublish 权限/边界、删除无事件、datacube 下线 | 已完成 |
| P1 | **回调+URL 主链路**：诊断端点产品化（验签/明文+AES 安全模式/多公众号路由）+ 事件→ArticleUrl→URL 直采→正文提取→入库统一 service + documents 加列与检索过滤 + run/item 账本 + 手动粘贴 URL 入口 + 删除 URL 存活复核 + 售前挂接 + portal/租户基础页 + 服务器配置引导（含 token 生成/验证交互） | 重估 |
| P2 | 图片下载转存 + VL 解析 + price_per_call 按张计费 + 纯图文章可用 | 2 天 |
| P3 | LLM 分类/标签/时效 + 子分类自动建 + 租户中心页 + **接口通道补齐**（wechat_mp 凭据类型、freepublish 定期对账、发布渠道文章同步） | 重估 |
| P4（后置） | **托底清单源接入**：wechat-download-api 自部署评估（AGPL 法务、代理池、扫码账号运营）→ 对接清单 API；crawler 分支按 §10 规约对齐合并 | 另议 |

全程三智能体流程（开发 → 独立测试 → 独立 CodeReview），单测覆盖 diff 算法、hash 幂等、软删除、过期过滤、计费金额、脱敏；集成测试覆盖同步全链路（mock 抓取与接口）。

## 13. 2026-09-14 审阅记录与外部验证门禁

- 当前工作区核对：`src/core/secret_crypto.py` 不存在；Fernet 原语在 RPA 模块；`append_own_source` 尚不存在；数据库启动读取 `deploy/db_update.yaml`；documents.metadata 为 TEXT；embedding 计费 helper 来源固定；列表与计数分别实现。crawler 分支只作为参考，不能声明这些能力已合入。
- P2 图片下载仅接受经过校验的微信 CDN HTTPS 地址，每次重定向重新验证主机/IP，禁止内网、回环与本地文件；限制响应字节、解码像素、图片数量与超时。正文 HTML 不直接渲染，模型处理原文作为数据，不能执行原文指令。
- 短于 20 字不等于纯图片：P1 有图片且文字不足时 deferred（非技术失败，不每轮重试）；无图片的有效短文本仍可入库；空内容跳过并记原因。P2 提升 pipeline_version 后补处理。
- 分类只在准备好的首篇文档提交事务中幂等创建，失败不遗留空分类；并发唯一性使用稳定分类标识，不能仅先查名称再插入。
- **来源更正**：前次字段理解来自第三方 SDK 搜索结果（silenceper/wechat、feng19/wechat 等），当时未读到官方正文，不能称为已核实契约。本轮 web 浏览工具仍失败，但通过 Python urllib 直接 HTTPS GET 已成功读取官方页面正文（2026-09-14，HTTP 200），核对结果见 §13.1；未调用真实公众号 API。
- **历史范围已确认**：以接口返回范围为准，遍历全部可用分页，不为获取不到的历史增加 URL 补采；WP0 记录覆盖事实，不再将“必须覆盖后台手工发布历史”作为门禁。
- **获取入口已确认**：定时、智能体、后台手动三入口均纳入 P1。基础挂接和租户立即获取入口同步提前，确保入库后可被已授权智能体检索；不将触发能力等同于跨租户或全部知识库访问权。


### 13.1 官方字段核对记录（2026-09-14）

以下依据官方页面正文，不依据 SDK 推断；D9 则是用户确定的需求范围，既不依赖“全部历史可取”的假设，也不证明该能力。

| 核对项 | 官方契约与实现含义 |
|--------|-------------------|
| [batchget](https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublish_batchget.html) | POST，offset/count 分页，count 1~20；no_content=1 省略正文。顶层 total_count/item_count/item；每个 item 含 article_id、update_time、content.news_item。计数是消息素材，不是展开子篇数。 |
| [getarticle](https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublishgetarticle.html) | POST，输入 article_id；返回顶层 news_item，不能套用列表的 item[].content 路径。子篇包含 title/author/digest/content/content_source_url/thumb_media_id/thumb_url/url/is_deleted 等，字段表未给独立稳定子篇 ID。 |
| 删除标记 | 两个接口字段表均列出 is_deleted（boolean）；按明确标记排除子篇。未说明整条消失时列表/详情最终表现、传播延迟或分页快照一致性，仍须 WP0 实测，不把缺失直接当删除。 |
| [删除发布文章](https://developers.weixin.qq.com/doc/subscription/api/public/api_freepublishdelete.html) | article_id 定位消息，index 从 1 起指定子篇，省略或 0 删除全部；这是删除请求的定位参数，不是稳定子篇 ID。本功能不调用删除接口，仅读取其契约。 |
| 权限 | 三页适用范围表列“公众号：仅认证；服务号：可调用”，其中认证限定企业主体；错误 48001 为接口未授权。[发布能力说明](https://developers.weixin.qq.com/doc/offiaccount/Publish/Delete_posts.html)另说明自 2025 年 7 月回收个人主体、企业未认证及不支持认证账号的这些权限。不可仅凭账号类型或 token 成功承诺可用，须按目标账号权限实测。 |
| 错误与链接 | getarticle 列出 53600 为无效 article_id，并未说明只表示删除，不能直接用它确认删除。url 被字段表描述为临时链接，content_source_url 是阅读原文地址；文案中有草稿用语，长期可用性须实测，不自行改释为永久链接。 |
| 历史边界 | batchget 定义成功发布集合并提供分页，未在本次页面正文承诺所有历史群发/手工发布内容均在内。按 D9 遍历实际集合即可，WP0 记录差异，不要求补采。 |

WP0 剩余验证：多图文样本、is_deleted 实际触发、整条删除后的返回/延迟、分页 >20、来源链接长期可用性。缺少稳定子篇 ID 已不再阻塞 P1。以上为字段摘要，不保存官方全文副本。

### 13.2 WP0 实测记录（2026-09-14，测试服务号，凭据仅存本地 git 忽略目录）

| 实测项 | 结果 |
|--------|------|
| stable_token | ✅ 正常签发，expires_in=7200 |
| batchget 接口权限 | ✅ 无 48001，企业已认证服务号具备发布接口权限 |
| IP 白名单 | ✅ 流程验证：未配置时报 40164 并回显出口 IP，加入白名单后立即可用 |
| **发布 vs 群发边界（关键实证）** | 2025-07 群发的历史文章更新后仍不在 batchget 集合（total_count=0）；`material/batchget_material(type=news)` 素材库同为空（该文章未沉淀为永久素材）；后台新"发布"（不开群发通知）一篇后**秒级可见**（total_count=1）。结论：仅"发布"渠道文章可同步，历史群发内容接口不可得，D9 边界成立。租户运营话术：历史内容可在后台重新"发布"（不推送粉丝）纳入同步范围 |
| getarticle 字段结构 | ✅ 顶层 news_item；子篇含 title/author/digest/content/url/thumb_url/is_deleted 等；实测样本 is_deleted=false，url 为 mp.weixin.qq.com 文章链接，content_source_url 为空 |
| 正文图片格式 | ✅ content HTML 中图片为 `<img data-src>`，CDN 域名实测为 `mmecoa.qpic.cn`（非仅 mmbiz）；样本文章 23 张图、纯文字 2180 字、HTML 19KB——正文提取与图片转存设计（§6.1）成立 |
| 控制台中文乱码 | 实测中 Windows 终端 GBK 显示乱码属本地显示问题，数据本身 UTF-8 正常；另注意微信 JSON 响应可能含孤立代理字符，解析需 `errors='replace'` 兜底（复用知识库 sanitize_text 思路） |

待实测（有样本后补）：多图文消息、子篇 is_deleted=true 表现、整条删除后列表/详情行为与延迟、分页 >20 条、文章 url 长期可用性。

### 13.3 社区清单方案调研记录（2026-09-14，支撑 D12）

| 方案 | 机制 | 覆盖 | 评估 |
|------|------|------|------|
| **wechat-download-api**（github.com/tmwgsicp） | 任意公众号管理员扫码登录公众平台（凭证约 4 天，带过期预警 webhook），调公众平台内部接口 | 任意公众号历史文章列表（分页+链接+标题+时间）与正文，含反风控（curl_cffi TLS 指纹+SOCKS5 代理池+限频） | **首选托底**。FastAPI 同构、Docker 部署、HTTP API 完整；AGPL 3.0（独立部署调 API，对外 SaaS 需法务过目）；平台承担扫码账号风控，建议配代理池 |
| wewe-rss（github.com/cooderl） | 微信读书接口，v2 宣称更稳定 | 公众号历史发布文章列表 | 备选；不需公众平台扫码，机制不同可对冲单一失效风险 |
| Wechat2RSS（xlab.app） | 私有部署付费服务，2021 年运营至今 | RSS 源（24h 内收录） | 备选；付费省心但数据经第三方 |
| 商业数据 API（新榜/极致了/次幂） | 第三方数据服务 | 历史清单+正文+互动数据 | 最后选；极致了有腾讯诉讼记录，需合规评估；按调用付费 |
| RSS 免费平台（Feeddd/WeRss 等） | — | — | 已实测社区反馈基本不可用/延迟数月，排除 |

统一约束：托底源只负责产出**文章 URL 清单**，正文一律走系统内统一的 URL 直采管道（不依赖托底源的正文接口，避免锁定与口径分叉）。

**群发历史链接获取路径实测结论（2026-09-14）**：旧图文群发统计接口 `getarticlesummary` 实测返回 errcode 47009（api offline），官方标注停止维护，替代接口仅提供阅读统计数据（标题/msgid/阅读量），**不含正文与链接**。官方服务端接口层面无任何途径自动获取群发文章链接/正文。第三方抓取/RPA 有风控与合规成本，不作为产品承诺。

**群发文章覆盖性决定性实测（2026-09-14，触发 D11 架构修订）**：测试号 16:45 正常群发一篇（推送粉丝），2 分钟后复查 freepublish/batchget 仍 total_count=1（仅 16:26 发布渠道文章）；`material/batchget_material(type=news)` 同步复查 total_count=0。**结论钉死：群发文章既不进发布集合也不沉淀永久素材**，凭据路径无法覆盖租户"必推送"的主流发文习惯，故 D11 将 URL 抓取升为主路径。群发完成事件 MASSSENDJOBFINISH 含 ArticleUrl（第三方文档佐证），但后台手动群发是否触发该事件未经实测，且接入需租户改服务器配置，列为兜底。

**mp 文章页直抓实测（2026-09-14，P1 底座假设验证）**：用 getarticle 返回的 url 以普通浏览器 UA 直接 HTTP GET（无 cookie/无登录态）：返回完整页面（样本 3.5MB），含 `js_content` 正文容器、可提取标题、23 张 `data-src` 图片（mmecoa.qpic.cn），无验证码/无拦截。**结论：单篇文章 URL 直抓可行，P1 底座成立**；图片 CDN 下载与防盗链细节在 P2 实测。

**★ 回调事件决定性实测（2026-09-14，agent2 诊断端点 /api/wechat-mp/callback）**：公众号后台配置服务器配置（URL+Token、明文模式）后，**后台手动群发一篇（推送粉丝，SentCount=92 全部成功）数秒内收到 MASSSENDJOBFINISH 事件**，事件 XML 含 `ArticleUrlResult/ResultList/item/ArticleUrl`（mp.weixin.qq.com/s/ 短链）；用该 URL 直抓正文成功（完整页面、标题、23 图）。**结论：后台手动群发触发事件且携带文章 URL，「回调拿 URL + 直采取正文」主链路端到端成立，租户保持正常群发推送习惯零改变。** 注意：事件字段为 `ArticleUrlResult`（非旧文档示例的 CopyrightCheckResult 内嵌），多图文时 Count>1 逐子篇给 URL；群发文章删除后是否有回调/事件未实测。接入前提：租户需配置服务器配置（一个公众号仅一个 URL，启用后接管后台自动回复），运营话术需覆盖。

**删除事件观察（2026-09-14，单次样本）**：服务器配置生效期间在后台删除一篇群发文章，回调未收到任何事件（当日日志仅有 MASSSENDJOBFINISH 一条）。**观察结论（单次样本，非全平台保证）：删除无回调**；设计按"无删除事件"保守处理——软删除感知靠定期 URL 存活复核：对已入库文章定期重抓 URL，删除判定须多信号（专用错误页结构 + 正文容器缺失 + 明确错误提示文案共同命中，防止正文引用该句误删）；网络失败/验证页/限流/解析失败一律不得判删除。该复核与定时入口（D10）共用。后续有更多删除样本时回填本结论。

## 14. 三种获取入口与回答闭环（P1）

| 入口 | 触发条件 | 结果 |
|------|----------|------|
| 租户定时任务 | 已启用并验证的配置按周期 due | 后台拉取与入库，记录运行结果 |
| 智能体主动获取 | 用户要求最新内容，或智能体判断需要刷新且具备工具授权 | 发起/复用同步任务，查询状态，完成后检索已入库内容 |
| 租户后台立即获取 | 具备配置管理权限的租户用户点击按钮 | 立即提交任务，页面显示运行状态、成功/失败数量与原因 |

- 三入口共用同一个同步服务、身份隔离、锁、hash 去重、余额检查及账本；trigger_type 分别为 scheduled/agent/manual，retry 为运维重试。手动与智能体绕过六小时 due 周期，但仍受限流、余额和运行互斥约束；不能因刚同步过就悄悄跳过明确的刷新请求。
- 同配置已有运行时返回其 run_id 和 running 状态，不新建重复执行；“立即获取”是立即提交，不保证网络拉取和入库瞬间完成。若任务已开始扫描，应返回 started_at，不能宣称覆盖请求时刻之后才发布的文章。
- 首次同步遍历全部接口可用历史，后续同步检查当前集合并只处理新增/变更；不另建“智能体直拉正文”旁路。大历史量分批执行并保存可恢复进度，单次时间上限只限制执行批次，不静默截断总历史；未完成全扫描前保持未完成状态，不允许缺失删除。offset 分页不稳定时重扫去重，不能盲用旧游标判断删除。
- 智能体提供两个受控能力：发起获取（返回 run_id、状态、started_at）、查询该运行结果（完成时间、计数、脱敏原因）。tenant_id、user_id、agent_id、session_id 从可信执行上下文获取，不接受模型自行指定租户或凭据；配置选择只允许本租户已授权配置。无配置返回“请先在后台配置公众号”，多配置且目标不明确时列出已授权配置供选择，不猜账号。
- 工具执行权限按现有智能体工具授权机制管理，不直接套用 HTTP require_admin，也不向所有对话用户默认开放。知识检索仍按知识来源授权；只读共享公众号知识不授予刷新源租户的权限。
- 同步成功后智能体调用现有 knowledge_base_search 回答并保留原文引用；运行中只说明正在获取，不能声称已拿到最新内容。若会话等待预算不足，返回可查询运行状态，不无限轮询或承诺未实现的主动通知。失败/部分失败时可使用旧知识，但需明确未更新完成及最近成功检查时间；无结果不能编造文章。
- P1 首篇有效文档入库时创建分类并幂等挂接本租户售前智能体；没有售前实例时保留成功入库，返回挂接未完成原因，后续实例创建/配置流程可补挂接。其他智能体使用既有知识来源配置；智能体触发工具本身不修改授权范围。
- **P1 能力边界（2026-09-14 评审确认措辞）**：P1 的"新增发现"依赖回调事件（租户已配服务器配置）与手动粘贴；智能体/手动的"主动获取"语义为**刷新已知内容 + 粘贴新 URL**，不是"主动发现公众号最新文章"。后者依赖清单源能力（D12 托底服务，P1 并行实验验证、按结果接入）；接口通道（P3）仅发现"发布"渠道新增。对用户的表述必须如实区分"检查已导入文章"与"发现新文章"。
- 并发语义以 §3 为准：租户级串行 + 持久化排队，"已有运行时返回 run_id"仅表示排队受理，不得丢弃新提交的 URL 批次。
- 验收覆盖三个入口分别成功、三入口同时触发排队执行不丢批、智能体不能跨租户刷新/查 run、长任务状态返回、失败不声称最新、获取完成后检索到新增文章，以及历史分页完成/中断恢复不漏采。
