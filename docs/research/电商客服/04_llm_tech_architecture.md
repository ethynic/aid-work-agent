# 淘宝客服智能体 — LLM 技术架构研究

> 调研日期: 2026-05-11 | 版本: v1.0

## 1. LLM 在电商客服中的应用现状

### 1.1 市场采用

- 82% 的企业计划在 12 个月内将 AI 智能体应用于客户支持
- Gartner 预测 2025 年有 25% 使用生成式 AI 的企业将部署自主 AI 智能体，2027 年翻倍至 50%
- 中国 2025 年已有 62% 的组织正在实践 AI 智能体

### 1.2 "大模型+小模型"协同架构

主流模式是双模型协同：

- **大模型**: 处理复杂咨询（说明书解读、故障排查、情感敏感场景），深度推理
- **小模型**: 处理高频标准化问题（订单查询、物流状态），毫秒级响应

瓴羊 Quick Service 采用此架构，实现 93% 问答准确率，响应 5 秒内，成本降 40%，效率提 3 倍。

---

## 2. 五层客服架构

现代 AI 客服系统采用五层架构：

| 层 | 职责 | 关键技术 |
|-----|------|---------|
| **对话层** | 多轮对话管理、会话状态、上下文窗口 | 对话状态追踪、上下文压缩 |
| **编排层** | Agent 工作流编排、工具路由、决策逻辑 | ReAct、Plan-and-Execute |
| **Prompt 层** | 系统 Prompt、角色定义、约束设置 | 结构化 Prompt、Few-shot |
| **RAG 层** | 知识检索、向量化、混合搜索 | Embedding + Vector DB + BM25 |
| **基础设施层** | LLM API、向量数据库、缓存、消息队列 | FastAPI、Redis、Milvus/pgvector |

---

## 3. AI Agent 架构

### 3.1 感知-决策-执行三层模式

```
感知层: 多源数据摄取（用户消息、订单状态、商品信息、用户画像）
    ↓
决策层: LLM 推理，结合 CoT 和工具选择
    ↓
执行层: 调用业务 API（订单、库存、退款、物流）
```

### 3.2 瓴羊四大核心 Agent 矩阵

1. **问答 Agent**: 知识密集型查询（产品参数、使用方法）
2. **任务 Agent**: 订单操作、退货处理（需要 API 调用）
3. **推荐 Agent**: 产品建议、搭配推荐
4. **主动 Agent**: 发货通知、售后跟进、复购引导

### 3.3 Agent 工作流编排

可视化低代码平台进行 Agent 工作流编排。例如用户说"我想取消昨天的订单"：

```
自动编排多步骤流程:
  1. 查找昨天的订单（调用订单 API）
  2. 验证订单状态是否允许取消
  3. 发起取消流程（调用取消 API）
  4. 处理退款（如已付款）
  5. 通知用户取消结果
```

---

## 4. RAG 与产品知识库

### 4.1 四阶段架构

```
1. 数据准备: 产品目录（标题、描述、规格、评论）→ 提取、清理、按元数据丰富
2. 向量存储: Embedding 模型（BGE-M3、E5）→ 向量数据库（Milvus/pgvector）
3. 混合检索: 密集向量搜索 + 稀疏 BM25 关键词搜索 + 重排序模型
4. LLM 生成: 检索到的上下文注入 Prompt 生成接地响应
```

### 4.2 关键高级策略

- **语义鸿沟弥合**: 产品描述使用用户查询语言，而非仅使用技术规格
- **上下文保留**: 分块过程中保持产品属性的结构完整性
- **元数据过滤**: 按价格范围、类别、可用性进行预过滤

### 4.3 向量数据库选型

| 方案 | 优势 | 适用场景 |
|------|------|---------|
| **pgvector** | 与现有 PostgreSQL 一致，运维成本低 | 推荐首选 |
| **Milvus** | 高性能分布式，适合大规模 | 数据量百万级以上 |
| **Weaviate/Qdrant** | 云原生，易部署 | 中小规模 |

---

## 5. Function Calling / 工具调用

### 5.1 工具定义模式

```
User: "我的订单到哪了？"
  → LLM 识别意图
  → 调用 get_order_status(order_id)
  → 调用 get_logistics_info(tracking_number)
  → LLM 用实时数据格式化响应
```

