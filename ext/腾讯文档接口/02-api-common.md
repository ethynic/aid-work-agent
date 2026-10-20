# 02 · 通用规范：请求 / 鉴权 / ID / 频控 / 错误码

> 快照来源：https://docs.qq.com/open/document/app/openapi/v2/ 、/openapi/v3/ 、
> /openapi/v2/file/common/req_headers.html 、/openapi/v2/file/common/id.html 、
> /openapi/v2/frequency_control/ 、/openapi/v2/CODE.html 、/openapi/v3/common/code.html
> 抓取日期：2026-10-20

---

## 1. 请求规范（V2 / V3 通用）

| 项 | 规定 |
| --- | --- |
| 网络协议 | `HTTPS` |
| 编码 | `UTF8` |
| 请求方法 | `GET` / `POST` / `PUT` / `PATCH` / `DELETE` |
| 请求参数类型 | Path Parameters、Query Parameters、Body Parameters |
| 响应格式 | `JSON` |
| 服务域名 | `https://docs.qq.com` |

### 1.1 鉴权请求头（三元组，必带）

```
Access-Token: <用户 access_token>
Client-Id:    <应用 client_id>
Open-Id:      <用户 open_id>
```

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `Access-Token` | string | 是 | 访问令牌，标识用户 + 接口鉴权 |
| `Client-Id` | string | 是 | 应用 ID，标识应用 + 接口鉴权 |
| `Open-Id` | string | 是 | 开放平台用户 ID，标识用户 + 接口鉴权 |

⚠️ 三者必须**互相匹配**（同一个应用、同一个用户的一次授权产物），任一不匹配会报 10302 / 10303 / 10313 / 37019。

### 1.2 响应体格式差异（重要）

**V2**：`ret` / `msg` / `data` 三段式

```json
{"ret": 0, "msg": "Succeed", "data": { ... }}
```

- `ret` 为返回码，`msg` 为描述，`data` 为业务数据
- **`ret != 0` 即视为失败**
- ⚠️ 官方明确：`msg` 仅作参考，**后续可能变动，不可用作成功判断依据**

**V3**：无 `ret/msg` 外壳，成功时 HTTP 200 + 直接返回接口定义的结构体；
失败时 HTTP 状态码非 200，返回 `{code, message, details}`（文档文字中同时出现 `code/msg/details` 两种表述）。

> 结论：**判断成功与否统一看 HTTP 状态码 + V2 的 `ret` / V3 的 `code`，不要依赖文案。**

### 1.3 接口文档模板

V2 每个接口按此模板描述：

```
接口说明 → 请求格式 → Scope → 请求头部 → 请求参数 → 请求体 → 响应体 → 请求示例 → 响应示例
```

V3 模板：

```
接口说明 → 权限说明 → 请求 → 响应 → 示例 → 错误码
```

---

## 2. ID 体系

### 2.1 `fileID`

在线文档的 ID，形如 `300000000$AAAAAAAAAAAA`。

获取方式：

1. 「新建文档」接口返回值
2. 「查询导入进度」接口返回值
3. 从文档链接取 `encodedID` → 调「fileID 转换」接口换算

### 2.2 `encodedID`

文档 URL 里的标识符。

```
https://docs.qq.com/doc/DAAAAAAAAAAAA
                        └── encodedID
```

### 2.3 `shortcutID`

快捷方式唯一标识。他人共享的文档、或通过「创建快捷方式」得到的文档属于**快捷方式文档**，用户只有操作快捷方式的权限。

- 「获取文档列表」返回的 `shortcutID` 字段非空 → 说明是快捷方式文档
- 除"打开文档"外，**其他文档操作都要传 `shortcutID`**

### 2.4 fileID 转换接口

```
GET /openapi/drive/v2/util/converter
```

**权限**：`scope.drive.readonly` / `scope.drive.file.metadata` / `scope.drive.file.metadata.readonly`（任一）
**频控**：openID 300 次/分钟

**查询参数**

| 参数名 | 类型 | 必选 | 描述 |
| --- | --- | --- | --- |
| `type` | integer | 是 | `1`：fileID → encodedID；`2`：encodedID → fileID |
| `value` | string | 是 | 需要转换的 ID |

**响应**

| 参数名 | 类型 | 描述 |
| --- | --- | --- |
| `ret` | integer | 业务返回码 |
| `msg` | string | 描述 |
| `data.encodedID` | string | type=1 时返回 |
| `data.fileID` | string | type=2 时返回 |

**示例**

```shell
curl --location --request GET 'https://docs.qq.com/openapi/drive/v2/util/converter?type=2&value=DFFFFFFFFFFFFFFFF' \
--header 'Access-Token: ACCESS_TOKEN' \
--header 'Client-Id: CLIENT_ID' \
--header 'Open-Id: OPEN_ID'
```

