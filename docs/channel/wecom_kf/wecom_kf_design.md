# 企业微信客服接入设计方案

> 版本: v1.0 | 创建: 2026-05-21 | 状态: 待审核

## 一、业务背景与目标

### 1.1 场景描述

企业微信客服场景：

```
企业员工（企微账号） ←→ 外部客户（微信个人号）
     ↑                        ↑
  企微内部通讯              1:1 单聊 / 群聊
```

企业通过企业微信添加外部客户的微信，建立一对一沟通渠道。在这个场景下，我们需要让 AI Agent **接管指定的企微客服账号**，自动与外部微信客户进行对话，实现 7x24 智能客服。

### 1.2 核心需求

1. **自动接管**：Agent 通过企微 API 自动接收和回复外部客户消息
2. **人工接管**：用户说"人工服务"时，会话转给真人客服
3. **多租户支持**：每个租户绑定自己的企微应用和客服账号
4. **多客服账号**：一个租户可以有多个客服账号，每个绑定不同的子智能体
5. **消息类型支持**：文本、图片、文件、链接等常见消息类型

### 1.3 与现有企微应用的区别

| 维度 | 现有企微应用（内部） | 微信客服（外部） |
|------|---------------------|---------------------|
| 通信对象 | 企业内部员工 | 外部微信用户 |
| 消息通道 | 应用消息 API | 微信客服 API |
| 触发方式 | 员工在工作台点击 | 客户在微信中发消息 |
| 消息收发 | 单向推送+回调 | 双向实时收发 |
| 回复限制 | 无限制 | 48 小时窗口 + 5 条/次 |

---

## 二、技术方案选型

### 2.1 方案对比

企业微信提供了多种与外部微信用户交互的 API：

| 方案 | 消息收发能力 | 合规性 | 适合场景 |
|------|------------|--------|---------|
| **微信客服 API** | 完整双向 | 完全合规 | AI 自动客服（推荐） |
| 客户联系 API | 仅欢迎语/群发模板 | 合规 | 营销触达 |
| 会话存档 API | 只读 | 合规（需付费） | 质检监控 |
| 接管员工账号 | 无官方支持 | 违反协议 | 不推荐 |

### 2.2 选定方案：微信客服 API

**微信客服**是企业微信提供的独立客服系统，支持通过 API 完全接管消息收发，天然适配 AI Agent 场景。

**核心优势**：

1. 官方正规通道，完全合规
2. 完整的双向消息 API（接收回调 + 读取消息 + 发送消息）
3. 内置"智能助手"接待模式，天然适配 AI 场景
4. 支持多场景接入（微信内链接、二维码、视频号、公众号、小程序、网页）
5. 不需要员工企微客户端在线
6. 会话转接机制原生支持（AI → 人工）

**核心限制**：

- 48 小时回复窗口：客户主动发消息后 48 小时内可回复
- 5 条消息限制：每次用户发消息后，企业最多可发 5 条；用户再发消息则刷新额度
- 客户需通过客服链接/二维码主动进入会话，不是在员工聊天窗口直接发消息

---

## 三、系统架构设计

### 3.1 整体架构

```
外部微信用户
    │
    │ 微信消息
    ▼
企业微信服务器
    │
    │ ① 回调推送（POST /t/{tenant_id}/wecom_kf/callback/{config_id}）
    ▼
Our Agent 后端（FastAPI）
    │
    │ ② 解析回调事件
    │ ③ 调用 sync_msg 拉取完整消息内容
    ▼
微信客服适配器（WeComKfAdapter）
    │
    │ ④ 转换为 UnifiedMessage
    ▼
渠道消息处理流程（channel_routes.py 已有逻辑）
    │
    │ ⑤ 路由到对应子智能体
    ▼
Agent 处理 → 生成回复
    │
    │ ⑥ 通过 WeComKfAdapter.send_message() 回复
    ▼
企业微信服务器
    │
    │ 微信消息
    ▼
外部微信用户
```

### 3.2 核心组件

#### 3.2.1 WeComKfAdapter（微信客服适配器）

新增渠道适配器，实现 `ChannelAdapter` 接口：

```
src/channels/wecom_kf/
├── __init__.py
├── adapter.py          # WeComKfAdapter 主类
├── crypto.py           # 复用现有 WeComCrypto（企微回调加密通用）
├── message.py          # 消息格式转换（微信客服消息 ↔ UnifiedMessage）
└── api_client.py       # 微信客服 API 封装（token、sync_msg、send_msg）
```

#### 3.2.2 API Client 核心接口

```python
class WeComKfApiClient:
    """微信客服 API 客户端"""

    async def get_access_token(self) -> str:
        """获取 access_token（带缓存和自动刷新）"""

    async def sync_msg(self, cursor: str = "", limit: int = 1000, voice_format: int = 0) -> dict:
        """拉取消息（POST /cgi-bin/kf/sync_msg）"""

    async def send_msg(self, touser: str, open_kfid: str, msgtype: str, content: dict) -> dict:
        """发送消息（POST /cgi-bin/kf/send_msg）"""

    async def send_welcome(self, code: str, content: dict) -> dict:
        """发送欢迎语（POST /cgi-bin/kf/send_msg_on_event）"""

    async def get_service_state(self, open_kfid: str, external_userid: str) -> dict:
        """获取会话状态"""

    async def trans_service_state(self, open_kfid: str, external_userid: str,
                                   service_state: int, servicer_userid: str = "") -> dict:
        """变更会话状态（接入/转接/结束）"""
```

