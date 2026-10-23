# 07 · 个人账号 vs 企业账号：三套完全不同的接入方式

> 调研日期：2026-10-20
> 来源：https://docs.qq.com/open/document/saas 、.../saas/common/api_info.html 、.../saas/openapi/file/ 、
> https://id.tencent.com/docs/openapi/api-guide/get-token 、.../authentication/app-access-token-internal 、
> .../authentication/user-access-token/ 、https://developer.work.weixin.qq.com/document/path/101161
>
> ⚠️ **本文修正了 01/02 文档中的部分结论**——之前写的"没有 client_credentials"**只适用于 C 端个人版**，
> 企业版 SaaS 通过 OneID **是支持的**。

---

## 0. 先纠正一个认知

> ❌ "个人账号没有 OAuth 功能" —— **这个理解反了。**

**OAuth2.0 授权码模式恰恰是为个人用户设计的。**
任何一个用微信/QQ 登录 `docs.qq.com` 的个人用户，都能通过
`https://docs.qq.com/oauth/v2/authorize` 授权第三方应用访问**他自己的**文档。
官方「接入教程」里走的就是这条链路（客户名"客服小迈"，明显是个人/普通账号）。

个人账号**没有**的是另一件东西：**企业管理后台 → 自建应用**。
那个只有买了腾讯文档企业版（SaaS 部署 / 私有化部署）的租户企业才有。

所以真正的分界线不是"有没有 OAuth"，而是：

| | 个人版（C 端） | 企业版（SaaS 部署） |
| --- | --- | --- |
| 域名 | `docs.qq.com`（固定） | `saas.docs.qq.com` 或**企业专属域名**（每家不同） |
| 应用创建方 | **你（ISV）** 在开放平台创建 | **租户企业管理员**在企业后台创建"自建应用" |
| 凭证归属 | 你的 client_id/secret，全局一份 | 租户自己的 client_id/secret，**每家一套** |
| API 版本 | `/openapi/drive/v2/`、`/openapi/doc/v3/` | `/openapi/drive/v3/`、`/openapi/smartsheet/v2/` |
| 鉴权头 | `Access-Token` + `Client-Id` + `Open-Id`（三元组） | `Access-Token` + `Client-Id`（**两元组，无 Open-Id**） |
| Token 来源 | `docs.qq.com/oauth/v2/token` | **OneID**（`identity.tencent.com` / `id.tencent.com`） |
| client_credentials | ❌ 不支持 | ✅ **支持（应用级 token）** |
| 组织信息 | 无 | ✅ 用户管理 API / 部门管理 API |

---

## 1. 三条路径全景

```
租户的腾讯文档账号
  │
  ├─ A. 个人版（微信/QQ 登录 docs.qq.com）
  │     └─ 走 C 端开放平台：你的应用 + 用户 OAuth 授权
  │        域名固定 docs.qq.com，三元组鉴权，/openapi/drive/v2/ + /openapi/doc/v3/
  │
  ├─ B. 企业版 SaaS 部署（saas.docs.qq.com 或企业专属域名）
  │     └─ 走 SaaS 开放接口：租户企业的自建应用 + OneID 拿 token
  │        {domain} 每家不同，两元组鉴权，/openapi/drive/v3/ + /openapi/smartsheet/v2/
  │
  └─ C. 企业微信文档（doc.weixin.qq.com）★ 又一个独立体系
        └─ qyapi.weixin.qq.com/cgi-bin/wedoc/*，用企业微信 corpid + secret
```

> ⚠️ **接单前必须先让租户确认是哪种**。最直接的判断方式：让租户把文档链接发给你，看域名前缀。
> `docs.qq.com/doc/...` → A；`xxx.saas.docs.qq.com` 或企业自定义域名 → B；`doc.weixin.qq.com/...` → C。

---

## 2. 路径 A：个人版（C 端）—— 已在 01~05 文档覆盖

**核心要点回顾**

- 你在 https://docs.qq.com/open/developers/ 注册开发者、创建应用、等审核 → 拿 client_id/secret
- 租户用户跳 `https://docs.qq.com/oauth/v2/authorize?...&new_login=1&scope=all` 扫码授权
- 你后端用 code 换 `access_token` + `refresh_token` + `user_id`(即 open_id)
- 调 API 时 Header 带 `Access-Token` / `Client-Id` / `Open-Id`
- API：`/openapi/drive/v2/...`（文件管理）、`/openapi/doc/v3/{fileId}`（读正文）、
  `/openapi/smartbook/v2/...`（智能表）、`/openapi/sheetbook/v2/...`（表格）

