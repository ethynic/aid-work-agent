# 竞品研究子智能体 — 设计文档

> 版本: v2.0 | 创建: 2026-05-06 | 更新: 2026-05-19 | 状态: 待审核

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
└─────────────────────────────────┘
```

### 与 v1.0 设计的关键区别

| 维度 | v1.0 设计 | v2.0 设计（本次） |
|------|-----------|-------------------|
| **核心驱动** | 数据库管理竞品列表，多模块架构 | 深度搜索研究驱动，搜索 → 材料 → 报告 |
| **数据存储** | 三张数据库表（竞品表、情报表、报告表） | 文件系统（Markdown 材料文件 + HTML 报告） |
| **报告格式** | Markdown / Word / Excel | HTML（逐页生成，可在前端 iframe 中预览） |
| **信息来源** | 持续爬虫监控 | 按需深度搜索，多轮递进 |
| **交互模式** | CRUD 管理竞品列表 | 对话式触发研究任务 |
| **复杂度** | 5 个功能模块 | 2 个阶段，流程简洁 |

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
    ▼  → 保存材料：01_基础信息.md
    │
Step 2: 产品深度分析（3-5 轮搜索）
    ├── 核心功能详解
    ├── 技术架构与差异化
    ├── 用户界面/体验分析
    └── 产品更新与路线图
    │
    ▼  → 保存材料：02_产品分析.md
    │
Step 3: 市场与口碑（3-5 轮搜索）
    ├── 用户评价与口碑（知乎、小红书、G2 等）
    ├── 媒体报道与分析师观点
    ├── 社交媒体动态
    └── 客户案例与合作
    │
    ▼  → 保存材料：03_市场口碑.md
    │
Step 4: 竞争态势（2-3 轮搜索）
    ├── 行业竞争格局
    ├── 与我方产品的直接对比
    ├── 优劣势对比
    └── 威胁与机会
    │
    ▼  → 保存材料：04_竞争态势.md
    │
Step 5: 最新动态（2-3 轮搜索）
    ├── 近期新闻与融资
    ├── 招聘信息（推测战略方向）
    ├── 产品最新更新
    └── 高管变动
    │
    ▼  → 保存材料：05_最新动态.md
    │
    ▼
汇总所有材料 → 进入报告生成阶段
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
| 快速信息检索 | `web_search` | Tavily 搜索，返回结构化结果 |
| 深度页面内容采集 | `browser_automation` | 打开具体页面获取完整内容 |
| 中文搜索补充 | `use_skill("baidu-search")` | 百度 AI 搜索，适合国内竞品 |

### 2.3 材料文件格式

每份材料文件是 Markdown 格式，存储在会话专属目录中：

```
storage/competitor_research/{session_id}/
├── 01_基础信息.md
├── 02_产品分析.md
├── 03_市场口碑.md
├── 04_竞争态势.md
├── 05_最新动态.md
└── meta.json          # 研究元数据
```

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

### 3.1 逐页生成机制

报告采用**逐页生成**模式：每生成一页 HTML，立即注册为可下载文件，用户可以在前端预览。不需要等全部页面生成完毕。

```
材料文件汇总
    │
    ▼
生成封面页 → register_download_file → 用户可预览第 1 页
    │
    ▼
生成公司概况页 → register_download_file → 用户可预览第 2 页
    │
    ▼
生成产品分析页 → register_download_file → 用户可预览第 3 页
    │
    ▼
... 逐页继续
    │
    ▼
生成汇总结论页 → register_download_file → 用户可预览最后一页
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
├── cover.html              # 封面
├── company_overview.html   # 公司概况
├── product_analysis.html   # 产品分析
├── pricing.html            # 定价策略
├── user_reviews.html       # 用户口碑
├── competition.html        # 竞争态势
├── latest_news.html        # 最新动态
└── summary.html            # 总结建议
```

每个 HTML 文件通过 `register_download_file` 注册后，前端可通过 `/api/files/{file_id}` 在 iframe 中预览。

---

## 四、前端预览支持

### 4.1 现状

当前前端 `AttachmentPreviewPanel.vue` 将 HTML 文件作为文本（源码）显示，不支持渲染预览。需要新增 HTML 渲染预览能力。

### 4.2 改动方案

在 `AttachmentPreviewPanel.vue` 中新增 HTML 预览类型：

```vue
<!-- HTML 报告预览 -->
<iframe
  v-if="previewType === 'html'"
  :src="getFileUrl(attachment.file_id)"
  class="w-full h-full border-0"
  sandbox="allow-same-origin"
  @load="loading = false"
/>
```

**previewType 判断调整**：

```typescript
// 将 'html' 从 textExts 中移除，新增 html 类型
const htmlExts = ['html', 'htm']

