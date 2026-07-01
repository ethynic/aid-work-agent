# PPT 工具设计文档

> 版本: v1.0 | 日期: 2026-05-09 | 状态: ✅ 基础版本已实现，后续能力由 [PPT 工具增强设计](ppt_tool_enhancement_design.md) 接续
> 设计参考: [MiniMax pptx-generator](https://github.com/MiniMax-AI/skills/blob/main/skills/pptx-generator/SKILL.md) (MIT License)
>
> 文档定位：保留用于追溯初始架构决策；当前实现、入参和交付能力以增强设计及其开发计划为准。

## 1. 功能概述

PPT 工具支持三种生成模式：

| 模式 | 输入 | 输出 | 说明 |
|------|------|------|------|
| **一键生成** | 主题/大纲/Markdown 内容 | 原生可编辑 .pptx | 用户输入主题，AI 规划内容，自动生成 PPT |
| **模板生成** | 用户上传 .pptx 模板 + 内容 | 原生可编辑 .pptx | 分析模板布局，智能匹配内容到页面 |
| **HTML 转换** | HTML+CSS 内容 | 原生可编辑 .pptx | 将 HTML 页面结构映射为 PPT 页面 |

**核心原则**：所有输出均为原生 .pptx（文本/形状/图表可编辑），不使用图片组装。

---

## 2. 系统架构

```
用户输入
  │
  ├─── 模式 A: 主题/内容 → 一键生成
  ├─── 模式 B: 模板 + 内容 → 模板生成
  └─── 模式 C: HTML+CSS → HTML 转换
       │
       ▼
┌──────────────────────────────────────────────────┐
│  Layer 1: 内容规划层                               │
│                                                    │
│  LLM 将输入转化为统一的结构化 PPT 大纲 JSON       │
│  - 每页: type, title, points, chart_data, notes   │
│  - 5 种页面类型: cover/toc/section/content/summary │
│  - 可选配图描述（用于后续 AI 插图）               │
└──────────────────────┬───────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ 内置主题  │  │ 用户模板  │  │ HTML 解析 │
  │ Theme    │  │ 分析器   │  │ 器       │
  │ 选择     │  │ Template │  │ HTML     │
  │          │  │ Analyzer │  │ Parser   │
  └────┬─────┘  └────┬─────┘  └────┬─────┘
       │              │              │
       └──────────────┼──────────────┘
                      ▼
┌──────────────────────────────────────────────────┐
│  Layer 2: 布局匹配层                              │
│                                                    │
│  内容大纲 + 布局信息 → 每页选择具体布局           │
│  - 规则匹配: cover→封面布局, content→内容布局     │
│  - 模板模式: 分析占位符类型 → 匹配内容            │
│  - HTML 模式: HTML 结构 → 对应页面类型            │
└──────────────────────┬───────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────┐
│  Layer 3: PPT 生成层 (python-pptx)                │
│                                                    │
│  遍历每页 → 创建 Slide → 填充内容 → 保存 .pptx   │
│  - 18 套配色方案 + 4 种风格配方                    │
│  - 5 种页面类型各自的布局渲染器                    │
│  - 图表/表格/图片支持                             │
└──────────────────────────────────────────────────┘
```

---

## 3. 设计系统（参考 MiniMax，适配 python-pptx）

### 3.1 Theme 对象

```python
from dataclasses import dataclass
from typing import Tuple

@dataclass
class PPTTheme:
    """PPT 主题配色 — 5 色体系"""
    primary: str       # 最深色，标题文字    e.g. "22223b"
    secondary: str     # 次要色，正文文字    e.g. "4a4e69"
    accent: str        # 强调色，高亮/按钮  e.g. "9a8c98"
    light: str         # 浅色强调，背景装饰  e.g. "c9ada7"
    bg: str            # 页面背景色          e.g. "f2e9e4"

    # 字体
    font_cn: str = "微软雅黑"      # 中文字体
    font_en: str = "Arial"         # 英文字体

    # 风格
    style: str = "soft"            # sharp / soft / rounded / pill
```

### 3.2 18 套配色方案

| # | 名称 | primary | secondary | accent | light | bg | 适用场景 |
|---|------|---------|-----------|--------|-------|----|---------|
| 1 | 现代健康 | 006d77 | 83c5be | edf6f9 | ffddd2 | e29578 | 医疗、咨询、护肤 |
| 2 | 商务权威 | 2b2d42 | 8d99ae | edf2f4 | ef233c | d90429 | 年报、金融、企业 |
| 3 | 自然户外 | 606c38 | 283618 | fefae0 | dda15e | bc6c25 | 户外、环保、农业 |
| 4 | 复古学术 | 780000 | c1121f | fdf0d5 | 003049 | 669bbc | 学术讲座、历史、博物馆 |
| 5 | 柔和创意 | cdb4db | ffc8dd | ffafcc | bde0fe | a2d2ff | 母婴、甜品、时尚 |
| 6 | 波西米亚 | ccd5ae | e9edc9 | fefae0 | faedcd | d4a373 | 婚礼策划、家居、有机食品 |
| 7 | 活力科技 | 8ecae6 | 219ebc | 023047 | ffb703 | fb8500 | 体育、健身、初创、教育 |
| 8 | 工匠艺术 | 7f5539 | a68a64 | ede0d4 | 656d4a | 414833 | 咖啡、手工艺、传统文化 |
| 9 | 深夜科技 | 000814 | 001d3d | 003566 | ffc300 | ffd60a | 科技发布、天文、汽车 |
| 10 | 教育图表 | 264653 | 2a9d8f | e9c46a | f4a261 | e76f51 | 统计报告、教育、市场分析 |
| 11 | 森林生态 | dad7cd | a3b18a | 588157 | 3a5a40 | 344e41 | 景观设计、ESG、环保 |
| 12 | 优雅时尚 | edafb8 | f7e1d7 | dedbd2 | b0c4b1 | 4a5759 | 高定、画廊、美妆 |
| 13 | 艺术美食 | 335c67 | fff3b0 | e09f3e | 9e2a2b | 540b0e | 美食、艺术展、民族风 |
| 14 | 奢华神秘 | 22223b | 4a4e69 | 9a8c98 | c9ada7 | f2e9e4 | 珠宝、酒店、高端咨询 |
| 15 | 纯净科技蓝 | 03045e | 0077b6 | 00b4d8 | 90e0ef | caf0f8 | 云/AI、医院、清洁能源 |
| 16 | 海岸珊瑚 | 0081a7 | 00afb9 | fdfcdc | fed9b7 | f07167 | 旅游、夏季活动、饮料 |
| 17 | 活力橙薄荷 | ff9f1c | ffbf69 | ffffff | cbf3f0 | 2ec4b6 | 儿童活动、促销、快消 |
| 18 | 铂金白金 | 0a0a0a | 0070F3 | D4AF37 | f5f5f5 | ffffff | 高端产品、企业站、金融 |

### 3.3 4 种视觉风格

| 风格 | 圆角 | 间距 | 适用 |
|------|------|------|------|
| **Sharp** (利落紧凑) | 0~0.05" | 紧凑 | 数据报表、专业报告 |
| **Soft** (柔和均衡) | 0.08~0.12" | 适中 | 企业商务、通用 |
| **Rounded** (圆润宽松) | 0.15~0.25" | 宽松 | 产品介绍、营销、创意 |
| **Pill** (胶囊轻盈) | 0.3~0.5" | 开放 | 品牌展示、发布会 |

### 3.4 字号规范

| 用途 | 字号 (pt) |
|------|----------|
| 数据标注/来源 | 10~12 |
| 正文/描述 | 14~16 |
| 副标题 | 18~22 |
| 页面标题 | 28~36 |
| 大标题 | 44~60 |
| 数据亮点 | 60~96 |

### 3.5 画布规格

| 项目 | 值 |
|------|-----|
| 宽度 | 13.333" (33.867 cm) |
| 高度 | 7.5" (19.05 cm) |
| 比例 | 16:9 |
| 页码位置 | x: 12.3", y: 6.8" |
| 安全边距 | 0.4~0.6" |

---

## 4. 内容规划 JSON 格式

LLM 输出的统一大纲结构：

```json
{
  "title": "2026年Q1季度汇报",
  "theme_id": 14,
  "style": "soft",
  "slides": [
    {
      "type": "cover",
      "title": "2026年第一季度绩效汇报",
      "subtitle": "华东销售部 · 2026年4月",
      "presenter": "张三",
      "date": "2026-04-15"
    },
    {
      "type": "toc",
      "title": "目录",
      "sections": [
        {"number": "01", "title": "核心业绩"},
        {"number": "02", "title": "重点项目"},
        {"number": "03", "title": "下季规划"}
      ]
    },
    {
      "type": "section",
      "number": "01",
      "title": "核心业绩",
      "intro": "第一季度关键指标概览"
    },
    {
      "type": "content",
      "layout": "bullets",
      "title": "核心业绩指标",
      "points": [
        "营收 ¥1,240万，同比增长 18%",
        "新增客户 847 家，完成率 106%",
        "客户留存率 94.2%"
      ]
    },
    {
      "type": "content",
      "layout": "chart",
      "title": "月度营收趋势",
      "chart": {
        "type": "bar",
        "labels": ["1月", "2月", "3月"],
        "series": [{"name": "营收(万)", "values": [380, 420, 440]}]
      },
      "takeaway": "3月营收创历史新高"
    },
    {
      "type": "content",
      "layout": "comparison",
      "title": "竞品对比",
      "left": {"title": "我方优势", "items": ["技术领先", "响应快"]},
      "right": {"title": "改进方向", "items": ["品牌", "渠道"]}
    },
    {
      "type": "content",
      "layout": "stat",
      "title": "关键数字",
      "stats": [
        {"value": "¥1,240万", "label": "营收", "trend": "+18%"},
        {"value": "847", "label": "新客户", "trend": "+23%"}
      ]
    },
    {
      "type": "summary",
      "title": "总结与展望",
      "takeaways": ["Q1超额完成目标", "客户增长强劲"],
      "next_steps": ["Q2冲刺¥1,500万", "开拓华东三线城市"],
      "contact": "zhangsan@example.com"
    }
  ]
}
```

### 页面类型与布局映射

| type | layout 可选值 | 说明 |
|------|-------------|------|
| `cover` | — | 封面页，无页码 |
| `toc` | `vertical` / `grid` / `sidebar` / `cards` | 目录页 |
| `section` | `center` / `left-accent` / `split` | 章节分隔页 |
| `content` | `bullets` / `chart` / `comparison` / `stat` / `timeline` / `image` | 内容页（6 种子布局） |
| `summary` | `takeaways` / `cta` / `thankyou` / `split` | 总结页 |

---

## 5. 模块设计

### 5.1 目录结构

```
src/tools/ppt/
├── __init__.py
├── ppt_tool.py              # PPT 工具入口（BaseTool 实现）
├── ppt_router.py            # FastAPI 路由（下载/模板上传）
├── planner.py               # LLM 内容规划器
├── generator.py             # PPT 生成引擎（核心）
├── theme.py                 # Theme 配色系统 + 18 套方案
├── layouts/                 # 各页面类型的布局渲染器
│   ├── __init__.py
│   ├── cover.py             # 封面页渲染
│   ├── toc.py               # 目录页渲染
│   ├── section.py           # 章节分隔页渲染
│   ├── content.py           # 内容页渲染（bullets/stat/comparison/timeline/image）
│   ├── chart.py             # 图表页渲染
│   └── summary.py           # 总结页渲染
├── template_analyzer.py     # 用户模板分析器
├── html_parser.py           # HTML+CSS → PPT 大纲转换器
├── html_layout_mapper.py    # HTML 元素 → PPT 形状映射
└── utils.py                 # 通用工具函数（颜色/字体/单位转换）
```

### 5.2 PPT 工具入口 (ppt_tool.py)

```python
from typing import Any, Dict, Optional
from pydantic import Field
from src.tools.base import BaseTool

class PPTGenerateTool(BaseTool):
    name = "ppt_generate"
    description = "生成可编辑的 PowerPoint 演示文稿。支持一键生成、基于模板生成、HTML转换三种模式。"
    display_name = "PPT生成"

    class InputModel(BaseModel):
        mode: str = Field(
            description="生成模式: 'auto'(一键生成) | 'template'(模板生成) | 'html'(HTML转换)",
            pattern="^(auto|template|html)$"
        )
        topic: Optional[str] = Field(None, description="PPT主题（auto模式）")
        content: Optional[str] = Field(None, description="Markdown格式的内容大纲")
        template_path: Optional[str] = Field(None, description="模板文件路径（template模式）")
        html_content: Optional[str] = Field(None, description="HTML+CSS内容（html模式）")
        theme_id: Optional[int] = Field(None, description="配色方案ID(1-18)，不指定则自动选择")
        style: Optional[str] = Field("soft", description="视觉风格: sharp/soft/rounded/pill")
        slide_count: Optional[int] = Field(None, description="期望页数（auto模式），默认由内容决定")

    async def execute(self, **kwargs) -> Dict[str, Any]:
        ...
```

### 5.3 LLM 内容规划器 (planner.py)

```python
class PPTPlanner:
    """调用 LLM 将用户输入转化为结构化 PPT 大纲"""

    SYSTEM_PROMPT = """你是一个专业的PPT内容规划师。根据用户输入的主题或内容，
生成一份结构化的PPT大纲。

规则：
1. 每页必须有明确的 type（cover/toc/section/content/summary）
2. 内容页的 layout 必须从以下选择：bullets/chart/comparison/stat/timeline/image
3. 封面页必须有 title 和 subtitle
4. 目录页必须列出所有 section
5. 每 3-5 个内容页之间插入一个 section 分隔页
6. 最后必须是 summary 总结页
7. 内容简洁精炼，每页要点不超过 5 条
8. 标题不超过 20 字，要点不超过 30 字

输出严格的 JSON 格式，不要包含 markdown 代码块标记。"""

    async def plan_from_topic(self, topic: str, slide_count: int = None) -> dict:
        """从主题生成大纲"""
        ...

    async def plan_from_content(self, content: str) -> dict:
        """从 Markdown 内容生成大纲"""
        ...

    async def plan_from_html(self, html: str) -> dict:
        """从 HTML 内容生成大纲"""
        ...
```

### 5.4 PPT 生成引擎 (generator.py)

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from src.tools.ppt.theme import PPTTheme, THEME_PRESETS

class PPTGenerator:
    """PPT 生成核心引擎"""

    def __init__(self, theme: PPTTheme = None):
        self.theme = theme or THEME_PRESETS[14]  # 默认奢华神秘
        self.prs = Presentation()
        # 16:9 标准尺寸
        self.prs.slide_width = Inches(13.333)
        self.prs.slide_height = Inches(7.5)

    def generate(self, plan: dict) -> str:
        """根据大纲生成 PPT，返回文件路径"""
        for i, slide_data in enumerate(plan["slides"]):
            slide_type = slide_data["type"]
            slide = self._create_slide(slide_type, slide_data, i + 1, len(plan["slides"]))
        output_path = f"storage/ppt/{plan['title']}.pptx"
        self.prs.save(output_path)
        return output_path

    def _create_slide(self, slide_type: str, data: dict, index: int, total: int):
        """根据类型调用对应的布局渲染器"""
        renderers = {
            "cover": self._render_cover,
            "toc": self._render_toc,
            "section": self._render_section,
            "content": self._render_content,
            "summary": self._render_summary,
        }
        renderer = renderers.get(slide_type)
        if renderer:
            renderer(data, index, total)

    def _render_cover(self, data, index, total):
        """封面页 — 居中大标题 + 副标题"""
        slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])  # blank
        # 背景
        background = slide.background
        fill = background.fill
        fill.solid()
        fill.fore_color.rgb = RGBColor.from_string(self.theme.bg)
        # 大标题
        self._add_text(slide, data["title"],
            x=1.5, y=2.0, w=10.3, h=2.0,
            font_size=Pt(54), bold=True, color=self.theme.primary,
            alignment=PP_ALIGN.CENTER)
        # 副标题
        self._add_text(slide, data.get("subtitle", ""),
            x=2.5, y=4.2, w=8.3, h=1.0,
            font_size=Pt(22), bold=False, color=self.theme.secondary,
            alignment=PP_ALIGN.CENTER)
        # 演讲者 + 日期
        meta = f'{data.get("presenter", "")}  ·  {data.get("date", "")}'
        self._add_text(slide, meta,
            x=3.0, y=5.5, w=7.3, h=0.6,
            font_size=Pt(14), bold=False, color=self.theme.accent,
            alignment=PP_ALIGN.CENTER)
        # 封面无页码

    def _render_content_bullets(self, slide, data):
        """内容页 — 要点列表"""
        # 标题
        self._add_text(slide, data["title"],
            x=0.8, y=0.5, w=11.7, h=1.0,
            font_size=Pt(36), bold=True, color=self.theme.primary)
        # 标题下装饰线
        self._add_accent_line(slide, x=0.8, y=1.4, w=2.0)
        # 要点
        points = data.get("points", [])
        for i, point in enumerate(points):
            y = 2.0 + i * 0.8
            # 要点标记圆点
            self._add_dot(slide, x=1.0, y=y + 0.12, color=self.theme.accent)
            # 要点文本
            self._add_text(slide, point,
                x=1.5, y=y, w=10.5, h=0.6,
                font_size=Pt(16), bold=False, color=self.theme.secondary)

    def _render_content_stat(self, slide, data):
        """内容页 — 数据亮点"""
        stats = data.get("stats", [])
        n = len(stats)
        card_width = min(3.0, (11.7 - (n - 1) * 0.4) / n)
        start_x = (13.333 - n * card_width - (n - 1) * 0.4) / 2
        for i, stat in enumerate(stats):
            x = start_x + i * (card_width + 0.4)
            # 卡片背景
            self._add_rounded_rect(slide, x, 2.0, card_width, 3.0,
                fill_color=self.theme.light)
            # 数值
            self._add_text(slide, stat["value"],
                x=x, y=2.5, w=card_width, h=1.2,
                font_size=Pt(42), bold=True, color=self.theme.primary,
                alignment=PP_ALIGN.CENTER)
            # 标签
            self._add_text(slide, stat["label"],
                x=x, y=3.8, w=card_width, h=0.5,
                font_size=Pt(14), bold=False, color=self.theme.accent,
                alignment=PP_ALIGN.CENTER)
            # 趋势
            if stat.get("trend"):
                self._add_text(slide, stat["trend"],
                    x=x, y=4.4, w=card_width, h=0.4,
                    font_size=Pt(16), bold=True, color="22c55e",
                    alignment=PP_ALIGN.CENTER)

    # ... _render_toc, _render_section, _render_content_chart,
    #     _render_content_comparison, _render_content_timeline,
    #     _render_summary 等方法
