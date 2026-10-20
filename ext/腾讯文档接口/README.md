# 腾讯文档开放平台 API — 本地快照

> 抓取日期：2026-10-20
> 来源：https://docs.qq.com/open/document/app/ （腾讯文档开放平台开发文档）
> 用途：SaaS 平台对接腾讯文档、以租户身份读取文档内容的前置技术调研

## 目录

| 文件 | 内容 |
| --- | --- |
| [01-overview-and-oauth.md](./01-overview-and-oauth.md) | 平台总览、能力边界、OAuth2.0 授权全流程（3 个接口） |
| [02-api-common.md](./02-api-common.md) | 请求规范、鉴权三元组、ID 体系、频率控制、错误码 |
| [03-file-management-api.md](./03-file-management-api.md) | 文件管理 API（V2）：文件/文件夹/权限/导入导出/搜索 |
| [04-doc-api.md](./04-doc-api.md) | 在线文档 API（V3）：获取 doc 内容、批量更新 |
| [05-sheet-smartsheet-form-api.md](./05-sheet-smartsheet-form-api.md) | 在线表格 / 智能表 / 收集表 API（V2） |
| [06-oss-projects.md](./06-oss-projects.md) | Python 开源对接项目调研与选型建议 |

---

## 一、关键结论（先看这里）

### 1. 只有一种授权模式：OAuth2.0 授权码模式（用户级）

腾讯文档开放平台**没有**对外公开的 `client_credentials`（应用级/服务账号）模式。
所有 OpenAPI 都必须在请求头携带 **三元组**：

```
Access-Token: <租户用户的 access_token>
Client-Id:    <你的应用 client_id>
Open-Id:      <租户用户的 open_id>
```

含义：**你的 SaaS 平台用自己的 `client_id/client_secret`，但访问的是"租户那个腾讯文档账号"的数据。**
所以「租户提供登录凭据」在实现上 = **租户在你的平台上完成一次 OAuth 授权**，你拿到并托管他的 `access_token` / `refresh_token` / `open_id`。

> ⚠️ 网上（含部分 AI 生成的文章）流传的 `https://docs.qq.com/api/v2/token?grant_type=client_credentials&appid=...` 写法
> **不在官方文档中，且实践中不可用**，不要采信。

### 2. Token 生命周期（多租户设计的硬约束）

| 令牌 | 有效期 | 说明 |
| --- | --- | --- |
| Authorization Code | 5 分钟（授权页写 10 分钟） | 一次性 |
| Access Token | 官方文字写 30 天，但接口示例 `expires_in=259200`（= 3 天） | **以响应里的 `expires_in` 为准，不要硬编码 30 天** |
| Refresh Token | 1 年 | 过期后必须让租户重新授权 |

**必须实现的机制**：access_token 过期前自动用 refresh_token 刷新；刷新失败（37017/37019）→ 标记租户授权失效 → 引导重新授权。
**Refresh Token 1 年** 是硬边界，SaaS 必须做到期提醒/续期流程，否则一年后静默失效。

### 3. 获取"文档内容"有两条路，选哪条差别很大

| 方式 | 接口 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A. 结构化读取 | `GET /openapi/doc/v3/{fileId}` | 实时、无限次（150 次/分钟） | 返回的是 `Document → MainStory → Section → Paragraph → Run → Text` 的深层属性树，**需要自己写递归解析器** |
| B. 异步导出 | `POST /openapi/drive/v2/files/{fileID}/async-export` | 直接拿到 docx/pdf 文件，现成解析器可用 | **每用户每天仅 9 次**，且 `operationID` 只有 20 分钟有效期 |

**建议**：结构化读取（A）做主链路，导出（B）作为低频兜底（如租户主动触发"生成 PDF 归档"）。
导出配额是**按 openID（即每个租户）每天 9 次**，多租户之间是隔离的，这点反而对你有利。

### 4. 计费 / 配额

- 2025-07-01 起：每个应用**免费 2 万次 API 调用**，超出需购买资源包。
- 频率控制：单文档 `fileID` 150 次/分钟；单用户 `openID` 300 次/分钟；导入 60 次/分钟；导出 9 次/天。

### 5. 域名注意

腾讯文档（`docs.qq.com`）与企业微信文档（`doc.weixin.qq.com`）是**两套体系**，API 不能混用。

---

## 二、最小可用调用链（Python 伪代码骨架）

