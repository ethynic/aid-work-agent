# 第三方渠道机器人配置指南

本文档详细介绍如何将 AID Work Agent 机器人接入到飞书、企业微信和钉钉三大平台。

## 目录

- [飞书机器人配置](#飞书机器人配置)
- [企业微信机器人配置](#企业微信机器人配置)
- [钉钉机器人配置](#钉钉机器人配置)
- [会话管理说明](#会话管理说明)
- [常见问题](#常见问题)

---

## 飞书机器人配置

### 1. 创建飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/)
2. 登录后进入「开发者后台」
3. 点击「创建应用」，选择「企业自建应用」
4. 填写应用名称和描述，创建应用

### 2. 获取应用凭证

在应用详情页获取以下信息：

| 凭证 | 说明 | 获取位置 |
|------|------|----------|
| App ID | 应用唯一标识 | 「凭证与基础信息」页面 |
| App Secret | 应用密钥 | 「凭证与基础信息」页面 |

### 3. 配置机器人能力

1. 在应用详情页，点击「添加应用能力」
2. 找到「机器人」能力，点击启用
3. 配置机器人基本信息

### 4. 配置消息事件

1. 进入「事件与回调」配置页面
2. 添加以下事件订阅：
   - `im.message.receive_v1` - 接收消息
3. 配置请求地址（回调URL）：
   ```
   https://your-domain.com/feishu/callback
   ```

### 5. 配置权限

在「权限管理」中添加以下权限：

| 权限名称 | 权限标识 | 用途 |
|----------|----------|------|
| 获取用户信息 | contact:user.employee_id:readonly | 获取用户基本信息 |
| 获取部门信息 | contact:department:readonly | 获取部门信息 |
| 发送消息 | im:message | 发送消息 |

### 6. 环境变量配置

```bash
# 飞书配置
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=your_verification_token
FEISHU_ENCRYPT_KEY=your_encrypt_key_32_chars
```

或在 `configs/config.yaml` 中配置：

```yaml
channels:
  feishu:
    enabled: true
    app_id: "cli_xxxxxxxxxxxxxxxxxx"
    app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    verification_token: "your_verification_token"
    encrypt_key: "your_encrypt_key_32_chars"
```

---

## 企业微信机器人配置

### 1. 创建企业微信应用

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」
3. 点击「创建应用」，选择「企业内部开发」
4. 填写应用信息

### 2. 获取应用凭证

| 凭证 | 说明 | 获取位置 |
|------|------|----------|
| Corp ID | 企业ID | 「我的企业」页面 |
| Agent ID | 应用AgentID | 「应用管理」- 应用详情 |
| Secret | 应用Secret | 「应用管理」- 应用详情 |

### 3. 配置企业微信应用

1. 在应用详情页，找到「企业可信IP」配置
2. 添加服务器IP地址到可信IP列表

### 4. 配置接收消息

1. 在「API接收消息」配置中启用
2. 设置「URL」为：
   ```
   https://your-domain.com/wecom/callback
   ```
3. 设置「Token」和「EncodingAESKey」

### 5. 配置权限

在「企业微信管理后台」- 「通讯录」中，确保应用有获取成员的权限。

### 6. 环境变量配置

```bash
# 企业微信配置
WECOM_ENABLED=true
WECOM_CORP_ID=wwxxxxxxxxxxxxxxxx
WECOM_AGENT_ID=1000001
WECOM_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

或在 `configs/config.yaml` 中配置：

```yaml
channels:
  wecom:
    enabled: true
    corp_id: "wwxxxxxxxxxxxxxxxx"
    agent_id: "1000001"
    secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    token: "your_token"
    encoding_aes_key: "your_encoding_aes_key_43_chars"
```

---

## 钉钉机器人配置

### 1. 创建钉钉应用

1. 登录 [钉钉开放平台](https://open.dingtalk.com/)
2. 进入「开发者后台」
3. 点击「创建应用」，选择「企业内部开发」
4. 选择「钉钉应用」类型
5. 填写应用信息

### 2. 获取应用凭证

| 凭证 | 说明 | 获取位置 |
|------|------|----------|
| AppKey | 应用Key | 「基础信息」页面 |
| AppSecret | 应用Secret | 「基础信息」页面 |

### 3. 配置机器人能力

1. 在应用详情页，点击「添加应用能力」
2. 找到「机器人」能力，点击启用
3. 配置消息接收模式为「Stream模式」或「HTTP模式」

### 4. 配置消息事件

1. 进入「事件与回调」配置页面
2. 添加以下事件订阅：
   - `im.message.receive_v1` - 接收消息
3. 配置请求地址（回调URL）：
   ```
   https://your-domain.com/dingtalk/callback
   ```

### 5. 配置权限

在「权限管理」中添加以下权限：

| 权限名称 | 权限标识 | 用途 |
|----------|----------|------|
| 获取用户信息 | dingtalk:user:employee:readonly | 获取用户基本信息 |

### 6. 环境变量配置

```bash
# 钉钉配置
DINGTALK_ENABLED=true
DINGTALK_APP_KEY=dingxxxxxxxxxxxxxxxx
DINGTALK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DINGTALK_TOKEN=your_token
DINGTALK_ENCODING_AES_KEY=your_encoding_aes_key_43_chars
```

或在 `configs/config.yaml` 中配置：

```yaml
channels:
  dingtalk:
    enabled: true
    app_key: "dingxxxxxxxxxxxxxxxx"
    app_secret: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    token: "your_token"
    encoding_aes_key: "your_encoding_aes_key_43_chars"
```

---

## 会话管理说明

### 会话ID生成规则

每个渠道用户的会话ID格式为：`{channel_type}_{channel_user_id}`

| 渠道 | 示例会话ID |
|------|-----------|
| 飞书 | `feishu_ou_xxxxxxxxxx` |
| 企业微信 | `wecom_xxxxxxx` |
| 钉钉 | `dingtalk_xxxxxxx` |

### 会话信息存储

渠道会话信息存储在 `channel_sessions` 表中，包含以下字段：

| 字段 | 说明 |
|------|------|
| session_id | 会话唯一ID |
| channel_type | 渠道类型 |
| channel_user_id | 渠道用户ID |
| channel_chat_id | 渠道会话/群ID |
| user_id | 关联的本地用户ID |
| username | 用户名称 |
| title | 会话标题 |
| context_data | 上下文数据(JSON) |
| created_at | 创建时间 |
| updated_at | 更新时间 |
| last_message_at | 最后消息时间 |
| metadata | 额外元数据(JSON) |

### 消息存储

渠道消息存储在 `channel_messages` 表中，包含以下字段：

| 字段 | 说明 |
|------|------|
| message_id | 消息唯一ID |
| session_id | 所属会话ID |
| role | 角色(user/assistant/system) |
| content | 消息内容 |
| message_type | 消息类型(text/image/file/event) |
| attachments | 附件列表(JSON) |
| metadata | 额外元数据(JSON) |
| created_at | 创建时间 |

### 会话管理API

```bash
# 列出会话
GET /api/channels/sessions
GET /api/channels/sessions?channel_type=feishu

# 获取会话详情
GET /api/channels/sessions/{channel_type}/{channel_user_id}

# 删除会话
DELETE /api/channels/sessions/{session_id}
```

---

## 常见问题

### 1. 回调URL验证失败

**问题**：平台提示回调URL验证失败

**解决方案**：
1. 确保服务器公网可访问
2. 检查防火墙/安全组是否放行
3. 确认Token和EncodingAESKey配置正确
4. 检查签名验证逻辑

### 2. 消息发送失败

**问题**：机器人无法发送消息

**解决方案**：
1. 检查应用的发送消息权限
2. 确认access_token是否过期或正确获取
3. 检查目标用户ID是否正确
4. 查看日志中的具体错误信息

### 3. 企业微信提示"签名不匹配"

**问题**：企业微信提示签名验证失败

**解决方案**：
1. 检查EncodingAESKey是否正确配置
2. 确保Token与其他配置一致
3. 验证回调URL的格式是否正确

### 4. 飞书消息解密失败

**问题**：飞书消息解密失败

**解决方案**：
1. 确认encrypt_key配置正确（32字符）
2. 检查消息是否确实被加密
3. 验证加密库是否正确安装

### 5. 如何实现多渠道用户关联？

**问题**：同一用户可能在多个渠道都与机器人对话

**解决方案**：
1. 在 `channel_sessions` 表中通过 `user_id` 字段关联
2. 可以通过 `user_id` 查询该用户的所有渠道会话
3. 在业务逻辑中实现跨渠道用户合并

---

## 部署检查清单

在部署前，请确认以下配置都已完成：

- [ ] 服务器公网可访问（配置好域名和SSL证书）
- [ ] 各平台的应用已创建并获取凭证
- [ ] 机器人能力已启用
- [ ] 消息回调已配置并验证通过
- [ ] 权限已申请并审批通过
- [ ] 环境变量或配置文件已正确设置
- [ ] 数据库表已创建（channel_sessions, channel_messages）
- [ ] 测试发送消息确认功能正常
