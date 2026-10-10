# 统一知识库工具替代 attraction/hotel_search——价格/项目表迁 metadata 开发计划

> 设计文档：[docs/system/knowledge-base/unified-kb-resource-table-metadata-design.md](../system/knowledge-base/unified-kb-resource-table-metadata-design.md)
> ideas 编号：20261010-1053（工具分区）

## 开发进度

| 阶段 | 内容 | 状态 | 完成记录 |
|------|------|------|---------|
| Phase 1 | 迁移脚本 + 单测（dry-run/execute/verify/幂等/32KB 告警） | ✅ 已完成 | 2026-10-10：脚本 + 37 单测 + 3 集成（真实库写路径乐观锁三方向）全过；真实库 dry-run 286 文档零冲突零超限。独立测试（3 P2）+ 独立 CR（2 P1 + 5 P2 + 复核发现 1 P0 乐观锁方向反写）全部修复并经 CR 终审「可合入」 |
| Phase 2 | 写读路径切 metadata（importer / PUT / PATCH / retrievers 兼容回退读）+ 单测 | ✅ 已完成 | 2026-10-10：kb_metadata 共享模块 + 两 importer/两 PUT/PATCH/retriever getter 全切换；独立测试（3 P1）+ 独立 CR（2 P1 + 2 P2）全部修复并终审可合入；工具回退正例、PUT 端点级、注入剥离、计费回归齐备；全量回归 2347 过（1 存量无关失败） |
| Phase 3 | 统一工具 cover_image 增强 + agent_events documents 扫描 + 测试 | 📋 待开发 | — |
| Phase 4 | 工具下线（删类 / 黄金清单 / SUBAGENT.md 改写 / 测试清理） | 📋 待开发 | — |
| Phase 5 | 真机迁移执行（--execute → verify → 观察 ≥1 周 → --drop-chunks，需用户部署授权） | 📋 待开发 | — |

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

- [ ] `knowledge_base_tool`：`documents_out[doc_id].cover_image`（顶层 `metadata.images.cover` → ImageRegistry 反解，失败 warning）
- [ ] `agent_events._extract_image_refs_from_tool_result`：增加 `result["documents"]`（dict）扫描
- [ ] 工具 `description` 补 raw_payload 业务数据说明
- [ ] 测试：test_agent_image_event_loop 补 documents 模式；test_knowledge_base_tool 补 cover_image

## Phase 4 — 工具下线与提示词

- [ ] 删除 `attraction_search_tool.py` / `hotel_search_tool.py`
- [ ] `test_tool_discovery.py` GOLDEN_TOOLS 移除两项
- [ ] `agent_events.py` 注释更新（cover_image 模式改为统一工具示例）
- [ ] `subagents/travel-consultant/SUBAGENT.md` 改写（设计 §4 D5 三处要点）
- [ ] 删除/改造 test_attraction_search_cover_image、test_travel_image_pipeline（统一工具路径）
- [ ] 全量单测回归

## Phase 5 — 真机迁移（需用户逐次明确授权）

1. 测试库演练：dry-run → execute → verify，报告留档；
2. 真机：dry-run 报告确认 → execute → verify；
3. Phase 2 代码上线后观察 ≥1 周（报价回归、聊天封面、Word 嵌图）；
4. `--drop-chunks` 清场 + verify 复查；回退读逻辑随后续清理任务移除；
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
