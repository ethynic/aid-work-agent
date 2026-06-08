# QB3.1 API 文档

QB3.1 是一套基于自研低代码平台的 ERP 系统，支持多租户。本文档仅介绍数据读取类接口，不涉及增删改接口。后端数据库为 MySQL 5.7。

---

## 1. 通用调用规范

### 请求地址
请求地址的 BASE_URL 为： ${BASE_URL}

### 请求方式

POST，Body 为 `application/json`

### 请求参数

**Header**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| Api-Authorize-Token | string | 是 | 从 `.env` 环境变量 `${OPEN_TOKEN}` 注入 |

### 返回响应

- HTTP 状态码：`200`，Content-Type：`application/json`
- 顶层结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| Code | int | `0` 表示无异常，非 `0` 表示异常 |
| Error | string | 异常信息 |
| Debug | string | 调试信息（异常时详细输出） |
| Response | object | 业务数据主体 |
| Slow | array | 慢查询日志 |
| Trace | string | 请求追踪信息 |

### 列表接口通用约定

以下规则对 **所有 `module_listing_view` 接口**（客户信息列表、客户订单列表等）通用：

**分页参数**（Body）

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| page | int | 否 | 页码，默认 1 |
| limit | int | 否 | 每页条数，默认 20 |

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
| `input` | 字符串 | LIKE 模糊查询 |
| `datetime` | 数组 `[开始, 结束]` | 时间范围查询 |
| `integer` / `number` / `currency` | 数组 `[最小值, 最大值]` | 数值范围查询 |

多个 filter 之间为 **AND** 关系。不指定 `component` ，默认为 `component`: `input`。

**返回响应结构**

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['data'] | array | 匹配的记录列表 |
| Response['fields'] | array | 字段元数据，可用于动态渲染前端 |
| Response['page'] | int | 当前页码 |
| Response['total'] | int | 总记录数 |
| Response['total_sum'] | object | 汇总数据 |
| Response['base'] | object | 模块基本信息 |
| Response['permission'] | array | 权限列表 |

> **关于字段元数据**：`fields` 数组中每个元素包含该字段的完整定义（`attr_name`、`display_name`、`attr_type`、`component`、`hidden`、`mandatory`、`unique`、`options` 等），各接口的示例仅列核心字段，实际返回以接口为准。

---

## 2. 客户信息列表接口

### 接口地址

```
${BASE_URL}/api/v1/erp.module/module_listing_view
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehuxinxi"` |
| filters | array | 是 | 过滤条件，参见第 1 节 filters 约定 |
| page, limit | - | - | 参见第 1 节分页参数 |

**请求示例**

```json
{
  "module": "kehuxinxi",
  "filters": [
    { "attr": "kehushouji", "value": "13916323347" }
  ],
  "page": 1,
  "limit": 20
}
```

### 返回响应

**响应示例（精简）**

```json
{
  "Code": 0,
  "Response": {
    "base": { "display_name": "客户信息", "path": "kehuxinxi" },
    "data": [
      {
        "id": 1, "sid": "KHXX2026-000001",
        "kehumingcheng": "孙小姐", "kehushouji": "13916323347",
        "kehubianhao": null, "beizhu": null,
        "create_user": "孙晨", "create_time": "2026-06-02 16:16:42",
        "status": 1, "status_approve": 0
      }
    ],
    "fields": [
      { "attr_name": "sid", "display_name": "业务编号", "attr_type": "varchar", "component": "input", "hidden": "0" },
      { "attr_name": "kehubianhao", "display_name": "客户编号", "attr_type": "int", "component": "number", "hidden": "1" },
      { "attr_name": "kehumingcheng", "display_name": "客户名称", "attr_type": "varchar", "component": "input", "hidden": "0" },
      { "attr_name": "kehushouji", "display_name": "客户手机", "attr_type": "varchar", "component": "input", "hidden": "0" },
      { "attr_name": "beizhu", "display_name": "备注", "attr_type": "text", "component": "textarea", "hidden": "0" },
      { "attr_name": "create_time", "display_name": "创建时间", "attr_type": "datetime", "component": "datetime" }
    ],
    "page": 1, "total": 1, "total_sum": {}
  }
}
```

