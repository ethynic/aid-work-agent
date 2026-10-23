# 06 · Python 开源对接项目调研

> 调研日期：2026-10-20
> 检索渠道：GitHub Search API、PyPI、Web 搜索
> 结论先行：**生态很薄，没有可以直接"拿来就用"的生产级 Python SDK。推荐"借鉴 1 个 + 自研薄封装"。**

---

## 一、检索结论速览

| 项目 | 语言 | Stars | 最近更新 | 维护度 | 可否直接用 |
| --- | --- | --- | --- | --- | --- |
| [easy-wx/qq-doc](https://github.com/easy-wx/qq-doc) | Python | 27 | 2024-12 | 低（8 commits，1 贡献者） | **可借鉴，不建议直接依赖** |
| [chinahtl/tencent-doc-sdk](https://github.com/chinahtl/tencent-doc-sdk) | **Go** | — | 2025-05 | 中 | 跨语言参考设计 |
| [IsSmallPigPig/txwd_record](https://github.com/IsSmallPigPig/txwd_record) | Python | 1 | 2026-10 | 个人项目 | ❌ 非官方抓取，生产禁用 |
| [StevenArai/qqsheet](https://github.com/StevenArai/qqsheet) | Python | 0 | 2026-09 | 个人项目 | ❌ 非官方 opendoc 接口 |
| [DerekNazh/tencent-docs-skill](https://github.com/DerekNazh/tencent-docs-skill) | Python | 0 | 2026-07 | 个人项目 | 仅作调用样例参考 |

**GitHub 上 `tencent docs api` 关键词、Python 语言的仓库总共只有 4 个**，且没有一个超过 30 stars。
**PyPI 上没有官方 SDK**，也没有被广泛使用的第三方包。

> 这与腾讯文档开放平台本身较新、且强制 OAuth 用户授权（不适合做"无状态公共 SDK"）有关。

---

## 二、重点项目评估

### 2.1 ⭐ easy-wx/qq-doc（`pip install qq-doc`）— 唯一值得看的 Python 封装

**基本信息**

| 项 | 值 |
| --- | --- |
| 仓库 | https://github.com/easy-wx/qq-doc |
| PyPI | https://pypi.org/project/qq-doc/ |
| 最新版本 | `0.1.0.dev3`（**dev 预发布版**） |
| 发布时间 | 2024-12-30 |
| License | MIT |
| Python | `>=3.8` |
| 依赖 | `requests`（`requires_dist` 未声明，实际用了 requests） |
| Stars / Forks / 贡献者 | 27 / 5 / **1** |
| Commits | 8 |
| 包体积 | wheel 8.5 KB（非常小） |

**代码结构**

```
qq_doc/
├── __init__.py       (273 B)
├── base.py           (2.7 KB)   基类：BASE_URL、token cache
├── auth_api.py       (2.8 KB)   OAuth：authorize / token / refresh / userinfo
└── file_mgr_api.py   (9.8 KB)   文件管理：文件夹、权限、导入导出
```

**已实现能力**

- ✅ OAuth 授权 URL 构造（`authorize`）
- ✅ `code` → `access_token` + `refresh_token`（`get_user_access_token`）
- ✅ `refresh_token` 刷新（`refresh_access_token`）
- ✅ 获取用户信息（`get_user_info`）
- ✅ token 本地 cache（`cache.json`）
- ✅ 文件夹/文件管理：`list_folder_contents`、`get_folder_metadata`、`get_folder_by_name`、
  `create_folder`、`create_folder_if_not_exist`、`delete_folder`
- ✅ 权限：`get_file_access`、`get_file_permission`、`set_file_permission`、`add_collaborators`、`get_collaborators`
- ✅ 导入：`create_import_info`、`async_import_document`、`get_import_progress`、复合封装 `upload_file`

**关键源码片段（授权部分，可直接抄）**

```python
# qq_doc/auth_api.py
def get_user_access_token(self, code, redirect_uri):
    url = f"{self.BASE_URL}/oauth/v2/token"
    params = {
        "client_id": self.client_id,
        "client_secret": self.client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code": code
    }
    response = requests.get(url, params=params, headers={"Accept": "application/json"})
    ...
```

**缺点 / 风险**

| 问题 | 影响 |
| --- | --- |
| 版本是 `0.1.0.dev3`，从未发过正式 release | 生产依赖风险高 |
| 单文件 cache（`cache.json`） | **完全不支持多租户**，只适合单人脚本 |
| 未覆盖 V3 文档内容接口（`GET /openapi/doc/v3/{fileId}`） | 你的核心需求（读正文）它没有 |
| 未覆盖 sheet / smartsheet 内容接口 | 需要自己补 |
| `authorize()` 未带官方要求的 `new_login=1` | 按官方最新要求应补上 |
| 无重试、无限流、无 token 过期自动刷新 | 生产环境必须自己加 |
| 8 commits、1 贡献者、近 2 年无更新 | 腾讯文档 API 若有变更不会同步 |

**判定**：**不直接 `pip install` 依赖，但可以整段借鉴 `auth_api.py`（约 60 行）作为你的 OAuth 模块骨架。**

---

### 2.2 chinahtl/tencent-doc-sdk（Go）— 跨语言设计参考

- 语言：Go（不能直接用，但设计值得抄）
- 特性：灵活的认证流程、文档列表/搜索、文档导出、**自动 Token 刷新**、可配置 HTTP Client（支持自定义 Transport）
- 最近更新 2025-05，比 Python 那个活跃

**值得抄的设计**

1. `config.WithClientID(...)` / `WithClientSecret(...)` 的 Option 模式
2. **自动 Token 刷新**（Python 项目缺这个，而这恰是多租户最痛的点）
3. 可注入自定义 HTTP Transport → 便于你接入统一的超时/重试/链路追踪中间件

---

### 2.3 非官方"爬虫式"项目 — 不建议碰

- `IsSmallPigPig/txwd_record`：解析腾讯文档前端渲染数据，把表格转成 DataFrame，**不走 Open API**
- `StevenArai/qqsheet`：走非官方 `opendoc` 接口导出公开表格

**为什么生产禁用**

1. 依赖腾讯文档前端内部接口/渲染结构，随时失效
2. 无法承载租户授权语义（绕过了 OAuth，合规性存疑）
3. 违反开放平台 ToS，SaaS 商用有法律风险
4. 无频控保障，容易触发风控

> 只能当作"理解腾讯文档数据结构"的参考资料。

---

## 三、给你的落地建议

### 3.1 推荐方案：**自研薄封装（≈300 行）+ 借鉴 qq-doc 的 OAuth 部分**

理由：

1. 你的核心需求（多租户 + 读 V3 文档内容 + 自动刷新）**现成库都没覆盖**
2. 官方 API 本身很薄（HTTP + 三元组 Header），自己封装成本极低
3. 避免依赖一个 `0.1.0.dev3`、单人维护、近 2 年不更新的包

### 3.2 建议的模块划分

```
your_project/integrations/tencent_docs/
├── client.py        # HTTP 层：统一 timeout / 重试 / 429 退避 / 日志
├── oauth.py         # 授权 URL、code 换 token、refresh（借鉴 qq-doc/auth_api.py）
├── token_store.py   # ★ 多租户 token 持久化（DB），带过期预刷新 + 分布式锁
├── drive.py         # 文件/文件夹/权限/导入导出（可借鉴 qq-doc/file_mgr_api.py）
├── doc.py           # V3 文档内容读取 + 属性树递归解析（自研，核心）
├── smartsheet.py    # 智能表记录/字段读写（自研）
├── parser.py        # document JSON → Markdown / 纯文本
└── errors.py        # 错误码映射（见 02 文档）
```

### 3.3 `token_store.py` 必须做对的几件事（qq-doc 完全没做）

```python
# 1) 过期预判：不要用固定 30 天，用 expires_in
expires_at = now + timedelta(seconds=resp["expires_in"])
# 提前 24h 刷新
if expires_at - now < timedelta(hours=24):
    do_refresh()

# 2) 并发刷新加锁（多租户下同一租户可能被并发触发）
#    Redis: SETNX tenant:{id}:refresh_lock  TTL 30s
#    否则会出现 refresh_token 被并发使用导致失效

# 3) 刷新失败分级处理
#    37017 / 37019 → 标记 tenant 授权失效，推送重新授权，不再重试
#    5xx / 网络异常 → 退避重试 ≤3 次

# 4) Refresh Token 1 年硬过期
refresh_expires_at = now + timedelta(days=365)
#    提前 15 天在租户后台提醒"即将需要重新授权"

# 5) 敏感字段加密存储
#    access_token / refresh_token 用 KMS 或 Fernet 加密后落库，别明文
```

### 3.4 顺手要做的两件事

- **频控**：对每个 `open_id` 做令牌桶（300/分钟）、每个 `file_id`（150/分钟），在网关层就拦截，别等腾讯返回 429
- **额度监控**：定时调 `GET /openapi/drive/v2/util/resource-use`，监控 2 万次免费额度消耗，快用完时告警

---

## 四、如果你想直接用现成库 —— 最小改造路径

若坚持要基于 `qq-doc` 起步，至少做这些改造：

```bash
# 不建议 pip install（dev 版），建议 vendor 进代码库
git clone https://github.com/easy-wx/qq-doc.git
cp -r qq-doc/qq_doc your_project/integrations/tencent_docs/_vendor/
```

必改项清单：

1. `authorize()` 补 `new_login=1`（官方 2025 版要求）
2. 把 `cache.json` token 存储换成数据库 / Redis（多租户）
3. 增加 `expires_at` 判断与自动刷新（现在是"永不过期"假设）
4. 增加 `GET /openapi/doc/v3/{fileId}` 内容读取方法（补齐核心缺口）
5. 增加 429 退避重试 + 401 自动刷新重试
6. 增加分布式锁防止并发刷新

---

## 五、一句话总结

> **抄 `easy-wx/qq-doc` 的 60 行 OAuth 代码，其余自己写。**
> 生态里没有第二个值得依赖的东西，而官方 API 本身足够简单，自研成本低于适配成本。
