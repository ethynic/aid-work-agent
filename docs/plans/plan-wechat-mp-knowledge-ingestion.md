# 微信公众号内容入知识库 P1 开发计划

> 设计依据：[设计文档](../system/wechat-mp/wechat-mp-knowledge-ingestion-design.md)
> 状态：2026-09-14 D11 架构修订后**本计划范围已过期**（原 P1 以 freepublish 凭据路径为核心；现 URL 抓取升为主路径，见设计 §12 新分期）。计划文档待按新分期重写，WP0 实测证据保留在设计 §13.2。
> 风险：高（迁移、并发、租户隔离、计费、外部 API），代码开发走开发→独立测试→独立 CodeReview；本次文档审阅不触发三智能体开发流程。
> 目标：凭据验证→可靠同步→文字入库→检索过滤→逐次运行/文章审计可查。不得以 mock 通过替代真实接口可行性验证。

## 0. 范围与交付边界

| P1 做 | 后续做 |
|------|------|
| WP0 目标公众号权限、历史覆盖、多图文合并、删除语义验证 | URL 直采（P4，不用于本期历史补采） |
| documents 四列、检索/列表/计数与软删除访问控制 | LLM 时效抽取与人工修正（P3）；P1 expires_at 默认 NULL，过滤用测试数据验收 |
| wechat_mp 凭据加密、两步 verify、配置前端 | 图片下载转存、VL 与按张计费（P2） |
| 多配置身份隔离、分页 diff、文字入库、调度恢复 | LLM 分类/标签（P3） |
| run/item 历史、管理 API、portal 基础页、租户后台立即获取/状态入口 | 租户独立中心页（P3） |
| embedding 独立计费、确定性摘要、基础售前知识挂接、智能体获取/状态工具 | 更丰富的分类管理（P3） |

历史按接口实际返回范围全量分页获取，不设额外日期/总篇数截断，不补采接口之外的历史。三入口均属于 P1，详见设计 §14。首篇有效文章提交时幂等创建「公众号内容/未分类」并完成基础售前知识挂接；无售前实例时记录待挂接原因，不阻断入库。失败不得遗留空分类。P1 文字中以 `[图片N]` 保序占位；有图片且有效文字不足 20 字时 deferred，P2 升级后补处理，不记技术失败或每轮重试。有效纯短文本不因不足 20 字被误判为纯图；空正文跳过并可查。

## 1. 工作包与完成标准

所有工作包当前均为 `[ ] 未开始`，开发时逐项更新状态及验证证据。

### WP0 [🔧] 真实接口可行性（2026-09-14 核心实测通过，证据见设计 §13.2）

已验证（测试服务号）：stable_token 签发、batchget 权限、IP 白名单流程、发布 vs 群发边界（历史群发不可得，D9 成立）、getarticle 字段结构、正文 data-src 图片格式（mmecoa.qpic.cn）。

剩余待测（有多图文/删除样本后补，不阻塞 WP1 开工）：
- 多图文顺序与子篇 is_deleted=true 表现、整条删除后列表/详情行为与延迟、分页 >20 条、文章 url 长期可用性。
- 约束不变：只读核验既有样本；需要新建/删除真实公众号内容的写操作必须另获明确授权，不为测试擅自发布或删除；脱敏记录，不保存凭据、token 或完整文章正文。

### WP1 [ ] 数据库与启动注册

- documents 增加 origin/external_id/status/expires_at 及设计 §7 索引；external_id=`appid:article_id:combined`，验证 VARCHAR(128) 足够，否则用 TEXT。
- 按 §8 创建 articles 当前表、sync_runs 和 sync_items 历史表，补 user_id/created_at、配置身份、处理版本、独立源/处理状态、heartbeat、billing_status 等。
- 当前生效迁移为 `deploy/db_update.yaml`：新增唯一递增批次，同时同步 `deploy/init-postgres.sql` 与 `src/wechat_mp/db.py` 幂等初始化及实际启动注册点；不编辑旧 db_update.sql。同步 `docs/system/database_system_table.md`。
- 每配置 running 部分唯一索引；同租户同 appid 配置唯一约束；文章 `(tenant_id,appid,article_id,item_key)` 唯一；相关运行/处理查询索引。
- 验证空库初始化、存量升级、重复执行与旧数据 active/永不过期默认值。默认值不要求业务回填，但不等于迁移无需评估锁等待。先迁移后部署依赖新列的代码。

