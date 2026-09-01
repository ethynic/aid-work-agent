# 电商ERP接口文档

本文档涵盖电商ERP系统（应用编号11022）的委托登录、数据读取类接口（列表、详情）和订单写入类接口（创建、修改）。后端数据库为 MySQL 5.7。

本系统采用**委托登录**鉴权：AI 智能体（代理人）持 `agent_token` 代表终端用户（委托人）访问，业务接口须同时携带 `agent_token + client_token`。

---

## 1. 通用调用规范

### 请求地址

BASE_URL：`https://erp11022.aidingyi.cn`

### 请求方式

POST，Body 为 `application/json`

### 鉴权方式（双 Token）

| Header | 来源 | 说明 |
|--------|------|------|
| `Api-Authorize-Token` | `.env` 环境变量 `${AGENT_TOKEN}` | 代理人 token，固定不变，标识 AI 智能体身份 |
| `Client-Authorize-Token` | 委托登录接口返回 | 委托人 token，标识终端用户（客户）身份，运行时获取 |

- **委托登录接口**（第 2 节）：仅需 `Api-Authorize-Token`
- **业务接口**（第 3 节起）：双 Token 缺一不可，缺失返回 `Code: -99`
- `client_token` 与 `agent_token` 绑定，跨 agent_token 不可用

### 返回响应

- HTTP 状态码：`200`，Content-Type：`application/json`
- 顶层结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| Code | int | `0` 正常；`-1` 业务异常；`-99` 认证失败（client_token 失效或缺失，需重新委托登录） |
| Error | string | 异常信息 |
| Debug | string | 调试信息（异常时详细输出） |
| Response | object | 业务数据主体 |
| Slow | array | 慢查询日志 |
| Trace | string | 请求追踪信息 |

### 列表接口通用约定

适用于所有 `module_listing_view` 接口。

**分页与排序参数**（Body）

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 20 |
| order | array | 否 | 排序规则，默认 `[]`，由后端按字段默认排序 |

**filters 元素结构**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| attr | string | 是 | 字段英文名 |
| value | any | 是 | 匹配值，规则见下表 |
| display_name | string | 否 | 字段中文名 |
| component | string | 否 | 字段组件类型，用于判断 value 格式 |

**value 类型规则**

| component 类型 | value 格式 | 匹配方式 |
|----------------|-----------|----------|
| `input` | 数组 `["值"]` | LIKE 模糊查询（**必须传数组**，见下方说明） |
| `datetime` | 数组 `[开始, 结束]` | 时间范围查询 |
| `integer` / `number` / `currency` | 数组 `[最小值, 最大值]` | 数值范围查询 |

多个 filter 之间为 **AND** 关系。不指定 `component`，默认为 `input`。

**input 组件 value 必须传数组（重要）**

`component: input` 的 `value` **必须传数组**（如 `["运动背包"]`、`["13916323347"]`），后端按 `LIKE %%值%%` 模糊拼接。**切勿传字符串**：后端会逐字符遍历 value，字符串会被拆成单字符 `OR LIKE`，导致匹配所有含任一字符的记录（例如传 `"13916323347"` 会命中任意手机号）。可选字段：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| empty | bool | false | `true` 表示"为空"匹配（`value` 被忽略） |
| exact | bool | false | `true` 改为精确匹配（`=`） |

**返回响应结构**

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['data'] | array | 匹配的记录列表 |
| Response['data2'] | array | 子表数据（无子表字段时为空数组） |
| Response['fields'] | array | 字段元数据，可用于动态渲染 |
| Response['total'] | int | 总记录数（用于分页） |
| Response['total_sum'] | object | 汇总数据（无汇总时为空对象） |

> `fields` 元素包含 `attr_name`、`display_name`、`options`；`options` 内部仅含 `label`、`value`，无选项时不返回 `options` 键。各接口示例仅列部分字段，实际以接口返回为准。

### 数据权限说明

委托登录下，业务接口返回的数据受委托人身份自动过滤，无需在 filters 中手动指定：

| 模块 | 数据范围 |
|------|---------|
| 客户信息（kehuxinxi） | 仅自己（1 条） |
| 客户订单（kehudingdan） | 仅自己名下的订单 |
| 商品管理（shangpinguanli） | 所有商品（无身份过滤） |

---

## 2. 委托登录接口

委托登录流程：终端用户提供手机号 -> AI 智能体调 `login` 获取 `client_token` -> 业务接口携带双 token -> 会话结束调 `logout`。

> 本系统只校验手机号在客户信息表中**存在**，不校验手机号属于提问者本人。手机号真实性验证（短信验证码等）由 AI 智能体自行完成。

### 2.1 委托登录（获取 client_token）

**接口地址**

```
POST https://erp11022.aidingyi.cn/api/v1/erp.delegate/login
```

**请求参数**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| mobile | string | 是 | 委托人手机号 |

Header：`Api-Authorize-Token: ${AGENT_TOKEN}`

**请求示例**

```json
{ "mobile": "13916323348" }
```

**返回响应**

| 字段 | 类型 | 说明 |
|------|------|------|
| client_token | string | 委托人 token，后续业务接口放在 `Client-Authorize-Token` Header，有效期 10 天 |
| record_id | int | 委托人在客户信息表中的 id，订单创建时 `kehu` 字段必须填此值 |
| role_id | int | 委托人角色 id |
| display_name | string | 委托人显示名（如"覃女士"） |
| agent_name | string | 代理人名称 |