### 3.3 消息流转设计

#### 3.3.1 接收消息流程

```
企业微信推送回调（含 encrypted XML）
    │
    ▼
1. 验证签名（verify_signature）
2. 解密消息体（WeComCrypto）
3. 解析 XML 获取 event_type
    │
    ├── event_type == "msg_send_fail" → 记录日志，忽略
    ├── event_type == "user_recall_msg" → 记录日志，忽略
    ├── event_type == "enter_session" → 发送欢迎语
    ├── event_type == "session_status_change" → 更新会话状态
    │
    └── event_type 包含消息 → 继续处理
        │
        ▼
4. 调用 sync_msg 拉取完整消息内容（带 cursor 分页）
5. 消息格式转换为 UnifiedMessage
6. 消息去重（MessageDeduplicator）
7. 立即返回 "success" 给企业微信（5秒要求）
8. 后台异步处理（asyncio.create_task）
    │
    ▼
9. 查找/创建 channel session
10. 路由到对应子智能体
11. Agent 生成回复
12. 通过 send_msg 发送回复
```

#### 3.3.2 发送消息流程

```
Agent 生成回复文本
    │
    ▼
1. 文本分割（长消息分段发送）
2. 消息格式检测（markdown vs 纯文本）
3. 调用 send_msg 发送（含重试和 token 刷新）
    │
    ├── 文本消息 → {"msgtype": "text", "text": {"content": "..."}}
    ├── 图片消息 → {"msgtype": "image", "image": {"media_id": "..."}}
    ├── 文件消息 → {"msgtype": "file", "file": {"media_id": "..."}}
    └── 链接消息 → {"msgtype": "link", "link": {"title": "...", "url": "..."}}
```

### 3.4 人工接管机制

#### 3.4.1 AI → 人工转接流程

```
用户发送"人工服务"/"转人工"等关键词
    │
    ▼
1. Agent 检测到人工服务意图
   （通过 system_prompt 指令识别）
    │
    ▼
2. Agent 回复："正在为您转接人工客服，请稍候..."
    │
    ▼
3. 调用 trans_service_state API：
   - service_state = 3（接入人工）
   - servicer_userid = 配置的接待人员
    │
    ▼
4. 后续消息由人工客服在企微端处理
   Agent 不再自动回复
```

#### 3.4.2 人工 → AI 接回流程

```
人工客服在企微端结束接待
    │
    ▼
1. 企业微信推送 session_status_change 回调
   service_state 变为 0（未处理/智能助手接待）
    │
    ▼
2. 后端更新会话状态
3. 后续客户消息重新由 AI Agent 处理
```

### 3.5 回调事件处理矩阵

| 事件类型 | 处理方式 | 说明 |
|---------|---------|------|
| 消息事件（text/image/voice/file/link等） | 转入 Agent 处理 | 核心业务 |
| `enter_session` | 发送欢迎语 | 客户首次进入会话 |
| `session_status_change` | 更新会话状态 | 跟踪 AI/人工切换 |
| `user_recall_msg` | 记录日志 | 用户撤回消息 |
| `msg_send_fail` | 重试或通知 | 消息发送失败 |

---

## 四、数据模型设计

### 4.1 复用现有表

| 表名 | 用途 |
|------|------|
| `tenant_channel_configs` | 存储微信客服配置（channel_type = "wecom_kf"） |
| `channel_sessions` | 微信客服会话管理 |
| `channel_messages` | 消息记录 |
| `channel_message_dedup` | 消息去重 |

### 4.2 微信客服配置格式

存储在 `tenant_channel_configs.config` JSON 中：

```json
{
    "corp_id": "ww1234567890",
    "secret": "客服secret",
    "token": "回调Token",
    "encoding_aes_key": "43位AES密钥",
    "kf_account": [
        {
            "open_kfid": "wkxxxxxx",
            "name": "售前咨询",
            "subagent_type": "sales-consultant",
            "welcome_message": "您好，我是AI智能客服，请问有什么可以帮您？",
            "servicer_userid_list": ["zhangsan", "lisi"],
            "allow_agent_transfer": true
        },
        {
            "open_kfid": "wkyyyyyy",
            "name": "售后服务",
            "subagent_type": "after-sales",
            "welcome_message": "您好，我是售后AI助手，请描述您的问题。",
            "servicer_userid_list": ["wangwu"],
            "allow_agent_transfer": true
        }
    ]
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `corp_id` | 企业微信企业 ID |
| `secret` | 微信客服应用的 Secret |
| `token` | 回调配置的 Token（用于签名验证） |
| `encoding_aes_key` | 回调配置的 EncodingAESKey |
| `kf_account` | 客服账号列表，支持一个配置对应多个客服账号 |
| `kf_account[].open_kfid` | 客服账号 ID（创建客服账号时生成） |
| `kf_account[].subagent_type` | 该客服账号绑定的子智能体类型 |
| `kf_account[].servicer_userid_list` | 可转接的人工客服企微 userid 列表 |
| `kf_account[].allow_agent_transfer` | 是否允许 Agent 主动转人工（默认 true） |
| `kf_account[].human_transfer_keywords` | 【已废弃】触发人工转接的关键词，不再使用 |

### 4.3 会话标识规则

`channel_sessions.session_id` 生成规则：

```
wecom_kf_{open_kfid}_{external_userid}
```

- `open_kfid`：客服账号 ID，区分不同客服入口
- `external_userid`：外部微信用户 ID（以 `wm` 开头）

### 4.4 用户身份管理

#### 4.4.1 企微隐私限制

**企业微信无法获取外部微信用户的手机号**。通过微信客服 API 能获取的客户信息：

| 字段 | 能否获取 | 说明 |
|------|---------|------|
| `external_userid` | 能 | 如 `wmXXXX`，脱敏标识 |
| 昵称 name | 能 | 自建应用可获取 |
| 头像 avatar | 仅自建应用 | 代开发应用不可获取 |
| 性别 gender | 能 | |
| unionid | 能 | 需绑定微信开发者 ID |
| **手机号** | **不能** | 隐私保护，API 不返回 |

#### 4.4.2 用户自动创建策略

微信客服客户首次进入时，系统自动创建用户账号：

```
客户首次进入微信客服会话
    │
    ▼
