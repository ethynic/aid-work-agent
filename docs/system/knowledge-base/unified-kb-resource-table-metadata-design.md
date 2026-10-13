# 统一知识库工具替代 attraction/hotel_search——价格/项目表迁入文档 metadata 设计

> ideas 编号：20261010-1053（工具分区）
> 开发计划：[docs/plans/plan-unified-kb-resource-metadata.md](../../../docs/plans/plan-unified-kb-resource-metadata.md)
> 前置结论：工具总体优化 Phase 7 收口时决定两工具不再单独优化，另立本任务

## 1. 背景与目标

`attraction_search` / `hotel_search` 是旅游资源的两个专用检索工具，靠**固定 chunk 位次**（酒店 chunk_index=1 价格表、景点 chunk_index=1/2 门票/项目表）在检索后回表取数据。这一做法不通用：新资源类型都得再造一个工具、再占两个 chunk 位。统一工具 `knowledge_base_search` 已具备混合检索、租户/共享范围、栏目授权与文档级归并输出，且 `doc_metadata` 白名单透出 `raw_payload`——业务数据只要进 `raw_payload`，统一工具默认就会带给 LLM，无需改工具代码（宏陶 renderer v1.9 已验证该契约）。

**目标**：

1. 价格/门票/项目表迁入 `documents.metadata.raw_payload`，统一工具检索即默认透出；
2. 下线两个专用工具，`travel-consultant` 子智能体提示词改用 `knowledge_base_search`；
3. 写入/读取路径全部切到 metadata 单一数据源，最终删除 chunks 1/2；
4. 图片链路（Word 嵌图 `file_id` 引用、聊天封面图事件）不回归。

**非目标**：

- 不改 HybridRetriever 检索算法与阈值（中文 FTS 分词优化另行评估）；
- 不迁移 region/category 等顶层业务键的**存储位置**（仅镜像进 `raw_payload`，见 §3）；
- 不处理 uuid 导入 attractions/hotels 不带 chunks 的存量缺口（迁移后该缺口只剩 chunk 0 缺失，与本任务解耦）。

## 2. 现状盘点

### 2.1 数据模型

| source_type | chunks 布局 | documents.metadata 顶层键 | 写入方 |
|---|---|---|---|
| `hotel_resource` | 0=信息摘要（向量化+text_vec）；1=价格明细表（仅 text_vec） | region / sub_region / diamond_level / source_sheet 等 | `import_hotel`（Excel 导入）、`PUT /kb/hotels/{doc_id}` |
| `attraction_resource` | 0=摘要（向量化）；1=门票价格表；2=项目/服务价格表（1/2 仅 text_vec） | region / category / category_cn / source_sheet / images{cover,gallery} | `import_attraction`（Excel+zip 导入）、`PUT /kb/attractions/{doc_id}`、`PATCH .../images` |

- 入库：`src/skills/travel-quote/scripts/hotel_retriever.py::import_hotel`、`attraction_retriever.py::import_attraction`（chunks 1/2 不进 chunks_vec，text_vec 由触发器自动维护）。
- 编辑：`src/api/travel_quote.py` PUT 接口按 chunk 位次 UPDATE 文本；PATCH images 合并顶层 `images` 键。

### 2.2 两个专用工具

| 工具 | 检索策略 | 额外取数 | 特殊输出 |
|---|---|---|---|
| `hotel_search` | 名称 ILIKE 优先 → 向量兜底，限 chunk 0 | chunk 1 价格表 | region / diamond_level |
| `attraction_search` | 向量，限 chunk 0 | chunk 2 项目表 | region / category / **cover_image（ImageRegistry 反解 ImageRef）** |

### 2.3 统一工具 `knowledge_base_search`

HybridRetriever 向量（阈值 0.5）+ FTS（tsvector）+ 加权 RRF 融合；`documents` 按 doc_id 归并输出 title/file_path/summary/doc_metadata 等，`_parse_doc_metadata` **白名单只透出 `raw_payload`**（防内部溯源键泄漏给 LLM）；租户 + 子智能体共享范围 + 栏目授权收口。资源文档 chunk 0 已向量化，统一工具本就能检索到，唯独价格/项目表（不向量化、也不在 doc_metadata）拿不到。