**响应示例**

```json
{
  "Code": 0,
  "Response": {
    "client_token": "e68dbb166da30b1456f69504ecd0d7c09c489ade2def8ba60f97d3bb35f5c712",
    "record_id": 2,
    "role_id": 2,
    "display_name": "覃女士",
    "agent_name": "电商客服智能体"
  }
}
```

> 手机号不存在时统一返回 `Code: -1, Error: "委托登录失败"`，不暴露"手机号不存在"细节。

### 2.2 委托人信息查询（校验 client_token）

**接口地址**

```
POST https://erp11022.aidingyi.cn/api/v1/erp.delegate/info
```

Header：`Api-Authorize-Token` + `Client-Authorize-Token`

**返回响应**

```json
{
  "Code": 0,
  "Response": {
    "record_id": 2,
    "role_id": 2,
    "display_name": "覃女士",
    "agent_name": "电商客服智能体",
    "identity_table": "t_kehuxinxi"
  }
}
```

用于校验 `client_token` 是否有效。返回 `Code: -99` 表示已失效，需重新调 `login`。

## 3. 客户信息列表接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_listing_view
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehuxinxi"` |
| filters | array | 否 | 过滤条件，参见第 1 节 filters 约定 |
| page, limit | - | - | 参见第 1 节分页参数 |

**请求示例**

```json
{
  "module": "kehuxinxi",
  "filters": [
    { "attr": "kehushouji", "value": ["13916323347"], "component": "input" }
  ],
  "page": 1,
  "limit": 20
}
```

### 返回响应

**响应示例**

```json
{
  "Code": 0,
  "Response": {
    "data": [
      {
        "id": 1, "sid": "KHXX2026-000001",
        "kehumingcheng": "孙小姐", "kehushouji": "13916323347",
        "kehubianhao": null, "beizhu": null,
        "create_user": "孙晨", "create_time": "2026-06-02 16:16:42",
        "status": 1, "status_approve": 0
      }
    ],
    "data2": [],
    "fields": [
      { "attr_name": "sid", "display_name": "业务编号" },
      { "attr_name": "kehubianhao", "display_name": "客户编号" },
      { "attr_name": "kehumingcheng", "display_name": "客户名称" },
      { "attr_name": "kehushouji", "display_name": "客户手机" },
      { "attr_name": "beizhu", "display_name": "备注" },
      { "attr_name": "create_time", "display_name": "创建时间" }
    ],
    "total": 1,
    "total_sum": {}
  }
}
```

---

## 4. 客户信息详情接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_prepare_edit
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehuxinxi"` |
| did | int | 是 | 客户记录 ID |

**请求示例**

```json
{ "module": "kehuxinxi", "did": 1 }
```

### 返回响应

> 响应顶层仅返回 `tables`。`tables[X]` 包含 `table_name`、`display_name`、`primary_key`、`foreign_key`、`parent_table`、`sections`、`data`；`sections[Y]` 包含 `display_name`、`attrs`；`attrs[Z]` 包含 `attr_name`、`display_name`、`options`（无选项时不含 `options` 键）；`options` 内部仅包含 `label`、`value`。

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['tables'][0]['data'] | object | 客户主表信息（单条，字典） |
| Response['tables'][1]['data'] | array | 客户收货地址（多条，数组） |
| Response['tables'][i]['table_name'] | string | 表名 |
| Response['tables'][i]['display_name'] | string | 表中文名 |
| Response['tables'][i]['primary_key'] | string | 主键字段名 |
| Response['tables'][i]['foreign_key'] | string\|null | 外键字段名（子表为 `fid`，主表为 null） |
| Response['tables'][i]['parent_table'] | string\|null | 父表名（主表为 null） |
| Response['tables'][i]['sections'] | array | 字段分区定义 |
| Response['tables'][i]['sections'][j]['attrs'] | array | 分区下的字段元数据（白名单过滤后） |

**响应示例**

```json
{
  "Code": 0,
  "Response": {
    "tables": [
      {
        "display_name": "客户信息",
        "table_name": "t_kehuxinxi",
        "primary_key": "id",
        "foreign_key": null,
        "parent_table": null,
        "data": {
          "id": 1, "sid": "KHXX2026-000001",
          "kehumingcheng": "孙小姐", "kehushouji": "13916323347",
          "kehubianhao": null, "beizhu": null,
          "create_user": { "label": "孙晨", "value": 1 },
          "create_group": { "label": "默认组", "value": 1 },
          "create_time": "2026-06-02 16:16:42",
          "update_time": "2026-06-02 16:19:03",
          "status": 1,
          "status_approve": { "label": "未发起", "value": 0 }
        },
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "kehubianhao", "display_name": "客户编号" },
              { "attr_name": "kehumingcheng", "display_name": "客户名称" },
              { "attr_name": "kehushouji", "display_name": "客户手机" },
              { "attr_name": "beizhu", "display_name": "备注" }
            ]
          },
          {
            "display_name": "系统信息",
            "attrs": [
              { "attr_name": "sid", "display_name": "业务编号" },
              { "attr_name": "create_user", "display_name": "创建人" },
              { "attr_name": "create_time", "display_name": "创建时间" },
              { "attr_name": "update_time", "display_name": "更新时间" },
              { "attr_name": "status", "display_name": "系统状态" },
              { "attr_name": "status_approve", "display_name": "审批状态" }
            ]
          }
        ]
      },
      {
        "display_name": "收货地址",
        "table_name": "t_kehuxinxi_2",
        "primary_key": "id",
        "foreign_key": "fid",
        "parent_table": "t_kehuxinxi",
        "data": [
          {
            "id": 1, "fid": 1,
            "shouhuoren": "孙晨", "shouji": "13916323347",
            "shengshiqu": "上海徐汇区", "xiangxidizhi": "南宁路1000号",
            "shifoumorendizhi": "是"
          },
          {
            "id": 2, "fid": 1,
            "shouhuoren": "孙晨", "shouji": "13916323347",
            "shengshiqu": "上海嘉定区", "xiangxidizhi": "芳林路958号",
            "shifoumorendizhi": "否"
          }
        ],
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "shouhuoren", "display_name": "收货人" },
              { "attr_name": "shouji", "display_name": "手机" },
              { "attr_name": "shengshiqu", "display_name": "省市区" },
              { "attr_name": "xiangxidizhi", "display_name": "详细地址" },
              {
                "attr_name": "shifoumorendizhi", "display_name": "是否默认地址",
                "options": [
                  { "label": "是", "value": "是" },
                  { "label": "否", "value": "否" }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}
```

