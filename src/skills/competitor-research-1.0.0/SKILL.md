---
name: competitor-research
description: 竞品研究技能 - 提供深度搜索策略、HTML报告模板和合并方案
metadata:
  version: 1.0.0
  author: system
---

# 竞品研究技能

本技能为竞品研究子智能体提供完整的领域知识，包括搜索策略、材料文件模板、HTML 报告设计系统和合并方案。

---

## 1. 使用场景

**何时使用此技能**：
- 竞品研究：用户要求研究某个竞争对手的产品或公司
- 竞品分析：用户需要对竞品进行系统性分析（产品功能、定价、市场口碑等）
- 竞品报告生成：用户需要生成可视化的竞品分析报告
- 对比研究：用户要求对比分析多个竞品

**关键词触发**：
- 竞品研究 / 竞品分析 / 竞品报告
- 竞争对手分析 / 对手研究
- 竞品对比 / 产品对比
- 市场分析 / 行业分析

---

## 2. 搜索策略模板

### 2.1 研究维度与关键词模板

每个研究维度提供一组搜索关键词模板。`{竞品名}` 和 `{我方产品}` 由子智能体根据用户输入替换。

#### 维度一：基础信息

| 序号 | 搜索关键词 | 目标 |
|------|-----------|------|
| 1 | `{竞品名} 公司介绍 官网` | 公司背景、成立时间、团队规模 |
| 2 | `{竞品名} 产品线 功能列表` | 产品线全貌、核心功能概览 |
| 3 | `{竞品名} 定价 版本对比` | 价格体系、版本差异 |
| 4 | `{竞品名} 目标客户 市场定位` | 客户群体、市场定位 |
| 5 | `{竞品名} 融资 估值 投资` | 融资历程、投资者背景 |

**搜索执行建议**：
- 先用 `web_search` 进行广度搜索，获取概览信息
- 发现重要页面（如官网、定价页）后，用 `browser_get_content` 采集完整内容
- 国内竞品补充使用 `use_skill("baidu-search")` 进行中文搜索
- 关键数据（定价、市场份额）至少从 2 个不同来源确认

#### 维度二：产品分析

| 序号 | 搜索关键词 | 目标 |
|------|-----------|------|
| 1 | `{竞品名} 核心功能 产品分析` | 功能深度分析 |
| 2 | `{竞品名} 技术架构 差异化` | 技术栈、技术优势 |
| 3 | `{竞品名} 产品更新 路线图` | 产品迭代节奏、未来方向 |
| 4 | `{竞品名} 用户体验 界面设计` | UI/UX 分析 |
| 5 | `{竞品名} API 集成 开放平台` | 生态能力、集成友好度 |

**搜索执行建议**：
- 产品功能页是重点采集目标，用 `browser_get_content` 获取完整功能列表
- 技术博客或开发者文档能反映技术实力，优先采集
- 产品更新日志（changelog）是推断路线图的直接依据

#### 维度三：市场口碑

| 序号 | 搜索关键词 | 目标 |
|------|-----------|------|
| 1 | `{竞品名} 用户评价 优缺点` | 用户真实反馈 |
| 2 | `{竞品名} 知乎 口碑` | 中文社区深度评价 |
| 3 | `{竞品名} G2 评分 review` | 国际评价平台 |
| 4 | `{竞品名} 客户案例 成功案例` | 知名客户、应用场景 |
| 5 | `{竞品名} 吐槽 问题 缺陷` | 负面反馈、已知问题 |

**搜索执行建议**：
- 知乎、小红书、V2EX 等社区有大量真实用户评价，优先采集
- G2、Capterra 等国际评价平台提供量化的评分和对比
- 注意区分真实用户评价和软文/广告

#### 维度四：竞争态势

