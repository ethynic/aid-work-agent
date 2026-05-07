# Skill 开发者指南 — 使用 http_api 工具对接外部 API

> 适用于需要调用外部 HTTP API 的 Skill 开发场景。阅读本文档后，你可以通过编写 SKILL.md 和 .env 文件对接任意 REST API，无需编写 Python 脚本。

---

## 概述

`http_api` 是系统内置的通用 HTTP 客户端工具，支持所有常见 HTTP 方法和认证模式。Skill 开发者只需：

1. 编写 **SKILL.md** — 描述 API 调用方式，指导大模型如何组装参数
2. 配置 **.env** — 存放 API 凭据，运行时自动加载

```
SKILL.md（调用指南）  +  .env（凭据）  →  大模型自动组装参数  →  http_api 工具执行  →  返回结果
```

---

## 快速开始

### 第一步：创建 Skill 目录

```
src/skills/
  my-api-skill-1.0.0/       ← 目录名格式：{名称}-{版本号}
    SKILL.md                 ← 技能定义（必需）
    .env                     ← API 凭据（不提交到 Git）
    .env.example             ← 凭据模板（提交到 Git）
```

### 第二步：编写 .env 文件

`.env` 存放 API 凭据，**不要提交到 Git**。

```ini
# .env（实际凭据）
MY_API_KEY=sk-xxxxxxxxxxxx
MY_API_URL=https://api.example.com
```

`.env.example` 是模板，提交到 Git 供其他开发者参考：

```ini
# .env.example（模板）
MY_API_KEY=your_api_key_here
MY_API_URL=https://api.example.com
```

### 第三步：编写 SKILL.md

以下是完整示例，展示 SKILL.md 的所有关键部分：

```markdown
---
name: my-api-skill
description: 调用示例 API 查询数据
version: 1.0.0
env:
  - MY_API_KEY
  - name: MY_API_URL
    default: "https://api.example.com"
---

# 示例 API 调用

## 何时使用

- 需要查询 XX 数据时
- 需要执行 XX 操作时

## 使用方式

本技能使用 `http_api` 工具调用外部 API。

### 查询数据

调用 http_api 工具，参数如下：

- method: GET
- url: ${MY_API_URL}/v1/data
- headers:
    - Authorization: Bearer ${MY_API_KEY}
- query_params:
    - keyword: {用户输入的搜索关键词}
    - page: 1
    - size: 10

### 创建记录

调用 http_api 工具，参数如下：

- method: POST
- url: ${MY_API_URL}/v1/records
- headers:
    - Authorization: Bearer ${MY_API_KEY}
    - Content-Type: application/json
- body:
    - name: {记录名称}
    - type: {记录类型}

## 结果说明

API 返回 JSON 数据，包含：
- id: 记录 ID
- name: 记录名称
- status: 状态

将关键字段整理后以表格或结构化格式呈现给用户。
```

完成。无需编写任何 Python 代码。

---

## SKILL.md 详解

### Frontmatter 字段

```yaml
---
name: my-skill              # 必需：技能名称（唯一标识）
description: 一句话描述       # 必需：技能描述，用于意图匹配
version: 1.0.0              # 可选：版本号

env:                         # 可选：声明需要的环境变量
  - REQUIRED_VAR             # 必需变量（无默认值，缺失时记录 warning）
  - name: OPTIONAL_VAR       # 带默认值的变量（缺失时自动注入默认值）
    default: "default_value"
---
```

#### `env` 字段说明

| 声明方式 | 含义 | 行为 |
|----------|------|------|
| `- VAR_NAME` | 必需变量 | Skill 加载时检查是否存在，缺失则记录 warning |
| `- name: VAR` + `default: "val"` | 可选变量 | 环境中不存在时自动注入默认值 |

`env` 声明的作用：
1. **文档化** — 告诉开发者这个 Skill 需要哪些变量
2. **默认值注入** — 有默认值的变量在缺失时自动设置
3. **启动检查** — 必需变量缺失时记录 warning 日志

### Body 结构建议