---

## 5. 客户订单列表接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_listing_view
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehudingdan"` |
| filters | array | 否 | 过滤条件，参见第 1 节 filters 约定 |
| page, limit | - | - | 参见第 1 节分页参数 |

**请求示例**

```json
{
  "module": "kehudingdan",
  "filters": [
    { "attr": "shouji", "display_name": "手机", "value": ["13916323347"], "component": "input" },
    { "attr": "xiadanshijian", "display_name": "下单时间", "value": ["2026-06-02 00:00:00", "2026-06-04 23:59:59"], "component": "datetime" }
  ],
  "page": 1,
  "limit": 20
}
```

**关于 datetime 截止时间的注意事项**

`value[1]` 必须带时分秒。写 `"2026-06-04"` 实际表示 `"2026-06-04 00:00:00"` 而非 `2026-06-04 23:59:59`，与口头表述不同。用户说"6月2日到6月4日之间"应翻译为 `["2026-06-02 00:00:00", "2026-06-04 23:59:59"]`。

### 返回响应

通用返回结构见第 1 节。以下为业务字段示例（实际 `fields` 以接口返回为准）：

**响应示例**

```json
{
  "Code": 0,
  "Response": {
    "data": [
      {
        "sid": "KHDD2026-000001",
        "kehu": "孙小姐",
        "shouhuoren": "孙小姐",
        "shouji": "13916323347",
        "xiadanshijian": "2026-06-01 13:30:00",
        "shengshiqu": "上海市,徐汇区",
        "xiangxidizhi": "南宁路1000号",
        "dingdanzhuangtai": "已支付",
        "fahuozhuangtai": "未发货",
        "shangpinjine": "208.00",
        "youhuijine": "40.00",
        "yunfei": null,
        "dingdanjine": "168.00",
        "zhifufangshi": "支付宝",
        "zhifujine": null,
        "id": 1,
        "create_user": "孙晨",
        "create_group": "默认组",
        "update_user": "孙晨",
        "create_time": "2026-06-02 16:45:24",
        "update_time": "2026-06-02 17:05:16",
        "archive_time": null,
        "status": 1,
        "status_approve": 0
      }
    ],
    "data2": [],
    "fields": [
      { "attr_name": "kehu", "display_name": "客户" },
      { "attr_name": "shouhuoren", "display_name": "收货人" },
      { "attr_name": "shouji", "display_name": "手机" },
      { "attr_name": "xiadanshijian", "display_name": "下单时间" },
      { "attr_name": "shengshiqu", "display_name": "省市区" },
      { "attr_name": "xiangxidizhi", "display_name": "详细地址" },
      { "attr_name": "dingdanzhuangtai", "display_name": "订单状态", "options": [{"label": "待支付", "value": "待支付"}, {"label": "已支付", "value": "已支付"}, {"label": "已发货", "value": "已发货"}, {"label": "已完成", "value": "已完成"}, {"label": "已取消", "value": "已取消"}] },
      { "attr_name": "fahuozhuangtai", "display_name": "发货状态", "options": [{"label": "未发货", "value": "未发货"}, {"label": "部分发货", "value": "部分发货"}, {"label": "已发货", "value": "已发货"}] },
      { "attr_name": "shangpinjine", "display_name": "商品金额" },
      { "attr_name": "youhuijine", "display_name": "优惠金额" },
      { "attr_name": "yunfei", "display_name": "运费" },
      { "attr_name": "dingdanjine", "display_name": "订单金额" },
      { "attr_name": "zhifufangshi", "display_name": "支付方式", "options": [{"label": "微信", "value": "微信"}, {"label": "支付宝", "value": "支付宝"}, {"label": "银行卡", "value": "银行卡"}] },
      { "attr_name": "zhifujine", "display_name": "支付金额" },
      { "attr_name": "sid", "display_name": "业务编号" },
      { "attr_name": "id", "display_name": "系统编号" },
      { "attr_name": "create_user", "display_name": "创建人" },
      { "attr_name": "create_group", "display_name": "创建组" },
      { "attr_name": "update_user", "display_name": "修改人" },
      { "attr_name": "create_time", "display_name": "创建时间" },
      { "attr_name": "update_time", "display_name": "更新时间" },
      { "attr_name": "archive_time", "display_name": "归档时间" },
      { "attr_name": "status", "display_name": "系统状态" },
      { "attr_name": "status_approve", "display_name": "审批状态" }
    ],
    "total": 1,
    "total_sum": {
      "dingdanjine": "168.00",
      "shangpinjine": "208.00",
      "youhuijine": "40.00",
      "yunfei": null,
      "zhifujine": null
    }
  }
}
```

