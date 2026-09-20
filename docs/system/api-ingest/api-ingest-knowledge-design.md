# API 数据源入知识库（api_ingest）设计文档

> 状态：2026-09-20 v2.2 修订（设计完成，未开发）。
> v2 修订：客户 API 说明到位（宏陶商城 `hongtaoshop.gzfenxiao.com`，「论坛帖子」+「商城商品」两个只读列表接口，见 §12），接入路线定稿为**文档即配置、agent 驱动、不写每接口代码**；运行期执行模式定为契约直执（默认）+ agent 循环兜底。
> v2.2 修订（独立评审后，评审结论已逐项落文）：① §6.4 明确 `max_items_per_run` 截断与删除对账的门禁交互；② §9 SSRF 增私网禁令与 host 清单强制人审；③ knowledge 删除回写分支提前至 Phase 1；④ 校正"复用先例"表述（白名单子类与 agent_loop token 预算均为新代码，external_push 无先例）；⑤ 粒度与分块统一字符口径（平台护栏为字符制）；⑥ 新增 §4.1 契约生命周期与 §6.5 detail_fetch 执行语义。
> 关联：[公众号内容入知识库设计](../wechat-mp/wechat-mp-knowledge-ingestion-design.md)（范式来源，其 §10「外部内容源接入规约」为本设计遵循的架构规约）。
> 开发计划：[plan-api-ingest-knowledge.md](../../plans/plan-api-ingest-knowledge.md)（技术实现方案 + 分期任务拆解 + 验收标准）。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 核心管道：4 表 DDL + 文档上传 + agent 契约生成（读文档 + http_api 实探 + dry-run 样例）+ 契约直执引擎（list_payload）+ 匹配/整理 + 幂等入库 + knowledge 删除回写分支 + 手动入口（API + agent 工具） | 📋 待开发 | — |
| Phase 2 | 调度与治理：驱动协程 + 定时同步 + detail_fetch 模式 + agent_loop 兜底模式 + 失败退避 + stale 回收 + 余额预检/计费 + two_strike 删除 + portal 审计视图 | 📋 待开发 | — |
| Phase 3 | 前端接入向导 + 图片转存/VL（可选，按张计费）+ 客户存量接入迁移 | 📋 待开发 | — |

## 1. 背景与目标

某租户已有「公众号文章自动入知识库」体系；其另一部分业务数据（论坛帖子、商城商品）通过第三方 API 获取，此前靠人写脚本抓入知识库，散装且无治理。同类"API 数据定期入知识库"需求在多个租户存在。

**产品目标（2026-09-20 定稿）**：租户**只需要上传接口文档**（如本客户 `api.txt`），agent 自己理解文档、调用接口，平台**不为任何接口写集成代码**；抓到的数据按公众号同款治理范式体系化入知识库（新增/更新判定、定时调度、手动及时获取、异常治理）。

**核心需求**：

| # | 需求 | 结论 |
|---|------|------|
| R1 | 知识库新增/更新 | 按 `(tenant_id, origin='api_ingest', external_id)` 幂等 upsert，content_hash 判定新增/更新/跳过 |
| R2 | 定时调度 API 获取 | 每源可配 `sync_interval_hours`，驱动协程模式（不用每租户 APScheduler job） |
| R3 | 数据匹配与整理、判定新增/更新 | 契约声明 native_id/字段映射；双基准变更检测（源时间戳 / 内容指纹），无时间戳过滤参数时全量 hash 比对 |
| R4 | 转知识库 | 键值结构化数据（JSON/XML）转**语义明确**的 markdown 文本（字段中文 label 化 + 模板/通用结构渲染器）→ 复用 TextChunker/embedding/三表单事务；原始记录存 metadata；可选 LLM 摘要 |
| R5 | 手动及时获取 | 管理 API 立即同步 + agent 工具（对话里"拉一下最新数据"） |
| R6 | 知识治理 | 状态机、异常队列、退避重试、删除保守策略、计费、脱敏、审计视图 |
| R7 | 多租户复用 | 文档与契约租户隔离；模块不含任何租户/接口硬编码 |
| R8 | 接入零代码（v2） | 租户上传文档 → agent 生成源契约 → dry-run 人审 → 体系化运行；全程无每接口代码 |
| R9 | 知识粒度可控（v2.1） | **agent 决定数据如何进知识库**：产品/商品类一条记录 = 一条知识（整条入库不切块，检索单元即完整产品，不自我拆分、不与其他记录混切）；长文类按需切分。粒度由 agent 在契约生成期按样例数据形状决定，dry-run 展示依据，人工可改 |

**非目标**：不做实时 webhook 推送入库；不做非 REST 协议（数据库直连/FTP 等）；不做自动爬取无文档接口；v1 不做图片转存与 VL（URL 直引入，Phase 3 可选）。

## 2. 关键架构决策

