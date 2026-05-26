# Browser Harness 可行性研究报告

> 版本: v1.0 | 日期: 2026-05-19 | 作者: Claude

## 1. 项目概述

### 1.1 Browser Harness 是什么

[Browser Harness](https://github.com/browser-use/browser-harness) 由 Browser Use 团队开发（同名 Python 库 browser-use 有 93k+ GitHub stars），是一个约 1,000 行代码的**极简 CDP（Chrome DevTools Protocol）浏览器控制层**。

核心理念：**一个 WebSocket 连接到 Chrome，中间没有抽象层。**

```
Chrome / Browser Use Cloud  →  CDP WebSocket  →  daemon 进程  →  IPC  →  agent 执行代码
```

### 1.2 核心数据

| 指标 | 值 |
|------|-----|
| 代码量 | ~1,000 行（4 个核心文件） |
| 运行时依赖 | 4 个（cdp-use, fetch-use, pillow, websockets） |
| Python 版本 | >=3.11 |
| 许可证 | MIT |
| Stars | ~11.9k |
| 浏览器引擎 | 任何支持 CDP 的浏览器（Chrome/Edge/Brave/Arc 等） |

---

## 2. 两个系统架构对比

### 2.1 架构差异

| 维度 | 当前实现（BrowserAutomationTool） | Browser Harness |
|------|--------------------------------|-----------------|
| **底层库** | Playwright（async API） | 原生 CDP（通过 cdp-use） |
| **浏览器引擎** | 仅 Chromium | 任何 CDP 兼容浏览器 |
| **元素定位** | 语义快照 + ref 映射 + 自然语言匹配 | 坐标点击（screenshot → 识别 → click_at_xy） |
| **核心循环** | LLM 驱动的决策循环（snapshot → decide → act） | Agent 直接写 Python 代码控制 |
| **代码量** | ~3,000+ 行（含语义子系统） | ~1,000 行 |
| **依赖数** | Playwright 全家桶（~100MB+） | 4 个轻量依赖 |
| **交互模式** | 声明式（自然语言任务描述） | 命令式（直接编写 Python 代码） |

### 2.2 功能对比

| 功能 | 当前实现 | Browser Harness | 优劣 |
|------|---------|-----------------|------|
| **导航** | `navigate(url)` | `goto_url(url)` | 相当 |
| **点击** | 语义匹配 → `ref` → Playwright click | 坐标点击 `click_at_xy(x,y)` | 各有优势 |
| **输入** | 语义匹配 → `fill(ref, text)` | CSS 选择器 → `fill_input(selector, text)` | 各有优势 |
| **截图** | `screenshot()` → 二进制 | `capture_screenshot()` → PIL Image | Harness 更灵活 |
| **JS 执行** | `page.evaluate()` | `js(code)` | 相当 |
| **Tab 管理** | 单页/单 tab | 完整多 tab 支持 | Harness 更好 |
| **iframe** | 同源 iframe 遍历 | 坐标点击天然穿透 iframe | Harness 更好 |
| **等待策略** | DOM loaded → network idle → 300ms 延迟 | `wait_for_load()`, `wait_for_element()`, `wait_for_network_idle()` | 相当 |
| **内容提取** | markdownify 转换 | `http_get()` + 可选 fetch-use 代理 | Harness 有代理能力 |
| **文件上传** | 不支持 | `upload_file()` | Harness 有 |
| **Cookie 持久化** | 不支持 | 本地 Chrome 同步到云 profile | Harness 有 |
| **反检测** | 无 | 本地无，云浏览器有（stealth + 住宅代理 + 验证码） | 云模式 Harness 更好 |
| **并发会话** | 内存 session 字典 | `BU_NAME` 命名空间隔离 | Harness 更好 |
| **代理 IP** | 不支持 | 云浏览器按国家选择住宅代理 | Harness 有 |
| **域名技能** | 无 | 100+ 社区贡献的站点操作手册 | Harness 独有 |
| **自愈能力** | 重试 3 次 | daemon 自动重连 stale session + agent 自己写 helper | Harness 更好 |

### 2.3 设计哲学差异

**当前实现**：构建了一个完整的「语义浏览器自动化框架」—— 通过 DOM 遍历、元素分类、语义标签、自然语言匹配等能力，让 Agent 通过自然语言描述任务，由内置的 LLM 循环自主决策每一步操作。

**Browser Harness**：构建了一个「透明的 CDP 管道」—— 不做任何决策，让 Agent 直接写代码控制浏览器。Agent 通过截图 + 坐标点击的方式与页面交互，缺失的 helper 代码由 Agent 在执行过程中自己写。

---

## 3. 能否替代？— 结论：部分替代 + 互补

### 3.1 不适合替代的场景

| 场景 | 原因 |
|------|------|
| **需要精确的 DOM 元素操作** | Harness 的坐标点击依赖 LLM 视觉识别坐标，不如语义匹配精确 |
| **表单批量填写** | 坐标点击逐字段操作效率低，不如语义匹配 + ref 批量操作 |
| **结构化数据提取** | Harness 没有语义快照，需要通过 JS 提取或截图 OCR |
| **当前 Agent 系统集成** | 当前 Agent 调用 `browser_automation` 工具时传的是自然语言任务描述，与 Harness 的代码式 API 不匹配 |
| **不依赖外部云服务** | Harness 的反检测能力全靠 Browser Use Cloud，本地无反检测 |

### 3.2 适合引入的场景

| 场景 | 原因 |
|------|------|
| **绕过反爬/反机器人** | 云浏览器的 stealth 模式 + 住宅代理 + 验证码解决 |
| **无头服务器部署** | `BU_AUTOSPAWN` 自动在无 GUI 服务器上启云浏览器 |
| **需要登录态的操作** | profile 同步机制可复用已有的登录状态 |
| **跨域 iframe 操作** | 坐标点击天然穿透 iframe 边界 |
| **多 tab 并行** | `BU_NAME` 隔离 + 完整的 tab 管理 |
| **轻量化需求** | 4 个依赖 vs Playwright 全家桶 |

### 3.3 推荐策略：互补共存

**不建议完全替代，而是引入为补充工具**：

```
当前 Agent 的浏览器工具体系:
├── BrowserAutomationTool（现有）  ← 处理常规网页操作
│   └── 语义快照 + LLM 决策循环
│
└── BrowserHarnessTool（新增）     ← 处理反爬场景
    └── CDP 直连 + 云浏览器 + 坐标交互
```

Agent 根据任务场景自动选择：
- 常规网页浏览、表单填写 → 使用现有的 BrowserAutomationTool
- 遇到反爬/验证码/需要登录态 → 使用 BrowserHarnessTool

---

## 4. 用户的核心问题分析

### 4.1 "浏览器没法在用户端执行"

**问题本质**：Agent 是 B/S 架构，用户通过企微/钉钉/飞书发消息，浏览器操作在服务端执行，结果返回给用户。用户无法看到实时的浏览器界面。

**两个系统的解决方式**：
- **当前实现**：截图 → base64 → 发给 LLM 分析 → 文字结果返回用户。用户看不到浏览器画面。
- **Browser Harness**：
  - 云浏览器提供 `liveUrl`，可在浏览器中实时观看
  - 但这个 liveUrl 是给开发者的，不适合直接暴露给终端用户
  - 截图同样是主要的内容获取方式

**结论**：两个系统在「用户端无浏览器界面」这个问题上没有本质区别。最终都是截图/文字返回给用户。Browser Harness 的 liveUrl 可以作为调试手段，但不改变面向用户的体验。

### 4.2 "服务器上是容器，没有界面，只能用 headless 模式"

**当前实现的局限**：
- Playwright headless 模式，基础 Chromium，无反检测
- 容器中需要安装 Chromium（~400MB+）
- 遇到 Cloudflare/reCAPTCHA 等反爬基本无解

**Browser Harness 的方案**：
- **本地模式**：容器中安装 Chrome，`--remote-debugging-port=9222`，设置 `BU_CDP_URL`。与 Playwright headless 类似，同样会被反爬检测。
- **云模式（推荐）**：设置 `BROWSER_USE_API_KEY` + `BU_AUTOSPAWN=1`，自动在 Browser Use Cloud 启动浏览器，具备 stealth + 住宅代理 + 验证码解决能力。

**结论**：Browser Harness 的云模式是解决容器化 + 反爬的最佳方案。但这引入了外部服务依赖和费用。

### 4.3 "有些防爬的就不行了"

**当前实现**：无反检测能力，被 Cloudflare、DataDome 等检测即封。

**Browser Harness**：
- 本地 Chrome：同样无反检测
- 云浏览器：
  - Stealth 模式（隐藏 webdriver 特征）
  - 住宅代理 IP（非数据中心 IP）
  - 验证码自动解决
  - 免费层：3 个并发浏览器，无需信用卡

**结论**：如果需要绕过反爬，Browser Harness + Browser Use Cloud 是目前唯一可行方案。

---

## 5. Browser Use Cloud 成本分析

| 项目 | 说明 |
|------|------|
| 免费层 | 3 个并发浏览器，无需信用卡 |
| 定价模型 | 按使用量计费（具体价格需官网查询） |
| 依赖风险 | Browser Use 是第三方服务，需评估长期可用性 |
| 数据安全 | 浏览器操作经过第三方云，敏感场景需评估 |

---

## 6. 实施方案

### 方案 A：轻量集成（推荐先做）

**目标**：引入 Browser Harness 作为补充工具，处理反爬场景。

**步骤**：

1. **安装 Browser Harness**
   ```bash
   pip install browser-harness
   # 或
   uv tool install browser-harness
   ```

2. **创建 BrowserHarnessTool**
   - 文件：`src/tools/browser/harness_tool.py`
   - 继承 `BaseTool`，暴露 `browser_harness` 工具给 Agent
   - 输入参数：`task`（自然语言任务）、`url`（可选）、`mode`（local/cloud）
   - 内部将 task 转为 browser-harness 的 Python 代码执行

3. **配置 Browser Use Cloud**
   - 环境变量：`BROWSER_USE_API_KEY`、`BU_AUTOSPAWN=1`
   - `configs/config.yaml` 新增 browser harness 配置项

4. **注册工具**
   - `agent.py` 中注册 `BrowserHarnessTool`

**预计工期**：2-3 天

### 方案 B：深度集成

**目标**：将 Browser Harness 的能力融入现有的 BrowserAutomationTool，统一入口。

**步骤**：

1. 完成方案 A
2. 修改 BrowserOrchestrator，增加"反爬检测"逻辑
3. 检测到反爬页面时，自动切换到 Browser Harness 云模式
4. 统一结果格式和进度回调

**预计工期**：5-7 天

### 方案 C：完全替代（不推荐）

**风险太高**：
- 丢失语义快照系统（团队花大量时间开发的核心能力）
- 坐标点击在表单填写等场景不如语义匹配精确
- Browser Use Cloud 的外部依赖不可控
- 对现有 Agent 系统的集成改动太大

---

## 7. 实施开发步骤（方案 A）

### Step 1: 安装依赖

```bash
pip install browser-harness
```

在 `requirements.txt` 中添加：
```
browser-harness>=0.1.0
```

### Step 2: 创建工具类

新建 `src/tools/browser/harness_tool.py`：

```python
"""Browser Harness 工具 - 通过 CDP + 云浏览器处理反爬场景"""
from typing import Dict, Any, Optional
from pydantic import Field
from src.tools.base import BaseTool


class BrowserHarnessInput(BaseTool.InputModel):
    task: str = Field(description="浏览器任务的自然语言描述")
    url: Optional[str] = Field(default=None, description="起始 URL")
    mode: str = Field(default="cloud", description="执行模式: local 或 cloud")


class BrowserHarnessTool(BaseTool):
    name = "browser_harness"
    description = "使用 Browser Harness 执行需要绕过反爬保护的浏览器任务"
    display_name = "反爬浏览器"
    category = "browser"

    async def execute(self, **kwargs) -> Dict[str, Any]:
        # 1. 解析参数
        # 2. 根据 mode 选择本地/云浏览器
        # 3. 通过 subprocess 或 Python API 调用 browser-harness
        # 4. 收集结果（截图、提取的文本等）
        # 5. 返回统一格式结果
        ...
```

### Step 3: 配置管理

在 `src/config/settings.py` 的 `BrowserToolConfig` 中新增：

```python
class BrowserToolConfig(BaseModel):
    # ... 现有配置 ...
    harness_enabled: bool = False
    harness_cloud_api_key: str = ""  # 从环境变量读取
    harness_mode: str = "cloud"  # local / cloud
```

在 `configs/config.yaml` 中新增：

```yaml
tools:
  browser:
    # ... 现有配置 ...
    harness_enabled: true
    harness_mode: cloud
```

### Step 4: 注册工具

在 `src/core/agent.py` 的 `_register_builtin_tools()` 中：

```python
if settings.tools.browser.harness_enabled:
    from src.tools.browser.harness_tool import BrowserHarnessTool
    self.tool_registry.register(BrowserHarnessTool())
```

### Step 5: Docker 适配

Dockerfile 中无需安装 Chromium（云模式），只需：

```dockerfile
ENV BU_AUTOSPAWN=1
ENV BROWSER_USE_API_KEY=${BROWSER_USE_API_KEY}
```

如果需要本地模式，Dockerfile 中需安装 Chrome。

### Step 6: Agent 工具选择引导

在 Agent 的 system prompt 中添加引导：

```
当用户的浏览器任务涉及以下场景时，使用 browser_harness 工具：
- 目标网站有反爬/反机器人保护（如 Cloudflare、reCAPTCHA）
- 需要登录后才能访问的内容
- 需要绕过 IP 限制

其他常规网页浏览和表单操作使用 browser_automation 工具。
```

---

## 8. 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| Browser Use Cloud 服务不可用 | 中 | 保留现有 BrowserAutomationTool 作为降级方案 |
| 云浏览器延迟高 | 中 | 设置超时，重试逻辑 |
| API 费用超预期 | 低 | 免费层 3 并发，监控用量 |
| Browser Harness Python >=3.11 要求 | 低 | 项目已满足 |
| 坐标点击精度问题 | 中 | 关键操作用 CSS 选择器 + `fill_input` 而非坐标点击 |
| 数据经过第三方云 | 高 | 敏感操作（如内网页面）使用本地模式 |

---

## 9. 最终建议

1. **采用方案 A（轻量集成）**，将 Browser Harness 作为补充工具引入
2. **主要使用云浏览器模式**解决反爬问题，本地模式作为备选
3. **保留现有 BrowserAutomationTool**，不替代，两者共存
4. **在 Agent prompt 中引导工具选择**，根据任务特征自动切换
5. **一期只做最小可用**：安装 → 工具类 → 云模式配置 → 注册，验证可行性后再深度集成

---

## 附录

### A. Browser Harness 核心依赖

```
cdp-use==1.4.5       # CDP WebSocket 客户端
fetch-use==0.4.0     # HTTP fetch 代理（反爬、住宅代理、重试）
pillow==12.2.0       # 图像处理（截图缩放）
websockets==15.0.1   # WebSocket 协议
```

### B. 关键环境变量

| 变量 | 用途 |
|------|------|
| `BROWSER_USE_API_KEY` | Browser Use Cloud API 密钥 |
| `BU_AUTOSPAWN` | 设为 `1` 在无头服务器自动启云浏览器 |
| `BU_CDP_URL` | 本地 Chrome 的 HTTP DevTools 端点 |
| `BU_CDP_WS` | 直接 CDP WebSocket URL |
| `BU_NAME` | Daemon 命名空间（支持并发隔离） |

### C. Browser Harness CLI 用法

```bash
# 基本执行
browser-harness -c 'goto_url("https://example.com"); capture_screenshot()'

# 诊断
browser-harness --doctor

# 更新
browser-harness --update -y

# 调试点击位置
browser-harness --debug-clicks -c 'click_at_xy(100, 200)'
```