### WP2 [ ] 知识库过滤与外部文档边界（依赖 WP1）

- vector_db 向量与 hybrid_retriever 全文所有租户/共享/全局分支在 LIMIT 前过滤 active 且未过期；核查结果装配路径不绕过授权。
- service.list_documents/count_documents 同条件过滤，API 响应模型与前端类型带 origin/status/expires_at；include_deleted 仅 platform_admin 审计，不能用于普通知识检索。
- 详情/chunks/下载票据统一软删除可见性。外部源无文件时明确禁用下载、提供原文 URL；不虚构 file_path。
- P1 通用编辑、移动、物理删除（含批量）拒绝外部源文档，保证同步拥有写入权；手动上传保持原行为。
- 验证已有手动文档、共享授权、过期边界、列表计数一致、越权直访、外部源写操作拦截。

### WP3 [ ] 凭据管理与前端（依赖 WP0 接口契约）

- API 枚举/必填校验/前端类型/表单支持 wechat_mp；凭据 appid+secret，附 enabled 与正整数 sync_interval_hours（默认 6）。appid 不允许原地改绑。
- 当前公共 `src/core/secret_crypto.py` 不存在：最小抽取 RPA Fernet 原语并保持旧导入兼容，复用原密钥策略；独立 wechat_mp codec，不带 RPA listen_mode 副作用。
- 覆盖 create/update、单查/列表掩码、后台解密读取、update_config_field 保护；缺失/掩码 secret 保留原值，禁止绕过加密存储。
- verify 专用分流：token + batchget(count=1) 都成功才 verified，有消息时再验证一条 getarticle，空列表不等于详情结构/历史覆盖已验证；未知错误不伪装为账号未认证。
- 凭据变更撤销 verified、更新凭据版本并使旧 token 失效；并发 verify 不得把新凭据标记为已验证。停用/删除配置阻止后续提交，保留审计与文档；同 appid 重建接续幂等身份。
- 检查启动扫描、ChannelFactory 与更新路径，wechat_mp 不进入消息收发 adapter。前端使用既有 Base* 组件显示验证结果/IP 白名单指引。

### WP4 [ ] WeChatMPClient（依赖 WP0、WP3）

- stable_token、batchget(no_content=0/1,count≤20)、get_article；分页记录完整性、总数与已见 ID，检测重复页/无进展/总数漂移。
- token key 包含 tenant/config/凭据版本，TTL 用 expires_in 减安全余量；账号级刷新单飞，无效 token 强刷一次后至多重试一次。
- 请求间隔≥200ms、timeout 15s，繁忙/限流/暂态网络有限退避和抖动；权限错误不重试，运行总时长有上限。
- 日志只输出错误类别、脱敏摘要和运行 ID；httpx 含 access_token 的 URL/异常与响应正文不得直出。

### WP5 [ ] 同步、事务与计费（依赖 WP1~4）

