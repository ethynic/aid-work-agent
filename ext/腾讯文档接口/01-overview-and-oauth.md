# 01 · 平台总览 & OAuth2.0 授权

> 快照来源：https://docs.qq.com/open/document/app/ 、https://docs.qq.com/open/document/app/oauth2/ 及其子页
> 抓取日期：2026-10-20

---

## 1. 平台总览

腾讯文档开放平台面向第三方应用提供一套基于 `HTTPS` 的 `Open API`。第三方应用**在取得用户授权的条件下**操作用户文档数据。
采用 **OAuth2.0 授权码方式**（RFC 6749 §4.1）做用户身份验证与授权。

能做的事：

- 获取文件列表中的文档信息
- 创建新的智能表格文档
- 编辑智能表格中的所有内容
- 编辑/查看在线文档中的内容

### 1.1 API 分组与版本

| 分组 | 版本 | 入口 |
| --- | --- | --- |
| 文件管理 API（文件/文件夹/权限/导入导出/搜索） | V2 | `/openapi/drive/v2/...` |
| 在线表格 API | V2 | `/openapi/sheetbook/v2/...` |
| 在线智能表 API | V2 | `/openapi/smartbook/v2/...` |
| 在线收集表 API | V2 | `/openapi/drive/v2/forms/...` |
| 在线文档（doc）内容 API | **V3** | `/openapi/doc/v3/...` |

> V3 官方说明：文件管理、在线表格、在线智能表格、在线收集表仍走 **V2**；V3 主要覆盖 doc 品类内容操作。
> 两个版本**共用同一套鉴权头**，但**响应体格式不同**（见 02 文档）。

### 1.2 限免与配额（2025-07-01 起）

- 每个应用免费调用 **20000 次** API。
- 超出需购买 API 资源包（在 https://docs.qq.com/open/document/app/ 页面填意向申请表）。
- 频率限制见 02 文档「频率控制」。

### 1.3 域名区分（易踩坑）

- **腾讯文档**：`docs.qq.com` ← 本快照覆盖的范围
- **企业微信文档**：`doc.weixin.qq.com` ← 另一套体系，API 不通用

---

## 2. OAuth2.0 授权流程

### 2.1 准备工作

1. 登录开发者后台：https://docs.qq.com/open/developers/
2. 注册成为开发者 → 创建**第三方应用** → **等待审核通过**
3. 审核通过后系统分配 `Client ID` 和 `Client Secret`

- **Client ID**：第三方应用唯一标识，OAuth 与 OpenAPI 调用都要带
- **Client Secret**：应用密钥，后台服务间调用时验证应用合法性
  ⚠️ 必须妥善存储，不要泄漏；泄漏后及时登录开放平台修改

### 2.2 四步流程（Web 应用）

```
① 你的服务构造授权请求 URL，前端跳转
② 用户在腾讯文档页面手动点击"同意授权"
③ 腾讯文档带上 code 重定向到你的 redirect_uri
④ 你的【后端】用 code 换 access_token + refresh_token
```

小程序 / App 场景：跳转腾讯文档小程序引导授权（见 https://docs.qq.com/open/document/mini-program/call-openapi/ ）

### 2.3 令牌有效期

| 参数 | 含义 | 有效时间 |
| --- | --- | --- |
| `Authorization Code` | 授权码 | **5 分钟**（授权页写 10 分钟），且只能使用一次 |
| `Access Token` | 用户访问令牌 | **30 天**（但 token 接口示例 `expires_in = 259200` = 3 天，以 `expires_in` 为准） |
| `Refresh Token` | 用户刷新令牌 | **1 年** |

⚠️ 第 ④ 步**必须在你的后台服务发起**，防止 Client Secret 泄漏。
⚠️ 所有访问 OpenAPI 的请求都必须由后台服务发起（官方明确要求）。

### 2.4 scope

- 发起授权时 `scope` 参数**固定为 `all`**。
- 实际能拿到的权限取决于开放平台给你的应用审批通过的 scope。
- token 响应里的 `scope` 字段是逗号分隔的实际权限串，例如：
  `"scope.file.editable,scope.folder.creatable"`

常见 scope（从各接口「权限说明」中汇总）：