---

## 6. 客户订单详情接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_prepare_edit
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehudingdan"` |
| did | integer | 是 | 客户订单 id |

**请求示例**

```json
{ "module": "kehudingdan", "did": 1 }
```

### 返回响应

> 响应顶层仅返回 `tables`。`tables[X]` 结构同第 4 节。

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['tables'][0]['data'] | object | 客户订单主表信息（单条，字典） |
| Response['tables'][1]['data'] | array | 优惠明细（多条，数组） |
| Response['tables'][2]['data'] | array | 订单产品明细（多条，数组） |

> 主表字段定义与第 5 节的 `fields` 一致，此处不再重复列出完整元数据。以下仅展示子表字段和示例数据。

**响应示例（已精简）**

```json
{
  "Code": 0,
  "Response": {
    "tables": [
      {
        "display_name": "客户订单",
        "table_name": "t_kehudingdan",
        "primary_key": "id",
        "foreign_key": null,
        "parent_table": null,
        "data": {
          "sid": "KHDD2026-000001",
          "kehu": { "label": "孙小姐", "value": "1" },
          "shouhuoren": "孙小姐",
          "shouji": "13916323347",
          "xiadanshijian": "2026-06-01 13:30:00",
          "shengshiqu": "上海市,徐汇区",
          "xiangxidizhi": "南宁路1000号",
          "dingdanzhuangtai": "已支付",
          "fahuozhuangtai": "未发货",
          "shangpinjine": "208.00",
          "youhuijine": "40.00",
          "yunfei": null,
          "dingdanjine": "168.00",
          "zhifufangshi": "支付宝",
          "zhifujine": null,
          "id": 1,
          "create_user": { "label": "孙晨", "value": 1 },
          "create_group": { "label": "默认组", "value": 1 },
          "update_user": { "label": "孙晨", "value": 1 },
          "create_time": "2026-06-02 16:45:24",
          "update_time": "2026-06-02 17:05:16",
          "archive_time": null,
          "status": 1,
          "status_approve": { "label": "未发起", "value": 0 }
        },
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "kehu", "display_name": "客户" },
              { "attr_name": "shouhuoren", "display_name": "收货人" },
              { "attr_name": "shouji", "display_name": "手机" },
              { "attr_name": "xiadanshijian", "display_name": "下单时间" },
              { "attr_name": "shengshiqu", "display_name": "省市区" },
              { "attr_name": "xiangxidizhi", "display_name": "详细地址" },
              { "attr_name": "dingdanzhuangtai", "display_name": "订单状态" },
              { "attr_name": "fahuozhuangtai", "display_name": "发货状态" },
              { "attr_name": "shangpinjine", "display_name": "商品金额" },
              { "attr_name": "youhuijine", "display_name": "优惠金额" },
              { "attr_name": "yunfei", "display_name": "运费" },
              { "attr_name": "dingdanjine", "display_name": "订单金额" },
              { "attr_name": "zhifufangshi", "display_name": "支付方式" },
              { "attr_name": "zhifujine", "display_name": "支付金额" }
            ]
          },
          {
            "display_name": "系统信息",
            "attrs": [
              { "attr_name": "id", "display_name": "系统编号" },
              { "attr_name": "sid", "display_name": "业务编号" },
              { "attr_name": "create_user", "display_name": "创建人" },
              { "attr_name": "create_group", "display_name": "创建组" },
              { "attr_name": "update_user", "display_name": "修改人" },
              { "attr_name": "create_time", "display_name": "创建时间" },
              { "attr_name": "update_time", "display_name": "更新时间" },
              { "attr_name": "archive_time", "display_name": "归档时间" },
              { "attr_name": "status", "display_name": "系统状态" },
              { "attr_name": "status_approve", "display_name": "审批状态" }
            ]
          }
        ]
      },
      {
        "display_name": "优惠明细",
        "table_name": "t_kehudingdan_2",
        "primary_key": "id",
        "foreign_key": "fid",
        "parent_table": "t_kehudingdan",
        "data": [
          { "fid": 1, "id": 1, "youhuiquanhuodong": "消费券200-25", "youhuiquanjine": "25.00" },
          { "fid": 1, "id": 2, "youhuiquanhuodong": "平台加补券150-15", "youhuiquanjine": "15.00" }
        ],
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "youhuiquanhuodong", "display_name": "优惠券活动" },
              { "attr_name": "youhuiquanjine", "display_name": "优惠券金额" },
              { "attr_name": "serial", "display_name": "序号" }
            ]
          }
        ]
      },
      {
        "display_name": "订单产品",
        "table_name": "t_kehudingdan_3",
        "primary_key": "id",
        "foreign_key": "fid",
        "parent_table": "t_kehudingdan",
        "data": [
          {
            "chanpinbianhao": "248",
            "chanpinmingcheng": "得宝（TEMPO）卷筒卫生纸 T4680 四层 160g/卷 10卷/提",
            "danjia": "52.00",
            "guigeshuxing": "黑色&36码&皮的",
            "fid": 1, "id": 1,
            "jiliangdanwei": "支",
            "pinpai": "得宝（TEMPO）",
            "tuidanshuliang": null,
            "tuihuoyuanyin": "",
            "tuihuozhuangtai": "",
            "wuliudanhao": "",
            "wuliugongsi": "",
            "xiadanshuliang": 4,
            "yifahuoshuliang": null,
            "youhuijine": "40.00",
            "zongji": "208.00"
          }
        ],
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "chanpinbianhao", "display_name": "产品编号" },
              { "attr_name": "chanpinmingcheng", "display_name": "产品名称" },
              { "attr_name": "pinpai", "display_name": "品牌" },
              { "attr_name": "guigeshuxing", "display_name": "规格属性", "component": "input" },
              { "attr_name": "jiliangdanwei", "display_name": "计量单位" },
              { "attr_name": "xiadanshuliang", "display_name": "下单数量" },
              { "attr_name": "danjia", "display_name": "单价" },
              { "attr_name": "zongji", "display_name": "总计" },
              { "attr_name": "youhuijine", "display_name": "优惠金额" },
              { "attr_name": "yifahuoshuliang", "display_name": "已发货数量" },
              { "attr_name": "wuliugongsi", "display_name": "物流公司" },
              { "attr_name": "wuliudanhao", "display_name": "物流单号" },
              { "attr_name": "tuidanshuliang", "display_name": "退单数量" },
              { "attr_name": "tuihuoyuanyin", "display_name": "退货原因" },
              { "attr_name": "tuihuozhuangtai", "display_name": "退货状态", "options": [{"label": "待处理", "value": "待处理"}, {"label": "已接收", "value": "已接收"}, {"label": "处理中", "value": "处理中"}, {"label": "已完成", "value": "已完成"}] },
              { "attr_name": "serial", "display_name": "序号" }
            ]
          }
        ]
      }
    ]
  }
}
```

