# 竞品研究子智能体 — 设计文档

> 版本: v1.0 | 创建: 2026-05-19 | 状态: 待审核

## 一、设计思路

### 核心理念

竞品研究子智能体的核心是**深度搜索研究**。用户指定竞品后，子智能体通过多轮、多角度的互联网搜索，系统性地收集竞品信息，将中间成果保存为材料文件，最终基于这些材料生成结构化的竞品分析报告。

```
用户指定竞品
    │
    ▼
┌─────────────────────────────────┐
│  阶段一：深度搜索研究             │
│  多轮多角度搜索竞品信息           │
│  每轮搜索结果整理为材料文件       │
└─────────────┬───────────────────┘
              │ 中间材料文件（.md）
              ▼
┌─────────────────────────────────┐
│  阶段二：竞品分析报告生成         │
│  基于材料文件，逐页生成 HTML 报告 │
│  每页可独立预览                   │
│  最终合并为带导航的完整报告       │
└─────────────────────────────────┘
```

---

## 二、深度搜索研究流程

### 2.1 研究阶段概览

```
用户输入竞品名称/产品名
    │
    ▼
Step 1: 基础信息搜索（3-5 轮搜索）
    ├── 公司概况、发展历程
    ├── 产品线与核心功能
    ├── 目标客户与市场定位
    └── 定价策略
    │
    ▼  → file_write(generate_prompt=...) → 01_基础信息.md
    │
Step 2: 产品深度分析（3-5 轮搜索）
    ├── 核心功能详解
    ├── 技术架构与差异化
    ├── 用户界面/体验分析
    └── 产品更新与路线图
    │
    ▼  → file_write(generate_prompt=...) → 02_产品分析.md
    │
Step 3: 市场与口碑（3-5 轮搜索）
    ├── 用户评价与口碑（知乎、小红书、G2 等）
    ├── 媒体报道与分析师观点
    ├── 社交媒体动态
    └── 客户案例与合作
    │
    ▼  → file_write(generate_prompt=...) → 03_市场口碑.md
    │
Step 4: 竞争态势（2-3 轮搜索）
    ├── 行业竞争格局
    ├── 与我方产品的直接对比
    ├── 优劣势对比
    └── 威胁与机会
    │
    ▼  → file_write(generate_prompt=...) → 04_竞争态势.md
    │
Step 5: 最新动态（2-3 轮搜索）
    ├── 近期新闻与融资
    ├── 招聘信息（推测战略方向）
    ├── 产品最新更新
    └── 高管变动
    │
    ▼  → file_write(generate_prompt=...) → 05_最新动态.md
    │
    ▼
汇总所有材料 → 逐页生成 HTML 报告 → 合并为完整报告
```

### 2.2 搜索策略

每轮搜索遵循以下原则：

| 原则 | 说明 |
|------|------|
| **多角度** | 同一主题用不同关键词搜索 2-3 次，确保覆盖面 |
| **递进式** | 先广度搜索获取概况，再深度搜索特定方面 |
| **交叉验证** | 关键数据（定价、功能）至少从 2 个来源确认 |
| **溯源记录** | 每条信息标注来源 URL 和搜索时间 |
| **中文优先** | 国内竞品优先搜索中文信息源，国际竞品补充英文搜索 |

**搜索工具选择**：

| 场景 | 工具 | 说明 |
|------|------|------|
| 快速信息检索 | `web_search` | Tavily 搜索，返回结构化结果摘要 |
| 具体页面内容采集 | `browser_get_content` | 直接打开 URL 返回页面 markdown（最长 50000 字符），适合采集文章、报告等静态页面 |
| 需要交互的页面采集 | `browser_automation` | LLM 驱动的浏览器操作（点击、翻页、填表），内部提取页面内容后返回 LLM 总结 |
| 中文搜索补充 | `use_skill("baidu-search")` | 百度 AI 搜索，适合国内竞品 |

> **典型组合**：先用 `web_search` 搜索获取相关 URL 列表，再用 `browser_get_content` 逐个打开采集完整内容。如果页面需要交互（如点击展开、登录后查看），则改用 `browser_automation`。

### 2.3 材料文件格式

每份材料文件是 Markdown 格式，通过 `file_write(generate_prompt=...)` 生成并存储到会话专属目录。搜索结果不直接作为材料保存，而是由 `file_write` 内部调用 LLM 整理为结构化的 Markdown 文件。

