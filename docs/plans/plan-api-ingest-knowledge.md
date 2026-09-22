# api_ingest 开发计划（技术实现方案 + 分期开发计划）

> **状态：⛔ 暂不开发（2026-09-22 归档）。** 宏陶需求已由专用模块 `src/tenant_custom/hongtao_shop/` 承接（[plan-hongtao-shop-kb.md](plan-hongtao-shop-kb.md)）；本方案作为多租户通用 API 入库设计归档保留，恢复开发前需重新评审与现有实现的差异。
> 设计依据：[api-ingest-knowledge-design.md](../system/api-ingest/api-ingest-knowledge-design.md)（v2.2，2026-09-20 独立评审修订版）。本文不重复设计论证，只落实"怎么实现、按什么顺序、怎么验收"；契约语义、门禁规则、SSRF 三层防线等以设计文档为准，冲突时以设计文档为准并回改本文。
> 模块：新建 `src/api_ingest/`（对齐 `src/wechat_mp/` 独立模块模式），另有一处对 `src/knowledge/service.py` 的删除回写分支改动。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| P1.1 | 数据层：4 表 DDL 三处同步 + config_codec | 📋 待开发 | — |
| P1.2 | contract.py：契约 schema/DSL 校验 + 提取渲染管线 + hash + granularity 落块 | 📋 待开发 | — |
| P1.3 | executor.py：白名单 HttpApiTool 子类（SSRF 三层防线）+ contract_direct 翻页引擎 | 📋 待开发 | — |
| P1.4 | service.py：run 生命周期 + _process_item + _persist_document_tx + 对账门禁 + embedding 计费 + knowledge 删除回写 | 📋 待开发 | — |
| P1.5 | assistant.py 契约生成（LLM + 实探 + dry-run）+ 租户端 API + agent 工具两个 | 📋 待开发 | — |
| P2.1 | scheduler.py + notify.py：驱动协程 + tick 生成器 + 退避/stale 回收 | 📋 待开发 | — |
| P2.2 | two_strike 删除 + 余额预检/no_credit 治理 + LLM 摘要（可选 content_mode）+ detail_fetch 模式（§6.5） | 📋 待开发 | — |
| P2.3 | agent_loop 兜底（含单 run token 预算）+ portal 审计视图 + fake API server 集成测试 | 📋 待开发 | — |
| P3 | 前端接入向导 + 图片转存/VL（可选）+ 宏陶两源正式接入替换存量脚本 | 📋 待开发 | — |

## 1. 集成点清单（新代码挂到哪，全部已核对现有模式）

| # | 改动点 | 位置 | 照搬的模式（已验证存在） |
|---|--------|------|--------------------------|
| I1 | 4 表 DDL 注册 | `src/db/database.py` `_init_postgresql()`（wechat_mp 块在 L1123-1132） | 每模块一个 try/except 块调 `init_api_ingest_tables(conn)`，失败仅 logger.error 不阻断启动 |
| I2 | 增量迁移 | `deploy/db_update.yaml` | 条目 `datetime/remark/statements`，SQL 必须 IF NOT EXISTS 幂等；datetime 须大于现有最大值（合并时取值），校验逻辑 `_load_db_update_blocks`（database.py L663）非法即拒启动 |
| I3 | 全量建表 | `deploy/init-postgres.sql`（wechat_mp 表在 L2557-2608 段后追加） | 与 I1/I2 三处同步（设计 §5） |
| I4 | 调度协程注册 | `src/background_runner.py` `_run()`（WeChatMPScheduler 在 L190-201） | `try: ApiIngestScheduler(); await start() except: logger.error("...启动失败（不影响 runner）")`；优雅停机处补 `await stop()`（L214-218 段） |
| I5 | agent 工具注册 | `src/tools/api_ingest_tools.py` | 继承 `BaseTool`（src/tools/base.py L35）+ `catalog=True` 自动进目录，无需手动登记；先例 `WechatMPSyncTool`（src/tools/wechat_mp_sync_tool.py L103）：`_require_tenant()` 取 `current_tool_execution_context().tenant_id`，service 调用一律 `asyncio.to_thread(...)` |
| I6 | 租户端 API | `src/api_ingest/api.py`，`APIRouter(prefix="/api/saas/api-ingest", ...)`，main.py include_router 块（L1789-1816 段）追加 | 鉴权 `require_admin(request)`（src/saas/api/tenant_auth.py L319，platform_admin 可 X-Tenant-Id 代管）；中间件按 `/api/saas/` 前缀解析租户（src/saas/middleware.py L107） |
| I7 | 平台端审计 | 同文件 portal 路由 | 本地 helper `_require_platform_admin(admin)` 抛 403（先例 client_usage_mgmt.py L34） |
| I8 | knowledge 删除回写 | `src/knowledge/service.py` `delete_document`（wechat_mp 分支 L699-710） | 同款一条同事务 UPDATE，origin='api_ingest' 回写 `bs_api_ingest_records` 置 deleted |
| I9 | LLM 直调（契约生成/摘要） | `LLMGateway` | 契约生成用 `gateway.chat`（temperature=0，"只输出严格 JSON"）+ 宽容解析 + pydantic 校验，先例 `extract_association_profile`（src/services/association_profile_extractor.py L274/L123）；摘要用 `chat_no_thinking` + 60s 超时 + 重试 1 次，先例 `ArticleSummarizer`（src/wechat_mp/summarize.py L83-165） |

