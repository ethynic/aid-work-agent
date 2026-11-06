# 连接器架构调研：WorkBuddy / 豆包工作 / 灵光，与飞书连接器实现路径

> 日期：2026-10-27；范围：三款国内 AI 工作助手的连接器产品形态与底层机制、飞书开放平台官方基础设施、与本仓库《连接中心》既有设计的对齐、飞书第一连接器的实现建议。
> 关联：[连接中心架构设计](../system/connection-center-design.md)（2026-07-30，📋 待开发，本文为其 Phase 2 飞书连接器提供输入）；[ideas.md](../ideas.md) 系统功能 #48。
> 证据边界：标注 **实据** 的内容有一手来源（官方文档/官方仓库/实测文章，均附 URL）；**推断** 为合理推测；**建议** 为本文观点。WorkBuddy/豆包工作/灵光的内部实现均无开源代码或官方技术白皮书，涉及"底层如何实现"的部分只能是实据+推断的组合，本文明确区分。

---

## 1. 结论摘要（先回答四个核心问题）

1. **这类产品的连接器底层架构是什么？** WorkBuddy 官方文档明确其连接器技术形态为 **「MCP + CLI（标准化协议）」与「Skill + CLI（内置脚本）」两种**，授权走 **OAuth（扫码/登录跳转）或 API Key 双轨**，凭证（Access/Refresh Token）由产品代管；豆包工作把飞书做成**平台内置能力**（字节集团整合），连接器只对接外部生态（企微/钉钉/百度网盘等 200+），底层官方未确认（第三方文章称基于 MCP）；灵光（蚂蚁集团出品，注意不是阿里）是 C 端账号授权连接（支付宝/钉钉/飞书/滴滴/米家），技术细节完全未公开。行业共识方向：连接器 = **凭证托管 + OAuth 授权 + 把第三方 API 封装成 Agent 可调用的工具**，暴露形态可以是原生工具、MCP 或 CLI（ChatGPT Connectors 已收敛到 remote MCP + OAuth 2.1）。
2. **飞书连接器是不是通过飞书开源的命令行工具实现的？** 飞书确实开源了官方 CLI：**`larksuite/cli`（2026-03 开源，MIT，17.9k stars，活跃维护，含 26 个 Agent Skills 与《Embed lark-cli in your Agent》官方集成文档）**，用户的说法有据。但 **WorkBuddy 是否直接嵌入 lark-cli 无公开证据（推断：大概率不是——其飞书连接器走"跳转飞书开放平台→代用户创建自建应用→授权"流程，即直接基于开放平台 API + OAuth；且实测算不上时间线）**。正确理解：CLI/MCP 只是连接器把能力暴露给 Agent 的"工具形态"选择，不是实现连接器的必要条件；连接器的本体是授权与凭证管理 + API 调用封装。
3. **飞书连接器本身有哪些能力？**（WorkBuddy 实测 + 飞书开放平台能力面）发消息/群通知（含消息卡片）、读群聊并总结、读云文档/知识库、读写多维表格、读日历并创建会议、（飞书 API 还支持）审批发起与查询、通讯录、任务、邮件。覆盖 18 个业务域、2500+ API。
4. **能给我们的智能体带来什么价值？** 见 §5：一句话版——**让 Agent 的产出进入企业工作流（发群、写回多维表格、沉淀知识库），让 Agent 获取活的企业上下文（文档/群聊/表格数据），并以"只在用户已有权限范围内操作"的可审计身份行事**；同时连接器是对标 Coze/Dify/n8n 的一级产品能力，是续费粘性资产。

---

## 2. 三款产品连接器架构

### 2.1 WorkBuddy（腾讯出品）

