# 就业咨询智能体客户数据库同步方案研究

> - 日期：2026-11-16 初稿；2026-11-17 两轮用户决议后定稿（现行方案见 §7，初版镜像表/SQL 工具路线已按用户要求彻底删除）
> - 输入：[就业咨询智能体关联库表数据字典](employment-agent-db-data-dictionary.md)（gt_lyjy / gt_sccy 两库 85 张关联表）；本系统知识库与外部数据接入现状调研（2026-11-16，两个只读探索智能体结论）；[宏陶商城产品知识库同步设计](../system/hongtao-shop/hongtao-shop-kb-design.md)（v1.10 已交付）
> - 结论有效性：客户库结构以 2026-11-11 只读探查为准；本系统代码事实以 2026-11-16 为准
> - 客户场景：基于客户既有「聊易就业」（gt_lyjy）与「水城创业网」（gt_sccy）两库，构建就业咨询智能体（职业介绍/政策/就业指导咨询），以 **iframe 嵌入客户官网**（同事开发中），7×24 应答、捕捉职业意向、复杂问题转人工

---

## 1. 核心结论（TL;DR）

1. **宏陶数据源模式整体复用**：`src/services/content_sync/` 通用表 + `src/tenant_custom/<module>/` 模块结构 + portal 数据源授权 + 租户后台配频率，一期交付两个 module：`lyjy_employment`（聊易就业）+ `sccy_policy`（水城创业网 → 「创业政策」分类）。
2. **一期纯知识库路线**：职位与文章统一「一条 = 一个检索单元」文档化入 KB；正文结构化字段区承载薪资/地区/要求等数值条件，混合检索处理口语语义，TopN 后 LLM 读 metadata 完成精确过滤；**无镜像表、无 SQL 查询工具**。
3. **作废/更新时效三重保证**：① 每轮 run 目录全量状态比对（下架/恢复滞后 = run 频率 15~30 分钟）；② **岗位 `deadline` 直接写 KB `documents.expires_at`——检索层天然排除过期文档，时效滞后归零**；③ `*_delete` 表 id 集合差 + 每日 hash 对账兜底。
4. **PII 分界（决议定版）**：**岗位联系电话直接给用户（非隐私）**；求职者侧字段（`idcard/telephone/mobile/qq/weixin/家庭住址`）与简历/投递/登记/走访表**零同步**；本期无单点登录，不与 `qs_members.uid` 绑定。
5. **区域匹配是检索要求**：分站专区内容同步且带区域属性；智能体优先匹配用户所在区域，无匹配时明确告知并推荐邻近地区。
6. **变更捕获不依赖 binlog**：lyjy 靠 `refreshtime`（有 `audit_refreshtime` 复合索引）+ 21 张 `*_delete` 软删除表 + 状态位；sccy 千级每日全量。内容表无更新时间列 → content_hash 对账兜底（微信管线现成模式）。

---

## 2. 客户库侧事实（变更捕获能力核查）

### 2.1 时间戳列分布（决定增量游标可用性）

| 表 | 时间列 | 增量游标 | 索引支持 |
|---|---|---|---|
| `qs_jobs` | addtime, **refreshtime**, stime | refreshtime | ✅ `audit_refreshtime(audit, refreshtime)` 复合索引 |
| `qs_company_profile` | addtime, refreshtime | refreshtime | — |
| `qs_article` / `qs_guide` / `qs_notice` / `qs_explain` | 仅 addtime | ❌ 只能做"新增"游标 | — |
| `qs_category_jobs/district/major` | **无任何时间列** | ❌ 只能全量比对 | — |
| sccy 全部 `zc_*` 表 | 仅 dateline（创建时间） | ❌ 同上 | — |

**证据**（2026-11-11 只读探查）：`information_schema.statistics` 显示 `qs_jobs` 有 `(audit, refreshtime)` 复合索引——聊易平台自身的"职位刷新排名"就按它做增量，直接沿用最稳。

### 2.2 删除语义：两库模式完全不同

- **gt_lyjy = 移入软删除表**：21 张 `*_delete` 表（`qs_jobs_delete` 等），删除 = 主表行移到 _delete 表（全列快照，**无删除时间列**，检测只能做 id 集合比对）。
- **gt_sccy = 状态位**：`zc_article.closed/audit`（audit：1 发布，2 待审）、`zc_article_cate.hidden`。删除与下架都是状态位翻转。