```markdown
# 技能名称

## 何时使用
（描述触发场景，帮助大模型判断何时调用）

## 使用方式
（描述如何调用 http_api，这是核心内容）

### 功能 A
调用 http_api 工具，参数如下：
- method: GET
- url: ...
- headers: ...
- query_params: ...

### 功能 B
...

## 结果说明
（描述 API 返回数据的结构和展示方式）
```

---

## http_api 工具参数参考

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `method` | string | 否 | HTTP 方法，默认 `GET`。可选：GET、POST、PUT、DELETE、PATCH |
| `url` | string | **是** | 完整的请求 URL。支持 `${ENV_VAR}` 占位符 |
| `headers` | dict | 否 | 请求头字典。支持 `${ENV_VAR}` 占位符 |
| `query_params` | dict | 否 | URL 查询参数字典。支持 `${ENV_VAR}` 占位符 |
| `body` | any | 否 | JSON 请求体（POST/PUT/PATCH 用）。与 files 互斥 |
| `form_data` | dict | 否 | 表单数据（application/x-www-form-urlencoded）。与 body 互斥 |
| `files` | dict | 否 | 文件上传，`{'字段名': '文件路径'}`，自动以 multipart/form-data 发送。与 body 互斥 |
| `timeout` | int | 否 | 超时秒数，默认 30 |
| `follow_redirects` | bool | 否 | 是否跟随重定向，默认 true |

### `${ENV_VAR}` 占位符

`url`、`headers`、`query_params` 中的 `${VAR_NAME}` 会在运行时自动替换为环境变量值。**不要在 body 中使用占位符**（body 是业务数据，不应包含凭据）。

替换示例：
```
传入: {"Authorization": "Bearer ${MY_API_KEY}"}
替换: {"Authorization": "Bearer sk-xxxxx..."}
```

---

## 常见认证模式

### Bearer Token

```yaml
# .env
MY_API_TOKEN=eyJhbGciOiJIUzI1NiJ9...
```

SKILL.md 中：
```
- headers:
    - Authorization: Bearer ${MY_API_TOKEN}
```

### API Key Header

```yaml
# .env
MY_API_KEY=sk-xxxxxxxx
```

SKILL.md 中：
```
- headers:
    - X-API-Key: ${MY_API_KEY}
```

### URL 参数传递 Key

```
- url: https://api.example.com/v1/search?key=${MY_API_KEY}
```

### Basic Auth

```yaml
# .env
MY_BASIC_AUTH=base64encoded_user_pass
```

SKILL.md 中：
```
- headers:
    - Authorization: Basic ${MY_BASIC_AUTH}
```

---

## 文件上传

使用 `files` 参数上传文件，自动以 `multipart/form-data` 编码发送。

### 单文件上传

```markdown
### 上传文件

调用 http_api 工具，参数如下：

- method: POST
- url: https://api.example.com/v1/files/upload
- headers:
    - Authorization: Bearer ${MY_API_TOKEN}
- files:
    - file: {用户上传的文件路径}
```

### 带附加字段的文件上传

`files` 可以和 `form_data` 同时使用，在 multipart 请求中同时传递文件和表单字段：

```markdown
调用 http_api 工具，参数如下：

- method: POST
- url: https://api.example.com/v1/files/upload
- headers:
    - Authorization: Bearer ${MY_API_TOKEN}
- files:
    - file: {文件路径}
- form_data:
    - category: report
    - description: {文件描述}
```

### 文件路径说明

- 文件路径来自用户上传的文件，通常为绝对路径（如 `C:\repos\...\storage\uploads\xxx\file.pdf`）
- 也支持相对路径（相对于项目根目录）
- 单个文件大小限制 20MB
- **`files` 和 `body` 不能同时使用**

---

## 环境变量加载机制

Skill 被触发时（Layer 2 加载），系统按以下优先级加载环境变量：

```
优先级从高到低：
1. os.environ 中已有的变量（全局配置 / 系统环境变量）
2. storage/tenants/{tenant_id}/skills/{skill_name}/.env  ← 租户级
3. src/skills/{skill_name}/.env                           ← Skill 默认级
4. SKILL.md env 字段中的 default 值                       ← 最低优先级
```

**多租户场景**：不同租户可以有不同的 API 凭据。在租户目录下放置 `.env` 即可覆盖 Skill 默认值：

