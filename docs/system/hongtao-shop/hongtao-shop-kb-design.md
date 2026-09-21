# 宏陶商城产品知识库同步（hongtao_shop 专用模块）设计

> 版本 v1.4（2026-09-21：v1.1 评审修订；v1.2 确认六表 DDL；v1.3 用户看码后三点决议——knowledge 零侵入/分类用租户「产品」/砍 agent 工具；v1.4 零私有表重构：账本缓存配置全部复用 content_sync 通用五表 + 论坛关联内存态，见 §10 与附录）。
> 客户：宏陶陶瓷（`tenant_d18c257ff434`，agent2 测试环境验收后上正式）。
> 与 api_ingest 通用设计的关系：通用方案**搁置**（见 ideas.md 状态），本模块为宏陶专用；「通用模块成熟后收编」仅作远期备注，不构成依赖。

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| P1 | 数据管线：双接口拉取/status=1 过滤/价格排除/产品目录缓存/图级 VL 缓存/论坛关联/渲染/幂等落库 + 计费种子行 + CLI 触发 | 📋 待开发 | — |
| P2 | 定时调度 + 租户后台（开关/频率/立即运行/产品挑选；agent 工具已取消） | 📋 待开发 | — |
| P3 | agent2 验收（含栏目授权、外链抽样）→ 正式部署（正式库插配置行+种子行即上线） | 📋 待开发 | — |

## 1. 目标与边界

把宏陶商城两个对外接口（商品 `getShopProduct`、论坛 `getLuntan`，均无鉴权 GET）合并为**以产品为中心的知识库**：一个产品 = 一条知识 = 一个检索单元。智能体检索命中后，可把产品图/详情图/实拍视频链接发给咨询用户。

**明确不做**：论坛帖 v1 不单独建知识文档（只做产品素材挂靠）；无平台端 portal 审计；无契约 DSL/agent 契约生成/SSRF 通用防线（域名硬编码白名单、无用户输入 URL 拼接，攻击面不存在）。

**对通用层的触碰仅限挂载点**（不写业务逻辑）：`database.py` 建表块、`main.py` include_router 块、`background_runner.py` 调度注册块。**不改 `knowledge/service.py`**（2026-09-21 用户决议：平台服务只能有通用代码）——用户删除抑制走同步侧自愈（§6.3），只调用通用函数（计费/embedding/vector_db）。

## 2. 数据源与字段策略

商品接口共 674 条（实测 2026-09-21：578 正常名称 + 96 "编号:xx" 占位名；detail 富文本基本全图无文字；386/674 的 name 前缀含可提取型号）。

**过滤**：只入库 `status=1`（上架）商品。**价格字段彻底排除**：`market_price`、`sell_price` 不进正文、不进 metadata、**不进任何缓存表**（fetcher 收到即弃，含 products 目录缓存表 DDL 不设价格列）。

字段去向（产品接口）：

| 字段 | 正文（进向量） | metadata（原文，供发链接/查实时值） | 说明 |
|------|:---:|:---:|------|
| name | ✅ | ✅ | 前缀型号段（`TFZJ1890014`）正则提取为独立"型号"字段；**标题始终保留原名** |
| procode | ✅ | ✅ | 商品编码，优先作为型号 |
| sellpoint | ✅ | ✅ | 卖点（系列工艺） |
| id / bid / cid | ✅（基本信息） | ✅ | 产品接口无分类名，cid 仅存 ID |
| pic / pics | ❌（正文只提"共 N 张效果图"） | ✅ `pics` | 效果图轮播；**不做 VL**（§4） |
| detail 图片 | ❌ | ✅ `detail_images` | 全部 URL（去重、剔除 spacer 占位）；**做 VL**（§4） |
| detail 文字 | ✅ | — | 富文本文字极少，并入正文 |
| video | ❌ | ✅ `video` | 商品视频（实测全空，保留通道） |
| stock/sales/comment_score/comment_num | ❌ **（v1.1 移出正文）** | ✅ | **易变数值只进 metadata**（§5.1 hash 口径），智能体答实时库存经检索结果的 metadata 直接可见（v1.3 工具已取消） |
| createtime | ✅（上架时间，稳定） | ✅ | |
| **market_price / sell_price** | ❌ | ❌ | **彻底排除** |

## 3. 知识模型：正文 vs metadata

### 3.1 正文（进向量 + 分块，只含稳定内容）

