# 开发计划：图片资产管线 Phase 3（高级能力扩展）

> **反向关联**：[主计划](plan-image-asset-pipeline.md)（Phase 0+1+2 已完成）· [设计文档](../system/image-asset-pipeline-design.md) · [ideas.md 第 37 条](../ideas.md)
> **创建日期**：2026-07-14（从主计划剥离）
> **状态**：📋 待开发（按需启动）
> **实施流程**：所有非平凡任务严格遵循 [.claude/rules/dev_workflow.md](../../.claude/rules/dev_workflow.md) 的「三智能体开发流程」（开发 → 测试 → CodeReview）。

---

## 0. 计划总览

### 0.1 定位

Phase 3 是图片资产管线的**开放能力扩展层**，不阻塞主链路。Phase 0+1+2 已建立完整的图片资产承载能力（来源 → 注册 → 寻址 → 嵌入 → 渲染 → 渠道发送），Phase 3 在此基础上按**实际使用反馈**渐进投入。

每个候选方向都是**独立可落地**的，不强求一次性完成。建议每个方向单独立子计划，估时 1-3 天。

### 0.2 Phase 0+1+2 已交付的能力（Phase 3 的基础）

| 能力 | 入口 | Phase 3 可复用 |
|------|------|---------------|
| ImageRef 数据契约 | `src/core/image_asset.py` | ✅ 所有 Phase 3 方向复用 |
| ImageRegistry 注册/寻址/清理 | `ImageRegistry.register/resolve_local_path/get_ref_by_file_id/fetch_to_local/cleanup_temp` | ✅ |
| Markdown `file_id:` scheme 解析 | `src/tools/_image_inliner.py` | ✅ PPT/PDF inliner / HTML img / `kb://` scheme 复用模式 |
| Agent 主循环推送 ImageRef | `src/core/agent.py` `_extract_image_refs_from_tool_result` | ✅ LLM 占位符方向复用 |
| UnifiedResponse.images 字段 | `src/models/message.py` `get_images/set_images/add_image` | ✅ wecom 渠道直接消费 |
| Web 端 ImageGallery + placement | `frontend/src/components/ui/ImageGallery.vue` | ✅ |
| 渠道占位符渲染 | `src/channels/_image_text_renderer.py` | ✅ wecom 渠道复用 |

---

## 1. 候选方向（按预估优先级排序）

### 1.1 wecom 渠道图片消息（预估优先级：高）

**触发条件**：wecom 渠道客户需要图片回复（目前 wecom 渠道只发文本卡片）。

**改造范围**：
- `src/channels/wecom/adapter.py` 的 `send_message` 拆分发送（与 feishu/dingtalk 一致）
- 复用 P2.9.0 `_image_text_renderer.render_text_with_image_placeholders`
- 复用 P2.9.1 feishu 的 `_resolve_image_local_path` 模式
- 调 wecom 的 `media.upload` API（如已存在）拿 media_id，发 image msg

**估时**：1 天（基础设施已就位，主要是 wecom 渠道 API 对接）

**关键约束**：
- wecom 媒体文件同样有大小限制（参考 wecom_kf 2MB 限制）
- 失败降级到文本占位符 + 跳过该图（不阻断整体发送）

---

### 1.2 wecom_kf 图片语义对称修复（COLLEAGUE-CODE 已知缺陷）

**触发条件**：同事下一轮迭代，或本计划启动时同步处理。

**现状缺陷**：
- wecom_kf `send_message` 走"含表格/图片时整段转长图"路径（同事 07fd592 完成），**只识别 md 文本中的 `![](file_id:xxx)`**
- 工具返回 ImageRef 但 LLM 未在 md 写引用时（如 `placement: after_text` 的纯推送场景），wecom_kf **完全丢图**
- feishu/dingtalk 通过 `message.get_images()` 兜底，wecom_kf 没有这一层

**改造方案**（两种二选一）：

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| A. 渲染前合并 | 在调 `contains_table_or_image` / `render_markdown` 前，先把 `message.get_images()` 中的 ImageRef 追加到 md 末尾（`![{name}](file_id:{file_id})`） | 单点改造，复用现有长图路径 | 改变了 md 原文（虽然只是追加） |
| B. 失败兜底 | 长图渲染/发送后，单独循环发送 `message.get_images()` 中未被 md 引用的图（按 file_id 差集） | 不污染 md | 多发图片消息，可能撞 wecom_kf 5 次回复限制 |

**建议**：方案 A（更符合 wecom_kf 整段长图的设计哲学）。

**估时**：0.5 天

---

### 1.3 PPT/PDF inliner 接入

**触发条件**：非 Word 文档嵌图需求（PPT 演示文稿 / PDF 报告）。

**改造范围**：
- `src/tools/ppt/` 集成 image_inliner：在 spec 构造时把 `file_id:` 解析为本地路径
- `src/tools/pdf/` 集成 image_inliner：weasyprint 接收 HTML 后由 inliner 预处理 `<img src>`
- 复用 `src/tools/_image_inliner.py`，可能需要扩展 HTML syntax 支持（见 1.7）

**估时**：2-3 天（PPT 和 PDF 可分开做）

**关键决策**：
- PPT 已有 `add_picture(path)` 模式，只需把 spec 中的 path 字段经过 inliner 处理
- PDF 用 weasyprint，需要 inliner 支持 `<img src>` HTML syntax

---

### 1.4 文档解析器内嵌图提取

**触发条件**：知识库内容图片检索需求（用户上传 Word/PPT 后想搜里面的图）。

