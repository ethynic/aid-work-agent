# HTML 转 PPTX 技术可行性深度调研

> 调研日期：2026-06-12
> 场景：将精心设计的单文件 HTML 网页 PPT（WebGL 背景、CSS Grid/Flex 布局、渐变色、阴影、动画）转换为可编辑的 .pptx 文件
> 状态：✅ 调研结论已落地，当前实现见 [PPT 工具增强设计](../tools/ppt/ppt_tool_enhancement_design.md)
>
> 文档定位：保留为技术选型依据；当前支持高保真、可编辑及双文件导出，实际行为以增强设计和开发计划为准。

---

## 目录

1. [场景分析：HTML PPT 结构特征](#1-场景分析)
2. [方案一：html2image + python-pptx（截图方案）](#2-方案一截图方案)
3. [方案二：HTML 解析 + python-pptx 映射](#3-方案二解析映射方案)
4. [方案三：LibreOffice 命令行转换](#4-方案三libreoffice-转换)
5. [方案四：dom-to-pptx 等 JS 库](#5-方案四js-库方案)
6. [方案五：Codex / artifact-tool 原生可编辑生成](#6-方案五codex--artifact-tool-原生可编辑生成)
7. [方案六：Aspose.Slides 方案](#7-方案六asposeslides-方案)
8. [方案七：中间格式方案（HTML → PDF → PPTX）](#8-方案七中间格式方案)
9. [综合对比与推荐方案](#9-综合对比与推荐)

---

## 1. 场景分析

### HTML PPT 的关键技术特征

基于 `src/skills/guizang-ppt-skill/assets/template.html` 的实际代码分析：

| 特征 | 具体实现 | PPTX 映射难度 |
|------|---------|-------------|
| **WebGL Shader 背景** | 双 canvas（`#bg-light` / `#bg-dark`），GLSL 着色器渲染流体背景 | **极高** — PPTX 无原生对应 |
| **CSS 变量主题** | `--ink`, `--paper`, `--ink-rgb` 等控制全局配色 | 低 — 颜色可直接映射 |
| **CSS Grid 布局** | `grid-6`, `grid-4`, `grid-3`, `split`, `grid-2-7-5` 等 | 中 — 需计算绝对坐标 |
| **Flexbox 布局** | `.row`, `.col`, `.center` 等 | 中 — 需计算绝对坐标 |
| **渐变色** | `linear-gradient` 用于背景、荧光标记 | 中 — PPTX 支持渐变填充 |
| **box-shadow** | 卡片阴影、按钮阴影 | 中 — PPTX 有 outer shadow |
| **filter: blur()** | `backdrop-filter: blur(3px)` 遮罩 | 高 — PPTX 有 soft edge 但效果不同 |
| **Google Fonts** | Playfair Display, Noto Serif SC, IBM Plex Mono 等 | 中 — 需嵌入字体 |
| **Lucide 图标** | SVG 内联图标 | 低 — SVG 可直接嵌入 |
| **Motion 动画** | Motion One 驱动的入场动画 | **极高** — PPTX 动画系统完全不同 |
| **100vw × 100vh 页面** | 每页满屏 section，横向排列 | 低 — 标准 16:9 幻灯片 |

### 核心矛盾

HTML PPT 的视觉质感主要来自两个 PPTX **无法原生还原**的要素：
1. **WebGL 着色器背景** — 这是 GPU 实时渲染的流体效果，PPTX 没有等价物
2. **backdrop-filter: blur()** — 半透明遮罩的磨砂玻璃效果，PPTX 的 soft edge 不等价

因此，**任何方案都面临"WebGL 背景只能做截图"的现实**。

---

## 2. 方案一：截图方案（html2image + python-pptx）

### 方案描述

```
HTML 文件 → Playwright 逐页截图 → 高清 PNG → python-pptx 插入每张图片到幻灯片
```

### 技术实现

```python
from playwright.sync_api import sync_playwright
from pptx import Presentation
from pptx.util import Inches, Emu

def html_to_pptx(html_path, output_path):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            device_scale_factor=2  # 2x 高清截图 → 3840×2160 像素
        )
        page = context.new_page()
        page.goto(f'file:///{html_path}')

        # 获取所有 slide section
        slides = page.query_selector_all('section.slide')

        prs = Presentation()
        prs.slide_width = Inches(13.333)  # 16:9 标准
        prs.slide_height = Inches(7.5)

        for i, slide in enumerate(slides):
            # 滚动到当前 slide 并等待 WebGL 渲染
            page.evaluate(f'document.querySelector("#deck").style.transform = "translateX(-{i}00vw)"')
            page.wait_for_timeout(1500)  # 等待 WebGL + 动画渲染完成

            # 截图
            img_bytes = slide.screenshot(type='png')
            img_path = f'slide_{i}.png'
            with open(img_path, 'wb') as f:
                f.write(img_bytes)

            # 添加到 PPTX
            slide_layout = prs.slide_layouts[6]  # 空白布局
            ppt_slide = prs.slides.add_slide(slide_layout)
            ppt_slide.shapes.add_picture(
                img_path, Emu(0), Emu(0),
                prs.slide_width, prs.slide_height
            )

        browser.close()

    prs.save(output_path)
```

### 截图质量评估

| 参数 | 推荐设置 | 效果 |
|------|---------|------|
| viewport | 1920×1080 | 匹配标准 PPT 尺寸 |
| deviceScaleFactor | 2（Retina） | 输出 3840×2160，印刷质量 |
| 图片格式 | PNG（无损） | 无压缩伪影 |
| WebGL 等待 | 1500ms+ | 确保 shader 渲染完成 |
| 动画状态 | `animations: 'disabled'` | 跳过入场动画，截取最终态 |

**截图质量：优秀**。Playwright 使用真实 Chromium 引擎渲染，WebGL、CSS Grid、渐变、阴影都能完美呈现。deviceScaleFactor=2 时输出 3840×2160，在 PPT 中放大观看也不会模糊。

### 优点

- **还原度最高**：WebGL 背景、复杂渐变、阴影、backdrop-filter 全部精确还原
- **实现简单**：核心代码 50 行左右
- **依赖少**：Playwright + python-pptx，都是成熟库
- **稳定性高**：不依赖 HTML 结构解析，不怕 HTML 结构变化
- **项目已有依赖**：项目已安装 python-pptx（venv 中），Playwright 也有使用

### 缺点

- **不可编辑**：每页是一整张图片，无法在 PPT 中修改文字/颜色/布局
- **文件体积大**：每页一张 3840×2160 PNG 约 3-8MB，20 页 PPTX 约 60-160MB
- **文字不可搜索**：图片中的文字无法被 PPT 搜索/复制
- **打印质量**：PNG 位图在超大幅面打印时可能不够锐利

### 还原度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **95/100** | WebGL + CSS 完美渲染 |
| 可编辑性 | **0/100** | 纯图片，无法编辑 |
| 文件大小 | **30/100** | 较大 |
| 实现难度 | **90/100** | 非常简单 |

---

## 3. 方案二：HTML 解析 + python-pptx 映射

### 方案描述

```
HTML → BeautifulSoup 解析结构 → tinycss2/cssutils 解析 CSS → 计算布局 → python-pptx 生成形状/文本
```

### 技术难点分析

#### 3.1 CSS 层叠解析

BeautifulSoup **不解析 CSS**。需要：
- `tinycss2` 或 `cssutils` 解析 `<style>` 块
- 手动实现 CSS 选择器匹配、specificity 计算、继承链
- 处理 CSS 变量（`var(--ink)`）的解析和替换

对于 `template.html` 中 250+ 行的 CSS，这是一个巨大的工程。

#### 3.2 布局计算

CSS Grid/Flexbox 的布局计算需要：
- 理解 `grid-template-columns: repeat(3, 1fr)` 的实际像素计算
- 理解 `gap: 4vw 6vw` 在 1920px 视口下的实际值
- 处理 `flex: 1` 的空间分配
- 处理 `position: absolute` + `inset: 0` 的定位

**本质问题：你需要重新实现一个 CSS 布局引擎。** 这正是浏览器做的事情。

#### 3.3 WebGL 背景

BeautifulSoup 完全无法处理 `<canvas>` 上的 WebGL 渲染结果。WebGL 内容只能通过截图获取。

#### 3.4 样式映射的不可桥接差异

| CSS 特性 | PPTX 等价物 | 映射可行性 |
|---------|-----------|-----------|
| `linear-gradient` | GradientFill | 可映射，但角度/颜色停止点需要转换 |
| `box-shadow` | OuterShadow | 可映射，笛卡尔→极坐标转换 |
| `backdrop-filter: blur()` | 无等价物 | **不可映射** |
| `opacity` | Fill.fore_color + alpha | 可映射 |
| `font-family` | font.name | 可映射 |
| `letter-spacing` | font.spacing | 部分映射 |
| `text-transform: uppercase` | 需手动转换文本 | 可处理 |
| CSS 变量 `var(--ink)` | 需解析变量值 | 可处理但复杂 |
| `::before` / `::after` 伪元素 | 无等价物 | 需转为真实元素 |
| `mix-blend-mode` | 无等价物 | **不可映射** |

#### 3.5 工作量评估

| 子任务 | 预估工作量 | 难度 |
|-------|-----------|------|
| CSS 解析器 + 选择器匹配 | 3-5 天 | 高 |
| CSS 变量解析 | 1 天 | 中 |
| Grid/Flex 布局计算 | 5-10 天 | 极高 |
| 文字样式映射 | 3 天 | 中 |
| 渐变/阴影映射 | 2-3 天 | 中 |
| 伪元素处理 | 2 天 | 高 |
| 适配 guizang-ppt 模板 | 2-3 天 | 中 |
| **总计** | **18-27 天** | **极高** |

### 还原度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **40-60/100** | WebGL 无法还原，复杂布局容易错位 |
| 可编辑性 | **80/100** | 文字/形状可编辑 |
| 文件大小 | **85/100** | 原生形状，文件小 |
| 实现难度 | **10/100** | 极度困难 |

### 结论

**不推荐**。本质上是重新实现一个 CSS 渲染引擎，工作量巨大且还原度有限。WebGL 背景仍然需要截图方案补充。

---

## 4. 方案三：LibreOffice 命令行转换

### 方案描述

```bash
soffice --headless --convert-to pptx:"MS PowerPoint 2007 XML" input.html
```

### 转换质量评估

LibreOffice 的 HTML → PPTX 转换路径存在根本性问题：

| 问题 | 说明 |
|------|------|
| **非原生路径** | LibreOffice 没有直接的 HTML → Impress → PPTX 管线。它先将 HTML 作为 Writer 文档导入，再转换 |
| **CSS 支持有限** | 只能处理基本的 HTML 格式（粗体、斜体、表格、列表），复杂的 CSS Grid/Flexbox 完全不支持 |
| **WebGL 无视** | `<canvas>` 元素被忽略 |
| **字体丢失** | Google Fonts 不会嵌入 |
| **布局崩塌** | 基于 viewport 的 vw/vh 单位全部失效 |
| **版本回退** | 7.3+ 版本 PPTX↔HTML 转换功能有已知回退 |

### 实际测试预期

对于 guizang-ppt-skill 的 HTML 模板：
- WebGL 背景：**完全丢失**
- CSS Grid 布局：**全部崩塌**，内容变成线性排列
- 渐变色/阴影：**丢失**
- 字体：**回退到系统默认**
- 整体还原度：**约 10-15%**

### 还原度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **10/100** | 几乎完全丢失 |
| 可编辑性 | **70/100** | 文本可编辑但布局已崩 |
| 实现难度 | **95/100** | 一行命令 |
| 适用性 | **不适用** | 对本场景完全不适合 |

### 结论

**强烈不推荐**。LibreOffice 的 HTML 转换仅适用于非常简单的 HTML（如表格报表），对于包含 WebGL 和现代 CSS 的 HTML PPT 完全不适用。

---

## 5. 方案四：dom-to-pptx 等 JS 库

### 5.1 dom-to-pptx（推荐重点考察）

**GitHub**: [atharva9167j/dom-to-pptx](https://github.com/atharva9167j/dom-to-pptx)
**版本**: v1.1.9（2026-05-16）
**Stars**: 186 | **Forks**: 37
**License**: MIT

#### 核心原理

dom-to-pptx **不解析 CSS**，而是：
1. 在浏览器中渲染 HTML
2. 用 `getBoundingClientRect()` 读取每个元素的**计算后位置和尺寸**
3. 用 `getComputedStyle()` 读取每个元素的**计算后样式**
4. 将计算后的位置/样式映射到 PPTX 的绝对定位形状

这个方法巧妙地**绕过了 CSS 布局计算**的问题——浏览器已经做好了所有布局工作。

#### 已支持的 CSS 特性

| CSS 特性 | 支持状态 | 说明 |
|---------|---------|------|
| `background-color` | 支持 | 直接映射 |
| `linear-gradient` | 支持 | 内置 CSS Gradient Parser，转为 SVG 填充 |
| `color`, `opacity` | 支持 | 直接映射 |
| `border`, `border-radius` | 支持 | 包含逐角圆角计算 |
| `box-shadow` | 支持 | 笛卡尔→极坐标数学转换 |
| `filter: blur()` | 支持 | 转为 PPTX soft-edge |
| `backdrop-filter: blur()` | 部分支持 | 通过 html2canvas 快照模拟 |
| `transform: rotate()` | 支持 | 提取旋转角度 |
| `text-transform` | 支持 | uppercase/lowercase |
| `letter-spacing` | 支持 | 直接映射 |
| `font-*` 系列 | 支持 | 含自动字体嵌入 |
| CSS Grid/Flex | **间接支持** | 浏览器计算后只读绝对坐标，精确 |
| SVG 元素 | 支持 | 可选保持为矢量（`svgAsVector: true`） |
| `<a>` 链接 | 支持 | 映射到 PPTX 超链接 |

#### 关键特性：自动字体嵌入

dom-to-pptx 会自动：
1. 扫描 HTML 中使用的字体
2. 从 CSS 中找到字体文件的 URL
3. 将字体文件嵌入到 PPTX 中

对于 Google Fonts，需要在 `<link>` 标签上加 `crossorigin="anonymous"`。

#### 对 guizang-ppt-skill HTML 的适配性分析

| HTML 特征 | dom-to-pptx 适配性 | 说明 |
|-----------|-------------------|------|
| WebGL canvas 背景 | **不支持** | canvas 元素无法映射为 PPTX 形状 |
| CSS 变量 | **间接支持** | getComputedStyle 返回的是解析后的最终值，CSS 变量已被解析 |
| CSS Grid 布局 | **完美支持** | 读取计算后的绝对坐标，Grid 布局精确映射 |
| Flexbox 布局 | **完美支持** | 同上 |
| 渐变色 | **支持** | linear-gradient 解析为 SVG |
| box-shadow | **支持** | 数学转换 |
| Google Fonts | **支持** | 自动嵌入（需 crossorigin） |
| Lucide SVG 图标 | **支持** | 可保持为矢量 |
| Motion 动画 | **不支持** | 只捕获当前状态 |
| `::before`/`::after` | **有限支持** | 伪元素不在 DOM 中，无法直接读取 |
| `backdrop-filter` | **部分支持** | html2canvas 快照 |
| 100vw × 100vh 页面 | **支持** | 推荐在 1920×1080 容器中构建 |

#### WebGL 背景的处理策略

WebGL 背景是最大的挑战。可能的解决方案：

1. **截图 + dom-to-pptx 混合**：先截取 WebGL canvas 为图片，替换 canvas 为 `<img>`，然后用 dom-to-pptx 处理其余元素
2. **两层导出**：WebGL 背景作为幻灯片背景图片插入，前景元素用 dom-to-pptx 映射

#### 代码示例（多 slide 导出）

```javascript
import { exportToPptx } from 'dom-to-pptx';

// 导出所有 slide section
const slides = document.querySelectorAll('section.slide');
await exportToPptx(Array.from(slides), {
  fileName: 'presentation.pptx',
  autoEmbedFonts: true,
  fonts: [
    { name: 'Noto Serif SC', url: 'https://fonts.gstatic.com/s/notoserifsc/...' },
    { name: 'Playfair Display', url: 'https://fonts.gstatic.com/s/playfairdisplay/...' },
    { name: 'IBM Plex Mono', url: 'https://fonts.gstatic.com/s/ibmplexmono/...' },
  ],
  svgAsVector: true,
  layout: 'LAYOUT_16x9',
});
```

#### 限制

- **纯客户端库**：必须在浏览器中运行，不能在 Node.js 中直接使用（需要 JSDOM + 布局引擎）
- **canvas 元素无法映射**：WebGL 内容需要截图补充
- **伪元素有限支持**：`::before`/`::after` 的内容需要转为真实 DOM 元素
- **CSS 动画不导出**：只捕获当前计算的视觉状态
- **较新库**：186 stars，社区相对小，可能有未发现的 edge case
- **CORS 限制**：跨域图片和字体需要正确的 CORS 头

### 5.2 html-to-pptx

[jsDelivr 上的 npm 包](https://www.jsdelivr.com/package/npm/html-to-pptx)。描述为"将页面元素转换为 pptx"，功能与 dom-to-pptx 类似但更基础，社区活跃度较低。不推荐优先使用。

### 5.3 llm-dom-to-pptx

专为 AI 生成的 HTML/CSS 设计的变体。[jsDelivr CDN 可用](https://www.jsdelivr.com/package/npm/llm-dom-to-pptx)。针对 LLM 生成的简化 HTML 优化，不适合包含 WebGL 的复杂模板。

### 5.4 PptxGenJS

[PptxGenJS](https://gitbrent.github.io/PptxGenJS/) 是底层 PPTX 生成库（3500+ stars），dom-to-pptx 就建立在它之上。它本身不是 HTML→PPTX 转换器，但支持 HTML `<table>` 直接导入。可作为底层 API 手动构建 PPTX。

### 还原度评分（dom-to-pptx）

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **75/100** | 文字/布局精确，但 WebGL 背景和 backdrop-filter 受限 |
| 可编辑性 | **90/100** | 文字、形状完全可编辑 |
| 文件大小 | **80/100** | 原生形状 + 嵌入字体 |
| 实现难度 | **70/100** | 需要浏览器环境，WebGL 部分需要混合方案 |

---

## 6. 方案五：Codex / artifact-tool 原生可编辑生成

> 补充调研日期：2026-06-22
> 目标：理解 Codex 自身生成可编辑 PPTX 的方式，并提炼到本项目的 HTML PPTX 导出设计中。

### 6.1 关键结论

Codex 的演示文稿能力并不是把任意 HTML 页面直接转换成 PPTX。它采用的是 **原生 PowerPoint 对象生成**：

```
结构化幻灯片描述 / JSX 布局
        ↓
@oai/artifact-tool 生成 Presentation 对象
        ↓
原生 PPTX 对象：文本框、形状、图片、表格、图表、连接线
        ↓
导出 PPTX + 渲染 PNG / layout JSON 做质量验证
```

这意味着 Codex 的“可编辑效果”来自两个设计选择：

1. **从一开始就生成 PPTX 原生对象**，而不是先做 HTML 截图。
2. **用渲染预览反向校验最终 PPTX**，保证布局、重叠、裁剪、换行等问题能被发现。

因此，若本项目希望达到类似 Codex 的效果，不应把“HTML 文件”作为唯一源格式；更稳妥的设计是建立一个中间的**语义幻灯片 AST / JSON**，由它同时驱动 HTML 预览和 PPTX 原生导出。

### 6.2 Codex 可编辑 PPTX 的核心机制

基于本地 Codex Presentations skill 调研，Codex 当前推荐使用 `@oai/artifact-tool`：

| 能力 | Codex 实现方式 | 对本项目的启发 |
|------|---------------|---------------|
| 页面尺寸 | `Presentation.create({ slideSize: { width: 1280, height: 720 } })`，单位为 CSS px（96 DPI） | HTML 预览与 PPTX 导出统一使用 1280×720 或 1920×1080 逻辑坐标 |
| 布局 | JSX/compose 支持 `row`、`column`、`grid`、`layers`、`box` | 不必把 CSS Grid/Flex 反解析为 PPTX；应在中间层保留布局语义 |
| 文本 | `paragraph` / textbox 生成 PPT 原生文本框 | 文字保持可编辑、可搜索、可复制 |
| 图形 | `shape` / `slide.shapes.add()` 生成 PPT 原生形状 | 卡片、圆角矩形、分割线、标签等可编辑 |
| 图片 | `slide.images.add()` 嵌入图片 bytes，支持裁剪、遮罩、圆角 | WebGL、复杂滤镜、不可映射区域作为图片层处理 |
| 表格/图表 | `table` / `chart` 生成 PowerPoint 原生表格和图表 | 数据型幻灯片不要截图，应走原生 chart/table |
| QA | 导出 slide PNG、layout JSON、montage，并 inspect 对象树 | 导出后必须渲染检查，不能只看 PPTX 文件生成是否成功 |
| 模板复用 | 可导入现有 PPTX，inspect 后定位对象并编辑 | 企业模板可作为母版/布局来源，而不是完全从 HTML 复刻 |

### 6.3 与 dom-to-pptx 的本质差异

| 维度 | dom-to-pptx | Codex / artifact-tool |
|------|-------------|-----------------------|
| 输入 | 已渲染 DOM 元素 | 结构化幻灯片代码 / JSX / Presentation API |
| 布局来源 | 浏览器 `getBoundingClientRect()` 和 `getComputedStyle()` | 自己的布局 DSL：row / column / grid / layers |
| 可编辑性 | 取决于 DOM→PPTX 映射是否成功 | 默认就是 PPTX 原生对象 |
| 视觉还原 | 对已有 HTML 友好，复杂 CSS 有边界 | 对新生成内容稳定，任意 HTML 不是目标 |
| 工程可控性 | 依赖第三方库覆盖 CSS 特性 | 可控，但需要建立自己的幻灯片语义层 |
| 适用场景 | 已有 HTML 页面需要快速导出 | 从 AI 生成 PPT 开始就追求可编辑和稳定交付 |

结论：**dom-to-pptx 更像“已有 HTML 的转换器”；Codex 方案更像“可编辑 PPTX 的原生生成器”。** 对本项目这种 AI PPT 技能，长期应优先学习 Codex 的架构，而不是把 HTML 当成不可改变的源格式。

### 6.4 推荐落地架构：语义 AST 双渲染

建议新增一层 `SlideDeckSpec` 作为唯一事实来源：

```ts
type SlideDeckSpec = {
  size: { width: 1280; height: 720 };
  theme: ThemeTokens;
  slides: SlideSpec[];
};

type SlideSpec = {
  id: string;
  background?: BackgroundSpec;
  nodes: SlideNode[];
};

type SlideNode =
  | TextNode
  | ShapeNode
  | ImageNode
  | GridNode
  | RowNode
  | ColumnNode
  | ChartNode
  | TableNode
  | RasterLayerNode;
```

然后提供两个渲染器：

```
SlideDeckSpec
    ├── HTML Renderer：用于浏览器预览、交互、主题切换
    └── PPTX Renderer：生成原生文本框、形状、图表、表格、图片
```

这样可以保留 HTML 预览体验，同时让 PPTX 导出尽量可编辑。WebGL 背景、复杂滤镜、特殊 shader 等无法编辑的视觉层，明确落入 `RasterLayerNode`，作为图片背景或图片层插入 PPTX。

### 6.5 已有 HTML 的兼容路线

如果短期必须从现有单文件 HTML 出发，可采用“浏览器抽取 + 原生重建 + 局部截图”：

```
HTML → Playwright 打开页面
    → 注入 DOM extractor
    → 读取元素 rect / computedStyle / text / z-index
    → 转为 SlideDeckSpec
    → PPTX Renderer 生成原生对象
    → 对 canvas、backdrop-filter、复杂 SVG/滤镜节点截图补层
```

抽取器建议只支持受控模板，不追求任意网页：

| HTML 元素/样式 | 转换策略 |
|----------------|----------|
| `section.slide` | 一个 `SlideSpec` |
| 标题、段落、列表 | `TextNode`，转 PPT 原生文本框 |
| 卡片、分割线、标签 | `ShapeNode` |
| `<img>`、可独立 SVG | `ImageNode`，SVG 尽量保留矢量 |
| CSS Grid/Flex 结果 | 用浏览器计算后的绝对坐标填充节点 bounds |
| WebGL canvas | 截图为 `RasterLayerNode` |
| `backdrop-filter` / `mix-blend-mode` | 优先截图为局部图片层 |
| 动画 | 不导出动画，只捕获最终状态 |

该路线比“手写 CSS 解析器”现实，也比纯 dom-to-pptx 更可控；但它仍然是兼容方案，不应作为长期主架构。

### 6.6 对本项目设计的修正建议

1. **不要只建设 HTML→PPTX 转换器**：应建设“AI PPT 语义模型 + HTML/PPTX 双渲染器”。
2. **HTML 作为预览产物，不作为唯一源文件**：避免后续为了导出 PPTX 被迫解析任意 CSS。
3. **PPTX 导出优先使用原生对象**：文本、形状、表格、图表必须尽量保持可编辑。
4. **不可编辑区域显式标记**：WebGL、复杂 shader、复杂滤镜、毛玻璃等作为图片层，并在导出报告中说明。
5. **加入导出 QA 流程**：导出后渲染每页 PNG，检查重叠、裁剪、空白页、字体回退和对象数量。
6. **保留降级策略**：可编辑导出失败时，自动降级为高保真整页截图版 PPTX。

### 6.7 还原度评分（Codex-like 原生生成）

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **80-90/100** | 对受控模板和语义节点很高；任意 HTML 不保证 |
| 可编辑性 | **90-95/100** | 文本、形状、表格、图表为原生 PPT 对象 |
| 文件大小 | **80/100** | 原生对象为主，只有复杂背景/滤镜为图片 |
| 实现难度 | **45-60/100** | 需要重构源格式和双渲染器，短期工作量高 |
| 稳定性 | **90/100** | 比 CSS 反解析稳定，符合企业场景可预测性要求 |

### 6.8 结论

**这是长期最推荐的方向。** 如果项目仍处于 PPT 技能设计阶段，应优先采用 Codex-like 的原生生成架构；如果已经有大量 HTML 模板，则先做 DOM 抽取兼容层，再逐步把模板迁移为 `SlideDeckSpec`。

---

## 7. 方案六：Aspose.Slides

### 方案描述

使用 Aspose.Slides（Python/.NET）的 `add_from_html()` 方法导入 HTML。

### 实际效果评估

根据 [Aspose 论坛的反馈](https://forum.aspose.com/tag/slides-htmlimport) 和 [Stack Overflow 的讨论](https://stackoverflow.com/questions/46358541/unable-to-embed-styling-in-aspose-ppt-which-is-rendered-from-html-file)：

| 问题 | 严重程度 |
|------|---------|
| `add_from_html()` 只支持**基本文本导入 + 有限的 HTML 标签** | 致命 |
| CSS 样式**不转换**为 PowerPoint 格式 | 致命 |
| 背景颜色属性**不处理** | 严重 |
| 嵌入的 `<style>` 标签**不完全支持** | 严重 |
| 列表样式可能**无法保留** | 中等 |
| WebGL canvas **完全忽略** | 致命 |
| CSS Grid/Flexbox **不处理** | 致命 |

Aspose 支持团队自己也承认："Slides 支持基本文本导入以及有限的标签支持"。这意味着 `add_from_html()` 设计用于导入简单的 HTML 文本片段（如富文本编辑器内容），而不是完整的 HTML 页面。

### 商业考量

- **价格**：约 $999/年起（Developer License）
- **功能不匹配**：付费但 HTML 导入能力极其有限
- **平台依赖**：Python 版本通过 .NET 运行，依赖较重

### 还原度评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **5/100** | 仅导入基本文本 |
| 可编辑性 | **50/100** | 文本可编辑但格式全丢 |
| 性价比 | **10/100** | 昂贵但功能不匹配 |

### 结论

**强烈不推荐**。Aspose.Slides 是优秀的 PPTX 操作库，但它的 HTML 导入功能设计目标是简单的富文本片段，完全不适合完整的 HTML 页面转换。

---

## 8. 方案七：中间格式方案（HTML → PDF → PPTX）

### 方案描述

```
HTML → Playwright 打印为 PDF → Adobe Acrobat / pdf2pptx 工具 → PPTX
```

### 第一步：HTML → PDF

Playwright 可以直接将 HTML 打印为 PDF：

```python
page.pdf(path='output.pdf', print_background=True, 
         width='1920px', height='1080px')
```

PDF 质量通常很高，WebGL 内容会被包含（如果渲染完成）。

### 第二步：PDF → PPTX

这是关键瓶颈。PDF 是固定布局格式，PPTX 是流式布局。转换时：

| PDF 特性 | PPTX 转换效果 |
|---------|-------------|
| 矢量文字 | 转为文本框（可编辑），但段落/换行可能错位 |
| 位图图片 | 保持为图片 |
| 矢量图形 | 可转为形状或图片 |
| 字体嵌入 | 部分保留 |
| 透明度/混合模式 | **经常丢失** |
| 渐变色 | **经常简化或丢失** |
| 阴影 | **经常丢失** |

#### 转换工具对比

| 工具 | 质量 | 价格 | 适用性 |
|------|------|------|--------|
| Adobe Acrobat Pro | **最好**（[业界标准](https://www.kuse.ai/blog/workflows-productivity/best-tools-convert-pdf-and-documents-to-powerpoint)） | ~$20/月 | 文字可编辑，布局保留较好 |
| [ConvertAPI](https://www.convertapi.com/pdf-to-pptx) | 中等 | API 按量付费 | 程序化调用 |
| [PDFgear](https://www.pdfgear.com/pdf-to-pptx/) | 中等 | 免费 | OCR 支持 |
| Aspose.Slides PDF 导入 | 中等 | $999+/年 | 编程接口 |
| LibreOffice PDF→PPTX | 差 | 免费 | 简单 PDF |

### 还原度评估

PDF → PPTX 的核心问题是**文本重排**：PDF 中的文本是固定位置的字符，转换器需要将这些字符重新组合为段落。对于多栏、网格布局，这个过程很容易出错。

| 维度 | 评分 | 说明 |
|------|------|------|
| 视觉还原 | **60/100** | 渐变/阴影/透明度经常丢失 |
| 可编辑性 | **50/100** | 文字可编辑但布局经常错位 |
| 实现难度 | **75/100** | 需要两步转换 + Acrobat 许可 |
| 适用性 | **中等** | 适合简单布局，复杂布局容易崩 |

### 结论

**不推荐作为首选**。两步转换引入双重信息损失，且 PDF→PPTX 的文本重排对 Grid 布局不友好。如果只有 PDF 源文件，Adobe Acrobat 是最佳选择，但从 HTML 出发有更好的方案。

---

## 9. 综合对比与推荐

### 9.1 七方案对比总表

| 方案 | 视觉还原 | 可编辑性 | 文件大小 | 实现难度 | WebGL 支持 | 总评 |
|------|---------|---------|---------|---------|-----------|------|
| ① 截图 + python-pptx | **95** | 0 | 30 | 90 | **完美** | 适合"预览/分享"场景 |
| ② HTML 解析映射 | 40-60 | 80 | 85 | 10 | 不支持 | 工作量巨大，不推荐 |
| ③ LibreOffice | 10 | 70 | 80 | 95 | 不支持 | 完全不适用 |
| ④ **dom-to-pptx** | **75** | **90** | **80** | 70 | 需混合 | 已有 HTML 的最佳平衡 |
| ⑤ **Codex-like 原生生成** | **80-90** | **90-95** | **80** | 45-60 | 作为图片层 | **长期最推荐** |
| ⑥ Aspose.Slides | 5 | 50 | 80 | 80 | 不支持 | 功能不匹配 |
| ⑦ PDF 中间格式 | 60 | 50 | 70 | 75 | 完美 | 双重损失 |

### 9.2 推荐方案：Codex-like 语义 AST 双渲染 + 混合导出

**长期最终推荐：方案五为主，方案一 + 方案四作为兼容和降级。**

```
SlideDeckSpec / 语义幻灯片 AST
        ├── HTML Renderer → 浏览器预览
        └── PPTX Renderer → 原生可编辑 PPTX
                ├── 文本/形状/图表/表格 → PPT 原生对象
                └── WebGL/复杂滤镜/特殊背景 → 图片层
```

#### 推荐实施路径

**Phase 1：短期 PoC（兼容现有 HTML）**
- 用 Playwright 渲染现有 HTML。
- 抽取 `section.slide` 内的文本、图片、基础形状、位置和计算后样式。
- 生成 `SlideDeckSpec`，再导出原生 PPTX。
- 对 WebGL canvas、复杂滤镜区域截图补层。
- 同时生成纯截图版 PPTX 作为降级结果。

**Phase 2：中期重构（语义模型优先）**
- AI 生成 PPT 时直接输出 `SlideDeckSpec`，HTML 只是预览渲染结果。
- 将常用版式沉淀为 Row/Column/Grid/Layer 组件。
- 为文字、卡片、指标、时间线、图表、表格建立稳定节点类型。
- PPTX 导出器不再依赖解析 HTML，而是直接生成原生对象。

**Phase 3：质量保障**
- 导出后渲染每页 PNG 和对象 layout JSON。
- 检测空白页、越界、重叠、文本截断、字体回退、图片缺失。
- 可编辑率统计：文本框数量、图片层数量、原生图表/表格数量。

#### 兼容方案：截图 + dom-to-pptx 混合

对于已经存在且短期不重构的 HTML，可以继续保留原方案：

```
                  ┌── WebGL Canvas 背景 → Playwright 截图 → 幻灯片背景图片
                  │
HTML 网页 PPT ────┤
                  │
                  └── 前景内容（文字/布局/图标）→ DOM 抽取 / dom-to-pptx → 可编辑形状/文本
```

**Step 1：预处理 HTML**
- 将 WebGL canvas 截图为高清 PNG
- 用截图替换 canvas 元素为 `<img>` 背景
- 将 `::before`/`::after` 伪元素转为真实 DOM 元素
- 确保所有 Google Fonts `<link>` 加上 `crossorigin="anonymous"`

**Step 2：dom-to-pptx 导出前景**
- 在浏览器中打开预处理后的 HTML
- 逐页调用 `exportToPptx()` 导出每个 `section.slide`
- 启用字体自动嵌入

**Step 3：后处理（可选）**
- 用 python-pptx 打开生成的 PPTX
- 将 WebGL 背景截图插入为幻灯片母版背景
- 微调位置/大小

#### 预期效果

| 维度 | 预期评分 | 说明 |
|------|---------|------|
| 视觉还原 | **85/100** | WebGL 背景精确（截图），前景文字/布局精确（dom-to-pptx），backdrop-filter 可能略有差异 |
| 可编辑性 | **85/100** | 文字、形状完全可编辑；WebGL 背景为图片（不可编辑，但可替换） |
| 文件大小 | **75/100** | 原生形状 + 嵌入字体 + 背景图片，预计 10-30MB |

#### 降级方案

如果 dom-to-pptx 在实际测试中效果不理想（如 backdrop-filter、伪元素处理不满足需求），**降级为纯截图方案**（方案一）。截图方案虽然不可编辑，但视觉还原度最高，实现最简单。

### 9.3 实施建议

1. **先做 PoC**：用 2-3 页 HTML 同时测试 DOM 抽取 / dom-to-pptx / 原生 PPTX Renderer 的还原效果
2. **重点测试**：
   - CSS Grid 布局（`.grid-6`, `.grid-3`, `.split`）
   - 渐变色背景（light/dark 主题切换）
   - Google Fonts 嵌入效果
   - `::before`/`::after` 伪元素（遮罩层、荧光标记）
3. **WebGL 截图策略**：两种 shader（light/dark）各截一张，根据 `data-theme` 属性分配
4. **可编辑率指标**：统计每页原生文本框/形状/图表/表格数量，以及截图层数量
5. **降级策略**：如果可编辑导出效果不够好，立即切换到纯截图方案

---

## 附录：关键链接

| 资源 | 链接 |
|------|------|
| dom-to-pptx GitHub | https://github.com/atharva9167j/dom-to-pptx |
| dom-to-pptx 支持的 CSS 特性 | https://github.com/atharva9167j/dom-to-pptx/blob/master/SUPPORTED.md |
| PptxGenJS 文档 | https://gitbrent.github.io/PptxGenJS/ |
| PptxGenJS HTML-to-PPTX（表格） | https://gitbrent.github.io/PptxGenJS/html2pptx/ |
| Playwright 截图文档 | https://playwright.dev/docs/screenshots |
| python-pptx 文档 | https://python-pptx.readthedocs.io/ |
| Aspose.Slides HTML 导入论坛 | https://forum.aspose.com/tag/slides-htmlimport |
| LibreOffice 过滤器名称 | https://help.libreoffice.org/latest/en-US/text/shared/guide/convertfilters.html |
| ConvertAPI PDF→PPTX | https://www.convertapi.com/pdf-to-pptx |
| Codex Presentations skill 本地说明 | `C:\Users\ethyn\.codex\plugins\cache\openai-primary-runtime\presentations\26.619.11828\skills\presentations\SKILL.md` |
| Codex artifact-tool API quick start | `C:\Users\ethyn\.codex\plugins\cache\openai-primary-runtime\presentations\26.619.11828\skills\presentations\artifact_tool\API_QUICK_START.md` |
| Codex artifact-tool API docs | `C:\Users\ethyn\.codex\plugins\cache\openai-primary-runtime\presentations\26.619.11828\skills\presentations\artifact_tool\api\API_DOCS.md` |