### 2.4 消费方清单（chunks 1/2 与两工具的全部引用）

| 消费方 | 依赖 | 本任务处理 |
|---|---|---|
| `src/tools/knowledge/hotel_search_tool.py`、`attraction_search_tool.py` | chunk 1/2 + 顶层 metadata 键 | 删除（工具下线） |
| `subagents/travel-consultant/SUBAGENT.md` | attraction_search（编排、title 校验、cover_image 嵌图）、hotel_search（直接问价）；**酒店候选/行程搜索已用 knowledge_base_search** | 改写提示词 |
| `src/skills/travel-quote/scripts/{hotel,attraction}_retriever.py` | 读 chunk 0/1/2（报价管线）、写 chunk 1/2（导入） | 读写均切 metadata |
| `src/api/travel_quote.py` PUT/PATCH | 写 chunks 1/2、合并顶层 metadata | 切 metadata |
| `src/core/agent_events.py` | `results[].cover_image` 图片事件模式（聊天封面展示） | 扩展识别 `documents` dict |
| 前端 `frontend/web/components/travel/{Attraction,Hotel}Manager.vue` | `row.metadata.images.cover`、`row.metadata.region/category_cn`（顶层键） | 不动（顶层键保留） |
| 测试：test_tool_discovery（黄金清单）、test_attraction_search_cover_image、test_travel_image_pipeline、test_agent_image_event_loop | 工具存在性与图片链路 | 更新/删除/改造 |
| Excel 导入导出（KB_DOC_EXPORT_COLS 含 metadata 列） | documents 行整表搬运 | 自动兼容（metadata 含 raw_payload 随行复制） |

## 3. metadata 标准与目标结构

**标准（沿用既有契约，本设计新定义键位）**：`documents.metadata` = 系统内部键（溯源 source_code/external_id/run_id/pipeline_version/ingested_at 等，不进 LLM）+ `metadata_only` 易变字段 + **`raw_payload` 业务原始数据包**（≤32KB；唯一白名单透出位，新增业务键无需改工具侧）。

**目标结构**（键名沿用现有 API 字段与提示词术语，`price_table` / `ticket_table` / `project_table`）：

```jsonc
// hotel_resource
{
  "region": "...", "sub_region": "...", "diamond_level": "4钻", "source_sheet": "...",  // 顶层键保持，前端/管理页用
  "raw_payload": {
    "price_table": "<原 chunk 1 文本>",
    "region": "...", "sub_region": "...", "diamond_level": "..."   // 业务键镜像，LLM 可见
  }
}
// attraction_resource
{
  "region": "...", "category": "...", "category_cn": "...", "images": {"cover": "file_xxx", "gallery": [...]},
  "raw_payload": {
    "ticket_table": "<原 chunk 1 文本>",
    "project_table": "<原 chunk 2 文本>",
    "region": "...", "category": "...", "category_cn": "...",
    "images": {"cover": "file_xxx", "gallery": [...]}   // file_id 镜像，LLM 可据以写 ![](file_id:xxx)
  }
}
```

决策说明：

- **顶层键保留为存储本位**（前端管理页、skill 列表直接读顶层），`raw_payload` 为 LLM 镜像位；写入路径在同一事务内同步两处，避免双源漂移。
- `images` 以 file_id 形式进 `raw_payload`：Word 嵌图只需 LLM 拿到 file_id 写 `![alt](file_id:file_xxx)`（image_inliner 契约），不需要反解 ImageRef。

## 4. 方案设计

### D1 迁移脚本 `scripts/migrate_travel_resource_tables_to_metadata.py`

参照 `migrate_storage_legacy.py` 惯例：

- 参数：`--env`、`--tenant-id`（可选，默认全量）、`--dry-run`（默认，仅报告）/ `--execute`（写库）/ `--verify`（校验模式：逐文档比对 raw_payload 键与 chunk 文本一致、输出差异清单）、`--drop-chunks`（清场，见 D6）。
- 逻辑：扫两类 source_type 文档 → 读 chunk 1/2 文本 + 顶层 metadata → 合成 `raw_payload`（不覆盖已有键，除 `--force`）→ 单文档事务 UPDATE `documents.metadata`。
- 幂等：`raw_payload` 已有对应键且与 chunk 文本一致则跳过；重跑零变更。
- 校验：合成后 `raw_payload` 序列化超 32KB → 告警列出文档清单（不截断，人工处理；价格表通常数 KB）。

