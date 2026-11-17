# 就业咨询智能体客户数据库同步实施计划

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 同步管线（lyjy_employment + sccy_policy 两模块，幂等落库 + 删除流 + expires_at） | ✅ 完成（2026-11-17） | commit 572ff7eb；39 隔离库单测 + 127 回归 + 启动安全全过；客户测试库只读冒烟通过（四步 SQL/真实渲染；实测富文本双重 HTML 编码已修）；CR 无 P0/P1 |
| Phase 2 | 调度与后台（定时 run、portal 数据源开通、租户后台） | ✅ 完成（2026-11-17） | commit 037c00c0；67 新功能 + 127 回归隔离库全绿（含时区修复）；前端 build 通过；CR 无 P0/P1（租户隔离/权限/事务原子性逐项核查）；平台挂载零改动（optional_modules 通用循环生效）。CR 登记：test_scheduler 一用例凌晨窗口脆弱性（P2 不阻断） |
| 真机验证 | 本地浏览器端到端（portal 开通→租户配置→冷启动→知识库） | ✅ 完成（2026-11-17） | agent2_1127 真机库 + 浏览器实走全流程：portal 开通两源（加密连接）→ 连接中心门控菜单出现 → 五分类范围页 → 立即同步三轮：run 2590 成功（95·严格语义正确过滤）/ 2591 放宽验证（岗位 1497+指导 58 入库，**PII 防线真实拦截 3 条身份证号岗位** pii_rejected）/ 2592 sccy 440 全量。合计落库 2090 文档六分类路由正确，计费逐 run 入账本（~110 万 tokens / 66 积分）。教程：[管理员手册](../guides/employment-sync-admin-guide.md) |
| Phase 3 | 配置与验收（无代码开发：管理员后台配置 + web_c 区域参数约定 + agent2 验收） | 📋 待执行 | 执行清单见下节；操作教程：[管理员手册](../guides/employment-sync-admin-guide.md) |

> - 日期：2026-11-17
> - 方案依据：[就业咨询智能体客户数据库同步方案研究](../research/employment-agent-db-sync-research.md)（§7 现行方案，用户两轮决议定稿）；[关联库表数据字典](../research/employment-agent-db-data-dictionary.md)
> - 复用骨架：[宏陶商城产品知识库同步设计](../system/hongtao-shop/hongtao-shop-kb-design.md)（`src/services/content_sync/` 通用层 + tenant_custom 模块结构）
> - 真机部署须用户当场明确指示；本计划不含部署授权。

## 范围

**做**：两个数据源 module（`lyjy_employment`：qs_jobs/qs_guide/qs_article/qs_notice/qs_explain → 在招职位/就业指导/政策资讯/通知公告/办事指南五分类；`sccy_policy`：zc_article → 创业政策分类）、目录全量+内容增量双层 run、`_delete` 表删除事件、岗位 deadline→KB `expires_at`、portal 开通（含连接配置）、租户后台（频率/范围开关/立即运行/账本）、Phase 3 配置与验收清单（见下）。

**不做**：求职者侧任何表（qs_members/qs_resume/投递/登记/走访）同步；镜像表与 SQL 查询工具；分站子分类树；转人工路由的开发（`qs_consultant` 由客户维护数据后走既有转人工能力）；VL 解析（内容无图）；**子智能体提示词与知识库绑定的开发**（管理员后台自行配置，2026-11-17 决议）；web_c 渠道区域参数注入的实现（归 web_c 渠道，本任务只出对接约定）。

## 前置条件与阻塞关系