| # | 决策点 | 结论 | 理由 |
|---|--------|------|------|
| D1 | agent 与 LLM 在链路中的位置 | **文档即配置，agent 驱动接入**：租户上传接口文档；agent 在**契约生成期**（一次性）读文档并用 http_api 实探接口，生成机器可执行的**源契约** JSON；dry-run 样例人审后生效。**运行期按契约执行**，两种模式：`contract_direct`（默认，代码按契约翻页调用受约束的 HttpApiTool 实例，零 token、确定性强）；`agent_loop`（兜底，recap external_push 同款受限 LLM 循环，契约生成失败/低置信度时用） | 两者都满足"不写每接口代码"：每接口的特异性全部落在 agent 生成的契约里。contract_direct 是 governance 级可靠性与运行成本的最优解——recap external_push 已证明 LLM 循环无人值守可行，但那是低频单条推送；多租户高频同步每 run 都过 LLM，成本与失败面不可接受，故只作兜底 |
| D2 | 源配置存哪 | 独立表 `bs_api_ingest_sources`（文档引用 + JSONB 契约 + 加密 secrets），不塞 `tenant_channel_configs` | 源数量多、字段关系复杂，管理端需要列表/过滤/统计；渠道配置表语义是"接入渠道"不是"数据源" |
| D3 | 凭证机制 | 契约 headers/query 中用 `${VAR}` 占位符；解析顺序：源配置 `secrets`（Fernet 加密落库）> 租户 `subagent_env_vars` > 进程环境变量；未解析占位符 fail-fast 拒发。契约生成期 LLM 只见文档与样例响应，**凭证占位符不渲染进 LLM 上下文** | 复用租户环境变量的管理界面与心智；本客户接口无鉴权，此机制对有鉴权接口生效；继承 http_api 2026-09-03 事故教训 |
| D4 | external_id 口径 | 文档侧 `origin='api_ingest'` 固定，`external_id = f"{source_code}:{native_id}"`（source_code 为源的用户定义稳定 slug） | documents 唯一索引不含 source 维度，必须源级命名空间防跨源碰撞；slug 比 serial id 跨环境稳定 |
| D5 | 删除检测 | 仅"完整可靠全量扫描"才触发：抓取完整（分页走完 + `total` 核对一致，接口无 total 时降级为分页耗尽）**且**本 run 条目无截断/中断（`max_items_per_run` 触顶即不可靠，见 §6.4）；默认 `two_strike`（连续两轮可靠全量缺失才软删），可配 `off`/`mark_only` | 对齐公众号"宁可不删不可误删"；公众号的完整性门禁（scan.reliable）包裹整个缺失迁移块，本模块门禁同样必须覆盖 miss_streak 推进与删除判定；本客户接口返回 total，完整性可硬校验 |
| D6 | 每源一个知识库分类 | 顶级分类 `source_type = f"k_api_ingest_{source_code}"`（展示名=源名称），惰性创建；源内可配 sub_category | knowledge_categories `UNIQUE(tenant_id, source_type)` 决定顶级分类以 source_type 为键；检索/知识共享可按源粒度开关 |
| D7 | 与 knowledge 服务的关系 | 与公众号一致：**直写 documents/chunks/chunks_vec 三表单事务**（照搬 `_persist_document_tx` 模式），不走 `upload_document`（它无 external_id 语义） | 遵循公众号 §10 规约第 2 条；`KnowledgeBaseService` 幂等 upsert 缺位是既有债务，不在本模块偿还 |
| D8 | 列表/详情两种形态 | 契约声明 `detail_endpoint` 有无：无则 `list_payload` 模式（列表响应即全量数据，本客户即此形态，Phase 1 实现）；有则 `detail_fetch` 模式（列表只产 external_id + 时间戳，按 §6.5 语义逐条拉详情，Phase 2 实现，Phase 1 契约校验直接拒绝该模式） | 覆盖常见 ERP/开放平台两种形态；本客户用不到 detail_fetch，避免 Phase 1 背负未验证的执行语义 |
| D9 | 与 http_api 工具的代码关系 | **运行期复用 HttpApiTool 请求层**：环境变量替换、占位符 fail-fast、大响应落盘、审计（单行 JSON、URL 剥 query、headers 不落盘）是现成能力；**白名单子类为新增代码**——HttpApiTool 现无任何 URL 策略钩子（follow_redirects 直接透传 httpx），子类需自建「解析后 IP 禁令 + 重定向每跳 host 校验 + 响应大小上限」切入层。能力边界：业务错误识别的判据是响应含 `Code` 字段，`status` 型接口（本客户）由契约 `success_check` 自行承担；不改共享工具路径 | 用户点名"用现成工具调用接口"；复用的是 external_push 的隔离 ToolRegistry 装配模式——其注册的实为基线 HttpApiTool，全仓库无子类先例，护栏靠循环内逐次纠偏；本设计把约束下沉进子类，比逐次纠偏更强。SSRF 债务不外溢，共享工具债务独立登记 |
| D10 | 字段范围与 hash 口径（v2 新增） | 契约把字段分两类：`in_doc`（渲染进文档正文，自动参与 content_hash——进正文的字段一变即判更新，语义正确）；`metadata_only`（易变指标如阅读数/点赞/库存/销量/评分，只进 documents.metadata，不触发重嵌入、不产生计费） | 防成本陷阱：本客户接口的 sales/stock/readcount 等每次同步都变，若进正文则每次全量重嵌入。价格/卖点是否进正文由租户在 dry-run 时确认（进正文=改价即更新） |
| D11 | 图片与视频（v2 新增） | v1：图片以原始 URL 进 markdown（`![](url)`），视频/头像等非正文资源只进 metadata。Phase 3 可选「转存 + VL 描述」：复用 wechat_mp image_downloader/vision 模式，按张计费 | 外链图片可用性依赖对方图床；VL 转述才有检索价值但成本高，按需后置 |
| D12 | 结构化数据→语义文本与原始留存（v2.1 新增） | 接口返回的 JSON/XML 键值数据**不裸进知识库**：契约字段带中文 label，模板渲染或通用结构渲染器生成语义化 markdown（扁平对象→「label：值」行、嵌套对象→小节、对象数组→表格）；doc_template 可省略（省略即用通用渲染器）。原始记录 JSON 存 `documents.metadata.raw_payload`（默认 ≤32KB，超出转存租户文件放指针），支撑溯源审计与**模板/管线升级时免重拉接口本地重渲**。契约支持 `response_format: json\|xml`，XML 先标准解析转 JSON 再走同一提取链 | RAG 检索的是语义文本，裸 key-value（英文键名、嵌套 JSON）直接分块入库检索质量差；label 化让「sellpoint: …」变成「商品卖点：…」；raw_payload 留存让 pipeline_version 升级不打对方接口 |
| D13 | 知识粒度（v2.1 新增） | 契约增加 `granularity`：`whole`（一条记录渲染后整条 1 个 chunk，一条记录=一条知识=一个检索单元，产品类默认）/ `chunked`（TextChunker 切分，长文类）/ `auto`（agent 在契约生成期按样例记录渲染后**字符长度分布**决定并写入契约，dry-run 展示依据：p95 字符数与切块数预估，人工可改）。whole 模式单条超 `whole_max_chars`（默认 4000，硬上限 ≤ 平台 6000 字符护栏）时**逐记录**回退 chunked 并记 `metadata.granularity_overflow`，不影响其他记录 | 用户要求智能体有能力决定整条进入还是切分进入。chunks 表本按文档组织，whole 模式天然实现「一产品一条知识」；逐记录回退应对长尾大详情商品；运行期确定性执行契约，agent 不必每 run 决策（与 D1 一致）。knowledge 侧已有 precomputed_chunks 旁路先例（excel_parser 行级分块已在用），非默认分块不破坏架构。口径统一为**字符**：TextChunker chunk_size 与 embedding 护栏（MAX_EMBEDDING_CHUNK_CHARS=6000，text-embedding-v3 标称 8192 token）均为字符制，平台无 tokenizer 基础设施，whole 上限硬约束 ≤6000 字符 |