| 序号 | 搜索关键词 | 目标 |
|------|-----------|------|
| 1 | `{竞品名} 行业竞争格局` | 行业整体竞争态势 |
| 2 | `{竞品名} 对比 `{我方产品}` | 直接竞品对比 |
| 3 | `{竞品名} SWOT 分析` | 优劣势分析 |
| 4 | `{竞品名} 市场份额 排名` | 市场地位 |
| 5 | `{竞品名} 竞争优势 核心壁垒` | 护城河分析 |

**搜索执行建议**：
- 行业报告（艾瑞、IDC、Gartner）提供权威的市场数据
- 竞品官网的"对比"页面是直接对比信息的来源
- 如用户未提供"我方产品"名称，跳过包含 `{我方产品}` 的搜索

#### 维度五：最新动态

| 序号 | 搜索关键词 | 目标 |
|------|-----------|------|
| 1 | `{竞品名} 最新新闻 融资` | 近期重大事件 |
| 2 | `{竞品名} 招聘 战略方向` | 从招聘推测战略方向 |
| 3 | `{竞品名} 新功能 发布` | 最新产品动态 |
| 4 | `{竞品名} 高管变动 人事` | 管理层变化 |
| 5 | `{竞品名} 2025 2026 发展` | 年度趋势和展望 |

**搜索执行建议**：
- 使用 `freshness` 参数限制搜索时间范围（建议 `pm` 即过去一个月）
- 招聘信息是推断战略方向的有效指标（新开岗位类型暗示业务扩张方向）
- 关注融资新闻，融资轮次和金额反映市场信心

### 2.2 搜索策略原则

| 原则 | 说明 |
|------|------|
| 多角度 | 同一主题用不同关键词搜索 2-3 次，确保覆盖面 |
| 递进式 | 先广度搜索获取概况，再深度搜索特定方面 |
| 交叉验证 | 关键数据（定价、市场份额等）至少从 2 个来源确认 |
| 溯源记录 | 每条信息标注来源 URL 和搜索时间 |
| 中文优先 | 国内竞品优先搜索中文信息源，国际竞品补充英文搜索 |
| 时效性 | 优先采集近期信息，注意信息发布时间 |

### 2.3 搜索工具选择

| 场景 | 工具 | 说明 |
|------|------|------|
| 快速信息检索 | `web_search` | Tavily 搜索，返回结构化结果摘要 |
| 具体页面内容采集 | `browser_get_content` | 直接打开 URL 返回页面 markdown（最长 50000 字符） |
| 需要交互的页面 | `browser_automation` | LLM 驱动的浏览器操作（点击、翻页、填表） |
| 中文搜索补充 | `use_skill("baidu-search")` | 百度 AI 搜索，适合国内竞品 |

**典型组合**：先用 `web_search` 搜索获取相关 URL 列表，再用 `browser_get_content` 逐个打开采集完整内容。需要交互时改用 `browser_automation`。

---

## 3. HTML 页面模板

### 3.1 CSS 设计系统

所有 HTML 报告页面共享以下 CSS 变量和通用样式。

**CSS 变量**：

```css
:root {
  --primary: #1a56db;
  --primary-light: #e8eefb;
  --text-primary: #111827;
  --text-secondary: #6b7280;
  --border: #e5e7eb;
  --bg-page: #ffffff;
  --bg-section: #f9fafb;
  --accent-green: #059669;
  --accent-red: #dc2626;
  --accent-yellow: #d97706;
}
```

**通用样式（所有页面共享）**：

```css
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif;
  color: var(--text-primary);
  background: var(--bg-page);
  line-height: 1.6;
  padding: 48px;
  max-width: 900px;
  margin: 0 auto;
}