---

## 3. 客户信息详情接口

### 接口地址

```
${BASE_URL}/api/v1/erp.module/module_prepare_edit
```

### 请求参数

**Body**

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 模块名称，固定为 `"kehuxinxi"` |
| did | int | 是 | 客户记录 ID |

**请求示例**

```json
{
  "module": "kehuxinxi",
  "did": 1
}
```

### 返回响应

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['tables'][0]['data'] | object | 客户主表信息（单条，字典） |
| Response['tables'][1]['data'] | array | 客户收货地址（多条，数组） |
| Response['tables'][i]['display_name'] | string | 子表名称 |
| Response['tables'][i]['sections'] | array | 字段分区定义 |
| Response['module'] | object | 模块元信息 |
| Response['permission'] | array | 当前用户权限列表 |
| Response['extra'] | object | 关联数据（如相关订单） |

**响应示例（已精简）**

```json
{
  "Code": 0,
  "Debug": "",
  "Error": "",
  "Response": {
    "module": {
      "display_name": "客户信息",
      "id": 19,
      "module_name": "kehuxinxi",
      "style": "module",
      "subsheet_style": "customize"
    },
    "permission": [ "insert", "update", "delete", "list_export", "list_import" ],
    "tables": [
      {
        "display_name": "客户信息",
        "table_name": "t_kehuxinxi",
        "primary_key": "id",
        "data": {
          "id": 1,
          "sid": "KHXX2026-000001",
          "kehumingcheng": "孙小姐",
          "kehushouji": "13916323347",
          "kehubianhao": null,
          "beizhu": null,
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
            "table_name": "t_kehuxinxi",
            "position": 1,
            "attrs": [
              { "attr_name": "kehubianhao", "display_name": "客户编号", "component": "number", "hidden": "1" },
              { "attr_name": "kehumingcheng", "display_name": "客户名称", "component": "input", "hidden": "0" },
              { "attr_name": "kehushouji", "display_name": "客户手机", "component": "input", "hidden": "0" },
              { "attr_name": "beizhu", "display_name": "备注", "component": "textarea", "hidden": "0" }
            ]
          },
          {
            "display_name": "系统信息",
            "table_name": "t_kehuxinxi",
            "position": 2,
            "attrs": [
              { "attr_name": "sid", "display_name": "业务编号", "build_in": 1 },
              { "attr_name": "create_user", "display_name": "创建人", "build_in": 1 },
              { "attr_name": "create_time", "display_name": "创建时间", "attr_type": "datetime", "build_in": 1 },
              { "attr_name": "update_time", "display_name": "更新时间", "attr_type": "datetime", "build_in": 1 },
              { "attr_name": "status", "display_name": "系统状态", "hidden": "1", "build_in": 1 },
              { "attr_name": "status_approve", "display_name": "审批状态", "hidden": "1", "build_in": 1 }
            ]
          }
        ]
      },
      {
        "display_name": "收货地址",
        "table_name": "t_kehuxinxi_2",
        "parent_table": "t_kehuxinxi",
        "foreign_key": "fid",
        "primary_key": "id",
        "data": [
          {
            "id": 1,
            "fid": 1,
            "shouhuoren": "孙晨",
            "shouji": "13916323347",
            "shengshiqu": "上海徐汇区",
            "xiangxidizhi": "南宁路1000号",
            "shifoumorendizhi": "是"
          },
          {
            "id": 2,
            "fid": 1,
            "shouhuoren": "孙晨",
            "shouji": "13916323347",
            "shengshiqu": "上海嘉定区",
            "xiangxidizhi": "芳林路958号",
            "shifoumorendizhi": "否"
          }
        ],
        "sections": [
          {
            "display_name": "基本信息",
            "table_name": "t_kehuxinxi_2",
            "attrs": [
              { "attr_name": "shouhuoren", "display_name": "收货人", "component": "input" },
              { "attr_name": "shouji", "display_name": "手机", "component": "input" },
              { "attr_name": "shengshiqu", "display_name": "省市区", "component": "input" },
              { "attr_name": "xiangxidizhi", "display_name": "详细地址", "component": "input" },
              {
                "attr_name": "shifoumorendizhi",
                "display_name": "是否默认地址",
                "component": "select",
                "options": [
                  { "label": "是", "value": "是" },
                  { "label": "否", "value": "否" }
                ]
              }
            ]
          }
        ]
      }
    ],
    "extra": {
      "relevance": [
        {
          "module": "kehudingdan",
          "label": "客户订单",
          "total": 1,
          "filters": [
            { "attr": "kehu", "display_value": "孙小姐", "value": [1] }
          ]
        }
      ]
    }
  }
}
```

