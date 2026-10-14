---
name: form-filling-api
description: >
  自动填表外部系统 API 技能。读取当前租户配置的第三方系统接口文档与表单字段元数据，
  获取/验证对话用户手机号并完成委托登录，供智能体在对话主流程内通过 http_api 工具
  代填第三方系统表单（查重、创建、修改）。
metadata:
  openclaw:
    emoji: "📝"
    requires:
      bins: ["python"]
# 依赖的工具：自定义数字员工勾选此技能时自动补全（见 subagent_definition_service.apply_skill_requirements）
requires_tools: [http_api, get_channel_user_info]
---

# 自动填表外部系统 API

## 如何使用此技能

用户要求创建/修改第三方系统中的表单数据（客户信息、跟进记录等）时，按以下流程执行：

### 第一步：加载租户 API 配置

```bash
python scripts/load_api_config.py
```

脚本返回当前租户配置的外部系统 API 说明文本（Markdown 格式）。仔细阅读，了解：
- 外部系统的 Base URL 和认证方式（如双 Token 委托登录）
- 可用 module 清单、各接口的调用方式与已踩过的坑
- 业务规则（如"填完跟进记录须同步客户摘要"）

若返回"未配置"，说明该租户尚未配置外部系统，告知用户联系管理员配置，不要尝试其他写入途径。

### 第二步：确定用户手机号（写操作准入，fail-closed）

**无法确定手机号或验证未通过时，拒绝一切写操作**，仅可闲聊/引导验证。

1. 先执行（无参）：

```bash
python scripts/get_user_phone.py
```

2. 返回 `bound: true` 时直接获得已绑定手机号
3. 返回 `bound: false` 时走验证流程：
   - 请用户在对话中提供手机号（11 位数字）
   - 调用 `sms-verification` 技能发送验证码（`send` 命令）
   - 用户回复验证码后，执行：

```bash
echo '{"mobile": "用户手机号", "code": "用户输入的验证码"}' | python scripts/get_user_phone.py verify
```

   - 返回 `success: true` 即完成验证并绑定（后续对话不再需要验证）；失败时告知用户验证码错误或已过期，可重新发送
   - **验证码校验以脚本返回结果为准，不要自行判断验证码是否正确**

注意：手机号存在但委托登录报"用户不存在/未启用"时，提示用户联系管理员在第三方系统中建号。

### 第三步：委托登录（文档要求双 Token 时）

执行以下脚本获取委托会话（脚本自动带 Redis 缓存，token 有效期内不重复登录）：

```bash
echo '{"login_url": "登录接口地址（以文档为准）", "mobile": "用户手机号", "hold_token": true}' | python scripts/delegate_login.py
```

- **mobile 必须取当前对话用户本人的手机号**（第二步获得），不可用其他人的手机号
- 返回 `token_held: true` 表示 client_token 已由系统托管，**无需也无法看到 token 明文**；后续业务接口 header 中写 `${client_token}` 占位符即可，http_api 工具会自动解析
- 返回 `cached=true` 表示命中缓存；业务接口返回 `Code=-99`（鉴权失效）时，带 `"force_refresh": true` 重新执行脚本
- 登录成功后无需调用登出接口，token 由缓存过期自然失效

### 第四步：拉取表单 schema（字段元数据）

```bash
echo '{"module": "模块名（如 kehuxinxi）", "base_url": "Base URL（以文档为准）"}' | python scripts/load_form_schema.py
```

脚本返回该模块的字段元数据（英文名/中文名/控件类型/必填/枚举/填写说明）。**字段细节一律以此为准**，不要凭记忆硬编码字段名。脚本带 1 小时缓存；需强制刷新时加 `"force_refresh": true`。

schema 关键字段语义：
- `required: true` 的字段缺失时必须向用户补问
- `options` 枚举字段：写入取 `value`，补问时给用户展示 `label` 列表
- `is_system: true` 系统字段：跳过填写与回显确认
- `create_hidden: true`：新建时无需填写
- `edit_hidden: true`：修改场景不可改
- `component` 决定值格式（date 为 YYYY-MM-DD、datetime 为 YYYY-MM-DD HH:mm:ss、integer/number 为数字等，详见 API 文档）

### 第五步：查重与写入（http_api 工具）

所有业务接口为 POST + JSON，headers 必须携带：

```json
{
  "Api-Authorize-Token": "${AGENT_TOKEN}",
  "Client-Authorize-Token": "${client_token}"
}
```

两个占位符由系统自动替换为真实凭证（`${AGENT_TOKEN}` 来自租户环境变量，`${client_token}` 来自委托登录托管），**不要试图自行获取或转写凭证值**。

- 创建前按文档业务规则查重（名称/手机号），命中则使用已有记录，不重复创建
- 写入参数骨架（insert/update 的 tables 结构、字段值格式）以 API 文档为准
- 响应判定：`Code=0` 成功；`Code=-1` 业务错误（把 Error 原样转述给用户）；`Code=-99` 鉴权失效（第三步带 force_refresh 重登后重试一次）

## 注意事项

- 每个会话首次调用外部 API 前，都要先执行第一步获取最新配置；不同租户对接的外部系统各不相同，切勿跨租户硬编码字段名或接口地址
- 写操作必须先回显确认（字段清单 + 值），用户明确确认后才执行；回执必须带单据编号
- 手机号等敏感信息仅用于接口参数和向用户本人确认，不向用户透露其他客户的信息
- 接口业务错误对照文档修正参数后重试一次；仍失败则如实告知用户，不伪造成功回执