```

### 5.5 模板分析器 (template_analyzer.py)

```python
class TemplateAnalyzer:
    """分析用户上传的 .pptx 模板，提取布局特征"""

    def analyze(self, template_path: str) -> dict:
        """返回模板分析结果"""
        prs = Presentation(template_path)
        return {
            "slide_width": prs.slide_width,
            "slide_height": prs.slide_height,
            "theme_colors": self._extract_theme_colors(prs),
            "layouts": [self._analyze_layout(layout) for layout in prs.slide_layouts],
        }

    def _analyze_layout(self, layout) -> dict:
        """分析单个 Slide Layout"""
        return {
            "index": layout.index,
            "name": layout.name,
            "inferred_type": self._infer_type(layout),
            "placeholders": [
                {
                    "idx": ph.placeholder_format.idx,
                    "type": str(ph.placeholder_format.type),
                    "name": ph.name,
                    "position": {"left": ph.left, "top": ph.top,
                                 "width": ph.width, "height": ph.height},
                }
                for ph in layout.placeholders
            ],
        }

    def _infer_type(self, layout) -> str:
        """推断布局类型"""
        name = layout.name.lower()
        ph_types = [str(ph.placeholder_format.type) for ph in layout.placeholders]
        if "title" in name and "content" not in name:
            return "cover"
        if "section" in name or "header" in name:
            return "section"
        if "blank" in name:
            return "content"
        return "content"

    def match_content_to_layouts(self, plan: dict, analysis: dict) -> list:
        """将内容大纲的每页匹配到模板的最佳 Layout"""
        matches = []
        layouts = analysis["layouts"]
        for slide in plan["slides"]:
            slide_type = slide["type"]
            # 优先找类型匹配的
            candidates = [l for l in layouts if l["inferred_type"] == slide_type]
            if not candidates:
                candidates = [l for l in layouts if l["inferred_type"] == "content"]
            chosen = candidates[0] if candidates else layouts[0]
            matches.append({"slide": slide, "layout": chosen})
        return matches
