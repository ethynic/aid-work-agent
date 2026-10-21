# 客户端构建与安装入口

## Runtime 与 BOSS 插件

**当前唯一打包流程：[Runtime 与 BOSS 插件构建及 ZIP 交付手册](../docs/ops/runtime-boss-release-guide.md)。**

手册覆盖 Windows 构建环境、组件版本、原生共享依赖、固定 Node、BOSS 签名插件、历史公钥登记、Host tgz、Runtime 安装器、验包、说明与摘要以及最终 ZIP。最终交付统一放在 `clients/release/`，仓库根 `release/` 不作为交付目录。

当前 Runtime 和 BOSS 是独立安装的组件：组合 ZIP 内有 Runtime EXE 和 BOSS `.aidplugin.zip`；用户先安装 Runtime，再在其界面导入插件。安装 Runtime 不会自动升级 BOSS。首次配对后自动复用服务器和设备身份，视觉调用无需用户额外配置 token。

| 需求 | 入口 |
|---|---|
| 从源码生成新的 Runtime + BOSS ZIP | [完整出包步骤](../docs/ops/runtime-boss-release-guide.md) |
| 收件人安装或升级 | 使用本次 ZIP 内的“先读我.md”；[说明生成及安装顺序](../docs/ops/runtime-boss-release-guide.md#8-复制交付文件说明和源码状态) |
| 完整桌面对话客户端（非独立 Runtime） | [桌面客户端构建手册](../docs/system/desktop-agent-client-build-manual.md) |
| 插件容器、签名及其他 Provider 资产 | [插件打包说明](runtime-plugin-packaging/README.md) |
| 共同契约及实际完成范围 | [集成契约](../docs/system/runner-desktop-runtime-integration-contract.md)、[Runtime 计划](../docs/plans/plan-runtime-plugin-host.md) |

## 历史流程

原本文的 `bash clients/pack.sh <版本>`、Runtime 内嵌 BOSS 的全局 npm tgz、计划任务安装方式适用于旧交付；当前 `clients/pack.sh` 已在修改文件前拒绝独立插件布局，不再推荐执行。历史文件、版本记录及已交付包保留原意，不作为当前构建步骤。旧部署内容可通过 Git 历史查阅；不要把历史 `clients/release/安装手册.md` 当作新 Runtime 的安装说明。

## 九、客户端计费接入规范（三模式，新客户端必读）

> 下列激活/ClientBinding 说明面向相应独立客户端接入，不是 Runtime/BOSS 的安装步骤。Runtime/BOSS 使用已有 Device 配对身份，不要求用户再取得独立 API token；其当前流程见本文顶部链接。

> 设计总纲：[docs/design/billing/client-billing-integration-design.md](../docs/design/billing/client-billing-integration-design.md)
> 铁律：**客户端永远不上报金额**。金额由服务端按价目表/用量计算，客户端只产生"事实"。

### 1. 选模式

| 模式 | 适用场景 | 计费方式 | 需要开发的工作 |
|------|---------|---------|--------------|
| A 代理模式 | 客户端需要 LLM 能力 | 调用经服务端代理，按 token 计费 | 走 `POST /api/client/v1/llm/chat`（协会信息收集客户端同款） |
| B 动作模式 | 客户端执行**服务端下发**的命令 | 按价目表对成功命令计费 | 实现 MCP Provider 接入 agent-tool-runtime（boss cli 同款），价目配 `boss_tool_billing` 同款配置 |
| C 上报模式 | 客户端**本地自主执行**，不经云端下发、不经服务端代理 | 客户端报事实，服务端按 `client_usage_report` 价目表计费 | 见下方接口契约 |

接入流程（三种模式通用前置）：找管理员拿激活码 → `POST /api/client/v1/activate` 换 `access_token`（绑定机器）→ 之后所有请求带 `Authorization: Bearer <token>` → 计费数据自动出现在租户计费页面与管理后台，无需前端改动。

### 2. C 模式接口契约（`POST /api/client/v1/usage/report`）

请求（单条对象与 `{"reports": [...]}` 批量（≤100 条）二选一）：

```json
{
  "client_ref_id": "uuid-v4（幂等键，8-100 字符，网络重试复用同一个）",
  "command": "weixin_add_friend",
  "kind": "action",
  "quantity": 1,
  "arguments_summary": {"target": "张三"},
  "session_id": "客户端本地会话标识（可选）",
  "occurred_at": "2026-09-01T12:00:00+08:00（可选，仅存档）",
  "detail": {"任意事实字段（可选）"}
}
```

- **payload 不含金额字段**，传了也会被忽略；金额 = 服务端价目单价 × quantity（十进制 ceil 到分）
- 价目配置在服务端 `configs/config.yaml` 的 `client_usage_report` 节（匹配优先级 `client名:命令` > `命令` > default），**大小写敏感精确匹配**，未列名命令按 default 计费（default 默认 0=免费——管理员配价时建议给地板价，防客户端换名命令绕费），改价重启生效
- `kind` 枚举：`action`（默认）/ `llm` / `custom`
- 响应 envelope：`{success, accepted, failed, results}`；`success` 仅在全部条目成功时为 true，部分失败看逐条 `results`
- `results` 每条两种形状：成功 `{client_ref_id, success: true, duplicate, credit_cost, balance_after}`；
  失败 `{client_ref_id, success: false, error: "RECORD_FAILED"}`（可原样重试，复用同一 client_ref_id）
- `duplicate=true`（此前已上报过）与 0 元条目的 `balance_after` 为 `null`，表示"本次调用未动余额"，对账以 `credit_cost` 为准
- 截断规则：`arguments_summary` 序列化 >1000 字符截断；`detail` 整体 >1500 字符整块丢弃替换为 `{"_truncated": ...}`；`command/quantity/user_id` 等计费事实键以服务端为准，客户端传同名键无效
- **端点默认关闭**（`client_usage_report.enabled: false`）：未开启时请求返回 HTTP 404 `{"detail": "USAGE_REPORT_DISABLED"}`（鉴权仍先行：无 token 是 401）。接入前联系管理员开启并配价
- 网络失败本地重试必须复用同一 `client_ref_id`；服务端不可达时本地排队补报即可，不会丢账（只要最终报上来）
- 余额不足不阻断上报（动作已发生），余额走负后由充值/提醒机制兜底

### 3. 已接入客户端

| 客户端 | 模式 | 台账 stage |
|--------|------|-----------|
| association-client（协会信息收集） | A 代理 | `official_profile` / `search_profile` / `wechat_*` 等 |
| boss cli（招聘） | B 动作 | `boss_tool` |
| （预留）本地自主执行客户端 | C 上报 | `<client_name>_<kind>` |
