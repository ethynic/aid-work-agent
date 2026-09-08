# 知识库 Excel 行级分块设计（表头注入 + 格式前置判定）

> 状态：📋 设计定稿（2026-09-08），待开发
> 关联：[knowledge-base-enhancement-design.md](knowledge-base-enhancement-design.md)（知识库能力增强总设计）

## 1. 背景与问题

### 1.1 现状链路

```
ExcelParser.parse() -> ParseResult(text)          # 行转 "a | b | c" 段落，\n\n 分隔
  -> service.upload_document()                    # 拼文档标题前缀
  -> TextChunker.chunk()                          # 按段落拆分 -> 按句子边界跨段落合并到 ~512 字符
  -> embed_batch() -> documents + chunks + chunks_vec
```

### 1.2 三个问题

**问题 1：「一行一片」规则实际未生效**。`TextChunker` 把每个段落拆成「句子」（行内无 `。！？；` 标点时整行算一个句子），然后**跨段落合并**到约 512 字符（`chunker.py` chunk_size=512）。Excel 数据行通常不含句末标点，导致连续多个商品行被合并进同一个 chunk——与 `excel_parser.py` 注释宣称的「每个商品成为独立检索单元」相反。只有单行超过 512 字符时才独占一块。

**问题 2：表头与数据行分离，chunk 缺字段语境**。表头行作为独立段落输出，经 chunker 合并后可能落到任意 chunk（或单独成块）；绝大多数数据行 chunk 是 `SKU1 | 100 | 50` 这类裸值拼接，没有字段名，对向量检索和 FTS 几乎是噪声。

**问题 3：无格式前置判定**。所有 sheet 一律按「第一行是表头、后续是数据行」的二维表假设处理（实际连表头注入都没做）。非二维表（单行记录、标题行 + 说明文字、合并单元格、竖排键值表）会全部错切。

## 2. 目标

1. 二维表 sheet：**一行数据 = 一个 chunk**，表头字段名注入每个 chunk，保证每个商品/记录可独立命中。
2. 每个 sheet 前置格式判定：二维表走行级分块；非二维表回退现有文本分块，不错切。
3. 判定默认零成本（启发式，无 LLM 调用）；可选 LLM 兜底开关（置信度低时启用，需按 billing_audit.md 计费）。
4. 不改动检索侧（`HybridRetriever` / `vector_db` / 跨租户共享），只改解析 → 分块这一段。

## 3. 方案设计

### 3.1 总体架构：结构化分块旁路

核心思路：**Excel 的分块决策在解析器内完成**，不经过 `TextChunker` 的句子合并逻辑（它天然不适合表格）。通过 `ParseResult` 增加可选字段把预分块结果旁路给 service：

```python
# src/knowledge/parsers/__init__.py
@dataclass
class ParsedChunk:
    text: str                    # 已含表头注入的最终 chunk 文本
    metadata: Dict[str, Any]     # sheet_name / row_number / chunk_type 等

@dataclass
class ParseResult:
    text: str
    metadata: Dict[str, Any]
    precomputed_chunks: Optional[List[ParsedChunk]] = None   # 新增，None = 走 TextChunker
```

```python
# src/knowledge/service.py upload_document() 分块处改为：
if parse_result.precomputed_chunks:
    chunks = [
        {"text": c["text"], "tokens": estimate(c["text"]), "index": i, "metadata": c["metadata"]}
        for i, c in enumerate(self._inject_filename(parse_result.precomputed_chunks, file_filename))
    ]
else:
    text_with_filename = f"文档标题：{file_filename}\n\n{parse_result.text}"
    chunks = self.chunker.chunk(text_with_filename)
```

- 文件名前缀注入策略：**每个 chunk 都拼** `文档标题：{file_filename}\n`（现有 TextChunker 路径只在首个 chunk 有文件名，其余 chunk 检索时无法命中文件名，本次一并修正 Excel 路径；TextChunker 老路径不动，控制影响面）。
- `chunks` 表已有 `metadata` JSONB 列，`ParsedChunk.metadata` 直接落库，`get_document_chunks` 已返回 metadata，前端/检索侧零改动。

### 3.2 ExcelParser 重构

