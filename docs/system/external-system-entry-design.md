# 外部系统入口（SSO 打开第三方系统）设计方案

> 状态：方案完成，待评审排期
> 关联：48 号连接中心 / 59 号留资 / 60 号外部推送重构 / 61 号推送代码级兜底（recap external_push）

## 1. 背景与目标

售前咨询智能体的客户数据已通过 recap external_push 推送到第三方系统（10605 ERP）。产品要求在我方系统页面提供打开第三方系统的入口，打通完整操作链路：

- 连接中心下新增「外部系统」菜单/Tab；
- 外部接待客户页面右上角增加打开按钮。

10605 提供单点登录接口，登录细节原则上在租户上传的 `pre-sales-api.md` 中描述，并要求未来可扩展到其他第三方系统。

## 2. 核心决策：机制与实例分离

| 层 | 职责 | 落点 |
|----|------|------|
| 系统层（通用机制） | 外部系统入口的配置模型、SSO 通用契约、跳转接口、前端入口渲染 | 本设计文档 + `src/api/`、连接中心、ExternalCustomerService.vue |
| 实例层（售前 → 10605） | 该第三方系统的接口细节、SSO 具体实现方式 | 租户文档 `pre-sales-api.md` 的 `api-meta` 块 |

- 我方系统代码**不硬编码 10605**：外部系统列表由租户文档 `api-meta` 声明驱动（与 61 号 external_push 同一事实来源），无声明的租户不显示入口。
- `pre-sales-api.md` 不承载通用规范，只描述 10605 如何实现通用契约；未来其他第三方系统复用同一契约，在各自租户文档中声明。

## 3. 通用契约：api-meta 扩展

现有 `api-meta`（61 号已实现，`parse_api_meta` 解析）新增可选 `sso` 块：

```markdown
```api-meta
login_url: https://10605.example.com/api/login
user_token_name: client_token
external_userid_field: unionid
http_method: POST
push_exclude_sections: 登录, 客户详情
sso_enabled: true
sso_system_name: 10605 ERP
sso_mode: ticket_redirect
sso_url: https://10605.example.com/sso/entry
sso_ticket_param: ticket
```
```

| 键 | 必填 | 说明 |
|----|------|------|
| `sso_enabled` | 是 | 声明提供外部系统入口，缺省 false（不显示入口） |
| `sso_system_name` | 否 | 入口显示名，缺省「第三方系统」 |
| `sso_mode` | 否 | 跳转模式，见 §4；缺省 `direct_url` |
| `sso_url` | 是 | 第三方入口地址（SSO 端点或普通登录页网址） |
| `sso_ticket_param` | 视模式 | URL 上携带票据的参数名，`direct_url` 模式不需要 |
| `sso_fallback_url` | 否 | SSO 失败回退地址（普通登录页）；未声明时回退到 `sso_url` 本身 |
| `sso_grant_url` | ticket_redirect 必填 | 票据签发接口（复用委托会话双 token 换一次性票据），10605 为 `/erp.delegate/sso_grant` |
| `sso_login_url` | 否 | 备选签发接口（agent_token + 手机号，无委托会话时兜底），10605 为 `/erp.delegate/sso_login` |

## 4. SSO 通用跳转模式

预留四种模式，由租户文档声明（缺省 `direct_url`），10605 实际支持哪种联调确认：

| mode | 流程 | 说明 |
|------|------|------|
| `direct_url`（缺省） | 前端直接 `window.open(sso_url)`，用户在第三方登录页手动登录 | 最简单：第三方无任何 SSO 接口、仅提供入口网址时使用，零凭据参与，后端无需生成跳转 URL |
| `ticket_redirect`（有 SSO 接口时首选） | 我方后端持 client_token 调第三方 SSO 换取接口 → 获得一次性 ticket（短 TTL）→ 前端打开 `sso_url?sso_ticket_param={ticket}` | token 不出后端，安全性最好；**第三方响应若直接返回完整跳转 url 则优先使用**（10605 即此模式：sso_grant 响应带 url） |
| `token_param` | 前端直接打开 `sso_url?sso_ticket_param={client_token}` | 有 SSO 但无换票接口时的兜底，长期 token 暴露在 URL |
| `form_submit` | 前端隐藏表单 POST 账密（凭据后端下发一次性使用） | 第三方仅有账号密码登录时的降级模式，Phase 2 视需要实现 |

Phase 1 实现 `direct_url` + `ticket_redirect` + `token_param` 三种；`form_submit` 预留枚举不实现。

**接口影响**：`direct_url` 模式下 `GET /api/external-systems` 直接返回 `entry_url=sso_url`，前端打开即可，无需调 `POST /sso-url`；后两种模式 `entry_url` 为空、`sso_ready=true`，前端先调 sso-url 接口换取跳转地址。

### 4.1 SSO 失败回退（手动登录兜底）

`ticket_redirect` / `token_param` 模式下，SSO 不保证成功（典型：用户账号在第三方系统不存在、第三方 SSO 服务异常）。统一约定：

