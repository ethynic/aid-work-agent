# 售前咨询外部推送重构方案（unionid 查重 + 每轮跟进记录）

> 日期：2026-09-03
> 触发：tr_9483b1bf02af4f51 实测发现同一微信侧用户（昵称"小团长"）触发 2 次留资，向 10605 推送了 2 条"客户"和 2 条"跟进记录"。产品经理提出业务逻辑调整，本方案给出落地设计。
> 关联文档：[lead-capture-design.md](./lead-capture-design.md)、ext/10605-售前咨询接口文档.md、subagents/pre-sales/SUBAGENT.md

---

## 1. 需求清单

### 1.1 产品经理业务需求

| # | 需求 | 内容 |
|---|------|------|
| P1 | 客户唯一化 | 同一微信侧用户（同一 unionid）在第三方系统只保留 1 条"客户"，信息有更新（如补充电话）调修改接口。10605 侧 `t_kehuxinxi` 已加 `unionid varchar(50)` 字段 |
| P2 | 每次对话一条跟进记录 | "一次对话"定义为**一轮问答**：用户说一句、智能体回复 1~5 句（微信客服单次回复上限 5 句）。每轮问答结束后推送 1 条跟进记录 |
| P3 | 留资与推送解耦 | "留资"对智能体仍然重要（引导话术、本地线索记录保留），但**不作为是否调用推送接口的依据** |

### 1.2 补充要求（技术侧）

| # | 需求 | 内容 |
|---|------|------|
| T1 | 客户名称兜底 | 客户未表明身份时，用微信昵称兜底写入"客户名称"（xingming），替代现状"待补充" |
| T2 | 微信用户信息透出 | 微信接口可取的**头像、性别**一并拿到提供给 Agent；本次 10605 不落库，供下次其他第三方应用选用 |

### 1.3 架构诉求

租户差异逻辑（本次是 10605 的查重/建改规则、每轮跟进记录规则）主要写在**租户上传的接口文档**（`pre-sales-api.md`）中；`SUBAGENT.md` 只保留跨租户通用表述："每完成一轮问答，都根据 pre-sales-api.md 的要求进行 API 接口数据推送"。

---

## 2. 关键问题核实结论

### 2.1 users 表 wx_openid / wx_unionid 为空的根因

代码记录链路**完整存在**：`channel_routes.py` 每条消息调 `adapter.get_user_info()`（wecom_kf/adapter.py:831，返回 `wx_unionid`），传入 `ensure_user_registered` → `auto_register.py` 的 `_extract_wx_unionid()` 建号时写入、`_update_user_info_from_channel()` 对老用户补写。因此：

- **wx_unionid 为空 = 微信 API 未返回**。微信客服的 `get_customer_info` 只在企业微信**绑定了微信开放平台**后才返回客户 unionid（且客户需在开放平台账号体系内）。当前为空说明该企微未绑定开放平台（或客户不在绑定体系内）。
- **wx_openid 为空是必然**：微信客服是 external_userid 体系（`wm` 开头），根本没有 openid 概念；代码中也没有任何 wecom_kf 渠道写 openid 的路径。

**结论**：unionid 是"绑定了开放平台之后才有"的条件字段，查重逻辑必须设计兜底键。

### 2.2 external_userid 的来源与稳定性

- 来源：微信客服回调消息的 `FromUserName`（代码中 `unified_msg.user_id` → kf context 的 `external_userid`），格式 `wmXXXX...`。
- 稳定性：是客户在**该企业微信**下的外部联系人唯一 ID，同一客户对同一企业恒定不变（换公众号/小程序才会变），**已确认作为查重键**（复用 10605 `t_kehuxinxi.unionid` 字段落库，见 §6.1）。
- 局限：换一个企微企业（不同租户/不同客服号主体）则不同——但我们本就按租户隔离推送，无影响。

### 2.3 "一次对话"= 一轮问答的定义

每轮问答 = 用户一条消息 → 智能体处理并回复（1~5 句）。推送到时机：**本轮回复内容确定后**（Agent 循环末尾、回复发出前后均可）。此定义下"每轮一条跟进记录"天然无跨轮去重问题（P2 的幂等风险仅剩"同轮内误调两次"，见 §5.4）。