**改造范围**：
- `src/knowledge/parsers/` 各解析器（word_parser / excel_parser / ppt_parser / pdf_parser）提取内嵌图
- 调 `ImageRegistry.register(source="knowledge_base", usage="inline", linked_doc_id=..., linked_chunk_id=...)` 注册
- 写入 `chunks.metadata.images` 契约（设计文档 §5.1.2 已定义）

**估时**：3-5 天（4 个解析器 + 测试）

**关键约束**：
- 不同格式提取方式不同（python-docx / openpyxl / python-pptx / pdfplumber 各有图提取 API）
- 提取的图必须注册为 knowledge_base 永久资产（不参与 cleanup_temp）

---

### 1.5 image_parser OCR + VLM

**触发条件**：知识库图片内容检索需求（用户问"包含瀑布的图"）。

**改造范围**：
- 实现 `src/tools/ocr/image_parser.py`（目前是空壳）
- OCR（PaddleOCR / 已有依赖）提取文字
- VLM（多模态 LLM）生成图片描述
- 把文字 + 描述组装成可检索 chunk，调 `attraction_retriever` 类似流程入库

**估时**：3-5 天

**关键决策**：
- VLM 调用成本：每次入库都调一次多模态 LLM，知识库量大时成本高
- 缓存策略：相同 file_id 不重复调 VLM
- chunk 内容：OCR 文字（精确检索）+ VLM 描述（语义检索）

---

### 1.6 LLM `[[IMAGE:file_id]]` 占位符

**触发条件**：LLM 主动引用图片需要精确行内定位（current `placement: inline` 在 Web 端按 after_text 渲染，渠道端按 `[图片：alt]` 占位符）。

**改造范围**：
- Agent 后处理：LLM 文本流中的 `[[IMAGE:file_xxx]]` 替换为对应的图片引用
- Web 端：在 `<img>` 标签精确位置渲染（不是文本下方画廊）
- 渠道端：按 `[图片：alt]` 占位符发送（与现有 inline 一致）

**估时**：2-3 天

**关键决策**：
- 占位符格式：`[[IMAGE:file_xxx]]` vs `![](file_id:xxx)`（后者已有，但 LLM 生成 md 时容易和正常图片混）
- 替换时机：LLM 流式输出完成后整体替换，还是边输出边替换

---

### 1.7 `kb://` scheme 支持

**触发条件**：LLM 频繁引用知识库图，`file_id:file_xxx` 太底层。

**改造范围**：
- 扩展 `_image_inliner.py` 支持 `![](kb://doc/42)` / `![](kb://chunk/108)` 语义化引用
- inliner 内部把 `kb://` 解析为对应 doc/chunk 的 cover_image file_id，再走现有 `file_id:` 路径

**估时**：1 天

---

### 1.8 HTML img tag 支持

**触发条件**：pdf_process / weasyprint 嵌图（HTML 渲染路径）。

**改造范围**：
- 扩展 `_image_inliner.py` 支持 `<img src="...">` HTML syntax（目前只支持 markdown `![]()`）
- 与 1.3 PDF inliner 配套

**估时**：0.5 天

---

## 2. 不在 Phase 3 范围

以下能力**不属于** Phase 3，明确边界：

- ❌ 视频资产承载（视频文件管理是另一套体系）
- ❌ 音频资产承载（同上）
- ❌ 图片编辑能力（裁剪 / 滤镜 / 标注）
- ❌ 图片 CDN 加速（当前 `/api/files/{file_id}/download` 已够用）

---

## 3. Phase 3 启动判断标准

每个方向的启动条件（任一满足即可评估）：

| 方向 | 启动信号 |
|------|---------|
| wecom 渠道图片 | wecom 渠道客户主动反馈"看不到图" |
| wecom_kf 语义对称 | 同事下一轮迭代 / 测试环境验证发现丢图 |
| PPT/PDF inliner | 旅游顾问之外其他子智能体（如数据分析）要生成 PPT/PDF 含图 |
| 文档解析器内嵌图 | 知识库用户主动问"我上传的 Word 里的图能搜吗" |
| OCR + VLM | 同上，且文字描述不够（需要看图内容） |
| `[[IMAGE:file_id]]` 占位符 | LLM 反馈"想在精确位置插图"但 placement 不够用 |
| `kb://` scheme | LLM 频繁写错 file_id（file_abc123 难记） |
| HTML img tag | PDF 嵌图需求出现 |

---

## 4. 验收门槛（每个方向独立）

- 单元测试覆盖核心路径（命中 / 未命中 / 异常 / 边界）
- 三智能体流程通过（开发 → 测试 → CodeReview）
- 启动安全检查（import 不拉起 master_agent，无循环依赖）
- 设计文档与本计划保持一致（如有变更同步更新）
- `file_usage.md` / `cache_usage.md` / `backend_dev.md` / `frontend_dev.md` 协调更新（如涉及）

---

## 5. 风险与回滚策略

| 风险 | 影响 | 缓解 / 回滚 |
|------|------|------------|
| wecom media.upload 失败率高 | 中 | 失败降级到文本占位符，单图不阻断 |
| VLM 调用成本失控 | 高 | 缓存 + 配额限制 + 仅在用户主动检索时调用（不入库时调） |
| 文档解析器内嵌图提取破坏原解析流程 | 中 | 在解析器末尾追加图提取，不修改原解析逻辑 |
| `[[IMAGE:file_id]]` 占位符替换影响 LLM 流式渲染 | 中 | 后处理在 LLM 输出完成后整体替换，不边输出边替换 |
| PPT/PDF inliner 引入新依赖 | 低 | 复用已有 python-pptx / weasyprint，不引入新包 |

---

**本计划结束**。Phase 3 各方向按需启动，每个方向单独立子计划，不强制一次性完成。
