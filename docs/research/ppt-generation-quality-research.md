# PPT 生成质量课题调研：从「能生成」到「能交付」

> 状态：✅ 调研完成（2026-09-29）。本文回答一个课题：**agent 生成的 PPT 如何达到「样式可以接受、可以交付」的水平**。
> 触发事件：agent2 连续三轮实测（tr_69650ee91a1b4886 / tr_ceaf2fe01b4f49a5 / tr_e690b1fb76734938）产出的 PPT 均被用户判定为「毫无美感 / 完全不合格」。
> 关联文档：[PPT 工具设计](../tools/ppt/ppt_tool_design.md)（现行架构，整合版）、[HTML 转 PPTX 技术调研](html-to-pptx-conversion-research.md)（前次调研）。

## 目录

1. [问题实测：三代产物的病灶](#1-问题实测三代产物的病灶)
2. [现有管线解剖：病根在架构](#2-现有管线解剖病根在架构)
3. [业界方案调研](#3-业界方案调研)
4. [目标架构建议](#4-目标架构建议)
5. [分阶段落地路线](#5-分阶段落地路线)
6. [结论速览](#6-结论速览)

---

## 1. 问题实测：三代产物的病灶

三代实测（同主题「渠道毛利分析」）覆盖了现有三条渲染路径，恰好每条路径暴露一类问题：

| 代次 | 路径 | trace | 病灶 |
|---|---|---|---|
| 一代 | python-pptx 回退 | tr_69650ee91a1b4886 | 大红底色（主题预设 `bg=d90429` 用作整页背景）+ 空白目录页/表格页（渲染器不支持该布局）+ 文件日期 2013（默认模板元数据）+ 18/18 页对象重叠（QA 已报警仍交付） |
| 二代 | pptxgenjs spec 路径 | tr_e690b1fb76734938（18 页附件） | **占位符泄漏**：对比页输出「方案 A / • 暂无内容」原文；标题是「内容 / 表格 / 图表 / 图片」等版式名；全片只有一套硬编码 navy/orange 配色；表格/图表页只有两个字标题的空架子 |
| 三代 | python-pptx 模板解析路径 | tr_e690b1fb76734938（27 页成功 span） | 7/27 空白页（QA 报「QA: PPTX 包含空白页」仍 `deliverable=true`）；内容匹配不进模板占位符，同样泄漏默认文案 |

共性结论：**三条路径都不是「渲染崩了」，而是「渲染成功的垃圾」**——管线对缺失内容、错误规划高度容忍，用硬编码默认值兜底把问题藏进成片，QA 形同虚设（报警但不拦截）。

## 2. 现有管线解剖：病根在架构

现行管线（[PPT 工具设计](../tools/ppt/ppt_tool_design.md) 所述整合架构的落地产物）：

```
用户输入 → input_normalizer → 模式路由 ─┬→ planner(LLM 一次出全量 plan JSON) → spec_builder → renderer-node(pptxgenjs)  ①spec 路径
                                        ├→ template_analyzer(python-pptx 解析模板+匹配)                                        ②模板路径
                                        └→ （pptxgenjs 不可用时）→ generator.py(python-pptx 11 套布局 + 18 主题)              ③回退路径
                                   → quality_validator → 交付
```

逐层病根：

**2.1 内容规划层（planner.py）**
- LLM 一次调用直出全量 plan JSON（slides 数组），**没有大纲确认环节**、没有按页迭代；内容质量完全押注单次生成。
- prompt 里 theme 选择是「商务→2 或 18」式的数字映射，LLM 对最终视觉效果零感知——选主题等于盲选。
- 规划失败模式无反馈：LLM 给 comparison 页却没给 left/right 数据时，管线不报错、不重试，静默流向渲染层。

**2.2 设计系统层（spec_builder.py + layout_registry.py）**
- **没有设计系统**。spec 路径全部视觉硬编码：单一 8 色 `COLORS` 字典（navy/blue/orange...），`theme.py` 的 18 套主题只有回退路径③在用，spec 路径根本不消费——所以 pptxgenjs 产物千篇一律。
- 11 种布局每种**只有一个固定变体**（`layout_registry.py` 每类一帧坐标），同类页连续出现时版面完全重复，正是「AI 味」的典型来源（Anthropic skill 明确把「同版式反复」列为 AI 痕迹红线）。
- **缺内容时的兜底是硬编码占位符**：`spec_builder.py` 中 `"内容"/"表格"/"图表"/"图片"` 默认标题、`"方案 A"/"方案 B"` 对比默认、`"• 暂无内容"` 正文默认——这是二代产物占位符泄漏的直接来源。

**2.3 模板解析层（template_analyzer.py）**
- 每次生成时逐次解析任意用户 pptx：占位符继承链回溯、版式名当语义标签、容量匹配启发式——**业界没有一家产品走这条路**（见 §3.6），公认的深坑。三代产物 7/27 空白页 + 占位符泄漏是必然结果而非偶然。

**2.4 QA 层（quality_validator.py）**
- 检查项偏「结构」（页数、越界、重叠、图片可读），**没有占位符残留检查**（「方案 A」「暂无内容」直接过关）。
- `qa_passed=false` 时 `deliverable` 照样 true，报警进 warnings 但 agent 和用户都看不到严重性——QA 是仪表盘不是门禁。

**2.5 回退路径（generator.py + theme.py + layouts/*.py）**
- 18 套主题预设配色角色错乱（`bg` 是高饱和红/橙，`light` 也是红）；布局只支持 bullets/stat/comparison/timeline 四种（toc/table 落空成空白页）；从不设置文件元数据（日期停在 2013）；坐标硬编码重叠。**用户已明确：此路径废弃**。

## 3. 业界方案调研

### 3.1 OpenAI Codex 官方 slides skill——「LLM 写 pptxgenjs 代码 + 工具化自检」

- **明文规定**：deck 生成用 PptxGenJS 写 JS（python-pptx 仅限检查类任务），同时交付 `.pptx` 和源码 `.js`。
- 关键机制：不靠 LLM 手算坐标——bundled `pptxgenjs_helpers`（`autoFontSize`/`calcTextBox`/`warnIfSlideHasOverlaps`/`warnIfSlideElementsOutOfBounds`）做排版兜底；交付前必须过 `render_slides.py`（逐页栅格化 PNG）+ `slides_test.py`（越界检测）+ contact sheet 拼图 + LibreOffice 字体替换检测，**修复所有警告才准交付**。
- 启示：①「渲染成图→程序化检查」是消灭空页/溢出/占位符的关键闭环；②上限取决于模型审美，无模板体系。

### 3.2 Anthropic 官方 pptx skill——「设计红线写进规则 + 模板克隆填充」

（本机 ZCode 插件内有完整实现，全文已解剖，是最权威的参考。）

- **新建 deck**：直写 pptxgenjs 脚本，SKILL.md 用一整套**设计红线**约束模型：配色 BACKGROUND(60-70%)/PRIMARY/ACCENT(5-10%) 三角色模型、深浅「三明治」结构、每页一个视觉焦点、每 deck 一个贯穿 motif、禁「标题下装饰线 / 色条 / 侧边条纹」（明文标注为 AI 生成痕迹）、字号/边距/对比度红线、CJK 字体规范（微软雅黑/等线，忌 Light 字重）。
- **模板路线**（与我们的模板解析完全不同的做法）：先程序化盘点模板（每个文本 shape 的位置/字号/**原文长度**），按 shape 特征分类每页**角色**（cover/section/stats/quote/content）；**「budget = 原文长度 × 1.1」**作为替换文案的硬上限；XML 级克隆页面再逐页替换；删页从高索引开始。
- **QA 三件套**：markitdown 抽全文 grep `lorem/TODO/[insert/xxx` **占位符残留**；代码级溢出/重叠检测；渲染成图后视觉抽查。「Template slots ≠ source items」明文提醒。

### 3.3 Presenton（开源，与我们场景最贴近）

- 管线：大纲生成 → LLM 从模板 `layouts.json` **布局集中选布局** → 内容映射进模板 structured fields → 导出**全可编辑 PPTX/PDF**。
- 模板用 **HTML + Tailwind CSS** 编写（内置 Momentum/Dynamic/Executive 等多套），提供「把你的 PPTX 转成 AI-ready 模板」的**一次性转换**能力。
- 启示：①「LLM 只做布局选择 + 槽位填充，样式完全交给模板」是保证下限的正确分工；②用户模板走「一次性转成受控表示」而非每次生成时解析。

### 3.4 Gamma / Kimi PPT / WPS AI / 百度文库（商用 AI 原生产品）

- 共性架构四模块：**内容生成（LLM）→ 模板匹配 → 规则引擎排版 → 格式输出**；模板库百套级、人工设计，LLM 只碰内容 JSON。
- Gamma：outline-first（先出可编辑大纲、用户确认再生成全稿）；卡片式 web 原生排版，导出 PPTX 是事后转换。
- **没有一家做「任意用户模板解析」**——样式与内容强制分离，排版由模板/规则引擎保证下限。

### 3.5 html2pptx（Anthropic 旧版/社区维护）——设计自由度与可编辑性兼顾

- **受控 HTML DSL**（只允许 p/h1-h6/ul/ol，禁裸文本/禁渐变/禁非 web-safe 字体，body 精确匹配版式尺寸）→ Playwright 渲染量坐标 → **逐元素映射**为原生 pptx 对象（文本框/形状/图片/原生图表），非截图。
- 适合「设计要求高的新 deck」；我们的 [前次调研](html-to-pptx-conversion-research.md) 已验证过其映射保真度。代价：DSL 约束 + Playwright 依赖（镜像已有）。

### 3.6 模板解析路线的业界定论

「解析任意 pptx 模板 → 提取版式配色 → 自动填充」**无成熟开源方案、无商用先例**。公认坑：占位符属性走 layout→master 继承链（读到 None）、主题色藏在 theme1.xml、版式名不可靠、XML 手术需 defusedxml。业界的替代：Presenton 一次性转换 / Anthropic 缩略图选版式+XML 手术 / AiPPT 只用自家模板库。

### 3.7 WorkBuddy

腾讯桌面 AI 办公智能体（本地模型 + 私有知识库 + Skill 流），PPT 依托**腾讯文档引擎「人机双写」**，内部实现未公开。可确认的是：PPT 能力交给专业文档引擎而非自研渲染管线——与「商用产品不做任意模板解析」的结论一致。

### 3.8 对比总表

| 方案 | 中间表示 | 样式来源 | 模板策略 | 可编辑性 | 对我们的适用度 |
|---|---|---|---|---|---|
| Codex slides | LLM 直写 pptxgenjs JS | 模型审美+helper 约束 | 无 | 全原生 | 参考其 QA 闭环与 helper 思路 |
| Anthropic pptx skill | pptxgenjs 脚本 / OOXML 手术 | 设计红线规则 | 缩略图选版式+克隆填充 | 原生 | **设计红线与模板克隆直接移植** |
| html2pptx | 受控 HTML DSL | HTML/CSS | 无 | 文本图表原生 | P3 可选：高设计自由度路线 |
| Presenton | structured fields + layouts.json | HTML+Tailwind 模板族 | 预制库+PPTX 一次性转换 | 全可编辑 | **主架构对标对象** |
| Gamma/Kimi/WPS | 大纲→版式槽位 | 百套级人工模板库 | 只用自家库 | 导出可编辑 | 「内容样式分离+模板保底」同构 |
| 我们的模板解析 | 占位符逐次匹配 | 用户任意模板 | 任意 | 理论可编辑 | **业界无先例，坑已实证，废弃** |

## 4. 目标架构建议

对课题六个问题逐一回答：

**4.1 内容结构化（大纲→每页内容→内容+样式）**

采用 **「大纲先行 + 单层规划」**（Gamma 的 outline-first 思想 + 我们 agent 天然的对话确认能力）：
- planner 第一次调用只产**页级大纲**（每页：页面意图、layout 选择、一句话摘要），在会话中呈现给用户/上层 agent 确认（agent 工具返回大纲让主 agent 决定继续，即交互式确认）；
- 第二次按页填充内容槽位。规划 schema 由**布局族的槽位定义**驱动（Presenton 模式），LLM 只选 `layout_id` + 填槽，**槽位缺失时校验失败→定向重试**，而不是渲染层兜占位符。

**4.2 与 pptxgenjs 框架的配合**

保留现有 `SlideDeckSpec`/renderer-node 骨架（已稳定、可编辑、QA 产物链完整），重做其上的**设计系统层**：
- 主题 token 化：palette（BG/PRIMARY/ACCENT/TEXT/MUTED 三角色模型）+ 字体对 + 卡片处理（tint/shadow）+ motif，全部成为 spec 的一等字段；
- 布局变体：每种布局 2-3 个变体（如 bullets 的 standard/split/cards），由主题+内容类型驱动选择，消灭「同版式连排」的 AI 味；
- 排版兜底代码化（Codex helper 思路）：字号自适应、溢出/重叠检测已在 renderer QA，补强为交付门禁。

**4.3 内置模板库（要，且是质量下限的来源）**

- **建议内置 6-10 套主题**（每套 = palette + 字体 + 版式偏好 + motif），按 deck 主题由 agent 选择，并把选择理由呈现给用户（「已选用『商务深蓝』主题」），对齐商用产品「模板保底」的共识；
- 主题设计直接采用 Anthropic skill 的设计红线作为验收标准（三明治结构、焦点、禁装饰线/色条、CJK 规范）；首版可人工精修 3 套（商务/科技/汇报），验证质量增益后再扩。

**4.4 上传模板解析（统一到同一管线，废弃逐次解析）**

- 路线改为 **「一次性模板转译」**（Presenton 式）：用户 pptx →（一次转换）→ 提取主题 token（theme1.xml 配色/字体）+ 每页角色盘点 + 槽位容量清单 → 生成一份「AI-ready 模板」（受控布局 spec）→ 之后所有生成走同一 spec 管线；
- 转换用 Anthropic 模板克隆的成熟机制：shape 盘点、角色分类、`原文长度×1.1` budget、XML 级克隆（高索引删页、defusedxml）；
- **短期（P1 前）**：模板路径降级为「只提取配色/字体注入内置布局」，不再尝试版式复用——先消灭空白页和占位符泄漏。

**4.5 QA 从仪表盘变门禁**

- 新增**占位符残留检查**：成片 markitdown 抽文 + grep（`暂无内容/方案 A/lorem/TODO/[insert/表格/图表/内容` 单字标题）——直接对应本次三代产物的泄漏；
- `qa_passed=false` 时**不再静默交付**：工具结果向 agent 返回结构化失败（含违规页清单），由 agent 修复重生成（渲染重试成本秒级），最多 N 次后才降级交付并明示警告；
- 中期补栅格化视觉回归（LibreOffice→pdftoppm 依赖镜像已具备，`preview_status: failed` 问题另行排查）。

**4.6 python-pptx 回退与模板解析（废弃）**

- 回退路径③（generator.py/theme.py/layouts）**直接删除**（用户已裁定）；pptxgenjs 不可用时返回明确错误让 agent 换方案（如 HTML 课件）而非产出垃圾；
- template_analyzer 逐次解析路径被 4.4 的「一次性转译」替代后删除。

## 5. 分阶段落地路线

| 阶段 | 内容 | 预期收益 | 量级 |
|---|---|---|---|
| **P0 止血** | 删 spec_builder 全部占位符默认值（缺内容→校验错误）；QA 增占位符残留检查；qa_passed=false 不静默交付；删 python-pptx 回退路径 | 消灭「渲染成功的垃圾」，agent 可自愈重试 | 小（天级） |
| **P1 设计系统** | 主题 token 化 + 3 套人工精修主题 + 布局变体 + 大纲先行两段式规划 | 「样式可以接受」的主要来源 | 中（1-2 周） |
| **P2 模板转译** | 用户 pptx 一次性转译成 AI-ready 模板（主题提取+角色盘点+budget 克隆），并入同一管线 | 模板路线从不可用到可用 | 中大 |
| **P3 可选增强** | html2pptx 受控 DSL 路线（高设计自由度需求）；主题库扩展到 6-10 套；栅格化视觉回归门禁 | 上限提升 | 按需 |

## 6. 结论速览

1. **业界共识**：LLM 只碰内容（选布局+填槽），样式由「人工设计的模板/主题 + 规则化排版」保证下限；「任意模板逐次解析」没有先例，我们的实证（7/27 空白页）与业界绕行一致——废弃。
2. **我们的三个致命伤**与业界解法一一对应：占位符泄漏→Anthropic 的 grep 残留门禁；千篇一律→Presenton 的模板族+布局变体；QA 失效→Codex 的「渲染成图+程序化检查不过不交付」。
3. **架构不必推倒**：SlideDeckSpec/renderer-node 骨架保留，重做设计系统层（主题 token+布局变体）+ 规划层（大纲先行+槽位校验）+ QA 门禁；模板路线改为一次性转译。
4. **最短路径到「能交付」**：P0（止血）+ P1（3 套精修主题）即可让默认产物达到「样式可以接受」；P2/P3 决定模板场景与上限。

---

*调研方法：三轮生产 trace 取证 + 产物 pptx 解剖 + 现有管线代码审读 + 业界公开资料检索（OpenAI/Anthropic skills、Presenton/Gamma/Kimi/WPS 公开架构、pptxgenjs 生态、python-pptx 模板解析社区实践）。本机 ZCode presentations 插件（Z.AI 版 Anthropic pptx skill）全文作为标杆方法论依据。*