## 3. 总体架构

```
【接入期 · 一次性，agent 驱动】
  租户上传接口文档（api.txt/markdown/OpenAPI，复用租户配置文件存储）
        ▼
  agent 契约生成：读文档 → http_api 实探（拉样例页）→ 生成源契约 JSON（含置信度）
        ▼
  dry-run 预览（拉 1 页、渲染 ≤3 条样例文档、external_id/hash 预览、字段 in_doc/metadata_only 分配）
        ▼
  人工确认（高置信度可自动通过其余字段，allowed_hosts 清单强制人审，见 §9）→ bs_api_ingest_sources（secrets 加密）→ verify 通过启用

【运行期 · 确定性治理，LLM 仅兜底】
  ┌ 手动：管理 API trigger / agent 工具 api_ingest_sync ┐
  │ 定时：ApiIngestScheduler tick（sync_interval_hours；       │
  │       全量对账；失败退避；stale 回收）                        │
  └──────────────┬──────────────────────────────────┘
                 ▼
  bs_api_ingest_runs(queued) ─▶ claim（租户×源级锁 + running 唯一索引）
                 ▼
  抓取执行器：contract_direct（默认，按契约翻页调白名单 HttpApiTool）
              / agent_loop（兜底，受限 LLM 循环：仅 http_api+提交工具+契约护栏）
                 ▼
  匹配：native_id → external_id；源时间戳（有则比）→ 建 items(new/update/check/delete)
                 ▼
  整理：in_doc 字段映射 → HTML 转 markdown → 模板渲染 → content_hash
       （hash 同 + pipeline 同 + doc active → 跳过零计费；doc 软删 → restore）
                 ▼
  入库：可选 LLM 摘要 → TextChunker → embedding → 单事务落
       documents(ON CONFLICT upsert) + chunks + chunks_vec + records + items
       → 惰性建分类 k_api_ingest_{source_code}
                 ▼
  收尾：run 计数终态 / 计费 fail-open / last_synced_at 与游标推进 / 对账门禁（§6.4：不完整 run 不做删除对账）
```

模块：新建 `src/api_ingest/`（对齐 `src/wechat_mp/` 独立模块模式）：

```
src/api_ingest/
├── db.py            # 4 表幂等 DDL（注册进 src/db/database.py）
├── config_codec.py  # SENSITIVE_KEYS Fernet 加密/脱敏（复用 src/core/secret_crypto，仿 wechat_mp/config_codec.py）
├── contract.py      # 源契约 schema 校验、字段映射、markdown 模板渲染、HTML→markdown、content_hash
├── executor.py      # 抓取执行器：contract_direct 翻页循环 + agent_loop 兜底（隔离 ToolRegistry + 白名单 HttpApiTool 子类）
├── assistant.py     # agent 契约生成：读文档 + http_api 实探 + 置信度评估 + dry-run
├── service.py       # ApiIngestSyncService：trigger/claim/_process_item/_persist_document_tx/_finalize_run/_reconcile_full
├── scheduler.py     # ApiIngestScheduler 驱动协程（仿 WeChatMPScheduler）
├── notify.py        # 队列唤醒信号（仿 wechat_mp/notify.py）
└── api.py           # /api/saas/api-ingest/*（租户端文档上传/源 CRUD/trigger/dry-run/runs/records/retry + portal 审计）
```

