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
| D11 | 获取路径优先级（2026-09-14 修订 D1） | **URL 抓取为主路径，freepublish 接口降为辅助**。架构分两层：①公共底座=「URL → 抓正文（mp 文章页公开可直抓，反爬狠的是列表接口而非文章页）→ 图片转存 → 入库」管道，所有来源共用；②清单来源可插拔，按优先级：手动/批量粘贴链接（MVP 即有）→ 第三方清单服务（新榜/极致了等，需商务+合规评估——极致了有腾讯不正当竞争诉讼记录）→ Playwright 扫码登录租户自有公众号后台读"发表记录"（自有账号场景覆盖最全、无第三方成本与灰色地带，代价是登录态维护）→ 群发完成事件回调 MASSSENDJOBFINISH（含 ArticleUrl，但需租户改服务器配置且可能接管自动回复，摩擦大，兜底）→ freepublish 凭据路径（已实测，仅覆盖"发布"渠道，保留辅助）。 |

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

```
租户后台配置 appid/secret ──► tenant_channel_configs (wechat_mp, Fernet 加密)
                                        │
              定时 tick / 智能体主动获取 / 租户后台立即获取
                                        ▼
                          统一触发入口 → WeChatMPSyncService（tenant_id + config_id）
                 ① stable_token → access_token（Redis 按 expires_in 缓存）
                 ② freepublish/batchget 拉发布集合（no_content=1 轻量列表）
                 ③ 与 bs_wechat_mp_articles diff：新增 / 变更(hash) / 删除
                                        ▼
                      逐文章处理管道 process_article()
   拉正文HTML → 正文提取 → 图片下载转存 → VL图片描述(按张计费)
      → LLM分类+标签+时效抽取(独立轻调用) → 拼正文(文字+图片描述+标签)
      → TextChunker 分块 → embedding → 单事务落 documents/chunks/chunks_vec
                                        ▼
        售前智能体 knowledge_base_search（source_type=「公众号内容」分类代号，
        检索 SQL 过滤 status='active' 且未过期）
```

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

## 4. 凭据管理（D1）

- `tenant_channel_configs` 新增 `channel_type=wechat_mp`，凭据 `{appid, secret}`；另允许受校验的 `sync_interval_hours`（默认 6，正整数）与 `enabled`（默认 true）。已有渠道支持多个配置，不能用 tenant_id 作为账号身份。
- 数据与锁按 `(tenant_id, config_id)` 隔离；记录 appid 作为来源身份，`documents.external_id` 使用 `appid:article_id:item_key`。同租户同 appid 禁止重复配置（并发创建需数据库唯一约束，可用 JSON 表达式部分索引）。appid 创建后不可原地改绑，换账号须新建配置。删除配置只停止同步，保留文档与账本；删除后重建同 appid 必须接续原文档幂等键。
- 当前 `ChannelConfigDB` 按 RPA 类型分支加密，并无通用“加密类型集合”。抽取现有 Fernet 原语，独立实现 wechat_mp secret 的加密/解密/掩码，不复用强制 `listen_mode=server` 的 RPA codec。覆盖 create/update、单查/列表、后台解密读取及 `update_config_field` 绕过保护；secret 未提供/掩码回传保留旧值，必填 secret 不允许清空。
- verify 专门分流，不创建消息渠道 adapter：先 stable_token，再 `batchget(count=1,no_content=1)`；token 与列表成功才设置 verified；存在消息时还需验证一条详情调用，失败不置 verified。空列表可验证接口可调用，但不能证明历史覆盖。分别返回认证、权限、白名单、限流/网络、未知错误，禁止仅凭一个错误码猜测认证状态。
- secret 变更撤销 verified、递增凭据版本并失效 token；并发 verify 仅能写回其验证版本，旧任务提交前检查配置仍启用且版本未变。接口授权检查在解密前完成。
- `ChannelConfig.vue` 增加凭据表单、验证结果和 IP 白名单指引；检查渠道枚举、前端类型、启动扫描和 adapter 工厂调用点，保证内容源不会进入收发消息链路。

## 5. 抓取与增量同步（D4 前半）

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

### 6.1 HTML 正文提取与多图文合并（content.py）

