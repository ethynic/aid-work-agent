# 钉钉接入实操手册

> 本文档面向**实际接入钉钉渠道**的运维/开发同学，按步骤操作即可完成接入。

---

## 1. 前置准备

### 1.1 信息收集

接入前请确认以下信息：

| 项目 | 来源 | 备注 |
|------|------|------|
| 服务器外网域名 | 运维 | 必须 HTTPS，钉钉不接受 http |
| 租户 ID（tenant_id） | 平台管理员 | 在 SaaS 管理后台「租户管理」中查询 |
| 钉钉企业管理员账号 | 钉钉超管 | 用于登录开放平台创建应用 |
| 子智能体（subagent）名称 | 业务方 | 选填，决定该渠道默认对接的数字员工 |

### 1.2 域名与端口

钉钉回调对网络要求：

- **协议**：HTTPS（自签证书不行，需要可公开验证的证书）
- **端口**：必须是 `443`（钉钉不允许自定义端口）
- **路径**：`/t/{tenant_id}/dingtalk/callback/{config_id}/{config_id}`

完整回调 URL 示例：
```
https://agent2.aidingyi.cn/t/tenant_b6459319f621/dingtalk/callback/chan_719d16d6ac84
```

## 2. 钉钉开放平台：创建企业内部应用

### 2.1 进入开发者后台

1. 访问 <https://open-dev.dingtalk.com/>
2. 使用钉钉企业管理员账号登录
3. 顶部切换到目标企业

### 2.2 创建应用

在「应用开发」→「企业内部开发」点击「创建应用」：

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/)
2. 进入「应用开发」→「企业内部应用」→「创建应用」
3. 记录 `AppKey` 和 `AppSecret`


### 2.3 启用「机器人」能力

进入左侧「应用能力」→「机器人」：

1. 点击「开通」
2. **机器人名称**：与应用同名即可（用户在 @ 时显示）
3. **机器人描述**：自定义
4. **机器人头像**：建议上传
5. **消息接收模式**：选「**HTTP 模式**」（不要选 Stream 模式）
6. **消息接收地址**：先**留空**或填占位 URL（稍后回填）
7. 保存


### 2.4 配置应用权限

进入左侧「权限管理」，添加以下权限（如未默认开启）：

| 权限名 | 用途 |
|--------|------|
| `Contact.User.Read` | 查询用户信息（可选） |
| `qyapi_chat_robot_send` | 机器人发消息 |
| `qyapi_robot_sendmsg` | 发送机器人消息 |

不同钉钉版本权限名可能略有差异，凡涉及 **机器人发消息 / 文件上传** 的都开启。

### 2.5 应用版本管理

在「版本管理与发布」页面创建一个**测试版本**（开发版本即可），后续每次配置变更都需要重新创建版本。

---

## 3. 系统侧：在管理后台配置渠道

### 3.1 登录管理后台

以**租户管理员**或**平台管理员**身份登录：

```
https://your-domain.com/t/{tenant_id}/saas/channels
```

### 3.2 新增钉钉渠道

点击「新增渠道」，填写：

| 字段 | 值 | 说明 |
|------|---|------|
| 渠道类型 | **钉钉** | 下拉选择 |
| 渠道名称 | 自定义（如 `公司钉钉`） | 仅作展示 |
| App Key | 步骤 2.2 记录的 AppKey | |
| App Secret | 步骤 2.2 记录的 AppSecret | |
| 关联数字员工 | 业务选 | 可选 |
| 欢迎消息 | 自定义 | 可选 |

> 💡 **关于 Token / EncodingAESKey**：表单为了与企微/飞书统一保留了这两个字段，钉钉**不使用**它们。留空即可，填了也不报错（系统会忽略）。

点击「保存」，系统会：
1. 在 `tenant_channel_configs` 表中插入一条 `channel_type=dingtalk` 记录
2. 在页面下方显示**回调 URL**，形如：
   ```
   https://your-domain.com/t/{tenant_id}/dingtalk/callback/{config_id}
   ```
3. 复制此 URL，下一步要用

---

## 4. 回填回调地址并发布

### 4.1 回填回调 URL

回到钉钉开发者后台「应用能力」→「机器人」：

1. **消息接收地址**：粘贴步骤 3.2 复制的回调 URL
2. 点击「保存」
3. 钉钉会**立即向该地址发起一次测试请求**，本系统的 GET `/t/{tenant_id}/dingtalk/callback/{config_id}/{config_id}` 会返回 `200 OK`