```
storage/competitor_research/{session_id}/
├── 01_基础信息.md
├── 02_产品分析.md
├── 03_市场口碑.md
├── 04_竞争态势.md
├── 05_最新动态.md
└── meta.json          # 研究元数据
```

**材料生成方式**（与 HTML 报告一致，均使用 `file_write` 内部生成）：

```
file_write(
  file_path="storage/competitor_research/{session_id}/01_基础信息.md",
  generate_prompt="根据以下搜索结果，整理为结构化的竞品基础信息 Markdown 文件：\n{搜索结果原文}",
  content_type="report"
)
→ 内部 LLM 整理 → 写入 .md 文件 → 只返回文件路径
```

> **关键**：材料整理和 HTML 报告生成都通过 `file_write(generate_prompt=...)` 完成，所有长文本内容（Markdown 材料、HTML 报告）都不进入 Agent 上下文。

**材料文件示例（01_基础信息.md）**：

```markdown
# 竞品基础信息 — 飞书

> 搜索时间: 2026-05-19 14:30 | 竞品: 飞书 (Feishu)

## 公司概况

- **所属公司**: 字节跳动
- **成立时间**: 2016 年（Lark 国际版）
- **定位**: 企业协作平台（IM + 文档 + 会议 + 项目管理）
- **目标客户**: 中大型企业，尤其是互联网/科技公司
- **市场份额**: 国内企业协作工具市场排名第二（来源：艾瑞咨询 2025）

> 来源: https://www.feishu.cn/about | 搜索时间: 2026-05-19

## 产品线

1. **飞书 Office**: 即时通讯 + 文档 + 日历 + 会议
2. **飞书 OKR**: 目标管理
3. **飞书人事**: HR 管理
4. **飞书审批**: 工作流引擎
5. **飞书项目**: 项目管理

> 来源: https://www.feishu.cn/product | 搜索时间: 2026-05-19

## 定价策略

| 版本 | 价格 | 适用规模 |
|------|------|----------|
| 免费版 | 0 元 | 50 人以下小团队 |
| 商业版 | 360 元/人/年 | 中型企业 |
| 旗舰版 | 联系销售 | 大型企业 |

> 来源: https://www.feishu.cn/pricing | 搜索时间: 2026-05-19
> 交叉验证: https://www.zhihu.com/question/xxx | 与官网定价一致
```

**meta.json 格式**：

```json
{
  "competitor_name": "飞书",
  "our_product": "XX协作平台",
  "created_at": "2026-05-19T14:30:00",
  "status": "researching",
  "completed_steps": ["基础信息", "产品分析"],
  "pending_steps": ["市场口碑", "竞争态势", "最新动态"]
}
```

---

## 三、HTML 报告生成

### 3.1 逐页生成 + 合并机制

报告采用**逐页生成，最终合并**的模式：

1. **逐页生成**：每生成一页 HTML，立即注册为可下载文件，用户可以逐页预览。不需要等全部页面生成完毕。
2. **最终合并**：所有页面生成完毕后，生成一个**汇总 HTML**，通过 iframe 嵌入各子页面，形成完整的可翻页报告。

> **为什么要逐页生成？** 完整报告的 HTML 体积大（含丰富样式和数据可视化），单次 LLM 调用可能无法生成完整内容。逐页生成降低单次生成难度，最终合并保证用户能获得完整报告。

```
材料文件汇总
    │
    ▼
file_write(generate_prompt="封面", file_path="cover.html") → 用户可预览第 1 页
    │
    ▼
file_write(generate_prompt="公司概况", file_path="overview.html") → 用户可预览第 2 页
    │
    ▼
... 逐页继续（HTML 不进入 Agent 上下文）
    │
    ▼
file_write(merge_files=[...], file_path="full_report.html") → 完整报告可预览
```

> **关键约束**：必须使用 `file_write(generate_prompt=...)` 内部生成 HTML，**禁止**先调 `content_generate` 再调 `file_write` 的两步模式。原因：HTML 内容会膨胀 Agent 上下文窗口，导致后续生成质量下降甚至上下文溢出。`file_write` 内部生成后只返回文件元信息，HTML 内容不泄露到对话上下文中。

