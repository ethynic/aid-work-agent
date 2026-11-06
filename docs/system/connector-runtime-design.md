# 连接器运行时设计：统一工具入口与三种转接器

> **文档类型**：架构设计（连接器运行平面）
> **状态**：📋 设计完成，待开发（Phase 2 立项时出开发计划；四项决策已于 2026-10-28 全部与用户定稿）
> **创建日期**：2026-10-28
> **关联**：[连接中心架构设计](connection-center-design.md)（管理平面，2026-07-30，两者配套：连接中心管"配置/凭据/页面"，本文管"智能体怎么调用"）、[飞书连接器调研](../research/connector-architecture-feishu-research.md)（2026-10-27，选型依据）
> **本文由来**：2026-10-27~28 与用户的设计辩论结论。用户初始思路为"一切皆 MCP client + 超集契约"，经辩论修正为"统一工具入口 + 三种转接器 + 扩展字段规范"，四个决策点结论见 §0。

---

## 0. 已拍板的决策（2026-10-28，与用户讨论确定）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 统一层选型 | **统一工具入口 + 三种转接器**（native / 文档驱动 / MCP），不采用"一切皆 MCP"。智能体只看到一种工具形状；接外部服务的"那根线"按情况选最省事的接法 |
| 2 | 分期与路线 | **飞书用 native（lark-oapi SDK 直连）先行**；文档驱动转接器**基于现有 http_api 工具 + 租户 API 文档做增强评估，不另造**；MCP 转接器最后做 |
| 3 | 扩展字段命名 | 自定义字段统一 **`x-aid-` 前缀**，挂在标准扩展位（`_meta` / 工具 `annotations`）；标准端忽略我们的字段后功能不残缺 |
| 4 | 外部 MCP 能力门 | 对外部 MCP server 的 sampling / elicitation 能力**默认关闭**（已确认，2026-10-28）。基本定位：**连接外部服务是让他们向我们提供服务，不允许他们反过来要我们提供服务**（单向供给），解释见 §5.4 |

---

## 1. 一句话架构

智能体 → 现有 ToolExecutor（权限/租户上下文/审计，零新造）→ **统一形状的连接器工具** → 三种转接器之一 → 外部系统。

```
                    ┌─────────────────────────────────────────────┐
                    │  智能体（主 Agent / 子智能体）                 │
                    │  只看到统一工具清单（名称/说明/参数表）          │
                    └──────────────────┬──────────────────────────┘
                                       │ 现有 ToolRegistry / ToolExecutor
                                       │ （权限、scope、租户上下文、审计）
                    ┌──────────────────▼──────────────────────────┐
                    │  连接器工具层（统一契约，§2）                  │
                    └───────┬───────────────┬───────────────┬─────┘
                            │               │               │
                    ① native 转接器   ② 文档驱动转接器   ③ MCP 转接器
                    （自写代码直连）   （http_api+API文档） （对接外部MCP服务）
                        飞书 SDK          客户内部系统       外部 MCP server
```

统一发生在**智能体看到的那一层**；转接器内部怎么接（进程内直调 / HTTP / MCP 协议）对智能体不可见。这是 Dify/Coze/n8n/LangChain 的共同做法，不是我们标新立异。

---

## 2. 统一工具契约（智能体看到什么）

### 2.1 落地方式：复用现有工具体系，零新造派发层

连接器工具编译为 `BaseTool` 子类（`src/tools/base.py` 的 `__init_subclass__` 自动注册），进现有 `ToolRegistry` → `ToolExecutor` → `assembly.py` 装配。权限检查、`ToolExecutionContext`（tenant_id/user_id/env_vars）、scope 过滤全部白得。MCP 的工具形状（name/description/inputSchema/结构化结果）作为规范交换形状——`BaseTool` 的 Pydantic→JSON Schema 已天然兼容。

### 2.2 结果契约（吸取本仓库既有教训）