```
{'ret': 0, 'msg': 'Succeed', 'data': {'fileID': '300000000$AAAAAAAAAAAA'}}
```

> 💡 **SaaS 必备**：租户粘贴给你的几乎一定是 `https://docs.qq.com/doc/DXXXX` 这种链接，必须先转换。
> 建议缓存 `encodedID → fileID` 映射，避免重复消耗频控额度。

---

## 3. 频率控制

采用「维度 + 频率」描述：

| 维度 | 限制频率 |
| --- | --- |
| `fileID` | 150 次 / 分钟 |
| `openID` | 300 次 / 分钟 |

含义：单篇文档每分钟 150 次；单个用户（openID）每分钟 300 次。

**分类约定**

| 场景 | 频控 |
| --- | --- |
| 文件操作（新建文件、智能表新建子表等） | openID 300 次/分钟 |
| **导入接口** | openID **60 次/分钟** |
| **导出接口** | openID **9 次/天** |
| 单篇文档内容操作（表格加一行等） | fileID 150 次/分钟 |

> ⚠️ 导出 9 次/天是**每用户**维度（不是每应用），多租户下天然隔离，但也意味着单租户一天最多导 9 个文件。

---

## 4. 错误码

### 4.1 V3 通用错误码（HTTP Status + 业务 code）

| HTTP 状态码 | 业务错误码 | 描述 | 排查建议 |
| --- | --- | --- | --- |
| 400 | 400001 | 请求参数错误 | 确认参数是否合法 |
| 504 | 400002 | 服务端请求超时 | 服务端错误 |
| 404 | 400003 | 访问的资源不存在 | 确认资源是否被删除 |
| 409 | 400004 | 创建的资源已存在 | 确认资源是否已存在 |
| 403 | 400005 | 不允许访问该资源 | 确认是否拥有合法权限 |
| **401** | **400006** | **资源操作未授权** | **确认鉴权三元组是否合法有效** |
| **429** | **400007** | **请求数超过限制** | **降低调用频率** |
| 400 | 400008 | 访问了超过范围的资源 | 确认操作范围是否越界 |
| 503 | 400009 | 服务处于不可用状态 | 服务端错误 |
| 500 | 400010 | 服务内部错误 | 服务端错误 |

### 4.2 V2 通用码

| 返回码 | 错误类型 | 描述 |
| --- | --- | --- |
| 0 | N/A | 成功 |
| 9998 | Bad Request | 请求调用次数超过限制 |
| 10002 | Bad Request | 参数错误 |
| 10003 | Server-Side Error | 后台服务系统错误（一般 RPC 调用错误） |
| 10004 | Server-Side Error | 后台服务系统错误（编解码错误） |
| 10005 | Bad Request | 身份校验失败 |
| 10006 | Bad Request | Content-Type 错误 |
| 10007 | Bad Request | 操作权限不足 |
| 10008 | Bad Request | 不支持的权限设置操作 |
| 10010 | Bad Request | 域名不合法 |
| 10011 | Bad Request | 用户无操作权限 |
| 10013 | Server-Side Error | 后台缓存读取错误 |
| 10015 | Bad Request | 无效的临时链接 |
| 10016 | Bad Request | 临时 URL 使用次数超出上限 |
| 10017 | Bad Request | Scope 权限不合法 |
| 10018 | Server-Side Error | 路由解析失败 |
| 10019 | Bad Request | xsrf 校验失败 |
| 10020 | Bad Request | 文档克隆时 OpenID 校验错误 |
| 10021 | Bad Request | 文档克隆时 fileTicket 错误 |
| 10022-10024 | Server-Side Error | 后台服务超时 |
| 10028 | Server-Side Error | 获取文档发布态错误 |
| 10059 | Bad Request | 用户存储空间不足 |

### 4.3 V2 文件错误

| 返回码 | 错误类型 | 描述 |
| --- | --- | --- |
| 10102 | Bad Request | 文档不存在 |
| 10103 | Bad Request | 文档类型错误 |
| 10104 | Bad Request | 文档 ID 错误 |
| 10105 | Bad Request | 演示房间 ID 不存在 |
| 10106 | Bad Request | 导入文档操作太频繁 |
| 10107 | Bad Request | 导入文档操作文件过大 |
| 10108 | Bad Request | 导入文档格式不支持，请转换为 .docx 再上传 |
| 10109 | Bad Request | 导入文档文件已损坏 |
| 10110 | Bad Request | 快捷方式已存在 |
| 10111 | Bad Request | 导入文件是加密的 |
| 10129 | Bad Request | 文档状态错误 |