### 3.1.1 合并方案：iframe 汇总页

合并方案采用**单页应用式汇总 HTML**，通过 iframe 嵌入各子页面，配合 JavaScript 实现翻页导航。

**为什么不直接拼接 HTML？**
- 各子页面是完整的 HTML 文档（含 `<!DOCTYPE html>`、`<head>`、`<body>`），直接拼接会产生无效 HTML
- iframe 嵌入保持各页面独立性，样式互不干扰
- 汇总页只需生成一次，体积小（导航框架 + iframe 标签），不增加 LLM 生成负担

**汇总 HTML 结构**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>竞品分析报告 - {竞品名称}</title>
  <style>
    /* 汇总页专用样式 — 导航栏 + iframe 容器 */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }

    .report-nav {
      position: fixed; top: 0; left: 0; right: 0;
      height: 48px; background: #1a56db; color: #fff;
      display: flex; align-items: center; padding: 0 24px;
      z-index: 100; gap: 16px;
    }
    .report-nav .title { font-size: 16px; font-weight: 600; }
    .report-nav .page-info { font-size: 14px; opacity: 0.8; }
    .nav-btn {
      background: rgba(255,255,255,0.2); border: none; color: #fff;
      padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 14px;
    }
    .nav-btn:hover { background: rgba(255,255,255,0.3); }
    .nav-btn:disabled { opacity: 0.4; cursor: default; }

    .toc-panel {
      position: fixed; top: 48px; left: 0; bottom: 0; width: 200px;
      background: #f9fafb; border-right: 1px solid #e5e7eb;
      overflow-y: auto; padding: 12px 0;
    }
    .toc-item {
      display: block; padding: 8px 16px; color: #374151; font-size: 13px;
      text-decoration: none; cursor: pointer; border-left: 3px solid transparent;
    }
    .toc-item:hover { background: #e8eefb; }
    .toc-item.active { background: #e8eefb; border-left-color: #1a56db; color: #1a56db; font-weight: 500; }

    .iframe-container {
      position: fixed; top: 48px; left: 200px; right: 0; bottom: 0;
    }
    .iframe-container iframe {
      width: 100%; height: 100%; border: none;
    }
  </style>
</head>
<body>
  <!-- 顶部导航栏 -->
  <div class="report-nav">
    <button class="nav-btn" id="prevBtn" onclick="prevPage()">◀ 上一页</button>
    <span class="title">竞品分析报告 · {竞品名称}</span>
    <button class="nav-btn" id="nextBtn" onclick="nextPage()">下一页 ▶</button>
    <span class="page-info" id="pageInfo">第 1 页 / 共 8 页</span>
  </div>

  <!-- 左侧目录 -->
  <div class="toc-panel" id="tocPanel">
    <a class="toc-item active" onclick="goToPage(0)">封面</a>
    <a class="toc-item" onclick="goToPage(1)">公司概况</a>
    <a class="toc-item" onclick="goToPage(2)">产品功能分析</a>
    <a class="toc-item" onclick="goToPage(3)">定价策略</a>
    <a class="toc-item" onclick="goToPage(4)">用户口碑与评价</a>
    <a class="toc-item" onclick="goToPage(5)">竞争态势分析</a>
    <a class="toc-item" onclick="goToPage(6)">最新动态</a>
    <a class="toc-item" onclick="goToPage(7)">总结与建议</a>
  </div>

  <!-- 内容区域 -->
  <div class="iframe-container">
    <iframe id="contentFrame" src="cover.html"></iframe>
  </div>

  <script>
    // 翻页逻辑（汇总页唯一允许的 JS）
    var pages = [
      { file: 'cover.html', title: '封面' },
      { file: 'company_overview.html', title: '公司概况' },
      { file: 'product_analysis.html', title: '产品功能分析' },
      { file: 'pricing.html', title: '定价策略' },
      { file: 'user_reviews.html', title: '用户口碑与评价' },
      { file: 'competition.html', title: '竞争态势分析' },
      { file: 'latest_news.html', title: '最新动态' },
      { file: 'summary.html', title: '总结与建议' }
    ];
    var current = 0;
    function goToPage(i) {
      current = i;
      document.getElementById('contentFrame').src = pages[i].file;
      document.getElementById('pageInfo').textContent = '第 ' + (i+1) + ' 页 / 共 ' + pages.length + ' 页';
      document.getElementById('prevBtn').disabled = (i === 0);
      document.getElementById('nextBtn').disabled = (i === pages.length - 1);
      var items = document.querySelectorAll('.toc-item');
      items.forEach(function(el, idx) { el.classList.toggle('active', idx === i); });
    }
    function prevPage() { if (current > 0) goToPage(current - 1); }
    function nextPage() { if (current < pages.length - 1) goToPage(current + 1); }
  </script>
</body>
</html>
```

**关键设计决策**：

| 决策 | 说明 |
|------|------|
| **iframe src 使用相对路径** | 子页面 HTML 与汇总页存放在同一目录下，使用相对路径（`cover.html`、`company_overview.html`）即可引用 |
| **汇总页允许 JavaScript** | 汇总页是程序化生成的（不是 LLM 生成），只包含翻页导航逻辑，不含动态内容，安全可控 |
| **子页面仍不含 JavaScript** | 子页面由 LLM 生成，保持纯 CSS + HTML，避免 LLM 生成不可控的脚本 |
| **iframe 使用相对路径的问题** | 由于前端通过 `/api/files/{file_id}/download` 加载文件，iframe 的相对路径引用会失效。解决方案见 3.1.2 |

### 3.1.2 文件服务方案

汇总页通过 `/api/files/{file_id}/download` 在前端 iframe 中预览时，iframe 内的相对路径（如 `src="cover.html"`）无法解析。

**解决方案：合并为单文件**

在生成汇总页时，将所有子页面的 HTML 内容**内联嵌入**汇总页中，使用 `srcdoc` 属性代替 `src`：

```html
<!-- 汇总页中用 srcdoc 替代 src -->
<iframe id="contentFrame"
  srcdoc="&lt;!DOCTYPE html&gt;&lt;html&gt;...封面页完整HTML...&lt;/html&gt;">
</iframe>
```

但 `srcdoc` 需要对 HTML 做实体编码，且内容量大时可能超出属性长度限制。

**推荐方案：合并为单一 HTML 文件**

最终合并时，不是生成"汇总页引用子页面"，而是**将所有子页面的 `<body>` 内容嵌入汇总页的 `<div>` 容器中，用 CSS 控制显示/隐藏**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>竞品分析报告 - {竞品名称}</title>
  <style>
    /* 汇总页导航样式（同 3.1.1） */
    /* ... */

    /* 子页面容器：默认隐藏，通过 JS 切换显示 */
    .page-section { display: none; padding: 48px; max-width: 900px; margin: 0 auto; }
    .page-section.active { display: block; }
  </style>
  <!-- 子页面的样式统一放在 head 中（去重合并） -->
  <style>
    /* 所有子页面共享的通用样式 */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
    /* ... 其他通用样式 ... */
  </style>
</head>
<body>
  <!-- 顶部导航栏 -->
  <div class="report-nav">
    <button class="nav-btn" id="prevBtn" onclick="prevPage()">◀ 上一页</button>
    <span class="title">竞品分析报告 · {竞品名称}</span>
    <button class="nav-btn" id="nextBtn" onclick="nextPage()">下一页 ▶</button>
    <span class="page-info" id="pageInfo">第 1 页 / 共 8 页</span>
  </div>

  <!-- 左侧目录 -->
  <div class="toc-panel" id="tocPanel">
    <a class="toc-item active" onclick="goToPage(0)">封面</a>
    <a class="toc-item" onclick="goToPage(1)">公司概况</a>
    <!-- ... 其他目录项 ... -->
  </div>

  <!-- 主内容区域：所有子页面的 body 内容依次排列 -->
  <div class="main-content">
    <div class="page-section active" id="page-0">
      <!-- 封面页的 <body> 内容 -->
      <h1>飞书竞品分析报告</h1>
      <!-- ... -->
    </div>
    <div class="page-section" id="page-1">
      <!-- 公司概况页的 <body> 内容 -->
      <h1>公司概况</h1>
      <!-- ... -->
    </div>
    <!-- ... 其他页面 ... -->
  </div>

  <script>
    // 翻页逻辑
    var total = document.querySelectorAll('.page-section').length;
    var current = 0;
    function goToPage(i) { /* ... */ }
    function prevPage() { /* ... */ }
    function nextPage() { /* ... */ }
  </script>
</body>
</html>
```

**合并过程**：

```
逐页生成完毕后：
  1. 读取所有子页面 HTML 文件
  2. 从每个子页面中提取 <body> 标签内的内容
  3. 将提取的内容放入汇总页对应的 <div class="page-section"> 中
  4. 将所有子页面的 <style> 标签内容去重合并到汇总页 <head> 中
  5. 生成汇总页并注册为可下载文件
```

> **合并操作由子智能体通过 `file_write` 工具完成**，不需要 LLM 参与。子智能体在提示词中定义合并脚本逻辑，逐个读取子页面、提取内容、写入汇总页。

### 3.1.3 用户体验流程

```
用户触发竞品研究
    │
    ├── 📄 第 1 页「封面」已生成 → 可预览（独立 HTML）
    ├── 📄 第 2 页「公司概况」已生成 → 可预览（独立 HTML）
    ├── 📄 第 3 页「产品功能分析」已生成 → 可预览（独立 HTML）
    ├── ...
    ├── 📄 第 8 页「总结与建议」已生成 → 可预览（独立 HTML）
    │
    └── 📋 完整报告已合并 → 可预览（带导航的汇总 HTML）
        ├── 左侧目录可跳转
        ├── 顶部翻页按钮
        └── 所有内容在一个文件中
```

### 3.2 报告页面结构

| 页码 | 标题 | 内容来源 |
|------|------|----------|
| 第 1 页 | **封面** | 竞品名称、研究日期、我方产品名 |
| 第 2 页 | **公司概况** | 01_基础信息.md → 公司背景、发展历程、规模 |
| 第 3 页 | **产品功能分析** | 02_产品分析.md → 核心功能、差异化、技术架构 |
| 第 4 页 | **定价策略** | 01_基础信息.md → 定价对比、性价比分析 |
| 第 5 页 | **用户口碑与评价** | 03_市场口碑.md → 情感分布、高频关键词 |
| 第 6 页 | **竞争态势分析** | 04_竞争态势.md → SWOT、优劣势对比 |
| 第 7 页 | **最新动态** | 05_最新动态.md → 近期新闻、战略方向推断 |
| 第 8 页 | **总结与建议** | 全部材料 → 综合建议、行动项 |

> 页数可根据竞品复杂度灵活调整。简单竞品可合并为 4-5 页，复杂竞品可拆分为 10+ 页。

### 3.3 HTML 页面设计规范

每个 HTML 页面是**独立的完整 HTML 文件**，包含内联 CSS，可直接在浏览器中打开。

**设计要求**：
- **内联样式**：所有 CSS 内联在 `<style>` 标签中，不依赖外部资源
- **响应式**：适配不同屏幕宽度
- **专业排版**：适合打印，A4 页面比例
- **统一风格**：所有页面共享相同的设计系统（配色、字体、间距）

**配色方案**（蓝色商务风）：

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

**页面模板结构**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>竞品分析报告 - {页面标题} - {竞品名称}</title>
  <style>
    /* 通用样式（所有页面共享） */
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text-primary);
      background: var(--bg-page);
      padding: 48px;
      max-width: 900px;
      margin: 0 auto;
    }
    /* ... 其他样式 ... */
  </style>