**Token 有效期修正**：官方「接入教程」里的真实例子是
`"expires_in": 2592000`（= **30 天**），而 `access_token` 接口文档里的示例写 `259200`（3 天）。
**倾向于 30 天，但仍必须读 `expires_in` 字段，不能硬编码。**

**仍未 100% 确认的点（建议你实测）**：
「开放平台是否允许个人主体开发者注册」——社区里两派说法都有：
- 2024 年兔小巢官方回复：审核要营业执照 + **对公账号打款**认证（= 企业主体）
- 2026-06 腾讯新闻稿：OpenAPI「面向企业和个人开发者，**不需要企业资质**」
> 👉 结论：政策可能已放宽。**请直接登录 https://docs.qq.com/open/developers/ 走一遍注册流程确认**，
> 这决定了你作为 ISV 能否只用一个企业主体覆盖所有个人租户。

---

## 3. 路径 B：企业版 SaaS —— 独立体系，重点看这里

### 3.1 官方入口

https://docs.qq.com/open/document/saas

> 官方原文：腾讯文档 SaaS 开放接口为 **SaaS 部署的企业**提供了一套基于 HTTPS 的 Open API，
> **自建应用**可以通过开放接口操作**企业下用户**文档数据。
> 在使用之前，用户需要在**企业管理后台创建一个自建应用**，并获取相应的 Client ID 和 Client Secret。

### 3.2 鉴权头（与个人版不同！）

```
Access-Token: <access_token>     ← 只有这两个
Client-Id:    <client_id>
```

**没有 `Open-Id`**（用户身份由 token 隐含）。

**响应头**会带 `X-Trace-Id`（遵循 OpenTelemetry 规范），**排障时务必记录这个字段**。

### 3.3 API 路径（全部变了）

| 分类 | 路径前缀 |
| --- | --- |
| 文件管理 | `/openapi/drive/v3/files...` |
| 异步任务（导入/导出/上传/下载/DOC转PDF） | `/openapi/drive/v3/files:xxx`、`/openapi/drive/v3/tasks/{taskId}` |
| 权限管理 | `/openapi/drive/v3/files/{fileId}/permissions...` |
| 在线智能表 | `/openapi/smartsheet/v2/files/{fileID}/sheets/{sheetID}/...` |
| 用户管理 / 部门管理 | `identity.tencent.com`（OneID 体系，不是 docs.qq.com） |
| 上传图片 / 附件 | `/api/v6/openapi/drive/v3/image:upload`、`/api/v6/openapi/drive/v3/attachment:upload` |

**文件接口清单（SaaS 版）**

| 接口 | 方法 | 路径 |
| --- | --- | --- |
| 新建文件 | POST | `/openapi/drive/v3/files` |
| 查询文件信息 | GET | `/openapi/drive/v3/files/{fileId}` |
| 修改文件信息 | PATCH | `/openapi/drive/v3/files/{fileId}` |
| 删除文件 | DELETE | `/openapi/drive/v3/files/{fileId}` |
| 生成文件副本 | POST | `/openapi/drive/v3/files/{fileId}:copy` |
| 恢复文件 | POST | `/openapi/drive/v3/files/{fileId}:recover` |
| 移动文件 | POST | `/openapi/drive/v3/files/{fileId}:move` |
| **拉取文件列表** | POST | `/openapi/drive/v3/files:list` |
| 搜索文件 | POST | `/openapi/drive/v3/files:search` |
| 导入文件 | POST | `/openapi/drive/v3/files:import` |
| 导出文件 | POST | `/openapi/drive/v3/files/{fileId}:export` |
| 上传文件 | POST | `/openapi/drive/v3/files:upload` |
| **下载文件** | POST | `/openapi/drive/v3/files/{fileId}:download` |
| 查询任务进度 | GET | `/openapi/drive/v3/tasks/{taskId}` |
| 取消任务 | POST | `/openapi/drive/v3/tasks/{taskId}:cancel` |
| **DOC 转 PDF** | POST | `/openapi/drive/v3/tasks/{taskId}:convertAndExportPDF` |

> 💡 比 C 端强的地方：**有独立的"下载文件"和"DOC转PDF"接口**，不用像 C 端那样被"每天 9 次导出"卡死。
> 这对你要"读文档内容"是重大利好。

**拉取文件列表**（注意是 POST + JSON body，不是 GET + query）

```shell
curl --location 'https://{domain}/openapi/drive/v3/files:list' \
--header 'Access-Token: ACCESS_TOKEN' \
--header 'Client-Id: CLIENT_ID' \
--data '{
    "list_type":"MY_DOC",
    "offset": 0,
    "limit": 10,
    "order_by":"FILE_NAME",
    "desc":true
}'
```