| scope | 用途 |
| --- | --- |
| `scope.doc` / `scope.doc.readonly` | 读取 doc 文档内容 |
| `scope.drive.readonly` | 拉取目录列表 |
| `scope.drive.file.metadata` / `.metadata.readonly` | 查询文件元数据、fileID 转换 |
| `scope.drive.exportable` | 导出文档 |
| `scope.user.info.base` | 获取用户信息（校验 token 有效性） |

---

## 3. 授权相关三个接口

### 3.1 发起授权

```
GET https://docs.qq.com/oauth/v2/authorize
Accept: application/json
```

一般**由前端/客户端发起**。不同端用户看到的页面不同：
浏览器内 → 扫码页；微信内 → 直接进入授权确认页。

**请求参数**

| 名称 | 类型 | 必选 | 备注 |
| --- | --- | --- | --- |
| `client_id` | string | 是 | 第三方应用唯一标识 |
| `redirect_uri` | string | 是 | 授权回调地址，需 URL Encode；后台校验域名白名单；**仅支持 HTTPS** |
| `new_login` | string | 是 | **固定填 `1`**，使用新版授权登录组件 |
| `response_type` | string | 是 | **固定填 `code`** |
| `scope` | string | 是 | **固定为 `all`** |
| `state` | string | 否 | 原样带回，用于防 CSRF，建议带上 |

新版授权登录组件对容器尺寸有要求：**宽 ≥ 480px，高 ≥ 610px**，否则样式异常。

**响应（重定向到你的回调地址）**

| 名称 | 类型 | 描述 |
| --- | --- | --- |
| `code` | string | 换取 access_token 的授权码，有效期 10 分钟，只能用一次 |
| `state` | string | 请求时传了就回传 |

**请求示例**

```shell
GET https://docs.qq.com/oauth/v2/authorize?client_id=CLIENT_ID&redirect_uri=https%3a%2f%2fexample.com%2fcallback&new_login=1&response_type=code&scope=all&state=STATE
```

**成功回调**

```
http://example.com/callback?code=CODE&state=STATE
```

**失败回调**

```
http://example.com/callback?error=server_error&error_description=The+authorization+server+encountered+an+unexpected+condition+that+prevented+it+from+fulfilling+the+request&state=STATE
```

（出现该错误 → 检查 `redirect_uri` 等参数是否正确）

---

### 3.2 获取 Access Token

```
GET https://docs.qq.com/oauth/v2/token
Accept: application/json
```

⚠️ 必须由**第三方服务端**发起。
⚠️ `code` 过期时间 10 分钟；`redirect_uri` 必须与发起授权时**完全一致**；否则返回 **HTTP 401**。

**请求参数**

| 名称 | 类型 | 必选 | 备注 |
| --- | --- | --- | --- |
| `client_id` | string | 是 | 第三方应用唯一标识 |
| `client_secret` | string | 是 | 第三方应用密钥 |
| `redirect_uri` | string | 是 | 必须与 authorize 请求中的一致 |
| `grant_type` | string | 是 | 固定 `authorization_code` |
| `code` | string | 是 | 授权成功后拿到的 code |

**响应体**

| 名称 | 类型 | 备注 |
| --- | --- | --- |
| `access_token` | string | 用户访问令牌 |
| `token_type` | string | 统一返回 `Bearer` |
| `expires_in` | integer | Access Token 过期时间，单位秒 |
| `refresh_token` | string | 刷新令牌，1 年有效期 |
| `user_id` | string | 用户唯一标识，即 **Open ID** |
| `scope` | string | 权限范围，`,` 分隔 |

**请求示例**

```shell
curl --location --request GET https://docs.qq.com/oauth/v2/token?client_id=CLIENT_ID&client_secret=CLIENT_SECRET&redirect_uri=https%3a%2f%2fexample.com%2fcallback&grant_type=authorization_code&code=CODE
```

**响应示例**

```json
{
  "access_token": "ACCESSTOKENEXAMPLE",
  "token_type": "Bearer",
  "refresh_token": "REFRESHTOKENEXAMPLE",
  "expires_in": 259200,
  "scope": "scope.file.editable,scope.folder.creatable",
  "user_id": "bcb50c8a4b724d86bbcf6fc64c5e2b22"
}
```

