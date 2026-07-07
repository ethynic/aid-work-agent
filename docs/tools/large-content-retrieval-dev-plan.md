# 大内容落盘检索闭环 开发计划

> 配套设计：[`large-content-retrieval-design.md`](./large-content-retrieval-design.md)
>
> 本计划把「落盘闭环 + grep 工具」拆为 4 个 Phase，串行推进。**第一个拿 http_api 开刀**——它既是信息黑洞最严重的工具，也是「智能复用决策」的试验田。

## 依赖链与推进顺序

```
Phase A（基建：_spill + grep + Dockerfile rg）
   ↓ 落盘管理器和检索工具就绪
Phase B（http_api 全方位改造：落盘 + 智能复用提示）← 重点，体现 agent 智能的样板
   ↓ 落盘闭环模式验证有效
Phase C（pdf/paddleocr 回填落盘）
   ↓ 全部截断工具闭环
Phase D（端到端验收：大响应→落盘→grep→read→回答）
```

> 每个 Phase 走三智能体开发流程（开发 → 测试 → CodeReview）。Phase B 完成后设置端到端验收 gate，验证「agent 能智能复用 + 智能回读」后再铺开 Phase C。

---

## Phase A — 基建：落盘管理器 + grep 工具

**目标**：补齐闭环的两个基础设施。这 Phase 不改任何业务工具，只造「工具的工具」。

### A1. 落盘管理器 `src/tools/_spill.py`
- 函数 `spill_large_content(content, *, prefix, suffix, meta)` → 返回 `{file_path, full_size, preview, truncated}`
- 落盘目录：`tempfile.gettempdir()/aid_agent_spill/`
- 文件命名：`{prefix}_{timestamp}_{short_uuid}{suffix}`
- 启动时清理旧 spill 文件（防无限增长），可选：按 mtime 老化（>24h 清理）
- 小内容不落盘（调用方判断阈值，本函数只负责落盘）
- 单测 `tests/unit/tools/test_spill.py`：落盘成功、内容完整、命名规范、清理逻辑

### A2. grep 工具 `src/tools/file/grep_tool.py`
- 调 ripgrep 二进制（subprocess 参数数组，不经 shell，防注入）
- 入参：pattern（必填，正则）、path（必填）、glob、output_mode（content/files_with_matches/count）、ignore_case、context、before_context、after_context、max_matches（默认 50）
- 行号 1-based（与 read cat -n 一致）
- 返回 matches 数组（含 file/line/content）+ count + truncated
- 路径白名单：复用 read 的白名单逻辑（项目根 + 临时目录）
- 超时兜底（10s）、二进制文件跳过（rg 原生行为）
- 单测 `tests/unit/tools/test_grep_tool.py`：正则匹配、行号、上下文、glob、三种 output_mode、max_matches 截断、路径越权拒绝、rg 不可用降级

### A3. 注册 + 部署
- `src/core/agent.py` 注册 GrepTool
- `Dockerfile` 补装 ripgrep（apt-get install -y --no-install-recommends ripgrep）
- 验证服务器 `rg --version` 可用

**验收**：
- _spill 单测全绿
- grep 单测全绿（含真实文件搜索）
- agent 启动注册 27 个工具（含 grep）
- Dockerfile 构建后 rg 可用

---

## Phase B — http_api 全方位改造（重点样板）

**目标**：http_api 作为第一个改造对象，同时落地两件事——①补落盘闭环（消除信息黑洞）；②**智能复用决策提示**（让 agent 学会判断要不要调）。这是后续所有 API 类工具的样板。

### B1. 落盘闭环接入
- `_parse_response`：JSON/text 序列化后 >5000 字符时，调 `spill_large_content` 落盘
- 返回结构（大响应）：`{data: 预览5000字符, truncated:true, file_path, full_size}`
- 返回结构（小响应）：`{success, data}` 不变，零回归
- 单测补充：大响应落盘、小响应不落盘、file_path 可被 grep/read 消费

### B2. 智能复用决策提示（核心：体现 agent 智能）

**问题**：当前 agent 每次都调 http_api，即使上下文已有相同结果。需要在 usage_guide 里植入「调用前判断」的框架，让 LLM 自主决策。

**设计思路**：提取订单场景背后的**三个通用判断维度**（不只限订单，适用所有 API）：

