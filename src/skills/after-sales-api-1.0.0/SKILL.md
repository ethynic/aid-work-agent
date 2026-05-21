---
name: after-sales-api
description: >
  售后服务外部系统 API 配置加载技能。读取当前租户配置的外部系统 API 说明，
  供 LLM 了解如何调用外部售后系统的接口。使用 http_api 工具发起实际请求。
metadata:
  openclaw:
    emoji: "🔌"
    requires:
      bins: ["python"]
---

# 售后服务外部系统 API 配置

## 如何使用此技能

当你需要调用外部售后系统 API（查询订单、退货、工单等）时：

1. 先执行以下命令加载当前租户的 API 配置：

```bash
python scripts/load_api_config.py
```

2. 脚本会返回当前租户配置的外部系统 API 说明文本（Markdown 格式）
3. 仔细阅读返回的 API 说明，了解：
   - 外部系统的 Base URL 和认证方式
   - 用户身份映射策略（通常为手机号映射）
   - 可用的 API 端点、请求格式和响应格式
4. 根据说明使用 http_api 工具调用外部系统
5. URL 和 headers 中的 ${VAR_NAME} 环境变量会自动替换为实际值

## 注意事项

- 每次对话中首次需要调用外部 API 时，都要先执行脚本获取最新配置
- 调用需要用户身份的 API 时，从系统注入的 [用户身份] 区块获取手机号等信息
- 写操作（创建退货、创建工单等）前，先向用户确认信息再调用
- 如果脚本返回"未配置"，说明该租户尚未配置外部系统，使用 after_sales_action 工具代替