如果保存失败，钉钉会提示错误码：
- `URL 不可访问` → 检查 HTTPS 证书、域名解析、防火墙
- `URL 验证失败` → 检查路由是否正确挂载、是否有反代/WAF 拦截

### 4.2 创建并发布版本

进入「版本管理与发布」：

1. 点击「创建新版本」
2. 版本号：`0.0.1`（或递增）
3. 应用范围：选「**仅本人可用**」（联调阶段）或「**指定部门/人员**」
4. 提交发布

> ⚠️ **重要**：钉钉企业内部应用必须**发布版本**才能被使用。新创建的应用如果不发版，机器人 @ 不到、回调收不到消息。

### 4.3 添加机器人到群（仅群聊场景）

如果要在群里使用：

1. 进入目标群聊 → 群设置 → 「机器人」
2. 添加自建机器人 → 选择刚才创建的应用
3. 群成员 @ 机器人即可触发回调

单聊场景无需此步骤，授权范围内的用户直接搜索机器人即可对话。

---

## 5. 联调验证

### 5.1 单聊测试

1. 在钉钉客户端搜索机器人名称（步骤 2.3 设置的）
2. 发送一条消息，如 `你好`
3. 后端日志（loguru）应输出：
   ```
   [Tenant DingTalk] POST 处理 ... msgId=xxxx
   [DingTalk] access_token 获取成功
   [DingTalk] 消息发送成功: conversation_type=1, target=xxx, msg_key=sampleText
   ```
4. 钉钉客户端应收到智能体的回复

### 5.2 群聊测试

1. 在已添加机器人的群里 @机器人 + 文本，如 `@AID智能助手 帮我查一下…`
2. 后端日志应包含 `conversation_type=2` 字样
3. 机器人在群里回复

### 5.3 验证签名校验

钉钉每次回调都会带 `timestamp` 和 `sign` 请求头，本系统会：

1. 从请求头读取 `timestamp` 和 `sign`
2. 用 `app_secret` 计算 `base64(HmacSHA256(timestamp + "\n" + app_secret, app_secret))`
3. 用 `hmac.compare_digest` 比对
4. 失败返回 `403`

可手动构造一个错误签名的请求测试：

```bash
curl -X POST https://your-domain.com/t/{tenant_id}/dingtalk/callback/{config_id} \
  -H "Content-Type: application/json" \
  -H "timestamp: $(date +%s%3N)" \
  -H "sign: WRONG_SIGN" \
  -d '{"msgtype":"text","text":{"content":"hi"},"msgId":"manual_test_001","conversationType":"1","senderId":"u1"}'

# 预期返回：
# {"success":false,"msg":"invalid signature"}  HTTP 403
```

### 5.4 验证消息去重

短时间内重复发送同一 `msgId`（钉钉不会，但可手动测试），应只触发一次后台处理。这个由 `MessageDeduplicator` 保证（5 分钟 TTL）。

### 5.5 自动化测试

服务端已配套以下测试，部署前可全量跑一次：

```bash
# 单元测试（112 用例）
pytest tests/unit/channels/test_dingtalk_*.py -v

# 集成测试（覆盖签名/去重/单群聊分流/错误处理）
pytest tests/integration/test_dingtalk_routes.py -v
```

---

## 6. 常见问题排查

### 6.1 钉钉后台保存回调 URL 失败

| 现象 | 原因 | 解决 |
|------|------|------|
| `URL 不可访问` | 公网无法访问 / 防火墙拦截 | 用 `curl -I https://your-domain.com/t/xxx/dingtalk/callback/chan_xxx` 自查 |
| `证书校验失败` | 自签 / 已过期 | 使用 Let's Encrypt 等正规证书 |
| `URL 验证失败` | 返回了非 2xx | 检查路由日志，确认请求到达后端 |

### 6.2 收不到回调消息

依次排查：

1. **应用是否发版？** 钉钉机器人不发版收不到消息
2. **机器人是否被加入到群？**（群聊场景）
3. **回调 URL 是否正确？** 域名、tenant_id 拼写
4. **HTTPS 是否正常？** `curl -v` 确认握手成功
5. **后端是否启动？** `journalctl` / docker logs 查看

### 6.3 签名验证一直失败

最常见原因：

| 原因 | 排查 |
|------|------|
| AppSecret 复制错（多/少字符） | 重新到「凭证与基础信息」页面复制 |
| AppSecret 已重置但配置未更新 | 比对页面显示值与 DB 中 `tenant_channel_configs.config` |
| 服务器时间偏差大于 1 小时 | `date` 命令对比，必要时安装 chrony/ntpd |
| 反代修改了请求头 | nginx 配置需要 `proxy_pass_request_headers on;` |

