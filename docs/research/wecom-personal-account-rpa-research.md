# 企业微信个人账号 RPA 接入调研报告

> 关联设计：[wecom-personal-rpa-design.md](../system/wecom-personal-rpa-design.md) / 开发计划：[plan-wecom-personal-rpa.md](../../plans/plan-wecom-personal-rpa.md)
> 调研日期：2026-06-16
> 调研范围：让 AI agent 通过接管"真实的个人企业微信账号"（真人员工账号）收发消息的实现方案
> 调研方法：联网搜索 GitHub、技术博客、官方文档、行业报告（2024-2026 一手资料）

---

## 1. 背景与目标

### 1.1 需求描述

aid-work-agent 当前已支持企业微信机器人、应用消息推送、微信客服（WeChat Customer Service）等官方渠道。本次新需求是：**让 AI agent 接管一个真实的"个人企业微信账号"**（即一个真人员工账号，登录在企业微信 PC 客户端或手机端），实现：

- 自动接收该账号收到的所有消息（单聊、群聊、外部联系人）
- 自动以该账号身份发送消息（文本、图片、文件、@提醒）
- 切换会话、读取群成员、自动通过好友申请等管理操作

### 1.2 核心痛点

企业微信官方**明确不提供**任何 API 来控制"个人企业微信账号"的收发消息。官方开放接口只覆盖：
- 自建应用/第三方应用（推送应用消息、调用工作台）
- 群机器人 Webhook（仅群内推送，无法读消息）
- 微信客服（客服账号场景，非个人账号）
- 会话存档（仅归档，不能发消息，且需 RSA 解密、企业开通）

因此业界普遍采用 **RPA / Hook / 协议模拟** 等"非官方"手段来实现这一目标。本报告目的是回答：

> **除了 RPA 之外，还有没有别的路径？如果只能走 RPA，怎么做最稳？风险在哪？**

---

## 2. 实现路径全景对比

### 2.1 候选方案清单

| # | 方案 | 一句话原理 |
|---|------|-----------|
| A | **Windows Hook / DLL 注入** | 把 DLL 注入到 `WXWork.exe` 进程，hook 收发消息的内部 Call |
| B | **PC 端 RPA（UI 自动化）** | 用 UIA / pywinauto / 图像识别模拟真人点击企业微信 PC 客户端 |
| C | **安卓无障碍 RPA** | 在安卓手机上用 AccessibilityService 监听并操控企业微信 App |
| D | **协议模拟（iPad / Web Cookie）** | 逆向 mmtls 协议或抓网页版 Cookie 直接调内部 HTTP 接口 |
| E | **网络层 MITM** | 中间人代理拦截客户端↔服务器流量并伪造请求 |
| F | **官方会话存档 SDK** | 用企业微信官方会话存档 C/Java SDK 拉取会话（只读） |
| G | **第三方 SCRM SaaS（白牌）** | 接入 WorkTool / WorkBot / FlowBot 等已封装好的 RPA/Hook 服务 |

### 2.2 对比矩阵

| 方案 | 可行性 | 技术难度 | 稳定性 | 封号风险 | 维护成本 | 官方打击 | 综合推荐度 |
|------|--------|---------|--------|---------|---------|---------|----------|
| A. Windows Hook/DLL 注入 | ✅ 高 | ⭐⭐⭐⭐⭐ 极高 | ⭐⭐ 受版本影响大 | 🔴 高 | 🔴 极高（每次版本升级需重新逆向） | 🔴 严厉（违反用户协议、可能触刑法 285 条） | ❌ 不推荐 |
| B. PC 端 RPA（UIA） | ✅ 中（新版 DirectUI 难抓） | ⭐⭐⭐ 中 | ⭐⭐⭐ 受 UI 改版影响 | 🟡 中（行为模拟可被识别） | 🟡 中 | 🟡 中（腾讯有"远控"识别） | ⚠️ 备选 |
| C. 安卓无障碍 RPA | ✅ 高（最成熟） | ⭐⭐ 低 | ⭐⭐⭐⭐ 较稳 | 🟢 较低（官方明文支持无障碍） | 🟢 低 | 🟢 弱（工信部要求 APP 支持无障碍） | ✅ **首选** |
| D. iPad / Web Cookie 协议 | ⚠️ 部分（功能受限） | ⭐⭐⭐⭐ 高 | ⭐⭐ Cookie 24h 失效 | 🔴 高 | 🔴 高 | 🔴 严厉 | ❌ 不推荐 |
| E. mmtls MITM | ❌ 极难 | ⭐⭐⭐⭐⭐ 极高 | ⭐ 几乎不可行 | 🔴 极高 | 🔴 极高 | 🔴 极严厉 | ❌ 不可行 |
| F. 官方会话存档 SDK | ✅ 高（仅读取） | ⭐ 低 | ⭐⭐⭐⭐⭐ 极稳 | 🟢 零 | 🟢 低 | 🟢 无 | ✅ **只读场景首选** |
| G. 第三方 SaaS（WorkTool/FlowBot） | ✅ 高 | ⭐ 极低（调 API） | ⭐⭐⭐⭐ 看供应商 | 🟡 看供应商实现 | 🟡 中（按账号付费） | 🟡 同方案 B/C | ✅ 快速验证首选 |