```
parse(file_path)
  -> 逐 sheet 读取为 rows: List[List[str]]          # 空单元格转 ""，跳过全空行
  -> 对每个 sheet 调 detect_layout(rows)             # §3.3 格式判定
       layout == "table"    -> 行级分块（§3.4）
       layout == "freeform" -> 回退现有文本模式（行转段落，交给 TextChunker）
  -> 汇总：有任一 table sheet -> 输出 precomputed_chunks + 混合 text
           全部 freeform     -> precomputed_chunks = None（完全走老路径）
```

- 保留 `ParseResult.text` 的现有生成逻辑（原始行文本），供摘要生成（`generate_summary` 取前 5000 字符）与 `documents.raw_text` 落库使用，不影响文档摘要质量。
- openpyxl 继续用 `read_only=True, data_only=True`；合并单元格在 read_only 模式下只有左上角有值，其余为 None，按空单元格处理（不展开合并区域，避免引入复杂度；真实场景中合并单元格的表格多数仍能被行级分块覆盖）。

### 3.3 格式判定（detect_layout，启发式）

**输入**：单个 sheet 的全部非空行 `rows`。行宽用**行跨度**（最后一个非空单元格下标 + 1）度量，避免中间/尾部空单元格（稀疏行）干扰计数。
**输出**：`"table"`（首行为表头的二维表）或 `"freeform"`（回退文本模式）。

**信号与规则**（按顺序短路，实现见 `excel_parser.py` `_detect_layout`）：

| # | 条件 | 判定 |
|---|------|------|
| 1 | 非空行数 < 3 | freeform（2 行无法区分「表头 + 数据」与散文行——两行散文的首行也常"像表头"） |
| 2 | 列结构不规则：数据行跨度众数 < 2（单列内容=列表/散文），或任意数据行跨度 ×2 < 众数（备注/合并行），或跨度 > 众数 + 2 | freeform |
| 3 | 首行跨度与数据行跨度众数不一致 | freeform（首行可能是标题/合并说明行） |
| 4 | 首行「像表头」且与任一数据行完全相同 | table（无表头数据表，首行即数据，不做表头注入） |
| 5 | 首行「像表头」 | table，首行为表头 |
| 6 | 其余 | table（保守默认：列数一致的矩形数据按表处理；首行不像表头时，首行也按数据行输出，不注入） |

**「像表头」打分**（首行各非空单元格逐格判定，满足条件占比 >= 0.6 即认为像表头）：

- 单元格文本短：长度 <= 30 字符；
- 少数字：数字字符占比 <= 0.5 且无 5 位以上连续数字（排除金额、SKU 编码；允许「价格2024」这类少量数字）；
- 无句末标点（`。！？`）；
- 唯一性（行级）：首行非空单元格去重后数量 / 总数 >= 0.9（表头列名不重复）。

**设计取舍**：

- **启发式优先、零成本**：绝大多数商品表 / 价格表 / 库存表能被上述规则覆盖。规则判定结果写入 `ParseResult.metadata.layout_report`（每个 sheet 的判定结果 + 命中规则号），排查与审计可见。
- **LLM 兜底做成可选开关（默认关）**：`configs/config.yaml` 新增 `knowledge.excel_llm_layout_fallback: false`。开启时，仅当启发式走到规则 6 且首行「像表头」得分在 0.4~0.6 灰色地带时，调用一次 `llm_gateway.chat`（输入该 sheet 前 20 行，输出 `table|freeform` 单词）重判（重判结果记录为规则 7=freeform / 8=table）。LLM 调用必须 `record_background_llm_usage(response.get("usage"), source="excel_layout_detect")` 计费（billing_audit.md §4.1 模式）。首版默认关闭，跑一段时间看 `layout_report` 误判率再决定是否启用。

### 3.4 行级分块规则（table 布局）

对判定为 table 的 sheet，设表头 `headers = [h1, h2, ...]`（取首个非空行的非空单元格，列对齐数据行众数列数）：