```python
import requests

BASE = "https://docs.qq.com"
CLIENT_ID = "..."      # 你的应用（全局一份）
CLIENT_SECRET = "..."

# 1) 引导租户去授权 —— 由前端跳转
def build_authorize_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,   # 必须 URL Encode，且域名在开放平台白名单内，仅 HTTPS
        "new_login": "1",               # 固定 1，新版登录组件
        "response_type": "code",        # 固定
        "scope": "all",                 # 官方固定为 all
        "state": state,                 # 防 CSRF，建议带
    }
    return f"{BASE}/oauth/v2/authorize?{urlencode(params)}"

# 2) 回调里用 code 换 token —— 必须在后端发起
def exchange_token(code: str, redirect_uri: str) -> dict:
    r = requests.get(f"{BASE}/oauth/v2/token", params={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": redirect_uri,   # 必须与第 1 步完全一致
        "grant_type": "authorization_code",
        "code": code,
    }, headers={"Accept": "application/json"}, timeout=10)
    r.raise_for_status()
    return r.json()
    # -> access_token / refresh_token / expires_in / scope / user_id(即 open_id)

# 3) 刷新
def refresh(refresh_token: str) -> dict:
    r = requests.get(f"{BASE}/oauth/v2/token", params={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }, headers={"Accept": "application/json"}, timeout=10)
    r.raise_for_status()
    return r.json()
    # 注意：刷新不会返回新的 refresh_token，沿用旧的

# 4) 调业务接口
def headers(access_token: str, open_id: str) -> dict:
    return {
        "Access-Token": access_token,
        "Client-Id": CLIENT_ID,
        "Open-Id": open_id,
        "Accept": "application/json",
    }

def get_doc_content(file_id: str, access_token: str, open_id: str) -> dict:
    r = requests.get(f"{BASE}/openapi/doc/v3/{file_id}",
                     headers=headers(access_token, open_id), timeout=20)
    r.raise_for_status()
    return r.json()   # {"document": {...}, "version": 4}
```

---

## 三、多租户落地要提前确认的坑

1. **Client Secret 只有一份**：你是一个应用（`client_id`），所有租户共用。租户身份靠 `open_id + access_token` 区分。
   → 数据库至少要存：`tenant_id, open_id, access_token, refresh_token, expires_at, refresh_expires_at, scope`。
2. **`redirect_uri` 白名单**：在 https://docs.qq.com/open/developers/ 后台配置，且**仅支持 HTTPS**。
   多租户场景下一般用一个统一回调域名 + `state` 带回租户标识。
3. **应用需要审核**：创建第三方应用后要等审核通过才拿到 `Client ID / Client Secret`。
4. **`scope` 申请**：授权时固定填 `all`，但你能拿到的实际 scope 取决于开放平台给你应用批的权限。
   读取文档内容至少需要 `scope.doc` 或 `scope.doc.readonly`；列目录需要 `scope.drive.readonly`；导出需要 `scope.drive.exportable`。
5. **`Open ID` 可能变化**：官方明确说用户做账号绑定/解绑时 `Open ID` 会变。
   → 不要只拿 open_id 做租户唯一键，建议 `(client_id, open_id)` 组合，并保留 `unionID` 做兜底对齐。
6. **文档链接 → fileID 需要一次转换**：租户给你的通常是 `https://docs.qq.com/doc/DXXXXXX`（这是 `encodedID`），
   要先调 `GET /openapi/drive/v2/util/converter?type=2&value=DXXXXXX` 换出 `fileID`（形如 `300000000$AAAAAAAAAAAA`）。
7. **快捷方式文档**：列表接口返回的 `shortcutID` 非空时，是别人共享给你的文档，做内容操作要传 `shortcutID` 而不是 `fileID`。
8. **导出配额极易打爆**：每用户每天 9 次。别用导出做同步，除非做缓存。

---

## 四、快照内的已知文档矛盾（以实测为准）

| 项 | 文档 A 说 | 文档 B 说 | 建议 |
| --- | --- | --- | --- |
| Authorization Code 有效期 | 授权流程页：5 分钟 | 发起授权页：10 分钟 | 按 5 分钟处理，拿到 code 立刻换 token |
| Access Token 有效期 | 授权流程页：30 天 | token 接口示例 `expires_in: 259200`（3 天） | **读 `expires_in` 字段，提前 1 天刷新** |
| 新版授权登录组件 | 需要 `new_login=1` | 容器 ≥ 480×610 px | 弹窗/iframe 接入时注意尺寸 |

---

## 五、官方文档入口速查

- 开放平台总览：https://docs.qq.com/open/document/app/
- 授权流程：https://docs.qq.com/open/document/app/oauth2/
- 发起授权：https://docs.qq.com/open/document/app/oauth2/authorize.html
- 获取 Token：https://docs.qq.com/open/document/app/oauth2/access_token.html
- 刷新 Token：https://docs.qq.com/open/document/app/oauth2/refresh_token.html
- 用户信息：https://docs.qq.com/open/document/app/oauth2/user_info.html
- OpenAPI V2 介绍：https://docs.qq.com/open/document/app/openapi/v2/
- OpenAPI V3 介绍：https://docs.qq.com/open/document/app/openapi/v3/
- 文件管理：https://docs.qq.com/open/document/app/openapi/v2/file/
- 在线文档：https://docs.qq.com/open/document/app/openapi/v3/doc/
- 在线表格：https://docs.qq.com/open/document/app/openapi/v2/sheet/
- 智能表：https://docs.qq.com/open/document/app/openapi/v2/smartsheet/
- 收集表：https://docs.qq.com/open/document/app/openapi/v2/form/
- 频率控制：https://docs.qq.com/open/document/app/openapi/v2/frequency_control/
- V2 错误码：https://docs.qq.com/open/document/app/openapi/v2/CODE.html
- V3 错误码：https://docs.qq.com/open/document/app/openapi/v3/common/code.html
- 开发者后台：https://docs.qq.com/open/developers/
