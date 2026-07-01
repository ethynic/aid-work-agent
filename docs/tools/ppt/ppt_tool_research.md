# PPT 工具深度调研报告

> 版本: v1.2 | 调研日期: 2026-05-09 | 状态: ✅ 调研完成，结论已由 [增强设计](ppt_tool_enhancement_design.md) 落地
>
> 文档定位：历史选型与决策依据。当前实现采用 Python 编排、Node.js/PptxGenJS 主渲染、HTML 双模式导出及统一 QA。

## 1. 需求背景

系统需要设计一个 PPT 生成工具，核心能力包括：

1. **AI 内容 → PPT**：将 AI 规划的深度内容（大纲+正文）转换为漂亮、可编辑的 PPT
2. **模板驱动**：支持用户上传 PPT 模板，分析模板中每个页面的布局特点，智能匹配内容到合适的页面
3. **内容格式转换**：将结构化内容（大纲/Markdown）转换为 PPT 工具可消费的格式（可借助 LLM 完成）
4. **可编辑性**：生成的 PPT 必须是原生 .pptx 格式，可在 PowerPoint/WPS 中自由编辑

---

## 2. 市场现有工具全景

### 2.1 国内 AI PPT SaaS 平台

| 平台 | API 可用性 | 核心能力 | 单次价格 | 中文支持 | 适用性评估 |
|------|-----------|---------|---------|---------|-----------|
| **AiPPT (aippt.cn)** | ✅ 完整服务端 API | 标题→大纲→内容→模板→生成PPT，完整工作流 | ¥0.32-0.49/次 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ 最佳中文 API |
| **讯飞智文** | ✅ 公开 API | 文本/文档→PPT，音视频→PPT，10语种翻译 | ~¥1.34/次（最低¥1344起） | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ API 门槛高 |
| **文多多 AiPPT (docmee.cn)** | ✅ API + Iframe | API+UI 双模式接入 | ¥0.32-0.49/次 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ 门槛低 |
| **即触 AI PPT** | ✅ API | 仅成功生成扣费 | 按量计费 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **302.AI** | ✅ API | 按量付费，零门槛 | 按量付费 | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **百度文库 AI PPT** | ⚠️ 千帆平台 | 文档→PPT | 需咨询 | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| **WPS AI** | ❌ 无公开 API | AI 主题/文档生成 PPT | 企业按量月结 | ⭐⭐⭐⭐⭐ | ⭐ 不可集成 |

### 2.2 国际 AI PPT SaaS 平台

| 平台 | API 可用性 | 核心能力 | 价格 | 中文支持 | 适用性评估 |
|------|-----------|---------|------|---------|-----------|
| **Gamma** | ⚠️ Beta API (Pro版) | AI 生成演示文稿，Web原生+导出PPTX | Pro $15-20/月 | ⭐⭐⭐ | ⭐⭐ API 仍为 Beta |
| **Beautiful.ai** | ✅ prompt-to-deck API | AI 设计系统，自动排版 | 企业定价 | ⭐⭐ | ⭐⭐⭐ |
| **SlideForge** | ✅ REST API + MCP (27工具) | 模板渲染+AI生成，咨询级质量 | $0.03-0.20/页 | ⭐⭐ | ⭐⭐⭐⭐ 开发者友好 |
| **SlideSpeak** | ✅ 完整开发者 API | PPT 生成+编辑+总结 | 按量付费 | ⭐⭐ | ⭐⭐⭐ |
| **Canva** | ❌ 仅 SDK（前端） | 设计平台，PPT 导出 | Free/Pro | ⭐⭐⭐ | ⭐ 不可后端集成 |
| **Tome** | ❌ 已关停 | — | — | — | ❌ |

### 2.3 开源项目

