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
**未完成（单独跟踪）**：安全模式 AES 实收、异常恢复实收、更多删除样本、多图文逐子篇与 freepublish 分页 >20 真机样本。
**2026-09-15 群发回调实收确认（负责人实验）**：MASSSENDJOBFINISH 事件实收且携群发文章链接，链接到手走既有 URL 直采管道；callback.py 的 ArticleUrlResult/ArticleUrl 逐子篇解析路径经 WP9 主控核对已覆盖，无解析缺口，不额外开发。

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

### WP9 [x] 接口通道（freepublish 定期对账同步）——2026-09-16 完成（负责人确认场景必须后重启；三智能体闭环：独立测试 489 passed（wechat_mp 393+渠道配置/知识库回归 91+真实 PG 并发/schema 6）+ 真实凭据探针两轮全绿（stable_token→batchget total=1 拉到真实文章→入库→二次对账 skipped=1 零重拉，临时租户自清理）+ 启动 import 安全+前端 build；测试期修 3 处 httpx.Client 连接池泄漏；CR 1 P1 已修——sync_interval_hours 极端值（inf/NaN/超大 int）穿透可致全租户定时对账瘫痪，加 >0 守卫+8760h 夹逼+回归用例；P2 登记×7：missing 复核无批量上限且对账期不续租、runs 表 (trigger_type,config_id) 无支撑索引、对账异常路径 client 靠 GC、40014/42001 重试耗尽文案、40164 仅解析 IPv4、互标读改写竞态、_handle_restore 不刷 wx_update_time；计划外：test_wp12_summary 一条断言存量失真（94f69cf7 兜底计价未同步用例），经干净 worktree 复核后改 >0）

- **client.py**：stable_token Redis 缓存（TTL=expires_in-300s、secret sha256 前 8 位换键、账号级单飞、40001/42001/40014 删缓存刷一次重试一次）+ batchget_all 分页（重复页/无进展/总数漂移/600s 预算→reliable=False）+ getarticle（53600→ArticleNotFoundError、40164→IPWhitelistError 解析出口 IP）；瞬时错误指数退避+抖动，token/secret/响应正文/异常原文不进日志。
- **身份与增量（以接口实测返回定稿，用户核心要求）**：消息身份=`{appid}:{article_id}:combined`（batchget item.article_id 64 字符 appid 内唯一，no_content=1 仍带子篇元数据无正文）；增量=源 update_time vs articles.wx_update_time，未变且 success 且 pipeline 同 → 不建 item 不拉正文仅推进 last_synced_at（真实探针二次对账零重拉实证）。
- **调度**：30min tick 第③生成器扫 verified=1+enabled+appid/secret 非空配置，最近 scheduled run 超 sync_interval_hours（默认 6h，尝试时间口径自然退避）→ 同事务建 queued run（无 items，上限 100/tick）+ 唤醒领取。
- **对账（scheduled run 内三阶段）**：单事务 diff（新增建 articles 行+item、源变/sync_failed/pipeline 变经活跃 item 门禁建 item、missing 重现恢复、deleted 行源变恢复）→ 事务外逐条 getarticle 缺失复核（仅 53600 软删；不完整扫描整体不迁移）→ 软删落库带 missing 守卫；对账失败 run=failed 不误报 success。
- **管道分支**：source_channel='freepublish' item 走 getarticle——is_deleted 子篇排除、未删子篇「标题→正文」保序合并（复用 extract_article）、全删走 _handle_deleted、空数组/结构异常失败保留旧版绝不判删除；之后与 URL 通道在 hash 快路径处汇合（VL/总结/计费/售前挂接全复用）；子篇 url 仅身份计算+落库+互标（服务端不抓取，SSRF 面不变）；documents.file_path 回填首个未删子篇 url（WP12 语义）、documents.metadata.sub_articles 落子篇清单、跨来源互标 related_doc_id fail-open。
- **verify 与前端**：wechat_mp 配置含 appid+secret 时「验证连接」三步实测（stable_token→batchget count=1→有消息 getarticle）返回 api_check 节点；成功 set_verified(True)（回调 config_verified_at 三态不动），失败仅在无回调验证态时才撤销 verified；40164 回显出口 IP 加白提示；ChannelConfig.vue secret/sync_interval_hours 字段语义修正（去掉「预留可先留空」误导）。
- **遗留待真机**：多图文消息、分页>20、整条删除延迟的真实样本（路径均有单测覆盖）；P2×7 见上。