```markdown
# {name}

{name}（型号 {model}）是宏陶商城在售的一款{sellpoint 系列}产品。
[VL 描述段：每张详情图 ≤100 字客观描述，剔除"图片无法识别"]
[仅占位名商品（编号:xx）：VL 提取到图上印刷型号/系列时补"疑似型号/系列：xx"]
该产品另有 M 组实铺实拍素材（来自门店工地实景帖）与 N 张效果图。

## 基本信息
- 商品ID / 商品编码 / 商品卖点 / 商品分类ID / 上架时间

---
数据来源：宏陶商城商品接口。
```

正文**不含**库存/销量/评分/同步日期（v1.1：全部移 metadata，消除 hash 抖动与新旧值不一致）。「M 组实拍素材」随论坛关联变化，低频触发重嵌，属预期。

### 3.2 metadata（documents.metadata，TEXT JSON，不进向量）

智能体经 `knowledge_base_search` 检索命中读到的 metadata（`_no_truncate` 全量返回，v1.3 已取消专有工具）获取链接与实时库存：

```json
{
  "name": "...", "model": "...", "procode": "...", "sellpoint": "...",
  "stock": 1000, "sales": 0, "comment_score": "5.0", "comment_num": 0,
  "pics": ["效果图轮播 URL..."], "detail_images": ["详情长图 URL..."], "video": "",
  "forum_media": [
    {"post_id": 419, "catename": "工地实景", "content": "TPJ157042地面铺贴实景…",
     "images": ["..."], "video": "https://...mp4"}
  ],
  "trace": {"source": "hongtao_shop", "native_id": 574,
            "content_hash": "sha256...", "sync_date": "2026-09-21", "ingested_at": "..."}
}
```

## 4. VL 解析范围与图级缓存（v1.1 修订）

**只对 detail_images 做 VL**（详情长图承载产品主体信息：印刷型号/系列/铺贴效果）：

| 图类别 | 数量级（冷启动） | VL | 产物去向 |
|---|---|:---:|---|
| detail_images 详情图 | 674 × ~4-5 ≈ **3,000 张** | ✅ | 描述进正文；占位名商品顺带提取疑似型号 |
| pics 效果图 | 674 × ~5.9 ≈ 3,980 张 | ❌ | 仅链接进 metadata |
| 论坛帖图 | 376 × ~6 ≈ 2,256 张 | ❌（v1） | 仅链接进 metadata.forum_media；v2 若需"实拍场景描述"再评估 |

冷启动 VL 费用约为 v1.0 方案（8,900 张）的 1/3。VL 实现：复制 wechat_mp/vision.py 模式（固定 GLM-5.3-Flash、Semaphore(3)、单张失败重试 1 次次选降级、描述 ≤100 字、前缀剥离、瓷砖定制指令：优先转述图上印刷的型号/系列/规格文字，无文字则一句客观白描）。

**图级 VL 缓存**（通用表 `bs_image_vision_cache`，UNIQUE(tenant_id, image_url)，任何源共用）：OSS 文件名内含内容 hash，URL 不变即图不变；缓存命中零费用零解析。存 description/model/status(ok/unrecognized/failed)/is_billed/parsed_at。

## 5. 处理管线

```
fetch（双接口全量分页；商品过滤 status=1，价格字段即弃；follow_redirects=False，
      响应大小护栏 10MB，UA 固定）
→ 记录账本刷新（bs_content_sync_records，payload 存目录字段，last_seen_at 对账用）
→ 论坛关联（§5，内存态全量重算，零关系表）
→ 图级 VL 缓存判增量 → VL 解析新增 detail 图
→ 渲染正文 + metadata
→ 幂等落库（origin='hongtao_shop'、external_id=f'product:{native_id}'，
   documents (tenant_id, origin, external_id) 部分唯一索引 upsert；
   content_hash 三态：§5.1）
```

### 5.1 content_hash 口径（v1.1 定版）

- **hash 输入 = pipeline_version + 渲染正文全文 sha256**（正文只含稳定内容，§3.1）；forum_media 以"M 组"计数参与正文，故关联变化会正确触发重嵌。
- **未变（跳过）**：不重建 chunks/vec、零计费、**不抖 updated_at**；stock/sales/score/sync_date 走 metadata「先比对后写」——值变才 UPDATE documents.metadata，一次轻量 UPDATE，不动 chunks。
- **变更**：删旧 chunks/vec 重建 + 重嵌计费（图级 VL 缓存在，只付 embedding）。
- **新建**：全链路。

### 5.2 落库实现