### 2.3 关键判断

1. **协议层方案（D/E）几乎不可行**：企业微信与个人微信共用 mmtls 加密层（基于 TLS 1.3 草案 + 证书 Pinning + 自签名），Citizen Lab 多伦多大学的逆向报告已证实外层加密后还有多层业务加密。Frida Hook 是唯一能截到明文的方法，但这意味着已经在做 Hook 而不是纯协议层。社区里号称的"iPad 协议"实际上是抓 **iPad Safari 网页版 Cookie（`wwrtx.sid`）** 后调用受限的 HTTP 接口，单账号 30 次/分钟，且只能发文本，**根本不能完整接管账号**。
2. **Windows Hook 路线技术上可行但合规性极差**：这是商业 SCRM 厂商的"内存偏移流派"底层实现（华为开发者博客原文："是目前商业化接口的主流底层实现"），但腾讯已多次起诉类似工具（如诉微源码、软媒不正当竞争案，赔偿数百万元），并可能触及《刑法》第 285 条"非法控制计算机信息系统"。aid-work-agent 是企业级产品，**不能走这条路**。
3. **真正合规稳健的只有两条路**：C（安卓无障碍 RPA）+ F（官方会话存档只读），B（PC 端 RPA）作为补充。

---

## 3. 推荐方案：安卓无障碍 RPA + 官方会话存档（双轨）

### 3.1 推荐结论

> **主路径（双向消息流）：安卓无障碍 RPA**
> 工信部明文要求所有 APP 必须进行无障碍改造，这是"政府和官方支持的唯一自动化方案"（WorkTool 原文）。在企业微信手机 App 上挂一台安卓设备（真机或云手机），通过 Android AccessibilityService 监听通知/界面节点变化，模拟点击/输入完成收发。
>
> **辅路径（消息归档/审计）：官方会话存档 SDK**
> 企业管理员开通会话存档后，可通过官方 C/Java SDK 拉取该员工所有内部 + 外部联系人的会话记录（加密、需 RSA 解密），作为**消息对账与可靠性兜底**。即使 RPA 漏抓某条消息，存档也能补齐。

### 3.2 为什么不是 PC 端 RPA

