---
name: pre-sales-api
description: >
  售前咨询外部系统 API 配置加载技能。读取当前租户配置的外部系统 API 说明，
  供 LLM 了解如何把每轮对话数据推送到外部客户管理系统。使用 http_api 工具发起实际请求。
metadata:
  openclaw:
    emoji: "🔌"
    requires:
      bins: ["python"]
# 依赖的工具与 recap 任务：自定义数字员工勾选此技能时自动补全（见 subagent_definition_service.apply_skill_requirements）
requires_tools: [record_lead_capture, http_api, transfer_to_human, get_channel_user_info]
requires_recap:
  - name: external_push
    when: every_round
  - name: lead_refresh
    when: every_round
---

# 售前咨询外部系统 API 配置

## 如何使用此技能

每完成一轮问答（客户一条消息 -> 智能体回复完成）需要把本轮对话数据推送到外部客户管理系统（查重/创建/更新客户、创建跟进记录等）时：

1. 先执行以下命令加载当前租户的 API 配置：

```bash
python scripts/load_api_config.py
```

2. 脚本会返回当前租户配置的外部系统 API 说明文本（Markdown 格式）
3. 仔细阅读返回的 API 说明，了解：
   - 外部系统的 Base URL 和认证方式（如双 Token 委托登录）
   - 可用的 API 端点、请求格式、字段映射和响应格式
   - 同步业务规则（查重与建/改分流、每轮跟进记录要求、字段兜底等）
   - 客户信息 / 跟进记录的字段要求（哪些必填、哪些选填、字段英文名）
4. 需要客户渠道侧资料（昵称/头像/性别/unionid/external_userid）时，先调用 `get_channel_user_info` 工具
5. 根据说明使用 http_api 工具调用外部系统
6. URL 和 headers 中的 ${VAR_NAME} 环境变量会自动替换为实际值

## 委托登录（文档要求双 Token 时使用）

若文档要求委托登录（双 Token 鉴权），执行以下脚本获取 `client_token`（脚本自动带 Redis 缓存，token 有效期内不重复登录）：

```bash
python scripts/delegate_login.py
```

stdin 入参（JSON）：`{"login_url": "登录接口地址（以文档为准）", "mobile": "归属员工手机号", "name": "归属员工姓名", "force_refresh": false}`

- **mobile 必须取归属员工手机号**（`record_lead_capture` 成功结果中的 `assignee_phone`，或 `get_channel_user_info` 返回的 `assignee_phone`），切勿使用客户手机号或留空
- **name 为归属员工姓名**（取同源的 `assignee_name`），可选；仅当手机号在外部系统中不存在触发自动建号时使用，缺失时外部系统按"用户+手机号后4位"兜底命名
- 脚本返回 `cached=true` 表示命中缓存；业务接口返回 `Code=-99`（鉴权失效）时，带 `"force_refresh": true` 重新执行脚本
- 登录成功后无需调用登出接口，token 由缓存过期自然失效

## 数据推送流程建议

每轮问答结束后，按以下流程推送：

1. 按文档业务规则查重，确保客户在外部系统中唯一（命中则按需更新、未命中则创建）
2. 按文档创建本轮跟进记录（内容为本轮客户诉求摘要与回复要点）
3. 按文档要求同步客户状态/汇总摘要等派生字段

## 注意事项

- 每轮问答中首次需要调用外部 API 时，都要先执行脚本获取最新配置
- 字段映射以当前返回的 API 说明文档为准————不同租户、不同外部系统的字段与业务规则各不相同，切勿凭记忆硬编码字段名或应用编号
- 手机号等敏感信息仅用于接口参数，不向用户透露其他客户的信息
- 推送是后台客户管理动作，不需要向客户确认、也不要向客户提及；留资信息本身的确认按智能体提示词执行
- 如果脚本返回"未配置"，说明该租户尚未配置外部系统，跳过推送即可
- 接口响应按文档约定的成功判定执行（如 `Code=0` 才表示成功）
- 业务错误（如 `{"Code": -1, "Error": "非空内容"}`）对照文档修正参数后重试一次；重试仍失败则放弃本轮推送，不阻塞与用户的正常对话
