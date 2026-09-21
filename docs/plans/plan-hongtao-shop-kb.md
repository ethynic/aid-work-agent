# hongtao_shop 开发计划（技术实现方案 + 分期开发计划）

> 设计依据：[hongtao-shop-kb-design.md](../system/hongtao-shop/hongtao-shop-kb-design.md)（v1.1，2026-09-21 开发前评审修订版）。本文不重复设计论证，只落实"怎么实现、按什么顺序、怎么验收"；知识模型、hash 口径、对账三路径、型号规则以设计文档为准，冲突时以设计文档为准并回改本文。
> 模块：`src/tenant_custom/hongtao_shop/`（租户定制插件目录，边界见 src/tenant_custom/__init__.py）+ 平台通用基础设施 `src/services/content_sync/`（五张通用表）。零私有表、零平台侵入：不改 knowledge 通用服务（用户删除走同步侧自愈）；对通用层的触碰仅剩 P2 两个挂载点（main.py 路由块 / background_runner.py 调度块）。
> **六表 DDL 已由设计 v1.2 确认（设计 §10 定稿）**，与本文草案的差异：products 补 `fail_count/next_retry_at`；`sync_interval_hours` 默认 24。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| P1.1 | 通用表（content_sync 五表）三处同步 + 种子行 + database.py 注册 | ✅ 完成（2026-09-21，v1.4 重构） | 初版六张私有表经三轮用户审查后重构为零私有表：账本/缓存/配置复用平台通用表（公众号表为蓝本 + module 维度）；型号正则 {6,7}→{5,7}（样例 5 位） |
| P1.2 | fetcher.py + joiner.py（分页拉取/价格即弃/detail 提取/型号 token 规则） | ✅ 完成（2026-09-21） | 单测含设计 §7 五组样例；真实接口 dry-run：674 商品/376 帖全量完整、184 帖可关联 |
| P1.3 | vision.py（VL + 图级缓存 + 落账）+ renderer.py（正文/metadata/hash/单 chunk） | ✅ 完成（2026-09-21） | VL 按张 token 计费照 wechat_mp WP13 现行模式（种子行保留不用）|
| P1.4 | service.py（run 生命周期 + 目录刷新 + 三态 + 对账三路径 + 计费 + 余额预检）+ knowledge 回写分支 | ✅ 完成（2026-09-21） | 一轮独立测试+CR 修复 4 项 P1（run 兜底/VL 单例/观测账本/conftest 清理）；二轮独立审查（用户侧，2026-09-21）修复 detail 图去重 P1（重复解析双倍计费）+ 计费口径/注释路径两处文档 + 5 项 P2（UTC+8 时区/缓存写 fail-open/下载限流/last_error 成功才清/失败 item 口径），56 用例全绿 |
| P1.5 | CLI 重构（雏形改薄壳）+ 雏形残留清点清理 + agent2 试跑 | 🔧 进行中 | 代码完成（--init-source/--dry-run/--limit）；agent2 真机试跑待环境（含雏形 k_products 残留清点，清理 SQL 已写入 CLI 头注） |
| P2.1 | scheduler.py + background_runner 注册 + stale 回收 | 📋 待开发 | — |
| P2.2 | api.py 租户后台 + main.py 注册 | 📋 待开发 | — |
| P2.3 | 前端（源设置卡 + 产品挑选列表） | 📋 待开发 | — |
| ~~P2.4~~ | ~~agent 工具两个~~ | ⛔ 已取消（2026-09-21 用户决议） | 租户专有功能不注册平台工具：触发走后台；发图由 knowledge_base_search 天然覆盖（返回含 documents.metadata 且 _no_truncate） |
| P3 | agent2 验收（栏目授权/外链抽样）→ 正式部署 | 📋 待开发 | — |

## 1. 集成点清单（新代码挂到哪，全部已核对现有模式）

