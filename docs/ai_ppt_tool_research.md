# AI PPT 生成工具调研报告

> 调研日期: 2026-05-09 | 版本: v1.0 | 调研范围: 国内外主流 AI PPT 工具、API 服务、开源项目

---

## 目录

- [1. 总览对比表](#1-总览对比表)
- [2. 国际 SaaS 工具](#2-international-saas-tools)
  - [2.1 Gamma](#21-gamma)
  - [2.2 Beautiful.ai](#22-beautifulai)
  - [2.3 Tome (已关停)](#23-tome)
  - [2.4 SlideSpeak](#24-slidespeak)
  - [2.5 Canva](#25-canva)
- [3. 国内 SaaS 工具](#3-domestic-saas-tools)
  - [3.1 AiPPT / 爱皮皮提](#31-aippt)
  - [3.2 讯飞智文](#32-讯飞智文)
  - [3.3 百度文库 AI PPT](#33-百度文库-ai-ppt)
  - [3.4 WPS AI](#34-wps-ai)
  - [3.5 MindShow](#35-mindshow)
- [4. 开发者工具 & 库](#4-developer-tools--libraries)
  - [4.1 Marp](#41-marp)
  - [4.2 Slidev](#42-slidev)
  - [4.3 python-pptx](#43-python-pptx)
  - [4.4 Aspose.Slides](#44-asposeslides)
- [5. 开源 AI PPT 项目 (GitHub)](#5-open-source-ai-ppt-projects-github)
  - [5.1 PPTAgent / DeepPresenter](#51-pptagent--deeppresenter)
  - [5.2 Presenton](#52-presenton)
  - [5.3 ppt-master](#53-ppt-master)
  - [5.4 其他开源项目](#54-other-projects)
- [6. API 可用性汇总](#6-api-availability-summary)
- [7. 企业集成评估](#7-enterprise-integration-assessment)
- [8. 推荐方案](#8-recommendation)

---

## 1. 总览对比表

| 工具 | 类型 | 中文支持 | API 可用 | 输出格式 | 价格区间 | 适合企业集成 |
|------|------|----------|----------|----------|----------|-------------|
| **Gamma** | SaaS + API | 一般 | REST API (Pro+) | PDF, PPTX(导出) | $8-20/月 | 中 |
| **Beautiful.ai** | SaaS + API | 差 | REST API (Team+) | PPTX, PDF | $12-40/月/人 | 中 |
| **SlideSpeak** | SaaS + API | 一般 | REST API (全面) | PPTX, PDF | $49-99/月起 | 高 |
| **AiPPT** | SaaS + API | 优秀 | REST API (完整) | PPTX | 按量计费 | 高 |
| **讯飞智文** | SaaS + API | 优秀 | REST API (完整) | PPTX | 1000点免费, 付费~1344元 | 高 |
| **百度文库 AI PPT** | SaaS + API | 优秀 | REST API (千帆平台) | PPTX | 付费(需申请) | 中 |
| **PPTAgent** | 开源 | 良好 | 无(自行部署) | PPTX | 免费 | 中 |
| **Presenton** | 开源 | 一般 | 自建 API | PDF, PPTX | 免费 | 高 |
| **MindShow** | SaaS | 优秀 | 无 | PPTX | 免费+付费 | 低 |
| **WPS AI** | SaaS | 优秀 | 暂无公开API | PPTX | 免费+会员 | 低 |
| **python-pptx** | 开源库 | N/A | 程序库 | PPTX | 免费 | 高 |
| **Marp** | 开源工具 | 良好 | CLI | PDF, PPTX, HTML | 免费 | 中 |
| **Slidev** | 开源工具 | 良好 | CLI/Dev | PDF, SPA(HTML) | 免费 | 中 |

---

## 2. 国际 SaaS 工具

### 2.1 Gamma

| 项目 | 说明 |
|------|------|
| **官网** | https://gamma.app |
| **类型** | SaaS + REST API |
| **API 文档** | https://developers.gamma.app |
| **API 可用性** | Pro 及以上计划可用 (Beta) |
| **中文支持** | 支持生成中文内容，但模板和 UI 以英文为主，中文排版一般 |

**定价:**

| 计划 | 价格 | 说明 |
|------|------|------|
| Free | $0 | 400 AI credits 一次性, 每次最多 10 张卡片 |
| Plus | $8-10/月/人 | 无限 AI 创作, 去除 Gamma 品牌 |
| Pro | $15-20/月/人 | 高级 AI 模型, API 访问权限 |
| Business | 自定义 | 团队管理, 企业级功能 |

**API 详情:**
- 认证: `X-API-KEY` header
- 核心端点:
  - `POST /v1.0/generations` — 从文本生成演示文稿
  - `POST /v1.0/generations/from-template` — 从模板生成
  - `GET /v1.0/generations/{id}` — 轮询生成状态
  - `GET /v1.0/themes` — 获取工作区主题
- 异步模式: 创建任务后轮询结果 (每 5 秒)
- 支持导出为 PDF 或 PPTX
- 提供 MCP Server 支持 AI Agent 集成
- 支持 ChatGPT / Claude 连接器

**优点:**
- API 设计清晰, 文档完善
- 支持多种输出格式 (演示文稿, 文档, 网站, 社交帖子)
- 可通过 MCP Server 与 AI Agent 集成
- 设计质量较高, 模板美观

**缺点:**
- API 目前仍在 Beta 阶段
- 需要 Pro 以上计划 ($15-20/月/人) 才能使用 API
- 中文排版质量一般
- 异步轮询模式增加了集成复杂度

---

### 2.2 Beautiful.ai

| 项目 | 说明 |
|------|------|
| **官网** | https://www.beautiful.ai |
| **类型** | SaaS + REST API |
| **API 文档** | https://docs.beautiful.ai |
| **API 可用性** | Team 及以上计划可用 |
| **中文支持** | 基本不支持中文, 模板和 UI 全英文 |

**定价:**

| 计划 | 价格 | 说明 |
|------|------|------|
| Pro | $12/月(年付) / $45/月(月付) | 个人用户 |
| Team | $40/人/月(年付) | 最多 20 人 |
| SMB 50 | $6,000/年 | 50 人团队 |
| SMB 100 | $12,000/年 | 100 人团队 |
| Enterprise | 自定义 | 大型企业 |

**API 详情:**
- 提供 prompt-to-deck 端点: 发送自然语言文本, 返回完整设计的演示文稿
- 生成的演示文稿完全可编辑
- 支持自定义品牌主题
- RESTful API, JSON 格式

**优点:**
- 设计质量非常高, 被誉为"设计师级"AI 演示文稿
- API 输出为完全可编辑的演示文稿
- 智能布局系统自动优化排版

**缺点:**
- 中文支持极差, 不适合中文场景
- API 仅 Team 及以上计划可用, 成本较高 ($40/人/月起)
- 文档相对简单, 功能有限

---

### 2.3 Tome

| 项目 | 说明 |
|------|------|
| **状态** | **已于 2025-2026 年关停** |
| **官网** | https://www.tomeapp.com (已不可用) |

Tome 曾是 AI 演示文稿领域的先驱之一, 以"AI 故事讲述"为特色。2025 年后逐步关停, 多个来源确认其已不再作为演示文稿工具运营。定价曾为 Free/$0、Plus/$10/月、Pro/$16-20/月。

**替代方案:** Gamma、Beautiful.ai、讯飞智文等。

---

### 2.4 SlideSpeak

| 项目 | 说明 |
|------|------|
| **官网** | https://slidespeak.co |
| **类型** | SaaS + REST API (面向开发者) |
| **API 文档** | https://slidespeak.co/features/slidespeak-api |
| **API 可用性** | 所有付费计划 |
| **中文支持** | 支持中文内容生成 |

**定价 (API):**

| 计划 | 价格 | Credits | 说明 |
|------|------|---------|------|
| Starter | $49/月 | 1,000 credits | 1 credit = 1 张幻灯片 |
| Popular | $99/月 | 2,500 credits | 最受欢迎 |
| Enterprise | 自定义 | 5,000+ | 企业级功能 |

**API 详情:**
- 输入: 纯文本、PDF、Word 文档、JSON
- 输出: PowerPoint (.pptx)、PDF
- 核心功能:
  - 从文本/文档生成演示文稿
  - 编辑现有 PowerPoint 文件
  - 按幻灯片精确定义内容
  - 自定义模板和布局
- 集成: Zapier, Make.com, Postman Collection, MCP Server
- 支持 Python, Node.js 等 SDK

**优点:**
- 面向开发者设计, API 文档最全面
- 支持多种输入源 (文本, PDF, Word, JSON)
- Credit 计费模式简单可预测
- 提供 MCP Server 用于 AI Agent 集成
- 企业级安全功能 (SSO, 自定义存储)

**缺点:**
- Credit 消耗较快 (1 credit/slide), 大量使用成本高
- 不支持实时协作编辑
- 中文支持仅为基本级别, 不是强项
- 不适合需要高度定制设计的场景

---

### 2.5 Canva

| 项目 | 说明 |
|------|------|
| **官网** | https://www.canva.com |
| **类型** | SaaS + Apps SDK |
| **API 可用性** | Apps SDK (JavaScript, 仅限编辑器内) |
| **中文支持** | 良好 |

**定价:**

| 计划 | 价格 | 说明 |
|------|------|------|
| Free | $0 | 基础功能 |
| Pro | $14.99/月/人 | 完整设计功能 |
| Teams | $29.99/月/人起 | 团队协作 |

**API 详情:**
- 提供 Apps SDK (JavaScript), 非传统 REST API
- 仅能在 Canva 编辑器内运行, 不支持后端自动化
- 功能: 资产上传、设计元素操作、导出
- 需要通过 App 审批流程

**优点:**
- 设计质量极高, 丰富的模板库
- 免费提供 SDK
- 庞大的用户社区

**缺点:**
- **无 REST API, 无法后端自动化** — 这是最大的限制
- 仅限 JavaScript 开发
- 需要通过 App 审批流程
- 不适合程序化批量生成

---

## 3. 国内 SaaS 工具

### 3.1 AiPPT

| 项目 | 说明 |
|------|------|
| **官网** | https://www.aippt.cn (国内) / https://www.aippt.com (国际) |
| **开放平台** | https://open.aippt.cn |
| **类型** | SaaS + 完整 REST API |
| **API 可用性** | 完整 API, 需申请 API Key |
| **中文支持** | 优秀 (专为中文场景设计) |

**定价:**

| 计划 | 价格 | 说明 |
|------|------|------|
| 免费版 | $0 | 基础功能, 有限次数 |
| Plus | $9/月 (年付 $108) | 20次/日 AI 生成 + 500 AI 图片积分/月 |
| Pro | 更高 | 无限次数 |

**API 详情 (完整流程):**
1. `POST /api/ai/chat/task` — 创建任务, 获取 task_id
2. `GET /api/ai/chat/outline?task_id=N` — 标题生成大纲 (SSE 流式)
3. `GET /api/ai/chat/content?task_id=N` — 大纲生成内容 (SSE 流式)
4. `POST /api/ai/chat/outline/save` — 编辑大纲
5. `GET /api/template_component/suit/search` — 模板套装列表
6. `GET /api/template_component/suit/select` — 模板筛选项
7. `GET /api/design/list` — 作品列表
8. 支持 Word 文档导入生成

**认证:** `x-api-key` + `x-token` + `x-channel` headers

**优点:**
- **API 最为完整**, 覆盖创建->大纲->内容->模板->生成全流程
- 中文场景优化最好, 大量中文模板
- 支持大纲编辑和自定义
- 支持多种 AI 引擎 (默认 AI + 百度 AI)
- 提供模板套装筛选 (颜色、风格)
- 文档清晰, 示例完整

**缺点:**
- API 需要申请, 审批流程不透明
- SSE 流式返回增加了客户端复杂度
- 依赖第三方服务, 数据安全需评估
- 定价信息不够公开透明

---

### 3.2 讯飞智文

| 项目 | 说明 |
|------|------|
| **官网** | https://zhiwen.xfyun.cn |
| **API 文档** | https://www.xfyun.cn/doc/spark/PPTv2.html (新版) |
| **类型** | SaaS + REST API |
| **API 可用性** | 完整 API, 通过讯飞开放平台 |
| **中文支持** | 优秀 (科大讯飞, 中文 NLP 起家) |

**定价:**
- 免费额度: 1,000 点 (约 100 次生成)
- 付费购买: 最低约 1,344 元
- 每次 PPT 生成消耗 10 点

**API 详情 (v2 新版):**
1. PPT 创建接口: `https://zwapi.xfyun.cn/api/aippt/create`
2. PPT 主题列表: `https://zwapi.xfyun.cn/api/ppt/v2/template/list`
3. PPT 进度查询: `https://zwapi.xfyun.cn/api/ppt/v2/progress`
- 支持通过文本或文档 (PDF 等, 限 10M) 生成 PPT
- 新版和旧版接口不能混用

**优点:**
- 大厂背景 (科大讯飞), 服务稳定
- 免费额度充足 (100 次), 适合测试
- API 文档完善, 支持多语言 SDK
- 中文理解和生成质量高
- 已有 MCP Server 开源项目 (github.com/Alieforwang/xunfeiPpt)

**缺点:**
- 付费价格偏高 (约 1344 元起)
- 模板设计质量中规中矩
- v2 API 接口仍在迭代中
- 生成速度相对较慢

---

### 3.3 百度文库 AI PPT

| 项目 | 说明 |
|------|------|
| **官网** | https://wenku.baidu.com |
| **API 入口** | 百度千帆平台 / 百度网盘开放平台 |
| **API 文档** | https://cloud.baidu.com/doc/qianfan/s/4mjcnolh6 |
| **类型** | SaaS + API (千帆平台) |
| **中文支持** | 优秀 |

**API 详情:**
- 通过百度千帆大模型平台提供 API
- 支持基于大纲内容和上传文件快速生成 PPT
- 支持自定义模板
- 已上架 Dify Marketplace, 可低代码集成
- 也可通过百度网盘企业开发者平台接入 SaaS UI 版

**定价:**
- 付费功能, 需申请 API 权限
- 具体价格需联系商务

**优点:**
- 百度生态整合, 模板资源丰富
- 千帆平台提供完善的 API 管理
- 已有 Dify 插件, 可快速集成
- 支持文档导入生成

**缺点:**
- API 权限申请流程较复杂
- 定价不透明
- 强依赖百度生态
- 文档质量参差不齐

---

### 3.4 WPS AI

| 项目 | 说明 |
|------|------|
| **官网** | https://ai.wps.cn |
| **类型** | SaaS (WPS Office 内置) |
| **API 可用性** | 暂无公开 API |
| **中文支持** | 优秀 |

**功能:**
- 内嵌在 WPS Office 中, 支持一键生成 PPT
- 支持从大纲、文档生成
- 与 WPS 云文档生态深度整合
- 支持中文排版, 模板本地化程度高

**优点:**
- 与 WPS Office 无缝集成, 企业用户基数大
- 中文排版和模板质量好
- 免费/会员制, 成本低

**缺点:**
- **无公开 API**, 无法程序化集成
- 仅限 WPS 生态内使用
- AI 生成质量中等, 定制化程度有限

---

### 3.5 MindShow

| 项目 | 说明 |
|------|------|
| **官网** | https://www.mindshow.ai / https://mindshow.fun |
| **类型** | SaaS |
| **API 可用性** | 无公开 API |
| **中文支持** | 优秀 |

**功能:**
- Markdown 直接转 PPT (不改文本)
- Word (.docx) 转 PPT
- 思维导图 (XMind) 转 PPT
- 标题一键生成完整演示文稿
- PPT 导入再编辑
- iOS 移动端 App

**优点:**
- Markdown 转 PPT 是其核心优势, 适合技术用户
- 支持多种输入格式 (Markdown, Word, XMind)
- 使用简单, 上手快

**缺点:**
- **无 API**, 不支持程序化调用
- 设计模板相对有限
- 生成质量中规中矩
- 不适合企业级集成

---

## 4. 开发者工具 & 库

### 4.1 Marp

| 项目 | 说明 |
|------|------|
| **官网** | https://marp.app |
| **GitHub** | https://github.com/marp-team/marp |
| **类型** | 开源 CLI 工具 |
| **许可证** | MIT |
| **输出** | PDF, PPTX, HTML |

**特点:**
- 纯 Markdown 编写, 用 `---` 分隔幻灯片
- 支持主题自定义 (CSS)
- CLI 工具, 可程序化调用
- 支持 Mermaid 图表渲染
- VSCode 插件支持实时预览

**适用场景:** 技术文档转演示文稿, 开发者快速制作简单幻灯片。

---

### 4.2 Slidev

| 项目 | 说明 |
|------|------|
| **官网** | https://sli.dev |
| **GitHub** | https://github.com/slidevjs/slidev |
| **类型** | 开源开发者演示工具 |
| **许可证** | MIT |
| **输出** | PDF, SPA (HTML), PNG |

**特点:**
- Markdown + Vue.js 组件
- 支持实时编码演示 (Live Coding)
- 支持演讲者模式
- 丰富的插件生态
- npm 生态集成

**适用场景:** 技术分享, 开发者演讲, 交互式演示。不适合生成传统 PPTX。

---

### 4.3 python-pptx

| 项目 | 说明 |
|------|------|
| **GitHub** | https://github.com/scanny/python-pptx |
| **类型** | Python 库 |
| **许可证** | MIT |
| **输出** | PPTX |

**特点:**
- Python 操作 PowerPoint 的标准库
- 支持: 幻灯片、文本框、形状、图表、表格、图片
- 不依赖 Microsoft Office
- 纯 Python 实现, 跨平台

**LLM + python-pptx 架构模式:**
```
用户 Prompt -> LLM 生成结构化内容 (JSON/Markdown)
                      |
                      v
              python-pptx 脚本渲染 .pptx
                      |
                      v
              可下载的 PowerPoint 文件
```

**适用场景:** 自建 AI PPT 生成流程的基础库, 最大的灵活性和控制力。

---

### 4.4 Aspose.Slides

| 项目 | 说明 |
|------|------|
| **官网** | https://products.aspose.com/slides |
| **类型** | 商业 SDK |
| **许可证** | 商业许可 |
| **支持语言** | Java, Python, C#, Node.js, C++ |
| **输出** | PPT, PPTX, PDF, 图片 |

**定价:**
- Developer: $999 (一次性)
- OEM: $2,997 (一次性)

**特点:**
- 功能最全面的商业 PPT 操作库
- 多语言 SDK 支持
- 企业级技术支持

**适用场景:** 企业级 PPT 自动化, 需要完整 PPTX 操作能力的场景。

---

## 5. 开源 AI PPT 项目 (GitHub)

### 5.1 PPTAgent / DeepPresenter

| 项目 | 说明 |
|------|------|
| **GitHub** | https://github.com/icip-cas/PPTAgent |
| **Stars** | ~4,300 |
| **开发者** | 中科院计算所 (ICIP-CAS) |
| **许可证** | 学术/开源 |
| **论文** | EMNLP 2025 |

**核心特性:**
- **两阶段生成**: 参考人类工作流, 先分析参考 PPT 再编辑生成
- **V2 (DeepPresenter, 2025/12)**: 重大升级
  - Deep Research Integration (深度研究集成)
  - Free-Form Visual Design (自由视觉设计)
  - Autonomous Asset Creation (自主资产创建)
  - Text-to-Image 能力
- 支持 PPTX 导出
- 支持离线模式
- 上下文管理防止溢出

**架构:**
```
用户输入 -> PPTAgent (分析参考PPT) -> 编辑操作序列 -> 生成新 PPTX
```

**优点:**
- 学术论文支撑, 方法论成熟 (EMNLP 2025)
- 生成质量高, 通过学习参考 PPT 的设计风格
- 支持完全自部署
- 中文团队开发, 中文支持好

**缺点:**
- 需要自行部署和维护
- 依赖 GPU 资源
- 对参考 PPT 质量有要求
- 文档以学术为主, 工程化程度一般

---

### 5.2 Presenton

| 项目 | 说明 |
|------|------|
| **GitHub** | https://github.com/presenton/presenton |
| **官网** | https://presenton.ai |
| **类型** | 开源 AI 演示文稿生成器 (Gamma 替代品) |
| **定位** | 自托管, 隐私优先 |

**核心特性:**
- 从提示词或上传文档生成幻灯片
- 导出为 PowerPoint (PPTX) 和 PDF
- 支持导入 PPTX 模板
- 自定义布局
- 多 AI 模型支持: OpenAI, Gemini, **Ollama (本地模型)**
- 可完全本地运行 (搭配 Ollama)
- 提供在线 Demo

**优点:**
- 完全开源, 可自部署
- 支持本地 AI 模型 (Ollama), 数据不出网
- 多模型支持, 不锁定单一 AI 提供商
- 适合企业内部部署

**缺点:**
- 项目相对年轻, 功能仍在完善
- 文档较少
- 社区规模小
- 设计质量不如商业 SaaS

---

### 5.3 ppt-master

| 项目 | 说明 |
|------|------|
| **GitHub** | https://github.com/hugohe3/ppt-master |
| **类型** | AI 生成原生可编辑 PPTX |

**核心特性:**
- 从任意文档生成 PPTX
- 生成的是**真正的 PowerPoint 形状** (不是图片)
- 支持原生动画
- 输出可直接在 PowerPoint 中编辑

**优点:**
- 输出为原生 PPTX 格式, 可编辑性最好
- 不依赖外部 SaaS 服务

**缺点:**
- 项目规模较小
- 文档有限
- 设计自定义能力有限

---

### 5.4 其他开源项目

| 项目 | GitHub | 说明 |
|------|--------|------|
| **llm-powerpoint** | github.com/M1T8E6/llm-powerpoint | Jupyter Notebook 演示, LLM 生成 PPT |
| **llm_pptx_deck_builder** | github.com/jc7k/llm_pptx_deck_builder | AI 驱动的研究演示生成器, 带 Brave Search + LLM |
| **讯飞PPT MCP Server** | github.com/Alieforwang/xunfeiPpt | 讯飞智文 API 的 MCP Server 封装 |
| **ALLWEONE** | github.com/topics/powerpoint-generation | TypeScript 开源 AI 演示文稿生成器 |

---

## 6. API 可用性汇总

### 有完整 API 的服务

| 服务 | API 成熟度 | API 类型 | 认证方式 | 输出格式 | 中文支持 |
|------|-----------|---------|----------|----------|----------|
| **AiPPT** | 高 (生产级) | REST + SSE | API Key + Token | PPTX (在线编辑+下载) | 优秀 |
| **讯飞智文** | 高 (生产级) | REST | API Key (讯飞开放平台) | PPTX | 优秀 |
| **Gamma** | 中 (Beta) | REST (异步) | X-API-KEY | PDF, PPTX | 一般 |
| **SlideSpeak** | 高 (生产级) | REST | API Key | PPTX, PDF | 一般 |
| **Beautiful.ai** | 中 | REST | OAuth | PPTX, PDF | 差 |
| **百度文库 AI PPT** | 中 | REST (千帆) | OAuth | PPTX | 优秀 |

### 无 API 或 API 受限的服务

| 服务 | 状态 |
|------|------|
| WPS AI | 暂无公开 API |
| MindShow | 无 API |
| Canva | 仅 Apps SDK (编辑器内), 无后端 API |
| Tome | 已关停 |

### 自建方案 (开源)

| 项目 | 输出 | AI 模型 | 部署复杂度 |
|------|------|---------|-----------|
| PPTAgent | PPTX | 自选 | 高 |
| Presenton | PDF, PPTX | OpenAI/Gemini/Ollama | 中 |
| python-pptx + LLM | PPTX | 自选 | 中 |
| Marp | PDF, PPTX, HTML | 需自行集成 | 低 |

---

## 7. 企业集成评估

### 评估维度

对每个适合集成到企业 AI Agent 系统的方案, 从以下维度评估:

| 维度 | 权重 | 说明 |
|------|------|------|
| API 完整性 | 高 | 是否覆盖创建->大纲->内容->模板->生成全流程 |
| 中文支持 | 高 | 中文内容生成和排版质量 |
| 可编辑性 | 高 | 输出的 PPTX 是否可二次编辑 |
| 数据安全 | 高 | 是否支持私有化部署, 数据是否经过第三方 |
| 成本 | 中 | API 调用费用是否可控 |
| 集成难度 | 中 | 接入开发工作量 |
| 输出质量 | 中 | 生成 PPT 的设计水平 |

### 各方案评分 (1-5 分)

| 方案 | API完整性 | 中文 | 可编辑 | 数据安全 | 成本 | 集成难度 | 输出质量 | 加权总分 |
|------|----------|------|--------|---------|------|---------|---------|---------|
| **AiPPT API** | 5 | 5 | 4 | 2 | 3 | 4 | 4 | **3.9** |
| **讯飞智文 API** | 4 | 5 | 4 | 3 | 3 | 4 | 3 | **3.7** |
| **Gamma API** | 4 | 2 | 3 | 2 | 3 | 4 | 5 | **3.3** |
| **SlideSpeak API** | 5 | 3 | 4 | 2 | 2 | 5 | 4 | **3.5** |
| **Presenton (自部署)** | 3 | 3 | 4 | 5 | 5 | 3 | 3 | **3.7** |
| **python-pptx + LLM** | 3 | 4 | 5 | 5 | 5 | 2 | 2 | **3.6** |
| **PPTAgent (自部署)** | 2 | 4 | 5 | 5 | 5 | 2 | 4 | **3.6** |

---

## 8. 推荐方案

### 方案 A: 国内 API 服务集成 (推荐, 快速上线)

**首选: AiPPT API**

- API 最完整, 覆盖创建->大纲->内容->模板->生成全流程
- 中文场景最佳
- 文档清晰, 集成成本最低
- 适合快速上线验证需求

**备选: 讯飞智文 API**

- 大厂服务稳定
- 免费额度充足 (100 次)
- 中文质量好
- 已有 MCP Server 可参考

**集成方式:**
1. Agent 接收用户的 PPT 生成请求
2. 调用 AiPPT/讯飞 API 创建任务
3. 流式获取大纲, 展示给用户确认/编辑
4. 调用内容生成接口
5. 选择模板
6. 下载生成的 PPTX 文件

### 方案 B: 自建方案 (推荐, 长期可控)

**架构: LLM + python-pptx**

```
用户需求 -> Agent 理解意图
                    |
                    v
            LLM 生成结构化大纲 (JSON)
                    |
                    v
            用户确认/编辑大纲
                    |
                    v
            LLM 逐页生成内容 (JSON)
                    |
                    v
            python-pptx 渲染 PPTX (基于模板)
                    |
                    v
            返回可编辑的 .pptx 文件
```

**优势:**
- 完全自主可控, 无第三方依赖
- 数据安全有保障
- 可深度定制模板和样式
- 成本仅 LLM 调用费 (本项目已有 LLM Gateway)

**挑战:**
- 需要开发模板引擎
- 设计质量依赖模板设计能力
- 开发周期较长

### 方案 C: 混合方案 (推荐, 平衡)

- **快速上线**: 先接入 AiPPT API 或讯飞智文 API, 满足即时需求
- **长期规划**: 同步开发 LLM + python-pptx 自建方案
- **模板策略**: 准备 10-20 个企业常用模板 (python-pptx 格式)
- **渐进迁移**: 自建方案成熟后逐步替代第三方 API

---

## 参考资料

- [Gamma API 文档](https://developers.gamma.app)
- [Gamma API 定价](https://developers.gamma.app/get-started/access-and-pricing)
- [Beautiful.ai API 文档](https://docs.beautiful.ai)
- [Beautiful.ai 定价](https://www.beautiful.ai/pricing)
- [SlideSpeak API](https://slidespeak.co/features/slidespeak-api)
- [SlideSpeak API 定价](https://slidespeak.co/api-pricing)
- [AiPPT 开放平台](https://open.aippt.cn)
- [讯飞智文 API 文档 (v2)](https://www.xfyun.cn/doc/spark/PPTv2.html)
- [百度千帆智能PPT](https://cloud.baidu.com/doc/qianfan/s/4mjcnolh6)
- [百度网盘智能PPT接口](https://pan.baidu.com/union/doc/Rmef7u4tj)
- [PPTAgent GitHub](https://github.com/icip-cas/PPTAgent)
- [Presenton GitHub](https://github.com/presenton/presenton)
- [ppt-master GitHub](https://github.com/hugohe3/ppt-master)
- [python-pptx GitHub](https://github.com/scanny/python-pptx)
- [Marp GitHub](https://github.com/marp-team/marp)
- [Slidev](https://sli.dev)
- [Top 5 AI Presentation APIs (SlideSpeak)](https://slidespeak.co/guides/top-5-ai-presentation-apis-2025)
- [2026年AI PPT工具横评 (CSDN)](https://blog.csdn.net/SpaceAIGlobal/article/details/159984239)
- [18款PPT AI 生成工具深度测评 (知乎)](https://zhuanlan.zhihu.com/p/1948133334464037069)
- [十大AI生成PPT工具排名 (知乎)](https://zhuanlan.zhihu.com/p/1949834434233763066)