</head>
<body>
  <!-- 页面头部 -->
  <header>
    <div class="report-meta">竞品分析报告 | {竞品名称} | 第 {N} 页</div>
    <h1>{页面标题}</h1>
  </header>

  <!-- 正文内容 -->
  <main>
    {具体内容，由 LLM 根据材料生成}
  </main>

  <!-- 页面底部 -->
  <footer>
    <p>生成时间: {timestamp} | 仅供内部参考</p>
  </footer>
</body>
</html>
```

### 3.4 文件命名与存储

```
storage/competitor_research/{session_id}/report/
├── cover.html              # 封面（独立可预览）
├── company_overview.html   # 公司概况（独立可预览）
├── product_analysis.html   # 产品分析（独立可预览）
├── pricing.html            # 定价策略（独立可预览）
├── user_reviews.html       # 用户口碑（独立可预览）
├── competition.html        # 竞争态势（独立可预览）
├── latest_news.html        # 最新动态（独立可预览）
├── summary.html            # 总结建议（独立可预览）
└── full_report.html        # 合并后的完整报告（带导航）
```

每个独立 HTML 文件通过 `register_download_file` 注册后，前端可在 iframe 中逐页预览。`full_report.html` 是最终合并的完整报告，包含所有页面内容和导航功能。

---

## 四、前端预览支持

### 4.1 现状

前端 HTML 预览功能**已实现**，无需开发：

- `AttachmentPreviewPanel.vue` 已支持 `previewType === 'html'` 分支，使用 `sandbox="allow-same-origin"` 的 iframe 渲染预览
- `previewType` 计算属性已将 `html`/`htm` 从文本扩展名中移除，作为独立预览类型处理
- 后端所有 MIME 映射（`register_download_tool.py`、`main.py`、`text_file_writer.py`）均已包含 `.html` → `text/html`

### 4.2 完整报告预览

合并后的 `full_report.html` 包含导航 JavaScript（翻页、目录跳转），需要前端 iframe 允许脚本执行。

**当前 iframe sandbox 设置**：`sandbox="allow-same-origin"` — 不包含 `allow-scripts`，会阻止汇总页的翻页 JavaScript 执行。

**方案：为 HTML 报告类型单独放宽 sandbox**

```vue
<!-- AttachmentPreviewPanel.vue 中修改 -->
<iframe
  v-if="previewType === 'html'"
  :src="getFileUrl(attachment.file_id)"
  class="w-full h-full border-0"
  sandbox="allow-same-origin allow-scripts"
  @load="loading = false"