| # | 改动点 | 位置 | 照搬的模式（已验证存在） |
|---|--------|------|--------------------------|
| I1 | 通用表 DDL 注册 | `src/db/database.py` `_init_postgresql()` | try/except 块调 `init_content_sync_tables(conn)`（content_sync 平台通用模块，非租户定制），失败仅 logger.error + rollback 不阻断启动 |
| I2 | 增量迁移（建表 + 种子行） | `deploy/db_update.yaml`（批次 `2026-09-21 12:00:00`） | 五张通用表 + 种子行，全部幂等；`_load_db_update_blocks` 校验严格递增、非法即拒启动 |
| I3 | 全量建表 | `deploy/init-postgres.sql` 末段 content_sync 区 | 与 I1/I2 三处同步 |
| I4 | 调度协程注册 | `src/background_runner.py`（WeChatMPScheduler 注册 L190-201、优雅停机 L214-218） | `try: HongtaoIngestScheduler(); await start() except: logger.error("...启动失败（不影响 runner）")`；停机处补 `await stop()` |
| I5 | 租户后台路由 | `src/tenant_custom/hongtao_shop/api.py`，`APIRouter(prefix="/api/saas/hongtao-shop", ...)`，main.py include_router 块（L1789-1816 段）追加 | 鉴权 `require_admin(request)`（src/saas/api/tenant_auth.py L319，platform_admin 可 X-Tenant-Id 代管）；中间件按 `/api/saas/` 前缀解析租户（src/saas/middleware.py L107） |
| ~~I6~~ | ~~knowledge 删除回写~~ | **已撤销（2026-09-21 用户决议：平台服务只允许通用代码）** | 改为同步侧自愈（service._process_product）：doc_id 有值且 documents 行不存在（知识库硬删）→ 置 user_deleted 静默跳过；与回写语义等价，knowledge/service.py 已还原 |
| I7 | agent 工具注册 | `src/tools/hongtao_shop_tools.py` | 继承 `BaseTool`（src/tools/base.py L35）+ catalog 自动登记；先例 `WechatMPSyncTool`（src/tools/wechat_mp_sync_tool.py L103）：`_require_tenant()` 取 `current_tool_execution_context().tenant_id`，service 调用一律 `asyncio.to_thread(...)` |
| I8 | embedding 计费 | `knowledge/service.py` `_record_knowledge_embedding_billing`（L113，keyword-only source_type 供外部源复用） | `source_type="hongtao_shop_embedding"`，fail-open；调用先例 wechat_mp/service.py L3285 |
| I9 | VL 解析 | 模块内 `vision.py` 复制 `src/wechat_mp/vision.py` 模式（不 import 不修改） | 固定模型 `_PINNED_VISION_MODEL="GLM-5.3-Flash"`（L99）、Semaphore(3)、单张失败重试 1 次次选降级、描述 ≤100 字（L59）；落账 source_type=`hongtao_shop_image_parse` 按张计费，价格种子行与 wechat_mp 同价 0.01 元/张 |

## 2. 技术实现方案

### 2.1 数据层（v1.4：零私有表，复用 src/services/content_sync/ 通用五表）

模块不建任何私有表。`src/services/content_sync/db.py` 的 `init_content_sync_tables(conn)` 建
五张通用表（module='hongtao_shop' 维度隔离，以公众号 bs_wechat_mp_* 为蓝本泛化；
公众号自身不迁移），DDL 三处同步（content_sync/db.py / init-postgres.sql /
db_update.yaml，datetime 递增幂等）：