### 5.2 典型工具集

| 工具名 | 功能 | 对应淘宝 API |
|--------|------|-------------|
| `query_order(order_id)` | 查询订单详情 | `taobao.trade.fullinfo.get` |
| `check_inventory(sku_id)` | 查询库存 | `taobao.inventory.query` |
| `get_logistics(tracking_no)` | 物流追踪 | `taobao.logistics.orders.detail.get` |
| `search_products(keyword, filters)` | 商品搜索 | `taobao.items.onsale.get` |
| `initiate_return(order_id, reason)` | 发起退货 | 退款 API |
| `apply_coupon(code, cart)` | 应用优惠券 | 营销 API（聚石塔） |
| `get_user_orders(user_id, time_range)` | 用户订单列表 | `taobao.trades.sold.get` |

### 5.3 与本项目架构的映射

本项目已有成熟的工具系统（`src/tools/`），淘宝客服工具可复用现有架构：

```
BaseTool 子类
  ├── InputModel（Pydantic BaseModel，带中文 Field 描述）
  ├── name / description / display_name
  ├── async execute(**kwargs) -> Dict[str, Any]
  └── 在 Agent._register_builtin_tools() 中注册
```

---

## 6. 关键技术挑战

### 6.1 幻觉预防

**最关键的挑战** — 错误的商品信息或价格导致直接收入损失。

| 策略 | 描述 |
|------|------|
| **RAG 接地** | 生成前从知识库检索事实信息 |
| **结构化输出约束** | 强制 JSON 格式，包含必填字段，便于验证 |
| **后处理验证** | 根据实时数据库核对价格、SKU 可用性和规格 |
| **引用溯源** | 要求 Agent 引用检索来源 |
| **置信度评分** | 用单独的 LLM 评估器检查幻觉 |

腾讯企点经验: 集成 DeepSeek + RAG，在知识密集型问答中"显著减少了模型幻觉"。

### 6.2 响应延迟

**要求**: 电商客服 P95 延迟 < 3 秒，支持 1000+ 并发会话。

| 优化手段 | 说明 |
|---------|------|
| **SSE 流式传输** | LLM 通过 SSE 分块输出，快速首字响应 |
| **分层缓存** | 常见问题模式缓存，绕过 LLM 调用 |
| **推理引擎优化** | 模型量化、加速推理（BladeLLM 等） |
| **大小模型分流** | 简单问题走小模型（毫秒级），复杂问题走大模型 |

### 6.3 大促高并发

阿里双 11 经验：
- 稳定性 99.99%
- 弹性扩展仅需 3 小时
- API 密钥池（类似本项目 `KeyPool`）+ 异步处理 + 弹性扩展
- 大促期间流量可激增 10-100 倍

### 6.4 平台规则合规性

| 合规要求 | 应对策略 |
|---------|---------|
| AI 生成内容标识 | 所有对话标记为 AI 生成 |
| 数据隐私（PIPL） | 用户数据脱敏处理，最小化收集 |
| 不实承诺限制 | System Prompt 限制 AI 不做无法兑现的承诺 |
| 价格/库存来源 | 必须来自实时 API，而非 LLM 知识 |

---

## 7. 中国 LLM 提供商选型

### 7.1 通义千问 (Qwen)

- **最新**: Qwen3.5-Plus、Qwen3-Max-Thinking（2026 年）
- **优势**: 原生集成阿里生态（淘宝、天猫）、多模态、强 Function Calling
- **电商适配**: 淘宝联合发起"2026 电商 AI 挑战赛"，深度优化电商场景
- **推荐度**: ★★★★★（与淘宝生态天然契合）

### 7.2 智谱 AI (ChatGLM/ZhipuAI)

- **优势**: 原生 Function Calling、多轮工具调用、强中文理解
- **电商适配**: 良好的文化细微差别处理
- **推荐度**: ★★★★（本项目已接入）

### 7.3 DeepSeek

- **优势**: 成本低、NLP 能力强、多模态
- **注意**: 需谨慎的幻觉缓解，推理风格可能产生自信但不正确的答案
- **推荐度**: ★★★★（性价比高）

### 7.4 对比总结