- 大量历史按批次处理并保存可恢复进度，不因运行时长上限截断总历史；分页不稳定时重扫去重，未完成不得宣告全量成功或执行缺失删除。
- run 建账→完整列表→新增/变更/重现/缺失判定→逐项处理→终态收尾。源 update_time 对比保存的成功源版本，不与 last_synced_at 比较。
- 一条消息一篇文档，item_key 固定 combined；保序拼接有效子篇标题和正文，分块保留子篇边界与对应来源 metadata，混合消息纯图子篇 deferred 不阻断文字子篇。hash 包含保序子篇内容与删除标记，成功 hash 与 pipeline_version 同业务事务提交；失败/版本升级需处理，hash 不变且文档存在则不重处理。
- 有效详情内明确 is_deleted 的子篇排除后重建合并文档，全部明确删除才软删除；空数组/缺字段不视为删除。含已删内容的旧版在重建失败/无余额时先隐藏，记录待重建；普通更新失败仍保留旧版。
- 整条消息完整扫描缺失仅标 missing，连续两次可靠扫描缺失且详情不存在判据通过后才软删除；异常、重复/缺页、权限变化及不可靠空集合均不得执行缺失删除。
- 源状态和处理状态分离：更新失败保留旧可用文档；重现恢复 active；deferred 等 P2 版本升级，不每轮调用收费链路。
- 事务外提取/分块/embed，P1 摘要用确定性截断；单连接单事务处理分类、documents、先删向量再删旧 chunks、插入 chunks/向量、文章成功状态及 run-item 状态。禁用内部提前 commit 的组合路径。
- embedding 计费 helper 来源目前固定，新增兼容适配 `source_type=wechat_mp_embedding`，不改变手动上传默认计费。每付费单元前复查余额；余额不足跳过付费处理，但不阻断已可靠确认的删除。
- 业务先提交、计费后提交；chat_records+余额扣减同事务，item 保存 pending/charged/failed/unknown 和引用，run 汇总实际已扣。unknown 禁止自动重扣，计费失败不导致重嵌入；不宣称 fail-open 天然保证幂等。
- articles 保存最新状态，sync_items 保留每轮逐篇结果；run 使用 running/success/partial_failed/failed/interrupted/skipped_no_credit，异常必须有完成时间和原因。

### WP6 [ ] 调度及中断恢复（依赖 WP5）

- manager 注册每 10 分钟 tick；due 根据配置周期和最近尝试时间，失败/无余额有退避。
- 定时/智能体/后台手动/单篇重试共用租户+配置锁，随机 owner、30min 可续租、owner 校验释放。数据库每配置 running 唯一，提交时锁定 run 检查 owner/状态及配置版本。
- 失锁停止调用/提交；Redis 故障拒绝新任务；确认旧租约失效后回收 stale running 为 interrupted。worker 恢复后不能再写旧 run。
- 使用有限并发后台执行器，停机停止接单并有终止/恢复策略；scheduler 主锁不能替代每配置任务锁。

### WP7 [ ] API、portal 与日志（依赖 WP5/6）

- `/api/saas/wechat-mp/`：GET /runs、GET /runs/{id}（含 items 历史）、GET /articles、POST /sync-now、POST /articles/{id}/retry。
- tenant_admin 仅本租户；platform_admin 可跨租户审计，但手动触发必须明确目标 tenant/config。所有对象查询和关联写入校验 tenant，不仅检查 require_admin。
- 手动触发返回 202+run_id；同配置已运行返回既有 run_id 和 running 状态，不能启动第二份；单篇 retry 复用账号锁且确认源仍存在。
- portal `/portal/wechat-mp` 基础页属于 P1（runs、历史明细、当前文章、触发/重试、错误与费用），P1 同时在租户现有配置/管理页提供立即获取、运行状态及失败原因，独立中心页留 P3；前端不将缺页/失败显示为空的成功列表。
- INFO 计数、WARNING 单项失败、ERROR 运行/计费异常，关联 tenant/config/run/item；敏感内容在写库/日志/响应前消除，遵循现有 log_error 链，不直接记录文章全文。
- 按当前应用实际注册方式补 API router、scheduler、前端路由/导航与权限，完成关键 import/启动检查。

### WP8 [ ] 智能体主动获取与检索闭环（依赖 WP5/6/7）

- 按现有工具注册/授权机制提供获取与查询状态能力，共用同步服务，trigger_type=agent；**工具是薄壳**：仅做授权校验、参数解析、状态回传，全部抓取/入库逻辑调 `WeChatMPSyncService`，不在工具侧复制（分层规则见设计 §3）。run 补 agent_id/session_id 关联字段，身份从可信执行上下文取得，不由模型指定 tenant_id 或凭据。
- 智能体可在需最新内容时触发，绕过周期 due 但遵循限流/锁/余额；已有任务复用 run_id。无配置提示配置，多配置目标不明时明确选择，不默认刷新所有账号。
- 工具返回 run_id/status/started_at 与完成后的计数、时间、脱敏原因；按会话预算有限查询，超时说明仍在同步，不无限轮询或声称已经拿到最新内容。
- 成功后复用 knowledge_base_search 检索新增内容并引用；失败或部分失败使用旧知识时说明同步状态。共享知识只读权限不授予刷新源租户权限。
- P1 实现首次有效入库的幂等售前来源挂接，保留原授权，只增不删；无实例时返回待挂接原因并由后续实例创建/配置流程补齐。其他智能体按已有来源授权配置，获取工具不扩大知识权限。
- 验收新增文章从 agent 触发→后台同步→入库→授权检索完整闭环，并验证工具权限、跨租户拒绝、长任务/失败回答及挂接幂等。