| 规则 | 依据 |
|------|------|
| 成功/失败必须结构化分明（失败带错误码、原因、建议动作），禁止"返回错误字符串但派发器记成功" | B2B 调研 P0 教训（write/read 失败仍 success=true） |
| 大结果必须分页或落盘（复用 `src/tools/_spill.py` 落盘机制，http_api/pdf 已有先例），返回截断预览 + 续读方式 | 搜索调研 12K 截断丢中间内容的教训 |
| 写操作带确认语义：write 级工具的说明与参数设计要求模型先给出目标确认 | 产品"稳定性优先"原则；WorkBuddy 同款做法 |

### 2.3 工具分级元数据（`x-aid-*`，见 §4）

每个连接器工具声明：`x-aid-write-level`（read / write）、`x-aid-audit`（是否强制审计）、`x-aid-rate-limit` 等，经 `_meta`/annotations 携带，标准端可忽略。

---

## 3. 三种转接器

### 3.1 native 转接器（第一优先：飞书）

自写代码直连，跑在自己进程里。飞书按[调研结论](../research/connector-architecture-feishu-research.md)执行：

- 接入路径 B（每租户自建应用填凭证），凭证入 `tenant_connector_configs` + `secret_crypto` 加密（**不沿用 `tenant_channel_configs` 飞书明文做法**）；
- 双 token：tenant_access_token（应用身份：群通知/多维表格/审批）+ user_access_token（用户身份：读个人有权文档/日历，OAuth 授权码流程 + refresh 管理）；
- 一期工具面约 10 个（读文档/知识库/群消息/表格/日历/通讯录 + 发消息/写表格/建日程/审批）；
- 每连接器频控（单应用几十~200 QPS 量级）。

其他 native 连接器（连接中心设计 Phase 2/3 清单：企微、钉钉、企查查、高德等）复用同一模式复制。

### 3.2 文档驱动转接器（基于现有 http_api，不另造）

**现状盘点（2026-10-28 代码核实）**——用户判断正确，基础已非常扎实：

| 已具备 | 位置 |
|--------|------|
| `http_api` 工具（通用 HTTP 调用） | `src/tools/network/http_api.py` |
| 调用审计日志（单行 JSON 落 `log/agent/http_api_audit_YYYYMMDD.log`） | 同上 L600 |
| `${VAR}` 环境变量注入（ToolExecutionContext 按租户隔离） | `src/tools/context.py` |
| 大结果落盘防截断 | `src/tools/_spill.py`（#34 Phase 3b） |
| 租户 API 文档驱动范式（api-meta 块声明 login_url/token header 等，LLM 读文档自主调接口） | `src/services/recap/tasks/external_push.py`（已验证）+ 连接中心 Tab 1 API 配置上传（已规划） |

**增强评估点**（对应用户"评估是否可以更高效简洁地使用 http_api"，Phase 3 立项时逐项评估，不做预设结论）：

1. 文档模板标准化：api-meta 块固定 schema，保存时校验（现在自由 Markdown，模型理解质量依赖文档质量）；
2. 配置时预编译：把文档要点在保存时编译成精简工具说明注入模型上下文，替代运行时现读长文档（省 token、更稳）；
3. 凭据从 `tenant_connector_configs` 统一注入（替代分散在 `subagent_env_vars`）；
4. 测试连接：对文档首个接口发最小请求验证（连接中心"测试连接"按钮）；
5. 危险操作分级：文档中声明 write 级接口清单，套用 §2.2 确认语义。

### 3.3 MCP 转接器（最后做）

对接**外部** MCP server（第三方提供的标准 MCP 服务）：

- 传输优先 streamable-http（远程无状态，服务端多租户友好）；stdio 子进程仅限单租户/桌面场景，服务端**不**按租户起子进程；
- 每租户凭据按调用注入（Authorization header 带 OAuth token），凭据仍入 `tenant_connector_configs`；
- 外部 server 的工具清单经转接器编译为内部 BaseTool（统一形状），工具元数据（read/write 分级）缺失时**默认按 write 处理**（保守）；
- 能力门见 §5.4。

---

## 4. 连接器说明书（manifest）与扩展字段规范

### 4.1 manifest 内容（存 `tenant_connector_configs` 的 config_json + 代码内置定义）