`listType` 枚举：`MY_DOC` / `RECENT` / `STAR` / `TRASH` / `COLLABORATION_LIST`(共享空间) / `SHARED`(与我共享)

**文件类型 FileType**（比 C 端丰富）：
`DOC` / `SHEET` / `SLIDE` / `FORM` / `PDF` / `MIND` / `FLOWCHART` / `REC` / `DRIVE` / `FOLDER` /
`COLLABORATE`(共享空间) / `SMART_SHEET` / `SMART_CANVAS` / `BOARD`

### 3.4 Token 获取：走 OneID（腾讯统一身份认证）

文档里 `{domain}` 是**占位符** → **每家企业域名不同**，必须让租户提供。

**两种 token：**

#### (1) 应用级 token —— `client_credentials`，⭐ 这是你最想要的

```
POST https://<企业管理后台给的 Token 端点>
Content-Type: application/x-www-form-urlencoded

client_id=<Client ID>
client_secret=<Client Secret>
grant_type=client_credentials
```

响应：`access_token` / `token_type=Bearer` / `expires_in`（秒）

✅ **不需要任何用户点击授权**，服务端直接换 token —— 完美适配 SaaS 后台定时同步。
❌ 但它是"**以应用身份**调用，校验**应用的数据访问范围**"——
注意：这可能是应用的可见范围，而**不是**任意用户的所有文档。**必须实测确认能读到哪些文档。**
❌ 官方说明：应用级 token **不返回 refresh_token**，过期后重新调用即可。

#### (2) 用户级 token —— OAuth 授权码

- 管理后台 → 自建应用 → **认证配置 → 登录回调地址**（支持配多个）
- 授权码：`GET <认证端点>?client_id=&response_type=code&redirect_uri=&state=&scope=`
  - ⚠️ 不能用 ajax，**必须后端 302 重定向或 `window.open`**
  - ⚠️ **`scope` 中必须包含 `offline_access` 才会返回 `refresh_token`** ← 大坑，别忘了
  - `scope` 中加 `openid` 会额外返回 `id_token`（JWT，含 `sid`/`auth_time`/`sexp`/`amr`）
- 换 token：`POST <Token端点>`，`grant_type=authorization_code` + `code` + `redirect_uri`
  （code 有效期 **5 分钟**，一次性；`redirect_uri` 指定到 path，不含 query）
- 刷新：`grant_type=refresh_token` + `client_id` + `client_secret` + `refresh_token`
  - ⚠️ **刷新会返回新的 refresh_token**，官方建议保存并覆盖旧的（旧的不会立即注销）

**OneID 自身鉴权方式**：`Authorization: Bearer <access_token>`
（注意：调**腾讯文档 SaaS 的** API 时用 `Access-Token` 头；调 **OneID 自己的**用户/部门管理 API 时用 `Authorization: Bearer`。两套头，别混。）

### 3.5 额外能力

- **用户管理 API**：https://identity.tencent.com/docs/openapi/contact/user/get/
- **部门管理 API**：https://identity.tencent.com/docs/openapi/contact/department/get/
  → 可以拉组织架构，对 SaaS 做"按部门授权/按成员同步"很有用
- **智能表自动化接口**：查询自动化配置、运行详情、批量运行记录
- **内容权限接口**：子表权限、指定成员额外权限的增删改

---

## 4. 路径 C：企业微信文档（别搞混）

如果租户说的"企业账号"其实是指**企业微信里的文档**，那是第三套：

- 文档域名：`doc.weixin.qq.com`
- API：`https://qyapi.weixin.qq.com/cgi-bin/wedoc/...`
- 鉴权：企业微信 `corpid` + 应用 `secret` 换 `access_token`（query 参数 `?access_token=`）
- 获取文档数据：`POST /cgi-bin/wedoc/document/get`，body `{"docid": "DOCID"}`
  → 返回的 `document` 结构与腾讯文档 V3 的 Node 树**几乎一致**
  （`Node` / `Property` / `SectionProperty` / `ParagraphProperty` / `RunProperty` / `TableProperty` ...）
  → **所以你写的文档树递归解析器可以复用**
- 权限说明：自建应用需配置到"可调用应用"列表；第三方应用需具有"文档"权限

---

## 5. 对你的 SaaS 架构的影响（这是最关键的）

原来的假设是"**一个 client_id + N 个 open_id**"，现在要改成"**两条适配器**"。

### 5.1 凭证模型要重构