// previewType 计算属性中增加：
if (htmlExts.includes(ext)) return 'html'
```

**后端 MIME 类型补充**：在 `register_download_tool.py` 的 `mime_type_map` 和 `main.py` 的 MIME 映射中添加：

```python
'.html': 'text/html',
'.htm': 'text/html',
```

### 4.3 安全考虑

- iframe 使用 `sandbox="allow-same-origin"` 属性，禁止 HTML 中的脚本执行和外部请求
- 生成的 HTML 报告不包含 JavaScript，纯 CSS + HTML 渲染
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
    │     ├── 阶段一：深度搜索研究
    │     │     ├── web_search → 多轮搜索竞品信息
    │     │     ├── browser_automation → 深度页面内容采集
    │     │     ├── use_skill("baidu-search") → 中文搜索补充
    │     │     └── 材料整理 → 保存到 storage/ 目录
    │     │
    │     ├── 阶段二：报告生成
    │     │     ├── content_generate → 基于 LLM 逐页生成 HTML
    │     │     └── register_download_file → 注册每页为可预览文件
    │     │
    │     └── 使用的工具
    │           ├── web_search — 互联网搜索
    │           ├── browser_automation — 深度页面采集
    │           ├── content_generate — LLM 长文生成
    │           └── register_download_file — 文件注册
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
1. 封面
2. 公司概况 ✅
3. 产品功能分析 ✅
4. 定价策略 ✅
5. 用户口碑与评价 ✅
6. 竞争态势分析 ✅
7. 最新动态 ✅
8. 总结与建议 ✅

你可以在右侧逐页预览，也可以下载完整报告。
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
2. **工作流**：两阶段（深度搜索研究 → HTML 报告生成）
3. **搜索策略**：5 个研究维度，每个维度 3-5 轮搜索，交叉验证
4. **材料管理**：每完成一个维度的搜索，整理为 Markdown 材料文件保存
5. **报告生成**：逐页生成 HTML，每页调用 `content_generate` + `register_download_file`
6. **HTML 模板**：提供完整的 CSS 样式系统和页面结构模板
7. **行为约束**：不编造信息、标注来源、不确定的结论标注置信度

---

## 八、实施优先级

### Phase 1：核心流程（2-3 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 深度搜索流程 | 5 维度多轮搜索，材料文件保存 | P0 |
| HTML 报告逐页生成 | 基于 content_generate 逐页生成，register_download_file 注册 | P0 |
| 前端 HTML 预览 | AttachmentPreviewPanel 新增 HTML iframe 预览 | P0 |
| SUBAGENT.md 编写 | 完整的子智能体定义和提示词 | P0 |
| 后端 MIME 类型 | 补充 .html 的 MIME 映射 | P0 |

### Phase 2：增强功能（1-2 周）

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 追加研究 | 在已有报告基础上追加搜索维度 | P1 |
| 对比报告 | 两个竞品的并排对比分析 | P1 |
| 报告导出 | 支持将 HTML 报告合并导出为 PDF | P2 |
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
3. **渐进产出**：逐页生成、逐页可预览，用户不需要等全部完成
4. **文件存储**：材料文件和报告文件都存文件系统，不引入新的数据库表
5. **复用现有工具**：web_search、browser_automation、content_generate、register_download_file
6. **对话式交互**：所有功能通过自然语言对话触发
7. **安全预览**：HTML 报告在 sandbox iframe 中预览，不执行脚本

---

## 十、文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `subagents/competitor-research/SUBAGENT.md` | 子智能体定义 |
| `src/skills/competitor-research-1.0.0/SKILL.md` | 竞品研究技能定义（搜索策略 + 报告模板） |
| `src/skills/competitor-research-1.0.0/scripts/research_helper.py` | 材料文件管理辅助脚本（保存/读取材料文件、生成 meta.json） |

### 修改文件

| 文件 | 改动范围 | 说明 |
|------|----------|------|
| `frontend/src/components/AttachmentPreviewPanel.vue` | 新增 `previewType === 'html'` 分支 | HTML iframe 预览 |
| `src/tools/file/register_download_tool.py` | mime_type_map 添加 `.html` | HTML 文件 MIME 类型 |
| `src/main.py` | MIME 映射添加 `.html` | 文件服务返回正确的 MIME 类型 |

---

## 十一、风险与注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| 搜索耗时 | 5 维度 × 3-5 轮搜索 = 15-25 次 API 调用 | 逐维度推进，每完成一个维度即向用户报告进度 |
| 数据准确性 | 互联网信息可能过时或不准确 | 标注来源和时间，关键数据交叉验证 |
| HTML 渲染安全 | 恶意 HTML 可能执行脚本 | sandbox iframe + 生成的 HTML 不含 JavaScript |
| LLM 生成质量 | content_generate 产出的 HTML 格式可能不稳定 | 在提示词中提供严格的 HTML 模板和 CSS 样式 |
| 上下文窗口 | 大量搜索结果可能超出 LLM 上下文 | 搜索结果先整理为材料文件，报告生成时只加载对应维度的材料 |
| 并发写入 | 多个搜索结果同时写入同一材料文件 | 单线程顺序执行（Agent 循环是顺序的） |