| 项 | 阻塞 | 缓解 |
|---|---|---|
| ~~客户确认 `close_type`/`deadline` 语义~~ **已拍板（2026-11-17 用户决议）**：`close_type` 不过滤（display=1 即展示）、`deadline` 已过期不展示（到期扫描保留）；`deadline=0` 为长期招聘。已按此修正（commit 72cc7d03），真机恢复 1474 长期岗 | 已解决 | — |
| **`expires_at` 时区口径**：已修复（P2 随 commit 037c00c0 改 Asia/Shanghai 挂钟） | 已解决 | — |
| `subsite_id` → 地区名映射数据 | P1 分站文章的区域字段 | 兜底用 qs_article 分类树专区名（彭水/潍坊等），生产映射到手后仅改渲染输入 |
| `qs_guide/qs_article` 审核位语义（测试库实测 audit 全 0；若客户工作流"audit=0 即可见"需放宽 `audit=1` 过滤） | P1 文章类冷启动数据 | 常量集中一处；冒烟已实测 notice/explain 渲染正确 |
| 生产库网络白名单（agent2 / 生产出口 IP） | P3 真机验收 | P1/P2 全程用测试库（只读账号已就绪） |
| 客户维护 `qs_consultant` 数据 | P3 转人工验收 | 非转人工功能开发阻塞 |
| 记账缺口（commit 72cc7d03 登记）：同 run 内条目先记 skip 后被目录比对 restore 升级时，items 行因 ON CONFLICT DO NOTHING 不升级为 restore，`restored_count` 少记 | 无功能影响 | 后续小改：`_finish_item` restore 场景升级已存在行 |

## Phase 1：同步管线

### WP1.1 通用层扩展

1. `deploy/db_update.yaml` 增量块：`ALTER TABLE bs_content_sync_sources ADD COLUMN IF NOT EXISTS sync_state JSONB`（模块自管水位：last_refreshtime/last_id、已处理删除 id 集合、上次对账时间），`deploy/init-postgres.sql` 同步补列。平台层不解读该列内容，模块读写。
2. `src/services/content_sync/` 不改既有函数语义；如需新增通用辅助（如源行守卫复用），先核对宏陶/公众号用法，避免为单模块改通用契约。

### WP1.2 lyjy_employment 模块骨架（`src/tenant_custom/lyjy_employment/`）

1. `fetcher.py`：
   - SQLAlchemy 连接：连接配置取 source 行加密字段（Fernet 解密，`data_connectors` 同模式），短连接用完 dispose；
   - **列白名单**：每张同步表定义显式列清单，SELECT 只取白名单列；求职者侧 PII 列（idcard/telephone/mobile/qq/weixin/householdaddress）及 qs_resume/qs_members 等表不在清单内；
   - 四步拉取（研究 §7.4）：① 目录全量（`SELECT id, audit, display, close_type, deadline, refreshtime FROM qs_jobs`，文章表对应 `id, audit, is_display, addtime`）；② `qs_jobs_delete`/`qs_resume` 相关仅取 id 集合；③ 到期扫描（`audit_deadline` 索引）；④ `refreshtime > 水位` 增量正文（仅 `audit=1 AND display=1 AND (close_type IS NULL OR close_type=0)`）；
   - 字典缓存：qs_category_jobs / qs_category_district / qs_article_category / qs_guide_category 每 run 现拉内存缓存；qs_jobs_contact、qs_company_profile 按 jobs_id/company_id 批量 join（联系方式、企业简介 200 字截断）；
   - 软删/下架/未审核记录不进入④正文拉取；②的删除事件与①的下架状态负责已入库文档的软删。
2. `renderer.py`：
   - 职位模板、文章模板（研究 §7.5 定稿格式）；渲染输出含「分类/地区/联系方式」行与「数据来源」尾行；
   - 分站区域：文章按 qs_article_category 专区名或 subsite 映射写「地区：」行；岗位写「工作地区：{district_cn}」；
   - **PII 出站扫描**：渲染后对正文做手机号/身份证正则扫描（白名单例外：qs_jobs_contact 电话在职位模板的「联系方式」行内生成，扫描器按行放行该行）——发现求职者侧 PII 直接拒渲染并记 item 错误；
   - 分类路由：按源表返回目标分类 display_name（六分类常量表）。
3. `service.py`：run 编排与幂等落库
   - 落库照微信管线 `_persist_document_tx` 模式模块内自写：`documents (tenant_id, origin, external_id)` 部分唯一索引 upsert；content_hash = pipeline_version + 正文 sha256 三态（未变跳过 + metadata「先比对后写」轻量更新；变更删旧 chunks/vec 重建；新建全链路）；
   - **expires_at**：岗位 `deadline>0` 时写 `documents.expires_at=deadline`（UTC datetime）；deadline=0 或空不写；
   - 删除/恢复：下架与删除事件 → `documents.status='deleted'`（软删）；重新上架 hash 未变走 restore（恢复 active 复用 chunks，零计费）、变了重建；
   - user_deleted 自愈：`bs_content_sync_records.doc_id` 有值而 documents 行不存在 → 置 `user_deleted` 跳过；hash 变化视为重新发布；
   - runs/items 账本与 records（payload 存目录展示字段 + last_seen_at 对账）照宏陶五表契约写入；embedding 计费调通用 `_record_knowledge_embedding_billing`（source_type=`lyjy_employment_embedding`）；
   - 分类自举：照宏陶「按 display_name 查找、查无则建普通分类」，六分类分属两个 module 各自自举。
