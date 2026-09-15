# 微信公众号内容入知识库 开发计划（D11/D12 定稿 + 二审修订版）

> 设计依据：[设计文档](../system/wechat-mp/wechat-mp-knowledge-ingestion-design.md)（D1~D12 确认，实测证据 §13.2，社区调研 §13.3）
> 状态：2026-09-14 二审修订：纳入可靠接收、租户串行队列、URL 身份算法、删除多信号判定、回调配置完整化、清单源实验提前。
> 风险：高（数据库迁移、外部回调入口、并发调度、租户隔离、计费），全程三智能体流程。
> P1 目标：回调/手动粘贴的 URL 可靠入库并可被售前检索；删除可感知；全程账本可查。
> **P1 能力边界（如实口径）**：新增发现靠回调+粘贴；主动操作=刷新已知内容+粘贴新 URL；"主动发现公众号最新文章"依赖清单源（WPS 实验验证后接入）。

## 0. 架构要点（定稿 + 二审修订）

- 两条抓取管道（`fetch_by_url` P1 主 / `fetch_by_api` P3 补）+ 可插拔清单来源，统一入 `WeChatMPSyncService`；工具/API/调度/回调全为薄入口。
- **可靠接收**：回调验签 → **同事务持久化事件 + 待处理 URL（pending）** → 返回 success → 后台领取处理。DB 不可用返回失败让微信重试；事件幂等键去重与文章 URL 去重分开。
- **并发**：租户级串行——Redis 锁与 DB running 唯一约束同为租户粒度；多触发源一律持久化排队，不丢批；config_id=NULL 的手动导入同规则。
- **URL 身份**：算法见设计 §5.4（original_url / fetch_url / external_id 三字段分离，短链 path token 或长链四参数组合为身份，白名单外参数剔除，页面 canonical 建别名）。
- **删除判定**：多信号（专用错误页结构 + js_content 缺失 + 明确删除文案共同命中）；网络/验证页/限流/解析失败一律不判删除。
- 分类惰性创建+售前挂接（D7）、软删除前台隐藏（D8）、embedding 独立 source_type 计费。

## 1. 工作包

### WP0 [x] 实测验证（2026-09-14 完成，主链路可行性）

已完成：回调事件携 ArticleUrl（ArticleUrlResult）、URL 直采无需登录态、freepublish 权限与边界、删除事件单次观察无、datacube 下线、agent2 诊断端点真实事件。
**未完成（单独跟踪）**：多图文群发回调、安全模式 AES 实收、freepublish 分页 >20、异常恢复实收、更多删除样本。

### WPS [ ] 托底清单源可行性实验（与 P1 并行 spike，0.5~1 天）

- 固定版本自部署 wechat-download-api（Docker），平台测试号扫码登录。
- 实测 3+ 公众号：历史分页完整性、去重、增量游标、登录过期与恢复、连续运行 24h、限流/风控表现；代理池必要性由实验数据决定。
- 产出：清单适配器契约（账号标识 + URL + 标题 + 发布时间 + 分页游标 + 完成状态）+ 接入/不接入结论 + AGPL 与风控评估记录。实验报告落 `docs/research/wechat-mp/`。
- 门禁：实验未完成前，对外不承诺"历史文章自动导入"能力。

### WP1 [x] 数据库 schema（依赖：无）——2026-09-14 完成（开发+独立测试+独立 CR 三智能体通过；并发专项含"running 时连续受理 queued""并发领取唯一成功"真实 PG 实证；CR 无 P0/P1，P2×2 已修：init 失败升 error 级、external_id TEXT 选型注释闭环；遗留 P2×2：unit 目录真实 DB 测试静默 skip、启动期 no-op DDL 取锁——登记后续）

- `documents` 加列：origin / external_id TEXT / status / expires_at + 唯一部分索引 `(tenant_id, origin, external_id)` + `(status, expires_at)` 索引。
- 四张 bs_ 表（结构与队列语义以设计 §8 二审定稿为准；DDL 三处同步：db_update.yaml 新批次 + init-postgres.sql + `src/wechat_mp/db.py` 幂等；登记 database_system_table.md）：
  - `bs_wechat_mp_articles`：文章唯一当前态（UNIQUE(tenant_id, external_id)；original_url/fetch_url/external_id 分离；status 含 alias/unconfirmed；processing_status + next_retry_at 退避）
  - `bs_wechat_mp_events`：回调收件箱（UNIQUE(tenant_id, config_id, event_key)；pending/done/failed；关联 run_id）
  - `bs_wechat_mp_sync_runs`：兼任执行队列（queued→running→终态；**租户级 running 部分唯一索引** `WHERE status='running'`——queued 不限条数可排队，领取时事务内 queued→running，与 Redis 锁同粒度）
  - `bs_wechat_mp_sync_items`：批次内逐篇任务（pending/running/终态 + billing_status）
