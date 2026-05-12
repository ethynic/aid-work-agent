# 淘宝开放平台 API 调研报告 — 智能客服场景

> 调研日期: 2026-05-11 | 目标: 调研构建淘宝商家 AI 客服助手所需的相关 API

---

## 一、平台概览

淘宝开放平台（TOP, Taobao Open Platform）提供超过 **10,000 个 API**，日均调用量超百亿次。千牛（Qianniu）是淘宝/天猫商家的工作台客户端，集成了旺旺客服、订单管理、商品管理等功能。构建 AI 客服助手涉及三个层面的 API：

| 层面 | 平台/产品 | 核心用途 |
|------|----------|----------|
| 业务数据 API | 淘宝开放平台 (TOP) | 商品、订单、物流、退款、评价、营销等数据查询 |
| 消息通道 | TMC 消息服务 + 千牛开放 | 实时接收买家消息、发送自动回复 |
| AI 能力 | 阿里云云小蜜 / 通义点金 | 意图识别、知识库管理、多轮对话 |

**官方文档入口**：
- 淘宝开放平台: https://open.taobao.com/api.htm
- 千牛基础服务开放: https://open.taobao.com/doc/category_list.htm?id=101078
- ISV 三方对接文档: https://open.taobao.com/doc.htm?docId=121857&docType=1

---

## 二、业务数据 API — 按客服场景分类

### 2.1 交易/订单接口 (Trade API)

客服最常用的接口，用于查询买家订单状态和详情。

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.trades.sold.get` | 批量查询已卖出交易订单 | "帮我查一下最近有哪些订单" |
| `taobao.trade.fullinfo.get` | 获取单笔交易完整详情 | "订单 123456 的详情是什么" |
| `taobao.trades.sold.increment.get` | 增量获取已卖出交易 | 按时间范围同步新订单 |

**使用流程**: 先 `taobao.trades.sold.get` 批量获取列表，再 `taobao.trade.fullinfo.get` 获取单笔详情。

**注意**: 消费者敏感信息（收件人姓名、手机、地址）为脱敏数据。

文档: https://open.taobao.com/api.htm?docId=54&docType=2

### 2.2 物流接口 (Logistics API)

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.logistics.orders.detail.get` | 批量查询物流订单详情 | "我的快递到哪了" |
| `taobao.logistics.online.send` | 在线发货（支持货到付款） | "帮我发货" |
| `taobao.logistics.fulfilorder.search` | 物流履约单据查询 | 物流状态追踪 |

文档: https://open.taobao.com/api.htm?scopeId=12141

### 2.3 退款接口 (Refund API)

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.refunds.receive.get` | 查询卖家收到的退款列表 | "最近有哪些退款申请" |
| `taobao.refund.get` | 获取单笔退款详情 | "退款单详情" |
| `taobao.refund.status.get` | 查询订单退款状态 | "这个订单有没有退款" |
| `taobao.refunds.apply.get` | 查询买家申请的退款列表 | 买家视角查退款 |
| `taobao.rp.refund.review` | 审核退款单（仅天猫） | "帮我同意退款" |

文档: https://open.taobao.com/api.htm?scopeId=11527

### 2.4 商品接口 (Item/Product API)

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.items.onsale.get` | 获取出售中的商品列表 | "这个商品还有吗" |
| `taobao.item.seller.get` | 获取单个商品详情 | "商品信息是什么" |
| `taobao.inventory.query` | 查询商品库存信息 | "还有库存吗" |
| `taobao.inventory.mode.query` | 查询库存模式 | 库存策略查询 |
| `tmall.inventory.query.forstore` | 新版库存查询（推荐） | 天猫店铺库存 |

**注意**: 淘宝 API 通常不直接提供实时库存数据（涉及商业机密），但商家自有商品可通过授权接口获取。

文档: https://open.taobao.com/api.htm?scopeId=12138

