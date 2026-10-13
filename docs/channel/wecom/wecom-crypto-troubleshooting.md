# 企业微信消息加解密与故障排查

> 本文档从 wecom-integration.md（已删除，2026-10-12 审计）摘取仍有效的技术章节，回调路由已按现行代码修正。
> 配置操作步骤见 [wecom_setup_verification.md](./wecom_setup_verification.md)。

## 回调路由（现行）

回调端点定义在 `src/saas/api/channel_routes.py`，每个租户渠道配置独立路由：

| 方法 | 路径 | 触发时机 |
|------|------|----------|
| **GET** | `/t/{tenant_id}/wecom/callback/{config_id}` | 企业微信后台保存回调 URL 时，验签 + 解密 echostr → 返回明文 |
| **POST** | `/t/{tenant_id}/wecom/callback/{config_id}` | 用户发消息时，验签 + 解密 → 立即返回 `"success"` → 后台异步处理 |

查询参数（GET/POST 通用）：`msg_signature`、`timestamp`、`nonce`、`echostr`（仅 GET）。

## 消息加解密说明

企业微信在配置了 EncodingAESKey 后，所有回调消息都会经过 AES-256-CBC 加密，这是生产环境的强制要求。

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
| `src/channels/wecom/crypto.py` | 加解密核心实现（WeComCrypto） |
| `src/channels/wecom/adapter.py` | 适配器（集成加解密） |
| `src/saas/api/channel_routes.py` | 回调路由（验签+解密+异步处理） |
| `src/channels/idempotency.py` | MessageDeduplicator 消息去重 |

## 消息处理架构

### 异步处理流程

```
用户发送消息 → 企业微信推送 POST /t/{tid}/wecom/callback/{config_id}
    ↓
[1] 验签 + 解密消息（< 1ms）
[2] 去重检查（< 1ms）
[3] 立即返回 "success"（满足 5 秒限制）
    ↓ (后台异步任务)
[4] 获取用户信息 + 创建会话
[5] Agent 处理消息（可能 10-60 秒）
[6] 拆分长回复（每段 ≤ 2048 字节）
[7] 通过主动消息 API 发送回复
```

企业微信要求回调在 **5 秒内**返回 HTTP 200，否则会重试（最多 3 次）。Agent 的 LLM 处理通常需要 10-60 秒，因此必须先返回 "success"，再在后台异步处理和发送回复。

### 去重机制

WeCom 的回调重试机制可能导致同一消息被推送多次。系统通过 `MessageDeduplicator` 基于 `message_id` 去重，5 分钟内相同 message_id 的消息会被跳过。

## 故障排查

### 回调 URL 验证失败

**现象**: 企业微信管理后台提示「URL 验证失败」

**排查步骤**:
1. 确认服务器公网可访问: `curl https://your-domain.com/health`
2. 检查 SSL 证书是否有效
3. 确认管理后台「渠道配置」中的 `token` 和 `encoding_aes_key` 与企业微信管理后台配置一致
4. 查看服务日志中的验签/解密错误
5. 检查 Nginx 是否正确代理到后端

### 消息发送失败

**现象**: 用户发消息后没有收到回复

**排查步骤**:
1. 检查日志中是否有 `获取 access_token 失败` 错误 → 确认管理后台「渠道配置」中的 `secret` 正确
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

## 安全注意事项

1. **HTTPS 强制**: 回调 URL 必须使用 HTTPS，自签证书不被企业微信接受
2. **密钥保护**: Token、Secret、EncodingAESKey 不应写入代码仓库，凭证加密存储在 `tenant_channel_configs` 表
3. **corp_id 验证**: 解密消息时会验证 corp_id，防止消息伪造
4. **签名验证**: 每条消息都验证签名，防止消息篡改
5. **时间戳校验**: 拒绝超过 5 分钟的回调请求，防止重放攻击
6. **日志安全**: 密钥和 token 不出现在日志输出中
7. **可信 IP**: 仅配置服务器 IP，限制 API 调用来源