1. 用 external_userid 调用"获取客户详情"接口 → 获取昵称、头像
    │
    ▼
2. 检查是否已存在该 external_userid 关联的用户
    ├── 已存在 → 直接使用，创建/恢复会话
    │
    └── 不存在 → 自动创建新用户
        │
        ▼
        users 表插入新记录：
        {
            "user_id": "user_{uuid4_hex[:12]}",
            "username": "微信客户_{name}",       # 昵称
            "source": "wecom_kf",
            "phone": null,                        # 无手机号
            "metadata": {
                "external_userid": "wmXXXX",
                "avatar": "头像URL",
                "gender": 1,
                "open_kfid": "wkAAAA"
            }
        }
```

#### 4.4.3 手机号补全与用户合并

```
场景 A：对话中用户主动提供手机号
    │
    ▼
Agent 检测到手机号（或通过专门工具提取）
    │
    ▼
调用用户合并逻辑：
    1. 查找该手机号是否已关联其他用户
        ├── 无 → 更新当前用户的 phone 字段
        └── 有 → 合并用户：
            - 将当前会话的 channel_session 转移到已有用户
            - 保留两个 external_userid 的映射
            - 标记当前 wecom_kf 用户为"已合并"
            - 后续消息直接路由到已有用户

场景 B：通过小程序授权获取手机号（后续扩展）
    │
    ▼
微信客服发送小程序消息引导授权
    │
    ▼
用户点击授权 → 小程序 getPhoneNumber 接口 → 获取手机号
    │
    ▼
回调通知后端 → 触发同上的用户合并逻辑
```

#### 4.4.4 用户来源标识

在 `users` 表（或 `channel_sessions` 表）中通过 `metadata` 记录用户来源：

```json
{
    "source": "wecom_kf",
    "external_userid": "wmAAAA",
    "open_kfid": "wkXXXX",
    "name": "张三",
    "phone_bound": false,
    "merged_from": null
}
```

已合并的用户：
```json
{
    "source": "web",
    "phone": "138xxxx",
    "wecom_kf_bindings": [
        {"external_userid": "wmAAAA", "open_kfid": "wkXXXX", "merged_at": "2026-05-21T10:00:00"}
    ]
}
```

---

## 五、回调路由设计

### 5.1 路由注册

在 `src/saas/api/channel_routes.py` 中新增：

```python
# 微信客服回调
@router.get("/t/{tenant_id}/wecom_kf/callback/{config_id}")
async def wecom_kf_verify(request: Request, tenant_id: str, config_id: str):
    """企业微信验证回调URL有效性"""

@router.post("/t/{tenant_id}/wecom_kf/callback/{config_id}")
async def wecom_kf_callback(request: Request, tenant_id: str, config_id: str):
    """企业微信微信客服消息回调"""
```

### 5.2 ChannelFactory 注册

在 `src/saas/services/channel_factory.py` 中新增：

```python
_ADAPTER_CLASSES = {
    "wecom":    "src.channels.wecom.adapter.WeComAdapter",
    "dingtalk": "src.channels.dingtalk.adapter.DingtalkAdapter",
    "feishu":   "src.channels.feishu.adapter.FeishuAdapter",
    "wecom_kf": "src.channels.wecom_kf.adapter.WeComKfAdapter",  # 新增
}
```

### 5.3 ChannelType 枚举

在 `src/models/message.py` 中新增：

```python
class ChannelType(str, Enum):
    wecom = "wecom"
    dingtalk = "dingtalk"
    feishu = "feishu"
    web = "web"
    wecom_kf = "wecom_kf"  # 新增
```

---

## 六、运营配置设计

### 6.1 配置流程（租户管理员视角）

```
步骤 1：企业微信管理后台配置
    ├── 开启"微信客服"功能
    ├── 创建客服账号（获取 open_kfid）
    ├── 设置"通过 API 管理微信客服账号"
    ├── 创建自建应用，配置回调 URL
    └── 将自建应用设为"可调用接口的应用"

步骤 2：Our Agent 后台配置
    ├── 进入租户设置 → 渠道配置
    ├── 选择"企业微信客服"
    ├── 填写 corp_id、secret、token、encoding_aes_key
    ├── 配置客服账号列表：
    │   ├── 客服账号 ID（open_kfid）
    │   ├── 绑定的子智能体
    │   ├── 欢迎语
    │   └── 人工接待人员列表
    ├── 点击"验证"（触发回调URL验证）
    └── 验证通过后启用

步骤 3：获取客服链接
    ├── 系统自动生成客服链接（或从企微后台获取）
    ├── 配置到公众号菜单、小程序、网页等入口
    └── 客户点击链接即可发起 AI 对话