## 2. 技术实现方案

### 2.1 数据层（db.py / config_codec.py）

**db.py**：`init_api_ingest_tables(conn)` 按设计 §5 建 4 表，全部 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`：

- `bs_api_ingest_sources`：`UNIQUE(tenant_id, source_code)`；`contract JSONB`、`secrets TEXT`（密文）、`sync_cursor TEXT`、`verify_status/confidence`、`last_sync_at/last_full_scan_at/last_error`。
- `bs_api_ingest_records`：`UNIQUE(tenant_id, source_id, native_id)`；`content_hash VARCHAR(64)`、`miss_streak INT DEFAULT 0`、`fail_count INT DEFAULT 0`、`next_retry_at`、`doc_id INTEGER`。
- `bs_api_ingest_runs`：**部分唯一索引** `ON (tenant_id, source_id) WHERE status='running'`（同源串行闸门）；`owner_token TEXT`、`heartbeat_at`、六项计数、`cursor_snapshot`、`scope VARCHAR(16) DEFAULT 'sync'`（sync/full，设计 §13-2）。
- `bs_api_ingest_items`：`UNIQUE(run_id, record_id)`；`action(new/update/check/delete/restore)`、计费三字段、`error_code`（固定白名单，见设计 §5）。

**config_codec.py**：仿 wechat_mp/config_codec.py，但更简单——`secrets` 列整体是 `{"VAR": "值"}` JSON 的 Fernet 密文：`encrypt_secrets(dict) -> str`、`decrypt_secrets(str) -> dict`（失败返回空 dict + warning）、`mask_secrets(str) -> str`。复用 `src/core/secret_crypto` 的 `encrypt_secret/decrypt_secret/looks_like_ciphertext`（`gAAAAA` 前缀防二次加密）。

### 2.2 契约层（contract.py）

纯函数模块，无 IO，全部可单测：

- **契约模型**：pydantic `ContractConfig`（顶层 `contract_version/content_rev/mode/response_format/base_url/list_endpoint/record/doc_template/raw_payload/granularity/content_mode/deletion_policy/sync_interval_hours/full_scan_interval_hours/rate_limit_rps/max_items_per_run/max_response_mb/allowed_hosts/execution_mode/confidence` 及子结构）。`_validate_contract()` 额外做 DSL 校验（设计 §4 受限子集）：
  - JSONPath 正则：`^\$(\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])+$`（自实现取值函数 `get_by_path(obj, path)`，不引第三方 jsonpath 库；**类型严格**：索引仅对 list、键仅对 dict，不匹配抛 MappingError，设计 §13-12）；
  - `doc_template` 经 `str.format` 校验（`Formatter().parse()` 收集字段名，字段必须 ∈ in_doc_fields，支持 `field[0]` 索引）；`title_field` 支持对象形式 `{field, max_chars}` 截断派生（设计 §13-5）；query 模板含 `{page}` 占位符直接 ValidationError（页码单一来源 = page_param，设计 §13-11）；
  - Phase 1 `mode=detail_fetch` 直接 ValidationError（提示待 Phase 2）；
  - `whole_max_chars` 钳制 ≤6000（平台护栏 MAX_EMBEDDING_CHUNK_CHARS）。
- **提取**：`response_format=xml` 先 `xmltodict.parse(text, disable_entities=True)`（属性 `@` 前缀、文本 `#text`；**必须 disable_entities=True 拒 XXE/实体膨胀**，新增依赖 `xmltodict>=0.13`，设计 §13-7）再走 JSON 提取。
- **渲染管线** `render_document(record_dict, contract) -> RenderedDoc`：
  1. in_doc 字段逐个 `get_by_path`，任一缺失或类型不匹配 → `MappingError`（error_code=mapping_failed，不静默出空文档）；
  2. `content_format=html` 字段过 `html_to_markdown()`（**markdownify + bs4 包装，不自写 html.parser 转换器**——两者均为既有依赖（wechat_mp 在用）；bs4 预删 script/style 节点，markdownify ATX headings，`<img>`→`![]()`，设计 §13-7）；
  3. 有 `doc_template` 按模板 format（字面大括号 `{{`/`}}` 转义）；无模板走通用结构渲染器（title 字段作 `#`，逐字段「label：值」，嵌套→小节，对象数组→表格；title 为对象形式时按 max_chars 截断派生）；
  4. `content_hash = sha256(pipeline_version + doc_template 指纹 + in_doc 规整序列 + granularity 规整串)`（模板指纹 = 模板全文 sha256 或 `"generic"`；granularity 规整串 = mode/whole_max_chars/chunk_size/overlap，设计 §13-10）；
  5. metadata_only 字段收集为 `meta_values`（含 label）。
