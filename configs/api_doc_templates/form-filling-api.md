# 通用模块接口文档（自动填表 · 通用模板）

<!--
本文件是第三方表单/业务系统的**通用 API 接口文档模板**，按技能名 form-filling-api 命名，
存于 configs/api_doc_templates/。适用判定：子智能体技能白名单包含 form-filling-api 技能。
首发对接系统：10605 售前咨询系统（客户信息 kehuxinxi、跟进记录 lianxijilu）。

- 应用号不写死，统一用 ${APP_ID} 占位符；系统在文档加载期用租户环境变量渲染
  （租户在管理后台「数字员工授权 > 环境变量」配置 APP_ID，与 AGENT_TOKEN 同机制）
- 凭证类占位符（${AGENT_TOKEN}、${client_token}）不在加载期渲染（防止泄漏进 LLM 上下文），
  由 http_api 工具在请求时替换
- 租户文档 storage/tenants/{tid}/templates/{subagent}-api.md 优先级高于本模板，
  模块清单或业务规则有差异的租户仍可单独上传
-->

<!-- api-meta：系统代码解析的机器可读约定块，请勿删除本块或改键名（login_url 必填；user_token_name 缺省默认 client_token；user_token_header / agent_token_header 可选，缺省分别为 Client-Authorize-Token / Api-Authorize-Token；delegate_auto_create 为 true 时委托登录遇到「用户不存在」自动建号，本系统要求 false——手机号无账号必须提示联系管理员；sso_* 可选，声明「外部系统」页面的跳转入口，无 SSO 能力的系统只填 sso_enabled + sso_url） -->
```api-meta
login_url: https://erp${APP_ID}.aidingyi.cn/api/v1/erp.delegate/login
auth_mode: delegate_login
user_token_name: client_token
agent_token_header: Api-Authorize-Token
http_method: POST
delegate_auto_create: false
sso_enabled: true
sso_system_name: CRM 客户信息
sso_mode: ticket_redirect
sso_url: https://erp${APP_ID}.aidingyi.cn/pages/list?id=13&path=kehuxinxi
sso_ticket_param: sso_ticket
sso_grant_url: https://erp${APP_ID}.aidingyi.cn/api/v1/erp.delegate/sso_grant
sso_login_url: https://erp${APP_ID}.aidingyi.cn/api/v1/erp.delegate/sso_login
sso_fallback_url: https://erp${APP_ID}.aidingyi.cn/
```

本系统采用**用户委托登录**：智能体（代理人）持 `agent_token` 代表终端用户（委托人）访问。全部接口为 `POST`，Body 为 `application/json`。