/>
```

> 安全风险可控：HTML 报告由系统生成（子页面纯 CSS + HTML 无脚本，汇总页只有翻页导航脚本），不含用户输入的脚本内容。

### 4.3 安全考虑

- 子页面 HTML（cover.html、company_overview.html 等）不包含任何 JavaScript
- 汇总页 full_report.html 只包含翻页导航的 JavaScript，不含动态内容加载或外部请求
- 文件通过后端 `/api/files/{file_id}` 路由提供，已有的鉴权机制保护访问

---

## 五、技术架构

### 5.1 系统集成

```
用户（对话）
    │
    ▼
主智能体 (Agent)
    │
    ├── delegate_to_subagent("competitor-research")
    │     │
    │     ▼
    │   竞品研究子智能体 (subagents/competitor-research/)
    │     │
    │     ├── 阶段一：深度搜索研究（材料 MD 不进入 Agent 上下文）
    │     │     ├── web_search → 多轮搜索竞品信息
    │     │     ├── browser_get_content → 静态页面内容采集
    │     │     ├── use_skill("baidu-search") → 中文搜索补充
    │     │     └── file_write(generate_prompt) → 搜索结果整理为 MD 材料文件
    │     │
    │     ├── 阶段二：报告生成（HTML 不进入 Agent 上下文）
    │     │     ├── file_write(generate_prompt) → 内部调 LLM 生成 HTML 并写入文件
    │     │     └── file_write(merge_files) → 合并多页为带导航的完整报告
    │     │
    │     └── 使用的工具
    │           ├── web_search — 互联网搜索
    │           ├── browser_get_content — 静态页面内容采集（返回 markdown）
    │           ├── browser_automation — 需交互的页面采集
    │           ├── file_write — 内部生成 HTML 并写入文件（generate_prompt 模式）
    │           └── file_write — 合并多页为完整报告（merge_files 模式）
    │
     └── 前端
           └── AttachmentPreviewPanel — HTML iframe 预览