---

### 3.3 刷新 Access Token

```
GET https://docs.qq.com/oauth/v2/token
Accept: application/json
```

⚠️ 必须由服务端发起。

**请求参数**

| 名称 | 类型 | 必选 | 备注 |
| --- | --- | --- | --- |
| `client_id` | string | 是 | 第三方应用唯一标识 |
| `client_secret` | string | 是 | 第三方应用密钥 |
| `grant_type` | string | 是 | 固定 `refresh_token` |
| `refresh_token` | string | 是 | 授权成功后获取的刷新令牌 |

**响应体**

| 名称 | 类型 | 备注 |
| --- | --- | --- |
| `access_token` | string | **新的**用户访问令牌 |
| `token_type` | string | `Bearer` |
| `expires_in` | Int64 | 过期时间（秒） |
| `scope` | string | 权限范围 |
| `user_id` | string | Open ID |

> 注意：刷新响应**不含新的 refresh_token**，继续沿用旧的（有效期 1 年不变）。

**请求示例**

```shell
curl --location --request GET https://docs.qq.com/oauth/v2/token?client_id=CLIENT_ID&client_secret=CLIENT_SECRET&grant_type=refresh_token&refresh_token=REFRESH_TOKEN
```

**响应示例**

```json
{
  "access_token": "ACCESSTOKENEXAMPLE",
  "token_type": "Bearer",
  "expires_in": 259200,
  "scope": "scope.file.editable,scope.folder.creatable",
  "user_id": "bcb50c8a4b724d86bbcf6fc64c5e2b22"
}
```

---

### 3.4 获取用户信息（兼作 token 有效性校验）

```
GET https://docs.qq.com/oauth/v2/userinfo
Accept: application/json
```

**权限**：`scope.user.info.base`（任一即可）

**请求参数**

| 名称 | 类型 | 必选 | 备注 |
| --- | --- | --- | --- |
| `access_token` | string | 是 | Access Token |

**响应体**

| 参数名 | 类型 | 描述 |
| --- | --- | --- |
| `ret` | integer | 业务返回码 |
| `msg` | string | 业务返回码描述 |
| `data.openID` | string | 用户唯一标识 |
| `data.nick` | string | 昵称 |
| `data.avatar` | string | 头像 |
| `data.source` | string | 授权方式：`wx` 微信 / `qq` QQ |
| `data.bindSource` | string | 绑定账号：`wx` / `qq` |
| `data.unionID` | string | 同一开发者账号下，同一用户唯一 |

**示例**

```bash
curl --location --request GET 'https://docs.qq.com/oauth/v2/userinfo?access_token=ACCESS_TOKEN'
```

```json
{
    "ret": 0,
    "msg": "Succeed",
    "data": {
        "openID": "OpenIDTest",
        "nick": "nickTest",
        "avatar": "https://example.com/avatar.png",
        "source": "wx",
        "unionID": "UnionIDTest"
    }
}
```

> ⚠️ 官方提示：`Open ID` 在个别情况下**可能变化**（用户做账号绑定/解绑操作）。
> 建议用 `unionID` 做跨应用的租户身份对齐。

---

## 4. 完整时序（SaaS 多租户）

```
[你的前端]  点击"绑定腾讯文档"
    → 302 https://docs.qq.com/oauth/v2/authorize?...&state=<tenant_id>
[腾讯文档]  用户扫码/确认
    → 302 https://your.com/callback?code=XXX&state=<tenant_id>
[你的后端]  GET /oauth/v2/token (code 换 token)          ← 服务端
    → 存库: tenant_id, open_id(user_id), access_token,
            refresh_token, expires_at=now+expires_in, refresh_expires_at=now+365d
[你的后端]  GET /oauth/v2/userinfo?access_token=...      ← 可选，拿 nick/avatar/unionID
[你的后端]  后续业务调用: Header 带 Access-Token / Client-Id / Open-Id
[你的定时任务] expires_at 前 24h → refresh → 更新库
              refresh 失败(37017/37019) → 标记租户"授权已失效"，推送重新授权
```