- `bs_content_sync_sources`：UNIQUE(tenant_id, module)；enabled/`sync_interval_hours`（默认 24）/selection_mode(all|ids)/selected_ids。
- `bs_content_sync_runs`：部分唯一索引 (tenant_id, module) WHERE status='running'；owner_token/heartbeat_at；六计数 + vl_parsed/vl_billed/embedding_tokens/credits_charged + 门禁佐证 fetch_complete/total_reported。
- `bs_content_sync_items`：UNIQUE(run_id, native_id)；action(new/update/skip/delete/restore，失败项为空)/vl_images/vl_billed/embedding_tokens/计费三字段。
- `bs_content_sync_records`：UNIQUE(tenant_id, module, native_id)；目录展示字段进 payload JSONB（name/model/procode/sellpoint/cid/status 原值）+ content_hash/pipeline_version/doc_id/user_deleted/miss_streak/fail_count/next_retry_at/last_seen_at。
- `bs_image_vision_cache`：UNIQUE(tenant_id, image_url)（无 module，任何源共用）。
- 计费种子行：hongtao_shop_image_parse=0.01 元/张（现行按 token 计费保留不用）；**不入平台 DDL，由模块自举**（bootstrap.ensure_billing_seed，CLI --init-source 种植）。

**分类（v1.3 决议）**：不建模块独立分类；service 按 display_name 查租户顶级「产品」
分类，查无则建普通分类（source_type 自动生成，同平台 create_category 约定），
文档只进该分类。

无 secrets（接口无鉴权），无 config_codec。

### 2.2 fetcher.py（纯逻辑 + httpx）

照雏形 `scripts/hongtao_product_ingest.py` 已实测逻辑模块化（urllib 改 httpx）：

- `fetch_products()` / `fetch_posts()` → `FetchResult(items, fetch_complete, total_reported)`：`pernum=100` 分页至 `total`（status==1 校验，≠1 记 error；页间断 0.3s；单页重试 3 次退避）；护栏：`follow_redirects=False`、响应体 10MB 上限（超限截断报 fetch_failed）、固定 UA（雏形 `aid-kb-ingest/1.0` 沿用）。
- **价格即弃**：解析后立刻 `pop market_price/sell_price`，后续任何结构不可见（单测断言）。
- **detail 提取**（雏形 `extract_detail` 原样迁移）：detail 为 UEditor 富文本 **JSON 数组**（非 HTML 字符串）——逐 block 取 `content` HTML，`src=` 正则提图 URL（剔 `spacer.gif`、去重），剥标签取文字（剔除 URL）。
- 数值字段统一 `_s()` 规整（该接口数值也返回字符串）。
- 单测用 `httpx.MockTransport`（先例 tests/unit/wechat_mp/test_wp9_client.py L139-153 构造注入）。

### 2.3 joiner.py（型号关联，纯函数）

- `catalog_model(item)`：`procode` 非空优先，否则 name 前缀 `^[A-Z]{2,5}\d{6,7}[A-Z]?` 提取，无则 None。
- `extract_full_tokens(content)`：`[A-Za-z]{2,5}\d{6,7}[A-Za-z]?` findall；`extract_truncated_tokens(content)`：`(?<![0-9A-Za-z])\d{5,6}(?![0-9])` findall。
- 匹配（设计 §7 定版规则）：full token 大小写不敏感精确匹配目录型号段 → 挂；truncated token 对目录型号段**数字部分**后缀反查，唯一命中才挂，多命中记 match_evidence（原因 + 候选列表）不挂；无命中忽略。一帖可挂多产品。
- **单测必含设计 §7 五组实测样例**（含 `tezj157004p` 大小写、`157004` 14 命中疑似）。

### 2.4 vision.py + renderer.py

**vision.py**（复制 wechat_mp/vision.py 模式）：固定 GLM-5.3-Flash（zhipu）、Semaphore(3)、单张失败重试 1 次后次选降级（多模态清单）、描述 ≤100 字截断；瓷砖定制指令「优先转述图上印刷的型号/系列/规格文字，无文字则一句客观白描」；占位名商品的疑似型号/系列由同一指令产出，结果回填正文（§3.1）与 products 缓存 model 仅作展示候选。缓存读写 `bs_image_vision_cache`：URL 未命中才解析入库；`unrecognized`（"图片无法识别"）不计费。**仅 detail_images 进 VL**（pics/论坛图不解析）。

**renderer.py**：`render_product(item, model, vl_descriptions, forum_media, sync_date) -> (title, content_md, metadata, content_hash)`：