```

### 6.2 前端配置页面设计

在现有渠道配置页面中新增"企业微信客服"渠道类型，配置表单：

```
┌─────────────────────────────────────────────────────┐
│  渠道配置 > 企业微信客服                            │
├─────────────────────────────────────────────────────┤
│                                                     │
│  企业微信企业ID (corp_id)：                          │
│  ┌─────────────────────────────────────────────┐    │
│  │ ww1234567890                                │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  应用 Secret：                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │ ••••••••••••••••••          [显示] [测试连接] │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  回调 Token：                                       │
│  ┌─────────────────────────────────────────────┐    │
│  │ my_callback_token                           │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  回调 EncodingAESKey：                              │
│  ┌─────────────────────────────────────────────┐    │
│  │ 43位密钥...                                 │    │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  回调 URL（复制到企微后台）：                        │
│  ┌─────────────────────────────────────────────┐    │
│  │ https://your-domain.com/t/xxx/wecom_kf/...  │ [复制] │
│  └─────────────────────────────────────────────┘    │
│                                                     │
│  ═══ 客服账号配置 ═══                               │
│                                                     │
│  ┌ 客服账号 1 ─────────────────────────────────┐    │
│  │ 客服账号名称：售前咨询                       │    │
│  │ open_kfid：wkAAAA                            │    │
│  │ 绑定子智能体：[下拉选择 ▼]                   │    │
│  │ 欢迎语：您好，我是AI智能客服...              │    │
│  │ 人工接待人员：[zhangsan] [lisi] [+添加]      │    │
│  │ 转人工关键词：人工服务, 转人工 [+添加]       │    │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌ 客服账号 2 ─────────────────────────────────┐    │
│  │ ...                                         │    │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  [+ 添加客服账号]                                   │
│                                                     │
│              [验证连接]  [保存]  [取消]              │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 6.3 子智能体绑定策略

每个客服账号可以绑定一个子智能体，用于处理该客服入口的消息：

| 客服账号 | 绑定子智能体 | 适用场景 |
|---------|------------|---------|
| 售前咨询 | sales-consultant | 产品咨询、报价、方案推荐 |
| 售后服务 | after-sales | 退换货、维修、投诉处理 |
| 技术支持 | tech-support | 技术问题、使用指导 |
| 通用客服 | master-agent | 未指定时的默认处理 |

绑定关系通过 `kf_account[].subagent_type` 字段配置，在消息路由时通过 `agent_router.get_agent(subagent_type, session_id)` 匹配。

---

## 七、system_prompt 设计（人工转接指令）

为了让子智能体正确识别和处理人工转接需求，需要在客服场景的 system_prompt 中注入特殊指令：

```markdown
# 客服模式指令

你正在通过企业微信与外部客户进行实时对话。请遵循以下规则：

## 转人工规则
当客户表达以下意图时，必须触发转人工流程：
- 直接说"人工服务"、"转人工"、"人工客服"、"找真人"
- 表达强烈不满或投诉情绪
- 连续 3 次表示无法解决问题

转人工时：
1. 先回复客户："正在为您转接人工客服，请稍候..."
2. 调用 transfer_to_human 工具

## 回复规范
- 回复简洁明了，避免过长的段落
- 每条消息控制在 500 字以内
- 如果回复内容较长，分段发送
- 使用友好的客服语气
- 不要暴露你是 AI 的技术细节

## 消息限制
- 你每次回复后，客户再发消息你才能继续回复
- 避免一次发送超过 5 条消息
```

### 7.1 转人工工具

新增一个内置工具 `transfer_to_human`，供 Agent 调用。

> **2026-06-23 更新**：详见 [transfer_to_human_optimization.md](./transfer_to_human_optimization.md)。要点：
> - `reason` 字段必填，用于审计与会话元信息记录
> - **渠道隔离完全由 execute 段的 `get_kf_context()` 判断**——LLM 推理时拿不到渠道信息，因此 description/usage_guide 不再约束 LLM "仅在微信客服渠道调用"（这种约束无效）
> - 非微信客服渠道调用时返回友好失败提示 `"当前渠道未提供人工客服"`，LLM 收到后改为直接用文字回复用户
> - **【2026-06-30 更新】移除全部关键词校验**——`human_transfer_keywords` 字段废弃，转人工完全由 Agent 通过 `transfer_to_human` 工具调用处理
> - 新增 `kf_config.allow_agent_transfer` 开关（默认 true），允许租户管理员禁用 Agent 主动转人工
> - 会话 metadata 新增 `transfer_source=agent`，区别于关键词触发的转接

```python
class TransferToHumanInput(BaseModel):
    reason: str = Field(..., description="转人工原因，必填")

class TransferToHumanTool(BaseTool):
    name = "transfer_to_human"
    description = "将会话转接给人工客服。..."  # 描述何时转人工 + 调用后无需回复
    usage_guide = ""  # 渠道由 execute 段兜底，不污染系统提示词

    async def execute(self, **kwargs):
        # 1. get_kf_context() 为 None → 非微信渠道，返回友好失败
        # 2. allow_agent_transfer=false → 返回失败
        # 3. servicer_userid_list 为空 → 返回失败
        # 4. 调用 adapter.transfer_to_human()
        # 5. 更新 channel session metadata（含 transfer_source=agent）
```

---

## 八、关键技术实现

### 8.1 消息同步与 Cursor 管理

