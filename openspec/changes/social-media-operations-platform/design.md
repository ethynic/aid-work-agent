# 设计说明

## 开发条件评估

设计文档已具备可开发条件：领域模型、状态机、连接器协议、API 草案和前端页面边界均已明确。仍不具备“完整真实平台闭环验收”条件，原因是微信公众号真实权限、视频号官方能力、指标接口范围和部分 RBAC 策略仍待 Phase 0 验证。

因此本次先落地稳定核心：

- 核心表结构和租户隔离；
- 平台连接器协议和能力声明；
- 账号、计划、内容版本、审核、发布任务和看板 API；
- 前端聚合工作台；
- 子智能体入口和业务页面登记。

真实微信公众号发布客户端、视频号数据导入解析和发布调度器在 Phase 5+ 继续实现，未验证前不得伪造成功状态。

## 前后端契约

统一响应：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "debug": null
}
```

账号只返回 `credential_mask`、`status` 和 `capabilities`，不返回密文或明文凭证。

能力值采用小写字符串，与数据库和前端一致：

- `account_credentials`
- `remote_draft`
- `api_publish`
- `assisted_publish`
- `scheduled_publish`
- `publish_status`
- `api_analytics`
- `data_import`

发布任务状态区分：

- `submitted`：平台已受理，不等于已发布；
- `published`：API 回查确认发布；
- `ready_for_manual_publish`：发布包已生成，待人工发布；
- `manually_confirmed`：人工确认，不能显示为 API 发布成功；
- `status_unknown`：外部结果未知，需要人工处理。

## 连接器边界

核心服务只读取账号能力并创建不可变发布快照。真实平台 HTTP 调用只允许放入连接器或发布执行器，不能写入 API 路由。

## 安全

- 凭证入库前加密；
- API 响应只返回掩码；
- 错误 debug 经过敏感字段过滤；
- DAO 查询必须带 `tenant_id`。