## 4. 源契约模型（agent 生成、人审生效；bs_api_ingest_sources.contract）

以本客户「商城商品」源为例（§12 有完整核对）：

```jsonc
{
  "contract_version": 1,                        // 契约 schema 版本，未来字段迁移用
  "content_rev": 1,                             // 内容修订号，每次契约修改 +1（生命周期见 §4.1）
  "mode": "list_payload",                       // list_payload（Phase 1）| detail_fetch（Phase 2，语义见 §6.5）
  "response_format": "json",                    // json | xml（xml 先标准解析转 json 再提取）
  "base_url": "https://hongtaoshop.gzfenxiao.com",
  "list_endpoint": { "method": "GET", "path": "/",
    "query": { "s": "/ApiExternal/getShopProduct", "aid": 1, "pagenum": "{page}", "pernum": 100 },
    "records_path": "$.datalist",
    "success_check": { "path": "$.status", "equals": 1 },
    "pagination": { "type": "page", "page_param": "pagenum",
                    "stop": "empty|short_page|total_reached", "total_path": "$.total",
                    "max_pages": 50 } },
  "detail_endpoint": null,
  "record": {
    "native_id_path": "$.id",
    "title_field": "name",                       // 无 doc_template 时作 # 标题
    "source_updated_at_path": "$.createtime",    // unix 秒；仅展示/排序用，本接口无增量过滤
    "content_format": "html",                    // html|text|md（detail 为富文本 HTML）
    "in_doc_fields": { "name": { "path": "$.name", "label": "商品名称" },
                       "sellpoint": { "path": "$.sellpoint", "label": "商品卖点" },
                       "detail": { "path": "$.detail", "label": "商品详情" },
                       "price": { "path": "$.sell_price", "label": "售价(元)" },
                       "pics": { "path": "$.pics", "label": "商品图" },
                       "procode": { "path": "$.procode", "label": "商品编码" } },
    "metadata_only_fields": { "sales": { "path": "$.sales", "label": "已售" },
                              "stock": { "path": "$.stock", "label": "库存" },
                              "comment_score": { "path": "$.comment_score", "label": "评分" },
                              "comment_num": { "path": "$.comment_num", "label": "评价数" },
                              "bid": "$.bid", "video": "$.video" }
  },
  "doc_template": "# {name}\n\n{sellpoint}\n\n{detail}\n\n![商品图]({pics[0]})\n\n> 编码 {procode}，售价 {price}",
                                               // 可省略：省略时用通用结构渲染器（title 字段 + 逐字段「label：值」行，嵌套→小节，数组→表格）
  "raw_payload": { "store": true, "max_kb": 32 },  // 原始记录 JSON → documents.metadata.raw_payload；超出 max_kb 转存租户文件放指针
  "granularity": { "mode": "whole",              // whole（一记录=一条知识，产品类默认）| chunked | auto（契约生成期 agent 按样例字符分布定）
                   "whole_max_chars": 4000,       // 整条上限（字符口径，硬上限 ≤ 平台 6000 字符护栏），超限逐记录回退 chunked 并记 metadata.granularity_overflow
                   "chunk_size": 512, "overlap": 64 },  // chunked 模式可按契约覆盖默认分块
  "content_mode": "template",                    // template | template+summary
  "deletion_policy": "two_strike",
  "sync_interval_hours": 6,
  "full_scan_interval_hours": 24,
  "rate_limit_rps": 2, "max_items_per_run": 200, "max_response_mb": 8,
  "allowed_hosts": ["hongtaoshop.gzfenxiao.com"],
  "execution_mode": "contract_direct",           // contract_direct | agent_loop
  "confidence": 0.95                             // 契约生成期 agent 自评；低于阈值强制人工确认；allowed_hosts 清单无论如何都要人审（§9）
}
```

`secrets`（同表独立列，Fernet 加密）：`${VAR}` 占位符取值，本客户接口无鉴权故为空。source_code：租户内唯一 slug（`^[a-z][a-z0-9_]{1,31}$`），建后不可改。另存 `doc_file`（上传文档引用）、`target_sub_category`、`sync_cursor`、verify 状态与时间戳。`in_doc_fields` 渲染结果整体参与 content_hash；模板字段缺失 → mapping_failed，不静默出空文档。

**契约 DSL 受限子集**（确定性执行要求，contract.py 强校验）：`records_path`/字段 `path` 用 JSONPath 受限子集——仅 `$` 起点的字段访问与数组索引（`$.datalist`、`$.pics[0]`），不支持过滤器/通配符/递归下降；XML 响应按 xmltodict 默认规则转 JSON（属性键 `@` 前缀、文本节点 `#text`）；`doc_template` 为 str.format 风格，字面大括号用 `{{`/`}}` 转义，字段引用支持 `{field}` 与 `{field[0]}` 数组索引。

### 4.1 契约生命周期与变更管理