```

### 5.2 Skill 拆分

**推荐方案：单个 Skill（competitor-research）**

竞品研究作为一个完整的 Skill，包含深度搜索和报告生成两个阶段。理由：

- 两个阶段强关联（搜索结果直接用于报告生成）
- 用户交互是统一对话式
- 单个 SKILL.md 可以包含完整的搜索策略和报告模板

---

## 六、用户交互设计

### 6.1 核心对话场景

| 场景 | 用户输入示例 | 智能体行为 |
|------|-------------|-----------|
| **完整研究** | "帮我研究一下飞书这个竞品" | 启动深度搜索 → 逐页生成报告 |
| **指定范围** | "只分析飞书的产品功能和定价" | 只搜索指定方面 → 生成对应报告页 |
| **对比研究** | "对比分析飞书和钉钉" | 分别搜索两个竞品 → 生成对比报告 |
| **追加研究** | "再深入研究一下飞书的用户口碑" | 追加搜索 → 追加材料 → 追加报告页 |
| **预览报告** | （前端自动） | 每页生成后自动可预览 |

### 6.2 多轮对话示例

```
用户：帮我研究一下飞书这个竞品

智能体：好的，我将从以下几个方面深度研究飞书：
1. 公司基础信息与产品线
2. 产品功能深度分析
3. 用户口碑与市场评价
4. 与我方产品的竞争态势
5. 最新动态与战略方向