照 wechat_mp `_persist_document_tx` 模式模块内自写（ON CONFLICT upsert + chunks/vec 删旧重建）；**分类写死租户知识库顶级「产品」**——按 display_name 查找、查无则建普通分类（同后台建法），文档只进该分类（v1.3 决议：不建模块独立分类）；embedding 计费调用通用 `_record_knowledge_embedding_billing`（复用函数不改代码，source_type=`hongtao_shop_embedding`）。

## 6. 对账与删除（v1.1 定版：三条路径分开）

### 6.1 源侧下架（目录行 status≠1）

明确信号，**当轮收尾立即软删**该产品文档（documents.status='deleted'，检索/列表自动失效，chunks 保留）；items 记 action=delete。重新上架：hash 未变走 **restore**（恢复 status='active' 复用旧 chunks，零计费），变了走重建。

### 6.2 源侧消失（本轮 fetch_complete 且目录 last_seen_at 未更新）

两击软删：miss_streak+1，≥2 才软删（防接口分页抖动误删）；**fetch 不完整（截断 run）不推进 miss_streak、不做删除对账**（对账门禁：fetch_complete AND total 核对一致 AND 本 run items 全终态）。重现时按 hash/时间恢复（wechat_mp 同款语义）。

### 6.3 用户在知识库界面删除（同步侧自愈，v1.3 修订）

**不改 knowledge 通用服务**。抑制逻辑在同步侧自愈判定：`products.doc_id` 有值（曾成功入库，入库失败不会留下 doc_id）而 documents 行不存在（知识库硬删是行消失的唯一路径——本模块软删只改 status 不删行）→ 判定用户已删，置 `user_deleted` 后静默跳过（hash 未变零计费）；hash 变视为重新发布，重建并清标记。与即时回写语义等价（重建只发生在 sync 时，无中间窗口）。

### 6.4 后台取消勾选（selection 变化）

用户显式意图，**立即软删**；重新勾选：hash 未变 restore、变了重建（图级 VL 缓存在，零 VL 费）。

## 7. 论坛素材关联（v1.1 定版：可单测规则 + 实测样例）

帖子 `content` 提取 token，与产品目录（每产品的型号段 = name 前缀 `^[A-Z]{2,5}\d{6,7}[A-Z]?` 提取，procode 非空时优先）匹配：

1. **完整型号 token**：`[A-Za-z]{2,5}\d{6,7}[A-Za-z]?`（findall，大小写不敏感）→ 与目录型号段精确匹配（大小写不敏感）→ 挂。
2. **截短编号 token**：`(?<![0-9A-Za-z])\d{5,6}(?![0-9])` → 对目录各产品型号段**数字部分后缀反查**；**唯一命中才挂**；多命中记 match_evidence 疑似（存候选列表）不挂；无命中忽略。
3. 无 token 的帖子不关联、不入库（v1）。

一帖可挂多产品。实测样例（必须进单测）：

| content 片段 | token | 结果 |
|---|---|---|
| `TPJ157042地面铺贴实景` | TPJ157042 | ✅ 唯一挂 TPJ157042米克萨斯（id 419） |
| `tezj157004p实铺效果` | tezj157004p | ✅ 挂 TEZJ157004P金水流砂（大小写不敏感，帖 422） |
| `厨房80357` | 80357 | ✅ 后缀唯一命中 TPG80357芭菲米白 |
| `卫生间26048，26097` | 26048 / 26097 | 无命中，忽略（目录中无尾号命中，宁缺勿误） |
| `157004`（假想） | 157004 | ⚠️ 14 命中（梦幻半山/和沐·莱姆石/…），记疑似不挂 |

关联结果聚合进各产品 metadata.forum_media；**关联为内存态每轮全量重算，不建关系表**（v1.4 决议：帖子数据每轮实时拉全量，重算零成本，metadata 即当轮快照）。

## 8. 租户隔离、定时调度与后台管理

代码不写死租户，"谁在定时跑"由配置表行决定：