### 2.3 可见性状态 ≠ 删除（关键设计红线）

`qs_jobs.audit`（0未审/1通过/2未通过）、`display`、`qs_guide.is_display` 等是**可见性状态**：审核中、下架、待审的岗位/文章仍存在于源库，随时可能恢复。同步侧对应物是 **KB 文档软删/恢复（宏陶同款：下架立即软删、恢复走 restore，不物理删）**。否则一次"审核打回"就会造成误删、恢复后又查不到的历史脏数据。按用户决议（2026-11-17），**源侧一切软删/下架/未审核记录不进同步管线**。

### 2.4 生产规模量级（按自增值推算；仅容量参考，不等于同步范围）

当前连的是测试库（表内仅 5~10 行测试数据），但自增值保留历史水位。**同步范围以 §7.5 映射表为准——本节列出大表只为说明库的容量级与同步负载预估**：

| 表 | AUTO_INCREMENT | 推断生产量级 | 同步与否 |
|---|---|---|---|
| `qs_members` | 329,783 | 会员 30 万级 | ❌ 用户账号信息，不同步（决议 2/4） |
| `qs_resume` | 280,600 | 简历 10 万级 | ❌ 求职者简历（隐私），零同步 |
| `qs_jobs` | 48,831 | 在招+历史岗位数万级 | ✅ 同步（映射表 #1）——**同步负载的决定表** |
| sccy `zc_article` | 999 | 千级 | ✅ 同步（映射表 #6） |

同步设计按 lyjy 岗位数万级、sccy 千级设计；每日增量按"活跃岗位日刷新一轮"估算在数千行/天，轮询完全够用。会员/简历虽是库里最大的表，但零同步，与同步负载无关。

### 2.5 敏感字段与实测状态坑

- `qs_resume.idcard`：注释"身份证号(加密)"——源库已加密存储；求职者侧字段零同步，无需解密能力。
- **实测坑①**：测试库 1436 行岗位 `close_type=2`（到期自动关闭）的 `display` 仍是 1，1495/1500 行已过期滞留主表——**到期关闭不修改状态位，只盯状态位必然漏检过期岗位**。对策见 §7.4（expires_at + 到期扫描双保险）。
- **实测坑②**：`deadline=0` 与极大值并存（语义待客户确认，推定 0=长期招聘）。
- `qs_notice` / `qs_explain` 的 `type_id` 分类字段在源库无分类数据（分类表空），实际无子分类。

---

## 3. 本系统侧事实（能力边界）

| 能力 | 现状 | 对本方案的意义 |
|---|---|---|
| 知识库存储与检索 | PostgreSQL + pgvector（1024 维，通义 text-embedding-v3），HybridRetriever 向量 0.7 + PG FTS 0.3 + RRF（`src/knowledge/retriever/hybrid_retriever.py`） | 全部同步内容复用；租户隔离现成 |
| **文档有效期** | `documents.expires_at` 已存在；检索可见性条件（`tenant_range.py build_active_document_condition`）= active + 未过期 + 未禁用 | **岗位 deadline → expires_at，过期文档检索自动排除，无需等同步任务**（决议 1 落地点） |
| 知识库增量更新 | 主链路无：`upload_document`（`src/knowledge/service.py` L570）恒新建；删除 `delete_document`（L798）单事务清理 | 同步管线自建幂等：微信管线（`src/wechat_mp/service.py` L2011-2115：content_hash 相同跳过、不同删旧重建）是现成模板 |
| documents 元数据 | 已有 `origin`、`external_id`、`metadata`(JSONB)、`status`（软删）、`disabled`、`expires_at` | `origin='<module>'` + `external_id=源表主键` 幂等与溯源，无需改表 |
| 外部关系库接入 | 数据分析智能体先例：`data_connectors` 表（密码 Fernet 加密）+ `DatabaseConnector`（SQLAlchemy/PyMySQL 短连接，`src/services/data_analysis/db_connector.py`） | 连接管理与凭据存储模式直接复用 |
| 定时任务 | APScheduler `ScheduledTaskManager`（`src/scheduler/manager.py`，Redis 分布式锁，系统 job 注册于 `_register_system_jobs()`） | 同步 Worker 注册为系统级 job |
| schema 演进 | `deploy/init-postgres.sql` 全量 + `deploy/db_update.yaml` 增量块 | `sync_state` 水位列走 db_update.yaml（§7.4） |
| 知识库挂载 | 工具 `knowledge_base_search`；智能体绑定 `subagent_knowledge_sources`（栏目授权硬隔离） | 一期无新增查询工具，只做栏目绑定 |
| 未实现（相关） | 文档级权限、Rerank、主库 content_hash（quick-wins 建议 2） | hash 幂等随本任务顺路补 |