/* 页面头部 */
header {
  margin-bottom: 36px;
  padding-bottom: 20px;
  border-bottom: 2px solid var(--primary);
}
.report-meta {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
header h1 {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}
header h2 {
  font-size: 18px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-top: 8px;
}

/* 正文段落 */
main p {
  margin-bottom: 16px;
  font-size: 15px;
  color: var(--text-primary);
}
main h2 {
  font-size: 22px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 32px 0 16px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
main h3 {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 24px 0 12px;
}

/* 表格 */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 16px 0;
  font-size: 14px;
}
th, td {
  padding: 10px 14px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
th {
  background: var(--bg-section);
  font-weight: 600;
  color: var(--text-primary);
}
tr:hover { background: var(--primary-light); }

/* 信息卡片 */
.info-card {
  background: var(--bg-section);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 20px;
  margin: 16px 0;
}
.info-card .label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.info-card .value {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

/* 列表 */
ul, ol {
  padding-left: 24px;
  margin: 12px 0;
}
li {
  margin-bottom: 8px;
  font-size: 15px;
  line-height: 1.6;
}

/* 标签 */
.tag {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  margin: 2px 4px;
}
.tag-green { background: #d1fae5; color: var(--accent-green); }
.tag-red { background: #fee2e2; color: var(--accent-red); }
.tag-yellow { background: #fef3c7; color: var(--accent-yellow); }
.tag-blue { background: var(--primary-light); color: var(--primary); }

/* 引用块（来源标注） */
blockquote {
  border-left: 3px solid var(--primary);
  padding: 8px 16px;
  margin: 12px 0;
  background: var(--primary-light);
  color: var(--text-secondary);
  font-size: 13px;
}

/* 网格布局（用于信息卡片组） */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 16px;
  margin: 16px 0;
}

/* 进度条 / 评分条 */
.bar-container {
  background: var(--bg-section);
  border-radius: 4px;
  height: 20px;
  margin: 4px 0;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--primary);
}

/* 页面底部 */
footer {
  margin-top: 48px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
}

/* SWOT 网格（竞争态势页专用） */
.swot-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin: 20px 0;
}
.swot-cell {
  border-radius: 8px;
  padding: 20px;
}
.swot-strengths { background: #d1fae5; border: 1px solid #6ee7b7; }
.swot-weaknesses { background: #fee2e2; border: 1px solid #fca5a5; }
.swot-opportunities { background: #dbeafe; border: 1px solid #93c5fd; }
.swot-threats { background: #fef3c7; border: 1px solid #fcd34d; }
.swot-cell h4 {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 10px;
}
.swot-cell ul { padding-left: 18px; }
.swot-cell li { font-size: 14px; margin-bottom: 6px; }
```

### 3.2 页面结构模板

每个 HTML 页面是独立的完整 HTML 文件，遵循以下结构：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>竞品分析报告 - {页面标题} - {竞品名称}</title>
  <style>
    /* CSS 变量 */
    :root { ... }
    /* 通用样式 */
    ...
    /* 页面特有样式 */
    ...
  </style>
</head>
<body>
  <header>
    <div class="report-meta">竞品分析报告 | {竞品名称} | 第 {N} 页</div>
    <h1>{页面标题}</h1>
  </header>
  <main>
    {页面具体内容}
  </main>
  <footer>
    <p>生成时间: {timestamp} | 数据来源: 互联网公开信息 | 仅供内部参考</p>
  </footer>
</body>
</html>
```

### 3.3 各页面布局指引

| 页面 | 布局要点 |
|------|----------|
| 封面 | 居中大标题 + 竞品名称 + 研究日期 + 研究范围，无 header/footer，使用全屏背景色 |
| 公司概况 | 顶部 3-4 个信息卡片（成立时间、规模、融资等），下方时间线或段落 |
| 产品功能分析 | 功能卡片网格，每个功能一个卡片（名称 + 描述 + 标签） |
| 定价策略 | 定价对比表格，版本横向对比，性价比评估 |
| 用户口碑 | 评分概览（数字 + 评分条），正面/负面标签云，典型评价引用 |
| 竞争态势 | SWOT 四宫格，优劣势对比表格 |
| 最新动态 | 时间线布局，按时间倒序排列事件 |
| 总结与建议 | 关键发现列表 + 行动建议卡片 |

### 3.4 封面页特殊样式

封面页不使用通用 header/footer，使用全屏居中布局：

```css
/* 封面页专用样式（覆盖通用样式） */
body.cover-page {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  max-width: none;
  padding: 0;
  background: linear-gradient(135deg, #1a56db 0%, #3b82f6 100%);
  color: #ffffff;
}
.cover-page .cover-content {
  text-align: center;
  max-width: 600px;
  padding: 40px;
}
.cover-page h1 {
  font-size: 42px;
  font-weight: 700;
  margin-bottom: 16px;
  color: #ffffff;
  border: none;
}
.cover-page .cover-subtitle {
  font-size: 20px;
  opacity: 0.9;
  margin-bottom: 40px;
}
.cover-page .cover-meta {
  font-size: 15px;
  opacity: 0.75;
  line-height: 2;
}
.cover-page .cover-divider {
  width: 80px;
  height: 3px;
  background: rgba(255,255,255,0.5);
  margin: 30px auto;
}
```

---

## 4. 材料文件 generate_prompt 模板

每份材料文件通过 `write(generate_prompt=...)` 内部生成，搜索结果原文传入 prompt，由 LLM 整理为结构化的 Markdown 文件。

### 4.1 01_基础信息.md

```
你是一个竞品研究分析师。请根据以下搜索结果，整理为结构化的竞品基础信息 Markdown 文件。

## 要求
1. 按以下结构组织内容（各节可选，有信息就写，没有就省略该节）
2. 每条信息标注来源 URL 和搜索时间
3. 关键数据（定价、市场份额等）至少从 2 个来源确认，标注交叉验证结果
4. 不确定的信息标注 [置信度:低/中/高]
5. 使用 Markdown 表格呈现结构化数据（定价、版本对比等）
6. 信息按重要性排序，最重要的信息放在前面

## 结构
### 公司概况
（公司全称、成立时间、总部、团队规模、核心团队背景）

### 发展历程
（关键里程碑，按时间线排列）

### 产品线
（主要产品/模块列表，每个产品一句话说明）

### 目标客户
（客户群体、行业分布、典型客户）

### 定价策略
（各版本价格、功能差异，用表格呈现）

### 市场份额与排名
（行业排名、市场份额数据、竞争地位）

## 搜索结果
{搜索结果原文}
```

### 4.2 02_产品分析.md

```
你是一个竞品研究分析师。请根据以下搜索结果，整理为结构化的竞品产品分析 Markdown 文件。

## 要求
1. 按以下结构组织内容（各节可选，有信息就写，没有就省略该节）
2. 每条信息标注来源 URL 和搜索时间
3. 功能描述要具体（不只是"支持XX"，要描述怎么支持、效果如何）
4. 技术细节尽量保留（架构、协议、性能指标等）
5. 不确定的信息标注 [置信度:低/中/高]

## 结构
### 核心功能详解
（每个核心功能单独一个小节，包括功能描述、实现方式、用户价值）

### 技术架构
（技术栈、部署方式、集成能力、开放 API）

### 产品差异化
（与同类产品的关键差异点）

### 用户体验分析
（界面设计、交互方式、学习曲线）

### 产品更新与路线图
（近期更新、版本历史、未来方向）

## 搜索结果
{搜索结果原文}
```

### 4.3 03_市场口碑.md

```
你是一个竞品研究分析师。请根据以下搜索结果，整理为结构化的竞品市场口碑 Markdown 文件。

## 要求
1. 按以下结构组织内容（各节可选，有信息就写，没有就省略该节）
2. 每条信息标注来源 URL 和搜索时间
3. 区分真实用户评价和媒体/分析师观点
4. 保留原始评价的关键语句（用引用格式）
5. 注意区分客观事实和主观评价
6. 不确定的信息标注 [置信度:低/中/高]

## 结构
### 用户评价概览
（整体口碑倾向：正面/中性/负面，评分数据）

### 正面评价汇总
（高频好评点，每条附代表性用户原话）

### 负面评价汇总
（高频差评点，每条附代表性用户原话）

### 媒体与分析师观点
（权威媒体的报道摘要、分析师评级）

### 客户案例
（典型客户、使用场景、效果评价）

### 社交媒体动态
（近期讨论热点、舆论趋势）

## 搜索结果
{搜索结果原文}
```

### 4.4 04_竞争态势.md

```
你是一个竞品研究分析师。请根据以下搜索结果，整理为结构化的竞品竞争态势分析 Markdown 文件。

## 要求
1. 按以下结构组织内容（各节可选，有信息就写，没有就省略该节）
2. 每条信息标注来源 URL 和搜索时间
3. 竞争分析要客观，避免主观判断
4. 优劣势对比要有具体论据支撑
5. SWOT 分析基于事实推导，不做无依据的推测
6. 不确定的信息标注 [置信度:低/中/高]

## 结构
### 行业竞争格局
（行业整体态势、主要玩家、市场集中度）

### 直接竞争对手对比
（与主要竞品的核心指标对比，用表格呈现）

### 优势分析
（该竞品的核心优势，每条附论据）

### 劣势分析
（该竞品的主要短板，每条附论据）

### SWOT 分析
（Strengths / Weaknesses / Opportunities / Threats，每项 3-5 条）

### 竞争威胁评估
（对我方产品的威胁程度：高/中/低，附理由）

## 搜索结果
{搜索结果原文}
```

### 4.5 05_最新动态.md

```
你是一个竞品研究分析师。请根据以下搜索结果，整理为结构化的竞品最新动态 Markdown 文件。

## 要求
1. 按以下结构组织内容（各节可选，有信息就写，没有就省略该节）
2. 每条信息标注来源 URL 和搜索时间
3. 信息按时间倒序排列（最新的在前）
4. 从招聘信息推断战略方向时，明确标注为推断
5. 关注近 3 个月的动态为主，更早的信息作为背景参考
6. 不确定的信息标注 [置信度:低/中/高]

## 结构
### 近期重要新闻
（融资、并购、合作、重大产品发布等）

### 产品最新更新
（近期版本更新、新功能发布）

### 招聘动态与战略推断
（正在招聘的岗位类型、推测的业务扩张方向）

### 高管变动
（关键人员加入/离开）

### 行业趋势与展望
（该竞品所在赛道的最新趋势）

## 搜索结果
{搜索结果原文}
```

---

## 5. HTML 报告页 generate_prompt 模板

每页 HTML 报告通过 `write(generate_prompt=...)` 内部生成。以下模板包含完整的 CSS 样式和内容指导，确保 LLM 生成的 HTML 风格统一。

### 5.1 封面页

```
你是一个专业的 HTML 报告设计师。请根据以下信息生成竞品分析报告的封面页 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 适合 A4 页面比例，居中布局
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案：主色 #1a56db，主色浅 #e8eefb

## 封面内容
- 报告标题：{竞品名称} 竞品分析报告
- 研究日期：{date}
- 研究范围：{scope}
- 我方产品：{our_product}

## CSS 样式要求（必须包含）
1. 全屏渐变背景：linear-gradient(135deg, #1a56db 0%, #3b82f6 100%)
2. 文字颜色：白色
3. 标题字号：42px，加粗
4. 副标题字号：20px
5. 日期和元信息字号：15px，透明度 0.75
6. 分隔线：80px 宽，3px 高，白色半透明
7. 整体垂直水平居中
8. 不使用 header 和 footer

## HTML 结构
- body 使用 class="cover-page"
- 内部一个 div.cover-content 容器
- 包含：h1 标题、div.cover-divider 分隔线、div.cover-subtitle 副标题、div.cover-meta 元信息
```

### 5.2 公司概况页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「公司概况」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案：:root 变量 --primary: #1a56db, --primary-light: #e8eefb, --text-primary: #111827, --text-secondary: #6b7280, --border: #e5e7eb, --bg-section: #f9fafb

## 页面结构
1. header：报告元信息 + 页面标题"公司概况"
2. 信息卡片组：使用 .card-grid 布局，3-4 个 .info-card（公司全称、成立时间、总部、团队规模等）
3. 发展历程：时间线或有序列表，展示关键里程碑
4. 产品线概览：表格或卡片列表，每个产品一行/一个卡片
5. 目标客户：段落描述 + 行业标签
6. footer：生成时间 + "仅供内部参考"

## 材料内容
{01_基础信息.md 内容}
```

### 5.3 产品功能分析页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「产品功能分析」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"产品功能分析"
2. 核心功能展示：使用 .card-grid 布局，每个功能一个 .info-card
   - 功能名称作为标题
   - 功能描述作为段落
   - 使用 .tag 标签标注功能类别（如 .tag-blue）
3. 技术架构：段落 + 架构要点列表
4. 差异化分析：对比表格或高亮卡片
5. 用户体验：简要评价 + 优缺点标签（.tag-green / .tag-red）
6. footer：生成时间 + "仅供内部参考"

## 材料内容
{02_产品分析.md 内容}
```

### 5.4 定价策略页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「定价策略」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"定价策略"
2. 定价总览：一个醒目的概览卡片，显示基础价格区间
3. 版本对比表格：表格列出版本名称、价格、核心功能差异
   - 使用 <table> 标签
   - 表头使用 <th>，背景色 var(--bg-section)
   - 行 hover 效果（var(--primary-light)）
4. 性价比分析：段落评价 + 标签标注性价比评级
5. 与竞品定价对比：如有数据，展示与同类产品的定价横向对比
6. footer：生成时间 + "仅供内部参考"

## 材料内容
{01_基础信息.md 中定价相关内容}
```

### 5.5 用户口碑与评价页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「用户口碑与评价」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"用户口碑与评价"
2. 口碑概览：信息卡片展示整体评分（如有数据）+ 口碑倾向标签
3. 正面评价：用 .tag-green 标签展示好评关键词，下方附代表性评价引用（<blockquote>）
4. 负面评价：用 .tag-red 标签展示差评关键词，下方附代表性评价引用
5. 媒体评价：引用格式展示权威媒体观点
6. 客户案例：卡片列表展示典型客户
7. footer：生成时间 + "仅供内部参考"

## 材料内容
{03_市场口碑.md 内容}
```

### 5.6 竞争态势分析页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「竞争态势分析」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"竞争态势分析"
2. SWOT 分析：使用 .swot-grid 四宫格布局
   - .swot-strengths（绿色背景）：优势
   - .swot-weaknesses（红色背景）：劣势
   - .swot-opportunities（蓝色背景）：机会
   - .swot-threats（黄色背景）：威胁
   - 每个格子包含 h4 标题 + ul 列表
3. 优劣势对比表：表格形式，左列"优势"右列"劣势"
4. 竞争威胁评估：一个醒目的 .info-card，标注威胁等级（高/中/低），附理由
5. footer：生成时间 + "仅供内部参考"

## 材料内容
{04_竞争态势.md 内容}
```

### 5.7 最新动态页

```
你是一个专业的 HTML 报告设计师。请根据以下材料生成竞品分析报告的「最新动态」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"最新动态"
2. 时间线布局：使用 CSS 创建竖线时间线效果
   - 左侧显示日期（使用 .text-secondary 颜色）
   - 右侧显示事件内容
   - 事件按时间倒序排列（最新在前）
3. 事件分类标签：融资用 .tag-blue、产品更新用 .tag-green、人事变动用 .tag-yellow
4. 战略推断部分：如有招聘信息，单独一个 section，明确标注"基于招聘信息的战略方向推断"
5. footer：生成时间 + "仅供内部参考"

## 材料内容
{05_最新动态.md 内容}
```

### 5.8 总结与建议页

```
你是一个专业的 HTML 报告设计师。请根据以下所有材料生成竞品分析报告的「总结与建议」页面 HTML。

## 设计要求
- 使用内联 CSS（所有样式在 <style> 标签中）
- 不包含任何 JavaScript
- 生成完整的 HTML 文档（<!DOCTYPE html> 开头）
- 配色方案同上

## 页面结构
1. header：报告元信息 + 页面标题"总结与建议"
2. 关键发现：编号列表，列出 5-8 条最重要的研究发现
   - 每条使用具体的客观数据或事实
   - 不使用模糊表述（如"产品还不错"），改为具体评价（如"定价低于行业均值 20%"）
3. 竞争力总评：一个 .info-card，给出综合竞争力度评价（强/中/弱），附理由
4. 行动建议：使用 .card-grid 布局
   - 每个建议一个 .info-card
   - 建议标题加粗，下方是具体说明
   - 按优先级排序（最重要排第一）
   - 使用 .tag-green（立即行动）、.tag-yellow（中期规划）、.tag-blue（长期关注）标注优先级
5. footer：生成时间 + "仅供内部参考" + 免责声明（数据来源为互联网公开信息，可能存在时效性偏差）

## 材料内容
{全部 5 份材料文件的摘要或关键结论}
```

---

## 6. 报告合并说明

### 6.1 合并流程

所有 HTML 子页面生成完毕后，通过 `skill_execute` 调用合并脚本合并为单一 HTML 文件（`full_report.html`）。

**合并步骤**：

1. 逐页生成 8 个子页面 HTML 文件（cover.html, company_overview.html, ...）
2. 每个子页面生成后通过 `register_download_file` 注册，用户可立即预览
3. 全部页面生成完毕后，调用合并脚本：

```
skill_execute(
  skill="competitor-research",
  command="python scripts/html_report_merger.py",
  content=json.dumps({
    "file_paths": [
      "storage/competitor_research/{session_id}/report/cover.html",
      "storage/competitor_research/{session_id}/report/company_overview.html",
      "storage/competitor_research/{session_id}/report/product_analysis.html",
      "storage/competitor_research/{session_id}/report/pricing.html",
      "storage/competitor_research/{session_id}/report/user_reviews.html",
      "storage/competitor_research/{session_id}/report/competition.html",
      "storage/competitor_research/{session_id}/report/latest_news.html",
      "storage/competitor_research/{session_id}/report/summary.html"
    ],
    "output_path": "storage/competitor_research/{session_id}/report/full_report.html",
    "title": "竞品分析报告 - {竞品名称}"
  })
)
```

4. 合并成功后，调用 `register_download_file` 注册完整报告：

```
register_download_file(
  file_path="storage/competitor_research/{session_id}/report/full_report.html",
  display_name="竞品分析报告_{竞品名称}.html"
)
```

合并脚本位于 `scripts/html_report_merger.py`，它将所有子页面的 `<body>` 内容提取出来，嵌入带导航框架的汇总页中。

### 6.2 合并方案：单文件嵌入

合并后的 `full_report.html` 将所有子页面的 `<body>` 内容嵌入汇总页的 `<div>` 容器中，通过 CSS 控制显示/隐藏，配合 JavaScript 实现翻页导航。

**汇总页结构**：
- 顶部导航栏：上一页/下一页按钮 + 报告标题 + 页码信息
- 左侧目录：8 个可点击的章节链接
- 主内容区：8 个 `.page-section` div，默认隐藏，通过 `.active` class 切换显示

**汇总页允许 JavaScript**（仅限翻页导航逻辑，不含动态内容加载或外部请求）。
**子页面 HTML 不包含 JavaScript**（纯 CSS + HTML）。

### 6.3 文件存储路径

```
storage/competitor_research/{session_id}/
├── 01_基础信息.md
├── 02_产品分析.md
├── 03_市场口碑.md
├── 04_竞争态势.md
├── 05_最新动态.md
├── meta.json
└── report/
    ├── cover.html
    ├── company_overview.html
    ├── product_analysis.html
    ├── pricing.html
    ├── user_reviews.html
    ├── competition.html
    ├── latest_news.html
    ├── summary.html
    └── full_report.html
```

---

## 7. 行为约束

### 7.1 搜索策略原则

| 原则 | 说明 | 违反示例 |
|------|------|----------|
| 事实驱动 | 所有分析必须基于搜索结果，不编造信息 | 没有搜索到定价信息就写"定价未知"，不猜测价格 |
| 溯源标注 | 每条信息标注来源 URL 和搜索时间 | 写"市场份额 30%"但不标注来源 |
| 交叉验证 | 关键数据至少 2 个来源确认 | 只看到一个论坛帖子就引用其中的数据 |
| 客观中立 | 不做无依据的主观判断 | 写"这个产品肯定会失败" |
| 时效意识 | 关注信息发布时间，优先近期信息 | 把 2023 年的定价当成当前定价 |

### 7.2 数据准确性要求

| 数据类型 | 最低来源数 | 处理方式 |
|----------|-----------|----------|
| 定价信息 | 2 个来源 | 交叉验证，不一致时标注差异 |
| 市场份额 | 2 个来源 | 标注数据年份和来源机构 |
| 功能列表 | 1 个来源（官网优先） | 标注采集时间 |
| 用户评价 | 3 条以上代表性评价 | 区分真实用户和媒体评价 |
| 融资信息 | 2 个来源 | 以官方公告为准 |

### 7.3 HTML 生成规范

| 规范 | 说明 |
|------|------|
| 必须使用 `write(generate_prompt=...)` | 禁止先调 `content_generate` 再调 `write` 的两步模式 |
| 内联 CSS | 所有样式在 `<style>` 标签中，不依赖外部资源 |
| 不含 JavaScript | 子页面 HTML 不包含任何 `<script>` 标签 |
| 完整 HTML 文档 | 以 `<!DOCTYPE html>` 开头，包含 `<html>`、`<head>`、`<body>` |
| CSS 变量一致 | 使用第 3 节定义的 CSS 变量和通用样式 |
| 中文内容 | 页面标题、标签、注释使用中文 |
| 响应式 | 适配不同屏幕宽度，使用 max-width 和百分比布局 |
| 语义化 HTML | 使用 `<header>`、`<main>`、`<footer>`、`<section>` 等语义标签 |
