# 淘宝客服智能体 — 淘宝开放平台 API 与集成技术研究

> 调研日期: 2026-05-11 | 版本: v1.0

## 1. 淘宝开放平台 (TOP) 概述

淘宝开放平台（Taobao Open Platform, TOP）是第三方接入淘宝生态的官方通道。AI 客服产品通过 TOP API 获取商品、订单、物流、退款等数据，并通过千牛消息接口实现客服对话。

---

## 2. 核心 API 分类

### 2.1 订单/交易 API

| API 名称 | 功能 | 关键字段 |
|----------|------|---------|
| `taobao.trades.sold.get` | 批量查询已卖出交易 | 订单列表、分页 |
| `taobao.trade.fullinfo.get` | 单笔订单详情 | 订单状态、金额、商品列表、收货地址、物流单号 |
| `taobao.trades.sold.increment.get` | 增量同步订单变更 | 按时间范围查询变更的订单 |

**订单状态流转**:
```
待付款 → 已付款/未发货 → 已发货/待签收 → 已签收/已完成
  ↓          ↓               ↓
关闭    修改地址/合并发货    物流异常处理
```

### 2.2 物流 API

| API 名称 | 功能 |
|----------|------|
| `taobao.logistics.orders.detail.get` | 查询物流订单详情和追踪信息 |
| `taobao.logistics.online.send` | 在线发货操作 |
| `taobao.logistics.fulfilorder.search` | 物流履约单搜索 |

### 2.3 退款 API

| API 名称 | 功能 | 说明 |
|----------|------|------|
| `taobao.refund.get` | 单笔退款详情 | 退款原因、金额、状态 |
| `taobao.refunds.receive.get` | 批量查询收到的退款 | 卖家视角退款列表 |
| `taobao.refund.status.get` | 退款状态查询 | 实时退款进度 |
| `taobao.rp.refund.review` | 退款审核（天猫） | 仅限天猫商家 |

### 2.4 商品 API

| API 名称 | 功能 |
|----------|------|
| `taobao.items.onsale.get` | 获取在售商品列表 |
| `taobao.item.seller.get` | 商品详情（卖家视角） |
| `taobao.inventory.query` | 库存查询 |

### 2.5 评价 API

| API 名称 | 功能 |
|----------|------|
| `taobao.traderate.get` | 获取评价详情 |
| `taobao.item.reviews.get` | 商品评价列表 |

### 2.6 营销 API（受限）

| API 名称 | 功能 | 限制 |
|----------|------|------|
| `taobao.promotion.coupon.apply` | 优惠券申请 | 仅聚石塔环境 |
| `taobao.promotion.coupon.send` | 优惠券发放 | 仅聚石塔环境 |

> **注意**: 营销类 API 大多限制在聚石塔（阿里云电商云）环境内调用，需要将服务部署在聚石塔中才能使用。

---

## 3. 千牛集成方案

千牛是淘宝/天猫商家的统一工作台，AI 客服通过千牛接入商家聊天。

### 3.1 千牛消息 API

| API 名称 | 功能 |
|----------|------|
| `taobao.qianniu.message.category.getlist` | 获取消息分类列表 |
| `taobao.qianniu.task.message.send` | 发送客服消息 |
| `taobao.jindoucloud.message.get` | 获取消息记录 |

### 3.2 千牛自动化机器人

千牛支持配置自动化机器人（WebHook 模式）：

- **消息接收**: 通过 WebHook 接收买家消息推送
- **消息发送**: 调用 API 回复买家消息
- **人机切换**: 设置转人工触发条件（关键词、意图、情绪）
- **文档**: https://open.alitrip.com/docs/doc.htm?treeId=759&articleId=121503&docType=1

### 3.3 ISV 客服面板插件

第三方 ISV 的标准接入路径：

- **接入类目**: 电商管理 > 客服工具 > 客服面板插件
- **功能范围**: 用户信息、订单管理、商品管理、知识库、工单系统
- **分发渠道**: 千牛分发、服务市场分发
- **计费模式**: 按周期计费、按效果计费
- **流程**: 提交审核方案 → 审核通过 → 开发 → 上架

---

## 4. TMC 消息服务（实时推送）

TMC (Taobao Message Center) 是淘宝的实时消息推送服务，用于接收订单、退款、物流等事件通知。

### 4.1 两种接入模式

| 模式 | 协议 | 适用场景 | 说明 |
|------|------|---------|------|
| **WebSocket 长连接** | WebSocket | 推荐，实时性高 | 实时推送，内置心跳和自动重连 |
| **API 轮询** | HTTP | 备选方案 | `taobao.tmc.messages.consume` |

### 4.2 接入流程

```
1. 调用 taobao.tmc.user.permit 订阅消息主题
2. 使用 TMC SDK 建立 WebSocket 连接
3. 接收实时消息推送（订单变更、退款事件、物流更新等）
4. 处理消息并确认（ACK）
```

### 4.3 支持的消息主题

- 交易事件（创建、付款、发货、签收、关闭）
- 退款事件（申请、同意、拒绝、完成）
- 物流事件（发货、签收、异常）
- 商品变更事件（上下架、价格变更、库存变更）