```

模板模式下的生成流程：

```python
class TemplatePPTGenerator:
    """基于用户模板的 PPT 生成"""

    def generate(self, template_path: str, plan: dict) -> str:
        analyzer = TemplateAnalyzer()
        analysis = analyzer.analyze(template_path)
        matches = analyzer.match_content_to_layouts(plan, analysis)

        prs = Presentation(template_path)
        # 删除模板中已有幻灯片（保留 layouts）
        while len(prs.slides) > 0:
            rId = prs.slides._sldIdLst[0].get(qn('r:id'))
            prs.part.drop_rel(rId)
            del prs.slides._sldIdLst[0]

        for match in matches:
            layout = prs.slide_layouts[match["layout"]["index"]]
            slide = prs.slides.add_slide(layout)
            self._fill_placeholders(slide, match["slide"])

        output = f"storage/ppt/{plan['title']}.pptx"
        prs.save(output)
        return output

    def _fill_placeholders(self, slide, data: dict):
        """填充占位符"""
        for ph in slide.placeholders:
            idx = ph.placeholder_format.idx
            ph_type = str(ph.placeholder_format.type)
            if idx == 0 or "TITLE" in ph_type:
                ph.text = data.get("title", "")
            elif idx == 1 or "BODY" in ph_type or "SUBTITLE" in ph_type:
                if data.get("points"):
                    ph.text = "\n".join(data["points"])
                else:
                    ph.text = data.get("subtitle", data.get("intro", ""))