- P1/P2 将一条消息的未删除子篇按返回顺序拼接为一篇文档，段落格式为“子篇标题 → 正文”，子篇间有明确分隔；文档标题取首个有效子篇标题，多篇时附篇数。分块保持子篇边界，避免两个产品/活动的描述混入同一 chunk；仍是一条 documents 记录。
- metadata 保存各子篇当前顺序、标题、url、content_source_url、删除标记和处理结果。顺序仅为本次内容定位，不是稳定 ID。chunk metadata 关联对应标题及来源字段；不把首篇 URL 用作全部子篇引用，也不承诺 url 永久有效。content_source_url 是“阅读原文”目标，不等同于公众号文章地址。
- 混合消息中 P1 可解析的文字子篇照常入库，纯图子篇保留标题/图片占位并记 deferred；整条消息无可用文字才整体 deferred。P2 以版本升级补解析图片，不因部分纯图而丢弃其他文字。统计以消息/合并文档为主，metadata 另存子篇数量及待处理数量。
- P3 若继续合并，分类/标签先作用于整篇文档；不得用某一子篇活动结束时间排除整篇仍含有效内容的文档。混合时效时整篇 expires_at 保持 NULL，子篇时效过滤或拆分需求须在 P3 设计时另行明确。


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

## 8. 数据表设计（bs_ 前缀，对齐 crawler 规约）

```sql
-- 文章当前状态（每条子文章独立），以下为目标 DDL，迁移须按项目规则补齐幂等保护
CREATE TABLE bs_wechat_mp_articles (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  config_id TEXT NOT NULL,
  appid TEXT NOT NULL,
  article_id TEXT NOT NULL,              -- 微信发布消息 ID
  item_key TEXT NOT NULL DEFAULT 'combined', -- P1/P2 固定：一条消息合并为一篇文档
  user_id TEXT,
  pipeline_version TEXT,
  processing_status TEXT DEFAULT 'pending',
  missing_count INT DEFAULT 0,
  last_run_id BIGINT,
  title TEXT, url TEXT,
  publish_time TIMESTAMP, wx_update_time TIMESTAMP,
  content_hash VARCHAR(64),
  doc_id INTEGER,                          -- 关联 documents.id
  sub_category VARCHAR(64), tags JSONB,
  status VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/missing/deleted
  error_message TEXT,                     -- 最近一次失败原因（脱敏后）
  image_count INT DEFAULT 0, image_parsed_count INT DEFAULT 0,
  last_synced_at TIMESTAMP, last_checked_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT now(),
  UNIQUE(tenant_id, appid, article_id, item_key)
);

-- 同步运行账本（运营排障主入口，对齐 bs_crawler_runs）
CREATE TABLE bs_wechat_mp_sync_runs (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  config_id TEXT NOT NULL,
  user_id TEXT,
  created_at TIMESTAMP DEFAULT now(),
  agent_id TEXT,                         -- agent 触发时记录可信运行时身份
  session_id TEXT,                       -- 可空，关联会话；不保存对话正文
  owner_token TEXT,
  heartbeat_at TIMESTAMP,
  scan_complete BOOLEAN DEFAULT false,
  trigger_type VARCHAR(16) NOT NULL,      -- scheduled/agent/manual/retry
  status VARCHAR(32) NOT NULL,            -- running/success/partial_failed/skipped_no_credit/failed/interrupted
  total_count INT, new_count INT, updated_count INT,
  deleted_count INT, skipped_count INT, failed_count INT,
  credits_charged NUMERIC(12,2) DEFAULT 0,
  error_message TEXT,                     -- run 级失败原因（如 token 获取失败，脱敏）
  started_at TIMESTAMP, completed_at TIMESTAMP
);

CREATE TABLE bs_wechat_mp_sync_items (
  id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT,
  created_at TIMESTAMP DEFAULT now(),
  run_id BIGINT NOT NULL,
  article_row_id BIGINT NOT NULL,
  action TEXT,                           -- new/update/delete/restore/check
  status TEXT,                           -- running/success/failed/skipped/deferred/interrupted
  error_code TEXT, error_message TEXT,
  started_at TIMESTAMP, completed_at TIMESTAMP,
  billing_status TEXT DEFAULT 'pending', -- pending/charged/failed/unknown/not_required
  billing_reference TEXT,
  credits_charged NUMERIC(12,2) DEFAULT 0,
  UNIQUE(tenant_id, run_id, article_row_id)
);
```