### 4.4 V2 文件夹错误

| 返回码 | 错误类型 | 描述 |
| --- | --- | --- |
| 10201 | Bad Request | 创建文件夹标题重复 |
| 10202 | Bad Request | 文件夹不存在 |
| 10203 | Bad Request | 目录文件夹层数过深（目前只支持五层） |
| 10204 | Bad Request | 文件夹已被删除 |

### 4.5 V2 身份信息校验错误（**重点**）

| 返回码 | 错误类型 | 描述 |
| --- | --- | --- |
| **10301** | Bad Request | Token 校验错误 |
| **10302** | Bad Request | Client ID 校验错误 |
| **10303** | Bad Request | Open ID 校验错误 |
| **10313** | Bad Request | **Access-Token 为空** |

### 4.6 V2 授权相关错误（**重点**）

| 返回码 | 类型 | 描述 |
| --- | --- | --- |
| 37000 | Bad Request | 授权参数校验失败 |
| 37001 | Bad Request | 授权权限集不合法 |
| 37002 | Server-Side Error | 授权服务内部错误 |
| 37003 | Server-Side Error | 授权服务获取重定向地址错误 |
| 37004 | Bad Request | 授权服务授权类型不合法 |
| 37005 | Server-Side Error | 授权失败 |
| 37006 | Server-Side Error | 获取应用信息失败 |
| 37007 | Server-Side Error | 获取用户信息失败 |
| 37008 | Bad Request | 用户登录态校验失败 |
| 37015 | Bad Request | 二次密码校验失败 |
| 37016 | Server-Side Error | 撤销 Token 接口失败 |
| **37017** | Bad Request | **Refresh Token 不合法** |
| **37019** | Bad Request | **Token 已过期 / 校验失败** |
| 37029 | Bad Request | 用户已取消授权 |

> **多租户自动刷新策略**：捕获 `37017`、`37019`、`10301` → 触发 refresh；
> refresh 仍失败或返回 `37017` → 标记该租户授权失效，前端引导重新授权。

### 4.7 Smartsheet 相关错误码

| 返回码 | 错误类型 | 描述 |
| --- | --- | --- |
| 22001 | Bad Request | 子表不存在 |
| 22002 | Bad Request | 视图 ID 不存在 |
| 22003 | Bad Request | 记录不存在 |
| 22004 | Bad Request | 字段不存在 |
| 22005 | Bad Request | 空视图错误 |
| 22006 | Bad Request | 记录超出限制 |
| 22007 | Bad Request | 字段超出限制 |
| 22008 | Bad Request | 单元格大小超出限制 |
| 22009 | Bad Request | 非法的视图类型 |
| 22010 | Bad Request | 非法的字段类型 |
| 22011 | Bad Request | 非法「邮件」类型值 |
| 22012 | Bad Request | 非法「电话」类型值 |
| 22013 | Bad Request | 非法「多选」类型值 |
| 22014 | Bad Request | 非法「图片」类型值 |
| 22015 | Bad Request | 非法「文本」类型值 |
| 22016 | Bad Request | 非法「链接」类型值 |
| 22017 | Bad Request | 非法「数值」类型字段 |
| 22018 | Bad Request | 非法「日期」类型字段 |
| 22019 | Bad Request | 非法「链接」类型字段 |
| 22020 | Bad Request | 非法「多选」类型字段 |
| 22021 | Bad Request | 非法「进度」类型字段 |
| 22022 | Bad Request | 视图已存在 |
| 22023 | Bad Request | 非法分页参数 |
| 22024 | Bad Request | 字段已存在 |
| 22025 | Bad Request | 重复的子表标题 |
| 22026 | Bad Request | 子表不存在 |
| 22027 | Bad Request | 文件不存在 |
| 22028 | Bad Request | 请求冲突，请重试 |
| 22029 | Bad Request | 文档超出限制（4 万行 / 150 列 / 80 万单元格 / 40MB 关键帧） |

---

## 5. 重试与容错建议

| 场景 | 处理 |
| --- | --- |
| `429` / `400007` / `9998` | 指数退避重试，并对该 openID 做令牌桶限流（300/分钟） |
| `401` / `400006` / `10301` / `10302` / `10303` / `37019` | 刷新 token 后重试一次，仍失败则标记租户授权失效 |
| `37017` | refresh_token 失效 → 必须重新授权 |
| `500` / `503` / `504` / `10003` / `10004` | 有限重试（≤3 次），记录日志告警 |
| `404` / `10102` / `10104` | 文档已删除或 ID 错误，业务层标记，不重试 |
| `22028` | 请求冲突，可直接重试 |