### 2.5 评价接口 (Trade Rate API)

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.traderate.get` | 查询评价详情 | "这个买家给了什么评价" |
| `taobao.traderate.add` | 新增单个评价 | 卖家回评 |
| `taobao.traderate.list.add` | 批量新增评价 | 多子订单批量回评 |
| `taobao.item.reviews.get` | 获取商品用户评价 | "这个商品的评价怎么样" |

文档: https://open.taobao.com/api.htm?docId=55&docType=2

### 2.6 营销/优惠券接口 (Marketing/Promotion API)

| API 名称 | 功能 | 客服场景 |
|----------|------|----------|
| `taobao.promotion.coupon.apply` | 优惠券领取 | "给我一张优惠券" |
| `taobao.promotion.coupon.send` | 店铺优惠券发放（限聚石塔） | 批量发券 |
| `marketing.coupon.createactivity` | 创建优惠券活动 | 创建促销活动 |

**注意**: `taobao.promotion.coupon.send` 为增值 API，限聚石塔内调用，每次最多 100 张。

文档: https://open.taobao.com/api.htm?scopeId=11841

---

## 三、千牛消息与客服接口

### 3.1 千牛消息 API

千牛是商家端的核心客户端，AI 客服需要通过千牛消息通道收发买家消息。

| API 名称 | 功能 |
|----------|------|
| `taobao.qianniu.message.category.getlist` | 获取千牛用户消息类目列表（免费，需授权） |
| `taobao.jindoucloud.message.authorize.permit` | 卖家工作台用户订阅消息类型 |
| `taobao.jindoucloud.message.get` | 获取消息 |
| `taobao.qianniu.task.message.send` | 千牛任务消息发送 |
| `taobao.qianniu.tasks.get` | 获取千牛任务列表 |

文档: https://developer.alibaba.com/docs/api.htm?apiId=30679

### 3.2 千牛自动化机器人

千牛支持自动化机器人方案，通过 WebHook 方式接收和发送消息：

**接入流程**:
1. 在千牛开放平台订购机器人服务
2. 在内部群 → 群设置 → 机器人 → 添加【千牛自动化】
3. 获取 WebHook 地址和密钥
4. 通过回调事件监听买家消息，触发 API 自动回复

**WebHook 机制**:
- 千牛自动化机器人提供 WebHook 地址
- 买家消息通过回调事件推送
- 第三方服务处理后通过 API 返回回复内容

文档: https://open.alitrip.com/docs/doc.htm?treeId=759&articleId=121503&docType=1

### 3.3 千牛客服质检 API

| API 名称 | 功能 |
|----------|------|
| `taobao.message.kefuinspect.broadcastnotify` | 客服质检质培启用事件通道 |
| `taobao.message.kefuinspect.status` | 客服质培状态查询 |

文档: https://jaq-doc.alibaba.com/docs/api.htm?apiId=67685

---

## 四、TMC 消息服务 — 实时消息推送

TMC（Taobao Message Channel）是淘宝开放平台的核心消息推送服务，用于替代 API 轮询。

### 4.1 两种消息消费模式

| 模式 | 协议 | 特点 | 适用场景 |
|------|------|------|----------|
| **tmcClient 长连接** | WebSocket | 服务端主动推送，实时性高 | 客服消息实时接收（推荐） |
| **API 轮询** | HTTP | `taobao.tmc.messages.consume` 主动拉取 | 无法维持长连接的场景 |

**推荐使用 tmcClient 长连接方式**，实时性好，延迟低。

### 4.2 TMC 消息类型（客服相关）

| 消息类型 | 说明 |
|----------|------|
| `taobao_trade_TradeCreated` | 创建交易 |
| `taobao_trade_TradePayment` | 买家付款 |
| `taobao_trade_TradeSuccess` | 交易成功 |
| `taobao_trade_TradeClose` | 关闭交易 |
| `taobao_trade_TradeChanged` | 交易修改 |
| `taobao_refund_RefundCreated` | 退款创建 |
| `taobao_refund_RefundSuccess` | 退款成功 |
| `taobao_item_ItemAdd` | 商品新增 |
| `taobao_item_ItemUpdate` | 商品更新 |
| `taobao_logistics_Logistics` | 物流状态变更 |

完整消息类型列表: https://open.taobao.com/tmc.htm

### 4.3 TMC 接入步骤

1. 调用 `taobao.tmc.user.permit` 开通消息服务（可选只订阅部分消息类型）
2. 使用 TMC SDK 建立 WebSocket 长连接
3. 实现消息监听回调，处理各类业务消息
4. SDK 自带心跳保活和断线重连机制

文档: https://open.taobao.com/doc.htm?docId=101663&docType=1

---

## 五、阿里云智能客服 API — 云小蜜

阿里云智能对话机器人（云小蜜/BeeBot）提供完整的智能客服 API 能力，可作为 AI 客服的后端引擎。

### 5.1 核心 API

| API 名称 | 功能 | 说明 |
|----------|------|------|
| `Chat` | 核心会话接口 | 根据机器人 ID 进行对话，自动管理上下文 |
| `BeginSession` | 获取欢迎语 | 新用户进入时的欢迎语 |
| `Associate` | FAQ 联想 | 根据用户 query 联想知识库中的 FAQ |
| `RecognizeIntention` | 意图识别 | 通义点金提供的意图识别 API |

### 5.2 知识库管理 API

| 功能模块 | 说明 |
|----------|------|
| FAQ 管理 | 知识库问答对管理（创建/查询/修改/删除） |
| 意图管理 | 创建/查询/删除意图，意图话术配置 |
| 对话工厂 | 多轮对话流程配置 |
| 业务空间 | 知识库隔离管理 |
| 发布管理 | 知识库版本发布 |

**知识库建议上限**: 2000 条 FAQ。

### 5.3 Chat 接口参数

```
POST /api/Chatbot/2022-04-08/Chat