### D2 写路径切换

- `import_hotel` / `import_attraction`：表文本写入 `metadata.raw_payload`，业务键（region 等）同步镜像；不再插 chunks 1/2，`total_chunks` = 1。
- `PUT /kb/hotels|attractions/{doc_id}`：`price_table` / `ticket_table` / `project_table` 字段改写 `metadata.raw_payload`（保持合并语义）；顶层 `metadata` 字段更新时同步镜像业务键。
- `PATCH /kb/attractions/{doc_id}/images`：更新顶层 `images` 的同时同步 `raw_payload.images`。

**Phase 2 实现细化（2026-10-10）**：

1. **共享同步模块**：镜像/表键写入/PUT 合并逻辑收敛到 `src/skills/travel-quote/scripts/kb_metadata.py`（SYSTEM_KEYS 与迁移脚本同清单、TABLE_KEYS 表键保护、`apply_put_metadata` 供两个 PUT 端点复用），三处写读路径共用，避免双源漂移。
2. **过渡期 PUT 双写 chunk**：PUT 表字段在写 `raw_payload` 的同时仍 UPDATE chunks 1/2（未迁移文档行存在时生效；已迁移/新导入 0 行命中无副作用）。理由：迁移脚本 `--verify` 与 `--drop-chunks` 的值一致性校验依赖「raw/chunk 文本相等」不变量——若只写 raw，观察期内被 PUT 过的文档会因 chunk 陈旧被 drop 防线拒绝或被 `--force` 重跑回退到陈旧值。双写保持不变量恒成立，Phase 5 清场后随回退读一并清理。
3. **region 确定性补入顶层**：`import_hotel` / `import_attraction` 将 region 参数 `setdefault` 进顶层 metadata（hotel parser 历史上不输出顶层 region、attraction parser 偶尔漏输出），使 §3 目标结构（顶层 region + raw_payload 镜像）对齐。
4. **空串语义**：表键值为空串 = 表存在但无内容（命中不回退 chunk），与旧契约「chunk 行在而文本空」一致；键缺失才回退。

### D3 读路径切换（travel-quote skill）

- `HotelRetriever.get_price_table` / `AttractionRetriever.get_ticket_table/get_project_table` 改读 `documents.metadata.raw_payload`；
- **兼容回退读**：`raw_payload` 无对应键时回退读 chunk 1/2——覆盖「代码已上线、数据未迁移」的窗口与环境（测试库/真机节奏不一），清场后自然失效，一并保留至 Phase 5 完成后再删（届时随任务收口清理）。
- 报价管线其余脚本（generate.py / attraction.py / update_hotel.py）经 retriever 方法访问，无直接改动。

### D4 统一工具侧（`knowledge_base_search`）

- **主体不改**：`_parse_doc_metadata` 白名单透出整个 `raw_payload`，价格/项目表迁入后自动进 LLM 上下文——这正是本方案的核心收益。
- 增强（图片事件保链路）：`documents_out[doc_id]` 增加 `cover_image` 字段——命中文档的顶层 `metadata.images.cover` 经 ImageRegistry 反解为 ImageRef（解析失败仅 warning，沿用 attraction_search 容错）；`agent_events._extract_image_refs_from_tool_result` 增加 `result["documents"]`（dict）扫描，复用 `cover_image` 模式。聊天封面图展示由「attraction_search 专属输出」泛化为「知识库文档通用能力」。
- `description` 补一句：`doc_metadata.raw_payload` 可含价格表/项目表/图片等业务数据。

### D5 工具下线与提示词改写