```

### 5.6 HTML 解析器 (html_parser.py)

将 HTML+CSS 内容转换为 PPT 大纲和布局。

```python
from bs4 import BeautifulSoup

class HTMLPPTParser:
    """将 HTML+CSS 转换为 PPT 大纲 JSON"""

    def parse(self, html_content: str) -> dict:
        """解析 HTML，生成 PPT 大纲"""
        soup = BeautifulSoup(html_content, 'html.parser')
        slides = []

        # 策略 1: 按 <section> / <article> 分页
        sections = soup.find_all(['section', 'article'])
        if sections:
            for i, sec in enumerate(sections):
                slides.append(self._parse_section(sec, i))
        else:
            # 策略 2: 按 <h1>/<h2> 标题分页
            slides = self._split_by_headings(soup)

        # 确保首尾类型正确
        if slides and slides[0]["type"] != "cover":
            slides.insert(0, {"type": "cover", "title": slides[0].get("title", "")})
        if slides and slides[-1]["type"] != "summary":
            slides.append({"type": "summary", "title": "总结"})

        return {
            "title": self._extract_title(soup) or "演示文稿",
            "slides": slides,
        }

    def _parse_section(self, section, index: int) -> dict:
        """解析一个 HTML section 为幻灯片"""
        h1 = section.find(['h1', 'h2'])
        title = h1.get_text(strip=True) if h1 else f"第{index+1}页"

        # 检测内容类型
        has_table = section.find('table') is not None
        has_chart = section.find(class_=re.compile(r'chart|graph|canvas'))
        has_img = section.find('img') is not None
        has_ul_ol = section.find(['ul', 'ol']) is not None

        if has_chart:
            layout = "chart"
        elif has_table:
            layout = "comparison"
        elif has_img and has_ul_ol:
            layout = "image"
        else:
            layout = "bullets"

        # 提取要点
        points = []
        for li in section.find_all('li'):
            points.append(li.get_text(strip=True))
        if not points:
            paragraphs = section.find_all('p')
            points = [p.get_text(strip=True) for p in paragraphs[:5]]

        return {
            "type": "content",
            "layout": layout,
            "title": title,
            "points": points,
            "html_images": [img.get('src') for img in section.find_all('img')],
        }

    def _split_by_headings(self, soup) -> list:
        """按标题标签分割为多页"""
        ...

    def _extract_table_data(self, table) -> dict:
        """提取 HTML 表格为 PPT 表格数据"""
        ...

    def _extract_style_mapping(self, element) -> dict:
        """提取 CSS 样式映射为 PPT 格式参数"""
        style = element.get('style', '')
        mapping = {}
        if 'color:' in style:
            mapping['font_color'] = self._parse_css_color(style)
        if 'background' in style:
            mapping['bg_color'] = self._parse_css_color(style, prop='background')
        if 'font-size' in style:
            mapping['font_size'] = self._parse_css_size(style)
        if 'text-align' in style:
            mapping['alignment'] = self._parse_alignment(style)
        return mapping