连接器类型（builtin_native / doc_api / mcp_remote）、显示信息、配置 schema（JSON Schema，前端动态表单，连接中心已有设计）、auth 声明（none / api_key / oauth2——授权地址、token 地址、scope、凭据字段映射）、工具白名单/黑名单、版本号。manifest 是"超集"所在：比"一个 MCP server URL"丰富，但运行时编译成标准形状，协议层不分叉。

### 4.2 `x-aid-` 扩展规范（决策点 3）

1. 所有自定义字段以 `x-aid-` 前缀命名（如 `x-aid-write-level`），MCP 场景挂 `_meta` 或工具 `annotations`（标准已有 annotations: readOnlyHint/destructiveHint，我们扩展不覆盖标准字段）；
2. **兼容铁律**：任何标准端（标准 MCP 客户端/服务器）忽略全部 `x-aid-` 字段后，功能不残缺，降级路径明确（如 write-level 丢失→默认按保守处理）；
3. 命名规范写入 `.claude/rules/backend_dev.md` 或开发计划 checklist，禁止各连接器随手自定义字段名；
4. skill 不并入本契约（skill 的渐进披露+文件资产特性与工具不同物种），保持独立、经现有桥接互通（`src/mcp` 已把 skill 暴露为工具）。

---

## 5. 安全与运营策略

### 5.1 凭据
全部走 `tenant_connector_configs` + `src/core/secret_crypto.py`（Fernet）；列表查询掩码返回；日志脱敏复用 `sanitize_error_info`。

### 5.2 审计
所有连接器调用记录：租户/用户/连接器/工具/动作/目标资源/结果摘要/耗时。native 复用 ToolExecutor 审计路径，http_api 已有审计日志，MCP 转接器在 client 层记。

### 5.3 频控与结果大小
每连接器限速（按外部系统频控倒推）；单次结果大小上限 + 落盘（§2.2）。

### 5.4 外部 MCP 能力门（决策点 4，大白话版）

MCP 协议是**双向**的：不只是我们调对方，标准允许对方服务器反过来向我们发两种请求——

1. **借 AI（sampling）**：对方发一段内容说"请用你们的 AI 帮我处理，结果给我"。设计初衷是给本地无 AI 能力的小工具借宿主的模型；但对接不可信远程服务时 = 对方可以无限次烧我们账上的模型钱，且其内容可能藏套取系统信息的提示。
2. **问用户要信息（elicitation）**：对方说"请向你的用户问 XX"。设计初衷是流程补输入；但不可信服务可借我们产品界面钓鱼（诱导用户填敏感信息）。

**策略（2026-10-28 用户确认）**：连接器的基本定位是**单向供给**——外部服务向我们提供能力，不允许外部服务反过来要我们提供服务。两个开关出厂默认关闭；仅对逐个审核过的可信服务开启，开启需平台级配置（不允许租户自行开启）。

### 5.5 写操作确认
write 级工具的目标确认语义（§2.2）；对外可见行为（发群、写表格、发审批）宁可多一步确认，与产品"稳定性、可预测性优先"一致。

---

## 6. 分期（与连接中心设计对齐）

| 阶段 | 内容 | 前置 |
|------|------|------|
| Phase 2（连接中心原设计） | 连接器框架（BaseConnector/manifest/凭据库/测试连接）+ **飞书 native 转接器** + 前端内置连接器 Tab | 本文定稿 |
| Phase 3 | 文档驱动转接器增强（§3.2 五个评估点立项评估后实施） | Phase 2 框架落地 |
| Phase 4 | MCP 转接器（外部 MCP server 接入 + 能力门） | Phase 2 框架落地 |

---

## 7. 开放问题

1. 飞书 user_access_token 的 OAuth 回调路由设计（公网可达，与渠道 callback 同域复用）——Phase 2 开发计划时细化；
3. `tenant_channel_configs` 中飞书 app_secret 明文迁移加密——独立小任务，另行立项；
4. 文档驱动"配置时预编译"是否值得做——Phase 3 评估，若 http_api 运行时读文档效果已够好则不预编译。
