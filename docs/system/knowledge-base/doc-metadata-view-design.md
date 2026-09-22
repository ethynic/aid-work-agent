# 知识库文档元数据查看（documents.metadata 前端可视化）

## 背景与问题

`documents.metadata`（TEXT JSON）是知识库的重要字段：api-ingest 管线写入溯源信息（source_code/external_id/run_id/pipeline_version/ingested_at）、metadata_only 易变字段（销量/库存/评论分等，D10 分家防易变字段重嵌入）、`raw_payload` 原始记录（默认 ≤32KB，D12）；公众号等外部来源写入 original_url 等。但整条链路断在两处：

1. 后端 `GET /documents` 列表的 `DocumentResponse` 不含 metadata，SQL 也不查询，前端拿不到；
2. 分块接口已返回 chunk 级 metadata，前端类型已声明但 UI 从未渲染。

用户核心诉求：文档级 metadata 在前端可见，入口放操作列。

## 决策

| 决策 | 内容 | 理由 |
|------|------|------|
| D1 详情按需拉取 | 新增 `GET /api/knowledge/documents/{doc_id}` 详情接口返回 metadata；列表接口不携带 | raw_payload 每条 ≤32KB，列表 100 条分页将放大 3MB+ 响应；操作列点开才拉 |
| D2 入口与载体 | 操作列「详情」按钮 → 「文档详情」Modal（BaseModal lg）；点击分块数仍进原分块弹窗 | 弹窗复用知识库页既有调试视图形态；不动「点标题=下载原文」既有交互 |
| D3 metadata 分层展示 | 已知语义键中文化键值列表（META_LABELS，未知键按原键名显示，不穷举维护）；对象/数组值 pretty JSON 等宽渲染；`raw_payload` 单独折叠区（默认收起、点击展开、点击复制、max-h 滚动） | 溯源字段日常扫一眼，大 JSON 不淹没小字段；与「向量数据」区块交互语言一致 |
| D4 可见性口径 | 详情接口与 chunks 接口同口径：对象级租户校验（跨租户/不存在统一 404 不泄漏存在性）；软删除仅 platform_admin 审计可见；platform_admin 全局视图恢复跨租户 | metadata 与文档本体同可见性，不新增越权面；service 层 SQL 作用域拼接复用既有三分支模式 |

## 接口契约

`GET /api/knowledge/documents/{doc_id}` → `{"success": true, "document": {...DocumentResponse 字段, "origin", "status", "expires_at", "metadata": {...}}}`

- metadata 由 service 层解析为 dict 返回；脏 JSON、合法 JSON 但非对象（如 `"null"`）容错为空对象
- 404：跨租户、软删除（非 platform_admin）、不存在

## 验证记录（2026-09-22）

- 后端：`tests/unit/test_knowledge_doc_visibility.py` 43 passed（新增 TestDocDetailVisibility 9 例 + TestApiDocDetail 3 例：SQL 作用域/软删除过滤/无租户收窄/metadata 解析与容错/参数透传/404 边界）；相邻回归 test_download_ticket + test_knowledge_tenant_scope + test_knowledge_upload_sanitize 51 passed
- 前端：`web/__tests__/api/knowledge.test.ts` 5 passed（新增 getDocumentDetail 2 例：认证头与解析、404 中文错误）；`npm run build` 通过
- 独立验证智能体：测试复跑一致 + diff 审查通过（租户边界/路由无冲突/语义 token/竞态守卫）；其指出的 P2 弹窗过期响应竞态与 nit（加载失败占位态、JSON null 边界）已在交付前修复并复跑