---

## 7. 产品列表接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_listing_view
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"shangpinguanli"` |
| filters | array | 否 | 过滤条件，参见第 1 节 filters 约定 |
| page, limit, order | - | - | 参见第 1 节分页与排序参数 |

**请求示例**

```json
{
  "module": "shangpinguanli",
  "filters": [
    {
      "attr": "chanpinmingcheng",
      "display_name": "产品名称",
      "value": ["运动背包"],
      "component": "input"
    }
  ],
  "page": 1,
  "limit": 20
}
```

### 返回响应

通用返回结构见第 1 节。本接口额外字段：

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['data2'] | array | 二级数据集（通常为空） |
| Response['total_sum']['xiaoshoujiahanshui'] | string | 销售价（含税）合计 |

**响应示例（精简）**

```json
{
  "Code": 0,
  "Response": {
    "data": [
      {
        "id": 320, "sid": "", "skubianhao": "YD0036",
        "chanpinmingcheng": "男女同款运动背包",
        "pinpai": "", "jiliangdanwei": "",
        "suoshulanmu": "运动类",
        "xiaoshoujiahanshui": "59.90",
        "guigechicun": "均码", "chanpinxinghao": "",
        "yanse": "黑色、灰色、蓝色、红色", "caizhi": "",
        "banxing": "双肩款", "shichuanwendu": "10-30℃ 四季通用",
        "fahuoshixiao": "72小时内发货",
        "chanpinjieshao": "防水耐磨聚酯纤维面料，多层设计，大容量收纳，透气减压肩带，户外登山、徒步、健身、日常通勤均可",
        "create_user": "孙晨", "create_group": null,
        "create_time": "2026-06-17 13:08:34",
        "update_time": null, "update_user": null,
        "archive_time": null,
        "status": 1, "status_approve": 0
      }
    ],
    "data2": [],
    "fields": [
      { "attr_name": "skubianhao", "display_name": "SKU编号" },
      { "attr_name": "chanpinmingcheng", "display_name": "产品名称" },
      { "attr_name": "pinpai", "display_name": "品牌" },
      { "attr_name": "jiliangdanwei", "display_name": "计量单位" },
      { "attr_name": "suoshulanmu", "display_name": "所属栏目" },
      { "attr_name": "xiaoshoujiahanshui", "display_name": "销售价(含税)" },
      { "attr_name": "guigechicun", "display_name": "规格尺寸" },
      { "attr_name": "chanpinxinghao", "display_name": "产品型号" },
      { "attr_name": "yanse", "display_name": "颜色" },
      { "attr_name": "caizhi", "display_name": "材质" },
      { "attr_name": "banxing", "display_name": "版型" },
      { "attr_name": "shichuanwendu", "display_name": "适穿温度" },
      { "attr_name": "fahuoshixiao", "display_name": "发货时效" },
      { "attr_name": "chanpinjieshao", "display_name": "产品介绍" },
      { "attr_name": "sid", "display_name": "业务编号" },
      { "attr_name": "id", "display_name": "系统编号" },
      { "attr_name": "create_user", "display_name": "创建人" },
      { "attr_name": "create_group", "display_name": "创建组" },
      { "attr_name": "update_user", "display_name": "修改人" },
      { "attr_name": "create_time", "display_name": "创建时间" },
      { "attr_name": "update_time", "display_name": "更新时间" },
      { "attr_name": "archive_time", "display_name": "归档时间" },
      { "attr_name": "status", "display_name": "系统状态" },
      { "attr_name": "status_approve", "display_name": "审批状态" }
    ],
    "total": 1,
    "total_sum": { "xiaoshoujiahanshui": "59.90" }
  }
}
```

**关键字段说明**

| 字段 | 说明 |
|------|------|
| suoshulanmu | 所属栏目，列表中为扁平字符串（如 `"运动类"`）；详情接口中为 `{label, value}` 对象，关联到 `shangpinleimu` 模块 |
| xiaoshoujiahanshui | 销售价（含税），货币类型，`total_sum` 中同名字段为当前查询结果的合计 |

---

## 8. 产品详情接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_prepare_edit
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"shangpinguanli"` |
| did | integer | 是 | 产品 id |