- 正文按设计 §3.1 模板（无库存/销量/评分/同步日期）；标题 = name 原名（长度护栏 120 字符）；占位名商品补「疑似型号/系列」行；`N 张效果图`/`M 组实拍素材` 计数进正文（关联变化触发重嵌，设计已声明预期）。
- `content_hash = sha256(pipeline_version + 正文全文)`（§5.1）；pipeline_version 模块常量，渲染/分块管线升级时 +1。
- metadata 按设计 §3.2：name/model/procode/sellpoint/stock/sales/comment_score/comment_num/pics/detail_images/video/forum_media/trace（含 sync_date）。
- **分块**：一产品 = 一 chunk（`precomputed_chunks` 旁路，先例 knowledge/parsers/__init__.py L26 + knowledge/service.py L499 消费），超 `MAX_EMBEDDING_CHUNK_CHARS=6000`（knowledge/chunker.py L10）才回退 `TextChunker` 切分——对应设计「一个产品 = 一条知识 = 一个检索单元」。

### 2.5 service.py（`HongtaoShopSyncService`）

run 生命周期照 wechat_mp/service.py 已验证模式（受理 `import_urls` L332 / 领取 `_claim_next_run` L1038 唯一约束冲突 L1062 / 心跳 L1067 / 终态 `_finalize_run` L3729）：

- `trigger_sync(tenant_id, trigger_type)`：余额预检（≤0 → run 直接终态 `skipped_no_credit` 不拉取）→ 建 queued run；`run_now()`（P1 CLI 用）= trigger + 同进程 claim 执行。
- `_execute_run`：fetch 双接口 → **目录刷新**（全量 upsert products：name/model/status/last_seen_at/processing_status；fetch 不过滤 status——目录存原值，仅入库过滤 status=1）→ joiner 关联（upsert forum_posts + linked_product_ids/match_evidence，全量重算）→ selection 过滤（ids 模式仅选中集入库）→ 逐产品 `_process_product` → `_reconcile`（对账三路径）→ `_finalize_run`（聚合计数 + credits + 终态）。
- `_process_product` 三态（§5.1）：`user_deleted AND hash 未变 → 静默跳过`；`hash 同 AND doc active AND pipeline 同 → skip`（metadata 先比对后写 stock/sales/score/sync_date，值变才一次轻量 UPDATE 不动 chunks 不抖 updated_at——skip 路径无变化完全不写行）；`doc 软删（下架/取消勾选后重新命中）→ restore`（恢复 status='active' 复用旧 chunks 零计费）；否则 new/update（VL 新图 → 渲染 → embedding 事务外 → `_persist_document_tx`）。
- `_persist_document_tx` 照 wechat_mp L2900 模式：documents `INSERT ... ON CONFLICT (tenant_id, origin, external_id) WHERE external_id IS NOT NULL DO NOTHING`（部分唯一索引 uq_documents_origin_external 已存在，wechat_mp/db.py L28-31）冲突回查转 UPDATE；chunks/chunks_vec 删旧重建（`vector_db.delete_by_doc` + `DELETE FROM chunks`）；单事务落 documents/chunks/chunks_vec + products/items 终态 + 惰性建分类 `k_hongtao_products`「产品知识库」（`ON CONFLICT (tenant_id, source_type) DO NOTHING`，先例 wechat_mp `_ensure_categories_tx` L3157）；embedding 事务外先算（wechat_mp 同款 L2092）。
- **对账三路径**（§6，收尾执行。门禁 `fetch_complete AND total_reported 核对一致 AND 本 run items 全终态` **仅约束「消失两击」路径**——不满足则 run partial_failed、不推进 miss_streak；下架与取消勾选是明确信号，不受门禁约束（设计 §6.1/§6.4，v1.1 评审定版）：
  - 下架：目录 status≠1 且文档 active → 当轮立即软删（documents.status='deleted'，chunks 保留）+ items action=delete；
  - 消失：门禁满足且 last_seen_at 未更新 → miss_streak+1，≥2 软删（两击）；重现按 hash/时间恢复（wechat_mp L1291-1315 同款语义）；
  - 取消勾选：selection 变化后未选中的已入库产品 → 立即软删；重新勾选 hash 未变 restore。