## 4. 客户订单列表接口

### 接口地址

```
${BASE_URL}/api/v1/erp.module/module_listing_view
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 固定为 `"kehudingdan"` |
| filters | array | 是 | 过滤条件，参见第 1 节 filters 约定 |
| page, limit | - | - | 参见第 1 节分页参数 |

**请求示例**

```json
{
  "module": "kehudingdan",
  "filters": [
    { "attr": "shouji", "display_name": "手机", "value": "13916323347", "component": "input" },
    { "attr": "xiadanshijian", "display_name": "下单时间", "value": ["2026-06-02", "2026-06-04"], "component": "datetime" }
  ],
  "page": 1,
  "limit": 20
}
```

**关于 datetime 截止时间的注意事项**

上例中，value[1]: "2026-06-04" 其实表示的是 "2026-06-04 00:00:00" 而非 "2026-06-04 23:59:59" ，这一点和很多用户口头表述不同。即，用户说“我的订单下单时间应该是在2026年6月2日到2026年6月4日之间”，那么应该翻译为 { "attr": "xiadanshijian", "display_name": "下单时间", "value": ["2026-06-02", "2026-06-05"], "component": "datetime" } 。

### 返回响应

通用返回结构见第 1 节"列表接口通用约定"。以下为业务字段示例（实际 `fields` 以接口返回为准）：

**data 记录示例**

```json
{
  "Code": 0,
  "Debug": "",
  "Error": "",
  "Response": {
    "approve": null,
    "base": {
      "create_time": "2026-06-02 16:20:50",
      "display_name": "客户订单",
      "enable_mobile": 1,
      "id": 25,
      "path": "kehudingdan",
      "position": 7,
      "status": 1,
      "temporary_save": 1,
      "type": "module",
      "update_time": "2026-06-02 16:20:50"
    },
    "category": null,
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
    "debug": [],
    "fields": [
      // --- 自定义字段 ---
      { "attr_name": "kehu", "display_name": "客户", "component": "input", "attr_type": "varchar", "dataflow": { "style": "sheetlink", "front_module": "kehuxinxi", "front_display": "kehumingcheng" } },
      { "attr_name": "shouhuoren", "display_name": "收货人", "component": "input", "attr_type": "varchar" },
      { "attr_name": "shouji", "display_name": "手机", "component": "input", "attr_type": "varchar" },
      { "attr_name": "xiadanshijian", "display_name": "下单时间", "component": "datetime", "attr_type": "datetime" },
      { "attr_name": "shengshiqu", "display_name": "省市区", "component": "input", "attr_type": "varchar" },
      { "attr_name": "xiangxidizhi", "display_name": "详细地址", "component": "input", "attr_type": "varchar" },
      { "attr_name": "dingdanzhuangtai", "display_name": "订单状态", "component": "select", "attr_type": "varchar", "options": [{"label": "待支付", "value": "待支付"}, {"label": "已支付", "value": "已支付"}, {"label": "已发货", "value": "已发货"}, {"label": "已完成", "value": "已完成"}, {"label": "已取消", "value": "已取消"}] },
      { "attr_name": "fahuozhuangtai", "display_name": "发货状态", "component": "select", "attr_type": "varchar", "options": [{"label": "未发货", "value": "未发货"}, {"label": "已发货", "value": "已发货"}, {"label": "运输中", "value": "运输中"}, {"label": "已到达", "value": "已到达"}] },
      { "attr_name": "shangpinjine", "display_name": "商品金额", "component": "currency", "attr_type": "decimal", "source_type": "compute", "compute": "sum( t_kehudingdan_3.zongji )" },
      { "attr_name": "youhuijine", "display_name": "优惠金额", "component": "currency", "attr_type": "decimal", "source_type": "compute", "compute": "sum( t_kehudingdan_2.youhuiquanjine )" },
      { "attr_name": "yunfei", "display_name": "运费", "component": "currency", "attr_type": "decimal" },
      { "attr_name": "dingdanjine", "display_name": "订单金额", "component": "currency", "attr_type": "decimal", "source_type": "compute", "compute": "t_kehudingdan.shangpinjine - t_kehudingdan.youhuijine + t_kehudingdan.yunfei" },
      { "attr_name": "zhifufangshi", "display_name": "支付方式", "component": "select", "attr_type": "varchar", "options": [{"label": "微信", "value": "微信"}, {"label": "支付宝", "value": "支付宝"}, {"label": "银行卡", "value": "银行卡"}] },
      { "attr_name": "zhifujine", "display_name": "支付金额", "component": "currency", "attr_type": "decimal" },
      // --- 系统内置字段 ---
      { "attr_name": "sid", "display_name": "业务编号", "component": "input", "attr_type": "varchar", "build_in": 1 },
      { "attr_name": "id", "display_name": "系统编号", "component": "input", "attr_type": "int", "build_in": 1, "list_hidden": "0" },
      { "attr_name": "create_user", "display_name": "创建人", "component": "input", "attr_type": "int", "build_in": 1 },
      { "attr_name": "create_group", "display_name": "创建组", "component": "input", "attr_type": "int", "build_in": 1, "list_hidden": "1" },
      { "attr_name": "update_user", "display_name": "修改人", "component": "input", "attr_type": "int", "build_in": 1, "list_hidden": "1" },
      { "attr_name": "create_time", "display_name": "创建时间", "component": "datetime", "attr_type": "datetime", "build_in": 1 },
      { "attr_name": "update_time", "display_name": "更新时间", "component": "datetime", "attr_type": "datetime", "build_in": 1, "list_hidden": "1" },
      { "attr_name": "archive_time", "display_name": "归档时间", "component": "datetime", "attr_type": "datetime", "build_in": 1, "hidden": "1" },
      { "attr_name": "status", "display_name": "系统状态", "component": "input", "attr_type": "int", "build_in": 1, "hidden": "1" },
      { "attr_name": "status_approve", "display_name": "审批状态", "component": "input", "attr_type": "int", "build_in": 1, "hidden": "1" }
    ],
    "page": 1,
    "permission": [
      "insert",
      "update",
      "delete",
      "list_export",
      "list_import"
    ],
    "sql": "SELECT t_kehudingdan.sid, t_kehudingdan.kehu, t_kehudingdan.shouhuoren, t_kehudingdan.shouji, t_kehudingdan.xiadanshijian, t_kehudingdan.shengshiqu, t_kehudingdan.xiangxidizhi, t_kehudingdan.dingdanzhuangtai, t_kehudingdan.fahuozhuangtai, t_kehudingdan.shangpinjine, t_kehudingdan.youhuijine, t_kehudingdan.yunfei, t_kehudingdan.dingdanjine, t_kehudingdan.zhifufangshi, t_kehudingdan.zhifujine, t_kehudingdan.id, t_kehudingdan.create_user, t_kehudingdan.create_group, t_kehudingdan.update_user, t_kehudingdan.create_time, t_kehudingdan.update_time, t_kehudingdan.archive_time, t_kehudingdan.status, t_kehudingdan.status_approve, (select kehumingcheng from t_kehuxinxi ft where ft.id=t_kehudingdan.kehu) AS `DISPLAY_kehu`, (SELECT `DISPLAY_sys_user`.name  FROM sys_user AS `DISPLAY_sys_user`  WHERE `DISPLAY_sys_user`.id = t_kehudingdan.create_user   LIMIT 1) AS `DISPLAY_create_user`, (SELECT `DISPLAY_sys_user`.name  FROM sys_user AS `DISPLAY_sys_user`  WHERE `DISPLAY_sys_user`.id = t_kehudingdan.update_user   LIMIT 1) AS `DISPLAY_update_user`, (SELECT `DISPLAY_sys_group`.group_name  FROM sys_group AS `DISPLAY_sys_group`  WHERE `DISPLAY_sys_group`.id = t_kehudingdan.create_group   LIMIT 1) AS `DISPLAY_create_group`  FROM t_kehudingdan ORDER BY id desc   LIMIT 0, 20",
    "title": null,
    "total": 1,
    "total_sum": {
      "dingdanjine": "168.00",
      "shangpinjine": "208.00",
      "youhuijine": "40.00",
      "yunfei": null,
      "zhifujine": null
    }
  },
  "Slow": [],
  "Trace": ""
}
```

## 5. 客户订单详情接口

### 接口地址

```
${BASE_URL}/api/v1/erp.module/module_prepare_edit
```

### 请求参数

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| module | string | 是 | 模块名称，固定为 `"kehudingdan"` |
| did | integer | 是 | 客户订单 id |

**请求示例**

```json
{
  "module": "kehudingdan",
  "did": 1
}
```

### 返回响应

| 路径 | 类型 | 说明 |
|------|------|------|
| Response['tables'][0]['data'] | object | 客户订单主表信息（单条，字典） |
| Response['tables'][1]['data'] | array | 优惠明细（多条，数组） |
| Response['tables'][2]['data'] | array | 订单产品明细（多条，数组） |
| Response['tables'][i]['display_name'] | string | 子表名称 |
| Response['tables'][i]['sections'] | array | 字段分区定义 |
| Response['module'] | object | 模块元信息 |
| Response['permission'] | array | 当前用户权限列表 |
| Response['extra'] | object | 关联数据（如相关订单） |

> 主表字段定义与 **第 4 节"客户订单列表接口"** 的 `fields` 一致，此处不再重复列出完整元数据。以下仅展示子表字段和示例数据。

**响应示例（已精简）**

```json
{
  "Code": 0,
  "Debug": "",
  "Error": "",
  "Response": {
    "approve": null,
    "debug": [
      "SELECT t_kehudingdan.id, t_kehudingdan.kehu, ... FROM t_kehudingdan WHERE t_kehudingdan.id = 1 ...",
      "SELECT t_kehudingdan_2.id, t_kehudingdan_2.youhuiquanhuodong, ... FROM t_kehudingdan_2 WHERE t_kehudingdan_2.fid = 1 ...",
      "SELECT t_kehudingdan_3.id, t_kehudingdan_3.chanpinbianhao, ... FROM t_kehudingdan_3 WHERE t_kehudingdan_3.fid = 1 ..."
    ],
    "extra": {
      "relevance": []
    },
    "module": {
      "display_name": "客户订单",
      "module_name": "kehudingdan",
      "style": "module",
      "subsheet_style": "customize",
      "temporary_save": 1
    },
    "permission": [
      "insert", "update", "delete", "list_export", "list_import"
    ],
    "reports": [],
    "tables": [
      {
        "display_name": "客户订单",
        "table_name": "t_kehudingdan",
        "primary_key": "id",
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
            "position": 1,
            "attrs": [
              // 字段定义同第 4 节的 fields，此处省略完整元数据
              { "attr_name": "kehu", "display_name": "客户", "component": "input" },
              { "attr_name": "shouhuoren", "display_name": "收货人", "component": "input" },
              { "attr_name": "shouji", "display_name": "手机", "component": "input" },
              { "attr_name": "xiadanshijian", "display_name": "下单时间", "component": "datetime" },
              { "attr_name": "shengshiqu", "display_name": "省市区", "component": "input" },
              { "attr_name": "xiangxidizhi", "display_name": "详细地址", "component": "input" },
              { "attr_name": "dingdanzhuangtai", "display_name": "订单状态", "component": "select" },
              { "attr_name": "fahuozhuangtai", "display_name": "发货状态", "component": "select" },
              { "attr_name": "shangpinjine", "display_name": "商品金额", "component": "currency" },
              { "attr_name": "youhuijine", "display_name": "优惠金额", "component": "currency" },
              { "attr_name": "yunfei", "display_name": "运费", "component": "currency" },
              { "attr_name": "dingdanjine", "display_name": "订单金额", "component": "currency" },
              { "attr_name": "zhifufangshi", "display_name": "支付方式", "component": "select" },
              { "attr_name": "zhifujine", "display_name": "支付金额", "component": "currency" }
            ]
          },
          {
            "display_name": "系统信息",
            "position": 2,
            "attrs": [
              { "attr_name": "id", "display_name": "系统编号", "build_in": 1 },
              { "attr_name": "sid", "display_name": "业务编号", "build_in": 1 },
              { "attr_name": "create_user", "display_name": "创建人", "build_in": 1 },
              { "attr_name": "create_group", "display_name": "创建组", "build_in": 1 },
              { "attr_name": "update_user", "display_name": "修改人", "build_in": 1 },
              { "attr_name": "create_time", "display_name": "创建时间", "attr_type": "datetime", "build_in": 1 },
              { "attr_name": "update_time", "display_name": "更新时间", "attr_type": "datetime", "build_in": 1 },
              { "attr_name": "archive_time", "display_name": "归档时间", "attr_type": "datetime", "build_in": 1, "hidden": "1" },
              { "attr_name": "status", "display_name": "系统状态", "build_in": 1, "hidden": "1" },
              { "attr_name": "status_approve", "display_name": "审批状态", "build_in": 1, "hidden": "1" }
            ]
          }
        ],
        "status": 1
      },
      {
        "display_name": "优惠明细",
        "table_name": "t_kehudingdan_2",
        "parent_table": "t_kehudingdan",
        "foreign_key": "fid",
        "primary_key": "id",
        "data": [
          { "fid": 1, "id": 1, "youhuiquanhuodong": "消费券200-25", "youhuiquanjine": "25.00" },
          { "fid": 1, "id": 2, "youhuiquanhuodong": "平台加补券150-15", "youhuiquanjine": "15.00" }
        ],
        "sections": [
          {
            "display_name": "基本信息",
            "attrs": [
              { "attr_name": "youhuiquanhuodong", "display_name": "优惠券活动", "component": "input" },
              { "attr_name": "youhuiquanjine", "display_name": "优惠券金额", "component": "currency" },
              { "attr_name": "serial", "display_name": "序号", "component": "input", "build_in": 1 }
            ]
          }
        ]
      },
      {
        "display_name": "订单产品",
        "table_name": "t_kehudingdan_3",
        "parent_table": "t_kehudingdan",
        "foreign_key": "fid",
        "primary_key": "id",
        "data": [
          {
            "chanpinbianhao": "248",
            "chanpinmingcheng": "得宝（TEMPO）卷筒卫生纸 T4680 四层 160g/卷 10卷/提",
            "danjia": "52.00",
            "fid": 1, "id": 1,
            "jiliangdanwei": "支",
            "pinpai": "得宝（TEMPO）",
            "tuidanshuliang": null,
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
              { "attr_name": "chanpinbianhao", "display_name": "产品编号", "component": "input" },
              { "attr_name": "chanpinmingcheng", "display_name": "产品名称", "component": "input" },
              { "attr_name": "pinpai", "display_name": "品牌", "component": "input" },
              { "attr_name": "jiliangdanwei", "display_name": "计量单位", "component": "input" },
              { "attr_name": "xiadanshuliang", "display_name": "下单数量", "component": "number" },
              { "attr_name": "tuidanshuliang", "display_name": "退单数量", "component": "number" },
              { "attr_name": "yifahuoshuliang", "display_name": "已发货数量", "component": "number" },
              { "attr_name": "danjia", "display_name": "单价", "component": "currency" },
              { "attr_name": "zongji", "display_name": "总计", "component": "currency", "source_type": "compute" },
              { "attr_name": "youhuijine", "display_name": "优惠金额", "component": "currency" },
              { "attr_name": "serial", "display_name": "序号", "build_in": 1 }
            ]
          }
        ]
      }
    ]
  },
  "Slow": [],
  "Trace": ""
}
```

## 变更记录

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-06-03 | 初始版本，涵盖客户信息列表/详情、客户订单列表/详情 |