**请求示例**

```json
{ "module": "shangpinguanli", "did": 320 }
```

### 返回响应

> 响应顶层仅返回 `tables`。主表字段定义与第 7 节的 `fields` 一致，此处不再重复列出完整元数据。
>
> **与列表接口的差异**：详情接口中关联字段（`suoshulanmu`、`create_user`、`status_approve` 等）的值由扁平字符串变为 `{label, value}` 对象，便于前端展示标签；`null` 值字段保持 `null`。

**响应示例（已精简）**

```json
{
  "Code": 0,
  "Response": {
    "tables": [
      {
        "display_name": "商品管理",
        "table_name": "t_shangpinguanli",
        "primary_key": "id",
        "foreign_key": null,
        "parent_table": null,
        "data": {
          "id": 320, "sid": "", "skubianhao": "YD0036",
          "chanpinmingcheng": "男女同款运动背包",
          "pinpai": "", "jiliangdanwei": "",
          "suoshulanmu": { "label": "运动类", "value": "25" },
          "xiaoshoujiahanshui": "59.90",
          "guigechicun": "均码", "chanpinxinghao": "",
          "yanse": "黑色、灰色、蓝色、红色", "caizhi": "",
          "banxing": "双肩款", "shichuanwendu": "10-30℃ 四季通用",
          "fahuoshixiao": "72小时内发货",
          "chanpinjieshao": "防水耐磨聚酯纤维面料，多隔层设计，大容量收纳，透气减压肩带，户外登山、徒步、健身、日常通勤均可",
          "create_user": { "label": "孙晨", "value": 1 },
          "create_group": null,
          "update_user": null,
          "create_time": "2026-06-17 13:08:34",
          "update_time": null,
          "archive_time": null,
          "status": 1,
          "status_approve": { "label": "未发起", "value": 0 }
        },
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "skubianhao", "display_name": "SKU编号" },
              { "attr_name": "chanpinmingcheng", "display_name": "产品名称" },
              { "attr_name": "pinpai", "display_name": "品牌" },
              { "attr_name": "jiliangdanwei", "display_name": "计量单位" },
              { "attr_name": "suoshulanmu", "display_name": "所属栏目" },
              { "attr_name": "xiaoshoujiahanshui", "display_name": "销售价(含税)" },
              { "attr_name": "guigechicun", "display_name": "规格尺寸" },
              { "attr_name": "chanpinxinghao", "display_name": "产品型号" },
              { "attr_name": "yanse", "display_name": "颜色" },
              { "attr_name": "caizhi", "display_name": "材质" },
              { "attr_name": "banxing", "display_name": "版型" },
              { "attr_name": "shichuanwendu", "display_name": "适穿温度" },
              { "attr_name": "fahuoshixiao", "display_name": "发货时效" },
              { "attr_name": "chanpinjieshao", "display_name": "产品介绍" }
            ]
          },
          {
            "display_name": "系统信息",
            "attrs": [
              { "attr_name": "id", "display_name": "系统编号" },
              { "attr_name": "sid", "display_name": "业务编号" },
              { "attr_name": "create_user", "display_name": "创建人" },
              { "attr_name": "create_group", "display_name": "创建组" },
              { "attr_name": "update_user", "display_name": "修改人" },
              { "attr_name": "create_time", "display_name": "创建时间" },
              { "attr_name": "update_time", "display_name": "更新时间" },
              { "attr_name": "archive_time", "display_name": "归档时间" },
              { "attr_name": "status", "display_name": "系统状态" },
              { "attr_name": "status_approve", "display_name": "审批状态" }
            ]
          }
        ]
      }
    ]
  }
}
```

---

## 9. 订单创建接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_data_update
```

### 请求方式

POST，Body 为 `application/json`

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehudingdan"` |
| did | null | 是 | 创建订单时固定为 `null` |
| tables | array | 是 | 包含主表和子表的数据，见下文 |
| temp | bool | 是 | 是否临时保存，固定为 `false` |

**tables 数组结构**

| 元素 | table | method | 说明 |
|------|-------|--------|------|
| tables[0] | `t_kehudingdan` | `update` | 订单主表，单条数据 |
| tables[1] | `t_kehudingdan_2` | `insert` | 优惠明细子表（可选，无优惠时省略） |
| tables[2] | `t_kehudingdan_3` | `insert` | 订单产品子表 |