- 计费：embedding `_record_knowledge_embedding_billing(source_type="hongtao_shop_embedding")`；VL 落账 `chat_records` source_type=`hongtao_shop_image_parse`（照 wechat_mp image_parse 落账路径，价格查 token_cost_prices 种子行，fail-open，unrecognized 不计）。
- 失败退避：单产品失败 `fail_count+1`、`next_retry_at = now + 300s × 2^fail_count`（上限 24h）。

### 2.6 用户删除的同步侧自愈（原 I6 已撤销）

不改 knowledge/service.py（用户决议：平台服务只允许通用代码）。`_process_product` 开头判定：`products.doc_id` 有值（曾成功入库）且 documents 行不存在（知识库硬删是行消失唯一路径；本模块软删只改 status）→ `_mark_user_deleted` 置位 → 走静默跳过；hash 变视为重新发布走重建清标记。单测 `test_kb_delete_detected_by_sync_self_healing` 覆盖。

### 2.7 CLI 重构（P1.5）

`scripts/hongtao_product_ingest.py` 改为薄壳：`--tenant-id`（默认 agent2 租户）/ `--init-source`（建默认源行：enabled、24h、all，设计 §10 v1.2 默认值）/ `--dry-run` / `--limit N`（冒烟）→ 调 `service.run_now()`。雏形旧路径（upload_document + title 幂等 + LLM 摘要）废弃。**前置清点**：检查 agent2 是否已有雏形跑出的 `k_products` 分类与 manual_upload 文档，有则提供清理 SQL（该数据不走 origin/external_id，与新链路不冲突但会重复检索）。

### 2.8 P2：scheduler + api + 前端 + 工具

- scheduler.py 照 WeChatMPScheduler（驱动锁 `hongtao_shop_scheduler_lock` TTL 300s 续期、Redis 不可用拒绝启动、60s 兜底、Semaphore(4)、30min tick 扫到期源、5min stale 回收 heartbeat 1800s）；background_runner 注册见 I4。
- api.py 见 I5：`GET/PATCH /source`、`POST /source/trigger`、`GET /runs`、`GET /runs/{id}`、`GET /products?keyword=&page=&selected=`（products 缓存本地分页，不实时调外部 API）；PATCH 改 selection 收尾对账软删/恢复。
- 前端：源设置卡 + 产品挑选列表（复用 Base* 组件，frontend-design 流程）。
- ~~agent 工具~~（2026-09-21 用户决议取消）：租户定制功能不注册进平台工具目录。手动/定时触发统一走租户后台；发图能力无需专有工具——`knowledge_base_search` 返回含 `documents.metadata`（pics/detail_images/video/forum_media/stock 随检索命中透出，`_no_truncate` 全量），已核实 src/tools/knowledge/knowledge_base_tool.py L190。

## 3. 测试方案