```

### 5.7 HTML 布局映射 (html_layout_mapper.py)

将 HTML 元素结构映射为 python-pptx 形状定位：

```python
class HTMLLayoutMapper:
    """HTML 元素 → python-pptx 形状位置映射"""

    # HTML 布局模式 → PPT 布局
    LAYOUT_PATTERNS = {
        "hero": "cover",           # 大标题+背景 → 封面
        "grid-2": "comparison",    # 两列 → 对比
        "grid-3": "stat",          # 三列 → 数据卡片
        "grid-4": "stat",          # 四列 → 数据卡片
        "list": "bullets",         # 列表 → 要点
        "timeline": "timeline",    # 时间线 → 流程
        "table": "comparison",     # 表格 → 对比
        "chart": "chart",          # 图表区 → 图表页
        "image-text": "image",     # 图文混排 → 图片页
    }

    def map_to_ppt_positions(self, html_structure: dict, slide_width: float, slide_height: float) -> list:
        """将解析的 HTML 结构转为 PPT 形状定位列表"""
        shapes = []
        margin = 0.6
        content_width = slide_width - 2 * margin
        content_height = slide_height - 2 * margin

        layout_type = html_structure.get("layout", "bullets")

        if layout_type == "hero":
            shapes.append({
                "type": "text", "content": html_structure["title"],
                "x": margin, "y": margin + 1.5, "w": content_width, "h": 2.0,
                "font_size": 54, "bold": True, "alignment": "center"
            })
        elif layout_type in ("grid-2", "comparison"):
            half_w = content_width / 2 - 0.2
            shapes.append({"type": "text", ...})  # 左列
            shapes.append({"type": "text", ...})  # 右列
        # ... 其他布局

        return shapes