### 主表 t_kehudingdan 字段要求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| kehu | string | 是 | **客户ID，必须等于委托登录返回的 `record_id`**（后端强制校验，不一致报错） |
| shouhuoren | string | 是 | 收货人，默认从客户详情取出，可改 |
| shouji | string | 是 | 手机，默认从客户详情取出，可改 |
| shengshiqu | string | 是 | 省市区，默认从客户详情取出，可改 |
| xiangxidizhi | string | 是 | 详细地址，默认从客户详情取出，可改 |
| xiadanshijian | string | 是 | 下单时间，应为当前时间（`YYYY-MM-DD HH:mm:ss`） |
| dingdanzhuangtai | string | 是 | 订单状态，必须为 `"待支付"` |
| fahuozhuangtai | string | 是 | 发货状态，必须为 `"未发货"` |
| shangpinjine | string | 是 | 商品金额，等于【订单产品】子表 `zongji` 字段的合计值 |
| youhuijine | string | 是 | 优惠金额，等于【优惠明细】子表 `youhuiquanjine` 字段的合计值；无优惠时为 `"0.00"` |
| yunfei | string | 是 | 运费 |
| dingdanjine | string | 是 | 订单金额 = 商品金额 - 优惠金额 + 运费 |
| update_time | string | 是 | 固定为空字符串 `""` |
| id | string | 是 | 固定为空字符串 `""`，表示新建 |

> **`kehu` 字段约束**：委托登录下，后端校验 `kehu` 必须等于当前 `client_token` 对应的 `record_id`。AI 智能体应从委托登录响应（第 2.1 节）取 `record_id`，不要从用户输入取。传错值会返回 `Code: -1, Error: "委托登录写入失败：表 t_kehudingdan 字段 kehu 应为 X，实际传入 Y"`。

### 优惠明细子表 t_kehudingdan_2

可选子表，无优惠时省略。有优惠时：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| youhuiquanhuodong | string | 是 | 优惠券活动名称 |
| youhuiquanjine | string | 是 | 优惠券金额，必须 > 0，且 < 商品金额 |

### 订单产品子表 t_kehudingdan_3

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| chanpinbianhao | string/int | 是 | 产品编号，从产品列表/详情接口获取 |
| chanpinmingcheng | string | 是 | 产品名称，从产品列表/详情接口获取 |
| pinpai | string | 否 | 品牌，从产品详情接口获取 |
| guigeshuxing | string | 否 | 规格属性，从产品详情接口获取，把规格尺寸&颜色&材质拼接起来 |
| jiliangdanwei | string | 否 | 计量单位，从产品详情接口获取 |
| xiadanshuliang | string/int | 是 | 下单数量，必须 > 0 |
| danjia | string | 是 | 单价，必须 > 0 |
| zongji | number/string | 是 | 总价 = 下单数量 * 单价 |
| youhuijine | string | 否 | 优惠金额，可为 `0`，不能 < 0，不能 > 总价 |

**创建时必须留空的字段**：`yifahuoshuliang`（已发货数量）、`wuliugongsi`（物流公司）、`wuliudanhao`（物流单号）、`tuidanshuliang`（退单数量）、`tuihuoyuanyin`（退货原因）、`tuihuozhuangtai`（退货状态）

### 请求示例

```json
{
  "module": "kehudingdan",
  "did": null,
  "tables": [
    {
      "data": [
        {
          "kehu": "2",
          "shouhuoren": "覃女士",
          "shouji": "13916323348",
          "xiadanshijian": "2026-07-03 17:17:24",
          "shengshiqu": "上海市徐汇区",
          "xiangxidizhi": "南宁路900号102室",
          "dingdanzhuangtai": "待支付",
          "fahuozhuangtai": "未发货",
          "shangpinjine": "29.80",
          "youhuijine": "0.00",
          "yunfei": "0.00",
          "dingdanjine": "29.80",
          "update_time": "",
          "id": ""
        }
      ],
      "method": "update",
      "table": "t_kehudingdan"
    },
    {
      "table": "t_kehudingdan_3",
      "method": "insert",
      "data": [
        {
          "zongji": 29.8,
          "chanpinbianhao": 318,
          "chanpinmingcheng": "男女同款运动吸汗发带",
          "danjia": "14.90",
          "jiliangdanwei": "",
          "pinpai": "",
          "xiadanshuliang": "2"
        }
      ]
    }
  ],
  "temp": false
}
```

### 返回响应

| 字段 | 类型 | 说明 |
|------|------|------|
| Code | int | `0` 表示创建成功 |
| Response | int | 新建订单的 `id` |

**响应示例**

```json
{
  "Code": 0,
  "Response": 5
}
```

---

## 10. 订单修改接口

### 接口地址

```
POST https://erp11022.aidingyi.cn/api/v1/erp.module/module_data_update
```

### 请求方式

POST，Body 为 `application/json`

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehudingdan"` |
| did | int | 是 | 订单 id（修改时必填） |
| tables | array | 是 | 仅包含需要修改的字段，见下文 |
| temp | bool | 是 | 是否临时保存，固定为 `false` |

> **身份过滤**：委托登录下，后端在 update 的 where 条件自动追加 `kehu = {record_id}`，AI 智能体无法修改他人订单。若 `did` 不属于当前委托人，接口表面返回成功但实际影响 0 行（数据未变），不会报错。

### 不同场景允许修改的字段

订单修改按当前 `dingdanzhuangtai` 划分允许操作的场景。**严禁跨场景修改**。

#### 场景一：订单状态为「待支付」

允许：取消订单，即将 `dingdanzhuangtai` 改为 `"已取消"`