- **修改流程**：生效契约的任何修改（agent 重生成或管理员手改）→ `content_rev` +1 → **强制重新 dry-run + verify** 才能回到 enabled，跳过则源置 disabled。
- **变更影响面**：改 `doc_template` 或 `in_doc_fields` 归类 → content_hash 口径变化 → 下轮全量 update（预期内的一次性重嵌入成本）；dry-run 确认页展示「预计 N 条记录将重嵌入」提示。
- **并发编辑**：源存在活跃 run（queued/running）时拒绝保存契约修改，提示稍后重试——避免 run 执行中途契约漂移，也免去 run 级契约快照成本。
- **schema 迁移**：`contract_version` 供未来字段演进（读取时按版本升级转换）；`pipeline_version` 独立演进（渲染/分块管线升级触发重建），与契约修订解耦。

## 5. 数据表设计（bs_ 前缀，DDL 三处同步：src/db/database.py + deploy/init-postgres.sql + deploy/db_update.yaml）

**bs_api_ingest_sources**：`source_id SERIAL PK`、`tenant_id`、`source_code`、`name`、`status(enabled/disabled)`、`doc_file TEXT`、`contract JSONB`、`secrets TEXT`（加密）、`sync_cursor TEXT`、`verify_status/confidence`、`last_sync_at/last_full_scan_at/last_error`、时间戳；`UNIQUE(tenant_id, source_code)`。

**bs_api_ingest_records**（记录当前态账本，对齐 bs_wechat_mp_articles）：`record_id PK`、`tenant_id`、`source_id`、`native_id TEXT`、`external_id TEXT`、`content_hash VARCHAR(64)`、`pipeline_version TEXT`、`source_updated_at TIMESTAMP`、`doc_id INTEGER`（→documents.id）、`status(active/missing/deleted)`、`processing_status(pending/success/sync_failed/deferred)`、`last_synced_at/last_checked_at`、`miss_streak INT`（two_strike 计数）、`next_retry_at`、`fail_count`、`error_code/error_message`；`UNIQUE(tenant_id, source_id, native_id)`。与 bs_wechat_mp_articles 的**有意偏离**：公众号 two_strike 是 active→missing→missing_recheck→deleted 状态机，第三步逐条调详情接口复核后才软删；本模块 list_payload 形态无单条接口可复核，改用 `miss_streak` 计数等价实现两击（miss_streak≥2 即软删），`fail_count` 亦为新增列；detail_fetch 模式（Phase 2）可将详情 404 作为逐条复核证据，对齐公众号第三步。

**bs_api_ingest_runs**：`run_id PK`、`tenant_id`、`source_id`、`trigger_type(manual/scheduled/full_scan/retry)`、`status(queued/running/success/partial_failed/failed/skipped_no_credit/interrupted)`、`owner_token + heartbeat_at`、六项计数（new/updated/deleted/skipped/failed/credits_charged）、`cursor_snapshot`；**部分唯一索引 `(tenant_id, source_id) WHERE status='running'`**（同源串行，异源可并行，受 worker Semaphore 约束）。

**bs_api_ingest_items**：`UNIQUE(run_id, record_id)`、`action(new/update/check/delete/restore)`、`status(pending/running/success/skipped/failed/interrupted)`、`error_code`、计费三字段。error_code 固定白名单（中文文案）：`no_credit / fetch_failed / auth_failed / contract_invalid / parse_failed / mapping_failed / record_missing / content_empty / internal_error`。

## 6. 契约生成与抓取执行

### 6.1 契约生成（assistant.py，接入期一次性）

输入：租户上传的接口文档全文。步骤：LLM 读文档产出候选契约（endpoint/分页/记录路径/字段清单/模板建议/置信度）→ 按契约用隔离 HttpApiTool **实探** 1 页验证（success_check、records_path、native_id 逐项校验）→ dry-run 渲染 ≤3 条样例文档 + hash 预览 + in_doc/metadata_only 分配表 → 低置信度强制人工确认。护栏：LLM 上下文只含文档与样例响应，凭证占位符不渲染；实探域名必须与文档一致且在 allowed_hosts 内，且**实探同样过 §9 私网禁令**——allowed_hosts 源自租户上传文档属不可信输入，内网/云元数据/保留段地址在 DNS 解析后一律拒绝（实探发生在人审之前，禁令不能等 dry-run）；实探只允许 GET/HEAD。契约生成失败 → 允许管理员手改契约 JSON 重跑 dry-run，或源置 `agent_loop` 模式先跑起来。

### 6.2 抓取执行器（executor.py，运行期）

**contract_direct（默认）**：代码按契约翻页循环调用 HttpApiTool 白名单子类（host 校验含重定向、响应大小上限、限速、审计），逐页过 success_check → 提取 records → 交给治理管道。停止条件按契约 `stop`：`empty`（空页）/`short_page`（不足 pernum）/`total_reached`（累计 ≥ total，本客户接口可硬校验）。**零 LLM、零 token**。

**agent_loop（兜底）**：参照 recap external_push 受限循环模式——lite 模型 + 仅两个工具（白名单 HttpApiTool + submit_records），系统提示嵌文档全文与任务规格（"分页遍历完整列表，逐页提交原始记录"），每轮对工具参数做确定性纠偏（method/固定 query 以契约为准、页码自增、status≠1 即停）；护栏：max_iterations、单 run token 预算（新增实现——external_push 仅有 max_tool_rounds=8 与连续失败 3 次熔断，无 token 预算先例）、total 核对，不达标 run 判 failed 不入库。适用于契约生成失败但文档清晰的场景。

