# 统一知识库工具替代 attraction/hotel_search——价格/项目表迁 metadata 开发计划

> 设计文档：[docs/system/knowledge-base/unified-kb-resource-table-metadata-design.md](../system/knowledge-base/unified-kb-resource-table-metadata-design.md)
> ideas 编号：20261010-1053（工具分区）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 迁移脚本 + 单测（dry-run/execute/verify/幂等/32KB 告警） | ✅ 已完成 | 2026-10-10：脚本 + 37 单测 + 3 集成（真实库写路径乐观锁三方向）全过；真实库 dry-run 286 文档零冲突零超限。独立测试（3 P2）+ 独立 CR（2 P1 + 5 P2 + 复核发现 1 P0 乐观锁方向反写）全部修复并经 CR 终审「可合入」 |
| Phase 2 | 写读路径切 metadata（importer / PUT / PATCH / retrievers 兼容回退读）+ 单测 | ✅ 已完成 | 2026-10-10：kb_metadata 共享模块 + 两 importer/两 PUT/PATCH/retriever getter 全切换；独立测试（3 P1）+ 独立 CR（2 P1 + 2 P2）全部修复并终审可合入；工具回退正例、PUT 端点级、注入剥离、计费回归齐备；全量回归 2347 过（1 存量无关失败） |
| Phase 3 | 统一工具 cover_image 增强 + agent_events documents 扫描 + 测试 | ✅ 已完成 | 2026-10-10：独立测试（0 P0-P2，3 P3）+ 独立 CR（可合入，1 P2 测试缺口）全部清零；封面反解 None/异常/脏数据三分支 + `_no_truncate` 共存断言齐备；相关 6 套件 79 过 |
| Phase 4 | 工具下线（删类 / 黄金清单 / SUBAGENT.md 改写 / 测试清理） | 🔧 4a ✅，4b 待观察期 | 2026-10-11：4a 软下线经独立测试（0 代码缺陷 + 实证迁移时序 P1）与独立 CR（可合入，P2×3 + nit 已修）双重验证；4b 删除待真机观察期 |
| Phase 5 | 真机迁移执行（--execute → verify → 观察 ≥1 周 → --drop-chunks，需用户部署授权） | 🔧 测试库演练完成 | 2026-10-11：agent2/3 共享测试库（本地 env 直连）--execute 286/286 零冲突 → verify 286 全一致（exit 0）→ 幂等重跑零变更；数据抽检表文本/镜像/封面全对 |

## Phase 1 — 迁移脚本

`scripts/migrate_travel_resource_tables_to_metadata.py`（设计 §4 D1）。✅ 2026-10-10 完成。

- [x] 主流程：扫 `hotel_resource` / `attraction_resource` → chunk 1/2 文本 + 顶层 metadata → 合成 `raw_payload` → UPDATE `documents.metadata`（单文档事务 + 原值乐观锁防并发丢更新）
- [x] 参数：`--env` / `--tenant-id` / `--dry-run`（默认）/ `--execute` / `--verify` / `--force` / `--drop-chunks`
- [x] 幂等：已有键且文本一致跳过；重跑零变更
- [x] 32KB 告警清单；汇总报告（扫描/迁移/跳过/冲突/超限/并发计数），报告基于脚本位置写 `log/temp/`
- [x] 单测：`tests/unit/test_migrate_travel_resource_tables.py`（37 例）+ 集成 `tests/integration/test_migrate_travel_resource_tables_db.py`（3 例，真实库乐观锁三方向含 NULL）
- [x] 验证加固（独立测试/CR 反馈修复）：verify 镜像漂移双向检查且 drop 后仍影响退出码；drop 前值一致性校验（防删较新 chunk）；异常中断报告留档；只读路径释放事务快照

## Phase 2 — 写读路径切换

✅ 2026-10-10 完成。实现细化见设计 §4 D2「Phase 2 实现细化」（共享模块 / 过渡期双写 / region 补顶层 / 空串语义）。

