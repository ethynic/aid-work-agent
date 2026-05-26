# 浏览器自动化 Agent 工具全景调研报告

> 版本: v1.0 | 日期: 2026-05-21 | 状态: 调研完成

---

## 1. 调研概览

本报告对当前（2025-2026）主流的开源浏览器自动化 Agent 工具进行全面调研，涵盖 5 个核心 Agent 框架、2 个浏览器基础设施平台、以及多种可视化推流方案。调研目标是评估各方案与本项目的集成可行性，特别是：

- 是否支持自定义 LLM 提供商（国内模型 Qwen/ZhipuAI）
- 是否有前端可视化能力（让用户实时观看 Agent 操作过程）
- 与现有 Python/Playwright 架构的集成难度
- 生产就绪程度

---

## 2. Agent 框架详细调研

### 2.1 Browser Use

| 维度 | 详情 |
|------|------|
| **GitHub** | [browser-use/browser-use](https://github.com/browser-use/browser-use) |
| **Stars** | 78,000+ |
| **语言** | Python |
| **许可证** | MIT |
| **浏览器驱动** | 原生 CDP（已从 Playwright 迁移到自研 cdp-use 库） |
| **最新动态** | 2026 年发布 v3，推出了自研 CDP 库 cdp-use，完全脱离 Playwright |

#### 核心架构

Browser Use 是目前最流行的浏览器 Agent 框架。它最近做了一次重大架构升级——从 Playwright 完全迁移到原生 CDP。

```
Agent (Python) → cdp-use (Python CDP 类型绑定) → CDP WebSocket → Chrome
```

关键设计决策：
- **不再使用 Playwright**：因为 Playwright 引入了一层 Node.js relay server，增加了延迟和状态不一致风险
- **cdp-use 库**：类型安全的 Python CDP 客户端，自动从 CDP 协议规范生成 Python 绑定
- **事件驱动架构**：通过 "watchdog" 服务监听 CDP 事件（下载、崩溃、导航等）
- **增强 DOM 节点**：使用 `targetId + frameId + backendNodeId` 的"超级选择器"，支持跨域 iframe
- **坐标点击**：通过截图 → LLM 视觉识别 → 坐标点击的方式交互

#### 自定义 LLM 支持

Browser Use 基于 LangChain 的 Chat Model 接口，支持：
- 内置 `ChatBrowserUse()`（自研模型，针对浏览器任务优化）
- `ChatGoogle`、`ChatAnthropic`
- **任何 OpenAI 兼容模型**：通过 `ChatOpenAI(model="xxx", base_url="...")` 传入自定义 endpoint

**国内模型集成**：可以通过 OpenAI 兼容接口接入 Qwen（DashScope）和 ZhipuAI。但 Browser Use 的 prompt 针对 GPT-4/Claude 优化过，国内模型可能效果下降。

#### 可视化能力

- **web-ui 项目**：[browser-use/web-ui](https://github.com/browser-use/web-ui) 提供了一个 Web 界面，可以运行 Agent 并实时看到操作过程
- **Browser Use Cloud**：提供 `liveUrl`，可以在浏览器中实时观看云浏览器的操作
- **Desktop 应用**：Browser Use Desktop，开源的桌面客户端
- **CLI 模式**：`browser-use` CLI 命令行工具

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成难度** | 中等。纯 Python，但已不依赖 Playwright，用自研 CDP 库。需要安装 cdp-use 等依赖 |
| **LLM 兼容** | 支持 OpenAI 兼容接口，可以接 Qwen/ZhipuAI，但效果未经优化 |
| **生产就绪** | 高。78k+ stars，活跃维护，有云服务支撑，但核心 Agent 循环仍在快速迭代 |
| **优点** | 社区最大，生态最丰富，反爬能力强（云模式），事件驱动架构先进 |
| **缺点** | 已脱离 Playwright，架构独特；自研 CDP 库成熟度待观察；坐标点击精度有限 |

---

### 2.2 Stagehand

| 维度 | 详情 |
|------|------|
| **GitHub** | [browserbase/stagehand](https://github.com/browserbase/stagehand) |
| **Stars** | ~15,000+ |
| **语言** | TypeScript（主）/ Python（[stagehand-python](https://github.com/browserbase/stagehand-python)） |
| **许可证** | MIT |
| **浏览器驱动** | Playwright（通过 CDP engine） |
| **最新动态** | v3 发布，500k+ 周下载量 |

#### 核心架构

Stagehand 的设计理念是**代码 + AI 混合控制**，而非纯 Agent 模式。

```
开发者代码 ←→ Stagehand 四大原语
                ├── act("自然语言动作")       → AI 执行单个动作
                ├── extract("自然语言查询")    → AI 提取结构化数据
                ├── observe("自然语言描述")    → AI 观察页面状态
                └── agent("多步任务")         → AI 执行多步任务
```

核心优势：
- **自愈能力**：自动缓存 AI 操作，网站变化时自动重新用 AI 定位
- **代码与自然语言混合**：确定性的操作用代码写，不确定的用自然语言
- **Playwright 兼容**：在标准 Playwright API 上增加 AI 层

#### 自定义 LLM 支持

Stagehand 通过 `llm` 配置参数支持自定义 LLM provider：
- 内置支持 OpenAI、Anthropic、Google
- **可通过 OpenAI 兼容接口接入国内模型**

#### 可视化能力

- **依赖 BrowserBase**：BrowserBase 云平台提供 Session Live View
- Live View 可嵌入 iframe，支持实时观看、点击、输入
- 支持移动端显示
- 有断连检测机制

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成难度** | 高。主要 TypeScript 生态，Python 版本功能不完整。需要 BrowserBase 云服务 |
| **LLM 兼容** | 支持 OpenAI 兼容接口，可接国内模型 |
| **生产就绪** | 中高。有 BrowserBase 公司支撑，500k 周下载量，但 Python SDK 较新 |
| **优点** | 自愈能力强，代码+AI混合控制理念好，BrowserBase Live View 可视化成熟 |
| **缺点** | TypeScript 优先，Python 支持是二等公民；强绑定 BrowserBase 云服务；项目使用 Qwen/ZhipuAI，Stagehand 的 prompt 未针对这些模型优化 |

---

### 2.3 Skyvern

| 维度 | 详情 |
|------|------|
| **GitHub** | [skyvern-ai/skyvern](https://github.com/skyvern-ai/skyvern) |
| **Stars** | ~30,000+ |
| **语言** | Python |
| **许可证** | AGPL-3.0（核心逻辑），云服务有商业许可 |
| **浏览器驱动** | Playwright |
| **最新动态** | v2.0 发布，SDK 化，支持 Python + TypeScript |

#### 核心架构

Skyvern 是**视觉优先**的浏览器 Agent，结合 LLM 和计算机视觉来理解网页。

```
用户任务 → Skyvern Agent Swarm
           ├── Planner Agent     → 规划步骤
           ├── Navigation Agent  → 导航操作
           ├── Action Agent      → 执行操作（点击/输入/提取）
           └── Validation Agent  → 验证结果
```

特点：
- **Vision + LLM 双通道**：不依赖 XPath，通过视觉理解页面布局
- **Playwright 扩展**：在标准 Playwright page 对象上增加 `page.act()`、`page.extract()` 等方法
- **三种交互模式**：传统 Playwright 选择器 / AI 自然语言 / 混合（选择器优先，失败回退到 AI）
- **Workflow Builder**：可视化工作流编辑器，支持循环、条件、HTTP 请求等

#### 自定义 LLM 支持

Skyvern 通过 **liteLLM** 支持多种 LLM provider：
- OpenAI、Anthropic、Azure OpenAI、AWS Bedrock、Gemini
- Ollama（本地模型）
- OpenRouter
- **OpenAI 兼容端点**：通过 liteLLM 接入任何兼容 API

**国内模型集成**：Qwen 和 ZhipuAI 的 API 都可以通过 OpenAI 兼容接口 + liteLLM 接入。Skyvern 的视觉+LLM 架构对模型能力要求较高。

#### 可视化能力

- **Livestreaming**：Skyvern 内置了 Chrome 视口流式传输功能
- **实时查看**：可以在本地 UI 中实时观看 Agent 操作浏览器的过程
- **调试友好**：完整的请求日志和操作记录

这是所有调研框架中**唯一内置了完整实时可视化**的方案。

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成难度** | 中等。纯 Python，基于 Playwright（与项目现有技术栈一致），提供 pip install |
| **LLM 兼容** | 通过 liteLLM 支持 OpenAI 兼容接口，可接国内模型 |
| **生产就绪** | 中。功能丰富但架构重（需要 PostgreSQL + Redis + 浏览器服务），AGPL 许可证限制商用 |
| **优点** | 基于 Playwright（技术栈一致），内置 Livestreaming 可视化，Vision+LLM 双通道，有 No-Code UI |
| **缺点** | 架构重，依赖多（PostgreSQL/Redis），AGPL 许可证对商业不友好，视觉模型 token 成本高 |

---

### 2.4 LaVague

| 维度 | 详情 |
|------|------|
| **GitHub** | [lavague-ai/LaVague](https://github.com/lavague-ai/LaVague) |
| **Stars** | ~5,000+ |
| **语言** | Python |
| **许可证** | Apache 2.0 |
| **浏览器驱动** | Selenium / Playwright / Chrome Extension |
| **最新动态** | 活跃度下降，更新频率降低 |

#### 核心架构

LaVague 采用 **World Model + Action Engine** 双引擎设计：

```
用户目标 → World Model（LLM）→ 分析当前页面状态 → 生成指令
                                              ↓
         Action Engine ← 编译指令为代码 → Selenium/Playwright 执行
```

特点：
- **World Model**：理解目标和页面状态，输出下一步指令
- **Action Engine**：将自然语言指令编译为可执行代码
- **多 Driver 支持**：Selenium、Playwright、Chrome Extension
- **Gradio Demo**：内置交互式 Web 界面
- **LaVague QA**：专门为 QA 工程师设计的测试自动化工具

#### 自定义 LLM 支持

- 默认使用 OpenAI GPT-4o
- 通过配置可切换为任何 LLM provider
- **支持自定义 context 配置**

#### 可视化能力

- **Gradio Demo**：提供 Gradio 界面，可以看到 Agent 的操作过程
- **Chrome Extension**：可以直接在浏览器中操作和观察
- 无专业的实时流式传输功能

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成难度** | 低。纯 Python，pip install lavague，支持 Playwright |
| **LLM 兼容** | 可配置自定义 LLM，但文档不够详细 |
| **生产就绪** | 低。社区活跃度下降，更新频率降低，不适合生产环境 |
| **优点** | Apache 2.0 许可证友好，架构简洁，支持 Playwright |
| **缺点** | 社区萎缩，维护不稳定，功能相对简单，无专业的可视化能力 |

---

### 2.5 Agent-E

| 维度 | 详情 |
|------|------|
| **GitHub** | [EmergenceAI/Agent-E](https://github.com/EmergenceAI/Agent-E) |
| **Stars** | ~3,000+ |
| **语言** | Python |
| **许可证** | MIT |
| **浏览器驱动** | Playwright / Chrome |
| **最新动态** | 仍在维护，但更新频率中等 |

#### 核心架构

Agent-E 采用**层级式多 Agent 架构**，基于 AG2（原 AutoGen）框架：

```
用户请求 → Planner Agent（规划）
              ↓
         Browser Navigation Agent（执行）
              ├── Sensing Skills（geturl, get_dom, get_user_input）
              └── Action Skills（click, enter_text, openurl, ...）
```

特点：
- **DOM 蒸馏**：将 HTML DOM 精简为 LLM 友好的 JSON 快照
- **Accessibility Tree**：使用 DOM Accessibility Tree 而非纯 HTML DOM
- **mmid 注入**：为每个 DOM 元素注入唯一标识符
- **技能库**：预定义的原子操作，LLM 不直接写代码
- **FastAPI 接口**：提供 HTTP API，可通过 POST 请求执行任务

#### 自定义 LLM 支持

- 通过 `AUTOGEN_MODEL_BASE_URL` 支持 OpenAI 兼容接口
- 支持 Ollama 本地模型（通过 LiteLLM 代理）
- **可以接入国内模型的 OpenAI 兼容 API**

#### 可视化能力

- **非 headless 模式**：可以打开可见的浏览器窗口观看操作
- **截图**：支持操作后截图
- 无专业的实时流式传输功能
- TODO 列表中有"执行过程视频录制"功能，尚未实现

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成难度** | 中等。纯 Python，基于 Playwright，提供 FastAPI 接口 |
| **LLM 兼容** | 通过环境变量配置，支持 OpenAI 兼容接口 |
| **生产就绪** | 低中。架构有学术性质，很多功能在 TODO 中，社区较小 |
| **优点** | MIT 许可证，层级式架构设计好，DOM 蒸馏思路有价值，有 FastAPI 接口 |
| **缺点** | 社区小（3k stars），很多功能未完成，不活跃 |

---

## 3. 浏览器基础设施平台

### 3.1 BrowserBase

| 维度 | 详情 |
|------|------|
| **官网** | [browserbase.com](https://www.browserbase.com/) |
| **性质** | 商业云浏览器平台（闭源 SaaS） |
| **融资** | Y Combinator，多轮融资 |

#### 核心功能

- **Session Live View**：实时观看浏览器操作，可嵌入 iframe
- **Session Replays**：事后回放浏览器操作
- **反检测**：Stealth 模式 + 代理轮换 + 验证码解决
- **多 tab 支持**：每个 tab 有独立的 live view URL
- **移动端**：支持移动视口 + 虚拟键盘
- **断连检测**：JavaScript 事件通知前端

#### Live View 技术实现

BrowserBase 的 Live View 通过 WebRTC/WebSocket 将远程浏览器的画面推流到前端：
- 可嵌入 `<iframe>` 中
- 支持点击、输入、滚动交互
- 支持多 tab 切换
- 有性能优化指南

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成价值** | 高。Live View 是目前最成熟的浏览器可视化方案 |
| **集成难度** | 中。REST API 调用，Playwright/Puppeteer 连接 |
| **成本** | 按使用量计费，有免费层 |
| **风险** | 第三方 SaaS 依赖，数据经过美国服务器 |

---

### 3.2 Steel

| 维度 | 详情 |
|------|------|
| **GitHub** | [steel-dev/steel-browser](https://github.com/steel-dev/steel-browser) |
| **Stars** | ~5,000+ |
| **语言** | Node.js + TypeScript |
| **许可证** | Apache 2.0 |
| **性质** | 开源浏览器 API 平台（可自托管） |

#### 核心功能

Steel 是一个**开源的浏览器 API**，将 Chrome 自动化包装为 REST API：

- **Session 管理**：创建/销毁/重置浏览器会话
- **Puppeteer + CDP**：底层使用 Puppeteer + CDP 控制浏览器
- **反检测**：内置 stealth 插件和指纹管理
- **代理支持**：代理链管理 + IP 轮换
- **Chrome 扩展**：支持加载自定义扩展
- **调试 UI**：内置 Web UI 查看和调试会话
- **快速操作 API**：`/scrape`、`/screenshot`、`/pdf` 等单次操作
- **SDK**：Python SDK + Node SDK

#### 调试 UI 可视化

Steel 提供了一个内置的 Web UI（端口 5173），可以查看和调试浏览器会话。但这个 UI 是面向开发者的，不适合直接暴露给终端用户。

#### 与本项目的集成评估

| 维度 | 评估 |
|------|------|
| **集成价值** | 中。提供了浏览器管理的 REST API，但可视化为开发者级别 |
| **集成难度** | 中。REST API 调用简单，Docker 部署方便，Python SDK 可用 |
| **优点** | 开源自托管，Apache 2.0 许可证，Docker 一键部署，反检测功能 |
| **缺点** | 不是 Agent 框架，只是浏览器管理基础设施；可视化是开发者级别的，无面向用户的实时推流 |

---

## 4. 浏览器可视化方案专项调研

### 4.1 Playwright Screencast API（v1.59+）

Playwright 1.59（2026 年发布）新增了 `page.screencast` API：

```python
# 录制视频
await page.screencast.start({ path: 'video.webm', size: { width: 1280, height: 800 } })
# ... 执行操作 ...
await page.screencast.stop()

# 实时帧流（关键能力）
await page.screencast.start({
    onFrame: ({ data }) => {
        # data 是 JPEG 编码的帧数据
        # 可以推送到 WebSocket → 前端 Canvas 显示
    },
    size: { width: 800, height: 600 },
})
```

**关键特性**：
- **onFrame 回调**：实时获取 JPEG 编码的帧，可用于推流到前端
- **action 装饰**：自动在截图上标注点击、输入等操作
- **chapter 覆盖层**：显示章节标题
- **quality 控制**：可调节帧质量以控制带宽

**可行性评估**：这是**最适合本项目**的可视化方案。本项目已经使用 Playwright，只需升级到 1.59+ 并利用 onFrame 回调将帧推送到前端即可。

### 4.2 CDP Page.screencast（原生 CDP）

在 CDP 层面，Chrome 原生支持 `Page.startScreencast` 命令：

```
CDP 命令: Page.startScreencast({ format: "jpeg", quality: 80, maxWidth: 800, maxHeight: 600 })
CDP 事件: Page.screencastFrame({ data: base64_jpeg, metadata: { ... } })
```

- 每当页面内容变化时，CDP 推送 `screencastFrame` 事件
- 帧数据为 base64 编码的 JPEG
- 需要发送 `Page.screencastFrameAck` 确认收到

**可行性评估**：如果使用 Browser Use 的 cdp-use 库或直接连接 CDP，可以直接使用这个原生能力。性能更好（无需 Playwright relay），但实现复杂度更高。

### 4.3 noVNC + websockify 方案

传统方案：将 Playwright 的浏览器通过 VNC 流到前端。

```
Chrome (headed) → Xvfb (虚拟显示器) → x11vnc (VNC server)
                                              ↓
                                         websockify (WebSocket 代理)
                                              ↓
                                         noVNC (前端 VNC 客户端)
```

**优点**：
- 成熟方案，广泛使用
- 完整的桌面/浏览器画面，可交互（点击、输入）
- 已有 Playwright MCP + noVNC 的 Docker 方案

**缺点**：
- 需要运行 headed 浏览器（headless 不行）
- 需要 Xvfb 虚拟显示（容器中需要额外安装）
- 延迟较高（VNC 协议 + WebSocket 转发）
- 资源消耗大（每会话一个 Chrome + Xvfb + x11vnc）

### 4.4 WebRTC 方案

使用 WebRTC 进行浏览器画面推流：

```
Chrome → CDP screencast frames → WebRTC stream → 前端 <video> 标签
```

**优点**：
- 低延迟（WebRTC 设计用于实时通信）
- 前端原生支持 `<video>` 标签播放
- 自适应码率

**缺点**：
- 实现复杂，需要信令服务器
- CDP screencast 到 WebRTC 的桥接需要自研
- 目前没有成熟的开源方案

### 4.5 方案对比

| 方案 | 延迟 | 实现难度 | 前端集成 | 资源消耗 | 交互能力 | 推荐度 |
|------|------|----------|----------|----------|----------|--------|
| **Playwright Screencast** | 低 | 低 | 中（Canvas/SSE） | 低 | 仅观看 | ★★★★★ |
| **CDP Page.screencast** | 极低 | 中 | 中（WebSocket+Canvas） | 极低 | 仅观看 | ★★★★☆ |
| **noVNC** | 高 | 中 | 低（iframe/noVNC） | 高 | 完整交互 | ★★★☆☆ |
| **WebRTC** | 极低 | 高 | 高（需要信令） | 中 | 可交互 | ★★☆☆☆ |
| **BrowserBase Live View** | 低 | 低（API调用） | 低（iframe嵌入） | 无（SaaS） | 完整交互 | ★★★★☆ |

---

## 5. 综合对比矩阵

### 5.1 Agent 框架核心对比

| 维度 | Browser Use | Stagehand | Skyvern | LaVague | Agent-E |
|------|-------------|-----------|---------|---------|---------|
| **Stars** | 78k+ | 15k+ | 30k+ | 5k+ | 3k+ |
| **语言** | Python | TS/Python | Python | Python | Python |
| **浏览器驱动** | CDP (cdp-use) | Playwright | Playwright | Selenium/PW | Playwright |
| **许可证** | MIT | MIT | AGPL-3.0 | Apache 2.0 | MIT |
| **国内 LLM** | OpenAI 兼容 | OpenAI 兼容 | liteLLM 兼容 | 可配置 | OpenAI 兼容 |
| **可视化** | Web UI + Cloud | BrowserBase | 内置 Livestream | Gradio | 无 |
| **反爬** | Cloud 强 | BrowserBase | Cloud 强 | 无 | 无 |
| **生产就绪** | 高 | 中高 | 中 | 低 | 低中 |
| **集成难度** | 中 | 高 | 中 | 低 | 中 |
| **Playwright 兼容** | 否（已弃用） | 是 | 是 | 是 | 是 |

### 5.2 可视化方案核心对比

| 方案 | 适合场景 | 与本项目匹配度 |
|------|----------|----------------|
| Playwright Screencast | 自托管，仅需观看，低延迟 | ★★★★★ 最佳匹配 |
| CDP Page.screencast | 与 Browser Use 配合，极低延迟 | ★★★★☆ 备选 |
| noVNC | 需要完整交互（点击/输入） | ★★★☆☆ 场景特殊时用 |
| BrowserBase Live View | 不想自建，预算允许 | ★★★☆☆ 外部依赖 |
| WebRTC | 对延迟极度敏感 | ★★☆☆☆ 过度设计 |

---

## 6. 推荐方案

### 6.1 Agent 框架推荐：分层策略

**不推荐单一框架满足所有需求**，建议分层：

```
┌──────────────────────────────────────────────────────┐
│  项目 Agent 现有架构                                     │
│  ├── BrowserAutomationTool（现有，基于 Playwright）     │
│  │   └── 语义快照 + LLM 决策循环                        │
│  │                                                     │
│  ├── BrowserHarnessTool（反爬场景补充）                  │
│  │   └── Browser Use Cloud + CDP 直连                  │
│  │                                                     │
│  └── 可视化层（新增）                                    │
│      └── Playwright Screencast → WebSocket → 前端      │
└──────────────────────────────────────────────────────┘
```

#### 各框架推荐程度

| 框架 | 推荐度 | 理由 |
|------|--------|------|
| **Browser Use (Browser Harness)** | ★★★★★ | 已有详细研究报告，最适合作为反爬补充。MIT 许可证，社区活跃 |
| **Skyvern** | ★★★☆☆ | Playwright 一致 + 内置 Livestream 是亮点，但 AGPL 许可证是硬伤，架构过重 |
| **Stagehand** | ★★☆☆☆ | TypeScript 优先，Python 是二等公民，强绑定 BrowserBase |
| **Agent-E** | ★★☆☆☆ | DOM 蒸馏思路有价值，但社区太小，功能不完整 |
| **LaVague** | ★☆☆☆☆ | 社区萎缩，不推荐 |

### 6.2 可视化推荐：Playwright Screencast

**推荐方案**：升级 Playwright 到 1.59+，使用 `page.screencast.start(onFrame=...)` 实现实时画面推送。

**实施路径**：

```
Phase 1: 基础可视化（1-2 天）
  ├── 升级 Playwright 到 1.59+
  ├── Agent 操作时启动 screencast
  ├── onFrame 回调将 JPEG 帧通过 SSE/WebSocket 推送到前端
  └── 前端 Canvas 渲染帧画面

Phase 2: 增强可视化（3-5 天）
  ├── 添加 action 装饰（显示点击位置、输入内容）
  ├── 支持截图下载
  ├── 支持操作步骤标注（chapter overlay）
  └── 添加 play/pause/seek 控制

Phase 3: 交互能力（可选）
  ├── 前端 Canvas 上的鼠标事件 → 后端 CDP 输入事件
  ├── 支持"人工接管"模式
  └── 或者改用 noVNC 方案实现完整交互
```

**技术架构**：

```
Playwright (headless)
  ↓ page.screencast.onFrame(jpeg_data)
FastAPI WebSocket endpoint
  ↓ 二进制帧推送
前端 Canvas / <img> 标签
  ↓ 实时渲染
用户看到 Agent 操作过程
```

**带宽估算**：
- 800x600 JPEG (quality=60)：每帧约 15-30KB
- 按页面变化频率（非视频，只在操作时变化）：约 1-5 FPS
- 带宽需求：约 50-150 KB/s，完全可接受

---

## 7. 国内模型适配性分析

各框架的核心 prompt 和系统提示词都针对 GPT-4/Claude 优化。使用国内模型时需注意：

| 模型 | 框架兼容性 | 注意事项 |
|------|------------|----------|
| **Qwen (通义千问)** | 通过 OpenAI 兼容接口接入 | qwen-plus/qwen-max 效果较好，qwen-turbo 可能指令跟随不够精确 |
| **ZhipuAI (智谱)** | 通过 OpenAI 兼容接口接入 | GLM-4 系列效果较好，需注意 function calling 格式兼容性 |
| **DeepSeek** | 通过 OpenAI 兼容接口接入 | 性价比高，指令跟随能力不错 |
| **本地模型 (Ollama)** | 部分框架支持 | 效果取决于模型大小，7B 级别可能不够 |

**关键建议**：本项目已有 LLM Gateway 层，Agent 框架的 LLM 调用应通过项目的 Gateway 统一管理，而非让框架直接调用 LLM API。这样可以：
1. 复用项目的 KeyPool、并发控制、日志等能力
2. 统一切换 LLM 提供商
3. 避免引入新的 LLM API 依赖

---

## 8. 风险与注意事项

### 8.1 许可证风险

| 框架 | 许可证 | 商用风险 |
|------|--------|----------|
| Browser Use | MIT | 无风险 |
| Stagehand | MIT | 无风险 |
| **Skyvern** | **AGPL-3.0** | **需关注**：AGPL 要求网络服务也开源，商用需评估 |
| LaVague | Apache 2.0 | 无风险 |
| Agent-E | MIT | 无风险 |
| Steel | Apache 2.0 | 无风险 |

### 8.2 技术风险

1. **Browser Use 脱离 Playwright**：cdp-use 是新库，稳定性和边界情况处理可能不如 Playwright 成熟
2. **Playwright 1.59 Screencast**：是新 API，可能有性能或兼容性问题
3. **国内模型效果**：各框架的 prompt 未针对国内模型优化，可能需要额外调优
4. **并发资源**：每个浏览器会话消耗 200-500MB 内存，需规划并发上限

---

## 9. 总结与下一步

### 核心结论

1. **Agent 框架**：保持现有 BrowserAutomationTool + 引入 Browser Use (Browser Harness) 作为反爬补充，不替换现有架构
2. **可视化**：Playwright 1.59 Screencast API 是最佳方案，与现有 Playwright 技术栈完美匹配
3. **国内模型**：通过项目 LLM Gateway 的 OpenAI 兼容接口统一接入，框架不直接调用 LLM

### 建议下一步

1. **验证 Playwright Screencast**：在开发环境升级 Playwright 到 1.59+，测试 screencast API 的帧率和延迟
2. **Browser Harness 集成**：按已有的 [browser_harness_research.md](./browser_harness_research.md) 实施方案 A
3. **可视化原型**：基于 Screencast API 搭建最小可视化原型（WebSocket + Canvas）
4. **国内模型测试**：在 BrowserAutomationTool 中测试 Qwen/ZhipuAI 的浏览器任务完成率

---

## 附录 A：GitHub Stars 参考（2026-05 数据）

| 项目 | Stars |
|------|-------|
| browser-use/browser-use | 78,000+ |
| skyvern-ai/skyvern | 30,000+ |
| browserbase/stagehand | 15,000+ |
| steel-dev/steel-browser | 5,000+ |
| lavague-ai/LaVague | 5,000+ |
| EmergenceAI/Agent-E | 3,000+ |
| browser-use/web-ui | 15,000+ |

## 附录 B：参考资料

- [Browser Use 官方文档](https://docs.browser-use.com)
- [Browser Use: 从 Playwright 迁移到 CDP](https://browser-use.com/posts/playwright-to-cdp)
- [Stagehand 官方文档](https://docs.stagehand.dev)
- [Skyvern 官方文档](https://www.skyvern.com/docs/)
- [BrowserBase Live View 文档](https://docs.browserbase.com/platform/browser/observability/session-live-view)
- [Steel 官方文档](https://docs.steel.dev/)
- [Playwright Screencast API](https://playwright.dev/docs/api/class-screencast)
- [Playwright 1.59 Release Notes](https://playwright.dev/docs/release-notes)
- [11 Best AI Browser Agents in 2026 - Firecrawl](https://www.firecrawl.dev/blog/best-browser-agents)
- [Best 30+ Open Source Web Agents - AIMultiple](https://aimultiple.com/open-source-web-agents)