- **目录**：`tests/unit/hongtao_shop/`（conftest 仿 tests/unit/wechat_mp/：真实 DB 模式 + 随机租户清理六表与 documents/chunks/chunks_vec/knowledge_categories；纯逻辑测试不依赖 DB）+ `tests/integration/`。命令 `./scripts/dev_test.sh tests/unit/hongtao_shop`。
- **单测清单**：
  - db：建表幂等（重复执行无错）、种子行存在、无价格列断言。
  - fetcher：MockTransport 分页翻完/total 核对/status≠1 报错/重试/价格字段即弃（返回结构无 market_price/sell_price）/detail 提取（UEditor JSON 数组、spacer 剔除、去重、文字提取）/10MB 截断。
  - joiner：设计 §7 五组样例 + 边界（`157004p` 裸带尾字母数字按截短处理疑似不挂、7 位纯数字 token 忽略、一帖多 token 挂多产品）。
  - renderer：正文模板（含占位名疑似型号行）/metadata 完整性/hash 幂等（同输入同 hash；pipeline_version 变则变；论坛计数变则变）/单 chunk 与 6000 回退。
  - vision：缓存命中零解析零计费/unrecognized 不计费/失败重试后次选。
  - service：三态（skip 零计费且不写行/metadata 先比对后写/restore 复用 chunks 零计费/new-update 删旧重建）/对账三路径（下架立即软删、消失两击、门禁不满足不推进）/user_deleted 静默跳过与 hash 变重建（清标记）/selection 取消勾选软删/余额预检 skipped_no_credit/价格零出现（chunks+metadata+缓存表三查）。
  - knowledge 回写分支：删 origin='hongtao_shop' 文档 → products.user_deleted 置位。
- **集成**：并发 claim 唯一索引冲突路径（仿 tests/integration/test_wechat_mp_concurrency.py，无 DATABASE_URL skip）。
- **agent2 试跑**：`--init-source` → `--limit 3` 冒烟 → 全量 → 重跑验证零 VL 费 + hash 未变零 embedding 费。

## 4. 分期任务拆解与验收

| 任务 | 涉及文件 | 依赖 | 验收 |
|------|----------|------|------|
| P1.1 | src/tenant_custom/hongtao_shop/db.py；database.py / init-postgres.sql / db_update.yaml（I1-I3） | DDL 设计确认 | 启动建表成功；种子行落库；db_update 校验通过（datetime 递增） |
| P1.2 | fetcher.py、joiner.py | P1.1 | §3 fetcher/joiner 单测全过（含价格即弃与五组样例） |
| P1.3 | vision.py、renderer.py | P1.2 | §3 vision/renderer 单测全过 |
| P1.4 | service.py；knowledge/service.py 分支（I6）；tools 不动 | P1.1-P1.3 | §3 service 单测 + 集成全过；knowledge 分支用例过 |
| P1.5 | scripts/hongtao_product_ingest.py 重构 + 残留清点 | P1.4 | agent2：674 全量入库、status 过滤数正确、检索"金水流砂/欧典米灰"命中、metadata 链接抽样可访问、重跑全缓存命中零 VL 费 + hash 未变零 embedding 费、价格三处零出现、占位名含疑似型号 |
| P2.1 | scheduler.py；background_runner.py（I4） | P1.4 | 双实例只一副本持锁；到点自动跑；stale 回收 interrupted |
| P2.2 | api.py；main.py（I5） | P1.4 | 后台改频率/立即运行/挑选生效（取消勾选→软删） |
| P2.3 | 前端页面 | P2.2 | `cd frontend && npm run build` 过；页面走通 |
| ~~P2.4~~ | （已取消，见进度表决议） | — | — |
| P3 | 部署脚本/SQL | P2 | 栏目授权给目标子智能体；客户验收；正式库插配置行+种子行 |

流程：P1.1-P1.5 为设计 §11 的 P1（纯新增模块 + db_update 种子），合并走一轮三智能体（开发 → 独立测试 → CR）；P2.1/P2.2 触及 background_runner/main.py 挂载点、P1.4 触及 knowledge/service.py，各自独立完整流程；P2.3 前端走 frontend-design。

## 5. 发布前检查与回滚

- **启动链路**：database.py 无 hongtao 建表报错；background_runner 出现调度启动日志（P2 后）；`GET /source` 与 `POST /source/trigger` 冒烟（P2 后）。
- **回滚**：模块自包含——background_runner 去注册即停调度；路由摘除即无入口；knowledge I6 分支为纯新增 origin 分支随模块回退；六表与种子行留存无副作用（无消费者）。
- **观测**：runs/items 账本 + sources.last_error；上线初期人工看 `GET /runs` 失败占比（无外部告警，与公众号现状一致）。