### WP10 [x] P2 图片 VL 解析提前（2026-09-15 负责人实测反馈：图文/纯图文章 URL 导入被 deferred，多模态解析优先做；依赖 WP5）——2026-09-15 完成（三智能体：独立测试 359 passed + 119 项真实探针——hash 稳定性（VL 措辞不进指纹）双重证实、SSRF 面逐项 fail-closed、按张计费三账一致，修 1 P1：混排（短文字+图）文章图片编号错位致 VL 描述张冠李戴，改图片序数枚举+变异验证回归锁定；CR 修 1 P1：deferred 文章不在重试/复核扫描范围致「将自动重试」承诺落空，复核条件扩 processing_status IN ('success','deferred') 复用 24h 节流与每 tick 20 篇限速；P2 登记：解码先于像素检查的内存尖峰（建议先查 size 再 load）、UNRECOGNIZED_TEXT 精确匹配改包含匹配、p2 升级存量文章首次复核各产生一次重建费用需部署公告；终态 361 passed + 前端 build 通过 + 启动安全）

- **图片下载转存**（§6.1/§13）：仅接受校验通过的微信 CDN HTTPS 地址（mmbiz/mmecoa.qpic.cn 等），逐跳重定向重新校验主机/IP，禁内网/回环/本地文件；Referer 头、单张 ≤10MB、超时 15s、解码像素上限、每文章图片上限（默认 30 张）；转存 `storage/tenants/{tid}/knowledge/wechat_mp/{article_row_id}/img_{n}.{ext}`，本地路径登记 articles/metadata。
- **VL 解析**（§6.2）：新建 vision.py——每张图独立 LLMGateway.chat()，不指定专用视觉模型：从 `list_multimodal_models()`（is_multimodal=TRUE）选已配置模型，failover 同过滤，无可用图片模型记 deferred 不发纯文本模型；OpenAI image_url base64 data URL（复用 src/core/agent.py:697 构造惯例，压缩到模型限制内）；单轮 user message=固定指令+1 图，不带任何会话上下文；指令聚焦「转述图片中的文字信息与活动内容（时间/地点/优惠/产品名），不评价不发挥」；Semaphore(3) 限流，单张失败重试 1 次后记 failed 继续。
- **按张计费**（D2）：`token_cost_prices` 加通用列 `price_per_call NUMERIC`（与 asr_price_per_call 区分），种子行 model_code='wechat_mp_image_parse' price_per_call=0.01（×usage_factor 100=1 积分/张，运营改 DB 调价）；每成功 1 张一条 chat_records（source_type='wechat_mp_image_parse'）复用 ChatRecordDB.create 原子扣减；VL token 用量记 usage_breakdown 供对账但收费按张。
- **管道接入**：`[图片N: 描述]` 插回正文序列对应位置；deferred 门禁改造——有图且文字不足不再直接 deferred，先 VL 解析，解析产出内容则继续入库，无可用模型/全部失败仍 deferred；PIPELINE_VERSION 升 'p2'（存量 active/deferred 文章凭 pipeline_version 变化自动重建，复用既有快路径机制）；文章级余额预检复用既有 no_credit 语义，逐张失败不中断。
- 单测：下载校验（域名白名单/重定向逐跳/内网拒绝/字节像素上限）、VL 模型选择与无模型 deferred、指令与上下文隔离、并发限流、单张失败重试、按张计费金额与 usage_breakdown、[图片N: ...] 插回、deferred 存量文章 p2 重建、压缩路径。