```
之前（仅个人版）：
  tenant_credential: tenant_id, open_id, access_token, refresh_token, expires_at

现在（混合）：
  tenant_docs_binding:
    tenant_id
    provider_type          # 'personal' | 'saas_enterprise' | 'wecom'
    api_domain             # 个人版固定 docs.qq.com；企业版是 {domain}，必须存
    client_id              # 个人版=你的应用；企业版=租户自建应用
    client_secret          # 企业版才有（个人版只有你一份，不必按租户存）
    token_endpoint         # 企业版 OneID Token 端点，每家可能不同
    auth_endpoint          # 企业版 OneID 认证端点
    access_token           # 加密存储
    refresh_token          # 加密存储（企业版需 scope 含 offline_access 才有）
    expires_at
    grant_mode             # 'user_oauth' | 'client_credentials'
    open_id                # 仅个人版需要
```

### 5.2 抽象一层 Provider 接口

```python
class DocProvider(Protocol):
    def list_files(self, tenant) -> list[FileInfo]: ...
    def get_file_meta(self, tenant, file_id) -> FileInfo: ...
    def get_doc_content(self, tenant, file_id) -> Document: ...   # 读正文
    def export_file(self, tenant, file_id, fmt) -> bytes: ...
    def refresh_credentials(self, tenant) -> None: ...

class PersonalProvider(DocProvider):   # docs.qq.com  三元组 + drive/v2 + doc/v3
class SaasEnterpriseProvider(DocProvider):  # {domain} 两元组 + drive/v3 + OneID
class WecomDocProvider(DocProvider):   # qyapi.weixin.qq.com/cgi-bin/wedoc
```

**可复用的部分**：文档内容树（Node/Paragraph/Run/Text）的解析器——
腾讯文档 V3 和企业微信 wedoc 的结构几乎相同，**解析器写一次就够**。
要分开的是：鉴权、路径、分页、错误码。

### 5.3 各路径的"读正文"能力对比

| 路径 | 读正文方式 | 限制 |
| --- | --- | --- |
| A 个人版 | `GET /openapi/doc/v3/{fileId}`（结构化树）<br>或 `async-export`（每天 9 次） | 导出配额极紧 |
| B 企业版 SaaS | 有 `files:export` + `files:download` + `convertAndExportPDF` 完整异步任务链 | 配额未在文档中明示，需实测 |
| C 企业微信 | `POST /cgi-bin/wedoc/document/get` | 需"文档"权限 |

### 5.4 落地建议

1. **先做路径 A（个人版）**——流程最标准、文档最全、也最容易拿测试账号验证。
2. **路径 B 必须找一家真实企业租户联调**：`{domain}`、OneID 端点、应用级 token 的可见范围
   这三件事**只能实测**，文档给不了。
3. **接单前置问题**（给用户填的表单）：
   - 文档链接域名是哪种？（`docs.qq.com` / `xxx.saas.docs.qq.com` / 其他自定义域名 / `doc.weixin.qq.com`）
   - 如果是企业版：能否提供 API 域名 + 在企业管理后台创建自建应用并把 Client ID/Secret 给我们？
   - 期望的授权方式：管理员一次授权（应用级 token），还是每个员工各自授权（用户级 token）？
4. **优先争取企业版租户走「应用级 token（client_credentials）」**——
   省掉用户授权交互、省掉 refresh_token 管理，是多租户 SaaS 最省心的形态。
   前提是实测确认应用级 token 能读到目标文档。

---

## 6. 官方文档入口（企业版）

- SaaS 开放接口总览：https://docs.qq.com/open/document/saas
- API 调用相关信息（鉴权头）：https://docs.qq.com/open/document/saas/common/api_info.html
- SaaS 文件管理：https://docs.qq.com/open/document/saas/openapi/file/
- SaaS 文件概述（FileType 枚举）：https://docs.qq.com/open/document/saas/openapi/file/overview.html
- SaaS 权限管理：https://docs.qq.com/open/document/saas/openapi/permission/
- SaaS 在线智能表：https://docs.qq.com/open/document/saas/openapi/smartsheet/
- OneID 获取访问凭证：https://id.tencent.com/docs/openapi/api-guide/get-token
- OneID 应用级 token：https://id.tencent.com/docs/openapi/authentication/app-access-token-internal/
- OneID 用户级 token：https://id.tencent.com/docs/openapi/authentication/user-access-token/
- OneID 用户管理：https://identity.tencent.com/docs/openapi/contact/user/get/
- OneID 部门管理：https://identity.tencent.com/docs/openapi/contact/department/get/
- 企业管理后台：https://workspace.tencent.com/admin/#/selfApp
- 企业微信文档（路径 C）：https://developer.work.weixin.qq.com/document/path/101161