我将逐项搜索并整理材料，过程中你可以随时查看已生成的报告页面。

开始第一步：搜索飞书的基础信息...
[调用 web_search 搜索 "飞书 产品介绍 官网"]
[调用 web_search 搜索 "飞书 字节跳动 公司概况"]
[调用 web_search 搜索 "飞书 定价 版本对比"]

已完成基础信息搜索，材料已保存。
正在生成第一页报告...

📄 第 1 页「公司概况」已生成，你可以在右侧预览。

继续搜索产品功能分析...
[调用 web_search 搜索 "飞书 核心功能 产品分析"]
[调用 browser_automation 打开飞书产品页采集详细信息]
...

📄 第 2 页「产品功能分析」已生成。

...（继续后续步骤）

全部报告已生成完毕，共 8 页：
1. 封面 ✅
2. 公司概况 ✅
3. 产品功能分析 ✅
4. 定价策略 ✅
5. 用户口碑与评价 ✅
6. 竞争态势分析 ✅
7. 最新动态 ✅
8. 总结与建议 ✅

正在合并为完整报告...
📋 完整报告已生成，你可以在右侧预览。左侧目录可跳转各章节，顶部可翻页浏览。

你也可以逐页预览单页内容。
```

### 6.3 追加研究示例

```
用户：再帮我深入分析一下飞书的用户口碑，特别是知乎上的评价

智能体：好的，我来重点搜索知乎上关于飞书的用户评价。
[调用 web_search 搜索 "飞书 知乎 评价 优缺点"]
[调用 web_search 搜索 "飞书 使用体验 吐槽"]
[调用 browser_automation 打开知乎相关帖子采集详细内容]

已收集到 15 条知乎用户评价，更新了口碑材料。
📄 第 5 页「用户口碑与评价」已更新，你可以重新预览。
```

---

## 七、SUBAGENT.md 核心结构

```yaml
---
name: 竞品研究专家
description: 基于深度搜索研究竞品，生成可预览的 HTML 竞品分析报告
version: 1.0.0
author: system
capabilities:
  - deep_web_research
  - competitor_analysis
  - report_generation
  - html_report
triggers:
  keywords:
    - 竞品
    - 竞品分析
    - 竞品研究
    - 竞品报告
    - 竞争对手
    - 对手分析
tools:
  inherit: true
skills:
  allowed:
    - baidu-search
context:
  max_input_tokens: 12000
  max_output_tokens: 6000