1. `POST /sso-url` 换票失败时**不向前端抛错阻断**，返回 `{success: false, fallback_url, error}`——`fallback_url` 取 `sso_fallback_url`（未声明则取 `sso_url` 本身）；
2. 前端收到后仍 `window.open(fallback_url)` 打开第三方系统，并 toast 提示「SSO 登录失败，已打开登录页，请手动登录」——保证用户始终能到达第三方系统，操作链路不因 SSO 故障中断；
3. 失败原因（账号不存在 / 网络异常 / 第三方 5xx）由后端记入 `error` 与审计日志，便于区分「该用户需开通账号」与「临时故障」。

**边界说明**：`token_param` 模式无后端换票环节，账号不存在等失败发生在第三方页面内，我方无法感知，由第三方自行展示其错误页，不做回退；`direct_url` 模式本身就是手动登录，无回退需求。回退仅对 `ticket_redirect` 生效。

## 5. 改动点清单

### 5.1 后端

| 改动 | 文件 | 说明 |
|------|------|------|
| api-meta 解析扩展 | `src/services/recap/tasks/external_push.py` 的 `parse_api_meta`（或抽公共模块） | 新增 sso_* 键解析；建议将 api-meta 解析抽到 `src/services/tenant_api_doc.py` 供推送与入口共用 |
| 外部系统列表 API | `src/api/external_systems.py`（新） | `GET /api/external-systems`：读租户文档 api-meta，返回 `[{system_id, name, sso_ready}]`；无 sso 声明返回空列表 |
| SSO 跳转 API | 同上 | `POST /api/external-systems/{system_id}/sso-url`：按 mode 生成跳转 URL；ticket_redirect 模式调第三方换票（复用 delegate_login 取 client_token，redis 缓存 23h 既有机制） |
| 租户文档解析缓存 | 复用/参照推送侧文档读取 | 解析结果 redis 短缓存（如 10min），文档更新自然过期 |

权限：与外部接待客户页一致——租户管理员全量可见；普通员工（引流负责人）是否可见列为决策点 Q2。

### 5.2 前端

| 改动 | 文件 | 说明 |
|------|------|------|
| 连接中心新二级菜单「外部系统」 | `MenuSidebar.vue` + 新页面组件 | 连接中心一级菜单下新增**独立二级菜单**（非 Tab，决策 Q4 已确认），路由如 `/t/:tenant_id/connections/external-systems`；页面为卡片列表：系统名 + 「打开」按钮（新开窗口）；无声明时显示空状态说明文案。sso 未就绪（sso_ready=false）时按钮禁用并提示 |
| 外部接待页右上角按钮 | `ExternalCustomerService.vue` | 加载时调列表 API，有可用系统才渲染「打开 10605 ERP」按钮（显示名取 sso_system_name），`window.open` 打开 sso-url |
| API 封装 | `frontend/web/api/`（新 connections 或 external-systems 模块） | 走 `getAuthHeader()`（自动含 X-Tenant-Id） |

### 5.3 文档

- `pre-sales-api.md` 模板（`storage/tenants/{tid}/templates/`）补充 sso 块示范与说明章节；
- `docs/subagent/pre-sales/external-push-code-hook-design.md` 补一节指向本文档。

## 6. 安全要点

1. **client_token 不下发前端**：ticket_redirect 模式换票在后端完成；ticket 一次性、TTL ≤ 60s。
2. **审计**：sso-url 生成调用写 `logger.info`（租户/用户/系统/时间），沿用现有审计口径，token 参数脱敏。
3. **租户隔离**：两个 API 均走 `request.state.tenant_id`，禁止从参数读取租户。
4. **失败兜底**：第三方换票失败返回标准错误结构（success/error/debug，debug 经 sanitize）。

## 7. 决策点（已确认，2026-09-08）

| # | 问题 | 结论 |
|---|------|------|
| Q1 | 10605 SSO 实际模式 | `ticket_redirect`：首选 `sso_grant`（复用委托会话双 token）签发一次性票据（5 分钟有效），兜底 `sso_login`（手机号）；第三方直接返回完整跳转 url，落地页由其前端自动 exchange。接口细节已补入 `ext/10605-售前咨询接口文档.md` §2.3 与 api-meta sso_* 键 |
| Q2 | 普通员工是否可打开第三方系统 | 可以。管理员与普通员工均可见入口 |
| Q3 | 新开窗口还是 iframe | 新开窗口（window.open） |
| Q4 | 连接中心形态 | 连接中心一级菜单下新增**独立二级菜单**（非 Tab） |

## 8. 分期

| Phase | 内容 | 预估 |
|-------|------|------|
| Phase 1 | api-meta sso 块解析 + 两个 API + 连接中心 Tab + 外部接待页按钮（direct_url / ticket_redirect / token_param） | 2-3 天（含三智能体流程） |
| Phase 2 | 10605 真机联调 + form_submit 模式（视 Q1 结论）+ 其他第三方系统接入验证 | 1-2 天 |