- 企业微信 4.1+ PC 客户端采用 **DirectUI 自绘界面**（`WeWorkWindow`、`PerryShadowWnd` 等定制容器），**不走标准 Windows 控件体系**，不暴露 `Invoke` / `Value` / `TextPattern` 等标准 UI Automation Patterns，导致 pywinauto / uiautomation 抓不到控件树（参考 [yangyuexiong/wx_work_auto](https://github.com/yangyuexiong/wx_work_auto) 的 README 明确指出此问题）。
- 微信 4.1+ 进一步引入 **On-Demand UI Automation（按需暴露机制）**，默认只暴露 1-2 个控件，需要 Inspect/FlaUI 主动触发才能拿到完整树（参考[微信4.1.5 UI 树消失问题解析](https://zhuanlan.zhihu.com/p/1979934377757655640)）。
- 即使能抓到，PC 端模拟键鼠会被企业微信的"远控检测"识别——影刀社区有人反馈："RPA 模拟键盘鼠标输入点击为什么企业微信会出现远控提示"，因为 RPA 调用的底层接口与真实硬件输入存在差异。
- 总结：**PC 端 RPA 折腾成本高、易被检测、UI 改版即崩**。安卓无障碍路线绕开了这些问题。

### 3.3 为什么不是 Hook

- 法律风险：腾讯已有多起胜诉案例（如[深圳中院腾讯诉微源码案](https://sdcourt.gov.cn/dyzy/372897/372898/7244935/index.html)，索赔 500 万）。
- 维护成本：每次企业微信客户端升级都需要重新逆向、重新计算偏移地址。社区维护的 [wxhelper](https://github.com/ttttupup/wxhelper) 主要服务个人微信（PC 3.9.x），企业微信专用 hook 项目零散且 star 极少（[yangyuexiong/wx_work_auto](https://github.com/yangyuexiong/wx_work_auto) 仅 2 star）。
- 一旦被检测，**整个企业账号都可能被连坐封禁**（2024 年企业微信违规账号封禁量增长 47%，"个人账号连坐"是六大雷区之一）。

---

## 4. 推荐方案的技术细节

### 4.1 技术栈选型

#### 4.1.1 安卓端（消息收发执行体）

| 组件 | 选型 | 说明 |
|------|------|------|
| 设备形态 | **云手机 / 红米 NOTE 系列真机** | 推荐 1 台真实安卓设备（避免模拟器被识别），或阿里云/华为云手机 |
| 系统 | Android 9-13（不要 14+，兼容性最好） | WorkTool 自身兼容 4.1.8-4.1.38、5.0.0-5.0.8 |
| 自动化框架 | **Android AccessibilityService** | 官方 API，工信部明文支持 |
| App 抓取 | `uiautomator2`（Python）+ `AccessibilityService`（Java/Kotlin） | uiautomator2 用于调试期 dump UI 树，AccessibilityService 用于运行期事件监听 |
| 设备控制 | `adb` over WiFi / scrcpy | 远程调试与画面投射 |
| 参考实现 | **[gallonyin/worktool](https://github.com/gallonyin/worktool)**（Apache 2.0 开源，2.8.1 版本维护到 2023-11） | 直接 fork 二开，或部署官方打包版 |

#### 4.1.2 后端服务（agent 协调层）

| 组件 | 选型 | 说明 |
|------|------|------|
| 通信协议 | **HTTP / WebSocket** | WorkTool 已提供标准 HTTP API（[worktool.apifox.cn](https://worktool.apifox.cn/doc-850007)），可直接对接 |
| 消息中间件 | Redis Stream / RabbitMQ | 解耦安卓端采集与 agent 主循环 |
| 会话路由 | `external_user_id` ↔ `session_id` 映射表 | 用企业微信 external_userid 作为唯一键 |
| 消息归档 | 企业微信官方会话存档 SDK（C/Java） | 通过 `libWeWorkFinanceLog_C.so` 拉取 + RSA 解密 |

#### 4.1.3 与 aid-work-agent 的对接

新增一个 `src/channels/wecom_personal/` 渠道适配器，实现 `ChannelAdapter` 接口：

```python
class WecomPersonalChannel(ChannelAdapter):
    async def parse_message(self, raw: dict) -> Message:
        # 从 WorkTool Webhook 回调解析消息
        ...

    async def send_message(self, msg: Message) -> SendResult:
        # 调用 WorkTool HTTP API 发送
        ...
```

### 4.2 架构设计（Windows 工作机 + 后端服务）

> ⚠️ 注意：本方案的"工作机"是**安卓设备**（不是 Windows PC），因为推荐走安卓无障碍路线。如果是 PC 端 RPA 备选方案，则需要 Windows 工作机。

```
┌─────────────────────────────────────┐
│  aid-work-agent 主服务（Linux）       │
│  ┌───────────────────────────────┐  │
│  │ master_agent (src/core/agent) │  │
│  └───────────┬───────────────────┘  │
│              │                       │
│  ┌───────────▼───────────────────┐  │
│  │ WecomPersonalChannel Adapter  │  │
│  │ (src/channels/wecom_personal/)│  │
│  └───────────┬───────────────────┘  │
│              │ HTTP/Webhook          │
└──────────────┼──────────────────────┘
               │
               │ （跨网络，通过 HTTPS）
               │
┌──────────────┼──────────────────────┐
│  消息中转服务（公网 VPS）             │
│  ┌───────────▼───────────────────┐  │
│  │ WorkTool 任务调度平台           │  │
│  │ - 接收 agent 下行消息           │  │
│  │ - 通过 MQTT/长连接下发到设备    │  │
│  │ - 接收设备上行消息→回调 agent   │  │
│  └───────────┬───────────────────┘  │
└──────────────┼──────────────────────┘
               │
┌──────────────┼──────────────────────┐
│  安卓工作机（云手机 or 真机）         │
│  ┌───────────▼───────────────────┐  │
│  │ WorkTool APP（无障碍服务）     │  │
│  │ + 企业微信 App（登录员工账号）  │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

### 4.3 关键操作实现

#### 4.3.1 读取指定会话的新消息（监听消息列表）

- **方案 A（推荐）**：监听系统通知栏（`NotificationListenerService`），企业微信收到消息会推系统通知，包含发送人、内容预览。**实时性最好，无需切换 UI**。
- **方案 B（兜底）**：定时（建议 5-10 秒）通过 AccessibilityService 遍历消息列表节点（`com.tencent.wework` 包下的 `ListView` / `RecyclerView`），提取未读标识。
- **方案 C（最稳）**：开通企业微信官方会话存档，通过 SDK 拉取对账（每次拉取上限 1000 条，限频 4000 次/分钟）。

#### 4.3.2 切换到指定联系人/群聊的会话窗口

```java
// 伪代码：通过 AccessibilityService 模拟点击搜索
1. 点击【搜索】入口（resource-id: com.tencent.wework:id/...）
2. 输入联系人名称 / external_userid
3. 等待搜索结果列表渲染（200-500ms 随机）
4. 点击第一条匹配项
5. 等待会话窗口加载完成
```

**关键技巧**（防检测）：
- 每步之间加入 **200-800ms 随机延时**
- 避免连续高频操作，单账号消息处理建议 **> 3 秒/条**
- 使用 `dispatchGesture` 而非 `performAction`，更接近真人触摸

#### 4.3.3 在输入框输入文字并发送

```java
1. 找到输入框节点（EditText）
2. 通过 AccessibilityNodeInfo.ACTION_SET_TEXT 设置文本
   或使用粘贴板 + ACTION_PASTE（更接近真人）
3. 找到【发送】按钮并点击
```

**注意**：`ACTION_SET_TEXT` 在某些版本会被识别为非用户输入，建议优先用粘贴板方案。

#### 4.3.4 发送文件/图片

- 通过 WorkTool 已封装的 `/sendImage` `/sendFile` 接口
- 底层实现：先把文件推送到安卓设备 → 通过分享菜单或拖拽进入会话 → 点击发送

#### 4.3.5 读取群成员、@提醒

- 进入群会话 → 点击右上角【...】→ 滑动群成员列表，dump 节点
- @提醒：在输入框先输入 `@`，触发成员选择浮层，点击目标成员

### 4.4 多账号 / 多会话扩展

#### 4.4.1 多账号隔离方案（强烈推荐）

| 方案 | 隔离强度 | 风控风险 | 成本 |
|------|---------|---------|------|
| **每账号一台真机/云手机**（推荐） | ⭐⭐⭐⭐⭐ 设备指纹完全独立 | 🟢 最低 | 高（每账号 100-300 元/月云手机） |
| 安卓多开应用（双开助手） | ⭐⭐ 共享设备指纹 | 🔴 高（多开本身违规） | 低 |
| 安卓模拟器（夜神/雷电）多实例 | ⭐⭐⭐ 模拟器特征明显 | 🟡 中 | 中 |

**企业微信官方明确规定**：同一台设备多开企业微信或将多个账号聚合运营属于违规操作，可能导致账号被封禁。**因此一台设备一个账号是必须的**。

#### 4.4.2 多会话路由

每个外部联系人的 `external_userid` 是全局唯一的，作为 `session_id` 的派生键：

```python
# src/channels/wecom_personal/router.py
def route_message(msg: WecomMessage) -> str:
    """将企微消息路由到 agent session"""
    # external_userid 形如 wmABCDEFG123456789
    return f"wecom_personal:{msg.tenant_id}:{msg.external_userid}"
```

### 4.5 消息可靠性保障

UI 自动化天然存在漏抓 / 重复 / 乱序风险，必须建立**三重保障**：

1. **消息唯一键去重**：每条消息用 `msgid`（企微服务端下发）+ `timestamp`+ `from_userid` 组合 hash，在 Redis 中做幂等
2. **状态机对账**：每条消息经历 `received` → `processing` → `replied` 状态，每 1 分钟扫描 `received` 超 5 分钟未 `replied` 的消息，触发补偿
3. **官方会话存档兜底**：定时（如每 10 分钟）拉取会话存档，与 RPA 抓取的消息做差集，补齐遗漏

---

## 5. 工程化挑战与对策

### 5.1 防封策略

根据[微盛 2026 避坑指南](https://college.wshoto.com/a/311316.html)和[小蜜蜂 RPA 方案](https://zhuanlan.zhihu.com/p/1982025049721561974)，企业微信封号检测主要在三个层面：

| 检测层 | 检测点 | 对策 |
|--------|--------|------|
| **行为层** | 操作频率异常、固定间隔、瞬间粘贴 | 加入随机延时（200-2000ms）、模拟逐字输入、单日单账号发消息 < 100 条 |
| **客户端完整性层** | 进程被 hook、内存被修改、有注入 DLL、有 Root | ✅ 安卓无障碍方案天然规避：不修改 App、不 Root、不注入 |
| **网络层** | 异常 IP、多地登录、设备指纹异常 | 每账号绑定固定设备 + 固定 IP（云手机选同省节点） |

**绝对禁止**的操作（业内统称"灰产功能"）：
- 批量主动加好友（>20 人/天必封）
- 群发广告、刷屏
- 朋友圈定时群发外挂
- 自动点赞、评论

### 5.2 客户端版本更新应对

企业微信客户端每隔 1-3 个月发布新版本，UI 结构可能变化。应对策略：

1. **锁定客户端版本**：禁用企业微信自动更新（需 MDM 配置），固定到 WorkTool 兼容版本（如 4.1.38）
2. **CI 兼容性测试**：在测试环境部署企业微信 beta 版，每次跑消息收发冒烟测试
3. **节点配置外置**：把所有 UI 节点的 resource-id / xpath 抽到 YAML 配置文件，UI 变化时只改配置不改代码
4. **订阅 WorkTool 更新**：WorkTool 团队会跟进适配，每次更新日志会列出兼容的企微版本（如 v2.8.0 兼容企微 4.1.10）

### 5.3 部署与运维

| 资源 | 数量 | 说明 |
|------|------|------|
| 安卓云手机 | 按账号数 ×1 | 阿里云/华为云手机，约 100-300 元/月/台 |
| 公网 VPS（中转服务） | 1 台 | 2C4G 即可，跑 WorkTool 调度平台 |
| 监控告警 | 必须 | 监控：①设备在线率 ②企业微信 App 是否崩溃 ③消息处理时延 ④封号预警 |

**运维工具推荐**：
- `scrcpy` 用于实时查看设备画面，排查问题
- `uiautomator2 dump` 用于 dump UI 树，定位控件变化
- 接入企微官方"AI 群聊检测"功能（[微伴助手](https://weibanzhushou.com/blog/33042)提供）做风险预警

---

## 6. 合规与法律风险

### 6.1 风险等级评估

| 维度 | 风险等级 | 说明 |
|------|---------|------|
| **腾讯用户协议** | 🟡 中（无障碍方案）/ 🔴 高（Hook 方案） | 企业微信服务协议明确禁止"使用未经授权的自动化工具"。但工信部要求 APP 支持无障碍，给无障碍方案留了一定灰色空间 |
| **《反不正当竞争法》** | 🟡 中 | 如被认定为"干扰腾讯产品正常运行"，可能被起诉 |
| **《刑法》第 285 条** | 🟢 低（无障碍）/ 🔴 高（Hook） | Hook 注入可能构成"非法控制计算机信息系统罪"，无障碍方案不涉及 |
| **《个人信息保护法》** | 🔴 高 | 接管员工账号意味着读取所有联系人的聊天记录，需明确告知并取得授权 |
| **企业内部合规** | 🔴 高 | 员工有权拒绝账号被接管，需签署授权协议 |

### 6.2 腾讯维权案例参考

- [**腾讯诉微源码不正当竞争案**](https://sdcourt.gov.cn/dyzy/372897/372898/7244935/index.html)：索赔 500 万元，深圳中院判决腾讯胜诉
- [**腾讯诉软媒案**](http://yxcpws.court.gov.cn/wspx/hundred/detail?oid=ab718a73-229e-11ec-874a-286ed488c78e)：最高法优秀裁判文书，被告被判多平台道歉 30 天
- 2024 年企业微信违规账号封禁量增长 47%，"个人账号连坐"是重点打击对象

### 6.3 风险缓解策略

1. **首选无障碍方案**，明确不用 Hook/注入/协议逆向
2. **取得书面授权**：被接管的员工必须签署知情同意书，明确告知其所有消息将被 AI 处理
3. **数据最小化**：只把必要字段（发送人、内容）传给 agent，不存储原始聊天记录
4. **明确告知对话方**：建议在自动回复首条消息时加一句"我是 AI 助手"，避免对方误认为是真人
5. **购买商业保险**：部分 SCRM 服务商提供"封号包赔"保险
6. **法务审查**：上线前由法务审查用户协议、隐私政策

---

## 7. 竞品技术方案分析

### 7.1 主流 SCRM 厂商

| 厂商 | 主路径 | 个人号接管？ | 备注 |
|------|--------|------------|------|
| **尘锋 SCRM** | 官方 API + 会话存档 | 否（合规路线） | 适合中大型企业，不做"接管个人号" |
| **微伴助手** | 官方 API + 会话存档 + AI 风控 | 否 | 主打会话存档质检，合规性极强 |
| **探马 SCRM** | 官方 API | 否 | 同上 |
| **句子互动** | **RPA 技术 + 国密认证** | 是 | 业内为数不多公开宣传 RPA 的厂商 |
| **WorkTool**（开源） | **安卓无障碍** | 是 | 开源，Apache 2.0 |
| **WorkBot / FlowBot**（商业） | 安卓无障碍 + 私有 API 封装 | 是 | 主打"零封号" |
| **集简云** | 安卓无障碍 | 是 | 3000 元/账号/年（1-9 账号） |
| **LinkAI** | 自研协议（疑似 Hook） | 是 | 7980 元 + 托管费 5000/账号/年 |
| **WeChatFerry**（开源） | **PC Hook** | 仅个微 | 5.7k star，但**不支持企业微信** |

### 7.2 关键判断

- **正规 SCRM 大厂**（尘锋、微伴、探马）：**坚决不走个人号接管路线**，只用官方 API + 会话存档。原因很简单——拿融资的公司不能碰违法业务。
- **私域运营厂商**（句子互动、WorkTool、FlowBot）：**走 RPA 路线**，主打"模拟人工、不破坏客户端"。这是aid-work-agent 应该参照的群体。
- **灰色厂商**（LinkAI、部分 iPad 协议厂商）：**走 Hook/协议路线**，封号率高、法律风险大，**不要参照**。

### 7.3 句子互动的"国密认证"启示

句子互动是业内第一个通过**国密安全合规认证**的企微 SCRM。这意味着 RPA 方案在中国是可以做合规认证的，但前提是：
- 不修改客户端
- 不破解协议
- 数据全程加密（国密 SM2/SM4）
- 完善的审计日志

aid-work-agent 若要长期演进，建议参照此路线图。

---

## 8. 开源项目参考

| 项目 | URL | Star | 活跃度 | 技术路线 | 是否可用 |
|------|-----|------|--------|---------|---------|
| **gallonyin/worktool** | [github.com/gallonyin/worktool](https://github.com/gallonyin/worktool) | ~1.5k | 维护到 2026-05 | 安卓无障碍 | ✅ **直接可用**（首选） |
| **xlrpa/FlowBot** | [github.com/xlrpa/FlowBot](https://github.com/xlrpa/FlowBot) | 商业 | 活跃 | RPA + API | ✅ 商用付费 |
| **yangyuexiong/wx_work_auto** | [github.com/yangyuexiong/wx_work_auto](https://github.com/yangyuexiong/wx_work_auto) | 2 | 低（3 commits） | PC uiautomation + pyautogui | ⚠️ 仅作参考，DirectUI 抓取困难 |
| **yihleego/robotic-process-automation** | [github.com/yihleego/robotic-process-automation](https://github.com/yihleego/robotic-process-automation) | ~200 | 中 | RPA 客户端/服务端架构 | ⚠️ 通用框架，需二开 |
| **LeoMusk/wechat-rpa-bot-skill** | [github.com/LeoMusk/wechat-rpa-bot-skill](https://github.com/LeoMusk/wechat-rpa-bot-skill) | 新 | 活跃 | 纯 RPA（模拟键鼠） | ✅ 思路可参考，定位为 AI agent skill |
| **licich0821/WeChatFerry** | [github.com/lich0821/WeChatFerry](https://github.com/lich0821/WeChatFerry) | 5.7k | 活跃 | **PC Hook** | ❌ 仅支持个人微信，不支持企微，且法律风险大 |
| **ttttupup/wxhelper** | [github.com/ttttupup/wxhelper](https://github.com/ttttupup/wxhelper) | ~5k | 活跃 | **PC Hook + HTTP API** | ❌ 仅个微 |
| **hanson/vbot** | [github.com/hanson/vbot](https://github.com/hanson/vbot) | ~2k | 低 | iPad 协议 | ❌ 已被腾讯告，慎用 |
| **Devo919/Gewechat** | [github.com/Devo919/Gewechat](https://github.com/Devo919/Gewechat) | 2.5k | 中 | 个微 Pad 协议（免费） | ❌ 仅个微 |
| **RockChinQ/LangBot** | [github.com/RockChinQ/LangBot](https://github.com/RockChinQ/LangBot) | 9.8k | 活跃 | 多协议（个微为主） | ⚠️ 企微支持弱 |

**结论**：**[WorkTool](https://github.com/gallonyin/worktool) 是唯一开源、活跃、专为企业微信设计、且采用合规无障碍路线的方案**，强烈建议作为起点。

---

## 9. 结论与建议

### 9.1 最终结论

> **方案可行，但必须走"安卓无障碍 RPA + 官方会话存档"双轨路线，严禁走 Hook / 协议逆向 / iPad 协议路线。**

### 9.2 推荐架构

```
aid-work-agent Linux 服务
  ↓ HTTPS
WorkTool 调度平台（公网 VPS）
  ↓ MQTT 长连接
安卓云手机（每账号 1 台）+ 企业微信 App
  ↑
官方会话存档 SDK（兜底对账）
```

### 9.3 风险摘要

| 风险 | 等级 | 缓解 |
|------|------|------|
| 法律合规 | 🟡 中 | 走无障碍路线，避免 Hook；取得员工授权；最小化数据 |
| 封号 | 🟡 中 | 模拟真人节奏，单账号 < 100 条/日，固定设备+IP |
| 客户端版本升级 | 🟡 中 | 锁定企微版本，节点配置外置，订阅 WorkTool 适配 |
| 消息漏抓 | 🟢 低 | 三重保障（msgid 去重 + 状态机对账 + 会话存档兜底） |
| 多账号扩展成本 | 🟡 中 | 每账号 100-300 元/月云手机成本 |

### 9.4 下一步行动建议

1. **Phase 1（1 周）：可行性验证**
   - 在一台真机上部署 [WorkTool](https://github.com/gallonyin/worktool)，登录一个测试企微账号
   - 通过 WorkTool HTTP API 实现：收消息→ echo 回复→发图→切群
   - 验证封号风险（连续运行 7 天，监控是否触发风控）

2. **Phase 2（2-3 周）：渠道适配器开发**
   - 在 `src/channels/wecom_personal/` 新增渠道适配器
   - 实现消息路由（external_userid → session_id）
   - 接入现有 `master_agent` 的 SSE 流

3. **Phase 3（1 周）：可靠性 + 监控**
   - 接入官方会话存档做对账
   - 完善监控告警（设备在线、消息时延、封号预警）
   - 法务审查用户协议与隐私政策

4. **Phase 4：灰度上线**
   - 选 1-2 个内部员工账号灰度
   - 观察 2-4 周稳定性后逐步扩大

### 9.5 强烈不建议的方向

- ❌ **不要自己写 Hook**：法律风险大，维护成本高，腾讯已有多起胜诉案例
- ❌ **不要走 iPad 协议 / Cookie 模拟**：功能受限（仅文本、30 次/分钟），且本质仍是协议逆向
- ❌ **不要尝试 mmtls MITM**：技术不可行（证书 Pinning），即使做出来也违法
- ❌ **不要用 PC 端 RPA 作为主路径**：DirectUI 抓取困难，远控检测容易触发
- ❌ **不要在 PC 上多开企业微信**：官方明文违规，会触发账号连坐封禁

---

## 10. 参考资料

### 10.1 官方文档
- [企业微信开发者中心 - 获取会话内容](https://developer.work.weixin.qq.com/document/path/91774)
- [企业微信会话存档 SDK 实践](https://feiyu.co/articles/Python%25E8%25B0%2583%25E7%2594%25A8C%25E5%25BA%2593%25E8%258E%25B7%25E5%258F%2596%25E4%25BC%2581%25E4%25B8%259A%25E5%25BE%25AE%25E4%25BF%25A1%25E4%25BC%259A%25E8%25AF%259D%25E5%25AD%2598%25E6%25A1%25A3/)
- [腾讯云 - 基于 TLS 1.3 的微信 mmtls 协议介绍](https://cloud.tencent.com/developer/article/1005518)

### 10.2 技术原理
- [华为开发者博客 - 企业微信外部群 RPA 技术架构解析](https://developer.huawei.com/consumer/cn/blog/topic/03215305177033083) ⭐ 核心
- [微信 4.1.5 UI 树"消失"问题解析](https://zhuanlan.zhihu.com/p/1979934377757655640)
- [微信 4.1 UI 树隐身之谜：按需暴露机制](https://blog.csdn.net/sony5/article/details/149743759)
- [Aliyun - 企业微信 iPad 协议登录流程逆向](https://developer.aliyun.com/article/1687819)
- [Citizen Lab - 微信 mmtls 加密协议安全性分析](https://citizenlab.ca/research/should-we-chat-too-security-analysis-of-wechats-mmtls-encryption-protocol/)
- [基于 RPA 的模拟人为鼠标操作方法（专利 CN115357130B）](https://patents.google.com/patent/CN115357130B/zh)

### 10.3 开源项目
- [gallonyin/worktool](https://github.com/gallonyin/worktool) - 安卓无障碍 RPA（首选参考）
- [WorkTool 官方 API 文档](https://worktool.apifox.cn/doc-850007)
- [yangyuexiong/wx_work_auto](https://github.com/yangyuexiong/wx_work_auto) - PC uiautomation
- [xlrpa/FlowBot](https://github.com/xlrpa/FlowBot) - 商业 RPA
- [yihleego/robotic-process-automation](https://github.com/yihleego/robotic-process-automation)
- [GbyAI 企微/个微机器人调研报告](https://gby.ai/wechat-bot/) ⭐ 重要参考

### 10.4 行业与合规
- [微盛 - 用企业微信 SCRM 工具会有封号风险吗？2026 避坑指南](https://college.wshoto.com/a/311316.html)
- [企微云 - 企业微信突遭封禁？自查六大雷区](https://www.wescrm.com/siyuzhishiku/siyuyunying/7970.html)
- [微伴助手 - 2025 企微 SCRM 实测](https://weibanzhushou.com/blog/33042)
- [云巴巴 - 2025 年企微 SCRM 选型指南（句子互动）](https://www.yun88.com/news/6156.html)
- [腾讯品牌保护报告 2024](https://www.tencent.com/zh-cn/articles/2202081.html)
- [深圳中院 - 腾讯诉微源码不正当竞争案](https://sdcourt.gov.cn/dyzy/372897/372898/7244935/index.html)
- [影刀社区 - RPA 模拟键鼠被企微识别为远控](https://www.yingdao.com/community/detaildiscuss?id=842630635981123584)

### 10.5 其他参考资料
- [微信 RPA 风控掉线问题讨论（pywechat Issue #92）](https://github.com/Hello-Mr-Crab/pywechat/issues/92)
- [小蜜蜂 RPA - 企业微信"零封号"自动化方案](https://zhuanlan.zhihu.com/p/1982025049721561974)
- [企业微信 Flutter 与大型 Native 工程跨四端融合实践](https://cloud.tencent.com/developer/article/2216321)
- [中信百信银行 - 企微智能助手项目供应商征集](https://ebid.cfhc.citic/cms/default/webfile/gyszj/20250506/1104452205978583040.html)