请求参数:
- RobotCode: 机器人唯一标识
- SessionId: 会话ID（首次不传，后续使用返回值保持上下文）
- Message: 用户消息内容

响应参数:
- MessageId: 消息ID
- Text: 机器人回复文本
- IntentName: 命中的意图名称
- IntentSource: 意图识别来源
```

文档:
- API 门户: https://api.aliyun.com/document/Chatbot
- 全部 API 参考: https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-reference-tongyi/
- Chat 接口: https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-chatbot-2022-04-08-chat-tongyi

---

## 六、OAuth 2.0 授权流程

### 6.1 ISV 接入授权

淘宝开放平台采用 **OAuth 2.0 Authorization Code** 模式，商家需授权第三方应用访问其数据。

```
商家点击授权链接
  → 重定向到淘宝授权页面
  → 商家登录并同意授权
  → 淘宝回调 redirect_uri 并返回 authorization_code
  → ISV 服务端用 code 换取 access_token
  → 使用 access_token 调用 API
```

### 6.2 关键参数

| 参数 | 说明 |
|------|------|
| 授权 URL | `https://oauth.taobao.com/authorize` |
| Token URL | `https://oauth.taobao.com/token` |
| `client_id` | 应用的 AppKey |
| `client_secret` | 应用的 AppSecret |
| `redirect_uri` | 回调 URL（需与后台配置一致） |

### 6.3 Token 有效期

| Token | 有效期 | 用途 |
|-------|--------|------|
| `access_token` | 通常 1 天 | API 调用凭证 |
| `refresh_token` | 通常 30 天 | 刷新 access_token |
| `r1_expires_in` | 通常 1 天 | r1 级别 API 调用有效期 |
| `w1_expires_in` | 通常 1 天 | w1 级别 API 调用有效期 |

### 6.4 API 调用签名

所有 API 调用需要对请求参数做 **HMAC-MD5 签名**，签名密钥为 AppSecret。调用时将 `session` 参数设为 `access_token`。

```
正式环境: https://eco.taobao.com/router/rest
请求参数: method, app_key, session, sign, timestamp, format, v, ...
```

### 6.5 接入前置条件

1. 在淘宝开放平台注册开发者账号
2. 创建应用，获取 AppKey / AppSecret
3. 申请所需的 API 权限包
4. 部分接口需要在聚石塔（阿里云电商云）环境内调用

---

## 七、技术要求与限制

### 7.1 API 频率限制

| 限制维度 | 说明 |
|----------|------|
| API 级别限制 | 所有 ISV 应用访问同一 API 的总频率限制（按分钟/秒） |
| 应用级别限制 | 单个 appkey 的调用频率限制 |
| 大促调整 | 618、双 11 等大促期间会有降级和限流调整 |

文档: https://open.taobao.com/doc.htm?docId=101617&docType=1

### 7.2 收费模式

- 自 2017 年起，淘宝开放平台对所有 TOP 接口调用实施 **API 收费**
- 数据同步服务（TMC）也需付费
- 费用规则: https://open.alitrip.com/docs/doc.htm?docType=1&articleId=104559

### 7.3 数据脱敏

- 消费者敏感信息（收件人姓名、手机号、地址等）均为**脱敏数据**
- 开发者需遵守数据安全规范，不得尝试反向解密

### 7.4 消息格式

- API 请求支持 JSON 和 XML 格式（默认 XML，推荐 JSON）
- 响应格式通过 `format` 参数指定
- 编码: UTF-8

### 7.5 聚石塔环境限制

部分高价值 API（如 `taobao.promotion.coupon.send` 优惠券发放）限制只能在聚石塔（阿里云电商云）环境内调用，需要在阿里云上购买聚石塔服务。

---

## 八、AI 客服助手架构建议

### 8.1 推荐集成架构