微信客服的 `sync_msg` 接口使用 cursor 分页机制：

```python
class CursorManager:
    """管理每个客服账号的消息同步游标"""

    def __init__(self):
        # 存储在 Redis 中，支持多 worker 共享
        # key: wecom_kf_cursor:{open_kfid}
        # value: cursor string
        pass

    async def get_cursor(self, open_kfid: str) -> str:
        """获取上次同步的 cursor"""

    async def set_cursor(self, open_kfid: str, cursor: str):
        """更新 cursor"""
```

**同步策略**：

1. 收到回调通知时，调用 `sync_msg` 拉取消息
2. 使用 cursor 递增拉取，直到 `has_more=0`
3. 每次成功拉取后更新 cursor
4. cursor 存储在 Redis 中（TTL 3 天，与微信客服消息保留期一致）

### 8.2 Token 管理

微信客服使用独立的 access_token，与现有企微应用 token 不同：

```python
class WeComKfApiClient:
    # token 缓存逻辑复用现有 WeComAdapter 的模式
    # key: wecom_kf_token:{corp_id}
    # 过期时间: expires_in - 300s（提前5分钟刷新）
    # 使用 asyncio.Lock 防止并发刷新
```

### 8.3 消息格式转换

```python
# 微信客服消息 → UnifiedMessage
def parse_kf_message(self, msg: dict) -> UnifiedMessage:
    """
    微信客服消息格式：
    {
        "msgid": "xxx",
        "open_kfid": "wkAAAA",
        "external_userid": "wmXXXXXXXX",
        "send_time": 1234567890,
        "origin": 3,  # 3=微信客户发送, 4=接待人员发送
        "servicer_userid": "",  # 接待人员（仅 origin=4 时有）
        "msgtype": "text",
        "text": {"content": "你好"}
    }
    """
    msg_type = msg.get("msgtype", "text")

    if msg_type == "text":
        content = msg.get("text", {}).get("content", "")
        message_type = MessageType.TEXT
    elif msg_type == "image":
        content = {"media_id": msg.get("image", {}).get("media_id", "")}
        message_type = MessageType.IMAGE
    elif msg_type == "voice":
        content = {"media_id": msg.get("voice", {}).get("media_id", "")}
        # 微信客服语音消息可能自带识别文本
        recognition = msg.get("voice", {}).get("Recognition", "")
        if recognition:
            content["text"] = recognition
        message_type = MessageType.TEXT  # 优先使用识别文本
    elif msg_type == "file":
        content = {"media_id": msg.get("file", {}).get("media_id", ""),
                   "file_name": msg.get("file", {}).get("file_name", "")}
        message_type = MessageType.FILE
    elif msg_type == "link":
        content = msg.get("link", {})
        message_type = MessageType.TEXT
    else:
        content = {"raw": str(msg)}
        message_type = MessageType.TEXT

    return UnifiedMessage(
        message_id=msg.get("msgid", ""),
        channel_type=ChannelType.WECOM_KF,
        user_id=msg.get("external_userid", ""),
        user_name="",  # 外部用户名需额外查询
        message_type=message_type,
        content=content,
        timestamp=datetime.fromtimestamp(msg.get("send_time", 0)),
        raw_message=msg
    )
```

### 8.4 发送消息（UnifiedResponse → 微信客服消息）

#### 8.4.1 格式兼容性问题

**微信客服 API 不支持 markdown 格式**（markdown/markdown_v2 是企微应用消息的类型，微信客服不支持）。

| Agent 输出格式 | Web 前端渲染 | 微信客服渲染 | 兼容方案 |
|---------------|-------------|-------------|---------|
| 纯文本 | 正常 | 正常 | 直接发送 |
| Markdown（表格、加粗、列表） | `marked` 渲染 | **纯文本显示，格式丢失** | 剥离 markdown 语法，转为纯文本 |
| DownloadFileCard（文件卡片） | `DownloadFileCard` 组件 | **无法渲染** | 转为 `link` 图文链接消息 |
| 图片（`![](url)`） | 正常渲染 | **显示为纯文本** | 转为 `image` 消息（需上传 media） |

#### 8.4.2 文件发送方案

Agent 生成文件后，通过 `tool_result` 事件返回结构化的 `downloadableFiles` 数据。微信客服渠道需要将这些文件转为企微支持的消息格式。

**方案 A（初期，推荐）**：使用 `link` 图文链接消息

```json
{
    "msgtype": "link",
    "link": {
        "title": "📄 report.docx",
        "desc": "点击下载文件 (12KB)",
        "url": "https://your-domain.com/api/files/{file_id}/download?token=TEMP_TOKEN",
        "thumb_media_id": "MEDIA_ID"
    }
}
```

- 客户在微信中看到一个可点击的图文卡片
- 点击后跳转到浏览器下载文件
- **前提**：文件下载 API 需要支持临时 token 认证（无需登录即可下载）
- `thumb_media_id` 需要预设一个默认缩略图（上传一次后复用）

**方案 B（后续优化）**：上传为 media 后发送 `file` 类型消息

```json
{
    "msgtype": "file",
    "file": {
        "media_id": "MEDIA_ID"
    }
}
```

- 需要先调上传临时素材接口，把文件从服务器上传到企微 → 获取 media_id
- 客户在微信中直接下载文件，体验更原生
- 限制：media_id 有效期 3 天，大文件上传可能超时
- 需要多一步中转：本地文件 → 上传到企微 → 发送 media_id

