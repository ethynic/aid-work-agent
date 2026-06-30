# 文件生成类工具入参语义拆分设计

> 创建日期：2026-06-30  
> 状态：设计完成，部分代码已开始  
> 关联工具：word_process、pdf_process、excel_process、ppt_process

---

## 1. 背景

当前 Word/PDF/Excel/PPT 等文件处理工具采用“Agent 只传 `context` + `file_paths`，工具内部 LLM 自动路由”的入口设计。

该设计降低了 Agent 使用门槛，但 `context` 同时承载了三类语义：

1. 用户目的：生成 Word、导出 PDF、读取、修改、格式化。
2. 待处理正文：Markdown、HTML、CSV、JSON、表格数据。
3. 隐含参数：标题、文件名、模板、输出格式。

这会导致内部 LLM 路由和实际执行阶段都读到噪声。典型问题：

- 纯 Markdown 正文没有“生成 Word”字样时，内部路由可能无法判断任务。
- 混合文本中包含“生成一份 Word 文档，格式为 Markdown 表格”时，转换器可能把该指令写进正文。
- PDF/Excel/PPT 存在同类风险，只是表现形式不同。

## 2. 问题分析

### 2.1 Word 工具

`word_process.context` 当前定义为“用户的原始需求描述和相关内容”。生成 Word 时，`md_to_word` 直接读取 `ctx.context` 作为 Markdown 正文。

风险：

- 路由依赖内部 LLM 对混合文本的判断。
- 正文边界不明确，工具指令可能进入 Word 正文。
- 标题、文件名依赖 LLM 从正文中抽取，失败时体验不稳定。

已完成的短期修复：

- 对明显 Markdown 文档正文增加确定性 `md_to_word` 路由兜底。
- `md_to_word` 执行前提取 Markdown 正文，剥离“生成/导出/格式为”等指令前缀。
- 从 H1-H6 标题提取默认标题和文件名。

### 2.2 PDF 工具

`pdf_process.context` 同样混合“用户需求 + Markdown/HTML 正文”。`md_to_pdf` 和 `html_to_pdf` 使用 `ctx.context` 作为正文。

风险：

- Markdown/HTML 转 PDF 时，指令前缀可能进入 PDF。
- 纯正文输入时，内部路由可能无法判断用户想生成 PDF。
- HTML 正文与自然语言指令混合后，可能破坏 HTML 结构。

### 2.3 Excel 工具

Excel 已有规则路由和导出数据校验，稳定性高于 Word/PDF。

风险：

- `export` 仍以 `ctx.context` 作为数据来源，若 context 包含“请导出为 Excel”前缀，可能污染 Markdown/CSV 数据。
- JSON/CSV/Markdown 表格正文缺少独立字段，后续扩展模板填充和多 sheet 数据时会变复杂。

### 2.4 PPT 工具

PPT 的 `context` 可表示主题或大纲，天然更接近生成指令，风险低于 Word/PDF。

风险：

- 默认标题使用 `context[:30]`，可能把“帮我生成一份...”作为标题。
- 模板模式下，需求与内容大纲混合，可能影响页面规划。

## 3. 设计目标

1. 将“用户目的”和“待处理正文”分离，降低内部 LLM 路由歧义。
2. 保持现有 `context` 兼容，不破坏已上线 Agent 调用。
3. 为 Word/PDF/Excel/PPT 建立一致的文件生成类工具入参契约。
4. 对高确定性场景优先规则路由，内部 LLM 作为补充。
5. 执行阶段只消费清洗后的正文，不直接消费混合 `context`。

## 4. 新入参契约

文件生成类工具逐步支持以下字段：

```python
instruction: Optional[str]  # 用户目的，如“生成 Word 文档”“导出 PDF”“读取附件”
content: Optional[str]      # 待处理正文，如 Markdown/HTML/CSV/JSON
content_type: Optional[str] # markdown/html/csv/json/text/auto
output_name: Optional[str]  # 业务文件名，可选
file_paths: Optional[List[str]]
context: Optional[str]      # 兼容旧调用
```

字段优先级：

1. 路由阶段优先使用 `instruction + file_paths + content_type + content 摘要`。
2. 执行阶段优先使用 `content`。
3. `context` 仅作为兼容入口：工具内部先拆分为 `instruction/content`，再进入统一流程。

## 5. 兼容策略

### 5.1 新调用方式

```json
{
  "instruction": "生成贵州安顺坝陵河3天2晚行程Word文档",
  "content": "## 安顺坝陵河大桥3天2晚行程\n\n| 天数 | 时段 | 行程安排 | ...",
  "content_type": "markdown",
  "output_name": "安顺坝陵河3天2晚行程.docx"
}
```

### 5.2 旧调用方式

```json
{
  "context": "生成一份贵州安顺坝陵河3天2晚行程Word文档，格式为Markdown表格。\n\n## 安顺坝陵河大桥3天2晚行程\n\n..."
}
```

兼容处理：

- 工具检测 `context` 中的 Markdown/HTML/CSV/JSON 正文边界。
- 指令前缀进入 `instruction`。
- 正文进入 `content`。
- 若无法可靠拆分，则保持旧逻辑，并返回明确错误或警告。

## 6. 路由原则

### 6.1 规则优先

以下场景不依赖内部 LLM：

- `content_type=markdown` 且 instruction 提到 Word/PDF 生成。
- `content` 是明显 Markdown 文档，且工具是 Word。
- `content_type=html` 且工具是 PDF。
- `file_paths` 是 `.csv/.json` 且 instruction 提到 Excel。
- `file_paths` 是 `.docx/.pdf/.xlsx` 且 instruction 是读取/转换。

### 6.2 LLM 补充

以下场景继续使用内部 LLM：

- 修改已有文件，需要抽取复杂 operations。
- 格式化、模板填充、拆分/合并等参数较复杂的操作。
- 用户自然语言描述不完整，需要综合判断。

## 7. 执行层要求

1. `md_to_word`、`md_to_pdf` 只接收清洗后的 Markdown 正文。
2. `html_to_pdf` 只接收完整 HTML 或 HTML 片段，不接收自然语言指令。
3. `excel export` 只接收表格数据，不接收导出指令。
4. `ppt_process` 生成标题时优先使用结构化标题字段或 Markdown 标题，不再直接截断混合 context。
5. 所有生成类工具返回结果中保留 `file_path`，交付仍由 Agent 调用 `cp`。

## 8. 观测与测试

新增测试矩阵：

| 工具 | 场景 | 期望 |
|------|------|------|
| Word | 纯 Markdown context | 一次生成成功 |
| Word | 指令 + Markdown context | 指令不进入正文 |
| PDF | 指令 + Markdown context | 指令不进入 PDF |
| PDF | 指令 + HTML context | HTML 结构不被污染 |
| Excel | 指令 + Markdown 表格 | 只导出表格数据 |
| Excel | 纯 Markdown 表格 | 提示需要明确导出意图或按工具语境导出 |
| PPT | 指令 + Markdown 大纲 | 标题来自大纲标题 |

## 9. 迁移范围

优先级：

1. Word：已开始，继续补齐正式入参字段。
2. PDF：复用 Word 的正文提取和规则路由。
3. Excel：为 export 增加正文提取和 `content` 字段。
4. PPT：优化标题提取和 context 拆分。

## 10. 非目标

- 不改变 Agent 必须调用 `cp` 交付文件的规则。
- 不移除内部 LLM 路由，只降低其在确定性场景中的依赖。
- 不一次性重写所有文件工具，只做兼容式演进。