- 空库/存量/重复执行验证；先迁移后部署依赖新列代码。

### WP2 [x] 知识库过滤与外部文档边界（依赖 WP1）——2026-09-14 完成（三智能体：独立测试抓 P1 下载接口跨租户探测泄漏已修；CR 3 个 P2 已修：票据流补 role、分类计数过滤 deleted、knowledge_base_tool OR 括号残留；终跑 75 passed；前置失败 5+3 经 stash 双向对照证实与本改动无关）

同前版：检索三分支 LIMIT 前过滤 active+未过期；列表/计数同条件；include_deleted 仅 platform_admin；外部文档禁编辑/移动/物理删（含批量）；无 file_path 禁下载给原文 URL；知识库现有套件回归全绿。

### WP3 [x] URL 直采管道（fetcher.py + content.py + identity.py，依赖 WP1）——2026-09-14 开发完成；独立测试（40 探针）+ 独立 CR 完成，CR 结论"需修复后交付"的 2 P1 + 5 P2 已全部修复，137 单测全绿

- **首任务：沉淀测试夹具**——用 WP0 实测 URL 重新抓取真实页面存 `tests/fixtures/wechat_mp/`（当前工作区尚无该目录，不得以"已有夹具"声明）。
- URL 规范化与身份（identity.py）：实现设计 §5.4 算法 + 别名解析；非法/非 mp 域拒收。
- 抓取：浏览器 UA、timeout 15s、间隔 ≥1s、单页 ≤8MB；SSRF 防护（仅 mp.weixin.qq.com，重定向逐跳校验）。
- 正文提取：js_content 保序序列、图片 `[图片N]` 占位、sanitize 孤立代理字符。
- **删除/异常多信号判定**：专用错误页 DOM 结构 + js_content 缺失 + 删除文案共同命中才判 deleted；验证页/限流/网络/解析失败 → fetch_failed / risk_blocked，进入退避重试队列（articles.next_retry_at），绝不误判删除。
- 单测：夹具提取、同文不同链撞键、跟踪参数剔除、三类异常页、SSRF 拦截。

### WP4 [x] 回调产品化——2026-09-14 完成（三智能体：独立测试 P0「config TEXT 列 jsonb 操作符致创建必 500」与 CR P0「RPA 兼容壳漏 re-export 致存档 poller 失效」均已修；P1×2（db.py 索引同步、update 回填）+P2 同批修复；终跑 540 passed；AES 真实算法端到端自测、密钥轮换、三态、排队受理三件套真实 PG 实证）

- `/api/wechat-mp/callback/{config_id}`：每配置独立 token + EncodingAESKey（渠道配置 JSON 存储，AESKey 按敏感字段加密）；支持明文+安全模式（WXBizMsgCrypt 解密 + msg_signature 校验）；绑定校验：事件 ToUserName（gh_xxx）与配置记录的公众号身份一致才受理。
- **可靠接收**：验签 → **同事务写 events（pending）+ queued run + pending items 三件套** → 返回 success → 后台领取；DB 不可用返回 500 让微信重试；event_key 幂等去重；恢复扫描 pending 事件。其他事件类型记录日志不处理，恒 success。
- 响应预算 <1s，入库异步。
- 配置引导与状态语义分离：`config_verified_at`（URL 验证通过时间，仅凭据/配置变更才撤销）/ `last_event_at`（最近收到事件，低频发文账号不误判失效）/ `last_error`；前端三态展示 + callback URL/token 复制 + 服务器配置图文指引 + **共存说明**（启用服务器配置接管后台自动回复；已有第三方占用 URL 时需中继方案，运营话术覆盖）。
- 密钥轮换：重置 token/AESKey 使旧配置失效并记录版本。
- 单测：验签/AES、事件解析（多子篇）、幂等键、DB 故障返回 500、身份不符拒收、恢复扫描。