- **`bs_content_sync_sources`**（通用表，module='hongtao_shop'）：`UNIQUE(tenant_id, module)`、`enabled`、`sync_interval_hours`、`selection_mode('all'|'ids')`、`selected_ids JSONB`、`last_sync_at/last_error`。当前仅 `tenant_d18c257ff434` 一行；正式上线在正式库插同款行，零代码变更。
- **调度**：`HongtaoIngestScheduler` 注册进 background_runner（照 wechat_mp：Redis 单副本驱动锁、30min tick 扫描到期源、Semaphore(4) 跨租户、租户×源 Redis 锁 + runs 活跃唯一索引双闸、5min stale 回收 heartbeat 1800s）。
- **账本**：通用 `bs_content_sync_runs/items`（module 维度）记每次运行的拉取/VL 张数/计费/失败，观测查表。
- **余额预检**：trigger 时余额 ≤0 → run 直接终态 `skipped_no_credit` 不拉取。
- **触发统一**：定时 tick、后台"立即运行"、agent 工具同一 `trigger_sync()` 建 queued run。

租户后台（`/api/saas/hongtao-shop` 前缀，require_admin）：

| 端点 | 说明 |
|------|------|
| `GET /source` / `PATCH /source` | 读/改 enabled、sync_interval_hours、selection_mode、selected_ids |
| `POST /source/trigger` | 立即运行 |
| `GET /runs`、`GET /runs/{id}` | 运行账本与进度 |
| `GET /products?keyword=&page=&selected=` | 产品目录缓存列表（挑选 UI 数据源，本地表分页，不实时调外部 API） |

前端：源设置卡（开关+频率+立即运行）+ 产品挑选列表（复用 Base* 组件）。

~~agent 工具~~（2026-09-21 用户决议取消：租户定制功能不注册平台工具；发图由 knowledge_base_search 返回 metadata 天然覆盖，触发走租户后台）。原方案：

- `hongtao_shop_sync`：触发同步。


## 9. 计费（v1.1 补种子行）

- VL 计费（2026-09-21 审查修正口径）：**按张实际 token × 所用模型单价走标准算价**（`calculate_credit_cost`，照 wechat_mp WP13 现行模式，每张一条 `chat_records` source_type=`hongtao_shop_image_parse`，fail-open）；`price_per_call` 种子行（0.01 元/张）保留不用，仅备运营切换按张固定价（wechat_mp 同款处理）。"图片无法识别"/失败张不计费。种子行不入平台 DDL，由 hongtao 模块自举（bootstrap.ensure_billing_seed，CLI --init-source 种植，幂等不改价）。
- embedding：通用 `_record_knowledge_embedding_billing`，source_type=`hongtao_shop_embedding`。
- 计费全记源配置 tenant_id；runs/items 记 VL 张数/tokens、embedding tokens。

## 10. 目录结构与数据表（v1.4：零私有表）

```
src/services/content_sync/              # 平台通用基础设施（任何数据源模块复用）
  db.py                        # 五张通用表幂等 DDL（注册进 database.py）
src/tenant_custom/hongtao_shop/  # 宏陶租户定制（零私有表、零平台侵入）
  fetcher.py  joiner.py  vision.py  renderer.py  service.py
  scheduler.py  api.py         # P2
```

**五张通用表**（以公众号 `bs_wechat_mp_*` 为蓝本泛化加 `module` 维度；公众号自身不迁移，远期可选）：

| 表 | 关键约束（v1.4 定稿） |
|---|---|
| bs_content_sync_sources | UNIQUE(tenant_id, module)；enabled/sync_interval_hours（默认 24）/selection_mode(all\|ids)/selected_ids |
| bs_content_sync_runs | 部分唯一索引 (tenant_id, module) WHERE status='running'（租户×源串行）；六计数 + vl_parsed/vl_billed/embedding_tokens/credits + 门禁佐证 fetch_complete/total_reported |
| bs_content_sync_items | UNIQUE(run_id, native_id)；action(new/update/skip/delete/restore，失败项为空)/status/error_code/vl_images/vl_billed/embedding_tokens/计费三字段 |
| bs_content_sync_records | UNIQUE(tenant_id, module, native_id)；native_id/external_id/content_hash/pipeline_version/doc_id/user_deleted/miss_streak/fail_count/next_retry_at/payload JSONB（各源目录展示字段）/last_seen_at |
| bs_image_vision_cache | UNIQUE(tenant_id, image_url)（无 module——任何源的图共用）；description/model/status/is_billed |

计费种子行：`hongtao_shop_image_parse = 0.01 元/张`（现行按 token 计费保留不用，备运营切换）；**不入平台 DDL**，由模块自举种植（`bootstrap.ensure_billing_seed`，CLI `--init-source` 执行，与 §9 一致）。

**分类**：文档归属租户知识库顶级分类「产品」（display_name 查找、查无则建普通分类），不建模块独立分类（v1.3 决议）。