### 6.3 处理管道（`_process_item`，两模式共用）

1. 取 payload（XML 响应先转 JSON）→ in_doc 字段映射 + HTML→markdown（通用转换：html.parser 去 script/style，保留标题/列表/表格文本语义，`<img>` 转 markdown 图）→ 语义化渲染：有 doc_template 按模板；无模板用通用结构渲染器（title 字段作 `#` 标题，逐字段「label：值」行，嵌套对象→小节，对象数组→markdown 表格）。字段缺失 → mapping_failed，不静默出空文档。
2. `content_hash = sha256(pipeline_version + doc_template 指纹 + in_doc 字段规整序列)`，模板指纹 = doc_template 全文 sha256（无模板记 `"generic"`）——模板改动必触发内容重判，不依赖 content_rev；metadata_only 字段只落 documents.metadata。
3. hash 相同 且 doc active 且 pipeline 相同 → `_handle_check_unchanged`（零计费跳过；metadata_only 值**先比对后写**——无变化完全不写 documents 行，避免每轮全表 UPDATE 与 updated_at 抖动，有变化才 UPDATE metadata）；doc 被软删 → `_handle_restore`；doc 行缺失 → 重建（用户在知识库侧删除已由回写分支同步置 record deleted，正常不会走到重建，见「删除联动」）。
4. 可选 `template+summary`：LLM 生成 ≤500 字摘要（超时重试 1 次，失败回退原文，对齐公众号 summarize）。
5. 按契约 granularity 落块：`whole` 模式整条 1 个 chunk（超 `whole_max_chars`，字符口径、硬上限 ≤6000 字符护栏，逐记录回退 TextChunker 切分并记 `metadata.granularity_overflow`）；`chunked` 模式 TextChunker（chunk_size/overlap 可按契约覆盖）。随后 `_persist_document_tx` 单事务：documents `INSERT ... ON CONFLICT (tenant_id, origin, external_id)` / UPDATE 覆盖 + chunks/chunks_vec 删旧重建 + record/item 终态 + 惰性建分类。metadata 记 `raw_payload`（原始记录 JSON，按契约 max_kb 截断、超出转存租户文件放指针）+ metadata_only 字段值（含 label）+ `granularity/overflow 标记` + 溯源（source_code/native_id/run_id/content_mode/pipeline_version/ingested_at）。whole 模式下一个产品 = 一条知识 = 一个检索单元，检索命中即完整产品，不自我拆分占用多个 top-k 名额。
6. 计费 fail-open：embedding 复用 `_record_knowledge_embedding_billing`（source_type=`api_ingest_embedding`），摘要 `api_ingest_summary`；no_credit 走退避且 run 终态 `skipped_no_credit`。

**删除联动（双向）**：全量对账按 D5 保守策略 `active→missing(miss_streak++)→连续两轮可靠全量→deleted`，同步软删 documents；知识库侧删除 `origin='api_ingest'` 文档时回写 record 软删（`knowledge/service.py delete_document` 增加 origin 分支，照搬 wechat_mp 分支的一条同事务 UPDATE，**Phase 1 随摄入一起落地**——否则用户删除会被下一轮 restore/重建静默撤销且重建路径重新计费，wechat_mp 加该分支的动因即此，见其 docstring「付费重拉重建」警示）。

**失败退避**：`300s × 2^fail_count` 上限 24h；`auth_failed`（401/403/占位符未解析）不重试，置源级 `last_error` 并停调度直到重新 verify，避免无效打爆对方接口。

### 6.4 截断、游标与对账门禁（max_items_per_run × 删除对账）

公众号 worker 层无条数截断（截断只发生在入队侧），本模块契约引入 `max_items_per_run` 是**新风险源**，规则如下：

- `max_items_per_run` 是保险丝不是常规路径：默认配置应覆盖源全量（本客户 200 > 记录总数），触顶即异常信号。
- **可靠性判定拆两半**：抓取完整（分页走完 + total 核对/分页耗尽）与条目处理完整（本 run items 全部终态）缺一不可；任一不满足 → run 终态 `partial_failed`，**不做删除对账、不推进任何 record 的 miss_streak**，只落已处理 items。
- 截断后续跑：无增量过滤参数的源**不依赖游标续跑**——下一轮 scheduled run 全量重来，靠 hash 跳过已处理记录（百级量级成本可忽略），规则简单优于游标精确；`sync_cursor` 仅用于有增量过滤参数的源记录增量水位。
- 连续 3 轮 `partial_failed` → 源级 `last_error` 告警日志，提示调大 max_items_per_run 或排查单条耗时。

### 6.5 detail_fetch 模式执行语义（Phase 2）

- 列表页仅提取 native_id + source_updated_at（+ 详情 URL 模板 `detail_endpoint`）；列表响应不参与 in_doc 渲染。
- **详情拉取范围**：列表提供 source_updated_at 且 record 已有该值 → 仅拉时间戳变化的记录（真增量）；无时间戳 → 每轮全量逐条拉（受 max_items_per_run 与限速约束）。
- 失败分类：详情返回明确的「记录不存在」（404 或业务码判定）→ `record_missing`，可作 two_strike 的逐条复核证据（对齐公众号第三步详情复核）；网络/5xx/解析失败 → `fetch_failed` 走退避。
- 限速与体积：rate_limit_rps 对列表与详情统一限速；单条详情响应计入 max_response_mb；详情富文本同样走 HTML→markdown 管道。
- Phase 1 契约校验直接拒绝 `mode=detail_fetch`（提示待 Phase 2），避免半成品语义上线。