- **granularity 落块** `build_chunks(rendered, contract) -> list[ParsedChunk]`：`whole` 且渲染文本 ≤`whole_max_chars` → 单 chunk 走 `precomputed_chunks` 旁路（先例：knowledge/parsers/\_\_init\_\_.py L26、knowledge/service.py L494 消费）；超限逐记录回退 `TextChunker(chunk_size, overlap)` 并打 `granularity_overflow` 标记。字符口径，不用 token（设计 D13）。

### 2.3 执行器（executor.py）

**`WhitelistHttpApiTool(HttpApiTool)`**（新增子类，设计 D9 已声明无先例、为新代码）：

- 构造参数：`allowed_hosts: list[str]`、`max_response_mb: int`、`secrets_env: dict`（解密后的源 secrets）。
- 重写 `execute()` 前置与请求层：
  1. `_check_host(url)`：host ∈ allowed_hosts；
  2. **DNS pin**：`PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport)`——`handle_async_request` 内 DNS 解析全部 A/AAAA，任一命中 `ipaddress` 的 `is_private/is_reserved/is_loopback/is_link_local`（含 169.254.169.254）即拒绝；通过后 URL host 改写为该校验 IP、Host 头保留原域名、`extensions["sni_hostname"]=原域名`（SNI 与证书校验均对原域名）——**校验与连接共享同一次解析，消除 rebinding TOCTOU**（设计 §13-8；P1.3 首日 spike 验证 sni_hostname 扩展，不支持则退化 60s DNS 缓存+登记残余窗口）；IP 字面量 host 同规则直判；
  3. 重定向手动逐跳：`follow_redirects=False`，遇 3xx 取 Location 后**回到第 1 步重过全部校验**，每跳计入上限（≤5）；
  4. 流式读响应体（`client.stream` 逐块累计），超过 `max_response_mb` 即停止读取，已读部分标记 `response_truncated`，**剩余不落盘**（不沿用 spill——护栏语义是拒收超限而非完整保留，截断体随后解析失败 → parse_failed；设计 §13-9）；
  5. 限速：源级最小间隔 `1/rate_limit_rps`（asyncio 锁内 sleep）。