---

## 3. 架构分工（职责边界）

| 层 | 文件 | 职责 | 本次改动 |
|----|------|------|---------|
| 系统层（跨租户通用） | `subagents/pre-sales/SUBAGENT.md` | 触发时机（每轮问答后推送一次）、通用流程（加载文档→按文档执行）、鉴权惯例、错误/降级通用规则、确认策略、敏感信息 | ✅ 改（删除"留资成功即推送"耦合） |
| 租户层（业务差异） | 租户上传的 `pre-sales-api.md`（源文件 ext/10605-售前咨询接口文档.md） | 10605 全部业务逻辑：unionid 查重与建/改分流、external_userid 兜底、每轮一条跟进记录、客户名称昵称兜底、字段映射、跟进汇总摘要同步、错误码处理 | ✅ 改（新增"同步业务规则"章节） |
| 代码层 | `src/` | 微信用户信息透出（头像/性别/unionid/external_userid → Agent 可读）；client_token 缓存优化（可选） | ✅ 小改动（见 §4） |

---

## 4. 系统侧代码改动

### 4.1 kf context 注入 channel_user_info（T1/T2 前提）

`src/saas/api/channel_routes.py` 的 `set_kf_context({...})`（约 2358 行）增加一项。`user_info` 本来就在每条消息处理时取好了（2088 行），**零额外微信 API 调用**：

```python
set_kf_context({
    ...  # 现有字段不动
    "channel_user_info": {
        "nickname": user_info.get("name", ""),        # 微信昵称
        "avatar": user_info.get("avatar", ""),         # 头像 URL
        "gender": user_info.get("gender", 0),          # 0 未知 / 1 男 / 2 女
        "wx_unionid": user_info.get("wx_unionid", ""), # 绑定开放平台后才有
    },
    # external_userid 已有
})
```

### 4.2 新增通用工具 `get_channel_user_info`（T2：提供给 Agent）

Agent（LLM）无法直接读 kf context，新增一个只读工具透出。**channel 无关**（其他渠道后续往各自 context 塞同结构即可复用，满足"下次其他应用可能需要"）：

- 位置：`src/tools/channel/channel_user_info.py`，继承 `BaseTool`，`catalog=True`，无参构造。
- 数据源优先级：`get_kf_context()["channel_user_info"]`（实时）→ fallback `users` 表（ctx 的 `user_id` 已有 nickname / avatar_url / wx_unionid 落库值）→ 都无则返回空字段。
- 返回字段：`channel`、`external_userid`、`nickname`、`avatar`、`gender`（含中文映射说明）、`wx_unionid`、`user_id`、`session_id`。
- 非微信客服渠道返回 `success: False` + 提示（与 record_lead_capture 的渠道隔离模式一致）。
- schema 由 InputModel 自动收集，无需改 `agent.py`（catalog=True 工具规范）。

### 4.3 client_token 缓存（已确认实施，2026-09-03）

委托登录 `mobile` = 归属员工手机号（每个客服账号固定），10605 服务端 client_token **有效期 1 天**（文件缓存）。现状每轮问答 login → 业务接口 → logout 全套太重（每轮 3 次额外 HTTP）。设计：

- **缓存载体**：Redis（经 `src/core/redis_client.py`，Redis 不可用自动降级内存），键前缀在 `CacheKeys` 注册，键模式 `pre_sales_client_token:{tenant_id}:{assignee_phone}`，**TTL 23h**（留 1h buffer，避开 1 天有效期的临界点）。
- **技能脚本**：pre-sales-api 技能新增 `scripts/delegate_login.py`——读缓存命中直接返回 `client_token`；未命中调登录接口（`mobile` 取归属员工手机号，见 §6.5 来源）并写缓存。
- **失效处理**：业务接口返回 `Code: -99` 时，Agent 调 `delegate_login.py` 带 `force_refresh=1` 强制重新登录并覆盖缓存。
- **logout 不再每轮调用**，token 由缓存过期自然失效。
- 登录/缓存流程属 10605 特有逻辑，规则写进 `pre-sales-api.md`，不进 SUBAGENT.md。