## 2. 测试与验收矩阵

| 风险/意图 | 必测场景 |
|----------|----------|
| 三入口与历史范围 | 定时、租户手动、agent 分别获取成功；完整遍历 API 历史分页，中断后恢复；完成后可检索新增文章，运行中/失败不声称最新 |
| 防串租户/账号/文章 | 同租户两个账号、同消息多篇图文合并后仍为一个 doc_id、重复配置竞争、跨租户 run/item 直访、共享检索授权 |
| 防误删 | 中途超时、缺页/重复页、总数漂移、权限失效、突然空集；两次可靠缺失+详情确认；子篇删除/重排触发同一文档更新，明确删除后重建失败隐藏旧版 |
| 防漏更新 | 源时间早于本机同步时间仍能检测变化、失败重试、deleted 重现、P1→P2 版本提升 |
| 防破坏旧版本 | embedding 失败/事务中途失败后旧内容可检索，成功 hash 不前移；新版本 chunks 与向量匹配 |
| 防重计费 | hash 不变零调用零扣费、业务成功计费失败、扣款结果未知不重试、run 费用与已扣记录一致 |
| 防永久卡住或双跑 | 并发手动+定时+agent+retry、任务超过锁 TTL、失锁、Redis 故障、worker 崩溃与 stale 回收、旧 worker 续写拒绝 |
| 内容与访问 | 标题/正文/引用按子篇对应、混合消息文字可用、有效短文本、纯图 deferred、空正文、图片保序、列表/计数相同过滤、软删直访、过期时区边界 |
| 凭据与日志 | 掩码回传、修改后撤销验证、并发旧 verify、两步权限验证、日志 URL/token/正文脱敏 |
| 迁移与兼容 | 空库/存量/重复迁移、旧上传/检索/共享/渠道回归、模块及路由启动、前端 build |

单测 `tests/unit/wechat_mp/`；集成 `tests/integration/` 使用真实 PostgreSQL 验证事务/约束，mock 微信响应覆盖新增→更新→删除→恢复全链路，不能用 SQL 字符串断言替代事务验证。测试账号实测单独记证据。

按 `.claude/rules/testing.md` 选当前环境可用测试入口；原 `./scripts/dev_test.sh <范围> -p no:cacheprovider -q` 是候选，不假定 Windows 可直接执行。前端运行 npm run build。只运行受影响回归，已验证代码状态不机械重跑；缺少数据库/账号时明确标为未验证，不写“全部通过”。

## 3. 流程、依赖与状态同步

1. WP0 先确认可行性；WP1→WP2，WP3/4 按接口契约推进，再 WP5→WP6/7→WP8；P1 包含基础挂接、三入口及幂等/授权验收。
2. 开发者定向自测→独立测试智能体选择回归→独立 CR 审租户、事务、并发、计费、日志与启动链路→主控最终验证。修复后重跑受影响检查。
3. 开始开发将 ideas.md #78 标为部分完成；本计划逐项更新验证与完成状态。P1 完成而 P2~P4 未完成时 #78 保持部分完成，不提前将整个功能移入 ideas_finished.md。
4. 全功能范围完成后再移入 ideas_finished.md，标记已完成开发；只有用户明确说“提交代码”才提交，本任务未获提交授权。
5. 当前 crawler 分支的 append_own_source 与 tick 不在工作区，可参考但不可当作已具备依赖。公共加密抽取、能力验证、恢复与管理页都应计入工作量。
6. 原 P1 3-4 人天仅作早期估计；WP0 后按上述工作包重新估算，不作为交付承诺。P2/P3 原估计也需包含能力路由、图片复用/计费、时效人工修正验收。