---

## 4. 变更捕获矩阵（新增/更新/删除逐表机制）

| 数据 | 新增 | 更新 | 删除/作废 | 对账兜底 |
|---|---|---|---|---|
| 岗位（qs_jobs） | `refreshtime > 水位` 且 `audit=1 AND display=1`（源侧软删/下架/未审核**不拉取**） | 同左 + 逐行 content_hash 三态（同跳过/异删旧重建） | ① 目录状态比对（下架→KB 软删、恢复→restore）；② `_delete` 表 id 集合差→软删；③ **deadline→KB `expires_at`，检索层自动排除**，过期扫描仅做账本清理 | 每日全量 id+hash 比对 |
| 文章（qs_guide/article/notice/explain） | `addtime > 水位` | **无更新时间列** → 每日全量 hash 比对，变化则 KB 删旧重建（微信管线模式） | `is_display=0`/`audit=2` → 目录比对软删；源消失两击软删 | 每日对账即更新检测主手段 |
| sccy 文章（zc_article） | — 统一策略 — | **每日全量拉取 + hash 比对**（千级） | `closed≠0` / `audit≠1` / 分类 `hidden=1` → 软删 | 全量即对账 |
| 分类字典（qs_category_jobs/district 等） | 全量比对 | 全量比对 | 全量比对 | 本身即全量 |
| KB 侧删除动作 | — | — | 优先 `status='deleted'` / `disabled=1`（保留审计与回滚），不物理删 | — |

**两个开工前抽样验证项**：① lyjy 编辑是否必更新 `refreshtime`（若否，靠每日 hash 对账兜底，滞后 ≤24h）；② 删除是否 100% 走 `*_delete` 表（抽样核对主表无行 + _delete 表有行；有硬删旁路则每日 id 集合对账兜底，机制已含）。

---

## 5. 同步频率（按业务时效 × 源库写入模式）

| 任务 | 频率 | 依据 |
|---|---|---|
| lyjy 岗位/文章同步 run（目录比对+删除+到期+增量四合一，见 §7.4） | **15~30 分钟**（租户可配） | 求职者咨询"最新岗位"容忍 30 分钟内；量级数千行/天，轮询无压力 |
| lyjy 每日对账 run | 每日一次低峰 | 兜底旁路变更（客户直改库、编辑不刷 refreshtime） |
| sccy 全量同步 | 每日一次 | 千级全量分钟内完成，简单压倒聪明 |
| 分类字典全量比对 | 随 run 现拉缓存 | 几乎不变；量小 |

原则：**频率由"用户可感知的时效容忍 + 源库有没有增量游标"共同决定**；凡无游标的表不硬造频率，直接全量对账。

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| refreshtime 漏更新（编辑不刷新场景） | 岗位内容滞后 ≤24h | 每日 hash 对账；与客户确认写入语义 |
| _delete 表旁路硬删 | KB 残留已删数据 | 每日 id 集合对账兜底（已设计） |
| KB 无文档级权限 | 敏感内容一旦入库即扩大暴露面 | 求职者侧数据零同步（红线）；入库前管线做手机号/身份证正则扫描拦截 |
| 过期岗位误推荐 | 用户拿到失效岗位 | **expires_at 检索层归零滞后**（§7.4）+ 到期扫描清理 |
| 客户库压力 | 轮询影响客户业务 | 只读账号 + 低峰对账 + 查询全部走索引；增量窗口行数上限保护 |

---

## 7. 现行方案：宏陶数据源模式（2026-11-17 用户决议定稿）

### 7.1 用户决议（两轮合并）

第一轮（方案方向）：

1. 模仿宏陶 API 模式：做成数据源，租户侧选择开通、配置同步频率；
2. 隐私数据不同步；
3. 同步"能回答用户问题"的数据到知识库：开放职位、政策类信息；
4. 本期无单点登录：用户在对话中自述自身情况，智能体根据知识库回答。

第二轮（细化拍板）：