---
```

**Markdown Body 核心内容（概要）**：

1. **身份定义**：竞品研究分析师，擅长通过深度搜索收集和分析竞品信息
2. **工作流**：两阶段（深度搜索研究 → HTML 报告生成 + 合并）
3. **搜索策略**：5 个研究维度，每个维度 3-5 轮搜索，交叉验证
4. **材料管理**：每完成一个维度的搜索，整理为 Markdown 材料文件保存
5. **报告生成**：必须使用 `file_write(generate_prompt=...)` 逐页生成 HTML（**禁止**两步调用 `content_generate` + `file_write`，避免 HTML 内容膨胀 Agent 上下文），最终通过 `file_write(merge_files=...)` 合并为完整报告
6. **HTML 模板**：提供完整的 CSS 样式系统和页面结构模板，汇总页含导航框架
7. **行为约束**：不编造信息、标注来源、不确定的结论标注置信度

---

## 八、实施优先级

### Phase 1：核心流程（2-3 周）

| 功能 | 说明 | 优先级 | 状态 |
|------|------|--------|------|
| 深度搜索流程 | 5 维度多轮搜索，材料文件保存 | P0 | 待开发 |
| HTML 报告逐页生成 | 使用 `file_write(generate_prompt=...)` 内部生成 HTML 并写入，HTML 不进入 Agent 上下文 | P0 | 待开发 |
| 报告合并机制 | 使用 `file_write(merge_files=...)` 合并多页为带导航的完整报告 | P0 | 待开发 |
| SUBAGENT.md 编写 | 完整的子智能体定义和提示词（强调禁止两步调用、必须用 file_write 内部生成） | P0 | 待开发 |
| 前端 HTML 预览 | AttachmentPreviewPanel iframe 预览 | P0 | ✅ 已实现 |
| 前端 sandbox 放宽 | iframe 增加 `allow-scripts` 支持汇总页导航 | P0 | 待开发 |
| 后端 MIME 类型 | .html 的 MIME 映射 | P0 | ✅ 已实现 |

### Phase 2：增强功能（1-2 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 追加研究 | 在已有报告基础上追加搜索维度 | P1 |
| 对比报告 | 两个竞品的并排对比分析 | P1 |
| 自定义模板 | 用户指定报告的维度和格式 | P2 |

### Phase 3：高级功能（按需）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 历史报告管理 | 查看、对比历次研究报告 | P3 |
| 定期刷新 | 定期重新搜索更新报告 | P3 |
| 多竞品矩阵 | 3+ 竞品的综合对比矩阵 | P3 |

---

## 九、设计原则

1. **搜索驱动**：一切分析基于实际搜索结果，不编造、不推测无依据的结论
2. **可溯源**：每条信息标注来源 URL 和搜索时间
3. **渐进产出**：逐页生成、逐页可预览，用户不需要等全部完成；最终合并为完整报告
4. **文件存储**：材料文件和报告文件都存文件系统，不引入新的数据库表
5. **上下文保护**：HTML 生成必须使用 `file_write(generate_prompt=...)` 内部生成，**禁止**两步调用（content_generate + file_write），避免 HTML 内容膨胀 Agent 上下文窗口
6. **复用现有工具**：web_search、browser_get_content、browser_automation、file_write
7. **对话式交互**：所有功能通过自然语言对话触发
8. **安全预览**：子页面 HTML 纯 CSS + HTML（无脚本），汇总页仅含导航脚本

---

## 十、文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/competitor-research/SUBAGENT.md` | 子智能体定义（含逐页生成 + 合并指令） |
| `src/skills/competitor-research-1.0.0/SKILL.md` | 竞品研究技能定义（搜索策略 + 报告模板 + 合并模板） |
| `src/skills/competitor-research-1.0.0/scripts/research_helper.py` | 材料文件管理辅助脚本（保存/读取材料文件、生成 meta.json） |

### 修改文件

| 文件 | 改动范围 | 说明 |
|------|----------|------|
| `frontend/src/components/AttachmentPreviewPanel.vue` | iframe sandbox 增加 `allow-scripts` | 支持汇总页翻页导航 |

---

## 十一、风险与注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| 搜索耗时 | 5 维度 × 3-5 轮搜索 = 15-25 次 API 调用 | 逐维度推进，每完成一个维度即向用户报告进度 |
| 数据准确性 | 互联网信息可能过时或不准确 | 标注来源和时间，关键数据交叉验证 |
| HTML 渲染安全 | 恶意 HTML 可能执行脚本 | 子页面纯 CSS + HTML 无脚本；汇总页仅含导航 JS |
| LLM 生成质量 | file_write 内部生成的 HTML 格式可能不稳定 | 在 generate_prompt 中提供严格的 HTML 模板和 CSS 样式；工具内置格式校验和自动重试 |
| 上下文窗口 | 大量搜索结果可能超出 LLM 上下文 | 搜索结果先整理为材料文件，报告生成时只加载对应维度的材料 |
| 并发写入 | 多个搜索结果同时写入同一材料文件 | 单线程顺序执行（Agent 循环是顺序的） |
| 合并文件体积 | 8 个子页面合并后 HTML 可能很大 | 子页面使用内联 CSS，共享样式在合并时去重；预估 8 页总计 100-200KB，可接受 |
| 合并过程可靠性 | 提取 `<body>` 内容可能因 LLM 生成格式不规范而失败 | 在子页面提示词中强调严格的 HTML 结构；合并脚本做容错处理 |