#### 8.4.3 Markdown 文本处理

微信客服的 `text` 消息不支持 markdown，需要将 Agent 的 markdown 回复转为纯文本：

```python
import re

def markdown_to_plain_text(md: str) -> str:
    """将 markdown 转为纯文本，适配微信客服 text 消息"""
    # 剥离 HTML 标签
    text = re.sub(r'<[^>]+>', '', md)
    # 表格 → 保留文本内容，用空格分隔
    text = re.sub(r'\|', ' | ', text)
    text = re.sub(r'^[-:]+$', '', text, flags=re.MULTILINE)
    # 加粗/斜体 → 去除标记符
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # 链接 [text](url) → text(url)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'\1(\2)', text)
    # 标题标记
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
```

#### 8.4.4 完整发送逻辑

```python
async def send_message(self, response: UnifiedResponse) -> bool:
    """发送消息到微信客服"""
    text = response.text
    downloadable_files = getattr(response, 'downloadable_files', [])

    # 1. 发送文本回复（markdown → 纯文本）
    plain_text = markdown_to_plain_text(text)
    parts = self._split_message(plain_text, max_bytes=2048)

    for part in parts:
        result = await self.api_client.send_msg(
            touser=response.reply_to,
            open_kfid=self.open_kfid,
            msgtype="text",
            content={"content": part}
        )
        if result.get("errcode", 0) != 0:
            logger.error(f"发送消息失败: {result}")
            return False

    # 2. 发送文件（如有）
    for file_info in downloadable_files:
        # 方案 A：link 图文链接
        download_url = file_info.get("download_url", "")
        # 确保是完整 URL
        if download_url.startswith("/"):
            download_url = f"{self.base_url}{download_url}"

        result = await self.api_client.send_msg(
            touser=response.reply_to,
            open_kfid=self.open_kfid,
            msgtype="link",
            content={
                "title": f"📄 {file_info.get('file_name', '文件')}",
                "desc": f"点击下载 ({format_size(file_info.get('file_size', 0))})",
                "url": download_url,
                "thumb_media_id": self.default_thumb_media_id,
            }
        )
        if result.get("errcode", 0) != 0:
            logger.error(f"发送文件链接失败: {result}")

    return True
```

#### 8.4.5 临时下载链接机制

现有 `/api/files/{file_id}/download` 接口需要认证。微信客户在浏览器中点击文件链接时无法携带登录态，需要支持临时 token：

```
GET /api/files/{file_id}/download?token=TEMP_TOKEN

TEMP_TOKEN 生成规则：
- 包含 file_id、过期时间（默认 1 小时）
- 使用 HMAC-SHA256 签名
- 无需登录即可下载
- 一次性或短时效，防止链接泄露
```

**涉及文件**：`src/api/files.py`（或对应文件下载路由），新增 `token` 参数验证逻辑。

### 8.5 回调处理核心流程

```python
async def wecom_kf_callback(request: Request, tenant_id: str, config_id: str):
    """微信客服消息回调"""
    # 1. 读取 raw body
    body = await request.body()

    # 2. 解析 XML，提取 Encrypt 字段
    xml_data = parse_xml(body)
    encrypt = xml_data.find("Encrypt").text

    # 3. 创建适配器
    adapter, _, subagent_type = ChannelFactory.create_from_tenant_config(
        tenant_id, "wecom_kf", config_id
    )

    # 4. 验证签名 + 解密
    msg_signature = request.query_params.get("msg_signature")
    timestamp = request.query_params.get("timestamp")
    nonce = request.query_params.get("nonce")

    if not adapter.verify_signature(msg_signature, timestamp, nonce, encrypt):
        return Response(content="invalid signature", status_code=403)

    decrypted = adapter.crypto.decrypt(encrypt)

    # 5. 解析回调事件
    callback_xml = parse_xml(decrypted)
    event_type = callback_xml.find(".//Event")?.text

    if event_type == "change_type":
        change_type = callback_xml.find(".//ChangeType").text

        if change_type == "kf_msg_or_event":
            # 消息事件 → 拉取消息处理
            open_kfid = callback_xml.find(".//OpenKfId").text
            asyncio.create_task(
                _process_kf_messages(tenant_id, config_id, open_kfid, adapter)
            )

        elif change_type == "session_status_change":
            # 会话状态变更 → 更新本地状态
            await _handle_session_status_change(callback_xml, tenant_id)

    # 6. 立即返回 success（5 秒要求）
    return Response(content="success")


async def _process_kf_messages(tenant_id, config_id, open_kfid, adapter):
    """后台拉取并处理微信客服消息"""
    # 1. 查找该 open_kfid 对应的客服账号配置
    kf_config = adapter.get_kf_config(open_kfid)
    if not kf_config:
        logger.warning(f"Unknown open_kfid: {open_kfid}")
        return

    # 2. 拉取消息
    cursor = await cursor_manager.get_cursor(open_kfid)
    has_more = True

    while has_more:
        result = await adapter.api_client.sync_msg(
            cursor=cursor,
            limit=100
        )
        has_more = result.get("has_more", 0) == 1
        cursor = result.get("next_cursor", "")

        for msg in result.get("msg_list", []):
            # 跳过非客户消息（origin=3 是客户，origin=4 是接待人员）
            if msg.get("origin") != 3:
                continue

            # 3. 消息去重
            msg_id = msg.get("msgid", "")
            if await MessageDeduplicator.is_duplicate(msg_id):
                continue

            # 4. 转换为 UnifiedMessage
            unified_msg = adapter.parse_message(msg)

            # 5. 查找/创建会话
            session_id = f"wecom_kf_{open_kfid}_{unified_msg.user_id}"
            session = await channel_session_manager.get_or_create_session(
                channel_type="wecom_kf",
                channel_user_id=unified_msg.user_id,
                channel_chat_id=open_kfid,
                tenant_id=tenant_id,
                context_data={"open_kfid": open_kfid}
            )

            # 6. 保存用户消息
            await channel_session_manager.add_message(
                session_id=session.session_id,
                role="user",
                content=unified_msg.text or str(unified_msg.content),
                metadata={"msgid": msg_id, "msgtype": msg.get("msgtype")}
            )

            # 7. 检查人工转接关键词
            if adapter.should_transfer_to_human(unified_msg.text, kf_config):
                await _transfer_to_human(adapter, session, kf_config)
                continue

            # 8. 路由到子智能体
            subagent_type = kf_config.get("subagent_type", "master")
            agent = agent_router.get_agent(subagent_type, session_id)

            # 9. Agent 处理
            reply = await agent.process_message(
                user_input=unified_msg.text or "[非文本消息]",
                session_id=session_id
            )

            # 10. 发送回复
            response = UnifiedResponse.from_text(
                text=reply,
                reply_to=unified_msg.user_id,
                message_id=str(uuid4())
            )
            await adapter.send_message(response)

            # 11. 保存助手回复
            await channel_session_manager.add_message(
                session_id=session.session_id,
                role="assistant",
                content=reply
            )

        # 更新 cursor
        await cursor_manager.set_cursor(open_kfid, cursor)
```