| 维度 | 内容 | 证据级别 |
|---|---|---|
| 定位 | 「WorkBuddy 与外部服务的桥梁」，连接器管理页（添加/启用/解绑），无连接器市场 | 实据，[腾讯云官方文档](https://cloud.tencent.com/document/product/1831/134525) |
| 技术形态 | **MCP + CLI（标准化协议）** 与 **Skill + CLI（内置脚本）** 两种；支持用户自定义 MCP 连接器 | 实据，同上 |
| 授权 | **OAuth（扫码/登录跳转）或 API Key 手动填写**；存储 OAuth Access/Refresh Token | 实据，同上 |
| 安全原则 | 每个连接器独立授权；不主动定时抓取；**不超出用户已有权限范围**；对外发送/内容修改前建议再次确认；连接器读写不耗积分、模型理解外部数据耗积分 | 实据，同上 |
| 飞书连接器授权流程 | 「专家技能连接器」→飞书→「+」→**跳转飞书开放平台→输入应用名称创建应用→「开通并授权应用」**，即**代用户一键创建飞书自建应用**，app_id/secret 由 WorkBuddy 代管 | 实据，[搜狐实测 2026-09-18](https://timeline.sohu.com/news/Q6nwyNEBt2)（信创平台转载文，产品名一致性 caveat 见文末注） |
| 飞书连接器能力（均实测） | 群发通知（外部群需在开放平台勾选"允许机器人加入外部群"）；读群聊总结（周报 HTML）；读知识库文档生成周报发群（卡片）；读日历筛空闲+创建会议+群通知；读多维表格算 ROI 生成 PPT | 实据，同上 |
| 身份语义 | 内部群可用**本人身份**发消息；外部群只能以 **WorkBuddy（机器人）身份**发——即 user_access_token 与 tenant/bot token 双身份并存 | 实据，同上；与 lark-cli `--as user/--as bot` 双身份设计互为印证 |

注：搜狐文来自「XPlaza 信创开源广场」转载，自称原文产品名可能有改名；但与腾讯云官方文档、本机 WorkBuddy V5.8.3（此前 B2B 调研中实测，内置腾讯元宝搜索）交叉一致，采信度高。

### 2.2 豆包工作（字节跳动，2026-09 发布）

- **飞书是内置能力而非连接器**：2026-07 字节将飞书产品团队与豆包团队整合，豆包工作支持飞书账号一键登录、继承企业额度与组织权限，飞书文档/审批/日历/多维表格属于平台原生打通（实据，[飞书官方文章](https://www.feishu.cn/content/article/7677519271848610746)、[官方帮助文档](https://www.feishu.cn/hc/zh-CN/articles/282796994123)）。
- **连接器只管飞书之外的外部生态**：企业微信、钉钉、百度网盘、腾讯文档、天眼查、高德、快递100、Linear、Gitee 等 200+ 技能与连接器，支持自定义连接器对接企业自有服务；官方口径"技能是手艺，连接器是通行证，工作伙伴是人"（实据，同上 + [36氪](https://36kr.com/p/3956019073144453)）。
- **授权与管控**："连接成功≠拥有全部权限"，实际可操作范围由所连账号与资源权限决定；管理员在后台审核应用权限、管理席位与额度（实据，[CSDN 实操](https://blog.csdn.net/weixin_29531177/article/details/165020091)）。底层是否 MCP 官方未确认（**推断**：基于 MCP 的第三方说法可信度中等——飞书开放平台自身已提供[远程 MCP 工具](https://open.feishu.cn/document/mcp_open_tools/supported-tools)，且飞书系 Aily 有[管理员统一接入 MCP 指南](https://aily.feishu.cn/hc/1u7kleqg_3d5z9ttt)）。

### 2.3 灵光（蚂蚁集团，2025-11 发布，注意不是阿里）

- 定位 C 端全模态生活+轻办公助手；可授权连接**支付宝**（核对自动续费/梳理扣款）、**钉钉、飞书、滴滴、米家及智能硬件、邮箱**，授权后可"主动识别需求、发起监测跟进"（实据，[App Store](https://apps.apple.com/us/app/id6751496092)、[微软商店](https://apps.microsoft.com/detail/xpdfkv9s6mglrx?hl=zh-CN&gl=CN)、[新浪](https://k.sina.com.cn/article_5953189932_162d6782c0670524ps.html)）。
- 飞书连接器实现细节**未找到公开资料**；钉钉属阿里系可集团内打通，飞书属外部生态（**推断**：大概率同样走飞书开放平台应用授权）。灵光偏"授权读数据+主动提醒"，与办公 Agent 的深度写操作定位不同。

### 2.4 模式归纳（对我们有用的三条）

1. **飞书接入有两条战略路线**：字节路线（飞书=内置能力，集团整合）对我们不可复制；WorkBuddy 路线（飞书=第一个连接器，走开放平台自建应用授权）**正是我们该走的**。
2. **代建自建应用**是降低授权摩擦的关键产品化手段（用户不用自己去开放平台手把手建应用）。一期先做"引导式配置"，二期可评估代建。
3. **"只在用户已有权限范围内操作"** 是连接器对企业管理员的核心安全叙事，技术上对应 user_access_token 语义，WorkBuddy 与豆包工作都在强调这一点。

### 2.5 国际/生态参照系（实据）

- **Coze/扣子**：区分插件（开发者配 API Key）与 **OAuth 插件**（最终用户运行时授权自己账号资源，授权码/PKCE/JWT），未授权时 Agent 提示点击授权（[官方文档](https://docs.coze.cn/guides_oauth_plugin)）。
- **ChatGPT Connectors**：remote MCP + OAuth 2.1/PKCE，公开上架需 App Review；**Claude** 原生支持 remote MCP connectors；**Copilot Studio** 经 Power Automate custom connectors 消费 remote MCP。
- 字节 **Anycross**（飞书旗下 iPaaS）：图形化集成流+预置连接器+触发器（[帮助中心](https://www.larksuite.com/hc/zh-CN/category/7173211266347958277-anycross)）。
- 结论：MCP 作为连接器协议已被 ChatGPT/Claude/Copilot 收敛采纳；自定义连接器框架（我们 Phase 4）支持"注册 remote MCP server"是对标方向。

---

## 3. 飞书官方接入基础设施（实现飞书连接器能用什么）

### 3.1 应用类型与多租户接入的两条路径

飞书应用分**企业自建应用**（租户自建、仅本企业可用、管理员配权限并发布版本）与**商店应用**（ISV 上架、租户安装）（实据，[平台总览](https://open.feishu.cn/document/platform-overveiw/overview?lang=zh-CN)）。第三方 SaaS 要接入"每个客户自己的飞书租户"：

| | 路径 A：商店应用（ISV） | 路径 B：每租户自建应用填凭证 |
|---|---|---|
| 流程 | ISV 上架应用中心→客户管理员安装授权→飞书推送授权事件→ISV 用 app_access_token+tenant_key 换租户 token | 客户在自己飞书后台建自建应用→把 App ID/Secret 填入我们系统→后端调 `/auth/v3/tenant_access_token/internal` |
| 代价 | 需飞书上架审核、SaaS 合规要求高；**不支持长连接事件模式** | 无审核成本；权限由客户管理员自己控制；支持长连接 |
| 适用 | 标准化 SaaS 分发（未来） | **一期推荐**（WorkBuddy 同路线） |

### 3.2 双 token 身份模型（连接器权限语义的基础）

- **tenant_access_token**：应用（机器人）身份，权限=应用申请的权限点，适合群通知、多维表格、审批等应用级操作。
- **user_access_token**：具体用户身份（标准 OAuth 2.0 授权码流程：跳转授权页→code→token/refresh），只能访问**该用户有权的资源**（个人文档、日历、所在群），是"不超出用户已有权限"承诺的技术实现（实据，[授权概览](https://open.feishu.cn/document/server-docs/authentication-management/access-token/web-app/web-app-overview)）。WorkBuddy"内部群本人身份/外部群机器人身份"与之完全对应。

### 3.3 官方工具演进：SDK → MCP → CLI ★关键问题答案

| 工具 | 时间/状态 | 要点 |
|---|---|---|
| [oapi-sdk-python](https://github.com/larksuite/oapi-sdk-python) 等官方 SDK | 活跃维护（2026-10 核实） | `pip install lark-oapi`：API client + 事件订阅 + `lark.ws.Client` **长连接客户端**一体；另有 client_assertion 无密钥模式 |
| [lark-openapi-mcp](https://github.com/larksuite/lark-openapi-mcp) | 2025 发布，Beta，**2025-08 后基本停更**（851 stars） | 把开放平台 API 封装成 MCP tools；双认证（App 凭证/OAuth user token）；不支持文件上传下载与云文档直接编辑 |
| **[larksuite/cli](https://github.com/larksuite/cli)** | **2026-03 开源，MIT，17.9k stars，活跃（2026-10 当日仍有提交）** | 官方 CLI，"built for humans and AI Agents"：Shortcuts（`+` 快捷命令，智能默认值/dry-run）/ API Commands（OAPI 元数据自动生成）/ Raw API（任意 2500+ 端点）三层；**OAuth 登录含 `--device-code`/`--no-wait` 非阻塞 Agent 模式**；user/bot 双身份（`--as user/--as bot`）；DPoP；凭据存 OS keychain；**26 个 Agent Skills**（skills/ 目录，`npx skills add larksuite/cli`）；官方《Embed lark-cli in your Agent》文档支持企业以 extension/wrapper 嵌入并自管凭据 |

**结论**：①"飞书开源了命令行工具"属实（lark-cli，2026-03）；②但 WorkBuddy 类产品的飞书连接器**并不依赖**该 CLI 也能完整实现——开放平台 REST API + OAuth 才是本体（推断+实据组合：WorkBuddy 的代建自建应用流程直接对应开放平台 API）；③官方演进路线 MCP(2025)→CLI(2026) 说明**不建议把 lark-openapi-mcp（已停更）作为依赖**；lark-cli 是可选加速器而非必经之路。

### 3.4 事件订阅：公网回调 vs 长连接

- **Webhook 回调**：需公网 HTTPS URL + challenge 校验 + Encrypt Key 解密（本仓库飞书渠道已用此模式，见 §4）。
- **长连接（WebSocket）**（实据，[官方文档](https://open.larksuite.com/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/use-websocket)）：SDK 内置 WS 客户端直连飞书服务器，**无需公网回调**；限制：**仅自建应用支持**（商店应用不支持）、每应用最多 50 连接、消息须 3 秒内处理完、多客户端集群模式随机投递不广播。对连接器一期（只做 Agent 主动调用）不需要；二期若做"飞书事件触发 Agent"（如审批状态变化），长连接可复用租户已有的自建应用且无需暴露公网回调。

### 3.5 能力面与频控

18 个业务域、2500+ API（lark-cli 口径）：消息与群（发消息/卡片/群 CRUD）、云文档（docx 块模型读写/wiki 知识库）、多维表格（记录 CRUD、视图、自动化工作流 API 2026 新增）、日历、审批（含三方审批接入）、通讯录、任务、邮件、视频会议。频控：各接口单独标注，常见单应用几十~200 QPS（bitable 建记录 150/更新 200 QPS 量级），可申请提额（量级参考，以各接口文档页为准）。

---

## 4. 与本仓库架构的对齐（现状盘点）

**最重要的现状：《连接中心架构设计》已于 2026-07-30 完成并登记（ideas.md 系统功能 #48，状态待开发），本文不另起炉灶，为其 Phase 2「feishu 连接器」补弹药。** 该设计已定义：

- `tenant_connector_configs` 表（config_json 非敏感 + encrypted_secret Fernet 加密 + status 测试状态机 + scope_subagents 作用域）；
- `src/connectors/`（BaseConnector + ConnectorRegistry，feishu.py 在 Phase 2 高优先级清单，测试连接方式已定为"获取 tenant_access_token"）；
- 前端 `ConnectionCenter.vue` 4 Tab 骨架已存在（内置/自定义连接器 Tab 目前是占位空壳，路由已注册）；
- `src/core/secret_crypto.py` 加密模块已按设计抽取就位。

其余可复用资产（本次代码盘点确认）：

| 资产 | 路径 | 对连接器的意义 |
|---|---|---|
| 飞书渠道 adapter（完整） | `src/channels/feishu/adapter.py`（868 行） | token 缓存刷新、限流、收发消息、签名校验已实现——连接器的 API 调用层可复用其 token 管理与 HTTP 模式；**但注意其 app_secret 目前明文存 `tenant_channel_configs`，连接器层不得沿用** |
| 工具系统 | `src/tools/base.py`（`__init_subclass__` 自动注册）→ `registry.py` → `executor.py` → `assembly.py` | 连接器工具 = 新增 catalog BaseTool 子类即可进入装配；`ToolExecutionContext`（tenant_id/user_id/env_vars）是租户隔离载体 |
| 文档驱动第三方操作范式 | `src/services/recap/tasks/external_push.py` | 「租户 API 文档 + http_api 工具 + LLM 循环」是已验证的轻量集成范式，自定义连接器（Phase 4）可参照 |
| 渠道样板 | `src/social_media/connectors/`（base+registry） | 与设计文档的 BaseConnector 同构的现成样板 |
| MCP 现状 | `src/mcp/`（server） | 项目只有对外 MCP server，**无 MCP client**；Phase 4 自定义连接器若走 MCP 路线需新建 client |
| 渠道与连接器关系 | — | feishu 渠道（消息出入口，公网回调）与 feishu 连接器（数据操作工具）是**两层**：同一租户可共享同一个自建应用的 app_id/secret，但配置表、生命周期、权限点各自独立（设计建议见 §6.5） |

---

## 5. 飞书连接器给智能体的价值（说清楚、想清楚）

按"Agent 的输入（上下文）—Agent 的输出（触达）—Agent 的流程（协作）—产品商业价值"四层：

1. **产出进入企业工作流（输出侧，最高频价值）**：现状 Agent 的交付物停在聊天窗或本系统文件下载。连接飞书后：周报/通知直接发到指定群（卡片形式）、B2B 线索等结构化结果写回多维表格供业务同事续办、分析结论沉淀为知识库文档。这把产品从"对话工具"变成"工作流参与者"——与既有 recap/external_push（推租户 ERP）同一方向，飞书连接器相当于把"外部推送"目标从 ERP 扩展到 IM 与协作数据面。
2. **获取活的企业上下文（输入侧，知识库联动）**：飞书云文档/知识库/群聊记录是大多数企业的知识主资产。短期：Agent 按需读指定文档/表格回答业务问题；中期：飞书文档增量同步入本系统知识库（RAG 数据源补齐，见既有知识库增强调研方向）。读群聊总结（WorkBuddy 实测最常用场景之一）对运营/客服租户直接可用。
3. **流程协作（日程与审批）**：读日历筛空闲+建会议（销售/招聘场景）、发起/查询审批（把 Agent 的决策建议接入企业审批流，如报价审批）。一期可只做读+创建，不做复杂状态机。
4. **可审计的身份与权限叙事（安全侧）**：user_access_token 使"Agent 只能做该员工本人有权做的事"成为代码可保证的事实而非提示词承诺——对租户管理员采购决策关键，也是 WorkBuddy/豆包共同强调的卖点。
5. **商业价值**：连接器是 Coze/Dify/n8n 的一级能力位，对标完成度直接影响产品定位；每多连接一个系统，租户迁移成本越高（续费护城河）。飞书作为第一连接器试样，跑通"凭证托管+OAuth+工具封装+审计"的通用框架后，企微/钉钉/网盘等即可低成本复制（连接中心设计已预置 12-15 个内置连接器清单）。

不夸大的部分（边界）：飞书在国内办公渗透率高但并非所有租户都用；连接器不改变 Agent 核心智能，只扩展可触达的数据与动作面；写操作（发群、写表格）涉及对第三方可见的行为，需确认机制与频控，避免把不稳定输出推给业务群（与产品"稳定性优先"原则一致）。

---

## 6. 飞书连接器实现方案建议（一期，对齐连接中心设计 Phase 2）

### 6.1 授权与接入路径

- **路径 B：每租户自建应用填凭证**（推荐一期）：无上架审核、支持后续长连接、权限由租户管理员掌控；WorkBuddy 同路线。产品化上做"引导式配置向导"：给出开放平台建应用的分步指引 + 所需权限点清单 + 回填 app_id/secret + 测试连接（获取 tenant_access_token，设计文档已定）。"代用户建应用"（WorkBuddy 式一键）作为二期产品化增强评估。
- **身份模型**：一期 tenant_access_token（应用身份：群通知、多维表格、日历、审批查询）+ user_access_token（用户身份：读个人有权文档/日历、以本人身份发内部群消息）。user token 的 OAuth 授权页与 refresh 管理（token 约 2h/refresh 长期）需要后端 OAuth 回调路由与加密存储。
- 二期若做商店应用（SaaS 分发），注意不支持长连接、需上架审核，另立调研。

### 6.2 实现路线对比与推荐

| | A：lark-oapi SDK 直连（推荐一期） | B：lark-cli 子进程 | C：lark-openapi-mcp（不推荐） |
|---|---|---|---|
| 多租户凭据 | 进程内按租户管理，入 `tenant_connector_configs` 加密存储，自然 | CLI 凭据默认存 OS keychain，服务器多租户需按官方 extension/wrapper 模式自管凭据，额外开发 | 同 A |
| 能力面 | 一期手工封装 8-12 个工具，够用；后续按需加 | 2500+ API 全覆盖、26 个官方 Agent Skills、官方升级即得 | MCP tools 全集但**官方已停更**（2025-08 后），不建议引入依赖 |
| 运维 | 无子进程；Windows/Linux 无差异；复用渠道 adapter 的 token/限流经验 | 需分发安装（npx/Go 二进制）、管理子进程生命周期、解析输出、并发控制 | 需自起 Node 进程 |
| 审计与频控 | 完全可控（工具级审计、频控） | 需包一层 | 一般 |
| 结论 | **一期主路线**；与既有 Python 后端/工具系统/租户隔离模型最契合 | 作为开发调试工具与二期"低成本扩 API 面"的评估项（其 Agent Skills 生态值得跟踪） | 仅作历史参照；Phase 4 自定义连接器的 MCP client 是另一件事（面向任意第三方 MCP server） |

### 6.3 一期工具面（建议 10 个左右，agent 工具命名对齐现有风格）

读：`feishu_read_doc`（云文档/知识库节点）、`feishu_list_wiki`、`feishu_read_chat_history`（群消息，用于总结）、`feishu_list_bitable_records`、`feishu_list_calendar_events`、`feishu_search_user`（通讯录）。
写：`feishu_send_message`（群/个人，卡片，user/bot 身份可选）、`feishu_create_bitable_record`/`update`、`feishu_create_calendar_event`、`feishu_create_approval`（可选，视权限点 friction）。
控制项：写类工具默认要求模型给出目标确认参数（对齐产品确认原则）；scope_subagents 作用域沿用设计文档；所有调用记录审计日志（租户/用户/连接器/动作/目标资源/结果）——这是"不超出用户权限"承诺的落地证据。

### 6.4 事件方向（二期）

一期只做 Agent 主动调用；"飞书事件触发 Agent"（审批状态变化、群 @机器人、文档更新）放二期，届时评估长连接模式（复用租户自建应用、免公网回调；注意 3 秒处理约束→接入现有后台异步处理模式，本仓库飞书渠道 callback 已有先例）。

### 6.5 与飞书渠道配置的关系（建议）

连接器配置独立于渠道配置（两张表、独立生命周期）；但前端在租户已配飞书渠道时提示"可复用渠道应用的 app_id/secret 一键导入"（同一自建应用同时开机器人能力与 API 权限点），降低管理员重复配置成本。**顺带建议（另行立项）**：`tenant_channel_configs` 中 feishu 的明文 app_secret 迁移到 secret_crypto 加密，连接器层绝不复制明文做法。

### 6.6 风险与开放问题

1. 权限点申请摩擦：自建应用需租户管理员在开放平台逐项开权限并发布版本，向导文案要把"所需权限点清单"讲清楚（对齐 WorkBuddy 经验：外部群等能力有额外开关）。
2. user_access_token 的授权回调需要公网可达的 OAuth redirect 路由（与渠道 callback 同域复用）；refresh token 失效后的重授权体验要设计（过期提醒）。
3. 频控为单应用级，多租户各自应用互不影响（路径 B 的天然优势）；单租户内 Agent 批量写表格需工具层限速。
4. 灵光/豆包工作连接器底层机制未公开，本文对其推断不应被引用为事实。
5. lark-cli 仍处快速迭代期，若二期引入需锁定版本并做输出契约测试。

---

## 7. 来源清单

- WorkBuddy 连接器官方文档（腾讯云）：https://cloud.tencent.com/document/product/1831/134525
- WorkBuddy 飞书连接实测（2026-09-18，搜狐/XPlaza 转载）：https://timeline.sohu.com/news/Q6nwyNEBt2
- 豆包工作/飞书整合：https://www.feishu.cn/content/article/7677519271848610746 、https://www.feishu.cn/hc/zh-CN/articles/282796994123 、https://36kr.com/p/3956019073144453
- 灵光：https://apps.apple.com/us/app/id6751496092 、https://www.microsoft.com/store/detail/xpdfkv9s6mglrx
- 飞书开放平台总览/授权/token：https://open.feishu.cn/document/platform-overveiw/overview 、https://open.feishu.cn/document/server-docs/authentication-management/access-token/web-app/web-app-overview
- 长连接事件订阅：https://open.larksuite.com/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/use-websocket
- 官方仓库：https://github.com/larksuite/cli 、https://github.com/larksuite/lark-openapi-mcp 、https://github.com/larksuite/oapi-sdk-python
- Coze OAuth 插件：https://docs.coze.cn/guides_oauth_plugin ；Anycross：https://www.larksuite.com/hc/zh-CN/category/7173211266347958277-anycross