### WP5 [x] 统一入库 service（service.py，依赖 WP1~WP3）——2026-09-15 完成（接手前开发已完成；本日补齐三智能体：独立测试全绿+启动安全、补 restore/失锁 2 探针；CR 1 P1 已修——纯图门禁占位符残留击穿 MIN_TEXT_CHARS 致纯图文章误入库计费，改正则整段剔除占位符+回归测试；顺手修 events 置 done 补 tenant_id；6 项遗留逐项定性：restore/check 计入 skipped_count 设计内、sub_category 重置 P3 分类上线时必改、master 行不存在防御与 balance_reason 登记）

- `process_url()`：规范化定身份 → 抓取提取（必须先抓页面，删除多信号判定）→ content_hash → 与已成功版本比对，相同记 check 跳过 embedding → 不同则拼正文 → chunk → embed → 单事务落三表（更新走 doc_id 快路径）→ 文章/run-item 同事务。
- **队列与归属（二审修订）**：受理即同事务建 queued run + pending items；events 关联 run_id；articles 只承载文章当前状态，不冒充队列。worker 按租户串行领取 queued run（事务内 queued→running）；恢复扫描 queued/stale running run 与 pending events/items。两个批次含同一 URL：各有独立 run/item 分别收尾，后处理者 hash 未变记 check；事件在其 run 全部 item 终态后由 worker 置 done。
- **处理顺序（二审修订）**：事件级去重（event_key 命中直接跳过重投递）；**刷新已有文章必须先抓取** → 删除多信号判定 → 算新 hash：相同才跳过 embedding；pipeline_version 变化或 doc 不存在仍需重建。受理阶段只有 URL，不存在"抓取前按 hash 跳过"。
- **别名收敛（三审修订）**：抓取后按设计 §5.4 识别短长链同文——保留主记录，别名行置 status='alias' + master_article_row_id；**同批次冲突不做 item 迁移**（撞 (tenant_id,run_id,article_row_id) 唯一键），重复项 item 标 skipped 并关联主 item，受理记录都保留；别名行已有 doc_id 的其 documents 置 deleted + metadata.merged_into_doc_id；证据不足保留 unconfirmed 不合并；别名查询限定 tenant_id。
- 分类惰性创建 + 售前挂接（master 实现等价 append_own_source；无售前实例记录待挂接原因）。
- 计费：embedding source_type='wechat_mp_embedding'；业务先提交计费后提交；unknown 不重扣；余额预检+单元复查；删除复核不受余额阻断。

### WP6 [x] 手动粘贴入口 + API + 页面（依赖 WP5）——2026-09-15 完成（三智能体：独立测试 227→228 passed + 44 项真实 PG 鉴权/隔离/限流探针 + 前端 build；CR 2 P1 已修——①pending 去重未限定活跃 run，孤儿 item 永久压制明确刷新请求，去重 join queued/running + 回归测试；②前端 URL 校验只放行短链，长链在 UI 永远无法导入，对齐后端口径；P2 登记：platform_admin 无 X-Tenant-Id 契约（WP7 已修为 400）、retry/recheck 限流统一与限流原子性（WP7 已修）、require_admin 放行 role='user' 属全局约定另立任务）

- `POST /api/saas/wechat-mp/import-urls`（≤50 条，域名校验）→ 排队（run trigger_type=manual）→ 返回 run_id + 排队语义说明。
- 管理端点：runs / runs/{id}（含 items）/ articles / retry / recheck；require_admin + 租户隔离；error 脱敏白名单。
- 前端：租户「公众号内容」页（配置引导三态、粘贴导入、运行状态、文章列表/失败原因）；portal 跨租户页。复用 Base* 组件。

### WP7 [x] 定时复核与队列驱动（scheduler.py，依赖 WP5）——2026-09-15 完成（三智能体：独立测试 248 passed + 时区口径/幂等/限流原子性/驱动循环真实 PG 探针，并修 1 处驱动锁同步 Redis 调用阻塞事件循环；CR 1 P1 已修——recheck item 遭 retry 受理翻转 processing_status 后落付费重建多扣一次 embedding，hash 快路径对 action='check' 豁免 status 条件（hash 与成功版本同事务原子写入，安全）+ 判别验证回归测试；驱动锁 TTL 300s 迭代间续期定性 P2 可接受——租户 Redis 锁+running 唯一约束双闸兜底；时区口径自洽（next_retry_at UTC naive/last_checked_at 会话 now()，读写同约定）登记长期统一 UTC）