```

---

## 6. API 接口设计

### 6.1 生成 PPT

```
POST /api/ppt/generate
```

```json
// 请求
{
  "mode": "auto",
  "topic": "AI在企业中的应用",
  "style": "soft",
  "theme_id": 7,
  "slide_count": 10
}

// 响应
{
  "success": true,
  "data": {
    "file_path": "storage/ppt/AI在企业中的应用.pptx",
    "download_url": "/api/ppt/download?file=storage/ppt/AI在企业中的应用.pptx",
    "slide_count": 10,
    "preview_images": ["storage/ppt/preview/slide_01.png", ...]
  }
}
```

### 6.2 上传模板生成

```
POST /api/ppt/generate-from-template
Content-Type: multipart/form-data
```

```
template: <.pptx 文件>
content: "# AI在企业中的应用\n## 1. 概述\n..."
theme_id: 7
```

### 6.3 HTML 转换

```
POST /api/ppt/generate-from-html
```

```json
{
  "html_content": "<html><body><section><h1>标题</h1>...</section></body></html>",
  "theme_id": 14,
  "style": "soft"
}
```

### 6.4 获取配色方案列表

```
GET /api/ppt/themes
```

```json
{
  "success": true,
  "data": [
    {"id": 1, "name": "现代健康", "colors": {...}, "preview_url": "..."},
    ...
  ]
}
```

### 6.5 下载 PPT

```
GET /api/ppt/download?file=storage/ppt/xxx.pptx
```

---

## 7. HTML 转换详细设计

### 7.1 转换策略

HTML 转 PPT 采用**结构映射**而非截图/图片方案，确保输出可编辑：

```
HTML 元素          →  PPT 元素
────────────────────────────────
<h1>, <h2>        →  标题文本框
<p>               →  正文文本框
<ul>/<ol>/<li>    →  要点列表
<table>           →  PPT 表格
<img>             →  图片形状
<section>         →  一页幻灯片
CSS color         →  字体颜色
CSS background    →  背景色
CSS font-size     →  字号
CSS text-align    →  对齐方式
CSS flex/grid     →  多列布局映射
```

### 7.2 分页算法

```
输入 HTML:
1. 按 <section>/<article> 分页 → 每个 section 一页
2. 如果没有 section，按 <h1>/<h2> 分页 → 每个 h1/h2 到下一个 h1/h2 之间为一页
3. 如果没有标题，按固定内容长度分页