```
storage/tenants/
  tenant_abc/
    skills/
      my-api-skill/
        .env           ← 租户 abc 的凭据（优先级高于 Skill 默认级）
```

---

## 完整示例

### 示例 1：天眼查企业查询

**目录结构**：
```
src/skills/
  tianyancha-lookup-1.0.0/
    SKILL.md
    .env.example
    .env              ← 不提交到 Git
```

**.env.example**：
```ini
TIANYANCHA_API_KEY=your_api_key_here
```

**SKILL.md**：
```markdown
---
name: tianyancha-lookup
description: 天眼查企业信息查询，包括工商信息、股东结构、经营状况等
version: 1.0.0
env:
  - TIANYANCHA_API_KEY
---

# 天眼查企业查询

## 何时使用

- 查询某公司的工商注册信息
- 查询企业的股东、法人、注册资本
- 企业背景调查

## 使用方式

本技能使用 `http_api` 工具调用天眼查 API。

### 查询企业工商信息

调用 http_api 工具，参数如下：

- method: GET
- url: https://open.api.tianyancha.com/services/v4/open/company/baseinfo
- headers:
    - Authorization: Bearer ${TIANYANCHA_API_KEY}
- query_params:
    - name: {企业名称}

### 查询股东信息

调用 http_api 工具，参数如下：

- method: GET
- url: https://open.api.tianyancha.com/services/v4/open/company/holder
- headers:
    - Authorization: Bearer ${TIANYANCHA_API_KEY}
- query_params:
    - id: {企业 ID（从工商信息查询结果中获取）}

## 结果说明

API 返回 JSON 数据，包含：
- companyName: 企业名称
- legalPersonName: 法定代表人
- regCapital: 注册资本
- status: 经营状态

将关键字段整理后以表格形式呈现给用户。
```

### 示例 2：企业内部 ERP API（POST 请求）

**SKILL.md**：
```markdown
---
name: erp-order-query
description: 查询 ERP 系统中的订单信息
version: 1.0.0
env:
  - ERP_API_TOKEN
  - name: ERP_API_URL
    default: "https://erp.company.com/api"
---

# ERP 订单查询

## 何时使用

- 查询订单状态和详情
- 查询客户历史订单

## 使用方式

### 查询订单

调用 http_api 工具，参数如下：

- method: POST
- url: ${ERP_API_URL}/orders/query
- headers:
    - Authorization: Bearer ${ERP_API_TOKEN}
    - Content-Type: application/json
- body:
    - orderNo: {订单号}
    - includeDetails: true

### 按客户查询订单列表

调用 http_api 工具，参数如下：

- method: POST
- url: ${ERP_API_URL}/orders/list
- headers:
    - Authorization: Bearer ${ERP_API_TOKEN}
    - Content-Type: application/json
- body:
    - customerName: {客户名称}
    - page: 1
    - pageSize: 20

## 结果说明

返回订单 JSON 数据，以表格展示关键字段（订单号、客户、金额、状态、日期）。
```

---

## 安全注意事项

1. **凭据不写进 SKILL.md 正文** — 使用 `${ENV_VAR}` 占位符，实际值放在 `.env` 文件中
2. **`.env` 不提交到 Git** — 在 `.gitignore` 中添加 `src/skills/*/.env`
3. **环境变量命名加前缀** — 如 `TIANYANCHA_API_KEY`、`ERP_API_TOKEN`，避免不同 Skill 之间变量名冲突
4. **日志自动脱敏** — 系统在显示工具调用时自动将 `${...}` 替换为 `***`，凭据不会出现在日志中

---

## 何时需要写 Python 脚本

以下场景 `http_api` 工具不适用，仍需编写 Python 脚本：

| 场景 | 原因 |
|------|------|
| 需要复杂数据转换（OCR → 提取 → 格式化） | 超出大模型参数组装能力 |
| 需要多步原子事务（任一步失败需回滚） | http_api 是单次请求 |
| 需要本地文件操作（读写 Excel、PDF 处理） | http_api 无法操作文件系统 |
| 需要长时间运行（批量数据处理） | 单次请求有超时限制 |

这些场景使用 `skill_execute` 执行 Python 脚本，脚本中可通过 `os.getenv()` 读取 `.env` 中注入的环境变量。