4. `bootstrap.py` + CLI：`--init-source` 建源行（测试/部署兜底）、计费种子行种植（幂等）。

### WP1.3 sccy_policy 模块（`src/tenant_custom/sccy_policy/`）

1. `fetcher.py`：zc_article 每日全量（`audit=1 AND closed=0`，分类 hidden 过滤），zc_article_cate 映射缓存；
2. `renderer.py` / `service.py`：文章模板（来源标注"水城创业网"）、全量 hash 比对落库（同 WP1.2 机制，无水位）、→「创业政策」分类。

### WP1.4 Phase 1 测试与验收

1. 单测（`bash scripts/dev_test.sh tests/... -p no:cacheprovider -q`，隔离库）：
   - fetcher：软删/下架/未审核记录不进正文拉取；列白名单（源表多出的列不被 SELECT）；_delete id 集合差计算；到期扫描条件；
   - renderer：两套模板快照；PII 扫描拦截与联系方式行放行；分站地区行；
   - service：hash 三态（跳过不抖 updated_at / 重建 / 新建）、expires_at 写入、软删→restore、user_deleted 自愈、六分类路由、账本与计费种子；
   - sccy：全量比对、closed/hidden 过滤。
2. 断言脚本（验收用，一次性）：测试库冷启动后扫描 documents/chunks/metadata，求职者 PII 正则零命中；岗位联系方式抽样存在。
3. 手动验收：CLI 冷启动两库全量入库，六分类文档计数与源侧有效行数对账；重跑 hash 未变零 embedding 计费；`qs_jobs_delete` 插测试行 → 对应文档软删；deadline 过期岗位文档 `expires_at` 生效（检索不可见）；"叉车/跟车"口语查询命中在招职位分类。
4. 挂载点检查：database.py 建表块 / main.py 路由挂载（P2 才有 API）/ background_runner 调度注册（P2），P1 仅 db_update.yaml 一处通用层触碰。

## Phase 2：调度与后台

### WP2.1 定时调度

1. `configs/config.yaml` `tenant_custom_modules` 加 `["hongtao_shop", "lyjy_employment", "sccy_policy"]`（保留既有）；两模块 scheduler 照宏陶 `HongtaoIngestScheduler` 模式：30min tick 扫描到期源、Redis 单副本锁、Semaphore(4) 跨租户、stale 回收、余额预检（≤0 → run 终态 skipped_no_credit）；
2. run 类型：常规 run（四步，按租户 `sync_interval_hours`；lyjy 建议默认 0.5h）与每日对账 run（模块内固定凌晨触发，全量 hash 比对）；sccy 仅每日全量 run；
3. 触发统一：定时 tick、后台「立即运行」、CLI 同一 `trigger_sync()`。

### WP2.2 portal 数据源开通

1. 企业管理「数据源」页签新增两个类型（`TenantDataSources.vue` 扩展）；开通 = 建源行（幂等，默认 enabled/频率/全范围）+ 计费种子自举；停用 = 删源行 + 同事务取消在队 run（宏陶 revoke 同款，执行侧源行守卫兜底）；
2. 开通表单增加**数据库连接配置**：host/port/database/user/password——Fernet 加密存 source 行新增连接字段（或复用 `data_connectors` 行关联，开发时按改动最小定，倾向 source 行加密 JSON 字段）；平台管理员可见可填，租户不可见；
3. `GET /api/saas/connection-sources` 返回清单自然带上两个新 module（核对通用查询是否 module 硬编码，需扩展则改通用接口白名单常量）。

### WP2.3 租户后台