CSS Grid/Flex 映射:
- grid-cols-2 / flex 两列 → comparison 布局
- grid-cols-3 / flex 三列 → stat 布局（数据卡片）
- grid-cols-4 → stat 布局
- 单列 → bullets 布局
```

### 7.3 CSS 样式映射

```python
CSS_TO_PPT_STYLE = {
    # 字号映射 (CSS px → PPT pt, 约 1px ≈ 0.75pt)
    "font-size": lambda px: Pt(max(10, min(96, int(px * 0.75)))),

    # 颜色映射
    "color": lambda css_color: parse_css_color(css_color),

    # 对齐映射
    "text-align": {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
        "justify": PP_ALIGN.JUSTIFY,
    },

    # 粗细映射
    "font-weight": {
        "bold": True, "700": True, "800": True, "900": True,
        "normal": False, "400": False,
    },
}
```

---

## 8. LLM Prompt 设计

### 8.1 内容规划 Prompt

```
你是专业的PPT内容规划师。根据用户输入生成PPT大纲。

输出规则：
1. 严格 JSON 格式，不要包含 markdown 标记
2. 页面类型只能是: cover, toc, section, content, summary
3. 内容页布局只能是: bullets, chart, comparison, stat, timeline, image
4. 每3-5个内容页后插入section分隔页
5. 开头必须有cover，最后必须有summary
6. 如有toc，紧跟cover之后
7. 标题≤20字，要点≤30字，每页要点≤5条
8. 包含数字数据时使用stat或chart布局