### 4.4 record_lead_capture 工具不动

本地留资记录、顾问通知、二维码下发逻辑全部保留。变的只是 SUBAGENT.md 中"本地留资成功即触发推送"的表述（推送触发与留资解耦）。

---

## 5. SUBAGENT.md 改造（系统层）

### 5.1 「外部系统留资推送」章节改写要点

- 触发语句改为：**"每完成一轮问答（用户一条消息 → 你的回复完成），都根据 pre-sales-api.md 的要求，进行 API 接口数据推送"**。
- 删除"本地留资成功即触发推送"的耦合；留资成功时的推送不再是特殊路径，统一走每轮推送。
- 通用规则保留：先 `use_skill("pre-sales-api")` + `load_api_config.py` 加载最新文档、**切勿凭记忆硬编码字段名/接口路径**、`Code=0` 才算成功、失败降级不阻塞对话、不向用户暴露接口报错细节、不泄露 token。
- 删除 10605 特有内容：`assignee_phone` 委托登录细节、双 Token 说明、"客户信息不足时的处理"整节（含"待补充"话术）→ 全部下沉到 `pre-sales-api.md`。
- 确认策略调整：涉及**留资信息写入**（手机号等）→ 保留"先向客户确认"；**跟进记录自动推送 → 无需向客户确认，也不向客户提及**（后台 CRM 沉淀，不打扰咨询中的客户）。
- 兜底数据来源指引：推送前可调用 `get_channel_user_info` 工具获取微信侧客户信息（昵称/头像/性别/unionid/external_userid），字段映射以文档为准。

### 5.2 留资章节保持不变

「客户留资（核心目标）」章节（触发判定、话术、record_lead_capture 动作指引）不动，仅在其中删除与外部推送的交叉引用。

---

## 6. 10605 接口文档改造（租户层，pre-sales-api.md）

在文档中新增「同步业务规则（AI 智能体必读）」章节（置于第 10 节"权限与写入约束"之前或合并），并同步改数据字典：

### 6.1 客户唯一化与建/改分流（P1）

每轮推送前，先确保客户存在。**查重键决策（2026-09-03 确认）：统一将我方 `external_userid`（`wm` 开头，对同一企微恒定）写入 10605 `t_kehuxinxi.unionid` 字段**，该字段即"我方外部用户唯一标识"落点（当前企微未绑定开放平台、真实 unionid 拿不到，统一存 external_userid 避免混存两种值；未来若绑定开放平台需切换真实 unionid，届时做一次性数据迁移）。查重规则：

1. **精确查询**：客户信息列表接口，`filters: [{attr: "unionid", value: ["<external_userid>"], component: "input", exact: true}]`。
2. **命中**：走修改接口按需更新；**未命中**：创建客户（调第 5 节创建接口），`unionid` 字段写入 `external_userid`。

命中后的更新规则：

- 本轮新收集到客户信息（手机号、行业、需求摘要等）且库中对应字段为空或不同 → 调修改接口（第 6 节）更新，注意 `update_time` 传当前时间防 `Code: 2`。
- 无新信息 → 跳过客户修改，仅推跟进记录。
- **禁止**在已命中客户的情况下再调创建接口（本次实测的重复"客户"根因即此）。

### 6.2 每轮问答一条跟进记录（P2）

- 触发：每轮问答（用户一条消息 → 智能体回复完成）推送 1 条跟进记录（第 9 节创建接口）。
- 内容：`neirong` 写本轮客户诉求摘要；`kehuhuifuneirong` 写本轮智能体回复要点；`genjindongzuo` 固定"微信咨询（AI）"或按文档约定；`shijian` 取当前日期。
- 客户基本信息字段（`kehuxingming`、`lianxifangshi` 等）从 6.1 确保存在的客户记录中回填（沿用第 9 节"创建前的必要步骤"）。
- 创建跟进记录后按 9.1 节同步 `zhuangtai` / `genjinhuizongzhaiyao`（无重要变化可跳过）。
- 同轮防重：一轮问答内只允许推送 1 次；接口失败重试 1 次后仍失败则放弃本轮（不阻塞对话），下一轮正常推送。