**各模块有哪些字段、字段中文名、是否必填、枚举选项、填写格式，一律不写在本文档中**，由 `module_schema` 元数据接口运行时提供（本智能体对应技能脚本 `load_form_schema.py`，带 Redis 缓存，默认 10 分钟）。**当前有哪些模块可填**由 `open_modules` 接口运行时提供（脚本 `load_open_modules.py`，同一缓存 TTL）。新增模块接入时只需在[模块清单表](#8-模块清单表)加一行（该表仅供人工查阅，运行时以 `open_modules` 为准）。

---

## 1. 通用调用规范

### 请求地址与方式

- BASE_URL：`https://erp${APP_ID}.aidingyi.cn`
- 全部为 `POST`，Body 为 `application/json`

### 鉴权方式（双 Token）

| Header | 来源 | 说明 |
|--------|------|------|
| `Api-Authorize-Token` | 环境变量 `${AGENT_TOKEN}` | 代理人 token，固定不变 |
| `Client-Authorize-Token` | 委托登录接口返回 | 委托人 token，运行时获取 |

- **委托登录接口**仅需 `Api-Authorize-Token`，其余业务接口（含 `module_schema`）**双 Token 缺一不可**，缺失返回 `Code: -99`
- `client_token` 与 `agent_token` 绑定，跨 agent_token 不可用
- **调用约定（本智能体专属）**：业务接口 header 直接写 `${AGENT_TOKEN}` 与 `${client_token}` 占位符，由系统自动替换为真实值；不要向用户询问或转写任何 token
- 委托登录：`POST /api/v1/erp.delegate/login`，入参 `{ "mobile": "委托人手机号" }`，返回 `client_token` 与 `record_id`（委托人在 `sys_user` 表的 id）。手机号不存在统一返回 `Code: -1, Error: "委托登录失败"`——此时提示用户联系管理员建号，**不要重试其他手机号**
- 会话结束调 `POST /api/v1/erp.delegate/logout` 主动失效 `client_token`

### 通用响应结构

HTTP 状态码恒为 200，业务语义在顶层 `Code`：

| 字段 | 类型 | 说明 |
|------|------|------|
| Code | int | `0` 正常；`-1` 业务异常（看 `Error`）；`2` 单据冲突（修改接口，见第 6 节）；`-99` 认证失败（Token 缺失/失效，需重新委托登录） |
| Error | string | 异常信息 |
| Debug | string | 调试信息（异常时） |
| Response | any | 业务数据主体（各接口不同） |
| Trace | string | 请求追踪信息 |

### delegate_login 脚本与本智能体的托管约定

委托登录由技能脚本 `delegate_login.py` 完成（入参 `{"login_url": "...", "mobile": "...", "hold_token": true}`）：

- 返回 `token_held: true` 即登录成功，`client_token` 已由系统托管（不回显、不进对话上下文）
- 返回 `cached: true` 表示命中 23h 内的既有会话，无需重复登录
- `Code=-99`（鉴权失效）时带 `force_refresh: true` 重新执行脚本后重试一次

---

## 2. module 参数、open_modules 与 module_schema 元数据接口

### module 参数规则

- `module` = 业务模块主表表名去掉 `t_` 前缀，如主表 `t_kehuxinxi` → `module: "kehuxinxi"`
- **子表不能作为 module**（子表没有独立接口，随主表走）
- 实际可用字段、字段类型与格式，**以 `module_schema` 返回为准，不要凭记忆硬编码字段名**

### open_modules 接口（先取「可填哪些模块」）

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/open_modules
```

入参：无。模块范围完全由 agent_token 配置的 `open_modules` 白名单 + 委托人角色权限决定，**不接受调用方传入**（防越权枚举）。

鉴权：与业务接口一致（双 Token），但**仅需 `Api-Authorize-Token`（`${AGENT_TOKEN}`）即可调用**（供委托登录前预取清单）；携带 `Client-Authorize-Token`（`${client_token}`）时与委托人角色可写权限（`insert`/`update`）取交集，保证「开放」与「能填」一致。**委托登录完成后调用（两个 header 都带）结果最准确。**

响应（`Response`）：

```json
{
  "modules": [
    { "module": "kehuxinxi", "display_name": "客户", "primary_table": "t_kehuxinxi" },
    { "module": "lianxijilu", "display_name": "跟进记录", "primary_table": "t_lianxijilu" }
  ]
}
```

| 字段 | 说明 |
|------|------|
| modules[] | 开放模块数组；未开放任何模块时为 `[]`（`Code` 仍为 0） |
| modules[].module | 模块名，与 `module_schema` / 业务接口的 `module` 参数口径一致 |
| modules[].display_name | 模块中文名，用于向用户呈现可填表单清单 |
| modules[].primary_table | 主表表名，便于理解数据结构 |

> **先调本接口确定可填模块，再对选定模块调 `module_schema` 取字段。** 第 8 节模块清单表仅供人工查阅，**运行时以本接口返回为准**（不同租户/代理人开放的模块不同，不要硬编码模块名）。

### module_schema 接口

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/module_schema
```

入参：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| module | string | 是 | 业务模块名，如 `kehuxinxi` |

响应结构（`Response`）示例：

```json
{
  "module": "kehuxinxi",
  "display_name": "客户信息",
  "primary_key": "id",
  "tables": [
    {
      "table_name": "t_kehuxinxi",
      "display_name": "客户信息",
      "role": "main",
      "foreign_key": null,
      "attrs": [
        { "attr_name": "xingming", "display_name": "客户名称", "component": "input", "required": true },
        { "attr_name": "tuiguangqudao", "display_name": "推广渠道", "component": "select", "required": false,
          "options": [{ "label": "微信私聊", "value": "微信私聊" }] },
        { "attr_name": "genjinhuizongzhaiyao", "display_name": "跟进汇总摘要", "component": "textarea", "required": false,
          "create_hidden": true, "note": "由填写跟进记录后归纳更新，新建时无需填写" },
        { "attr_name": "id", "display_name": "系统编号", "component": "input", "required": false, "is_system": true }
      ]
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| tables[] | 覆盖主表与子表；`role: "main"/"child"`，子表 `foreign_key` 为关联主表的字段（如 `fid`） |
| attrs[].attr_name | 字段英文名，写入接口的 data key |
| attrs[].display_name | 字段中文名，回显确认用 |
| attrs[].component | 控件类型（`input`/`select`/`date`/`datetime`/`textarea`/`integer`/`number` 等），同时决定列表 filters 的 value 格式（见第 3 节） |
| attrs[].required | 新增时是否必填（以编辑页 mandatory 元数据为准） |
| attrs[].options | 枚举选项 `[{label, value}]`，写入/查询取 `value`；无枚举时省略 |
| attrs[].note | 字段特殊说明（如"由填写跟进记录后归纳更新，新建时无需填写"）；未配置时省略 |
| attrs[].create_hidden | `true` = 新增场景无需填写 |
| attrs[].edit_hidden | `true` = 修改场景不可改 |
| attrs[].is_system | `true` = 系统字段（`id`/`sid`/`create_user`/`create_time`/`update_time`/`status` 等），由后端自动处理，跳过用户填写与回显确认 |

> **子表说明**：有子表的模块会在 `tables[]` 中返回 `role: "child"` 的元素（`foreign_key` 指向主表），其字段元数据同样可用于查询与回显确认；但**新增/修改接口的子表写入暂不开放**，data 只提交主表。

**值格式约定**（写入/查询时 data 的 value 格式）：

| component | value 格式 |
|-----------|-----------|
| `input` / `textarea` | 字符串 |
| `select` | 取对应 `options[].value` 的字符串 |
| `date` | `YYYY-MM-DD` |
| `datetime` | `YYYY-MM-DD HH:mm:ss` |
| `integer` / `number` | 数字 |

---

## 3. 列表查询

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/module_listing_view
```

入参：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| module | string | 是 | 业务模块名 |
| filters | array | 否 | 过滤条件 |
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 20 |
| order | array | 否 | 排序规则，默认按模块默认排序 |

**filters 元素结构**：

```json
{ "attr": "字段英文名", "value": "按 component 定", "component": "字段控件类型", "empty": false, "exact": false }
```

| component | value 格式 | 匹配方式 |
|-----------|-----------|----------|
| `input` | **数组** `["值"]` | LIKE 模糊查询 |
| `date` / `datetime` | 数组 `[开始, 结束]` | 时间范围 |
| `integer` / `number` | 数组 `[最小值, 最大值]` | 数值范围 |
| `select` | 字符串 | 精确匹配 |

多个 filter 之间为 **AND** 关系；不指定 `component` 默认按 `input` 处理；`empty`（为空匹配）与 `exact`（精确匹配）为可选 bool。

**已踩坑（务必遵守）**：

1. **`component: input` 的 `value` 必须传数组**。传字符串会被逐字符拆成单字符 `OR LIKE`，导致匹配所有含任一字符的记录——例如传 `"13916323347"` 会命中任意电话。正确写法 `["13916323347"]`。
2. **`datetime`/`date` 范围的 `value[1]` 必须带时分秒**。写 `"2026-06-30"` 实际表示当天 `00:00:00`，用户说「6月30日之前」应译为 `["1900-01-01 00:00:00", "2026-06-30 23:59:59"]`。
3. 查询值应为**完整值**而非用户口语片段；如需精确匹配电话号码，加 `"exact": true`。

**响应结构**：

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['data'] | array | 记录列表（字典数组，key 为 attr_name） |
| Response['data2'] | array | 子表数据（无子表时为空数组） |
| Response['fields'] | array | 本视图字段元数据（`attr_name`/`display_name`/`options`） |
| Response['total'] | int | 总记录数，用于分页 |
| Response['total_sum'] | object | 汇总数据（无汇总时为空对象） |

示例（按手机号查客户，用于创建前查重）：

```json
{
  "module": "kehuxinxi",
  "filters": [
    { "attr": "lianxidianhua", "value": ["13916323347"], "component": "input", "exact": true }
  ],
  "page": 1, "limit": 20
}
```

---

## 4. 详情

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/module_prepare_edit
```

入参：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| module | string | 是 | 业务模块名 |
| did | int | 是 | 记录主键 id |

响应 `Response.tables[]`：

| 路径 | 说明 |
|------|------|
| tables[i].table_name / display_name | 表名 / 表中文名 |
| tables[i].primary_key / foreign_key | 主键字段名 / 外键字段名（主表为 null，子表为 `fid`） |
| tables[i].data | 主表为单条字典；子表为字典数组 |
| tables[i].sections[].attrs | 字段元数据（同 module_schema，精简为 `attr_name`/`display_name`/`options`） |

> **系统字段值格式差异**：详情接口中系统字段（`create_user`、`create_group`、`status_approve` 等）的值为 `{label, value}` 对象，业务字段为扁平值。写入接口时不要把这种对象原样传回。

---

## 5. 新增

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/module_data_update
```

入参骨架：

```json
{
  "module": "kehuxinxi",
  "did": null,
  "temp": false,
  "tables": [
    {
      "table": "t_kehuxinxi",
      "method": "insert",
      "data": [
        { "xingming": "张三", "update_time": "", "id": "" }
      ]
    }
  ]
}
```

要点：

- `did` 固定传 `null`；`tables[0].table` 为模块主表表名，`method` 固定 `"insert"`；`temp` 固定 `false`
- `data` 为单元素数组；`update_time` 与 `id` 固定传**空字符串** `""`
- 只传业务字段（跳过 `is_system: true` 与 `create_hidden: true` 的字段），value 格式按 `component` 约定（第 2 节）
- **`create_user` 可省略**：后端自动补为当前委托人 `record_id`；显式传入也会被系统默认值覆盖
- 返回：`Response` = 新建记录的 id（int），可用作后续修改/详情的 `did`，回执中应带上

---

## 6. 修改

```
POST https://erp${APP_ID}.aidingyi.cn/api/v1/erp.module/module_data_update
```

入参骨架（只改状态字段为例）：

```json
{
  "module": "kehuxinxi",
  "did": 8,
  "temp": false,
  "tables": [
    {
      "table": "t_kehuxinxi",
      "method": "update",
      "data": [
        { "id": 8, "zhuangtai": "待跟进", "update_time": "2026-08-27 16:33:00" }
      ]
    }
  ]
}
```

要点：

- `did` 必填（记录主键）；`data` 中 **`id` 必须传**（等于 `did`）
- **`data` 只传需要修改的字段**，避免覆盖未变更数据
- 建议传 `update_time`（`YYYY-MM-DD HH:mm:ss`）。若所传时间早于库中更新时间，返回 `Code: 2`「单据已发生修改，无法继续保存」；校验通过后后端覆盖为服务器时间。拿不准时先调详情接口取最新 `update_time`
- `edit_hidden: true` 的字段不可改（写了也不会生效）
- **身份过滤**：委托登录下，后端在 update 的 where 条件自动追加身份过滤。修改不属于当前委托人的记录，接口表面返回成功但实际影响 0 行（数据未变、不报错）——回执前如需确认，可重新查询核对
- 返回：`Response` 为空数组 `[]`

---

## 7. 数据权限

委托登录下，业务数据按委托人角色的数据范围（`personal_scope`）**自动过滤**——列表/详情仅返回委托人自己创建的数据，新增/修改自动归属委托人。**无需也不能**在 filters 中手动指定 `create_user`。用户查不到他人数据属于正常现象，不要向用户透露其他客户的数据。

---

## 8. 模块清单表

| module | 中文名 | 主表 | 备注 |
|--------|--------|------|------|
| kehuxinxi | 客户信息 | t_kehuxinxi | 见第 9 节模块备注 |
| lianxijilu | 跟进记录 | t_lianxijilu | 见第 10 节模块备注 |

新增模块由系统方在此表加行（module 名 / 中文名 / 主表 / 备注），文档其余部分不变。

> 运行时以 [`open_modules` 接口](#open_modules-接口先取可填哪些模块)返回的模块清单为准（由 agent_token 配置 + 委托人角色权限决定），本表仅供人工查阅。

---

## 9. 模块备注：客户信息（kehuxinxi）

### 跟进汇总摘要同步（重要业务规则）

`genjinhuizongzhaiyao`（跟进汇总摘要）用于沉淀客户跟进状态的累计摘要，新建客户时无需填写（`create_hidden: true`），由填写跟进记录后归纳更新：

1. 每次填写跟进记录后，判断该客户**最新的客户状态** `zhuangtai`（取值见 `module_schema` 返回的 options）
2. 结合本次跟进记录与历史跟进情况，归纳最新的跟进汇总摘要 `genjinhuizongzhaiyao`（建议控制在 200 字以内）；**保留历史摘要条目**，按天分条追加，条与条之间用 CRLF（`\r\n\r\n`）分隔
3. 调用修改接口（第 6 节），同时更新 `zhuangtai`、`genjinhuizongzhaiyao` 两个字段
4. 若本次跟进**没有重要信息**，客户状态与摘要均无变化，可跳过修改

### 修改客户时的建议动作

修改客户状态时需同时更新 `zhuangtai` 与 `genjinhuizongzhaiyao`，保持状态与摘要一致；`beizhu`（备注）可按需更新。

---

## 10. 模块备注：跟进记录（lianxijilu）

### 创建前的必要步骤

创建跟进记录前，应**先查询客户信息**确认客户存在，并回填客户基本信息：

1. 调用客户列表接口，按 `xingming` 或 `lianxidianhua` 查找客户
2. 若客户不存在，先创建客户信息
3. 从客户记录中提取字段，按映射表传入跟进记录

### 字段映射（客户信息 → 跟进记录）

| 客户信息字段 | 跟进记录字段 | 说明 |
|-------------|-------------|------|
| `xingming` | `kehuxingming` | 客户姓名，**必须与客户记录的 `xingming` 完全一致**（原样取自客户记录，不要转写） |
| `lianxiren` | `lianxiren` | 联系人 |
| `lianxidianhua` | `lianxifangshi` | 联系电话（注意字段名不同） |
| `weixinhao` | `weixinhao` | 微信号 |
| `suoshuhangye` | `suoshuhangye` | 所属行业 |

> 跟进记录中这些字段虽有 `edit_hidden`（前端编辑表单不可改），但**创建时必须由智能体从客户信息中获取并主动传入**，后端不会自动同步。填写跟进记录后按第 9 节同步客户状态与摘要。

---

## 11. 权限与写入约束

- 写操作（insert/update）由委托人角色决定，无权限时返回 `Code: -1, Error: "委托登录无 insert/update 权限：模块 xxx"`——告知用户联系管理员开通权限
- **删除接口不对外提供**：委托登录下物理删除被后端硬编码禁止。业务上的「删除」语义应通过修改状态字段实现（如将客户状态改为「已流失」），且须先回显确认再执行