配色方案选择：
根据主题自动选择最合适的配色（1-18），参考：
- 商务/企业 → 2或18
- 科技/互联网 → 7或9或15
- 教育/培训 → 4或10
- 健康/医疗 → 1
- 创意/设计 → 5或12
- 环保/自然 → 3或11

输出格式：
{"title":"PPT标题","theme_id":数字,"style":"soft","slides":[...]}
```

### 8.2 模板分析辅助 Prompt（复杂模板）

当模板的 Slide Layout 名称不规范时，用 LLM 辅助判断：

```
以下是PPT模板中所有 Slide Layout 的占位符信息：
{layout_details_json}

请为每个 Layout 判断其最适合的内容类型。
类型只能从以下选择：cover, toc, section, content, summary
对于 content 类型，还需判断子布局：bullets, chart, comparison, stat, timeline, image

输出格式：
[{"layout_index": 0, "type": "cover"}, ...]
```

---

## 9. 开发计划

| 阶段 | 任务 | 工期 | 前置依赖 |
|------|------|------|---------|
| **P0** | 基础框架 + Theme 系统 + 一键生成（cover/content-bullets/summary） | 3天 | — |
| **P1** | 完整布局渲染器（toc/section/stat/chart/comparison/timeline） | 3天 | P0 |
| **P2** | 模板分析器 + 模板生成模式 | 2天 | P0 |
| **P3** | HTML 解析器 + HTML 转换模式 | 2天 | P0 |
| **P4** | API 路由 + 前端集成 | 2天 | P1+P2+P3 |
| **P5** | 图表渲染优化 + 更多内置模板 | 按需 | P1 |

**总计：约 12 个工作日完成 P0-P4**

---

## 10. 依赖与风险

### 依赖

| 依赖 | 用途 | 安装 |
|------|------|------|
| python-pptx | PPT 生成核心 | `pip install python-pptx` |
| beautifulsoup4 | HTML 解析 | `pip install beautifulsoup4` |
| Pillow | 图片处理/预览图 | `pip install Pillow` |
| lxml | HTML 高效解析 | `pip install lxml` |

### 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| python-pptx 不支持圆角矩形原生属性 | 圆角风格受限 | 通过 XML 注入或降级为直角 |
| HTML 复杂布局映射精度 | 排版偏差 | 支持常见布局模式，复杂 HTML 提示用户简化 |
| 模板多样性导致分析不准 | 匹配错误 | 优先支持标准 PowerPoint 模板 + LLM 辅助判断 |
| 中文字体在不同系统渲染差异 | 显示不一致 | 使用系统通用字体（微软雅黑/苹方） |
| 无动画支持 | 用户期望落差 | 文档说明，建议用户在 PPT 中手动添加 |