5. 数据有效期用知识库 `expires_at` 承接；
6. 源侧软删除记录一律不同步；
7. 知识库只保留大分类；分站专区有区域意义——**必须考虑咨询用户所在区域，优先匹配本地区域，无匹配时告知并推荐邻近城市**；
8. 岗位联系电话直接给用户，不是隐私；
9. KB 栏目按本研究规划定名；
10. sccy 本期一起交付，单独一个「创业政策」分类；
11. 渠道为 iframe 嵌入客户官网（同事开发中）。

### 7.2 宏陶模式盘点与复用映射

宏陶已交付的骨架（[设计文档](../system/hongtao-shop/hongtao-shop-kb-design.md) v1.10）正是"数据源 × 租户授权 × 频率配置"的完整实现，直接复用：

| 宏陶既有件 | 复用方式 |
|---|---|
| `src/services/content_sync/` 五张通用表（sources/runs/items/records/图缓存） | 原样复用；**两个 module**：`lyjy_employment`（聊易就业）+ `sccy_policy`（水城创业网），本期一起交付；增量水位见 §7.4 一处通用扩展 |
| `src/tenant_custom/<module>/` 模块结构（fetcher/renderer/scheduler/api/service） | 落位 `src/tenant_custom/lyjy_employment/` 与 `src/tenant_custom/sccy_policy/`，零私有表、平台层只碰三个挂载点 |
| portal 企业管理「数据源」页签开通/停用（源行即授权记录） | 同款，新增两个数据源类型；开通表单增加**数据库连接配置**（两库同一 MySQL 实例，各配 database） |
| `GET /api/saas/connection-sources` 菜单门控 | 同款，连接中心出现「就业数据同步」「创业政策同步」入口 |
| 租户后台：enabled / sync_interval_hours / 立即运行 / selection_mode+selected_ids | 同款；selected_ids 复用为**同步范围开关**（按 §7.5 映射表行，默认全选） |
| content_hash 三态幂等（跳过/重建/新建，origin+external_id upsert） | 同款：`origin='lyjy_employment'`、`external_id='job:{id}'`/`'guide:{id}'`/`'article:{id}'`…；sccy 同构 |
| 对账三路径（下架立即软删+restore；消失两击软删；用户删除自愈） | 同款；源侧信号 = 状态位 + `*_delete` 表 id 集合差 |
| 调度：通用按需加载 scheduler、Redis 锁、余额预检、runs/items 账本 | 原样复用 |
| embedding 计费走通用 `_record_knowledge_embedding_billing` | 同款；无 VL（内容均无图），费用结构比宏陶更简单 |

### 7.3 与宏陶的差异点及应对

| 维度 | 宏陶 | 就业/创业数据源 | 应对 |
|---|---|---|---|
| 源类型 | 无鉴权 HTTP GET（域名白名单硬编码） | **MySQL 只读账号**（网络 + 凭据） | portal 开通表单增加连接配置（host/port/database/只读账号），Fernet 加密存 source 行（`data_connectors.password_encrypted` 同模式）；平台管理员填写，租户不可见 |
| 数据量 | 674 条全量分页 | lyjy 岗位数万级 + 文章数千；sccy 千级 | **「目录全量 + 内容增量」双层 run**（§7.4）；sccy 每日全量 |
| 删除信号 | status≠1 下架 / 源消失两击 | `*_delete` 表 id 集合（lyjy）+ audit/display/closed 状态位 | 目录比对→立即软删+restore；id 集合差→当轮软删；两击软删兜底 |
| 挑选粒度 | 逐产品勾选 | 数万岗位逐条勾选无意义 | selection_mode 固定 'all'，selected_ids = §7.5 映射表行开关 |
| 文档有效期 | 无 | 岗位 `deadline` | **写入 KB `documents.expires_at`**，检索层自动排除（§7.4） |
| 区域属性 | 无 | 分站专区（彭水/潍坊等）+ 岗位地区 | 正文与 metadata 带区域；会话按用户区域优先匹配（§7.6） |
| 用户身份 | 不涉及 | 无单点登录 | 不与 `qs_members.uid` 绑定；用户自述 + iframe 参数提供区域（§7.6） |

### 7.4 同步 run 结构与作废/更新时效

lyjy 数据量大，采用**「目录全量 + 内容增量」双层结构**——状态与作废走每轮全量轻比对（保证及时），正文内容走水位增量（节省 embedding 费用）。对 content_sync 通用层的唯一扩展：`bs_content_sync_sources` 加 **`sync_state JSONB`** 列（模块自管水位：last_refreshtime/last_id、已处理删除 id 集合、上次对账时间）。