- **override `_substitute_env_vars`**：先查解密 secrets，未命中调 super()——优先级 secrets > subagent_env_vars > 进程环境（基类只有后两级，设计 §13-6）；fail-fast 沿用 `_find_unresolved_vars`，错误文案仅占位符名（加单测断言不含值）。
- 继承不动：占位符 fail-fast（L135）、审计（L552，单行 JSON、URL 剥 query、headers 不落盘）。
- 不改共享 HttpApiTool 任何代码。

**contract_direct 翻页引擎** `fetch_all(contract, secrets_env) -> FetchResult`：

- 按 `pagination` 循环：`page_param` 注入页码，逐页过 `success_check`（`get_by_path(resp, path) == equals`，不等 → `contract_invalid`）；
- 停止条件 `stop`：`empty`（records 空）/ `short_page`（本页 < pernum）/ `total_reached`（累计 ≥ total_path 取值）；
- `max_pages` 兜底；返回 `FetchResult(records, fetch_complete, total_reported, total_matched)`——`fetch_complete` 与 `total` 核对结果供对账门禁（设计 §6.4）。
- 零 LLM。

**agent_loop（P2.3）**：`_create_tool_runtime()` 隔离 ToolRegistry 注册 WhitelistHttpApiTool + SubmitRecordsTool（catalog=False，先例 PushReportTool external_push.py L683）；`chat_lite` 循环；每轮工具参数确定性纠偏（method/固定 query 以契约为准、页码自增、success_check 不过即停，模式取 external_push `_normalize_http_method` L747）；护栏 max_iterations、**单 run token 预算（新实现，无先例）**、total 核对，不达标 run 判 failed。

### 2.4 服务层（service.py，`ApiIngestSyncService`）

run 生命周期照搬 wechat_mp/service.py 的已验证函数模式：

| 环节 | 本模块函数 | 照搬来源（wechat_mp/service.py） |
|------|-----------|--------------------------------|
| 受理 | `trigger_sync(tenant, source, mode)`：同事务建 queued run + pending items + `notify_queued_work()` | `import_urls` L332（advisory lock → 建 run/items） |
| 领取 | `claim_and_run(tenant)`：Redis 租户×源锁 + `UPDATE ... FOR UPDATE SKIP LOCKED` queued→running，撞 `uq_..._runs_active` 返回 conflict | `_claim_next_run` L1038（唯一约束冲突处理 L1062） |
| 心跳 | `_heartbeat(run_id, owner)` owner 守卫推进 heartbeat_at | L1067 |
| 执行 | `_execute_run`：executor 抓取 → 匹配建 items → 逐条 `_process_item` → `_reconcile` → `_finalize_run` | L1083 |
| 回收 | `recover_stale_runs()`：heartbeat 超 1800s + 锁校验 → interrupted | L974 / service.py L146 |
| 终态 | `_finalize_run`：按 item 终态聚合六计数/credits/run 状态 | L3722 |

**`_process_item`**（设计 §6.3 六步）：payload → contract 渲染 → hash 三态判定（相同且 active 且 pipeline 同 → `_handle_check_unchanged`，metadata_only **先比对后写**；doc 软删 → `_handle_restore` 复用旧 chunks 零计费；否则 new/update）→ 可选摘要（P2.2）→ granularity 落块 → `_persist_document_tx`。

**`_persist_document_tx`** 照搬 wechat_mp/service.py L2893 模式（已核对）：

- documents `INSERT ... ON CONFLICT (tenant_id, origin, external_id) WHERE external_id IS NOT NULL DO NOTHING`（部分唯一索引 src/wechat_mp/db.py L29），冲突回查转 UPDATE；
- chunks/chunks_vec 删旧重建（`vector_db.delete_by_doc` + `DELETE FROM chunks`）；
- 单事务：embedding 在**事务外**先算（wechat_mp 同款，L2085），事务内只落三表 + records + items + 惰性建分类（`ON CONFLICT (tenant_id, source_type)`，顶级 `k_api_ingest_{source_code}`）；
- metadata 落 `raw_payload`（按 max_kb 截断/转存）+ metadata_only 值 + granularity 标记 + 溯源（source_code/native_id/run_id/pipeline_version/ingested_at）。

