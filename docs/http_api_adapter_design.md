# 外部 API 适配器工具 & Skill 环境变量增强 — 设计文档

> 版本: v1.0 | 创建: 2026-05-07 | 状态: 待审核

## 一、背景与目标

### 1.1 业务背景

系统需要对接大量外部 API（EAS 系统、天眼查、企查查、竞品分析 API、CRM 系统、物流接口等）。当前每个 API 对接都是**独立开发**：

- EAS 合同 API → `eas-contract-verify` 技能中硬编码 `requests`/`httpx` 调用
- 百度搜索 API → `baidu-search` 技能中独立 `requests.post()`
- WhatsApp API → `whatsapp-messaging` 技能中独立 `requests.Session`

**问题**：
1. 每对接一个 API 都需要写 Python 脚本，开发成本高
2. HTTP 调用模式重复（认证、超时、重试、错误处理），没有复用
3. API 凭据散落在各技能脚本的 `os.getenv()` 中，管理混乱
4. 不同租户对接的 API 不同，无法灵活扩展

### 1.2 目标

构建一个**通用 HTTP API 调用工具**（`http_api`），让 Skill 通过声明式配置即可调用任意 HTTP API，无需编写 Python 脚本：

1. **通用 HTTP 工具**：支持 GET/POST/PUT/DELETE，支持所有常见传参方式（query、header、body JSON、form、文件上传）
2. **Skill 环境变量机制**：每个 Skill 可声明自己的 `.env` 文件，其中的变量在 Skill 加载时自动注入，用于存放 API 凭据
3. **Skill 内使用 `http_api` 工具**：Skill 的 SKILL.md 中指导大模型如何组装参数调用 `http_api`，凭据用 `${ENV_VAR}` 占位符表示，运行时由系统自动替换

### 1.3 设计原则

- **工具层通用**：`http_api` 工具不关心具体业务，只负责发 HTTP 请求
- **Skill 层定制**：具体 API 的调用方式（URL、参数格式、认证方式）由 SKILL.md 描述
- **凭据隔离**：每个 Skill 的凭据存在自己的 `.env` 文件中，按租户隔离
- **安全优先**：凭据不写入 SKILL.md 正文，不暴露给用户，不记录到日志

---

## 二、`http_api` 工具设计

### 2.1 工具定位

一个通用 HTTP 客户端工具，注册到 ToolRegistry，可被所有 Skill 和主智能体直接调用。

```
用户请求 → Agent → 调用 http_api 工具 → 外部 API
                     ↑
            参数由 Skill 指导组装
            凭据由 ${ENV_VAR} 替换
```

### 2.2 参数定义（Pydantic InputModel）

```python
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from enum import Enum


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


class HttpApiInput(BaseModel):
    """通用 HTTP API 调用参数"""

    method: str = Field(
        default="GET",
        description="HTTP 方法：GET、POST、PUT、DELETE、PATCH"
    )
    url: str = Field(
        ...,
        description="请求 URL，支持包含路径参数的完整 URL"
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="请求头，如 {'Authorization': 'Bearer ${API_TOKEN}', 'Content-Type': 'application/json'}"
    )
    query_params: Optional[Dict[str, str]] = Field(
        default=None,
        description="URL 查询参数，如 {'page': '1', 'size': '10'}"
    )
    body: Optional[Any] = Field(
        default=None,
        description="请求体（JSON 对象或字符串），用于 POST/PUT/PATCH 请求"
    )
    form_data: Optional[Dict[str, str]] = Field(
        default=None,
        description="表单数据（application/x-www-form-urlencoded），与 body 互斥"
    )
    timeout: Optional[int] = Field(
        default=30,
        description="请求超时时间（秒），默认 30 秒"
    )
    follow_redirects: Optional[bool] = Field(
        default=True,
        description="是否跟随重定向，默认 true"
    )
```

### 2.3 工具实现