### WP12 [x] 入库内容质量优化：描述清洗 + 总结入库（2026-09-15 负责人 agent2 实测反馈，依赖 WP10）——2026-09-15 开发完成（vision.py PARSE_INSTRUCTION 强化（直接输出图片文字与信息本身，禁任何前缀/标签/标题行/说明性开场）+ clean_description 保守剥离产出开头常见标签（行首匹配、可多轮剥、多行仅剥首行，仅成功路径调用）；新增 summarize.py：SUMMARY_MAX_CHARS=500、单轮指令提炼 ≤500 字核心要点（图文去重/禁标签/不发挥），LLMGateway() 主 provider 默认文本模型，wait_for 60s 超时+重试 1 次，两次失败回退 merged 原文入库；service 文档组装改造：chunk 文本=总结+「原文链接：{original_url}」（去掉「文档标题：」前缀），raw_text=merged 全文，summary 列=总结（回退截断），metadata.content_mode='summary'|'raw_fallback'+summary_fallback；总结计费 record_background_llm_usage(source='wechat_mp_summary', model 显式) 独立落账 fail-open；PIPELINE_VERSION p2→p3 存量复核自动重建；不变量 content_hash 按原始节点（VL/总结不进指纹）二次处理仍 check 零成本。单测：新增 test_wp12_summary.py 32 用例（前缀剥离/指令构造/总结器重试超时截断/成功与回退组装/超长截断/VL 前缀端到端清洗/计费 source+model+独立落账/hash 不变量+二次 claim 零成本/p2 重建 p3），既有 test_service/test_wp10 断言按新语义改写零破坏；三智能体闭环：独立测试 395 passed+67 项真实探针（四点要求逐条落库取证、hash 不变量独立复算、计费三账闭合 2.24 对平、修 test_service 重复 _make_service 死代码 1 处）；CR 无 P0/P1，P2 登记：A1 总结计费在业务提交前→已修（2026-09-16 移至 persist 后与 embedding/图片同序，落库失败不再白扣总结费）、A2 清洗后复查空串/无法识别标记再计费、A3 clean_description「识别结果/这张图」类前缀建议强制冒号防误剥真实海报文字；终态 408 passed（含并行渠道配置改动自带 13 用例）+前端 build 通过+import 启动安全；真实 LLM 总结质量待 agent2 部署后实测。**补充定版（负责人 2026-09-15）**：原文链接不拼进正文 chunk（500 字总结+链接常超 chunk_size 512、链接独占低语义 chunk），改存 documents.file_path 文档位置字段（与手动上传文档的本地路径同字段，URL 即外部文档的位置）；下载票据/兑换两处边界改按 origin 拦截（file_path=URL 不再可能进 os.path.exists 泄漏进报错），物理删除既有 origin 守卫在前，迁移脚本复制循环跳过外部文档；聚焦 CR 全仓排查 file_path 消费方 11 处无残留风险，针对性套件 78 passed（含 2 个新增「file_path=URL 仍 400+原文链接」边界用例）。**总结计费落 0 修复（2026-09-15 负责人定版）**：agent2 实测总结记录落库但 credit_cost=0——主模型 deepseek-flash 不在 token_cost_prices 价目表；曾实现固定 GLM-5.3-Flash 方案后按负责人决策撤回，改为通用兜底规则：模型匹配不到价目行时按 deepseek-v4-flash 价格计费（billing.py BILLING_FALLBACK_MODEL，breakdown 记 price_model/price_fallback 供对账，模型与兜底均无价目行才落 0），对全部 text 算价路径生效；test_credit_billing 73 passed（3 新增）。待提交）

- 负责人四点要求：①AI 文字干净——文本即原文本、图片即图片内容转述，不得出现「标题：」「图片识别结果如下：」等标签；②一篇 URL 最终入库一篇 ≤500 字核心要点总结，向量打在总结上，一篇 URL 对应一个知识文档；③多图识别内容合并参与总结且不与文字内容重复；④保留原文链接。
- VL 描述清洗：指令强化（只输出图片文字与信息本身，禁止任何前缀/标签/标题行）+ 产出后保守前缀剥离（图片识别结果如下/标题/描述/这张图展示了等）。
- 总结生成：LLMGateway 主 provider 默认文本模型，timeout 60s 重试 1 次；指令=合并原文与图片转述提炼 ≤500 字核心要点、图文信息不重复、无标签不评价；失败回退 merged 原始内容入库（metadata.summary_fallback）；计费 record_background_llm_usage(source='wechat_mp_summary', model 显式)。
- 文档组装：chunk 正文=总结+「原文链接：{original_url}」，去掉「文档标题：」前缀；documents.raw_text 存 merged 全文（审计）；PIPELINE_VERSION p2→p3（存量复核自动重建为总结版）。
- 不变量：content_hash 仍按原始节点（VL/总结输出均不进指纹）→ 同文章二次处理仍 check 零计费；售前挂接/分类/计费链路不动。
- 单测：前缀剥离、总结指令与合并视图、失败回退、计费 source/model、链接保留、超长截断 500、p3 重建、快路径零成本（总结不进 hash）。

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