**对账门禁（设计 §6.4/§13-2）**：`reconcile_eligible = fetch_complete AND total_matched AND 本 run items 全部终态`，**与 trigger_type 无关**（任何 run 满足门禁即对账并推进 last_full_scan_at）；不满足 → run `partial_failed`，**不推进任何 miss_streak、不产生 delete**。满足时 set-diff：active 记录不在本轮 fetched 集 → `miss_streak+1`；`miss_streak≥2` → 软删 records + documents（two_strike，P1.4 落 mark_only 行为 = **推进 miss_streak、置 missing、不动 documents**，软删开关 P2.2 完整）。deleted 记录重现规则与 missing 恢复见设计 §6.3 第 3 步（§13-1）。「连续 3 轮 partial_failed 置源级 last_error」归 P2.1。

**失败退避**：`next_retry_at = now + 300s × 2^fail_count`（上限 24h）；`auth_failed`（401/403/占位符未解析）不重试，置源级 `last_error` 并停调度直到重新 verify。

**计费**：embedding 复用 `_record_knowledge_embedding_billing`（knowledge/service.py L108），`source_type="api_ingest_embedding"`，fail-open——**P1.4 就落地**（避免 Phase 1 手动触发形成免费窗口）；**P1 带简单余额预检**（设计 §13-15）：trigger 时读租户 embedding 余额，≤0 → run 直接终态 `skipped_no_credit` 不拉取；运行中计费余额不足按 fail-open 照记账目（可能负余额），已入库数据不回滚。完整 no_credit→退避治理在 P2.2（对齐 wechat_mp `_mark_item_no_credit` L3603 模式）。

### 2.5 契约生成（assistant.py，接入期一次性）

`generate_contract(tenant, doc_text) -> ContractResult`：