```json
{
  "module": "kehudingdan",
  "did": 1,
  "tables": [
    {
      "data": [
        {
          "dingdanzhuangtai": "已取消",
          "update_time": "2026-06-02 17:05:16",
          "id": 1
        }
      ],
      "method": "update",
      "table": "t_kehudingdan"
    }
  ],
  "temp": false
}
```

#### 场景二：订单状态为「待支付」或「已支付」

允许：修改收货人信息，包括 `shouhuoren`、`shouji`、`shengshiqu`、`xiangxidizhi`

```json
{
  "module": "kehudingdan",
  "did": 1,
  "tables": [
    {
      "data": [
        {
          "shouhuoren": "覃先生",
          "shouji": "13916323349",
          "shengshiqu": "上海市浦东新区",
          "xiangxidizhi": "张杨路500号",
          "update_time": "2026-06-02 17:05:16",
          "id": 1
        }
      ],
      "method": "update",
      "table": "t_kehudingdan"
    }
  ],
  "temp": false
}
```

#### 场景三：订单状态为「已支付」「已发货」或「已完成」

允许：客户发起退货，修改【订单产品】子表 `t_kehudingdan_3` 的 `tuidanshuliang`、`tuihuoyuanyin`、`tuihuozhuangtai`

**约束**：
- `tuidanshuliang`（退单数量）必须等于该行的 `yifahuoshuliang`（已发货数量），不允许部分退货
- `tuihuozhuangtai`（退货状态）必须为 `"待商家处理"`

```json
{
  "module": "kehudingdan",
  "did": 1,
  "tables": [
    {
      "table": "t_kehudingdan_3",
      "method": "update",
      "data": [
        {
          "tuidanshuliang": 4,
          "tuihuoyuanyin": "商品质量问题",
          "tuihuozhuangtai": "待商家处理",
          "id": 1
        }
      ]
    }
  ],
  "temp": false
}
```

### 返回响应

| 字段 | 类型 | 说明 |
|------|------|------|
| Code | int | `0` 表示修改成功 |
| Response | array | 修改操作返回空数组 `[]` |

**响应示例**

```json
{
  "Code": 0,
  "Response": []
}
```

### 注意事项

1. **修改时必须传递 `id` 字段**：主表传订单 id，子表传对应记录的 id
2. **主表修改时必须传递 `update_time`**：需带上当前时间（`YYYY-MM-DD HH:mm:ss`）
3. **只传需要修改的字段**：避免传递未变更的字段，防止覆盖意外数据
4. **状态约束严格**：不允许跨场景修改（如「待支付」状态下不能发起退货）

---

## 11. 权限与写入约束

委托登录下，后端强制执行以下约束，AI 智能体无需也无法绕过，但应了解以避免调用失败。

### 11.1 操作权限矩阵

| 模块 | 列表/详情 | 新增 | 修改 | 删除 |
|------|----------|------|------|------|
| 客户信息（kehuxinxi） | ✓ | ✗ | ✓ | ✗ |
| 客户订单（kehudingdan） | ✓ | ✓ | ✓ | ✗ |
| 商品管理（shangpinguanli） | ✓ | ✗ | ✗ | ✗ |

调用无权限的写入接口会返回 `Code: -1, Error: "委托登录无 insert/update 权限：模块 xxx"`。

### 11.2 数据权限

- 客户信息、订单：仅返回/操作委托人自己的数据（后端自动过滤，无需在 filters 中指定）
- 商品：返回所有商品

### 11.3 删除约定

**委托登录禁止物理删除业务数据**。所有"删除"语义统一走修改状态字段：

| 场景 | 实现方式 |
|------|---------|
| 取消订单 | 修改 `dingdanzhuangtai` 为 `"已取消"`（见第 10 节场景一） |
| 商品下架 | 委托人无操作权限，需联系后台管理员 |
| 客户停用 | 委托人无操作权限，需联系后台管理员 |

### 11.4 身份字段强制一致

订单创建时，主表 `t_kehudingdan.kehu` 必须等于委托登录返回的 `record_id`。后端校验，不一致直接报错。AI 智能体应从 `client_token` 对应的委托登录响应中取 `record_id`，不要从用户输入取。

---

## 变更记录

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-06-03 | 初始版本，涵盖客户信息列表/详情、客户订单列表/详情 |
| v1.1 | 2026-07-03 | 订单模块字段补全：修正订单列表 `fahuozhuangtai` 的 options；订单产品子表补充 `wuliugongsi`/`wuliudanhao`/`tuihuoyuanyin`/`tuihuozhuangtai` 字段定义及示例。 |
| v1.2 | 2026-07-03 | 新增第 8 节「订单创建接口」、第 9 节「订单修改接口」。 |
| v1.3 | 2026-07-06 | 接口输出精简：列表接口仅返回 `data`/`data2`/`fields`/`total`/`total_sum`；详情接口顶层仅返回 `tables`。 |
| v1.4 | 2026-07-07 | 字段元数据进一步精简：`attrs` 仅含 `attr_name`/`display_name`/`options`；`sections` 仅含 `display_name`/`attrs`；`options` 内部仅含 `label`/`value`。 |
| v2.0 | 2026-07-10 | 改为委托登录鉴权：双 Token（`agent_token` + `client_token`）；新增第 2 节委托登录接口（login/info/logout）；订单创建补充 `kehu` 必须等于 `record_id` 约束；新增第 11 节权限与写入约束（操作权限矩阵、删除约定、身份字段强制一致）。 |
