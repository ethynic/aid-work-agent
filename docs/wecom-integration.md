# 企业微信接入智能体 - 配置手册

本文档详细介绍如何将 AID Work Agent 接入企业微信，包括管理后台配置、服务器部署、环境变量和安全注意事项。

## 目录

- [1. 概述](#1-概述)
- [2. 部署模式](#2-部署模式)
- [3. 企业微信管理后台配置](#3-企业微信管理后台配置)
- [4. 管理后台配置渠道（多租户模式）](#4-管理后台配置渠道多租户模式)
- [5. 环境变量配置（单租户模式）](#5-环境变量配置单租户模式)
- [6. config.yaml 配置](#6-configyaml-配置)
- [7. Nginx 反向代理配置](#7-nginx-反向代理配置)
- [8. 消息加解密说明](#8-消息加解密说明)
- [9. 消息处理架构](#9-消息处理架构)
- [10. 故障排查](#10-故障排查)
- [11. 安全注意事项](#11-安全注意事项)

---

## 1. 概述

### 回调地址

企业微信回调地址格式为:

```
https://<你的域名>/wecom/callback
```

系统注册了两个回调端点:

| 方法 | 路径 | 触发时机 | 说明 |
|------|------|----------|------|
| **GET** | `/wecom/callback` | 管理后台保存回调 URL 时 | 验签 + 解密 echostr → 返回明文，验证服务器有效性 |
| **POST** | `/wecom/callback` | 用户给应用发消息时 | 验签 + 解密消息 → 立即返回 `"success"` → 后台异步处理 |

**查询参数（GET/POST 通用）**:

| 参数 | 说明 |
|------|------|
| `msg_signature` | 签名，用于验证请求来源 |
| `timestamp` | 时间戳 |
| `nonce` | 随机数 |
| `echostr` | 仅 GET，加密的挑战字符串 |

**示例**:
```
GET https://your-domain.com/wecom/callback?msg_signature=xxx&timestamp=1609459200&nonce=test&echostr=encrypted_str
POST https://your-domain.com/wecom/callback?msg_signature=xxx&timestamp=1609459200&nonce=test
```

### 接入架构

```
企业微信用户 ←→ 企业微信服务器 ←(HTTPS 回调)→ 你的服务器 (AID Work Agent)
                                     ↑                    ↓
                              /wecom/callback      Agent 处理 + 主动消息 API
```

### 支持的功能

| 功能 | 说明 |
|------|------|
| 文本消息收发 | 支持中英文、表情 |
| 图片消息 | 用户发送图片，自动下载 |
| 文件消息 | 用户发送文件，自动下载 |
| 语音消息 | 语音转文字（需企业微信开启） |
| Markdown 回复 | Agent 回复支持 Markdown 格式 |
| 长消息自动拆分 | 超长回复自动分段发送 |
| 消息加解密 | AES-256-CBC 加密传输 |
| 消息去重 | 防止回调重试导致重复处理 |
| 异步处理 | Agent 处理不阻塞回调响应 |
| 多租户隔离 | 每个租户独立凭证、独立回调地址 |

---

## 2. 部署模式

系统支持两种部署模式，选择取决于你的使用场景:

### 模式对比

| | 单租户模式 | 多租户（SaaS）模式 |
|---|---|---|
| **适用场景** | 单个企业自用 | SaaS 平台服务多个企业 |
| **配置方式** | 环境变量 / config.yaml | 管理后台 API 动态配置 |
| **回调地址** | `/wecom/callback` | `/t/{tenant_id}/wecom/callback` |
| **凭证存储** | 服务器 .env 文件 | 数据库 `tenant_channel_configs` 表 |
| **启用方式** | `WECOM_ENABLED=true` | 通过管理后台配置 |
| **Agent 实例** | 全局 master_agent | 租户绑定的 agent_instance |

### 回调地址规则

| 模式 | 回调地址 | 说明 |
|------|----------|------|
| 单租户 | `https://your-domain.com/wecom/callback` | 全局配置，所有消息走同一个 Agent |
| 多租户 | `https://your-domain.com/t/{tenant_id}/wecom/callback` | 每个租户独立凭证和回调，绑定各自的 Agent 实例 |

> **tenant_id** 在管理后台创建租户时自动生成。在管理后台「渠道配置」页面可以查看完整的回调地址。

---

## 3. 企业微信管理后台配置

### 3.1 创建自建应用

1. 使用企业管理员账号登录 [企业微信管理后台](https://work.weixin.qq.com/wework_admin/frame)
2. 进入「应用管理」→「自建」→ 点击「创建应用」
3. 填写信息:
   - **应用名称**: 如「智能助手」
   - **应用logo**: 上传应用图标
   - **可见范围**: 选择可使用此应用的部门/人员
4. 点击「创建应用」

### 3.2 获取应用凭证

在应用详情页面记录以下信息:

| 凭证 | 位置 | 格式示例 |
|------|------|----------|
| **CorpID** (企业ID) | 「我的企业」→「企业信息」 | `ww4bc16c5c2e154d95` |
| **AgentId** (应用ID) | 应用详情页 | `1000017` |
| **Secret** (应用密钥) | 应用详情页 → 点击「查看」 | 32 字符字符串 | `SjhOGbTaKWYk-fA2bW2Fkm_lBAe-DjKXTyZwYh6J0HM`

> **安全提示**: Secret 是应用的核心凭证，切勿泄露。建议通过环境变量配置，不写入代码仓库。

### 3.3 配置 API 接收消息

1. 在应用详情页，找到「接收消息」→「设置 API 接收」
2. 填写配置:
   - **URL**: `https://your-domain.com/wecom/callback`
   - **Token**: 点击「随机获取」或自行设置（建议 16+ 字符）
   - **EncodingAESKey**: 点击「随机获取」（43 字符 Base64 字符串）
3. **重要**: 先在服务器配置好凭证并启动服务，再点击「保存」。WeCom 会立即发送 GET 请求验证。
4. URL 根据部署模式选择:
   - **单租户**: `https://your-domain.com/wecom/callback`
   - **多租户**: `https://your-domain.com/t/{tenant_id}/wecom/callback`（tenant_id 在管理后台查看）

### 3.4 配置可信 IP

1. 在应用详情页，找到「企业可信IP」
2. 点击「配置」，添加服务器公网 IP 地址
3. 仅配置了可信 IP 的服务器才能调用 WeCom API

### 3.5 申请通讯录权限

1. 在应用详情页，找到「权限」
2. 申请「获取成员详情」权限（用于显示用户姓名等信息）
3. 管理员审批通过后生效

---

## 4. 管理后台配置渠道（多租户模式）

多租户 SaaS 模式下，每个租户通过管理后台 Web 界面或 API 配置自己的企业微信渠道。

### 4.1 管理后台 Web 界面

1. 登录管理后台: `https://your-domain.com` → 管理员登录
2. 进入「渠道配置」页面
3. 点击「添加渠道」→ 选择「企业微信」
4. 填写凭证信息:

| 字段 | 说明 | 获取位置 |
|------|------|----------|
| **corp_id** | 企业ID | 企业微信后台「我的企业」 |
| **agent_id** | 应用AgentId | 企业微信后台「应用管理」 |
| **secret** | 应用Secret | 企业微信后台「应用详情」 |
| **token** | 回调Token | 设置 API 接收时配置 |
| **encoding_aes_key** | 回调加密密钥 | 设置 API 接收时配置 |

5. 点击「验证」确认凭证有效
6. 记录系统分配的回调地址: `https://your-domain.com/t/{tenant_id}/wecom/callback`
7. 将此回调地址填入企业微信后台「设置 API 接收」的 URL 栏

### 4.2 API 配置

也可以通过 API 直接管理:

```bash
# 获取管理员 Token
curl -X POST https://your-domain.com/api/saas/auth/login \
  -H "Content-Type: application/json" \
  -d '{"phone": "13800000000", "code": "888888"}'

# 创建渠道配置
curl -X POST https://your-domain.com/api/saas/channels \
  -H "Authorization: Bearer saas_xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_type": "wecom",
    "config": {
      "corp_id": "ww1234567890abcdef",
      "agent_id": "1000002",
      "secret": "your-secret",
      "token": "your-token",
      "encoding_aes_key": "your-43-char-base64-key"
    }
  }'

# 验证凭证
curl -X POST https://your-domain.com/api/saas/channels/{config_id}/verify \
  -H "Authorization: Bearer saas_xxx"

# 列出渠道配置
curl https://your-domain.com/api/saas/channels \
  -H "Authorization: Bearer saas_xxx"
```

### 4.3 绑定 Agent 实例

渠道配置好后，需要将一个 Agent 实例绑定到企业微信渠道:

```bash
# 创建并启动一个 Agent 实例，绑定企业微信
curl -X POST https://your-domain.com/api/saas/instances \
  -H "Authorization: Bearer saas_xxx" \
  -d '{
    "subagent_type": "general",
    "display_name": "企业微信助手",
    "bound_channel_type": "wecom"
  }'

# 启动实例
curl -X POST https://your-domain.com/api/saas/instances/{instance_id}/start \
  -H "Authorization: Bearer saas_xxx"
```

> **注意**: 一个渠道类型只能绑定一个运行中的 Agent 实例。如果已有实例绑定该渠道，新消息会路由到该实例。

---

## 5. 环境变量配置（单租户模式）

### 3.1 前置条件

- Python 3.10+
- 公网可访问的服务器（有域名 + SSL 证书）
- 已安装项目依赖 (`pip install -r requirements.txt`)

### 3.2 配置步骤

1. 复制环境变量模板:
```bash
cp .env.example .env
```

2. 编辑 `.env` 文件，填入企业微信凭证（见下一节）

3. 启动服务:
```bash
# 开发环境
python -m src.main

# 生产环境
gunicorn -c deploy/gunicorn.conf.py src.main:app
```

4. 验证服务是否启动:
```bash
curl http://localhost:8000/health
# 应返回: {"status": "ok"}
```

5. 回到企业微信管理后台，点击「保存」完成回调 URL 验证

---

### 必填变量

### 必填变量

```bash
# 启用企业微信渠道
WECOM_ENABLED=true

# 企业 ID（在「我的企业」页面获取）
WECOM_CORP_ID=ww1234567890abcdef

# 应用 ID（在应用详情页获取）
WECOM_AGENT_ID=1000002

# 应用密钥（在应用详情页获取）
WECOM_SECRET=your-32-character-secret-here

# 回调 Token（设置 API 接收时配置）
WECOM_TOKEN=your_token_here

# 回调加密密钥（设置 API 接收时配置，43 字符 Base64）
WECOM_ENCODING_AES_KEY=your-43-char-base64-encoding-aes-key
```

### 可选变量

以下变量有合理默认值，通常不需要配置:

```bash
# 默认消息类型: text 或 markdown
WECOM_MSG_TYPE=markdown

# 单条消息最大字节数（默认 2048）
WECOM_MSG_MAX_BYTES=2048

# API 调用重试次数（默认 3）
WECOM_MAX_RETRIES=3

# 每用户每分钟速率限制（默认 10）
WECOM_RATE_LIMIT=10
```

---

## 6. config.yaml 配置

也可以通过 `configs/config.yaml` 配置（环境变量优先级更高）:

```yaml
channels:
  wecom:
    enabled: true
    corp_id: "ww1234567890abcdef"
    agent_id: "1000002"
    secret: "your-secret"
    token: "your-token"
    encoding_aes_key: "your-43-char-base64-key"

    # 消息配置
    message:
      default_type: "markdown"     # 默认消息类型
      max_bytes: 2048              # 单条消息最大字节数
      split_on_paragraph: true     # 按段落拆分

    # 媒体配置
    media:
      upload_dir: "./uploads/wecom"  # 媒体文件存储目录
      max_file_size: 20971520        # 最大文件大小（20MB）

    # 速率限制
    rate_limit:
      enabled: true
      max_per_minute: 10

    # 重试配置
    retry:
      max_attempts: 3
      backoff_base: 1.0
```

---

## 7. Nginx 反向代理配置

企业微信要求回调 URL 使用 HTTPS。推荐使用 Nginx 作为反向代理:

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /etc/ssl/certs/your-domain.crt;
    ssl_certificate_key /etc/ssl/private/your-domain.key;

    # 企业微信回调（单租户）
    location /wecom/callback {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 10s;
    }

    # 租户级渠道回调（多租户）
    location ~ ^/t/([^/]+)/([\w]+)/callback$ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 10s;
    }

    # 其他 API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

---

## 8. 消息加解密说明

### 为什么要加密？

企业微信在配置了 EncodingAESKey 后，所有回调消息都会经过 AES-256-CBC 加密。这是生产环境的强制要求。

### 加密流程

```
明文 XML → [16字节随机前缀][4字节长度][消息体][corp_id] → PKCS7填充 → AES-256-CBC加密 → Base64
```

### 解密流程

```
Base64 → AES-256-CBC解密 → PKCS7去填充 → 去16字节随机前缀 → 读4字节长度 → 提取消息 → 验证corp_id
```

### 签名验证

签名算法: `SHA1(sort([token, timestamp, nonce, encrypt]))`

- GET 回调: `encrypt` = echostr 参数值
- POST 回调: `encrypt` = XML 中 `<Encrypt>` 节点值

### 关键文件

| 文件 | 说明 |
|------|------|
| `src/channels/wecom/crypto.py` | 加解密核心实现 |
| `src/channels/wecom/adapter.py` | 适配器（集成加解密） |
| `src/channels/callback.py` | 回调路由（验签+解密+异步处理） |

---

## 9. 消息处理架构

### 异步处理流程

```
用户发送消息 → 企业微信推送 POST /wecom/callback
    ↓
[1] 验签 + 解密消息（< 1ms）
[2] 去重检查（< 1ms）
[3] 立即返回 "success"（满足 5 秒限制）
    ↓ (后台 asyncio.create_task)
[4] 获取用户信息 + 创建会话
[5] Agent 处理消息（可能 10-60 秒）
[6] 拆分长回复（每段 ≤ 2048 字节）
[7] 通过主动消息 API 发送回复
```

### 为什么需要异步？

企业微信要求回调在 **5 秒内**返回 HTTP 200，否则会重试（最多 3 次）。Agent 的 LLM 处理通常需要 10-60 秒。因此必须先返回 "success"，然后在后台异步处理和发送回复。

### 去重机制

WeCom 的回调重试机制可能导致同一消息被推送多次。系统通过 `MessageDeduplicator` 基于 `message_id` 去重，5 分钟内相同 message_id 的消息会被跳过。

---

## 10. 故障排查

### 回调 URL 验证失败

**现象**: 企业微信管理后台提示「URL 验证失败」

**排查步骤**:
1. 确认服务器公网可访问: `curl https://your-domain.com/health`
2. 检查 SSL 证书是否有效
3. 确认环境变量 `WECOM_TOKEN` 和 `WECOM_ENCODING_AES_KEY` 与管理后台配置一致
4. 查看服务日志中的验签/解密错误
5. 检查 Nginx 是否正确代理到后端

### 消息发送失败

**现象**: 用户发消息后没有收到回复

**排查步骤**:
1. 检查日志中是否有 `获取 access_token 失败` 错误 → 确认 `WECOM_SECRET` 正确
2. 检查日志中是否有 `errcode=60011` → 应用没有发送消息权限
3. 检查日志中是否有 `errcode=81013` → 用户不在应用可见范围内
4. 检查可信 IP 是否已配置
5. 确认 Agent 处理是否成功（查看 `WeCom 后台消息处理失败` 日志）

### 消息重复发送

**现象**: 用户收到重复的回复

**排查步骤**:
1. 检查去重器是否正常工作
2. 确认消息 ID 唯一（查看日志中的 `重复消息，跳过` 记录）
3. 如果问题持续，检查是否有多个服务实例在运行

### Access Token 频繁刷新

**现象**: 日志中频繁出现 `获取企业微信 access_token`

**说明**: 正常行为。Token 有效期 2 小时，系统会提前 5 分钟刷新。如果多实例部署，需要使用 Redis 共享 token。

---

## 11. 安全注意事项

1. **HTTPS 强制**: 回调 URL 必须使用 HTTPS，自签证书不被企业微信接受
2. **密钥保护**: Token、Secret、EncodingAESKey 不应写入代码仓库，使用环境变量或密钥管理服务
3. **corp_id 验证**: 解密消息时会验证 corp_id，防止消息伪造
4. **签名验证**: 每条消息都验证签名，防止消息篡改
5. **时间戳校验**: 拒绝超过 5 分钟的回调请求，防止重放攻击
6. **速率限制**: 每用户每分钟最多 10 条消息，防止滥用
7. **日志安全**: 密钥和 token 不出现在日志输出中
8. **可信 IP**: 仅配置服务器 IP，限制 API 调用来源

---

## 部署检查清单

上线前请逐项确认:

- [ ] 企业微信自建应用已创建
- [ ] CorpID、AgentId、Secret 已获取
- [ ] API 接收消息已配置（URL + Token + EncodingAESKey）
- [ ] 回调 URL 验证通过
- [ ] 可信 IP 已配置
- [ ] 通讯录权限已申请并审批
- [ ] 服务器 HTTPS 证书有效
- [ ] 环境变量已正确配置
- [ ] 健康检查端点正常: `curl https://your-domain.com/health`
- [ ] 发送测试消息并收到回复
- [ ] 长消息拆分正常（发送超长文本测试）
- [ ] 日志无错误输出