1. **LLM 产候选**：`gateway.chat`，system「只输出严格 JSON（契约 schema 说明）」，user 带文档全文（凭证占位符若出现在文档中，按 `${VAR}` 原样保留、不渲染值）；宽容解析（剥 ```json 围栏/截取 {}，先例 association_profile_extractor `_parse_json_object` L123）→ pydantic 校验 → DSL 校验。
2. **实探**：用 WhitelistHttpApiTool 按候选契约拉 1 页（GET only）；域名与文档一致 + 私网禁令 + allowed_hosts；逐项校验 success_check/records_path/native_id_path/pagination.total_path；失败把样例响应喂回 LLM 修正，≤2 轮。
3. **置信度**：实探全过 + 字段覆盖率 → confidence；<0.8 强制人工确认（allowed_hosts 清单无论如何都要人审，设计 §9）。
4. **granularity auto**：渲染 ≥10 条样例 → 字符分布 p95 → whole/chunked 决策写入契约。

`dry_run(tenant, contract_or_source_id)`：拉 1 页、渲染 ≤3 条样例文档 + external_id/hash 预览 + in_doc/metadata_only 分配表 + granularity 依据（p95 字符数/切块数）+ 契约修改时「预计 N 条重嵌入」，**不写库**。

### 2.6 调度（scheduler.py + notify.py，P2.1）

照搬 WeChatMPScheduler（src/wechat_mp/scheduler.py，数值已核对）：

- 驱动锁 `api_ingest_scheduler_lock` TTL 300s 续期；Redis 不可用拒绝启动（不无锁降级）；
- 60s 兜底扫描 + notify 唤醒（`notify.py`：`redis.rpush(make_key("api_ingest_queue_wakeup"), "1")` best-effort，先例 wechat_mp/notify.py L29）；
- `Semaphore(4)` 跨租户并发；租户×源 Redis 锁 + running 唯一索引双闸；
- 30min tick 四生成器：retry ≤50 / scheduled 每源每 tick 1 个 / full_scan ≤20 / stale 回收每 5min（heartbeat 阈值 1800s）；
- 生成器幂等（NOT EXISTS 活跃同类 run）；
- background_runner 注册见 I4。

### 2.7 API 与 agent 工具（api.py + src/tools/api_ingest_tools.py）

租户端（require_admin，前缀 `/api/saas/api-ingest`）：

| 端点 | 说明 |
|------|------|
| `POST /sources`（multipart：文档 + 名称 + source_code，可选 secrets） | 上传文档存租户配置文件存储（扩展名 {.txt,.md,.markdown,.json,.yaml,.yml}、上限 2MB、`storage/tenants/{tid}/api_ingest/`，设计 §13-14），建源（draft） |
| `GET/PATCH /sources`、`POST /sources/{id}/verify` | 源 CRUD；PATCH 改契约走 §4.1 生命周期（活跃 run 拒绝；content_rev+1；强制重新 dry-run）；PATCH 接受可选 `secrets` 对象整体替换（响应只回变量名+掩码，§13-13）；verify 为人工确认动作，请求体必须 `allowed_hosts_confirmed:true`（§13-4） |
| `POST /sources/{id}/dry-run` | 契约未保存也可预览，不写库 |
| `POST /sources/{id}/trigger?mode=sync\|full` | 立即同步，queued 排队；mode 写入 run.scope（P1 无增量源 full 行为等同 sync，§13-2） |
| `GET /runs`、`GET /runs/{id}`、`GET /records?status=`、`POST /records/{id}/retry` | 账本查询与重试；retry = 触发 trigger_type='retry' 的源级 run + 清该 record 的 next_retry_at（§13-3） |

平台端：`GET /portal/runs`、`GET /portal/sources`（`_require_platform_admin`，跨租户审计）。

agent 工具（BaseTool catalog 自动注册，I5 模式）：

- `api_ingest_sync`：参数可选 `source_code`；身份一律 `current_tool_execution_context().tenant_id`（缺失拒绝），**绝不由 LLM 传租户**；`asyncio.to_thread(trigger_sync, ...)` 薄入口；
- `api_ingest_status`：可选 `run_id`；跨租户统一「未找到」防探测。

## 3. 测试方案

- **目录**：`tests/unit/api_ingest/`（conftest 仿 tests/unit/wechat_mp/）+ `tests/integration/`；命令 `./scripts/dev_test.sh tests/unit/api_ingest`。
- **单测清单**（按模块）：
  - contract：DSL 校验（JSONPath 越权语法拒绝/模板字段不在 in_doc 拒绝/大括号转义/XML 转 JSON（disable_entities=True 下外部实体/参数实体样例拒绝）/query 含 {page} 拒绝）；渲染（字段缺失或类型不匹配→mapping_failed——含 str 字段走 `[0]` 索引用例/HTML→markdown 典型富文本（markdownify 包装）/通用结构渲染器嵌套与数组/title 对象形式截断派生）；hash 幂等（模板指纹与 granularity 规整串均进 hash——改 granularity 触发重判）；whole 回退（4000 边界/6000 硬钳/overflow 标记）。
  - executor：翻页停止三条件 + max_pages + empty/short_page 恒兜底；success_check 不过→contract_invalid；**SSRF**（私网 IP 字面量/DNS 解析私网/重定向到白名单外/重定向到私网/每跳上限/响应超限流式截断拒收且不落盘/限速间隔/DNS pin 改写纯逻辑（URL host→IP + Host 头 + sni_hostname）+ secrets 解析优先级与错误文案不含值）。
  - service：hash 三态（跳过零计费且不写行/软删 restore/重建）；**deleted 记录重现两态**（时间或 hash 变→恢复走 new/update；均未变→保持 deleted 静默跳过零计费）与 missing 重现恢复；**截断 run 不推进 miss_streak、不做删除对账**（设计 §6.4 核心用例）；并发 claim 冲突（唯一索引 conflict 路径，参考 tests/integration/test_wechat_mp_concurrency.py，无 DATABASE_URL skip）；余额预检（≤0 → skipped_no_credit 不拉取）；no_credit 退避（P2）。
  - assistant：宽容 JSON 解析；实探修正循环；granularity auto 决策。
- **集成测试（P2.3）**：fake API server 仿真宏陶形态（`status=1`/`datalist`/`total`/`pagenum`/`pernum`），先例 httpx.MockTransport（tests/unit/wechat_mp/test_wp9_client.py）。必备用例：全量 run 幂等两轮（第二轮全 skipped 零计费）；接口返回空列表 + total 异常 → 不删；截断 + 下轮全量恢复；two_strike 两轮后才软删；用户在知识库删文档 → record 回写 deleted → 下轮不重建不计费。

## 4. 分期任务拆解与验收标准

| 任务 | 内容 | 涉及文件 | 依赖 | 验收 |
|------|------|----------|------|------|
| P1.1 | 4 表 DDL + db_update.yaml + init-postgres.sql + config_codec + database.py 注册 | src/api_ingest/db.py、config_codec.py；database.py/I1-I3 三处 | — | 启动建表成功；secrets 加解密往返 + 密文前缀防重加密单测过 |
| P1.2 | 契约模型 + DSL 校验 + 提取渲染 + hash + granularity | src/api_ingest/contract.py | P1.1 | §3 contract 单测全过；宏陶商品样例 JSON 渲染出预期 markdown |
| P1.3 | WhitelistHttpApiTool + contract_direct | src/api_ingest/executor.py | P1.2 | §3 executor 单测全过（含 SSRF 全组）；MockTransport 翻页端到端 |
| P1.4 | run 生命周期 + _process_item + _persist_document_tx + 对账门禁 + embedding 计费 + knowledge 删除回写（I8） | src/api_ingest/service.py；src/knowledge/service.py 一处分支 | P1.1-P1.3 | 手动构造 run：新增/更新/跳过/restore/软删回写全链路；截断门禁用例过；隔离库并发用例过 |
| P1.5 | 契约生成 + dry-run + 租户端 API + 两工具 | src/api_ingest/assistant.py、api.py；src/tools/api_ingest_tools.py；main.py | P1.4 | 用宏陶 api.txt 走完 上传→生成→实探→dry-run→verify→trigger 全流程；工具在对话内可触发且租户隔离 |
| P2.1 | scheduler + notify + background_runner 注册 + 退避/stale | src/api_ingest/scheduler.py、notify.py；background_runner.py | P1.4 | 双实例只一副本持锁；tick 生成幂等；stale run 回收 interrupted |
| P2.2 | two_strike 完整 + 余额预检/no_credit + 摘要（api_ingest_summary 计费）+ detail_fetch（§6.5） | service.py、executor.py | P2.1 | no_credit 终态与退避；摘要失败回退原文；detail_fetch 增量门控与 404 复核用例 |
| P2.3 | agent_loop + portal 审计 + fake server 集成测试 | executor.py、api.py、tests/integration | P2.2 | §3 集成用例全过；agent_loop 在故意坏契约下兜底跑通 |
| P3 | 前端接入向导（Base* 组件）+ 图片转存/VL（可选按张计费）+ 宏陶两源替换存量脚本 | 前端 + service.py | P2 | 向导四步走通；存量脚本下线 |

每阶段按 dev_workflow 三智能体流程（开发 → 独立测试 → CR → 主控提交前验证）；P1.1/P1.2 等纯新增低风险任务可合并走一轮，P1.4/P2.x 触及共享文件（knowledge/service.py、background_runner.py、main.py）必须独立完整流程。

## 5. 发布前检查与回滚

- **启动链路检查**：`database.py` init_api_ingest_tables 无报错日志；background_runner 出现 api_ingest scheduler 启动日志（或明确的"未抢到锁"）；`POST /sources/{id}/dry-run` 与 `trigger` 冒烟。
- **回滚**：模块自包含——background_runner 去掉注册即停调度；API 路由摘除即无入口；4 表留存无副作用（无消费者）。knowledge/service.py 的 I8 分支为纯新增 origin 分支，回滚随模块整体回退。
- **观测**：run/item 两级账本 + 源级 last_error；上线初期人工每日看 `GET /runs` 失败占比（无外部告警通道，与公众号现状一致，设计 §9 已诚实声明）。