### 6.4 消息能收到但回复发不出

排查 `_send_with_retry` 报错：

| 错误 | 原因 | 解决 |
|------|------|------|
| `errcode=40001/40002/40014` | access_token 过期/无效 | 系统会自动重新刷新；持续失败检查 AppKey/AppSecret |
| `errcode=400xxx 权限不足` | 应用缺少机器人发消息权限 | 步骤 2.4 重新检查权限 |
| `用户不在可用范围` | 应用版本未对当前用户发布 | 步骤 4.2 调整可见范围 |
| 网络超时 | DNS / 出口防火墙 | 测试 `curl https://api.dingtalk.com` |

### 6.5 群聊回复发到了错误的群

确认逻辑：

- 单聊：`reply_target = senderId`（即 userId）
- 群聊：`reply_target = openConversationId`（即 conversationId）

由 `_process_tenant_dingtalk_background` 中的：
```python
reply_target = conversation_id if conversation_type == "2" else message.user_id
```
保证。如果还是错，检查回调 JSON 中的 `conversationType` 字段是否为字符串 `"1"` / `"2"`。

### 6.6 长消息被截断或乱发

长消息策略：

- 单条 ≤ 4000 字节：直接发
- 单条 > 4000 字节：按段落（`\n\n`）→ 行（`\n`）→ 字节 三级拆分
- 每条之间 sleep 0.5s（避免触发钉钉限流）

如果业务消息被错误拆分，可调整：
```python
DingTalkAdapter(
    ...,
    max_bytes=8000,            # 提升单条字节上限
    split_on_paragraph=False,  # 改为按行拆
)
```

### 6.7 速率限制告警

日志：`[DingTalk] 速率限制已触发: user=xxx`

每用户默认 60 秒内最多 10 条。调整：
```python
DingTalkAdapter(
    ...,
    rate_limit_window=60,
    rate_limit_max=20,
)
```

---

## 附录：快速校验清单

部署上线前过一遍：

### 钉钉开放平台
- [ ] 已创建企业内部应用
- [ ] 已开通「机器人」能力，消息接收模式选 **HTTP 模式**
- [ ] 已配置消息接收地址（回调 URL）
- [ ] 已添加权限（机器人发消息相关）
- [ ] 已创建并发布版本
- [ ] AppKey、AppSecret 已记录

### 系统侧
- [ ] 服务器有 HTTPS、443 端口、稳定域名
- [ ] 管理后台「渠道配置」中已新增钉钉渠道
- [ ] AppKey / AppSecret 已正确填入
- [ ] 数据库 `tenant_channel_configs` 中能查到该记录

### 联调
- [ ] `pytest tests/unit/channels/test_dingtalk_*.py` 全部通过
- [ ] `pytest tests/integration/test_dingtalk_routes.py` 全部通过
- [ ] 钉钉后台保存回调 URL 时收到 200
- [ ] 单聊：用户发消息能收到机器人回复
- [ ] 群聊：@机器人能收到群内回复
- [ ] 后端日志 `[Tenant DingTalk]` / `[DingTalk]` 没有 ERROR

### 安全
- [ ] AppSecret 未写入 Git
- [ ] 回调 URL 仅通过 HTTPS 访问
- [ ] 服务器时间已与 NTP 同步
- [ ] 数据库 `tenant_channel_configs.config` 字段已加密存储（如启用了字段级加密）

---

## 附录：关键文件索引

| 用途 | 文件 |
|------|------|
| 适配器入口 | `src/channels/dingtalk/adapter.py` |
| 签名验证 | `src/channels/dingtalk/crypto.py` |
| 消息构建 | `src/channels/dingtalk/message_builder.py` |
| 媒体下载/上传 | `src/channels/dingtalk/media.py` |
| 回调路由 | `src/saas/api/channel_routes.py:838-1080` |
| 工厂注册 | `src/saas/services/channel_factory.py:26` |
| 配置字段定义 | `src/saas/api/channel_config.py:49-54` |
| 前端配置页 | `frontend/src/components/saas/ChannelConfig.vue` |
| 单元测试 | `tests/unit/channels/test_dingtalk_*.py` |
| 集成测试 | `tests/integration/test_dingtalk_routes.py` |
| 设计文档 | `docs/channel/dingtalk/integration_guide.md` |
| 实施计划 | `docs/channel/dingtalk/implementation_plan.md` |