---

## 九、文件变更规划

### 9.1 新增文件

| 文件 | 说明 |
|------|------|
| `src/channels/wecom_kf/__init__.py` | 模块初始化 |
| `src/channels/wecom_kf/adapter.py` | 微信客服适配器（核心） |
| `src/channels/wecom_kf/api_client.py` | 微信客服 API 客户端 |
| `src/channels/wecom_kf/message.py` | 消息格式转换 |
| `src/channels/wecom_kf/crypto.py` | 复用现有 WeComCrypto 或重新导入 |
| `src/tools/transfer_to_human.py` | 转人工工具 |

### 9.2 修改文件

| 文件 | 改动 |
|------|------|
| `src/models/message.py` | ChannelType 枚举新增 `wecom_kf` |
| `src/saas/services/channel_factory.py` | `_ADAPTER_CLASSES` 新增 `wecom_kf` |
| `src/api/files.py`（或文件下载路由） | 新增临时 token 下载机制 |
| `src/saas/api/channel_routes.py` | 新增微信客服回调路由 |
| `src/core/agent.py` | `AGENT_TOOLS` 新增 `transfer_to_human` |

### 9.3 前端变更

| 文件 | 改动 |
|------|------|
| `frontend/src/api/channel.ts` | 新增微信客服配置 API |
| `frontend/src/components/ChannelConfig.vue` | 新增微信客服渠道配置表单 |

---

## 十、开发计划

### Phase 1: 核心后端（预计 6-8 天）

| 任务 | 涉及文件 | 预计天数 |
|------|----------|---------|
| 1.1 WeComKfApiClient API 封装 | `src/channels/wecom_kf/api_client.py` | 1 天 |
| 1.2 WeComKfAdapter 适配器 | `src/channels/wecom_kf/adapter.py` | 1.5 天 |
| 1.3 回调路由 + 消息处理 | `src/saas/api/channel_routes.py` | 1.5 天 |
| 1.4 Cursor 管理器 | `src/channels/wecom_kf/cursor.py` | 0.5 天 |
| 1.5 转人工工具 | `src/tools/transfer_to_human.py` | 0.5 天 |
| 1.6 ChannelFactory + 枚举注册 | 多文件 | 0.5 天 |
| 1.7 Markdown 转纯文本 + 文件 link 消息 | `src/channels/wecom_kf/adapter.py` | 0.5 天 |
| 1.8 临时下载链接机制 | `src/api/files.py` | 0.5 天 |
| 1.9 单元测试 | `tests/unit/tools/test_wecom_kf.py` | 1 天 |

**验收标准**：
- [ ] 微信客服回调 URL 验证通过
- [ ] 能接收外部微信客户消息并自动回复
- [ ] Markdown 格式的回复在微信中可读（剥离格式标记）
- [ ] Agent 生成的文件以 link 卡片形式发送，客户点击可下载
- [ ] 临时下载链接有效且有时效控制
- [ ] 人工转接功能正常
- [ ] 消息去重和 cursor 管理正常

### Phase 2: 前端配置页面（预计 2-3 天）

| 任务 | 涉及文件 | 预计天数 |
|------|----------|---------|
| 2.1 微信客服配置 API | `src/saas/api/channel_routes.py` | 0.5 天 |
| 2.2 前端配置页面 | `frontend/src/components/ChannelConfig.vue` | 1.5 天 |
| 2.3 配置验证功能 | 前后端 | 0.5 天 |

**验收标准**：
- [ ] 租户管理员可自助配置微信客服
- [ ] 配置验证流程正常
- [ ] 多客服账号配置支持

### Phase 3: 生产优化（预计 2-3 天）