```
买家消息
  │
  ▼
千牛/旺旺
  │
  ▼
TMC 消息服务 (WebSocket 长连接接收买家消息)
  │
  ▼
AI 客服 Agent (本项目 agent.py)
  │
  ├── 意图识别（自行实现 or 云小蜜 RecognizeIntention）
  ├── 调用业务 API（订单查询、物流查询、退款查询等）
  │     ├── taobao.trade.fullinfo.get (订单详情)
  │     ├── taobao.logistics.orders.detail.get (物流信息)
  │     ├── taobao.refund.get (退款详情)
  │     └── taobao.items.onsale.get (商品信息)
  ├── 知识库检索（商品FAQ、售后政策等）
  └── 生成回复
  │
  ▼
千牛消息发送 API (回复买家)
```

### 8.2 核心能力矩阵

| 能力 | 实现方式 | 涉及 API |
|------|----------|----------|
| 接收买家消息 | TMC WebSocket 长连接 | `taobao.tmc.user.permit` + SDK |
| 发送自动回复 | 千牛消息发送 | `taobao.qianniu.task.message.send` |
| 订单查询 | TOP 交易 API | `taobao.trade.fullinfo.get` |
| 物流查询 | TOP 物流 API | `taobao.logistics.orders.detail.get` |
| 退款查询 | TOP 退款 API | `taobao.refund.get` |
| 商品查询 | TOP 商品 API | `taobao.item.seller.get` |
| 库存查询 | TOP 库存 API | `taobao.inventory.query` |
| 发放优惠券 | TOP 营销 API（限聚石塔） | `taobao.promotion.coupon.send` |
| 退款审核 | TOP 退款 API | `taobao.rp.refund.review` |
| 实时事件通知 | TMC 消息订阅 | 各类 Trade/Refund/Logistics 消息 |
| 意图识别 | 云小蜜 API 或自行实现 | `RecognizeIntention` / `Chat` |
| 知识库管理 | 云小蜜知识库 API 或本地知识库 | `Associate` / FAQ 管理 API |

### 8.3 第三方方案参考

已有第三方工具实现了"ChatGPT + 私有知识库对接千牛消息"的方案，支持淘宝、拼多多、抖音、京东等 20+ 平台的 AI 自动回复。参考: https://blog.csdn.net/ZhiMaoYiDeHuaiRen/article/details/136061190

---

## 九、开发注意事项

1. **Token 管理**: access_token 有效期短（约1天），需实现自动刷新机制（refresh_token），避免授权过期
2. **签名安全**: AppSecret 绝不能暴露在前端，签名计算在服务端完成
3. **频率控制**: 实现本地速率限制，避免触发平台 API 频率限制
4. **消息可靠性**: TMC 消息有重试机制，但需要做好幂等处理
5. **大促准备**: 618/双 11 期间 API 会有降级，需提前关注平台公告
6. **聚石塔限制**: 部分高价值 API 必须在聚石塔环境调用，需评估是否需要购买
7. **数据合规**: 用户敏感数据已脱敏，不可尝试还原

---

## 十、参考链接汇总

| 资源 | 链接 |
|------|------|
| 淘宝开放平台 API 文档 | https://open.taobao.com/api.htm |
| 千牛基础服务开放 | https://open.taobao.com/doc/category_list.htm?id=101078 |
| ISV 三方对接文档 | https://open.taobao.com/doc.htm?docId=121857&docType=1 |
| TMC 消息服务文档 | https://open.taobao.com/doc.htm?docId=101663&docType=1 |
| TMC 消息类型列表 | https://open.taobao.com/tmc.htm |
| API 调用方法详解 | https://open.taobao.com/doc.htm?docId=101617&docType=1 |
| 千牛自动化机器人方案 | https://open.alitrip.com/docs/doc.htm?treeId=759&articleId=121503&docType=1 |
| 千牛消息类目 API | https://developer.alibaba.com/docs/api.htm?apiId=30679 |
| 千牛业务 API | https://jaq-doc.alibaba.com/docs/api.htm?apiId=67685 |
| 阿里云云小蜜产品页 | https://www.aliyun.com/product/beebot |
| 云小蜜 Chat API | https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-chatbot-2022-04-08-chat-tongyi |
| 云小蜜全部 API 参考 | https://help.aliyun.com/zh/beebot/intelligent-dialogue-robot-tongyi-version/api-reference-tongyi/ |
| 云小蜜 OpenAPI 门户 | https://api.aliyun.com/document/Chatbot |
| 通义点金意图识别 API | https://help.aliyun.com/zh/model-studio/api-dianjin-2024-06-28-recognizeintention |
| 商家自主研发指南 | https://open.taobao.com/doc.htm?docId=120869&docType=1 |
| 技术服务费规则 | https://open.alitrip.com/docs/doc.htm?docType=1&articleId=104559 |
| 2026 淘宝 API 接入指南 | https://developer.aliyun.com/article/1724447 |