- [x] `import_hotel` / `import_attraction`：表文本写 `raw_payload`、业务键镜像、total_chunks=1、不再插 chunks 1/2
- [x] `PUT /kb/hotels|attractions/{doc_id}`：三个表字段改写 `raw_payload`；`metadata` 合并时同步镜像业务键（body 的 raw_payload/images/系统键/表键剥离，防 LLM 通道注入）
- [x] `PATCH /kb/attractions/{doc_id}/images`：顶层 `images` 与 `raw_payload.images` 同步（含全删分支镜像清理）
- [x] `HotelRetriever.get_price_table` / `AttractionRetriever.get_ticket_table/get_project_table`：优先 `raw_payload`，回退 chunk 1/2
- [x] 部署窗口保护：attraction/hotel_search 两工具取表加 raw_payload 回退（Phase 4 下线前新导入数据不缺表，设计 §5 风险 #7）
- [x] 单测：kb_metadata 合成/镜像/注入剥离；retriever 导入写路径与回退读边界；PATCH 双写/双清；PUT 端点级四方向；工具回退正例；导入计费回归

## Phase 3 — 统一工具图片增强

✅ 2026-10-10 完成。

- [x] `knowledge_base_tool`：`documents_out[doc_id].cover_image`（顶层 `metadata.images.cover` → ImageRegistry 反解；失败 warning、查无 None 静默不落键、非 str 脏数据前置拦截防重复 WARNING；无封面不落键；反解在连接释放后执行）
- [x] `agent_events._extract_image_refs_from_tool_result`：增加 `result["documents"]`（dict）扫描；对 agent_engine/runtime/legacy 三个消费点自动生效（提取 → placement 归一 → SSE images 事件，与 results 模式等价，独立测试读码确认）
- [x] 工具 `description` 补 raw_payload 业务数据（价格表/项目表/图片 file_id/嵌图语法）与 cover_image 说明
- [x] 测试：test_agent_image_event_loop 补 documents 模式；test_knowledge_base_tool 补 cover_image 5 用例（反解/无封面/异常/查无 None/脏数据 + `_no_truncate` 共存）

## Phase 4 — 工具下线与提示词

> 按用户指示拆两步：**4a 标记下线**（catalog=False 软禁用，代码与测试保留、可随时回滚）；**4b 稳定后删除**（真机观察期通过后删除两个工具文件与专属测试：test_attraction_search_cover_image、test_travel_search_tool_fallback、integration/test_hotel_search_tool 中专属部分，黄金清单注释同步清理）。

> **⚠️ 部署前置条件**：Phase 4a 上线前，目标环境必须已执行迁移脚本 `--execute`（raw_payload 已就位）——改写后的提示词依赖 `doc_metadata.raw_payload.project_table / price_table` 与 `documents[].cover_image`，数据未迁移则旧文档项目/价格表对 LLM 不可见。

### 4a 标记下线（2026-10-11，✅）

- [x] 两个工具类 `catalog = False`（退出 Catalog 自动发现，全部装配路径拿不到；回滚 = 去掉标记并恢复黄金清单两项）
- [x] `test_tool_discovery.py`：GOLDEN_TOOLS 移除两项、CATALOG_EXCLUDED_TOOLS 增加两项（含注释 + 头部沿革条目）
- [x] `subagents/travel-consultant/SUBAGENT.md` 改写（设计 §4 D5）：attraction_search → knowledge_base_search（「城市 景点」top_k=10）；title 白名单 → documents[].title；项目表 → doc_metadata.raw_payload.project_table（首处定义简称）；封面 → documents[].cover_image.file_id（备选 raw_payload.images.cover）；酒店直接问价 → raw_payload.price_table
- [x] `agent_events.py` / `knowledge_base_tool.py` 注释去工具专名
- [x] 装配安全性确认：DB `subagent_definitions.tools.allowed` 残留旧工具名走 retain_only 交集语义，无崩溃
- [x] 验证收尾修复（独立测试/CR 反馈）：白名单限定「景点：」前缀文档（防跨栏目混入）；搜索结果太少时回退纯城市名重搜（多词 query 的 FTS simple 分词通道失效，向量通道 148/148 在位为主力）；示例 title 补前缀；回滚注释补全；措辞统一
- [x] 运行时验证：discover_tool_classes 不含两工具（SUBAGENT/MASTER 两角色装配实测）；travel-consultant 解析正常、提示词零旧引用、新路径齐备