| 任务 | 说明 | 预计天数 |
|------|------|---------|
| 3.1 消息发送限流 | 适配 5 条/次限制 | 0.5 天 |
| 3.2 文件直发（方案 B） | 文件上传为 media_id，发送 file 类型消息，体验更原生 | 1 天 |
| 3.3 接收图片/文件消息 | 客户发送的图片和文件通过 media_id 下载后传入 Agent | 1 天 |
| 3.4 错误恢复与重试 | token 刷新、消息重发 | 0.5 天 |
| 3.5 监控与告警 | 回调异常、发送失败监控 | 0.5 天 |
| 3.6 集成测试 | 端到端测试 | 0.5 天 |

**验收标准**：
- [ ] 消息发送限流合规
- [ ] 文件以原生 file 消息发送，客户直接下载
- [ ] 客户发送的图片和文件 Agent 可识别
- [ ] 异常场景有合理恢复机制

---

## 十一、风险与注意事项

### 11.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 48 小时回复窗口 | 超时无法回复客户 | 监控未回复消息，及时提醒 |
| 5 条消息限制 | 复杂回复受限 | 长文本分段，控制单次回复量 |
| 回调 5 秒响应要求 | 企微重试导致重复处理 | 立即返回 HTTP 200 "success"，Agent 处理放后台 asyncio.Task（见下方说明） |
| Token 并发刷新 | 重复请求 | 使用 asyncio.Lock + 缓存 |
| Cursor 丢失 | 重复拉取消息 | Redis 持久化 + 消息去重 |

#### 11.1.1 "5 秒回调响应"机制详解

这个限制**只影响回调 HTTP 响应，不影响 Agent 处理时间**。

```
企业微信推送回调
    │
    ▼
我们的回调路由处理（必须在 5 秒内）：
    ├── 验证签名 ──→ 几毫秒
    ├── 解密消息 ──→ 几毫秒
    ├── 消息去重检查 ──→ 几毫秒
    ├── 创建后台任务 ──→ 几毫秒
    └── 返回 HTTP 200 "success" ──→ 总计远小于 5 秒
         │
         │  后台 asyncio.Task（不受 5 秒限制）：
         │  ├── Agent 推理 ──→ 可能 10-60 秒
         │  ├── 工具调用 ──→ 可能多次，每次数秒
         │  ├── 生成回复 ──→ 数秒
         │  └── 调用 send_msg API 主动推送给客户
         │
         ▼
客户收到 Agent 回复（通过独立 HTTP 请求推送，与回调无关）
```

关键点：**回调响应和消息回复是两个完全独立的 HTTP 请求**。回调响应只是告诉企微"我收到了"，Agent 回复是通过 `send_msg` API 主动推送。现有企微渠道 (`_process_tenant_wecom_background`) 已经采用这个模式。

### 11.2 运营风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 转人工不及时 | 客户体验差 | 关键词触发 + 情绪检测 |
| 多客服账号管理混乱 | 配置错误 | 前端校验 + 配置预览 |
| 企业微信 API 变更 | 功能失效 | 关注官方公告，预留适配层 |

### 11.3 合规注意事项

1. **数据隐私**：外部客户的微信消息属于敏感数据，存储需加密，遵守《个人信息保护法》
2. **消息存档**：企微端会话存档为客户自主开通的增值功能；我们系统已通过 `channel_messages` 表存储所有往来消息
3. **客户知情**：建议在欢迎语中告知客户正在与 AI 对话
4. **企业微信审核**：部分 API 能力需要企业微信认证

---

## 十二、与现有系统的集成点

### 12.1 复用的组件

| 组件 | 复用方式 |
|------|---------|
| `WeComCrypto` | 直接复用，加密算法相同 |
| `MessageDeduplicator` | 直接复用，消息去重逻辑通用 |
| `ChannelSessionManager` | 直接复用，channel_type 区分 |
| `ChannelFactory` | 扩展注册，新增 wecom_kf 类型 |
| `agent_router` | 直接复用，按 subagent_type 路由 |

### 12.2 新增的组件

| 组件 | 说明 |
|------|------|
| `WeComKfAdapter` | 微信客服适配器 |
| `WeComKfApiClient` | 微信客服 API 客户端 |
| `CursorManager` | 消息同步游标管理 |
| `TransferToHumanTool` | 转人工工具 |

---

## 十三、待讨论事项

1. **会话存档**：企微端的会话存档是增值付费功能，由客户自行决定是否开通，不影响我们系统。我们系统端的会话存储已通过现有 `channel_sessions` + `channel_messages` 表覆盖，`channel_type = "wecom_kf"` 自然隔离，无需额外设计
2. **图片/文件消息优先级**：已确定初期方案——Agent 回复中的 markdown 转纯文本发送，生成的文件通过 `link` 图文链接卡片发送（Phase 1）；后续优化为 `file` 类型直接发送（Phase 3）；客户发送的图片/文件在 Phase 3 支持
3. **客户身份关联**：已确定方案——企微无法获取外部用户手机号，首次进入时用 `external_userid` 自动创建无手机号用户；对话中用户提供手机号后触发用户合并；后续可扩展小程序授权获取手机号。详见 4.4 节
4. **多 Agent 协作**：已确定——不需要多 Agent 协作，一个客服账号绑定一个子智能体即可
5. **主动消息能力**：已确定——不需要，仅在 48 小时窗口内回复即可
6. **会话评估机制**：已确定——不需要
7. **敏感词过滤**：已确定——不需要
8. **客户画像集成**：已确定——不需要自动回流到 CRM