**每轮常规 run（频率=租户配置，建议 15~30 分钟）四步**：

| 步 | 动作 | 查询 | 成本 |
|---|---|---|---|
| ① 目录全量 | 轻量拉全量 `id + audit + display + close_type + deadline + refreshtime`（数万行秒级），与 records 逐条比状态：下架→立即软删、恢复→restore | 全表单次 | 低；宏陶"全量目录+状态比对"同款语义 |
| ② 删除事件 | 拉 `*_delete` 表全量 **id 集合**（单列），与 sync_state 已处理集合做差，差集→软删 | 单列全量 | 低（_delete 表无删除时间列，只能集合比对） |
| ③ 到期扫描 | `WHERE audit=1 AND display=1 AND deadline>0 AND deadline<now()` 走 `audit_deadline` 索引 → 软删清理账本 | 索引扫描 | 低。实测依据见 §2.5 坑① |
| ④ 内容增量 | `refreshtime > 水位` 且状态有效 → 拉正文 → content_hash 三态 | 索引增量 | 仅变更条目付 embedding |

**expires_at 双保险（决议 5）**：岗位 `deadline>0` 时同步管线直接写入 KB 文档 `documents.expires_at=deadline`——**即使同步轮询有滞后，检索可见性条件（未过期）也让过期岗位立即从所有检索结果消失，滞后归零**；③ 的到期扫描退化为账本清理角色。`deadline=0` 按长期处理不设 expires_at（语义待客户确认，§7.8）。

**每日对账 run（兜底，低峰）**：全量正文 hash 比对，修复增量漏（"编辑不刷 refreshtime"等旁路）；fetch_complete 门禁与两击软删沿用宏陶语义。水位只在 run 终态成功后推进，失败重试自然重放。

**时效保证（最大滞后上界）**：

| 变更类型 | 检测机制 | 滞后上界 |
|---|---|---|
| 岗位下架（audit→2 / display→0 / 手动关闭） | ① 目录状态比对 | = run 频率（15~30 分钟） |
| **岗位到期（deadline 过期）** | **expires_at → 检索层即时排除**；③ 仅清理账本 | **归零（对用户可见层面）** |
| 岗位/文章删除（_delete 表） | ② id 集合差 | = run 频率 |
| 新增 / 内容更新 | ④ refreshtime 增量 + hash | = run 频率；旁路变更 ≤24h（对账兜底） |
| 用户在知识库界面删除 | user_deleted 自愈（宏陶同款） | 即时（下轮同步不再重建） |

文章类（qs_guide/article/notice/explain）无 refreshtime：④ 退化为 addtime 增量（只覆盖新增），更新靠每日对账（滞后 ≤24h，政策咨询可接受）；①②③ 对文章表同样执行（is_display/audit 状态位 + 消失两击软删）。sccy（zc_article）每日全量，全量即对账。

### 7.5 同步范围与「源表 → 知识库分类」映射（决议 6/7/8/9/10 落地）

**分类对齐原则**：源库用「一张表 = 一类数据」组织内容，知识库分类与源表一一对应，不交叉混挂；**只建大分类**（决议 7）。源表内部的业务子分类与分站专区**写进正文「分类/地区」行**（进向量可检索、可引用），不在知识库建子分类树。

**一期同步映射总表（已按决议定版，可核对）**：

| # | 源表 | 源侧过滤条件 | → KB 分类 | 知识粒度 | 正文附加信息 |
|---|---|---|---|---|---|
| 1 | `qs_jobs` | `audit=1 AND display=1 AND (close_type IS NULL OR close_type=0)`；软删/下架/未审核一律不进管线 | **在招职位** | 一个岗位 = 一条知识 | 职位类别、工作地区、薪资、联系方式（决议 8）、企业简介；`deadline>0` → 文档 `expires_at` |
| 2 | `qs_guide` | `audit=1 AND is_display=1` | **就业指导** | 一篇文章 = 一条知识 | 分类：7 类（活动动态/生涯指南/云探岗/专家团队/云课堂/职业云体验/定制式服务） |
| 3 | `qs_article` | `audit=1 AND is_display=1`，**含分站专区（决议 7）** | **政策资讯** | 一篇文章 = 一条知识 | 分类：树形分类路径；**地区：分站名**（彭水/潍坊等专区与 subsite_id 映射） |
| 4 | `qs_notice` | `is_display=1` | **通知公告** | 一篇 = 一条 | 无子分类 |
| 5 | `qs_explain` | `is_display=1` | **办事指南** | 一篇 = 一条 | 无子分类 |
| 6 | `zc_article`（sccy） | `audit=1 AND closed=0` 且分类未隐藏 | **创业政策**（决议 10，独立分类） | 一篇文章 = 一条知识 | 分类：zc_article_cate 路径；来源标注"水城创业网" |