### 6.3 客户名称兜底（T1）

`xingming`（客户名称）取值优先级：客户在对话中表明的身份（姓名/称呼）> **微信昵称**（调 `get_channel_user_info` 获取）> external_userid 前 8 位或"待补充"。全链路不再默认写"待补充"。

### 6.4 数据字典与字段说明

- 11.1 `t_kehuxinxi` 数据字典补一行：`unionid | 外部用户唯一标识（存我方 external_userid） | varchar(50)`（10605 侧已加字段；2026-09-03 确认复用该字段存 external_userid，**无需再新增字段**）。
- 头像/性别：本次 10605 不落库、不推送；文档预留说明（下次应用需要时在对应文档中加字段映射即可，Agent 侧 `get_channel_user_info` 已就绪）。

### 6.5 委托登录与 token 缓存（已确认实施 §4.3）

- 登录 `mobile` 来源：本轮 `record_lead_capture` 成功结果中的 `assignee_phone`（若有）；否则取客服账号绑定的归属员工手机号（`kf_account.tenant_user_id` → `users.phone`，技能脚本内查询）。归属员工手机号在每个客服账号维度固定，是缓存键的组成之一。
- 缓存策略：优先用缓存的 client_token（§4.3）；`Code: -99` 时 `force_refresh` 重新登录；logout 不再每轮调用。

---

## 7. 降级与异常策略（通用，写 SUBAGENT.md；10605 特有细节写文档）

| 场景 | 行为 |
|------|------|
| 未配置 pre-sales-api 技能 / 文档缺失 | 不推送，对话正常进行 |
| 查重/创建/更新/跟进接口业务错误（Code != 0） | 对照文档修参重试 1 次；仍失败放弃本轮推送，**不阻塞对话** |
| 鉴权失败（Code = -99） | `force_refresh` 重新委托登录（刷新缓存）后重试 1 次 |
| assignee_phone 缺失 | 放弃推送，对话正常 |
| 所有失败 | 不向客户暴露接口报错细节，不向客户提及推送行为 |

---

## 8. 决策记录（2026-09-03 已确认）

| # | 问题 | 决策 |
|---|------|------|
| Q1 | external_userid 在 10605 的落点 | **复用 `t_kehuxinxi.unionid` 字段**存我方 external_userid，不再新增字段（见 §6.1） |
| Q2 | client_token 缓存 | **实施**（10605 token 有效期 1 天，Redis 缓存 TTL 23h，见 §4.3） |
| Q3 | 跟进记录内容生成方式 | **LLM 生成摘要**——Agent 循环内顺手生成（复用本轮上下文，无额外 LLM 调用），写入 `neirong` / `kehuhuifuneirong` |
| Q4 | 最小内容门槛 | **全推**——每轮问答无条件推送 1 条跟进记录，不设内容门槛 |

---

## 9. 实施步骤

| 步骤 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | kf context 注入 channel_user_info | `src/saas/api/channel_routes.py` |
| 2 | 新增 `get_channel_user_info` 通用工具 + 单测 | `src/tools/channel/`、`tests/unit/tools/` |
| 3 | CacheKeys 注册 `pre_sales_client_token` 前缀 | `src/core/cache_utils.py` |
| 4 | pre-sales-api 技能新增 `delegate_login.py`（读缓存 → 未命中登录 → 写缓存 TTL 23h，支持 force_refresh）+ 单测 | `src/skills/pre-sales-api-1.0.0/scripts/` |
| 5 | 改写 SUBAGENT.md 推送章节（通用化 + 解耦留资 + 确认策略） | `subagents/pre-sales/SUBAGENT.md` |
| 6 | 10605 文档新增「同步业务规则」章节（external_userid→unionid 查重、每轮跟进记录、昵称兜底、token 缓存规则）+ 数据字典补 unionid | `ext/10605-售前咨询接口文档.md`（上传后即 pre-sales-api.md） |
| 7 | 回归实测（重点：同用户二轮对话只 1 条客户、每轮 1 条跟进、昵称兜底、token 缓存命中、Code=-99 强刷） | — |