## 7. 调度设计（ApiIngestScheduler）

完全对齐 `WeChatMPScheduler` 模式，注册进 `src/background_runner.py`（独立协程，启动失败不影响 runner）：

- 单副本驱动锁：Redis `api_ingest_scheduler_lock`（TTL 300s 续期）；Redis 不可用拒绝启动，不无锁降级。
- 60s 兜底扫描 + notify 唤醒信号领取 queued runs；`Semaphore(4)` 跨租户有限并发；租户×源级 Redis 锁 + running 唯一索引双闸。
- 30min tick 生成器：① 失败退避到期 retry（单 tick 上限 50）；② enabled+verified 源距最近 scheduled run 超过 `sync_interval_hours`（默认 6h）→ 同步 run；③ 距最近 full_scan run 超过 `full_scan_interval_hours` 且 deletion_policy≠off → 全量 run（上限 20）；④ 5min stale 回收（heartbeat 超时 + 锁校验 → interrupted）。
- 生成器只建 queued run，幂等（NOT EXISTS 活跃同类 run 门禁）。错峰上限为本模块自定义：retry ≤50（对齐公众号 RETRY_TICK_LIMIT）、full_scan ≤20、scheduled 每源每 tick 至多 1 个——公众号无 full_scan 对应物（其 20 是 recheck 上限、scheduled/list_sync 上限为 100），数值不可比。
- 无增量过滤参数的源（本客户即此）：scheduled run 即全量拉取，靠 hash 跳过未变记录，成本可控（百级记录单页拉完）。

## 8. 手动及时获取入口

| 入口 | 形态 | 说明 |
|------|------|------|
| 管理 API | `POST /api/saas/api-ingest/sources/{id}/trigger`（mode=sync/full） | 租户管理员立即同步；queued 排队不并发 |
| Agent 工具 | `api_ingest_sync`（参数：可选 source_code；**身份一律取 ToolExecutionContext，绝不由 LLM 传租户**） | 对话"把商城商品最新数据拉进知识库"；薄入口 `asyncio.to_thread` |
| Agent 工具 | `api_ingest_status`（可选 run_id，跨租户统一"未找到"防探测） | 查看最近 run/失败原因 |
| dry-run | `POST /sources/{id}/dry-run`（契约未保存也可预览） | 拉 1 页 → ≤3 条样例文档 + external_id/hash 预览，不写库 |

租户端另有 `GET /runs`、`GET /runs/{id}`、`GET /records`（status 过滤）、`POST /records/{id}/retry`、源 CRUD + `POST /sources/{id}/verify` + 文档上传（复用租户配置文件存储）；平台端 `GET /portal/runs|/portal/sources` 跨租户审计（platform_admin）。

## 9. 安全与治理

- **SSRF**：三层防线。① `allowed_hosts` 域名白名单（契约生成期实探、保存时、运行期逐请求含重定向后每跳 host 校验）；② **私网禁令（新增，白名单不可豁免）**——allowed_hosts 来自租户上传文档属不可信输入，白名单挡不住「租户指定内网地址」：host 为 IP 字面量或 DNS 解析后任一 A/AAAA 记录命中私网/保留/回环/链路本地段（含 169.254.169.254）即拒绝，校验解析结果而非域名以防 DNS rebinding；③ **host 清单强制人审**——高置信度自动通过不豁免 allowed_hosts 确认，首次启用前管理员必须核对目标域名清单。运行期校验由 HttpApiTool 白名单子类承载，共享工具的 SSRF 债务不外溢、独立登记。
- **凭证**：secrets 加密落库；日志/审计/错误信息一律脱敏（复用 `sanitize_error_info`，位于 knowledge/embedding/embedding_client.py，wechat_mp 同款用法）；占位符未解析 fail-fast 拒发（继承 http_api Code=-99 事故教训）；契约生成期凭证不进 LLM 上下文。
- **审计**：executor 独立审计日志（仿 http_api audit：单行 JSON、URL 剥 query、headers 不落盘）。
- **可观测**：run/item 两级账本 + 源级时间戳（last_sync_at/last_full_scan_at/last_error）+ portal 审计视图；无外部告警通道，与公众号现状一致（诚实声明）。
- **前端**：Phase 3 提供接入向导页（上传文档 → 契约确认 → dry-run 样例 → 启用），复用 Base* 组件与语义 token，不做视觉创新。

## 10. 分期开发计划