**验证记录**：独立测试（32+26+66 分层全过、真实 assemble_agent_tools 两角色实测、DB 残留名单无崩溃、12 处旧引用逐一等价核对；实证本地 dev 库未迁移 → 部署时序 P1 属环境非代码）+ 独立 CR（下线彻底性全域确认、契约一致性核对、可合入；P2×3 + nit 全部修复）。

### 4b 稳定后删除（真机观察期通过后）

- [ ] 删除 `attraction_search_tool.py` / `hotel_search_tool.py`（含过渡期取表回退）
- [ ] 删除/收编专属测试（cover_image 用例已由统一工具测试承接；fallback 测试随文件删除；hotel 集成测试专属部分删除）
- [ ] 黄金清单注释同步清理（CATALOG_EXCLUDED_TOOLS 移除两项）

## Phase 5 — 真机迁移（需用户逐次明确授权）

1. ✅ 测试库演练（2026-10-11，agent2/3 共享测试库——本地 env 直连，用户确认无独立本地库）：
   - dry-run：286 文档（148 景点/138 酒店）全部可迁移、零冲突零超限
   - `--execute`：286/286 已迁移、冲突 0、超限 0、并发 0
   - `--verify`：286 全一致（exit 0）；幂等重跑：将迁移 0 / 幂等跳过 286（零变更）
   - 数据抽检：景点 ticket/project 表文本 + category/category_cn/region/source_sheet 镜像齐全；酒店 price_table + address/sub_region/diamond_level 镜像齐全（旧数据顶层无 region 键故不镜像，新导入才有——镜像现实语义）；唯一带封面的景点（天空之桥 doc=1307）raw_payload.images 正确镜像
   - 报告留档：`log/temp/travel_metadata_migrate_agent23.log`
2. 真机：dry-run 报告确认 → execute → verify；
3. Phase 2 代码上线后观察 ≥1 周（报价回归、聊天封面、Word 嵌图）；
4. `--drop-chunks` 清场 + verify 复查；回退读逻辑随后续清理任务移除；
   - ✅ **测试库已提前清场**（2026-10-11，用户授权「排除旧数据干扰，强制 metadata 单源验证」）：
     备份 434 行表 chunk（景点 148×2 + 酒店 138，`log/temp/chunks_backup_agent23_20261011.json`）→
     `--drop-chunks --execute` 286/286 已删除、拒绝 0 → `--verify` exit 0；残留表 chunk 行 0、
     total_chunks 全部 2/3→1、chunk 0（摘要+向量）286 完好、payload 表键覆盖 138/138 + 148/148；
     真实 retriever 无 chunks 可回退时取表正常（酒店 81 字价格表 / 景点 192+1043 字门票项目表）。
     ⚠️ 影响：agent2/3 服务端在部署 Phase 2-4a 代码前，旧代码取表断供——需尽快发布。
5. **收尾清理**：真机全量迁移 + 清场 + verify 复查通过后，Phase 1 的一次性产物——
   `scripts/migrate_travel_resource_tables_to_metadata.py`、
   `tests/unit/test_migrate_travel_resource_tables.py`、
   `tests/integration/test_migrate_travel_resource_tables_db.py`——即完成使命，
   整体删除，不留长期维护负担（脚本头部 docstring 已同步标注）。

## 风险与回退

- 迁移只增改 `documents.metadata`（保留 chunks），任意时刻可重跑 verify 核对；回退即恢复读取 chunks 路径（Phase 2 回退读天然支持）。
- `--drop-chunks` 为不可逆动作（chunks 1/2 删除），前置条件：verify 通过 + 观察期无回归 + 用户明确授权；执行前对两类资源行数与文本做导出备份。
- **部署顺序**：Phase 2 先于 Phase 4 上线时，attraction/hotel_search 两工具已加 raw_payload 取表回退（见设计 §5 风险 #7），新导入文档不缺表；Phase 3/4 可独立节奏发布。
- **注入防护**：PUT body 的 metadata 剥离 raw_payload/images/系统键/表键（设计 §5 风险 #8），表数据只能经表字段写入。