- **不同步**：`qs_help`（空表）、求职者简历/投递/登记/走访类全部（决议 2/4）、`qs_company_profile` 不独立成档（简介并入岗位正文）。
- **栏目名**：上表六个分类照宏陶模式按 display_name 查找、查无则建顶级分类，文档按表路由；`selected_ids` 开关 = 上表行号（默认全选）。
- **智能体授权**：`subagent_knowledge_sources` 按栏目绑定——咨询智能体绑定全部六个分类，栏目授权硬隔离保证不串其他租户知识。

**PII 分界（决议 8 修订版；fetcher 只 SELECT 白名单列）**：

- **同步并可给用户**：岗位联系方式（`qs_jobs_contact` 联系人/电话——用户拍板非隐私，进正文「联系方式」行）；
- **即弃（列白名单外不进管线）**：求职者侧 `idcard`、`telephone`、`mobile`、`qq/weixin`、`householdaddress`，及简历/投递/登记/走访全部字段；
- 白名单机制保证源库将来加列也不会泄露（列名白名单，不是黑名单）；入库前正则扫描兜底（拦截文章正文里偶发的手机号/身份证号）。

**分类字典**：`qs_category_jobs/district`（lyjy）、`zc_article_cate`（sccy）的 ID→名称映射由 fetcher 每 run 现拉缓存（约 1.1 万行，秒级），用于渲染正文；不单独建知识文档。

**职位正文模板**（宏陶 v1.7 结构化字段区同款，稳定内容进向量）：

```markdown
# {jobs_name}（{companyname}）

## 职位信息
- 职位类别：{category_cn} / {subclass_cn}
- 工作地区：{district_cn}
- 薪资：{minwage}-{maxwage} 元/月（negotiable 时写"面议"）
- 学历/经验要求：{education_cn} / {experience_cn}
- 招聘人数：{amount}
- 福利待遇：{tag_cn}
- 岗位职责：{contents 纯文本化}
- 其他要求：{jobAsk、性别/年龄/语言/证件等非空项}
- 特殊群体：优先招用重点群体 / 退役军人 / 残疾人（有则写）
- 联系方式：{contact 人 / 电话（qs_jobs_contact，决议 8）}

## 企业信息
- {company_profile 简介 200 字内（无则省略整节）}

---
分类：{category_cn}；数据来源：聊易就业平台。
```

文章正文模板（qs_guide / qs_article / qs_notice / qs_explain / zc_article 共用）：

```markdown
# {title}

分类：{子分类路径}
地区：{分站名（有分站属性时写，决议 7）}
来源：{author/source（有则写）}

{content/details 纯文本化（去 HTML 标签，保留段落）}

---
数据来源：聊易就业平台 / 水城创业网。
```

metadata.raw_payload 放：源表名、源主键、分类 ID/名、**地区/subsite**、薪资数值（仅岗位）、addtime/refreshtime、trace——与宏陶 v1.9 契约一致。**hash=pipeline_version+正文 sha256 同宏陶口径；正文不含 deadline/close_type 等每轮变化的时效字段（时效归 §7.4 状态机 + expires_at 管），不含同步日期**。

### 7.6 无身份会话、区域匹配与渠道（决议 4/7/11）

- **渠道**：iframe 嵌入客户官网（同事开发中）；官网页面可带区域参数（如 `?city=xx`）注入会话上下文——P3 与其对接参数约定。用户区域两个来源：**iframe 参数优先，对话自述兜底**。
- **不绑定身份**：仅拿渠道匿名会话标识做会话隔离，不与 `qs_members.uid` 关联。
- **区域匹配回答策略**（决议 7）：system prompt 固定指导——① 检索词携带用户区域；② 命中后按 metadata 地区字段优先本地区域排序；③ **本地区域无匹配时，必须明确告知"该地区暂无匹配"，再给出邻近城市/全市结果**，不得沉默降级。
- **回答链路**：用户自述（经验/地域/工种意向）→ 意图归一化（分类字典辅助）→ `knowledge_base_search`（栏目限定）→ 命中文档 metadata 补数值 → 区域优先过滤排序 → 组织回答；政策/指导类直接检索对应分类。
- **画像回流降级**：原「职业意向/技能短板回流画像表」降级为匿名会话级意向记录（不挂自然人），是否一期保留另出设计时再定，本同步方案不涉及。