| 项目 | GitHub | 核心思路 | 输出格式 | 适用性 |
|------|--------|---------|---------|--------|
| **PPTAgent** | [icip-cas/PPTAgent](https://github.com/icip-cas/pptagent) ⭐~4.3k | 参考演示文稿的编辑式生成（EMNLP 2025 论文，中科院） | 原生 .pptx | ⭐⭐⭐⭐ 学术前沿 |
| **Presenton** | [presenton/presenton](https://github.com/presenton/presenton) | 开源 Gamma 替代，支持 Ollama 本地模型 | PPTX/PDF | ⭐⭐⭐ 可自托管 |
| **md2pptx** | [MartinPacker/md2pptx](https://github.com/MartinPacker/md2pptx) | Markdown → PPTX 转换器 | 原生 .pptx | ⭐⭐⭐ 简单场景 |
| **MarpToPptx** | [jongalloway/MarpToPptx](https://github.com/jongalloway/MarpToPptx) | Marp Markdown → PPTX，含模板诊断功能 | 原生 .pptx | ⭐⭐⭐ 模板分析参考 |

### 2.4 Python 基础库

| 库 | 定位 | 能力 |
|----|------|------|
| **python-pptx** | Python PPT 操作标准库 | 创建/编辑/读取 .pptx，操作幻灯片/形状/文本/图表/表格/图片 |
| **mdtopptx** | Markdown → PPTX | 基于 python-pptx 的简单转换 |
| **python-pptx-text-replacer** | 文本替换 | 替换 .pptx 中所有文本（含组合形状和图表） |
| **PptxGenJS** | JavaScript PPT 生成 | 前端/Node.js 环境的 PPTX 生成 |

---

## 3. 重点工具深度分析

### 3.1 python-pptx — 基础能力评估

**基本信息**：MIT 许可，Python 3.8+，GitHub [scanny/python-pptx](https://github.com/scanny/python-pptx)

#### 能力矩阵

| 能力 | 支持度 | 说明 |
|------|--------|------|
| 创建新演示文稿 | ✅ 完整 | 从零创建幻灯片、形状、文本、图表、表格、图片 |
| 读取/编辑现有 .pptx | ✅ 完整 | Round-trip 所有 Open XML 元素 |
| 加载模板 | ✅ 完整 | `Presentation('template.pptx')` 加载 |
| 使用模板的 Slide Layout | ✅ 完整 | `prs.slide_layouts[idx]` 选择布局 |
| 占位符文本替换 | ✅ 完整 | 遍历 `slide.placeholders` 替换内容 |
| 操作形状/文本框 | ✅ 完整 | 添加/修改/删除任意形状 |
| 图表支持 | ⚠️ 有限 | 支持基本图表，不支持瀑布图/树状图/旭日图 |
| 动画/转场 | ❌ 不支持 | GitHub #1106 常年呼声最高的需求 |
| 幻灯片母版编辑 | ⚠️ 有限 | 可读取但修改受限 |
| SmartArt | ❌ 不支持 | |
| 媒体嵌入 | ⚠️ 基础 | 支持图片，视频/音频有限 |

#### 核心痛点

```python
# 问题 1：坐标硬编码，无设计系统
box = slide.shapes.add_shape(1, Inches(0.5), Inches(1.5), Inches(2.8), Inches(2.0))
# 每个元素位置都需要手动计算，移一个元素其他全要调

# 问题 2：制作一个专业级 KPI 仪表板页面需要 60-100 行代码
# 一个咨询级演示文稿需要 10-15 种不同布局 → 600-1500 行

# 问题 3：无设计一致性保障
# 颜色、字体、间距全靠开发者手工维护常量
```

#### 模板操作核心代码

```python
from pptx import Presentation

# 加载模板
prs = Presentation('template.pptx')

# 枚举所有 Slide Layout 及其占位符
for i, layout in enumerate(prs.slide_layouts):
    print(f"Layout {i}: {layout.name}")
    for ph in layout.placeholders:
        print(f"  Placeholder {ph.placeholder_format.idx}: {ph.name} (type={ph.placeholder_format.type})")

# 使用模板 Layout 创建幻灯片
slide_layout = prs.slide_layouts[0]  # 例如标题页布局
slide = prs.slides.add_slide(slide_layout)

# 填充占位符
title_ph = slide.placeholders[0]  # 通常 idx=0 是标题
title_ph.text = "季度汇报"

subtitle_ph = slide.placeholders[1]  # 通常 idx=1 是副标题
subtitle_ph.text = "2026年第一季度"

# 替换所有文本中的标记
for slide in prs.slides:
    for shape in slide.shapes:
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if '{{COMPANY}}' in run.text:
                        run.text = run.text.replace('{{COMPANY}}', '智谱AI')

prs.save('output.pptx')
```

#### 占位符类型对照

| placeholder_format.type | 典型 idx | 含义 | 适合填充的内容 |
|------------------------|----------|------|---------------|
| TITLE (1) | 0 | 标题 | 一级标题 |
| BODY (2) | 1 | 正文 | 要点列表、段落 |
| CENTER_TITLE (3) | 0 | 居中标题 | 封面标题 |
| SUBTITLE (4) | 1 | 副标题 | 封面副标题 |
| DATE (5) | — | 日期 | 自动日期 |
| SLIDE_NUMBER (6) | — | 页码 | — |
| FOOTER (7) | — | 页脚 | — |
| PICTURE (18) | — | 图片 | 图片路径 |
| CHART (12) | — | 图表 | 数据 |

### 3.2 AiPPT 开放平台 API — 最佳中文方案

**官网**：[open.aippt.cn](https://open.aippt.cn)

#### API 工作流

```
1. 创建任务 → POST /api/ai/chat/task (传入标题)
2. 标题→大纲 → GET /api/ai/chat/outline?task_id=X (SSE 流式)
3. 大纲→内容 → GET /api/ai/chat/content?task_id=X (SSE 流式)
4. 编辑大纲 → POST /api/ai/chat/outline/save (可修改大纲)
5. 选择模板 → GET /api/template_component/suit/search (按颜色/风格筛选)
6. 生成 PPT → (根据模板+大纲+内容生成最终 PPT)
```

#### 核心优势

- **完整的 PPT 生成工作流**：从标题到最终 PPT 全链路 API 覆盖
- **大纲可编辑**：生成大纲后可修改，再生成内容
- **模板系统**：支持按颜色、风格筛选模板套装
- **SSE 流式输出**：大纲和内容生成为 SSE 格式，支持实时展示
- **文档导入**：支持 Word 文档导入生成 PPT

#### 定价

| 套餐 | 价格 | 积分 | 单次成本 |
|------|------|------|---------|
| 入门 | ¥99 | 200次 | ~¥0.49/次 |
| 标准 | ¥299 | 666次 | ~¥0.45/次 |
| 批量 | ¥799 | 2000次 | ~¥0.40/次 |

#### 局限性

- 生成的是 AiPPT 平台专有格式（canvas JSON），**不是原生 .pptx**
- 需要通过 AiPPT 的在线编辑器查看/编辑，不能直接用 PowerPoint 打开
- 模板生态封闭，只能用 AiPPT 的模板库
- API 依赖第三方服务，无法离线使用

### 3.3 讯飞智文 API

**官网**：[xfyun.cn/doc/spark/PPTGeneration.html](https://www.xfyun.cn/doc/spark/PPTGeneration.html)

| 项目 | 说明 |
|------|------|
| 接口 | `https://zwapi.xfyun.cn/api/aippt/createByDoc` |
| 输入 | 文本（≤8000字）或文档（≤10MB） |
| 免费额度 | 1000积分（生成一次约10积分，约100次） |
| 最低购买 | ¥1344，无按量付费 |
| 特色 | 音视频→PPT、10语种翻译、自动演讲备注 |

### 3.4 SlideForge API — 最佳开发者体验

**官网**：[slideforge.dev](https://slideforge.dev)

| 项目 | 说明 |
|------|------|
| API 类型 | REST API + MCP (Model Context Protocol) |
| MCP 工具数 | 27 个 |
| 输出格式 | 原生 .pptx、PDF、PNG |
| 定价 | $0.03/页（模板渲染），$0.20/页（AI 生成） |
| 免费额度 | $3 信用额度，无需信用卡 |
| SDK | Python, Node.js, C#, Ruby, PHP, Java |
| 组件库 | 35+ 内置组件（Metric, BarList 等） |

#### 核心优势

```python
# 5 行代码生成咨询级 KPI 仪表板
response = requests.post(
    "https://api.slideforge.dev/v1/render",
    headers={"Authorization": "Bearer sf_live_..."},
    json={
        "template": "kpi_dashboard",
        "params": {
            "title": "Q1 2026 绩效总结",
            "metrics": [
                {"value": "$12.4M", "label": "营收", "trend": "+18%"},
                {"value": "847", "label": "新客户", "trend": "+23%"},
            ],
        },
    },
)
print(response.json()["pptx_url"])  # 下载原生 .pptx
```

#### 局限性

- 国际服务，国内访问可能有延迟
- 模板库偏向英文/商务风格
- 中文支持程度未知
- 依赖外部 API，无法离线

### 3.5 PPTAgent — 学术前沿方案

**GitHub**：[icip-cas/PPTAgent](https://github.com/icip-cas/PPTAgent) | EMNLP 2025 论文

**核心创新**：

传统方法：文本 → 幻灯片（从零生成，质量差）
PPTAgent：参考演示文稿 → 分析 → 编辑式生成（质量高，可编辑）

**两阶段流程**：

```
阶段 1：分析参考 PPT
  - 识别幻灯片布局类型
  - 提取设计元素（颜色、字体、间距）
  - 建立布局-内容映射关系

阶段 2：编辑式生成
  - 选择最匹配的参考幻灯片
  - 替换文本内容
  - 保持原有设计风格
```

**输出**：原生可编辑 .pptx

**价值**：这个思路与我们的"模板分析 + 智能匹配"需求高度吻合。

### 3.6 Presenton — 开源自托管方案

**GitHub**：[presenton/presenton](https://github.com/presenton/presenton)

| 项目 | 说明 |
|------|------|
| 定位 | 开源 Gamma 替代品 |
| AI 后端 | OpenAI / Gemini / Ollama（本地模型） |
| 部署方式 | 自托管 / 桌面 / 云端 |
| 导入 | PPTX 模板 |
| 导出 | PPTX、PDF |
| 隐私 | 可完全本地运行（配合 Ollama） |

**优势**：可自托管、支持本地模型、PPTX 模板导入
**局限**：项目较新、输出质量不如商业方案、中文支持待验证

### 3.7 豆包（字节跳动）PPT 生成 — 深度拆解

#### 演进历程

| 阶段 | 方案 | 体验 |
|------|------|------|
| V1（早期） | 与 AiPPT 合作，通过接口调用 AiPPT 生成，完成后跳转 AiPPT 下载 | 体验割裂，需跳转第三方 |
| V2（当前） | **原生内置** PPT 生成能力，与博思 AIPPT、迅捷 AiPPT 等平台技术对接 | 体验流畅，一体化 |

#### 技术架构（三层分离）

```
┌─────────────────────────────────────────────┐
│  Layer 1: 内容生成层 — 豆包大模型（LLM）      │
│                                               │
│  用户输入主题/文档 → LLM 生成：               │
│  - 逻辑严谨的 PPT 大纲                        │
│  - 封面页、章节划分、内容要点                  │
│  - 配图建议                                   │
│  - 内容迭代优化（2-3轮）                      │
│                                               │
│  底层: MoE 架构 + UltraMem 稀疏模型           │
│  响应速度: 毫秒级                             │
└───────────────────────┬─────────────────────┘
                        │ 结构化大纲
                        ▼
┌─────────────────────────────────────────────┐
│  Layer 2: 排版引擎 — 第三方平台               │
│                                               │
│  博思 AIPPT / 迅捷 AiPPT 提供：              │
│  - 模板库（按颜色/风格/行业分类）             │
│  - 排版渲染引擎                              │
│  - 布局优化（文字/图片/公式自动排版）         │
│  - 视觉规范遵循                              │
└───────────────────────┬─────────────────────┘
                        │ 渲染后的页面
                        ▼
┌─────────────────────────────────────────────┐
│  Layer 3: 组装输出层                          │
│                                               │
│  - 在线编辑器（Web 端查看/修改）              │
│  - 导出为 PPTX/PDF                           │
│  - 支持后续编辑                              │
└─────────────────────────────────────────────┘
```

#### 豆包/扣子（Coze）Agent 的 PPT 生成 Skill

字节在扣子（Coze）AI Agent 平台上公开了一个 `bytedance/ppt-generation` Skill，其实现方式非常值得关注：

**核心思路：每张幻灯片生成一张 AI 图片，然后组装为 PPTX**

```
Step 1: 理解需求 → 识别主题、页数、风格、比例
Step 2: 创建 Presentation Plan（JSON 结构）
Step 3: 逐页生成 AI 图片（严格顺序，每页参考上一页保持风格一致）
Step 4: 调用脚本将图片组装为 PPTX
```

**支持 8 种设计风格**：

| 风格 | 说明 | 适用场景 |
|------|------|---------|
| `glassmorphism` | 毛玻璃效果，半透明卡片，渐变背景 | 科技产品、AI/SaaS 演示 |
| `dark-premium` | 深黑背景，发光强调色 | 高端品牌、高管演示 |
| `gradient-modern` | 大胆渐变，现代排版 | 初创公司、品牌发布 |
| `neo-brutalist` | 粗犷排版，高对比，反设计 | 年轻品牌、创意机构 |
| `3d-isometric` | 等距 3D 插画，柔和阴影 | 技术说明、产品功能 |
| `editorial` | 杂志级排版，精致字体 | 年报、奢侈品、思想领导力 |
| `minimal-swiss` | 网格精确，极简留白 | 建筑、设计、咨询 |
| `keynote` | Apple 风格，大留白，戏剧感 | 主题演讲、产品发布 |

**关键设计决策**：

1. **视觉一致性保障**：逐页顺序生成（不可并行），每页用上一页的图片作为参考（reference image），prompt 中强制强调 "EXACT visual style from reference"
2. **PPTX 组装脚本**：最终调用 `generate.py --plan-file ... --slide-images ... --output-file ...` 将所有图片组装为 PPTX
3. **Prompt 工程极为精细**：包含精确的 hex 色值、字体参数、模糊度、阴影规格等
4. **输出结果**：每页是一张 AI 生成的图片（非原生 PPT 形状/文本），可编辑性有限

**这个方案的局限**：

- 输出的 PPTX 中每页是一张**图片**，不是原生文本/形状
- 用户无法在 PowerPoint 中直接编辑文字（因为文字是图片的一部分）
- 风格一致性高度依赖 AI 图像生成的质量，可能有波动
- 不支持图表、表格等结构化内容

### 3.8 MiniMax PPTX Generator — 开源 Agent Skill 参考

**GitHub**：[MiniMax-AI/skills/pptx-generator](https://github.com/MiniMax-AI/skills/blob/main/skills/pptx-generator/SKILL.md)

这是 MiniMax 开源的一个 AI Agent Skill，用 **PptxGenJS**（JavaScript 库）从零创建 PPT，与豆包的"图片组装"方案完全不同。

**核心思路：用代码（JS）逐页创建原生 PPTX**

```
Step 1: 调研需求（主题、受众、目的、基调）
Step 2: 选择配色方案 & 字体（内置设计系统）
Step 3: 选择设计风格（Sharp/Soft/Rounded/Pill）
Step 4: 规划幻灯片大纲（5 种页面类型分类）
Step 5: 生成每页的 JS 文件（可并行，最多 5 个 subagent）
Step 6: 编译为最终 PPTX（compile.js）
Step 7: QA 检查
```

**5 种页面类型**：

| 类型 | 说明 |
|------|------|
| Cover | 封面页 |
| TOC | 目录页 |
| Section Divider | 章节分隔页 |
| Content | 内容页（要点列表/图文/数据） |
| Summary | 总结页 |

**设计系统**：

```
Theme Object（必须遵循）:
{
  primary:   "22223b",  // 最深色，标题
  secondary: "4a4e69",  // 次要色，正文
  accent:    "9a8c98",  // 强调色
  light:     "c9ada7",  // 浅色强调
  bg:        "f2e9e4"   // 背景色
}

规格：
- 尺寸: 10" x 5.625" (16:9)
- 中文字体: Microsoft YaHei
- 英文字体: Arial
- 颜色: 6位 hex，不含 #
- 页码徽章: 右下角 (x:9.3", y:5.1")
```

**每页 JS 文件示例**：

```javascript
// slide-01.js
const pptxgen = require("pptxgenjs");

function createSlide(pres, theme) {
  const slide = pres.addSlide();
  slide.background = { color: theme.bg };

  slide.addText("演示标题", {
    x: 0.5, y: 2, w: 9, h: 1.2,
    fontSize: 48, fontFace: "Microsoft YaHei",
    color: theme.primary, bold: true, align: "center"
  });

  return slide;
}

module.exports = { createSlide };
```

**优势**：
- 输出是**原生 .pptx**，所有文本/形状可编辑
- 设计系统完善，风格统一
- 支持图表（柱状/饼图/折线图等）
- 可并行生成多页

**局限**：
- 需要 Node.js 环境
- 设计质量受限于 PptxGenJS 的布局能力
- 没有模板分析能力，从零创建

### 3.9 两种主流 Agent 方案对比

| 维度 | 豆包方案（图片组装） | MiniMax 方案（代码生成） |
|------|---------------------|------------------------|
| **核心技术** | AI 图像生成 + PPTX 组装 | PptxGenJS 代码生成 |
| **输出格式** | 每页是一张图片 | 原生文本/形状 |
| **可编辑性** | ❌ 文字不可编辑 | ✅ 完全可编辑 |
| **设计质量** | ⭐⭐⭐⭐⭐ AI 生成视觉 | ⭐⭐⭐ 代码定义布局 |
| **图表支持** | ❌ 无 | ✅ 支持 |
| **风格一致性** | 逐页参考图（顺序执行） | Theme 对象强制统一 |
| **执行方式** | 串行（每页依赖上一页） | 可并行（最多 5 页同时） |
| **语言** | Python 脚本 | JavaScript (Node.js) |
| **内容灵活性** | 高（AI 自由生成视觉） | 中（受限于预定义布局） |
| **模板支持** | ❌ 无 | ⚠️ 可通过 XML 编辑模板 |
| **适合场景** | 视觉冲击力强的展示 | 需要编辑的数据演示 |

**对我们的启示**：

- 豆包方案**设计质量极高但牺牲了可编辑性**，适合"一次性演示"
- MiniMax 方案**可编辑但设计能力有限**，适合"需要后续修改"的场景
- **最佳方案应结合两者优势**：用 LLM 做内容规划 + python-pptx 做原生 PPTX 生成 + 精心设计的内置模板/主题系统保障视觉质量

---

## 4. 技术方案对比分析

### 4.1 三大路线

| 维度 | 路线 A: 接入第三方 API | 路线 B: python-pptx 自建 | 路线 C: 混合方案 |
|------|----------------------|------------------------|----------------|
| **开发周期** | 1-2 周 | 4-8 周 | 2-4 周 |
| **设计质量** | ⭐⭐⭐⭐⭐ 专业级 | ⭐⭐ 依赖自身设计能力 | ⭐⭐⭐⭐ |
| **可控性** | ⭐⭐ 依赖第三方 | ⭐⭐⭐⭐⭐ 完全自主 | ⭐⭐⭐⭐ |
| **成本** | ¥0.3-1.5/次 | 仅服务器成本 | 混合 |
| **离线能力** | ❌ | ✅ | ⚠️ 部分可离线 |
| **模板分析** | ❌ 用平台模板 | ✅ 可自定义 | ✅ |
| **输出格式** | 取决于平台 | 原生 .pptx | 原生 .pptx |
| **维护成本** | 低（API 更新） | 高（布局代码维护） | 中 |

### 4.2 各平台 API 的关键限制

| 平台 | 输出是否为原生 .pptx | 是否支持自定义模板 | 是否支持模板分析 |
|------|---------------------|------------------|----------------|
| AiPPT | ❌ 平台专有格式 | ❌ 只能用平台模板 | ❌ |
| 讯飞智文 | ⚠️ 需确认 | ⚠️ 有限 | ❌ |
| SlideForge | ✅ 原生 .pptx | ✅ 内置 50+ 模板 | ❌ |
| Gamma | ⚠️ Web 原生，可导出 PPTX | ❌ | ❌ |

**关键发现**：**没有任何第三方 API 同时满足"原生 .pptx 输出 + 自定义模板分析 + 内容智能匹配"这三个需求。**

---

## 5. 推荐方案

### 5.1 结论：采用混合方案（路线 C）

基于调研结果，推荐 **"LLM 内容规划 + python-pptx 模板引擎"自建方案**，辅以可选的第三方 API 集成。

**理由**：

1. **核心需求无法外包**：市场上没有 API 同时提供"模板分析 + 内容匹配 + 原生 PPTX 生成"
2. **项目已有 LLM Gateway**：可复用现有的 Qwen/ZhipuAI 调用能力做内容规划
3. **python-pptx 成熟稳定**：是 .pptx 操作的行业标准，能满足所有基础操作
4. **PPTAgent 提供了参考架构**：其"编辑式生成"思路可直接借鉴
5. **豆包方案的教训**：图片组装方案虽然视觉质量高但牺牲了可编辑性，不符合我们的需求
6. **MiniMax 方案的启发**：其 Theme 设计系统和页面分类体系值得参考

### 5.2 系统架构设计

**借鉴豆包三层分离 + MiniMax 设计系统**，采用如下架构：

```
用户输入（标题/文档/AI 规划内容）
    │
    ▼
┌─────────────────────────────────────────────┐
│  Layer 1: 内容规划层（LLM 驱动）              │
│  ← 借鉴豆包：LLM 负责大纲+内容+配图建议      │
│                                               │
│  Input → LLM → 结构化 PPT 大纲 JSON          │
│  - 每页标题、要点、图表类型、布局类型          │
│  - 配图描述（可选，用于 AI 生成插图）         │
│  - 输出统一的 JSON 格式                       │
│                                               │
│  页面分类（借鉴 MiniMax 5 类型体系）:         │
│  cover | toc | section | content | summary    │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  Layer 2: 模板/主题层                          │
│                                               │
│  两种模式:                                     │
│  A) 用户上传模板 → python-pptx 分析 Layout:  │
│     - 布局类型、占位符、设计元素               │
│     → 输出模板特征 JSON                       │
│  B) 使用内置主题（借鉴 MiniMax Theme 系统）:  │
│     {primary, secondary, accent, light, bg}  │
│     + 预设模板集（商务/简约/科技/学术等）      │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  Layer 3: 内容匹配层（规则 + LLM）            │
│                                               │
│  内容规划 JSON + 模板特征 JSON → 匹配:        │
│  - 规则匹配：cover→标题布局，content→列表布局  │
│  - LLM 辅助：复杂场景由 LLM 判断最佳布局      │
│  → 输出 页面-布局 绑定关系                     │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│  Layer 4: PPT 生成层（python-pptx）           │
│  ← 输出原生可编辑 .pptx（非图片组装）          │
│                                               │
│  遍历每页内容 → 选择匹配的 Layout →           │
│  创建 Slide → 填充占位符 → 保存               │
│                                               │
│  → 输出原生 .pptx 文件                        │
└─────────────────────────────────────────────┘
    │
    ▼
用户下载可编辑 .pptx 文件
```

### 5.3 模板分析实现方案

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.shapes import PP_PLACEHOLDER
import json

class PPTTemplateAnalyzer:
    """分析 PPT 模板，提取每个 Slide Layout 的特征"""

    def analyze(self, template_path: str) -> dict:
        prs = Presentation(template_path)
        result = {
            "slide_width": prs.slide_width,
            "slide_height": prs.slide_height,
            "layouts": []
        }

        for i, layout in enumerate(prs.slide_layouts):
            layout_info = {
                "index": i,
                "name": layout.name,
                "placeholders": [],
                "inferred_type": self._infer_layout_type(layout),
            }

            for ph in layout.placeholders:
                ph_info = {
                    "idx": ph.placeholder_format.idx,
                    "type": str(ph.placeholder_format.type),
                    "name": ph.name,
                    "width": ph.width,
                    "height": ph.height,
                    "left": ph.left,
                    "top": ph.top,
                }
                layout_info["placeholders"].append(ph_info)

            result["layouts"].append(layout_info)

        return result

    def _infer_layout_type(self, layout) -> str:
        """推断布局类型"""
        name_lower = layout.name.lower()
        ph_types = [str(ph.placeholder_format.type) for ph in layout.placeholders]

        if "title" in name_lower and "content" not in name_lower:
            return "cover"       # 封面页
        elif "section" in name_lower or "header" in name_lower:
            return "section"     # 章节分隔页
        elif "two" in name_lower or "comparison" in name_lower:
            return "comparison"  # 对比页
        elif "blank" in name_lower:
            return "blank"       # 空白页
        elif "picture" in name_lower:
            return "image"       # 图片页
        elif PP_PLACEHOLDER.PICTURE in ph_types or len([t for t in ph_types if 'PICTURE' in t]) > 0:
            return "image_content"  # 图文页
        elif PP_PLACEHOLDER.CHART in ph_types or len([t for t in ph_types if 'CHART' in t]) > 0:
            return "chart"       # 图表页
        else:
            return "content"     # 通用内容页
```

### 5.4 内容规划 JSON 格式

```json
{
  "title": "2026年Q1季度汇报",
  "slides": [
    {
      "layout_type": "cover",
      "title": "2026年第一季度绩效汇报",
      "subtitle": "华东销售部 · 2026年4月",
      "notes": "封面页"
    },
    {
      "layout_type": "content",
      "title": "核心业绩指标",
      "bullet_points": [
        "营收 ¥1,240万，同比增长 18%",
        "新增客户 847 家，完成率 106%",
        "客户留存率 94.2%",
        "NPS 评分 4.7/5.0"
      ],
      "notes": "KPI 概览页"
    },
    {
      "layout_type": "chart",
      "title": "月度营收趋势",
      "chart_type": "bar",
      "data": {
        "labels": ["1月", "2月", "3月"],
        "values": [380, 420, 440]
      },
      "notes": "图表页"
    },
    {
      "layout_type": "comparison",
      "title": "竞品对比分析",
      "left": {
        "title": "我方优势",
        "points": ["技术领先", "服务响应快", "价格有竞争力"]
      },
      "right": {
        "title": "需改进方向",
        "points": ["品牌知名度", "渠道覆盖", "生态整合"]
      }
    }
  ]
}
```

### 5.5 开发优先级

| 阶段 | 内容 | 预估工期 |
|------|------|---------|
| **P0** | LLM 内容规划 + python-pptx 基础生成（无模板，使用内置布局） | 1-2 周 |
| **P1** | 移植 MiniMax 设计系统（Theme + 页面分类）到 python-pptx | 1 周 |
| **P2** | 模板分析器 + 内容-布局智能匹配 | 1 周 |
| **P3** | 内置设计模板集（商务/简约/科技各 3-5 套） | 1 周 |
| **P3** | 图表生成支持（柱状图/饼图/折线图） | 1 周 |
| **P4** | 可选第三方 API 集成（AiPPT/SlideForge 作为备选生成路径） | 按需 |

### 5.6 MiniMax pptx-generator 方案嫁接评估

**结论：可以嫁接，且非常值得做。**

MiniMax 的 `pptx-generator` Skill 发布在 GitHub（[MiniMax-AI/skills](https://github.com/MiniMax-AI/skills)），采用 **MIT 许可证**，明确允许商业使用和修改。

#### 可直接复用的部分

| 组件 | 说明 | 复用方式 |
|------|------|---------|
| **Theme 设计系统** | 5 色配色体系 `{primary, secondary, accent, light, bg}` | 直接移植为 python-pptx 的 Theme 类 |
| **页面类型分类** | 5 种类型：Cover / TOC / Section / Content / Summary | 直接复用为内容规划的 layout_type 枚举 |
| **设计规范** | 字体（Microsoft YaHei + Arial）、尺寸（16:9）、颜色格式（hex） | 直接复用 |
| **设计风格配方** | Sharp / Soft / Rounded / Pill 四种视觉风格 | 参考 `design-system.md` 转译为 python-pptx 形状参数 |
| **页面编号徽章** | 圆形/药丸形页码 | python-pptx 实现 |
| **内容规划 JSON 结构** | slideConfig 格式 | 直接复用 |

#### 需要适配的部分

| 原始（PptxGenJS/Node.js） | 适配目标（python-pptx/Python） | 工作量 |
|---------------------------|-------------------------------|--------|
| `slide.addText(...)` JS 调用 | python-pptx 的 `slide.shapes.add_textbox(...)` | 低（API 一一对应） |
| `slide.addShape(...)` | python-pptx 的 `slide.shapes.add_shape(...)` | 低 |
| `slide.addChart(...)` | python-pptx 的 `slide.shapes.add_chart(...)` | 低 |
| `compile.js` 编译脚本 | Python 主生成脚本 | 中 |
| 每页独立 JS 文件可并行生成 | Python 串行或 asyncio 并行 | 低 |
| `markitdown` 读取 PPTX 内容 | `python-pptx` 直接读取 | 低 |

#### 嫁接实施方案

```
MiniMax 方案移植路线：

1. 从 GitHub 下载 MiniMax-AI/skills/skills/pptx-generator/ 全部文件
   ├── SKILL.md           → 内容规划 Prompt（核心）
   ├── design-system.md   → 配色/字体/风格配方（核心）
   ├── slide-types.md     → 5 种页面类型的布局规范（核心）
   ├── pitfalls.md        → QA 检查清单（参考）
   ├── editing.md         → 模板编辑工作流（参考）
   └── pptxgenjs.md       → PptxGenJS API 参考（转译用）

2. 将 JS 布局代码转译为 python-pptx 等价代码
   - 每种 slide type 的 JS 示例 → 转为 Python 函数
   - Theme 对象 → Python dataclass
   - 风格配方 → Python 常量/配置

3. 集成到我们的 PPT 工具中
   - LLM 内容规划 Prompt 借鉴 SKILL.md 的指引
   - python-pptx 生成层使用转译后的布局代码
   - 模板分析器保持独立（MiniMax 没有此能力）
```

#### 风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| MIT 许可证合规 | 低 | 保留原始版权声明即可 |
| PptxGenJS → python-pptx 功能差异 | 中 | 部分高级效果（如圆角矩形）需手动实现 |
| 设计质量不如原始 JS 方案 | 中 | 逐步调优，以模板占位符为主要布局方式 |
| 依赖 Node.js | 无 | 完全移植到 Python，不依赖 Node.js |

### 5.7 豆包合作的排版引擎（博思 AIPPT）合作评估

**结论：可以合作，接入门槛低，但需权衡成本和可控性。**

#### 博思 AIPPT 开放平台

| 项目 | 详情 |
|------|------|
| **开放平台** | [open.aippt.cn](https://open.aippt.cn) |
| **公司** | 深圳市博思云创科技（万兴科技旗下，A股上市） |
| **接入方式** | API（服务端）+ SDK（前端 iframe 嵌入） |
| **接入流程** | 在线申请 → 2 个工作日审核 → 获取 APP Key → 调用 API |
| **API 能力** | 创建任务 → 标题生成大纲 → 大纲生成内容 → 选择模板 → 生成 PPT |
| **输出格式** | 平台专有格式（在线编辑）+ 可导出 |

#### 合作可行性分析

| 维度 | 评估 |
|------|------|
| **接入难度** | ⭐ 低。申请+获取 Key 即可，API 文档完善 |
| **成本** | ¥0.32-0.49/次（按量付费），企业可谈批量价 |
| **设计质量** | ⭐⭐⭐⭐⭐ 专业设计团队打造的模板库 |
| **中文支持** | ⭐⭐⭐⭐⭐ 本土产品，中文场景最佳 |
| **可编辑性** | ⚠️ 在线编辑器可编辑，但导出的 PPTX 可能损失格式 |
| **可控性** | ⭐⭐ 依赖第三方服务，无法自定义模板分析逻辑 |
| **数据安全** | ⚠️ 内容需发送到博思服务器，涉及用户数据隐私 |
| **模板分析** | ❌ 不提供模板分析 API，只能使用博思的模板库 |
| **自定义模板** | ❌ 不支持上传自有模板 |

#### 建议策略

**博思 AIPPT 不适合作为核心引擎**（因为不支持自定义模板分析、数据需外传），但可以作为 **可选的快速生成路径**：

```
我们的 PPT 工具提供两种生成模式：

模式 A：自建引擎（默认）
  - LLM 内容规划 + python-pptx 生成
  - 支持自定义模板分析
  - 数据不出系统
  - 适合：有模板需求、数据敏感、需要完全控制

模式 B：博思 AIPPT 引擎（可选）
  - 调用 open.aippt.cn API
  - 使用博思模板库
  - 生成速度快、设计质量高
  - 适合：快速出活、无自定义模板需求、用户同意数据外传
```

#### 与其他第三方 API 的横向对比

| 维度 | 博思 AIPPT | 讯飞智文 | SlideForge |
|------|-----------|---------|------------|
| 接入难度 | 低（申请即用） | 中（¥1344 起购） | 低（$3 免费额度） |
| 单次成本 | ¥0.32-0.49 | ~¥1.34 | $0.03-0.20 |
| 中文质量 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐ |
| 输出格式 | 平台格式+导出 | PPTX | 原生 PPTX |
| 自定义模板 | ❌ | ⚠️ 有限 | ✅ 内置50+ |
| 数据本地化 | ❌ | ❌ | ❌ |
| **推荐优先级** | **1** | 3 | 2 |

---

## 6. 技术细节参考

### 6.1 PPTX 文件内部结构

```
.pptx (ZIP 包)
├── [Content_Types].xml       # 内容类型定义
├── _rels/
│   └── .rels                 # 全局关系
├── ppt/
│   ├── presentation.xml      # 演示文稿主文件
│   ├── slides/               # 幻灯片
│   │   ├── slide1.xml
│   │   ├── slide2.xml
│   │   └── ...
│   ├── slideLayouts/         # 幻灯片布局（模板中的页面类型）
│   │   ├── slideLayout1.xml
│   │   └── ...
│   ├── slideMasters/         # 幻灯片母版
│   │   └── slideMaster1.xml
│   ├── theme/                # 主题（颜色、字体）
│   │   └── theme1.xml
│   ├── media/                # 图片等媒体
│   └── charts/               # 图表
├── docProps/                 # 文档属性
└── ...
```

**关键概念**：
- **Slide Master** → 定义全局样式，一个模板通常有 1 个
- **Slide Layout** → 继承 Slide Master，定义页面布局类型，一个模板通常有 5-20 个
- **Slide** → 继承 Slide Layout，实际的内容页面
- **Placeholder** → Layout 中预定义的内容区域（标题、正文、图片等）

### 6.2 python-pptx 常见操作速查

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# === 创建/加载 ===
prs = Presentation()                         # 新建
prs = Presentation('template.pptx')          # 加载模板

# === Slide Layout 操作 ===
layout = prs.slide_layouts[0]                # 获取布局
slide = prs.slides.add_slide(layout)         # 使用布局创建幻灯片

# === 占位符操作 ===
for ph in slide.placeholders:
    ph.text = "新内容"                        # 填充文本

# === 自由形状 ===
txBox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
tf = txBox.text_frame
tf.text = "自由文本"

# === 表格 ===
table = slide.shapes.add_table(rows=3, cols=2, left=Inches(1), top=Inches(2),
                                width=Inches(8), height=Inches(3)).table
table.cell(0, 0).text = "表头1"

# === 图片 ===
slide.shapes.add_picture('image.png', Inches(1), Inches(1), Inches(4), Inches(3))

# === 保存 ===
prs.save('output.pptx')
```

---

## 7. 风险与注意事项

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| python-pptx 不支持动画 | 输出 PPT 无动画效果 | 可接受，用户可在 PowerPoint 中手动添加 |
| 模板多样性导致匹配困难 | 非标准模板分析不准 | 提供模板规范性指南 + LLM 辅助判断 |
| 坐标硬编码维护成本高 | 每种布局需单独适配 | 以模板占位符为主要填充方式，减少自由形状 |
| 中文字体兼容性 | 不同系统字体渲染不同 | 使用通用字体（微软雅黑/思源黑体） |
| 第三方 API 稳定性 | 服务中断影响体验 | 自建为主，API 为辅 |
| 组合形状内文本替换 | python-pptx 无法遍历组合形状内部 | 使用 python-pptx-text-replacer 或 XML 操作 |

---

## 8. 参考资源

- [python-pptx 官方文档](https://python-pptx.readthedocs.io/)
- [python-pptx GitHub](https://github.com/scanny/python-pptx)
- [AiPPT 开放平台 API](https://open.aippt.cn/docs/js/service.html)
- [讯飞智文 PPT API](https://www.xfyun.cn/doc/spark/PPTGeneration.html)
- [SlideForge API](https://slideforge.dev/guides/powerpoint-api)
- [PPTAgent (EMNLP 2025)](https://github.com/icip-cas/PPTAgent)
- [Presenton 开源项目](https://github.com/presenton/presenton)
- [md2pptx Markdown→PPTX](https://github.com/MartinPacker/md2pptx)
- [MarpToPptx 模板诊断](https://github.com/jongalloway/MarpToPptx/issues/85)
- [SlideForge vs python-pptx 对比](https://slideforge.dev/blog/generate-powerpoint-python)
- [Presentation API 2026 对比](https://slideforge.dev/blog/presentation-apis-2026)
- [豆包 PPT 生成全流程拆解](https://deepseek.csdn.net/69f6a4f254b52172bc7181e5.html)
- [豆包原生 PPT 支持（知乎）](https://zhuanlan.zhihu.com/p/1996192344270198513)
- [豆包 PPT 对接博思/迅捷（太平洋科技）](https://www.pconline.com.cn/ai/1983/19834220.html)
- [bytedance/ppt-generation Skill（Decision Hub）](https://hub.decision.ai/skills/bytedance/ppt-generation)
- [MiniMax pptx-generator Skill（GitHub）](https://github.com/MiniMax-AI/skills/blob/main/skills/pptx-generator/SKILL.md)
- [PPTAgent 论文（EMNLP 2025）](https://arxiv.org/abs/2501.03936)
