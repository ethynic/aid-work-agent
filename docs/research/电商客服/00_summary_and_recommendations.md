# 淘宝客服智能体 — 研究总结与开发建议

> 调研日期: 2026-05-11 | 版本: v1.0

## 文件索引

| 文件 | 内容 |
|------|------|
| [01_market_competitive_analysis.md](01_market_competitive_analysis.md) | 市场概况、竞品分析（店小蜜/瓴羊/晓多/乐言/智齿/网易七鱼/腾讯企点）、定价模式 |
| [02_taobao_api_integration.md](02_taobao_api_integration.md) | 淘宝开放平台 API、千牛集成、TMC 消息服务、认证授权 |
| [03_functional_domain_deep_dive.md](03_functional_domain_deep_dive.md) | 商品推荐、优惠活动、产品使用指导、订单管理、售后、客户画像、人机协作 |
| [04_llm_tech_architecture.md](04_llm_tech_architecture.md) | LLM 架构模式、RAG 知识库、Function Calling、幻觉预防、LLM 选型 |

---

## 研究核心发现

### 1. 市场格局

- 店小蜜 5.0 是当前淘宝生态最强竞品（Agent 原生、意图识别 94%+），作为官方产品拥有最深集成
- 第三方 ISV（晓多、乐言、智齿）通过千牛插件 + TOP API 接入，在跨平台和性价比上有优势
- 定价从免费（店小蜜基础版）到年费 8 万+（企业版），行业正向"按效果付费"转型

### 2. 技术路线

- **2026 年主流**: AI Agent + 大小模型协同 + 多模态
- **核心架构**: 五层（对话→编排→Prompt→RAG→基础设施）
- **关键能力**: Function Calling（工具调用）是连接 LLM 与淘宝 API 的核心机制
- **RAG 是标配**: 所有主流产品都使用 RAG 做产品知识库，混合检索（向量+BM25+元数据过滤）是最佳实践

### 3. 与本项目的契合度

本项目（aid-work-agent）已具备构建淘宝客服智能体的核心能力：

| 本项目能力 | 淘宝客服映射 | 契合度 |
|-----------|------------|--------|
| LLM 网关（Qwen/ZhipuAI） | 电商大模型底座 | 高 — 已接入两大主流 LLM |
| 工具系统（BaseTool + ToolRegistry） | 淘宝 API 工具封装 | 高 — 直接复用架构 |
| 子智能体系统（SubagentExecutor） | 专业化 Agent 编排 | 高 — 可按功能域拆分子智能体 |
| 技能系统（SkillRegistry + SKILL.md） | 电商知识库 | 高 — 产品知识可封装为 Skill |
| 多租户（TenantContextMiddleware） | 多店铺隔离 | 高 — 天然支持多商家 |
| 渠道系统（ChannelAdapter） | 千牛消息通道 | 中 — 需新增千牛渠道适配器 |
| RAG 知识库（向量检索） | 产品知识库 | 中 — 需扩展电商特化的检索策略 |

---

## 开发建议

### 分阶段实施路线

#### Phase 1: MVP（核心对话能力）

**目标**: 接入千牛消息通道，实现基础商品咨询和订单查询

1. **千牛渠道适配器**
   - 实现 `QianniuChannel` 继承 `ChannelAdapter`
   - 对接千牛自动化机器人 WebHook，收发消息
   - 处理 OAuth 授权流程

2. **淘宝 API 工具集**
   - `TaobaoOrderTool` — 订单查询（`taobao.trade.fullinfo.get`）
   - `TaobaoLogisticsTool` — 物流追踪（`taobao.logistics.orders.detail.get`）
   - `TaobaoProductTool` — 商品搜索（`taobao.items.onsale.get`）
   - `TaobaoRefundTool` — 退款查询（`taobao.refund.get`）
   - `TaobaoInventoryTool` — 库存查询（`taobao.inventory.query`）

3. **电商知识 Skill**
   - 创建 `taobao-cs-1.0.0` 技能目录
   - SKILL.md 中定义电商客服角色、话术规范、常见 FAQ
   - 产品知识库通过 RAG 灌入

#### Phase 2: 深度功能

**目标**: 实现商品推荐、优惠计算、售后处理等深度功能

4. **商品推荐子智能体**
   - 基于 RAG 的商品知识检索
   - 属性模糊匹配、场景化推荐
   - 库存实时校验

5. **优惠活动工具**
   - 优惠券查询和推荐
   - 凑单计算（满减最优方案）
   - 价保申请

6. **售后服务子智能体**
   - 智能挽单（退款前预干预）
   - 退货退款流程引导
   - 售后工单管理

7. **客户画像**
   - 用户购买历史分析（RFM）
   - VIP 等级识别
   - 情感分析

#### Phase 3: 高级能力

8. **TMC 实时消息**
   - WebSocket 接入 TMC 服务
   - 订单/物流事件实时推送
   - 主动通知（发货提醒、签收提醒）

9. **人机协作**
   - 智能转接（情绪检测 + 意图识别）
   - 上下文无缝传递
   - AI 座席助手模式

10. **大小模型协同**
    - 高频标准化问题走小模型（毫秒级响应）
    - 复杂咨询走大模型（深度推理）
    - 降低 API 调用成本

---

### 技术风险与应对

| 风险 | 影响 | 应对策略 |
|------|------|---------|
| 淘宝 API 权限限制 | 部分营销 API 需聚石塔环境 | MVP 阶段避开受限 API，或部署在聚石塔 |
| 幻觉导致错误报价 | 直接收入损失 | 所有价格/库存信息必须来自 API 实时查询，禁止 LLM 编造 |
| 大促高并发 | 服务不可用 | API 密钥池 + 异步处理 + 弹性扩展 |
| 合规风险 | 监管处罚 | AI 标识 + 数据脱敏 + Prompt 约束 |
| 店小蜜竞争 | 市场差异化难 | 聚焦跨平台+深度定制能力，店小蜜弱于的领域 |

---

### 关键技术选型建议

| 组件 | 建议 | 理由 |
|------|------|------|
| LLM 主模型 | Qwen（通义千问） | 与淘宝生态天然契合，电商场景优化最深 |
| LLM 备选 | ZhipuAI（已接入） | Function Calling 支持，中文理解优秀 |
| 向量数据库 | pgvector | 与现有 PostgreSQL 一致，运维简单 |
| 消息通道 | 千牛 WebHook + TMC WebSocket | 官方推荐方案 |
| RAG 策略 | 混合检索（向量+BM25+元数据） | 电商需精确匹配和语义匹配 |
| 认证 | OAuth 2.0 + 商家授权 | 淘宝标准流程 |

---

## 下一步行动

1. **确认产品定位**: 是做千牛 ISV 插件（对标晓多），还是做独立 SaaS（对标智齿），还是做商家自研工具
2. **申请淘宝开放平台开发者账号**: 获取 AppKey，申请 API 权限
3. **设计 SUBAGENT.md**: 基于功能域划分子智能体（商品、订单、售后、推荐等）
4. **搭建 MVP**: 千牛通道 + 基础 API 工具 + 电商知识 Skill
5. **找到试点商家**: 用真实数据和场景验证效果