- **队列即时驱动（二审修订）**：受理持久化成功后**立即通知 worker 领取**（进程内唤醒/Redis 通知，尽力而为）；另有短周期（1min）扫描兜底。通知丢失不丢任务——DB 队列是唯一事实来源。
- 30min tick 只负责生成**到期任务**：①失败退避到期（next_retry_at）重试入队；②存量 active 文章存活复核（默认 24h/篇，限速，复核任务同样走队列）；③复核中 hash 变化 → 更新入库。
- 租户级 Redis 锁 + owner/heartbeat/续租 + DB running 唯一第二道闸；Redis 故障拒绝启动；stale run 回收 interrupted；有限并发执行器。

### WP8 [x] 智能体工具 + 售前闭环（依赖 WP5/WP6）——2026-09-15 完成（三智能体：独立测试 263 passed + 薄工具纪律/身份可信/租户隔离/口径独立探针；CR 1 P1 已修——async execute 直调同步 service 违反假异步规范阻塞事件循环，3 处调用点 asyncio.to_thread 包裹；部署待办：售前子智能体 allowed_tools 增补 wechat_mp_sync/wechat_mp_sync_status，真机验收（测试矩阵末行）留待部署后实收）

- 薄工具 `wechat_mp_sync` / `wechat_mp_sync_status`：粘贴 URL 或触发复核；返回 run_id + 排队状态；身份取可信执行上下文；工具授权机制管理。
- 口径：只承诺"已提交获取/刷新"，不宣称发现最新；运行中/失败如实说明；完成后走 knowledge_base_search。
- 验收：粘贴/群发回调 → 入库 → 售前检索命中闭环。

### WP9 [ ] 接口通道提前开发（⏸ 2026-09-15 负责人确认场景必须、当日暂停待排期；原 P3「接口通道补齐」前移；依赖 WP5/WP7。开发未开始，仅本登记）

### WP10 [x] P2 图片 VL 解析提前（2026-09-15 负责人实测反馈：图文/纯图文章 URL 导入被 deferred，多模态解析优先做；依赖 WP5）——2026-09-15 完成（三智能体：独立测试 359 passed + 119 项真实探针——hash 稳定性（VL 措辞不进指纹）双重证实、SSRF 面逐项 fail-closed、按张计费三账一致，修 1 P1：混排（短文字+图）文章图片编号错位致 VL 描述张冠李戴，改图片序数枚举+变异验证回归锁定；CR 修 1 P1：deferred 文章不在重试/复核扫描范围致「将自动重试」承诺落空，复核条件扩 processing_status IN ('success','deferred') 复用 24h 节流与每 tick 20 篇限速；P2 登记：解码先于像素检查的内存尖峰（建议先查 size 再 load）、UNRECOGNIZED_TEXT 精确匹配改包含匹配、p2 升级存量文章首次复核各产生一次重建费用需部署公告；终态 361 passed + 前端 build 通过 + 启动安全）

- **图片下载转存**（§6.1/§13）：仅接受校验通过的微信 CDN HTTPS 地址（mmbiz/mmecoa.qpic.cn 等），逐跳重定向重新校验主机/IP，禁内网/回环/本地文件；Referer 头、单张 ≤10MB、超时 15s、解码像素上限、每文章图片上限（默认 30 张）；转存 `storage/tenants/{tid}/knowledge/wechat_mp/{article_row_id}/img_{n}.{ext}`，本地路径登记 articles/metadata。
- **VL 解析**（§6.2）：新建 vision.py——每张图独立 LLMGateway.chat()，不指定专用视觉模型：从 `list_multimodal_models()`（is_multimodal=TRUE）选已配置模型，failover 同过滤，无可用图片模型记 deferred 不发纯文本模型；OpenAI image_url base64 data URL（复用 src/core/agent.py:697 构造惯例，压缩到模型限制内）；单轮 user message=固定指令+1 图，不带任何会话上下文；指令聚焦「转述图片中的文字信息与活动内容（时间/地点/优惠/产品名），不评价不发挥」；Semaphore(3) 限流，单张失败重试 1 次后记 failed 继续。
- **按张计费**（D2）：`token_cost_prices` 加通用列 `price_per_call NUMERIC`（与 asr_price_per_call 区分），种子行 model_code='wechat_mp_image_parse' price_per_call=0.01（×usage_factor 100=1 积分/张，运营改 DB 调价）；每成功 1 张一条 chat_records（source_type='wechat_mp_image_parse'）复用 ChatRecordDB.create 原子扣减；VL token 用量记 usage_breakdown 供对账但收费按张。
- **管道接入**：`[图片N: 描述]` 插回正文序列对应位置；deferred 门禁改造——有图且文字不足不再直接 deferred，先 VL 解析，解析产出内容则继续入库，无可用模型/全部失败仍 deferred；PIPELINE_VERSION 升 'p2'（存量 active/deferred 文章凭 pipeline_version 变化自动重建，复用既有快路径机制）；文章级余额预检复用既有 no_credit 语义，逐张失败不中断。
- 单测：下载校验（域名白名单/重定向逐跳/内网拒绝/字节像素上限）、VL 模型选择与无模型 deferred、指令与上下文隔离、并发限流、单张失败重试、按张计费金额与 usage_breakdown、[图片N: ...] 插回、deferred 存量文章 p2 重建、压缩路径。