```python
class HttpApiTool(BaseTool):
    """通用 HTTP API 调用工具"""

    name = "http_api"
    description = (
        "调用外部 HTTP API 接口。支持 GET/POST/PUT/DELETE/PATCH 方法，"
        "支持 JSON body、表单数据、自定义请求头。"
        "URL 和 headers 中的 ${VAR_NAME} 会被替换为环境变量值。"
    )
    usage_guide = """\
## http_api 工具使用指南

这是一个通用 HTTP 客户端工具，可以调用任何 HTTP API。

### 参数说明
- **method**: HTTP 方法（GET/POST/PUT/DELETE/PATCH），默认 GET
- **url**: 完整的请求 URL
- **headers**: 自定义请求头字典（可选）
- **query_params**: URL 查询参数字典（可选）
- **body**: JSON 请求体，可以是对象或数组（可选，POST/PUT 用）
- **form_data**: 表单数据字典（可选，与 body 互斥）
- **timeout**: 超时秒数，默认 30（可选）

### 凭据替换
URL 和 headers 中可以使用 `${ENV_VAR}` 占位符，运行时自动替换为实际值。

### 常见认证模式示例
1. Bearer Token: headers 中设置 `{"Authorization": "Bearer ${API_TOKEN}"}`
2. API Key Header: headers 中设置 `{"X-API-Key": "${API_KEY}"}`
3. Basic Auth: headers 中设置 `{"Authorization": "Basic ${BASIC_AUTH}"}`
4. URL 参数: url 中使用 `https://api.example.com?key=${API_KEY}`
"""
    display_name = "HTTP API 调用"
    category = "network"
    InputModel = HttpApiInput

    def get_display_name(self, tool_args=None):
        if tool_args:
            method = tool_args.get("method", "GET")
            url = tool_args.get("url", "")
            if url:
                # 隐藏含凭据的 URL 参数
                display_url = re.sub(r'\$\{[^}]+\}', '***', url)
                return f"HTTP {method} {display_url}"
        return self.display_name

    async def execute(self, **kwargs) -> Dict[str, Any]:
        method = kwargs.get("method", "GET").upper()
        url = kwargs.get("url", "")
        headers = kwargs.get("headers")
        query_params = kwargs.get("query_params")
        body = kwargs.get("body")
        form_data = kwargs.get("form_data")
        timeout = kwargs.get("timeout", 30)
        follow_redirects = kwargs.get("follow_redirects", True)

        # ${ENV_VAR} 替换
        url = self._substitute_env_vars(url)
        if headers:
            headers = {k: self._substitute_env_vars(v) for k, v in headers.items()}
        if query_params:
            query_params = {k: self._substitute_env_vars(v) for k, v in query_params.items()}

        # 执行请求
        try:
            import httpx
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=follow_redirects,
            ) as client:
                request_kwargs = {}
                if headers:
                    request_kwargs["headers"] = headers
                if query_params:
                    request_kwargs["params"] = query_params
                if form_data:
                    request_kwargs["data"] = form_data
                elif body is not None:
                    request_kwargs["json"] = body

                response = await client.request(method, url, **request_kwargs)

                # 解析响应
                return self._parse_response(response)

        except httpx.TimeoutException:
            return {"success": False, "error": f"请求超时（{timeout}秒）"}
        except httpx.ConnectError as e:
            return {"success": False, "error": f"连接失败: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"请求异常: {sanitize_error_info(str(e))}"}

    @staticmethod
    def _substitute_env_vars(text: str) -> str:
        """替换字符串中的 ${ENV_VAR} 为环境变量值"""
        import os
        import re
        def replace(match):
            var_name = match.group(1)
            value = os.environ.get(var_name)
            if value is None:
                logger.warning(f"环境变量 {var_name} 未设置")
                return match.group(0)  # 保留原样
            return value
        return re.sub(r'\$\{([^}]+)\}', replace, text)

    @staticmethod
    def _parse_response(response) -> Dict[str, Any]:
        """解析 HTTP 响应"""
        status_code = response.status_code
        success = 200 <= status_code < 300

        result = {
            "success": success,
            "status_code": status_code,
        }

        # 尝试解析 JSON
        try:
            result["data"] = response.json()
        except Exception:
            # 非 JSON 响应
            text = response.text
            if len(text) > 5000:
                text = text[:5000] + "...(截断)"
            result["data"] = text

        if not success:
            result["error"] = f"HTTP {status_code}: {str(result['data'])[:500]}"

        return result
```

### 2.4 设计要点

#### 凭据替换（`${ENV_VAR}` 机制）

URL、headers、query_params 中的 `${VAR_NAME}` 占位符在执行前自动替换为环境变量值：

```
调用时传入：
  url: "https://api.example.com/v1/data"
  headers: {"Authorization": "Bearer ${TIANYANCHA_API_KEY}"}

执行时替换：
  url: "https://api.example.com/v1/data"
  headers: {"Authorization": "Bearer sk-xxxxx..."}
```

**替换范围**：仅替换 `url`、`headers`、`query_params` 中的占位符。`body` 和 `form_data` 不做替换（它们是结构化业务数据，不应包含凭据）。

#### 安全处理

1. **日志脱敏**：`get_display_name()` 将 `${...}` 替换为 `***`，工具内部日志也做同样处理
2. **错误信息脱敏**：使用项目已有的 `sanitize_error_info()` 过滤错误响应中的敏感信息
3. **响应截断**：非 JSON 响应超过 5000 字符自动截断，防止上下文窗口溢出

#### 响应格式

```python
# 成功响应
{
    "success": True,
    "status_code": 200,
    "data": {"id": 123, "name": "..."}
}

# 失败响应
{
    "success": False,
    "status_code": 401,
    "error": "HTTP 401: Unauthorized",
    "data": {"message": "Invalid API key"}
}
```

---

## 三、Skill 环境变量增强

### 3.1 现状问题

当前 Skill 的凭据管理有三种方式，都不理想：

| 方式 | 示例 | 问题 |
|------|------|------|
| 全局环境变量 | `BAIDU_API_KEY` 在项目根 `.env` | 所有租户共享，无法按租户配置 |
| 脚本内 `os.getenv()` | `eas_contract_verify.py` 中直接读 | 凭据硬编码在脚本中，修改需改代码 |
| Skill 目录 `.env` | `whatsapp-messaging/.env` | 已有但未系统化支持 |

### 3.2 方案：Skill `.env` 自动加载

**核心机制**：Skill 加载到 Layer 2 时（`SkillLoader.get_content()`），自动加载 Skill 目录下的 `.env` 文件，将其中的变量注入当前进程环境。

#### `.env` 文件位置

```
src/skills/
  trade-customer-1.0.0/
    SKILL.md
    scripts/
    .env              ← Skill 专属环境变量
```

#### `.env` 文件格式

```ini
# EAS 系统 API 配置
EAS_API_URL=https://dc.example.com/api/eas
EAS_API_TOKEN=sk-xxxxx

# 天眼查 API
TIANYANCHA_API_KEY=xxxxxxxx
```

#### 加载时机

```
用户触发 Skill → SkillLoader.get_content()
                    │
                    ├── 解析 SKILL.md frontmatter
                    ├── 读取 SKILL.md body
                    ├── ★ 新增：加载 skill_dir/.env 到 os.environ ★
                    │     （仅加载该 Skill 的 .env，不覆盖已有的同名变量）
                    └── 执行 SkillSubstitutor.substitute()
```

#### 租户级 `.env`

对于多租户场景，每个租户可以有自己的 API 凭据。按优先级加载：

```
优先级从高到低：
1. os.environ 中已有的变量（全局配置 / 系统环境变量）
2. storage/tenants/{tenant_id}/skills/{skill_name}/.env  ← 租户级
3. src/skills/{skill_name}/.env                           ← Skill 默认级
```

**租户级 `.env` 目录结构**：

```
storage/
  tenants/
    tenant_abc123/
      skills/
        tianyancha-lookup/
          .env           ← 租户 abc123 的天眼查凭据
        erp-integration/
          .env           ← 租户 abc123 的 ERP 凭据
    tenant_def456/
      skills/
        tianyancha-lookup/
          .env           ← 租户 def456 的天眼查凭据（不同 key）
```

### 3.3 代码改动

#### 3.3.1 `SkillLoader.get_content()` 增加 `.env` 加载

在 `src/core/skill_loader.py` 的 `get_content()` 方法中新增 `.env` 加载逻辑：

```python
def get_content(self, name: str, substitutions: Optional[Dict] = None) -> Optional[str]:
    """Layer 2: 获取完整 SKILL.md 正文"""
    skill = self._skills.get(name)
    if not skill:
        return None

    # ★ 新增：加载 Skill .env 文件
    self._load_skill_env(skill)

    # ... 后续不变（读取 body、执行 substitutions）

def _load_skill_env(self, skill: Skill) -> None:
    """加载 Skill 目录下的 .env 文件到进程环境变量"""
    env_files = []

    # 优先级 3：Skill 默认级 .env
    skill_env = skill.dir / ".env"
    if skill_env.exists():
        env_files.append(skill_env)

    # 优先级 2：租户级 .env（如果 SaaS 模式且存在 tenant_id）
    tenant_id = os.environ.get("CURRENT_TENANT_ID")
    if tenant_id:
        tenant_env = Path(f"storage/tenants/{tenant_id}/skills/{skill.name}/.env")
        if tenant_env.exists():
            env_files.append(tenant_env)  # 后加载的优先级更高

    for env_file in env_files:
        try:
            from dotenv import load_dotenv
            # override=False: 不覆盖已有的环境变量（保证系统级变量优先）
            load_dotenv(env_file, override=False)
            logger.debug(f"Loaded skill env from {env_file}")
        except Exception as e:
            logger.warning(f"Failed to load skill env from {env_file}: {e}")
```

#### 3.3.2 SKILL.md frontmatter 新增 `env` 声明字段

在 SKILL.md 的 YAML frontmatter 中新增 `env` 字段，声明该 Skill 需要的环境变量：

```yaml
---
name: tianyancha-lookup
description: 天眼查企业信息查询
version: 1.0.0
env:                           # ★ 新增：声明需要的环境变量
  - TIANYANCHA_API_KEY         # 必需变量（无默认值）
  - name: EAS_API_URL          # 带默认值的变量
    default: "https://dc.example.com/api"
  - name: TIMEOUT
    default: "30"
---
```

**`env` 字段的作用**：
1. **文档化**：告诉开发者和运维人员这个 Skill 需要哪些环境变量
2. **启动检查**：Skill 加载时检查必需变量是否已设置，缺少则记录 warning
3. **默认值注入**：对有默认值的变量，如果环境中不存在，自动注入

**Skill 数据模型新增字段**：

```python
@dataclass
class SkillEnvVar:
    """Skill 环境变量声明"""
    name: str
    default: Optional[str] = None

@dataclass
class Skill:
    # ... 现有字段 ...
    env: List[SkillEnvVar] = field(default_factory=list)  # 新增
```

### 3.4 环境变量生命周期

```
┌─────────────────────────────────────────────────┐
│                   进程启动                        │
│  全局 .env → os.environ（全局 API key 等）       │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│             Skill 被触发（Layer 2）               │
│  1. 加载 skill_dir/.env（override=False）         │
│  2. 加载 tenant .env（override=False）            │
│  3. 注入 env 默认值（仅未设置的变量）             │
│  4. 检查必需变量是否齐全                          │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           Skill 执行期间                          │
│  - SKILL.md body 中 ${VAR} 由 SkillSubstitutor  │
│    替换为 os.environ 值                           │
│  - http_api 工具中 ${VAR} 由工具自行替换          │
│  - scripts 中 os.getenv() 直接读取               │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────┐
│           Skill 完成（skill_complete）            │
│  ★ 可选：清理 Skill 注入的临时环境变量            │
│  （避免不同 Skill 的变量互相污染）                │
└─────────────────────────────────────────────────┘
```

**关于环境变量污染**：由于 `override=False`，后加载的 Skill `.env` 不会覆盖先加载的。但如果 Skill A 定义了 `API_KEY=xxx`，后续 Skill B 的脚本也可能读到这个值。为避免这种情况：

- **短期方案**：命名约定，Skill 环境变量以 Skill 名为前缀（如 `TIANYANCHA_API_KEY`）
- **长期方案**：在 `skill_complete` 时清理该 Skill 注入的变量（需记录注入了哪些 key）

---

## 四、使用示例：以天眼查查询为例

### 4.1 Skill 目录结构

```
src/skills/
  tianyancha-lookup-1.0.0/
    SKILL.md                    # 技能定义
    .env                        # API 凭据（不提交到 Git）
    .env.example                # 凭据模板（提交到 Git）
```

### 4.2 `.env.example`（提交到 Git 的模板）

```ini
# 天眼查 API Token
TIANYANCHA_API_KEY=your_api_key_here
```

### 4.3 `.env`（实际凭据，不提交）

```ini
TIANYANCHA_API_KEY=a1b2c3d4e5f6...
```

### 4.4 SKILL.md

```markdown
---
name: tianyancha-lookup
description: >
  天眼查企业信息查询。查询企业的工商信息、股东结构、经营状况等。
  使用场景：企业资质验证、合作伙伴背景调查、竞品公司信息收集。
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
    - id: {企业 ID（从工商信息中获取）}

## 结果说明

API 返回 JSON 数据，包含：
- companyName: 企业名称
- legalPersonName: 法定代表人
- regCapital: 注册资本
- status: 经营状态
- ...

将关键字段整理后以表格或结构化格式呈现给用户。
```

### 4.5 对话流程

```
用户：帮我查一下字节跳动公司的工商信息

智能体：[识别到"查公司信息"意图 → 触发 tianyancha-lookup 技能]
       [Skill 加载 → 自动加载 .env → TIANYANCHA_API_KEY 注入环境变量]
       [调用 http_api 工具]

工具调用：
  http_api(
    method="GET",
    url="https://open.api.tianyancha.com/services/v4/open/company/baseinfo",
    headers={"Authorization": "Bearer ${TIANYANCHA_API_KEY}"},
    query_params={"name": "字节跳动"}
  )

       [http_api 执行 → ${TIANYANCHA_API_KEY} 替换为实际值 → 发送请求]
       [返回企业信息 JSON]

智能体：以下是字节跳动公司的工商信息：

| 项目 | 信息 |
|------|------|
| 企业名称 | 北京字节跳动科技有限公司 |
| 法定代表人 | 张利东 |
| 注册资本 | 3000 万美元 |
| 经营状态 | 在营 |
| ... | ... |
```

---

## 五、与现有模式对比

### 5.1 改造前后对比

| 维度 | 改造前（当前模式） | 改造后 |
|------|-------------------|--------|
| **新增 API 对接** | 写 Python 脚本（100-300 行） | 写 SKILL.md 指南（50-100 行）+ .env |
| **凭据管理** | 散落在各脚本 `os.getenv()` | Skill 目录 `.env` + 租户级 `.env` |
| **HTTP 调用** | 每个脚本独立实现 | 统一 `http_api` 工具 |
| **错误处理** | 每个脚本各自处理 | 工具统一处理（超时、连接错误、脱敏） |
| **租户适配** | 改代码或改全局配置 | 替换租户级 `.env` 文件 |
| **可维护性** | 需懂 Python | 只需懂 API 文档 + Markdown |

### 5.2 适用场景

| 场景 | 使用方式 |
|------|----------|
| **简单 REST API 调用**（查天气、查企业、发通知） | SKILL.md + http_api 工具，无需写脚本 |
| **复杂多步 API 流程**（合同归档、ERP 同步） | SKILL.md 指导多轮 http_api 调用，或保留现有脚本模式 |
| **需要数据转换的 API**（Excel → API → 报告） | SKILL.md + http_api + 现有数据处理工具 |
| **文件上传 API** | http_api 工具后续支持（见第六节） |

### 5.3 不替代的场景

`http_api` 工具**不替代**以下场景中的 Python 脚本：

1. **需要复杂数据处理**（如 OCR 解析 → 数据提取 → 格式转换）
2. **需要多步原子事务**（如查询 → 比对 → 上传 → 审批，任一步失败需回滚）
3. **需要本地文件操作**（如读写 Excel、PDF 合并、文件系统操作）
4. **需要长时间运行**（如批量数据处理、爬虫任务）

这些场景继续使用 `skill_execute` 执行 Python 脚本，脚本内也可通过 `os.getenv()` 读取 Skill `.env` 中注入的变量。

---

## 六、功能扩展预留

以下功能在 Phase 1 不实现，但设计时预留扩展空间：

### 6.1 文件上传支持

未来在 `HttpApiInput` 中新增 `files` 参数：

```python
files: Optional[Dict[str, str]] = Field(
    default=None,
    description="文件上传，{'字段名': '文件路径'}，自动以 multipart/form-data 发送"
)
```

### 6.2 响应提取（JMESPath）

对于返回大量 JSON 的 API，支持 JMESPath 表达式提取关键字段：

```python
extract: Optional[str] = Field(
    default=None,
    description="JMESPath 表达式，从响应 JSON 中提取指定字段，如 'data.items[*].name'"
)
```

### 6.3 请求模板

在 Skill 的 `.env` 或配置文件中预定义请求模板，Skill 只需传入参数：

```ini
# .env 中的模板定义（未来扩展）
API_TEMPLATE_SEARCH={"method":"GET","url":"https://api.example.com/search","headers":{"Authorization":"Bearer ${API_KEY}"}}
```

### 6.4 响应缓存

对 GET 请求支持短期缓存（如 TTL 5 分钟），避免重复调用相同 API：

```python
cache_ttl: Optional[int] = Field(
    default=None,
    description="缓存时间（秒），仅 GET 请求生效。0 表示不缓存"
)
```

### 6.5 OAuth 2.0 支持

支持 OAuth 2.0 client_credentials 模式，自动获取和刷新 access_token：

```ini
# .env 中声明 OAuth 配置（未来扩展）
OAUTH_TOKEN_URL=https://auth.example.com/oauth/token
OAUTH_CLIENT_ID=xxx
OAUTH_CLIENT_SECRET=xxx
```

---

## 七、实施计划

### Phase 1：核心工具 + Skill .env 增强（3-5 天）

| 任务 | 说明 | 涉及文件 |
|------|------|----------|
| 1.1 实现 `http_api` 工具 | 通用 HTTP 客户端，支持 GET/POST/PUT/DELETE/PATCH | `src/tools/network/http_api.py`（新增） |
| 1.2 注册 `http_api` 工具 | 在 Agent 启动时注册到 ToolRegistry | `src/core/agent.py`（修改 `_register_builtin_tools`） |
| 1.3 Skill `.env` 自动加载 | SkillLoader.get_content() 中加载 `.env` | `src/core/skill_loader.py`（修改） |
| 1.4 SKILL.md `env` 字段解析 | 解析 frontmatter 中的 env 声明 | `src/core/skill_loader.py`（修改） |
| 1.5 租户级 `.env` 支持 | 按 tenant_id 加载对应目录的 `.env` | `src/core/skill_loader.py`（修改） |
| 1.6 Skill `allowed-tools` 中声明 `http_api` | 各 Skill 的 SKILL.md 中将 `http_api` 加入 `allowed-tools` | 各 SKILL.md |
| 1.7 测试 | 单元测试 + 集成测试 | `tests/unit/tools/test_http_api.py`（新增） |

### Phase 2：文档

| 任务 | 说明 |
|------|------|
| 2.1 编写 Skill 开发者指南 | 教用户如何创建使用 http_api 的 Skill |

### Phase 3：高级功能（按需）

| 任务 | 说明 |
|------|------|
| 3.1 文件上传支持 | `files` 参数 |
| 3.2 响应提取 | JMESPath 表达式 |
| 3.3 响应缓存 | GET 请求短期缓存 |
| 3.4 OAuth 2.0 支持 | 自动 token 管理 |

---

## 八、文件变更清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `src/tools/network/__init__.py` | 网络工具包初始化 |
| `src/tools/network/http_api.py` | 通用 HTTP API 工具实现 |
| `tests/unit/tools/test_http_api.py` | 工具单元测试 |

### 修改文件

| 文件 | Phase | 改动范围 |
|------|-------|----------|
| `src/core/agent.py` | 1.2 | `_register_builtin_tools()` 中注册 `http_api` |
| `src/core/skill_loader.py` | 1.3-1.5 | `get_content()` 增加 `.env` 加载；新增 `env` 字段解析 |

---

## 九、安全注意事项

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| **SSRF 攻击** | 用户可能通过 Skill 诱导 Agent 请求内网地址 | 可配置 URL 白名单/黑名单，默认禁止内网地址 |
| **凭据泄露** | `.env` 文件可能被意外提交到 Git | `.gitignore` 中添加 `src/skills/*/.env`；只提交 `.env.example` |
| **日志泄露** | 请求 URL/响应中可能包含 token | `get_display_name()` 脱敏；响应中大段文本截断 |
| **环境变量污染** | 多 Skill 共享 `os.environ`，变量可能互相覆盖 | `override=False` 防止覆盖；命名约定加前缀 |
| **请求频率** | Skill 可能触发大量 API 调用导致费用超支 | 工具层面可选配请求频率限制 |

### SSRF 防护（可选实现）

```python
# 可选：在 http_api execute() 开头检查 URL
BLOCKED_HOSTS = ["127.0.0.1", "localhost", "0.0.0.0", "::1", "169.254.169.254"]

def _is_safe_url(self, url: str) -> bool:
    """检查 URL 是否安全（防 SSRF）"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname in BLOCKED_HOSTS:
        return False
    # 可扩展：检查内网 IP 段
    return True
```

---

## 十、待讨论事项

1. **`.env` 文件是否加密**：当前 `.env` 是明文存储。对于高安全要求的场景，是否需要对 `.env` 内容加密（类似现有的 `EncryptionManager`）？

2. **环境变量清理时机**：Skill 执行完毕后是否需要清理注入的环境变量？清理可能影响并发请求中正在使用同一 Skill 的其他会话。

3. **`http_api` 工具是否需要权限控制**：是否需要像 `browser`、`email` 工具一样需要用户授权才能调用？还是作为通用基础设施工具默认可用？

4. **租户级 `.env` 的管理界面**：是否需要在前端提供租户管理员配置 Skill 环境变量的界面？还是纯文件管理？

5. **请求日志审计**：是否需要记录所有 `http_api` 调用的日志（URL、方法、响应状态）用于审计？记录级别如何平衡安全和调试需要？

6. **`body` 中的 `${ENV_VAR}` 是否需要支持**：当前设计只在 `url`/`headers`/`query_params` 中替换，如果 API 需要在 JSON body 中传凭据（如 `{"apiKey": "${KEY}"}`），是否需要扩展？