1. **一行数据 = 一个 chunk**，禁止合并相邻行；
2. chunk 文本格式（键值对形式，比裸值拼接的检索命中率高）：

   ```
   [工作表: Sheet1] 商品名称: 阿克苏苹果 | 价格: 12.5 | 产地: 新疆 | 库存: 500
   ```

   - sheet 名前缀仅在有多个 sheet 时加（单 sheet 文件省略，减少噪声 token）；
   - 数据行列数多于表头时，多出的列用 `列1/列2...` 占位表头；少于表头时缺省字段不输出；
   - 空值单元格输出为 `字段: `（保留字段名，提示该字段存在但为空——部分场景「有没有这个字段」本身是信息）；
3. **超长行**：单个 chunk 超 `MAX_EMBEDDING_CHUNK_CHARS`（6000 字符）时复用 `_split_long_text` 的定长切割逻辑（在 ExcelParser 内实现等价函数，不 import chunker 内部方法），各分段 metadata 带 `row_number` + `segment` 序号；
4. metadata：`{"chunk_type": "excel_row", "sheet_name": ..., "row_number": ...}`（row_number 为 Excel 实际行号，含表头偏移，便于人工核对原文件）；
5. `## 工作表: xxx` 这类标题行**不再作为独立 chunk**，sheet 归属已内联在每行前缀中。

### 3.5 freeform 回退行为

判定为 freeform 的 sheet 沿用现有行为（行转 `| ` 拼接段落 + `\n\n` 分隔），与其它 freeform sheet 一起拼成 `ParseResult.text` 交给 `TextChunker`，`precomputed_chunks = None`。即：**非二维表文件的最终行为与今天完全一致**，只有含 table sheet 的文件才走旁路。

### 3.6 计费核对

- 启发式判定：纯 CPU 字符串运算，无 LLM / Embedding 新增调用，无计费影响；
- Embedding：chunk 数量会变化（table 文件从「多行合并一块」变为「一行一块」，chunk 数增多），embedding tokens 总量基本不变（文本总量近似），单 chunk 变短反而降低单次请求超限风险；
- 可选 LLM 兜底：见 §3.3，`record_background_llm_usage` 计费，默认关闭。

## 4. 涉及文件

| 文件 | 改动 |
|------|------|
| `src/knowledge/parsers/__init__.py` | `ParseResult` 加 `precomputed_chunks` 字段；新增 `ParsedChunk` dataclass |
| `src/knowledge/parsers/excel_parser.py` | 重构：sheet 矩阵读取 + `detect_layout` + 行级分块 + `layout_report` |
| `src/knowledge/service.py` | `upload_document` 分块分支（precomputed 旁路 + 文件名注入每个 chunk） |
| `configs/config.yaml` + `src/config/settings.py` | `knowledge.excel_llm_layout_fallback`（默认 false，预留） |
| `src/knowledge/chunker.py` | 不改动（老路径保持原样） |

## 5. 测试计划

单测放 `tests/unit/`（解析器纯逻辑，全 mock，无外部服务）：

| 用例组 | 覆盖点 |
|--------|--------|
| detect_layout | 标准二维表（table+表头）、无表头数据表（规则 4）、单行 sheet（freeform）、首行是标题行（freeform）、不规则列数（freeform）、含数字数据首行不像表头（规则 6 无表头注入）、唯一性/长度边界 |
| 行级分块 | 一行一块、表头键值注入、多 sheet 前缀、超长行切割、空值字段、列数不齐（多列占位/缺列省略） |
| 旁路 | `precomputed_chunks=None` 走老路径不变；有 precomputed 时 `upload_document` 落库 chunk 数/metadata 正确 |
| 回归 | 现有 `test_*.py` 知识库集成测试全绿（freeform 行为不变） |

手工验证：准备 3 类真实 Excel（商品表 / 多 sheet 混合表 / 说明文式 sheet）上传后用 `get_document_chunks` 检查 chunk 文本与 `layout_report`。

## 6. 开发计划

| 步骤 | 内容 | 预估 |
|------|------|------|
| 1 | `ParsedChunk` + `ParseResult.precomputed_chunks` + service 旁路分支 | 0.5 天 |
| 2 | ExcelParser 重构（矩阵读取 + detect_layout + 行级分块） | 1 天 |
| 3 | 单测 + 回归 + 真实文件手工验证 | 0.5 天 |

高风险项：无（不触检索链路、不动数据库 schema、默认无新增 LLM 调用）。按 dev_workflow.md 属「常规」级别：开发自测 + 一个独立验证智能体。