### WP11 [x] 回调配置体验补齐（2026-09-15 负责人反馈优先；依赖 WP4）——2026-09-15 开发完成（①自定义回调 Token：config_codec.is_valid_callback_token 3~32 位字母数字校验；create 留空/缺省=服务端生成不变，传合法明文=去空白加密入库；update 传合法明文=改密（credential_version 递增+撤销 config_verified_at+verified 置 0，与 rotate 语义一致），留空/掩码/null 保留旧值不变；API 层前置校验非法 400，设置 Token 时响应 callback_token_plaintext 一次性；rotate 端点保留未动。②ChannelConfig.vue 保存成功后「公众平台服务器配置」一站式面板：URL/Token/EncodingAESKey 三件套各带复制+逐步指引，success/warning token 风格零新依赖；表单新增可选「自定义 Token」输入（前端先行校验，与掩码展示/重置共存，未保存态提示"将使用自定义 Token"）。③基线用例改写断言增强。三智能体闭环：独立测试 46 项探针（自定义 Token DB Fernet roundtrip、真实验签路径旧 Token 403/新 Token 200、留空/掩码/null 保留旧值、非法 400）；CR 修 1 P1：update API 对掩码 Token 误 400 与 DB 层「掩码保留」矛盾（GET→PUT 整体回传场景破约），掩码前缀按未提供处理；终态 361 passed + 前端 build 通过）

- 支持**自定义回调 Token**（可选）：表单新增 Token 输入（留空=服务端生成），3~32 位字母数字校验，与公众平台侧填写值一致即可；服务端生成逻辑保留。
- 创建保存成功后**一站式配置面板**：弹窗内显著展示「配置三件套」——完整回调 URL、Token 明文（一次性）、EncodingAESKey，各带复制按钮 + 逐步指引（先复制 URL/Token/AESKey → 到公众平台服务器配置粘贴 → 保存启用 → 回列表看三态验证），消除"保存后自动生成"占位期的困惑。
- 列表页已有回调地址/三态/重置 Token 保持不动。