### 4.4 可靠性保证

- 内置心跳检测
- 自动重连机制
- 消息重试（未确认的消息会重新推送）
- 消息有序性保证

---

## 5. 阿里云云小蜜 (BeeBot) AI 能力

云小蜜提供开箱即用的 AI 对话能力，可作为 AI 客服的 NLU 底座。

### 5.1 核心 API

| API | 功能 | 说明 |
|-----|------|------|
| `Chat` | 对话 API | 自动管理会话上下文，支持多轮对话 |
| `RecognizeIntention` | 意图识别 | 识别用户意图分类 |
| `Associate` | FAQ 联想推荐 | 基于知识库的智能推荐 |

### 5.2 知识库管理

- FAQ 增删改查 API
- 意图管理 API
- 对话工厂（多轮对话编排）
- 推荐限制: 每个知识库最多 2000 条 FAQ

### 5.3 适用场景

- 快速搭建 FAQ 型客服
- 不需要深度定制 AI 行为的场景
- 作为大模型 Agent 的前置意图路由

---

## 6. 认证与授权

### 6.1 OAuth 2.0 流程

```
商家授权 → 获取 authorization_code
         → 用 code 换取 access_token
         → 用 access_token 调用 API
         → access_token 过期后用 refresh_token 刷新
```

- `access_token` 有效期约 1 天
- `refresh_token` 有效期约 30 天
- 所有 API 调用需要 HMAC-MD5 签名

### 6.2 商家授权流程

1. 第三方应用在 TOP 注册，获取 AppKey 和 AppSecret
2. 商家通过 OAuth 页面授权应用访问其数据
3. 应用获取 access_token，可调用商家授权范围内的 API
4. 消费者敏感信息以脱敏 + OAID 形式返回

---

## 7. 技术限制与注意事项

### 7.1 API 调用限制

- **平台级限制**: 所有 ISV 应用合计的调用上限
- **应用级限制**: 单个 AppKey 的调用频率限制
- **计费**: 自 2017 年起 API 调用收费
- **大促调整**: 618、双 11 期间限制会动态调整

### 7.2 数据安全

- 消费者 PII 数据脱敏处理
- 部分数据使用 OAID 替代真实 ID
- 营销 API 限制在聚石塔环境内

### 7.3 MCP 服务

淘宝开放平台已支持 MCP (Model Context Protocol) 服务，使 AI 应用（大模型）能更高效地调用淘宝 API。这为 AI Agent 直接调用淘宝 API 提供了更便捷的通道。

---

## 8. 集成方案建议

### 8.1 推荐架构

```
┌──────────────────────────────────────────────┐
│                  AI 客服智能体                  │
│  ┌─────────┐  ┌──────────┐  ┌─────────────┐ │
│  │ LLM Agent│  │ RAG 知识库 │  │  意图路由    │ │
│  └────┬─────┘  └────┬─────┘  └──────┬──────┘ │
│       └──────────────┼───────────────┘        │
│                      │                        │
│              ┌───────┴───────┐                │
│              │  Tool Layer    │                │
│              │  (Function     │                │
│              │   Calling)     │                │
│              └───────┬───────┘                │
└──────────────────────┼────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
   ┌────┴────┐  ┌─────┴─────┐  ┌────┴────┐
   │ 淘宝 TOP │  │  TMC 消息  │  │ 千牛    │
   │ REST API │  │  WebSocket │  │ 机器人   │
   └─────────┘  └───────────┘  └─────────┘
```

### 8.2 技术选型建议

| 组件 | 推荐方案 | 理由 |
|------|---------|------|
| 消息通道 | 千牛自动化机器人 + TMC WebSocket | 实时消息推送，官方推荐 |
| 数据获取 | TOP REST API + MCP 服务 | 标准化接口，MCP 简化 Agent 调用 |
| AI 底座 | 自建 LLM Agent + RAG | 比云小蜜更灵活，可控性更强 |
| 认证 | OAuth 2.0 + 商家授权 | 标准流程 |
| 部署 | 聚石塔（如需营销 API）或自有服务器 | 按需选择 |

---

## 参考来源

- [淘宝开放平台 API 文档](https://open.taobao.com/api.htm)
- [千牛基础服务文档](https://open.taobao.com/doc/category_list.htm?id=101078)
- [ISV 集成指南](https://open.taobao.com/doc.htm?docId=121857&docType=1)
- [TMC 消息服务](https://open.taobao.com/doc.htm?docId=101663&docType=1)
- [千牛自动化机器人](https://open.alitrip.com/docs/doc.htm?treeId=759&articleId=121503&docType=1)
- [云小蜜 Chat API](https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-chatbot-2022-04-08-chat-tongyi)
- [云小蜜完整 API 参考](https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-reference-tongyi/)
- [2026 淘宝 API 指南](https://developer.aliyun.com/article/1724447)
- [API 调用频率限制](https://open.taobao.com/doc.htm?docId=101617&docType=1)
- [商家自研指南](https://open.taobao.com/doc.htm?docId=120869&docType=1)