- **Phase 1（核心管道）**：4 表 DDL + config_codec + contract（schema/映射/HTML→markdown/通用结构渲染器/raw_payload 留存/granularity 落块/hash/契约 DSL 受限子集校验）+ executor（contract_direct + 白名单 HttpApiTool 子类，含私网禁令与重定向每跳校验）+ service（受理/处理/入库/收尾/截断对账门禁）+ assistant（契约生成 + 实探 + dry-run，含 granularity auto 决策）+ **knowledge delete_document 的 api_ingest 回写分支** + 租户端 API + agent 工具两个 + 契约生命周期（修改流程/content_rev）+ 单测（翻页停止条件/success_check/hash 幂等/HTML 转换/结构渲染器/whole 回退切分/mapping 边界/并发 upsert 冲突/**私网禁令与重定向校验/截断 run 不推进 miss_streak/JSONPath 受限子集/模板大括号转义**）。
- **Phase 2（调度与治理）**：scheduler 驱动协程 + notify + tick 生成器 + 退避/stale 回收 + **detail_fetch 模式（§6.5）** + agent_loop 兜底（含单 run token 预算）+ 计费 + two_strike 删除 + portal 审计视图 + 集成测试（fake API server，含本客户接口形态的仿真，加截断/不完整扫描不误删用例）。
- **Phase 3（收尾）**：前端接入向导 + 图片转存/VL（可选）+ 本客户两源（论坛帖子/商城商品）正式接入，替代存量脚本。
- 开发按 dev_workflow 三智能体流程执行；DDL 三处同步。

## 11. 风险与开放问题

| 风险/问题 | 应对 |
|------|------|
| agent 契约生成失败/置信度低（文档质量差） | 人工改契约 JSON 重跑 dry-run；源置 agent_loop 模式先跑；两者都不通才回退人工接入 |
| HTML→markdown 转换质量（detail 富文本、嵌套表格等） | dry-run 样例人审把关；转换器单测覆盖典型富文本；转换缺陷按 pipeline_version 升级触发全量重建 |
| 易变字段误判更新导致全量重嵌入（成本陷阱） | D10 in_doc/metadata_only 分家；dry-run 展示分配表由租户确认；run 计数监控 updated 占比异常 |
| 全量对账误删（对方接口故障返回空列表、max_items_per_run 截断） | D5 完整性门禁（total 核对 + 分页耗尽 + **条目处理完整**，§6.4：截断 run 不推进 miss_streak）+ two_strike + `off/mark_only` 逃生开关 |
| 租户文档指向内网/云元数据地址（SSRF） | §9 三层防线：私网禁令（校验 DNS 解析结果）+ host 清单强制人审 + 白名单每跳校验 |
| 契约修改触发全量重嵌入（成本尖峰） | §4.1 变更管理：强制 dry-run 展示影响面（预计重嵌入条数）+ content_rev 追溯 |
| LLM 摘要/agent_loop 成本 | 摘要默认关、独立计费、失败回退；agent_loop 单 run token 上限，超限判 failed |
| 第三方接口无 updated_at 过滤参数 | 全量拉取 + hash 跳过（本客户量级百级、单页 100 条，小时级同步无压力）；大数据量源需对方支持增量过滤，登记为接入评估项 |
| 多源同租户打爆对方接口 | 源级限速 + 同源串行（running 唯一索引）+ tick 错峰上限 |

## 12. 客户 API 适配核对（2026-09-20，文档：宏陶商城对外接口）

| 核对项 | 结论 |
|--------|------|
| 接口形态 | 2 个只读 GET 列表接口：`getLuntan`（论坛帖子）、`getShopProduct`（商城商品）→ 建 2 个源，共用 1 份上传文档，各得一个知识库顶级分类「论坛帖子」「商城商品」 |
| 鉴权 | 无需鉴权（`aid` 区分账号，默认 1）→ secrets 为空；allowed_hosts=`hongtaoshop.gzfenxiao.com` |
| 分页 | `pagenum`/`pernum`（默认 10、最大 100）→ 契约 pernum=100 单页拉完；stop=total_reached |
| 成功标记 | `status=1` 成功 / 0 参数错误 → 契约 success_check，status=0 归 contract_invalid |
| 增量能力 | 无 updated 过滤参数，`createtime` 为 unix 发布时间 → **全量 hash 模式**；百级记录单页拉完，小时级同步无压力 |
| 删除检测 | 无删除标记，但返回 `total` → set-diff + 完整性硬校验 + two_strike |
| 商品源字段分配 | in_doc：name/sellpoint/detail(富文本 HTML)/sell_price/market_price/pics/procode；metadata_only：sales/stock/comment_score/comment_num/bid/video。价格进正文 = 改价即更新（dry-run 时与租户确认） |
| 帖子源字段分配 | in_doc：content/pics/video/catename/nickname；metadata_only：readcount/zan/plcount/is_top/headimg；标题无原生字段 → 模板用 content 截断作 `#` 标题 |
| 结构化→语义文本 | 契约字段中文 label（商品名称/商品卖点/售价(元)/已售/评分…）+ 模板渲染语义化 markdown（无模板走通用结构渲染器）；原始记录 JSON 存 metadata.raw_payload（本接口单条含 detail HTML，体积远小于 32KB 上限），模板/管线升级可免重拉本地重渲 |
| 知识粒度 | 商品源 `granularity=whole`（一条商品=一条知识，检索命中即完整商品）；dry-run 按样例渲染后**字符长度分布**定，个别超大详情商品逐记录回退切分并标 overflow；帖子源 content 为长文 → agent 在契约生成期决定 chunked 或 auto（dry-run 展示 p95 字符数与切块数依据） |
| 待实现期确认 | detail 富文本实际标签复杂度（决定 HTML 转换器打磨点）；pics 外链图床可达性与防盗链（影响 Phase 3 转存决策）；商品 `status`（1 上架）是否同步下架语义（下架→删除 or metadata_only 标记，dry-run 定） |