`bs_wechat_mp_sync_items` 逐篇记录成功/失败/跳过/删除，不能用当前文章表冒充历史。无余额全轮跳过无需伪造逐篇处理记录。

索引：runs `(tenant_id, config_id, started_at DESC)`、每配置 running 部分唯一索引；articles `(tenant_id, config_id, processing_status)`；items `(tenant_id, run_id)`。所有按 ID 读写附带 tenant_id，doc_id 使用 INTEGER 对齐现有 documents.id；后台 user_id 可空，手动触发记录操作者。时间统一按既有 TIMESTAMP 约定存储 UTC，微信 Unix 时间显式转换，API 输出带时区。

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

> 2026-09-14 按 D11 重排：URL 抓取底座提前为 P1 核心；freepublish 凭据路径降为辅助；清单来源按 D11 优先级逐步接入。详细计划文档待按新分期重写。

| Phase | 内容 | 预估 |
|-------|------|------|
| P0 | WP0 已实测部分见 §13.2；补充实测：mp 文章页直抓稳定性（正文提取/图片 data-src/防盗链）、"已发布文章能否再群发"后台验证 | 0.5-1 天 |
| P1 | **URL 抓取底座**：单篇/批量链接粘贴导入（手动清单来源）+ mp 文章页正文提取（复用 §6.1 保序序列与图片占位）+ documents 加列与检索过滤 + 统一同步管道 service 层 + run/item 账本 + 三入口（定时对 URL 源意义为"重采更新检测"）+ 基础售前挂接 + portal/租户后台基础页 | 重估 |
| P2 | 图片下载转存 + VL 解析 + price_per_call 按张计费 + 纯图文章可用 | 2 天 |
| P3 | LLM 分类/标签/时效 + LLM 子分类自动建 + 租户独立中心页；**清单来源扩展**：Playwright 扫码登录自有公众号后台读"发表记录"（自有账号主路径）与第三方清单服务（合规评估通过后）并行接入为可插拔 provider | 重估 |
| P4（后置） | freepublish 凭据路径补齐（已实测部分并入）、事件回调兜底、crawler 分支按 §10 规约对齐合并 | 另议 |

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

**群发历史链接获取路径实测结论（2026-09-14）**：旧图文群发统计接口 `getarticlesummary` 实测返回 errcode 47009（api offline），官方标注停止维护，替代接口仅提供阅读统计数据（标题/msgid/阅读量），**不含正文与链接**。官方服务端接口层面无任何途径自动获取群发文章链接/正文。第三方抓取/RPA 有风控与合规成本，不作为产品承诺。

**群发文章覆盖性决定性实测（2026-09-14，触发 D11 架构修订）**：测试号 16:45 正常群发一篇（推送粉丝），2 分钟后复查 freepublish/batchget 仍 total_count=1（仅 16:26 发布渠道文章）；`material/batchget_material(type=news)` 同步复查 total_count=0。**结论钉死：群发文章既不进发布集合也不沉淀永久素材**，凭据路径无法覆盖租户"必推送"的主流发文习惯，故 D11 将 URL 抓取升为主路径。群发完成事件 MASSSENDJOBFINISH 含 ArticleUrl（第三方文档佐证），但后台手动群发是否触发该事件未经实测，且接入需租户改服务器配置，列为兜底。

**mp 文章页直抓实测（2026-09-14，P1 底座假设验证）**：用 getarticle 返回的 url 以普通浏览器 UA 直接 HTTP GET（无 cookie/无登录态）：返回完整页面（样本 3.5MB），含 `js_content` 正文容器、可提取标题、23 张 `data-src` 图片（mmecoa.qpic.cn），无验证码/无拦截。**结论：单篇文章 URL 直抓可行，P1 底座成立**；图片 CDN 下载与防盗链细节在 P2 实测。

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
- 验收覆盖三个入口分别成功、三入口同时触发只执行一次、智能体不能跨租户刷新/查 run、长任务状态返回、失败不声称最新、获取完成后检索到新增文章，以及历史分页完成/中断恢复不漏采。