- 背景（WP0 实测支撑）：仅"发布"渠道文章在 batchget 集合（历史群发不可得）；企业认证服务号权限可用；getarticle 顶层 news_item 含 content/is_deleted/url；IP 白名单 40164 流程已验证。
- `client.py`：stable_token（Redis 缓存键含 tenant/config/凭据版本、expires_in 扣余量、账号级单飞刷新、无效 token 仅刷一次重试）+ batchget 分页遍历（count≤20、重复页/无进展检测）+ getarticle；timeout 15s、间隔 ≥200ms、指数退避+抖动+单轮总超时；权限/参数错误不盲重试；access_token/响应正文/异常原文不进日志。
- 对账与增量：每 tick 扫描 enabled+secret+verified 配置，sync_interval_hours 到期（默认 6h）→ 建 scheduled run；batchget(no_content=1) 全分页对账，article_id 对齐 articles.source_channel='api' 行（external_id=appid:article_id:combined）；首次/源 update_time≠wx_update_time/上轮失败/pipeline 升级 → 建 pending item 拉正文；源时间比较用 wx_update_time。
- 正文管道：getarticle → is_deleted 子篇排除 → 未删子篇按序拼「子篇标题→正文」（item_key=combined，一消息一篇文档，分块保持子篇边界，metadata 存子篇顺序/标题/url/删除标记）→ content.py 提取 → 后续与 URL 通道汇合（hash 比对/deferred 门禁/单事务落库/计费/售前挂接全复用）。
- 删除语义：全部子篇明确 is_deleted → 软删除整篇；空数组/字段缺失/类型异常≠删除，记异常保留旧版；**整条缺失防护**——分页失败/结构异常/重复页/总数漂移不执行缺失删除仅标记 missing，连续两次可靠完整扫描缺失+getarticle 详情复核（53600）才软删除。
- 跨来源重叠（§5.4）：接口文档入库前按子篇 url 规范身份查重，命中已入库 URL 文档 → documents.metadata 互标 related_doc_id，不物理合并。
- 配置与验证：verify 扩展三步实测（stable_token+batchget count=1+有消息时 getarticle）；40164 报错回显出口 IP 提示配 IP 白名单；ChannelConfig.vue 修正 secret/sync_interval_hours 字段语义（去掉"填了无效"的误导提示）。
- 单测：token 缓存/单飞/失效重试一次、分页遍历+重复页检测、增量对账（新增/变更/未变跳过）、is_deleted 子篇排除与全删软删、缺失防护两次扫描+详情复核、跨来源互标、来源 url 长期可用性依赖真机验收（WP0 待实测项：多图文样本、分页>20、整条删除延迟）。

## 2. 测试与验收矩阵

| 风险/意图 | 必测场景 |
|----------|----------|
| 可靠接收 | 事件先落库再返回、DB 故障返回 500 微信重试、幂等键去重、pending 恢复扫描、进程退出不丢 URL |
| 回调链路 | GET 回显、验签 403、明文/AES、多子篇多 URL、ToUserName 身份不符拒收、未知事件吞掉、响应 <1s |
| URL 身份 | 同文不同链撞键去重、不同子篇不撞键、长链跟踪参数剔除、短长链别名收敛（**同批次重复项 item 标 skipped + duplicate_of_item_id 关联主 item，两条受理记录保留，不触发唯一键冲突**；别名行置 alias、复核只跟主记录）、证据不足不合并、非法 URL 拒收 |
| URL 直采 | 夹具提取（文字/图片占位/标题）、删除多信号判定、验证页/限流不误删、SSRF 拦截、**刷新必抓页面再比 hash**（不接受抓取前跳过） |
| 队列与并发 | 回调+定时+手动+工具同租户并发排队不丢批、**已有 running 时仍可连续受理多条 queued（DB 约束实测）**、领取事务内 queued→running、锁过期回收、崩溃 stale 回收、退避重试不风暴、受理后立即唤醒+短扫兜底 |
| 幂等计费 | hash 未变零处理零计费、更新替换后旧内容不可检索、unknown 不重扣、无余额跳过付费不阻断删除复核 |
| 软删除 | 复核命中删除页 → 检索不可见+前台隐藏+管理端可审计；复核失败不误删 |
| 租户隔离 | 跨租户直访 404、回调 config_id 串租户不可达、售前挂接仅本租户 |
| 兼容回归 | 知识库套件全绿、手动上传不变、共享检索不变、渠道配置现有类型回归 |
| 真机验收 | agent2 群发 → 回调 → 入库 → 售前命中；删除 → 复核 → 软删除；多图文群发逐子篇入库 |

单测 `tests/unit/wechat_mp/`；集成 `tests/integration/` 真实 PostgreSQL；微信侧 mock + WP0/WP3 真实夹具。`./scripts/dev_test.sh <范围> -p no:cacheprovider -q`；前端 `npm run build`。

## 3. 流程与依赖

1. 顺序：WP1 → WP2 → WP3 →（WP4 / WP5）→ WP6/WP7 → WP8；WPS 与主线并行，实验结论决定 P4 接入排期。
2. 开发 → 定向自测 → 独立测试 → 独立 CR（重点：回调安全、可靠接收、SSRF、租户隔离、事务、计费、队列）→ 主控终检。
3. 开工时 ideas.md #79 标 🔧；逐项更新证据；未获授权不提交。
4. 部署：回调需公网 80/443（agent2 链路已验证）；`deploy/服务器部署现状.md` 登记回调端点。
5. P3/P4 依赖：freepublish 凭据在本地 .tmp（不入库）；清单源接入需 AGPL 法务确认 + 代理池预算（依 WPS 结论）。