| 提供商 | Function Calling | 多模态 | 电商适配 | 成本 | 与本项目契合 |
|--------|-----------------|--------|---------|------|------------|
| Qwen | ★★★★★ | ★★★★★ | ★★★★★ | 中 | 高（阿里生态） |
| ZhipuAI | ★★★★ | ★★★★ | ★★★ | 中 | 已接入 |
| DeepSeek | ★★★ | ★★★★ | ★★★ | 低 | 可接入 |

### 7.5 推荐策略

**大小模型协同**:
- 大模型: Qwen（电商深度优化）或 ZhipuAI（已接入）
- 小模型: 轻量级 Qwen 模型或 BERT 类模型，处理高频标准化查询

---

## 8. 多 Agent 系统趋势

### 8.1 从单 Agent 到多 Agent

2026 年行业趋势：从单一 AI Agent 向多 Agent 协作编排转变。

- **Agent 专业化**: 不同 Agent 处理不同任务（订单、退货、产品知识、投诉）
- **协作编排**: 一个路由 Agent 确定将任务委托给哪个专业 Agent
- **实时调整**: Agent 根据对话上下文动态调整行为

### 8.2 与本项目子智能体架构的映射

本项目已有完整的子智能体系统（`subagents/`），可复用：

```
淘宝客服主 Agent（路由）
  ├── 商品推荐子智能体
  ├── 订单处理子智能体
  ├── 售后服务子智能体
  ├── 优惠活动子智能体
  └── 产品知识子智能体
```

每个子智能体通过 `SUBAGENT.md` 定义，拥有独立的工具集和知识库。

---

## 9. 架构关键决策

| 决策 | 推荐方案 | 理由 |
|------|---------|------|
| LLM 选择 | Qwen（主）+ ZhipuAI（备） | 生态整合 + 已有能力 |
| 幻觉预防 | RAG + 后验证 + 结构化输出 | 不依赖 LLM 知识获取价格/库存 |
| 架构模式 | 多 Agent 专业化 | 避免单一 Prompt 臃肿 |
| 知识库 | 混合搜索（向量 + BM25 + 元数据过滤） | 电商需精确匹配和语义匹配 |
| 延迟优化 | SSE 流式 + 缓存 + 大小模型分流 | 客户期望 P95 < 3 秒 |
| 并发处理 | API 密钥池 + 异步 + 弹性扩展 | 大促流量激增 |
| 合规 | AI 标识 + 数据最小化 + Prompt 约束 | 中国监管要求 |

---

## 参考来源

- [MDPI: RAG 客服聊天机器人设计](https://www.mdpi.com/2674-113X/5/2/15)
- [ScienceDirect: 中国电商平台 AI 功能](https://www.sciencedirect.com/science/article/abs/pii/S0747563226000968)
- [The World of Chinese: DeepSeek 驱动电商](https://www.theworldofchinese.com/2025/06/how-ai-is-driving-chinas-next-e-commerce-push/)
- [CSDN: Qwen3 电商客服多轮对话部署](https://deepseek.csdn.net/69f6a6e054b52172bc718827.html)
- [知乎: Qwen3.5 系列发布](https://zhuanlan.zhihu.com/p/2008846603730048341)
- [腾讯云: 腾讯企点接入 DeepSeek](https://cloud.tencent.com/developer/article/2500101)
- [53AI: DeepSeek 幻觉问题](https://www.53ai.com/news/RAG/2025021454301)
- [AWS: RAG 幻觉检测](https://aws.amazon.com/blogs/machine-learning/detect-hallucinations-for-rag-based-systems/)
- [掘金: 企业级知识库 RAG 高级策略](https://juejin.cn/post/7603219519667421203)
- [阿里云: 瓴羊 Quick Service 实践](https://developer.aliyun.com/article/1710247)
- [驱动之家: 瓴羊四大 Agent 矩阵](https://news.mydrivers.com/1/1117/1117902.htm)
- [阿里云: Agent 工作流编排](https://developer.aliyun.com/article/1715169)
- [智谱 AI: 工具调用文档](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)
- [澎湃新闻: 2026 Agentic AI 十大趋势](https://m.thepaper.cn/newsDetail_forward_32317179)
- [新华网: 多智能体上岗元年](http://www.news.cn/tech/20260123/5f74717e26fe477d9411f7cc5/c.html)