1. API（require_admin，照宏陶 `/api/saas/hongtao-shop` 前缀模式）：`GET/PATCH /api/saas/lyjy-employment/source`、`POST /source/trigger`、`GET /runs`、`GET /runs/{id}`；sccy_policy 同构；PATCH 字段 = enabled（自动/仅手动）、sync_interval_hours、selected_ids（§7.5 映射表行开关，默认全选）；
2. 前端（复用 Base* 组件与宏陶三卡页结构）：源设置卡（同步方式/频率/范围开关/立即运行）+ 运行账本列表；无逐条挑选 UI（范围=分类开关）；
3. 测试：调度到期触发、改频率/关范围生效、立即运行出账本、余额预检、revoke 取消在队 run、两 module 互不影响、多 worker 不双跑（Redis 锁）。

## Phase 3：配置与验收清单（无代码开发；2026-11-17 用户决议：子智能体提示词与知识库绑定由管理员后台自行定义，区域参数归 web_c 渠道能力）

按顺序执行，全部为后台配置 / 联调 / 验收操作：

1. **portal 开通（平台管理员）**：企业管理「数据源」页签开通「就业数据同步」（lyjy_employment）与「创业政策同步」（sccy_policy），表单录入客户库连接配置（host/port/database/只读账号），Fernet 加密落库；生产库需先完成 IP 白名单（§前置条件）。
2. **同步冷启动与对账检查（租户管理员或平台代跑）**：租户后台「立即同步」触发冷启动，运行账本核对：六分类文档计数与源侧有效行数一致；PII 零出现断言（验收脚本随 P1 已交付思路）；`qs_jobs_delete` 有新行时对应文档软删；到期岗位在检索不可见（expires_at 生效）。
3. **管理员后台配置**：
   - 六分类（在招职位/就业指导/政策资讯/通知公告/办事指南/创业政策）绑定就业咨询子智能体（`subagent_knowledge_sources` 后台操作；栏目授权硬隔离，其他子智能体默认检索不到）；
   - 子智能体提示词由管理员自行定义；区域匹配策略建议要点（供管理员参考，非平台强加）：检索词携带用户区域 → 命中后按地区优先本地排序 → 本地区域无匹配时明确告知并给邻近城市结果；
   - 转人工依赖 `qs_consultant` 数据，由客户运营维护。
4. **web_c 区域参数约定（对接同事的官网客服渠道，非本任务开发）**：iframe 嵌码 URL 带区域参数（建议 `?city=xx`），web_c 渠道将其注入会话上下文，管理员提示词引用该上下文即可实现"优先本地区域"；参数名与注入位置以 web_c 渠道交付为准。
5. **agent2 验收（真机）**：职位推荐（含区域话术：本地区域优先、无匹配告知+邻近推荐）、政策问答、就业指导、自述匹配（"三年叉车想找县城的活"）、口语查询命中（"跟车的活"）、过期岗位不再被推荐、转人工（依赖第 3 条客户数据，可后置）。
6. **兼容验证**：只升级服务端、既有客户（宏陶/公众号管线、BOSS CLI 等）回归不受影响——`tenant_custom_modules` 追加不改既有项、content_sync 通用层只增列不改语义、共享前端组件仅增量（P2 CR 已核查）。
7. **正式部署**：部署动作须用户当场明确指示；生产开通走 portal（替代插行）。

## 风险

| 风险 | 应对 |
|---|---|
| close_type/deadline 语义误判 | 条件集中常量化；P1 验收前拿到客户确认即可修正，无需返工结构 |
| refreshtime 不随编辑更新 | 每日对账兜底已含；时效滞后 ≤24h 登记为已知取舍 |
| qs_jobs 目录全量在生产达数十万行 | 只拉六列，秒级；若生产实测慢，改游标分页（fetch_complete 门禁保证两击软删不受影响） |
| 两 module 与既有 hongtao/wechat_mp 调度争抢 | 复用 Semaphore(4) 与 Redis 锁既有机制；账本观测 run 时长 |
| 冷启动 embedding 费用 | 数十元级（研究 §7.7 估算）；余额预检兜底 |

## 登记与流程

- 每阶段完成：先更新本计划「开发进度」，再同步 `docs/ideas.md` 条目状态；开发完成后移入 `docs/ideas_finished.md`。
- P1 触及通用层挂载点（db_update.yaml）与两个新模块，走完整三智能体流程；P2 触及 portal/前端，同流程；P3 验收为主。