| 维度 | 含义 | 决策 |
|------|------|------|
| **数据时效性** | 数据是随时间变化，还是产生即固定？ | 固定型：上下文有就复用；变化型：每次都调 |
| **实体终态** | 查询对象是否已进入不可逆终态？ | 终态后所有查询免调（终态不可逆） |
| **上下文新鲜度** | 本会话是否已查过相同参数？结果还新鲜吗？ | 刚查过的固定数据直接复用 |

**订单场景的三种表现**（验证框架覆盖度）：
- 「查已下单订单买了哪些商品」→ **历史/详情类**（商品明细产生即固定）→ 上下文有则不调
- 「查订单当前状态」→ **实时/状态类**（状态会变）→ 每次都调
- 「已完成的订单还查状态」→ **终态判定**（已完成=终态，状态锁定）→ 不调

**usage_guide 草案文案**（植入 http_api，简洁，给框架非穷举）：

```
### 调用决策：上下文已有结果时是否复用
调用前先看本会话是否已有相同 API（同 URL 同参数）的近期结果，按数据特性决定：

1. 历史/详情类（订单商品明细、交易记录、基础资料、已发生事件）：数据产生即固定
   → 同会话已查过，直接复用上下文结果，不重复调用
2. 实时/状态类（当前状态、库存、价格、位置、余额）：随时间变化
   → 每次询问都应调用获取最新值
3. 终态判定：若上下文已显示实体进入终态（订单已完成、流程已关闭、物流已签收）
   → 该实体后续所有查询无需再调，终态不可逆

不确定数据属于哪类时，倾向于调用（宁可多调一次，不要用陈旧数据回答）。
```

> 最后一行是安全兜底——避免 LLM 误判把变化型当固定型，用陈旧数据回答。宁可多调，不可错答。

**schema 层微调**：description 补半句引导「调用前先检查上下文是否已有可用结果（见使用指南）」，把 LLM 注意力引向 usage_guide。

### B3. 端到端验收 gate（重点）
Phase B 完成后，用真实场景验证两件事：
- **落盘闭环**：构造 >5000 字符的 API 响应，验证 agent 能 grep 定位 + read 精读被截断内容
- **智能复用**：构造「订单商品明细」连续两轮询问，验证第二轮 agent 不重复调 API（看 tool_call 次数）；构造「订单状态」两轮询问，验证第二轮仍调用

不达标 → 调整 usage_guide 文案，不进 Phase C。

**验收**：
- 单测全绿（落盘 + 既有 http_api 测试无回归）
- 端到端：大响应能回读、智能复用决策生效
- Dockerfile 构建通过

---

## Phase C — pdf/paddleocr 回填落盘

**目标**：把 Phase 2 截断的两个工具补上落盘，消除剩余信息黑洞。复用 Phase A 的 _spill，模式与 Phase B 一致。

- `paddleocr_doc_parsing`：full_text >5000 字符时落盘
- `pdf_process._merge_results`：read/ocr/pdf_to_md 截断字段落盘
- 返回结构对齐 http_api 模式（preview + file_path + truncated）
- 单测补充：大文档落盘、file_path 可被 grep/read 消费

**验收**：单测全绿 + 端到端（OCR 大文档→grep 定位→read 精读）。

---

## Phase D — 端到端验收与文档收尾

- 全量回归 `tests/unit/tools/`
- 端到端跑通三类闭环：http_api 大响应 / OCR 大文档 / pdf 大文档
- 更新 `docs/tools/tool-development-spec.md`：补「大内容落盘闭环」章节作为新工具标准
- 更新 `docs/ideas.md` #34 状态

---

## 时间预估

| Phase | 预估 |
|-------|------|
| A 基建 | 1-1.5 天 |
| B http_api（含智能提示迭代） | 1-2 天（含端到端 gate） |
| C pdf/paddleocr | 0.5-1 天 |
| D 收尾 | 0.5 天 |

## 风险

- **rg 部署**：Dockerfile 补装后需确认服务器 rg 可用；grep 工具需在 rg 缺失时优雅降级（返回明确错误而非崩溃）。
- **智能复用提示效果**：usage_guide 引导依赖 LLM 理解力，可能需迭代文案。Phase B gate 专门验证此点，不达标不铺开。
- **临时文件堆积**：spill 文件落临时目录，依赖 OS 清理 + 启动时清理，需测试清理逻辑不误删正在使用的文件。