## 11. 分期计划与验收

| 阶段 | 内容 | 验收 |
|------|------|------|
| P1 | 管线全链路 + 计费种子行 + CLI 触发（重构 scripts/hongtao_product_ingest.py 雏形进模块） | agent2 全量入库：status 过滤后数量正确；检索"金水流砂/欧典米灰"命中；metadata 链接抽样可访问（防盗链风险登记）；**重跑全缓存命中：零 VL 费 + hash 未变零 embedding 费**；价格字段 chunks+metadata+缓存表三处零出现；占位名商品含疑似型号 |
| P2 | scheduler + 后台 API/页面（agent 工具已取消，2026-09-21 决议） | 定时到点自动跑；后台改频率/立即运行/挑选生效（取消勾选→软删） |
| P3 | agent2 真机验收 → 正式部署 | **「产品」栏目授权给目标子智能体**（栏目授权硬隔离，配置步骤）；客户验收检索与发图；正式库插配置行+种子行 |

每阶段按 dev_workflow 三智能体流程；P1 纯新增模块（+db_update.yaml 种子）可合并走一轮，P2 触及 background_runner/main.py/knowledge 挂载点走完整流程。

## 附录：开发期架构决议（2026-09-21，用户定版）

- **零私有表（v1.4）**：撤销 v1.2 的六张 hongtao 私有表。账本/缓存/配置复用平台通用 `src/services/content_sync/` 五张表（module 维度；以公众号表为蓝本泛化，公众号不迁移）；products/forum_posts 业务关系表砍除——产品数据只存知识库（documents + metadata），论坛关联内存态每轮重算。用户动机：「以后 100 个源不能建 600 张表」。计费本就合用通用 chat_records。
- **分类用租户「产品」（v1.3）**：不建 k_hongtao_products 独立分类；同步启动查租户顶级「产品」分类（查无则建普通分类），文档只进该分类。

- **knowledge 层零侵入（2026-09-21 用户决议）**：撤销 v1.2 的「delete_document 加 origin 回写分支」方案，改为同步侧自愈判定（§6.3）——平台服务只允许通用代码，租户定制的删除抑制收敛在本模块 sync 逻辑内。
- **VL 计费口径修正**：§9 原文「按张 price_per_call 种子行计费」为实现前旧口径；实现（及 wechat_mp WP13 现行先例 src/wechat_mp/service.py:3342）为按张 token 计费，种子行保留不用。独立审查（2026-09-21 二轮）指出文档滞后，本条理顺。
- **agent 工具取消**：原 §8 的 `hongtao_shop_sync`/`hongtao_product_media` 两工具取消。理由：①租户定制功能不注册为平台工具（catalog 全局可见，污染其他租户的工具面）；②触发需求由后台定时+手动按钮完全覆盖；③发图能力由 `knowledge_base_search` 天然覆盖（返回含 metadata 且 `_no_truncate`，已核实实现）。

- **模块位置**：租户定制代码不进平台 `src/` 根目录，统一放 `src/tenant_custom/`（租户定制插件目录，边界见 `src/tenant_custom/__init__.py`：定制模块不得被平台层 import，对通用层触碰仅限挂载点）；本模块落位 `src/tenant_custom/hongtao_shop/`。agent 工具薄壳因 catalog 发现机制留在 `src/tools/`。

## 附录：v1.0 → v1.1 变更（开发前评审结论落实）

1. **hash 口径定版**：正文移除易变字段（stock/sales/score/同步日期全部只进 metadata）；hash=pipeline_version+正文 sha256；跳过路径 metadata 先比对后写、不抖 updated_at。
2. **对账三路径定版**：下架立即软删+上架 restore；消失两击软删（fetch_complete 门禁）；用户知识库删除加 origin 回写分支（user_deleted，hash 未变静默跳过）。
3. **VL 范围收窄**：仅 detail_images（≈3,000 张）；pics 与论坛图不 VL，费用降至 1/3。
4. **型号规则精确化** + 4 组实测样例进单测（§7）。
5. **六表 DDL 关键约束定版**（§10），products 无价格列。
6. **计费种子行**纳入 P1（三处同步）。
7. 次要项采纳：占位名标题保留原名正文补疑似系列；http 细节（follow_redirects=False/10MB 护栏/固定 UA）；media 工具走目录缓存不查 JSON 列；栏目授权入 P3 步骤；api_ingest 通用方案搁置（不删，ideas.md 状态注记）；收编愿景降级为远期备注。