### WP13 [ ] 图文 Markdown 化入库(2026-09-16 负责人实测反馈与定稿,依赖 WP10/WP12)

- 背景实测:《皇家玉石 2.0》37 图/正文 1040 字,因「文字<20」门禁跳过 VL,图片信息全部丢失;负责人确认公众号文章基本是图文,图片内容与文本同等重要,必须进总结。
- **门禁放开**:所有含图文章都解析图片(维持单篇 30 张上限);无多模态模型不再 deferred——文本照常入库,图片行保留地址、描述留空,metadata 记缺失原因(纯图且无任何可总结内容仍 content_empty/deferred)。
- **content_md**:新增加按节点顺序忠实组装 Markdown——`# 标题` + 文本段落原样 + 图片行 `![VL描述](CDN地址)`(描述即注释;VL 失败/超限/无模型 → `![图片N](地址)` 无描述);存 documents.metadata.content_md;raw_text(纯文本)保留审计兼容。
- **总结**:输入改为 content_md 全文,指令补「图片转述内容与文本同等重要需纳入总结,图文去重」;输出仍 ≤500 字,向量打在总结上。
- **metadata 增补**:content_md、ingested_at(入库时间 ISO)、image_parsed_count/image_failed_count/image_skipped_count;其余既有字段保留;external_id 不进 metadata(列已有)。
- **计费**:图片解析从固定 price_per_call 改为**按实际 token 计费**(每成功一张按该张实际 usage × 所用模型单价走标准 text 算价路径,含价目兜底;usage_breakdown 记 billing_mode='token' 与实际模型);实测均值 ≈0.18 积分/张(输入 1155+输出 305,倍率 100 已含),重图最高 ~0.9。price_per_call 种子行保留不用。
- **不变量**:content_hash 仍按原始节点;VL/总结/md 均不进指纹;PIPELINE_VERSION p3→p4 存量复核自动重建(存量有图文章补付解析费,运营知会)。
- 单测:md 组装(顺序/注释/无描述行/纯图/无图)、门禁放开(文字充足有图触发 VL)、无模型入库不 deferred、总结含图片信息、metadata schema(content_md/ingested_at/计数)、按 token 计费(重轻图差异/价目兜底)、p4 重建、hash 不变量。——2026-09-16 完成(三智能体:开发 557 passed;独立测试 16/16 探针修 build_markdown 降级路径可抛缺陷 1 处、全量 573 passed、两批混和工作区互扰检查通过;CR 无 P0/P1,P2 登记:A1 总结计费时序遗留已于 2026-09-16 移序修复（随 p4 放量的暴露面消除）、空描述仍计 parsed、降级中前序目标 token 不落账(少收不漏收);p4 存量重建成本评估:典型 10 图 ≈2-2.5 积分/篇、无图 ≈0.4-0.5、峰值 2000+/天/租户直至消化,deleted 不重建、recheck 20/tick 限速——部署前运营知会。注:工作区另混有列表源接入批次(另一会话,CR 快查无阻塞,文件归属清单见 CR 报告),service.py 为混合文件,提交需协调。待提交)
- WP13-r2 迭代（2026-09-18，负责人定版）：①VL 指令放宽——文字优先不变，无文字图改一句话客观白描（成功路径统一 ≤100 字截断），真不可判读才输出「图片无法识别」（unrecognized 判定保留）；②移除单篇 30 张产品上限，改 200 张防失控硬护栏（非产品限制，skipped 语义不变）；③image_parsed_count 成功路径回写 articles 列（口径=metadata.image_parsed_count）。PIPELINE_VERSION p4→p5，存量复核自动重建（图片补解析，运营知会）。真机复验 53 图奢石文章：下载 53 成功 0 跳过（旧 23 张超限跳过）、VL 53 成功 0 失败（旧 30 下载中 20 张 unrecognized）。单测 wechat_mp 495 passed + channel_config 65 passed，未提交。

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