### 7.7 实施计划

| 阶段 | 内容 | 验收要点 |
|---|---|---|
| **P1 管线** | `src/tenant_custom/lyjy_employment/` + `src/tenant_custom/sccy_policy/`：fetcher（只读账号 + 列白名单 + 软删过滤 + 分类字典/分站映射缓存）、renderer（职位/文章模板）、幂等落库（origin/external_id/content_hash 三态 + **expires_at**）、删除流（目录状态 + `_delete` id 集合差）、CLI 触发、`sync_state` 通用列（db_update.yaml） | 两库冷启动全量入库且分类路由正确（六分类逐一对账）；求职者 PII 在 documents/chunks/metadata 三处零出现（断言脚本）；岗位联系电话出现在正文（决议 8 抽查）；重跑 hash 未变零 embedding 费；`*_delete` 插行→文档软删；deadline 过期岗位检索不出现（expires_at 生效）；"叉车/跟车"口语查询命中岗位 |
| **P2 调度与后台** | 定时调度（增量 run + 每日对账 run）、portal 开通表单（两 module 连接配置 Fernet 加密）、租户后台（频率/范围开关/立即运行/账本）、余额预检 | portal 开通→连接中心两个数据源可见；改频率/关范围生效；立即运行出账本；停用→同事务取消在队 run |
| **P3 智能体接入验收** | 六分类授权给就业咨询子智能体、**iframe 官网嵌入对接**（区域参数约定）、区域匹配策略验收（本地区域优先/无匹配告知）、转人工路由（`qs_consultant` 需客户先维护数据）、agent2 验收 → 正式部署 | 栏目授权硬隔离生效；真机问答抽测（职位推荐含区域话术/政策问答/自述匹配）；只升级服务端不影响既有客户功能 |

每阶段按 dev-workflow 三智能体流程；P1 触及 database.py/background_runner 挂载点（`sync_state` 列 + 调度注册），走完整流程。

**冷启动费用量级**：活跃岗位 1~3 万条 × 正文约 400 token + 文章数千条，合计数百万~千万 token 级 embedding，按通义现行定价为小额一次性成本（数十元级）；日常增量仅变更条目重嵌。无 VL 费用。

### 7.8 待确认问题（合并去重后）

1. lyjy 编辑岗位是否必更新 `refreshtime`（抽样可验；影响更新时效精度，机制已兜底）。
2. 删除是否 100% 走 `*_delete` 表（抽样可验；有硬删旁路已由每日对账兜底）。
3. 生产库网络可达性与 IP 白名单流程（本次验证的是测试库出口）。
4. **岗位状态字段语义**：`close_type` 的 0/NULL 是否表示"在招中"（测试库只有 1/2 值）；`deadline=0` 是否表示"长期招聘"。决定 §7.4/§7.5 过滤条件写法。
5. **分站区域映射**：`subsite_id` → 地区名的对应表（qs_subsite 测试库为空，需客户提供生产数据或按文章分类树专区名映射）。
6. 租户模型：该客户是本平台单租户部署还是共享多租户之一（影响开通与授权操作方式，非 schema）。
7. `qs_consultant` 空表由谁维护（转人工依赖它）。

### 7.9 已知取舍（登记备查）

- **数值筛选精度**："薪资 5000 以上、某某镇附近"类过滤由 LLM 读 metadata.raw_payload 判断，弱于 SQL 精确过滤。缓解：检索 TopN 充足 + 正文含薪资/地区字段 + 栏目限定；试运行发现不足时再评估增强（本方案不做 SQL 路线）。
- **文章更新滞后 ≤24h**：文章类无更新时间列，对账是更新检测主手段——政策咨询场景可接受。
- **易变字段全链路排除**（宏陶先例）：refreshtime/浏览量等易变字段不进正文不入 metadata；时效状态归 §7.4 状态机 + expires_at 管。