- 删除两个工具文件；`test_tool_discovery.py` GOLDEN_TOOLS 移除两项；`agent_events.py` 相关注释更新为统一工具模式。
- `SUBAGENT.md` 改写要点：
  - `attraction_search(query="平塘", top_k=8)` → `knowledge_base_search(query="平塘 景点", top_k=10)`；景点 `title` 校验改从 `documents[].title` 取；编排用项目表改从 `doc_metadata.raw_payload.project_table` 取；
  - 封面图：从 `documents[].cover_image.file_id`（或 `raw_payload.images.cover`）取得；
  - 直接问酒店价格：`knowledge_base_search` 后从 `doc_metadata.raw_payload.price_table` 据实回答（保留「价格不写入行程」约束原文）。

### D6 清场（独立执行，需部署授权）

`--drop-chunks`：删除两类资源的 chunks 1/2 行（chunks_vec 无对应行，天然无残留），UPDATE `total_chunks`（2→1 / 3→1）。**仅在 D2/D3 上线且 `--verify` 通过、真机观察期（建议 ≥1 周）无回归后执行**。

## 5. 行为差异与风险

| # | 差异/风险 | 评估与缓解 |
|---|---|---|
| 1 | 酒店名精确匹配从 ILIKE 变为向量+FTS（simple 分词对中文弱） | 产线已验证：SUBAGENT 酒店候选流程现行走 `knowledge_base_search` 且报价 skill 依赖其「酒店名称」字段，可用性有实证；上线后观察，必要时仅在提示词层调整 query 形态 |
| 2 | 价格表文本退出 FTS 索引（删 chunk 后价格关键词直接命中能力下降） | 由 chunk 0 摘要命中 + `raw_payload` 透出弥补；摘要本就含价格概览 |
| 3 | `info[:500]` 截断消失，统一工具片段全文返回 | 由 top_k 控制上下文占用（统一工具 `_no_truncate` 是既有设计） |
| 4 | raw_payload 超 32KB | 迁移脚本告警清单，人工拆分或精简 |
| 5 | 双写镜像（顶层 + raw_payload）漂移 | 写入收敛在同一函数/事务；`--verify` 模式可随时比对 |
| 6 | uuid 导入 attractions/hotels 不带 chunks 的存量缺口 | 迁移后价格表随 metadata 行复制反而更完整；chunk 0 缺失为存量独立问题，不在本任务修 |
| 7 | Phase 2 上线 → Phase 4 工具下线之间的部署窗口：新导入文档无 chunks 1/2，未下线的 attraction/hotel_search 取表静默为空 | 已缓解：两工具取表函数加 raw_payload 回退（Phase 2 实现细化），新导入数据照常取到；工具下线后回退随之消亡 |
| 8 | body.metadata 携带 raw_payload/images 经 PUT 注入（raw_payload 是 LLM 唯一可见通道，伪造表数据/提示注入） | 已缓解：`apply_put_metadata` 剥离 body 的 raw_payload/images/系统键/表键，表数据只能经表字段写入（Phase 2 实现细化） |

## 6. 验收标准

1. 迁移脚本 dry-run/execute/verify 在测试库通过，幂等重跑零变更；`--drop-chunks` 后 total_chunks 正确；
2. 统一工具搜酒店/景点，`documents[].doc_metadata.raw_payload` 含 price_table / ticket_table / project_table，顶层业务键镜像齐全；
3. 报价 skill 回归：酒店/景点价格提取结果与迁移前一致（走 e2e 或等价单测）；
4. Word 嵌图（`file_id` 引用）与聊天封面图事件链路不回归（统一工具 cover_image 增强路径）；
5. 黄金清单不再含两工具，相关单测更新后全量通过；
6. 真机迁移执行留档（dry-run 报告 + verify 报告）于任务收口记录。

## 7. 关联文档

- metadata 白名单契约：`src/tools/knowledge/knowledge_base_tool.py`（`_parse_doc_metadata`）
- metadata 分层查看设计：[doc-metadata-view-design.md](doc-metadata-view-design.md)
- 宏陶 raw_payload 对齐先例：[hongtao-shop-kb-design.md](../hongtao-shop/hongtao-shop-kb-design.md)
- 图片资产管线（file_id 嵌图契约）：[plan-image-asset-pipeline.md](../../plans/plan-image-asset-pipeline.md)
