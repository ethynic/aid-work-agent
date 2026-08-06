# 协会信息收集客户端 — 技术设计文档

> **权威设计**。本文档是客户端、命令行工具、服务端三端开发的唯一依据。智能体开发时必须严格按照本文档的接口契约、数据结构、协议格式实现，不得猜测。
>
> **交付目标**：下周一（2026-08-10）交付给客户。客户安装后即可在单台 Windows 电脑上完成协会信息收集全流程。

---

## 0. 一句话定位

将现有的「协会信息收集」能力从服务端内部工具，改造成一个 **可交付给客户电脑独立运行的产品**：
- **Electron 客户端**：用户输入协会名、查看进度/日志/结果/积分消耗的 GUI 入口
- **本地命令行工具（PyInstaller exe）**：承载完整 5 步业务逻辑（LLM联网搜索基础信息→采集官网→微信搜一搜搜领导姓名→微信RPA取证手机号），LLM/OCR 能力通过 HTTP 调用服务端代理（服务端计费）
- **服务端（aid-work-agent）**：LLM 代理网关 + 积分计费 + 激活鉴权 + 日志接收

客户端与客户租户绑定，消耗的积分按 **5 倍系数** 计入该租户的通用积分池。

---

## 1. 核心架构

### 1.1 三端职责划分

```
┌─────────────────────────────────────────────────────────────────┐
│                    客户端电脑（Windows）                          │
│                                                                  │
│  ┌──────────────────┐     child_process      ┌────────────────┐ │
│  │  Electron 客户端  │ ──────────────────────▶│ 本地命令行工具  │ │
│  │  (GUI 入口/展示)  │◀──── stdout/JSON ──────│ (业务逻辑全部)  │ │
│  │                  │                        │ (PyInstaller)  │ │
│  │  - 输入协会名     │                        │                │ │
│  │  - 查看进度/日志  │                        │ 5步流水线：     │ │
│  │  - 下载结果Excel  │                        │ 1.LLM搜基础信息 │ │
│  │  - 查看积分余额   │                        │ 2.采集官网      │ │
│  └────────┬─────────┘                        │ 3.微信搜领导    │ │
│           │                                   │ 4.微信RPA取证   │ │
│           │                                   └───────┬────────┘ │
│           │                                           │          │
│      激活/积分查询                             LLM/OCR代理   │
│           │                               (含enable_search) │
│           │                                           │          │
└───────────┼───────────────────────────────────────────┼──────────┘
            │ HTTPS                                     │ HTTPS
            ▼                                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                    服务端（aid-work-agent）                        │
│                                                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────┐  ┌───────────┐ │
│  │ 激活码管理   │  │ 积分查询/扣减  │  │ LLM代理    │  │ 日志接收   │ │
│  │ POST/activate│  │ GET /credits  │  │ /llm/proxy│  │ POST /log │ │
│  └─────────────┘  └──────────────┘  └───────────┘  └───────────┘ │
│         │                │                │               │       │
│         ▼                ▼                ▼               ▼       │
│     client_bindings   tenants.          llm_gateway    client_logs│
│         表          credit_balance       (Qwen/Zhipu       表     │
│                          (×5)            /DeepSeek)             │
│                                                │                   │
│                                                ▼                   │
│                                         token_cost_prices         │
│                                         (元/百万token)            │
└───────────────────────────────────────────────────────────────────┘
```

### 1.2 架构决策记录（已确认，不再讨论）

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 客户端形态 | **Electron 桌面应用** | 与 boss-resume-assistant 技术栈一致；能 spawn 子进程、内嵌本地 SQLite、直接复用 HTML UI |
| 积分归属 | **共用租户通用积分池** `tenants.credit_balance` | 复用现有计费链路，改动最小 |
| 5倍系数含义 | **实际扣租户积分 = 标准积分 × 5** | 客户端消耗直接乘以5后扣减租户余额 |
| 租户绑定方式 | **一次性激活码** | 适合"发码给客户"的交付场景；激活后换取绑定令牌 |
| 业务逻辑载体 | **本地命令行工具（PyInstaller exe）** | 客户端只调命令行，不直接含业务逻辑；命令行含完整5步流水线 |
| LLM 调用方式 | **客户端→服务端代理→模型提供商** | 服务端集中计费、集中管控 Key |
| 服务端现有 agent-desktop 参考 | **不照搬其"纯壳连远程"模式** | 微信RPA必须在本地跑；本客户端的Python逻辑也打包在本地exe |
| Playwright 浏览器 | **客户预装，不打包进 exe** | Chromium ~150MB 打包进 PyInstaller 风险高、易出错。改为交付手册写明预装步骤，CLI 启动时检测缺失则提示安装 |
| 激活码与租户绑定入口 | **平台后台「租户管理」界面操作** | 不裸暴露 API，在编辑租户页面提供「生成客户端激活码」按钮，绑定该租户 |

### 1.3 与现有代码的关系

| 现有模块 | 复用方式 |
|----------|----------|
| `src/services/association_batch_enrichment.py`（`AssociationBatchEnricher`） | **命令行工具直接 import**，5步编排逻辑零改动。新增 `wechat_leader_name_provider` 注入项（可选，传 None 时跳过微信搜领导步骤） |
| `src/services/association_enrichment_providers.py`（5个Provider） | **命令行工具直接 import**。⚠️ **gateway 注入改造**（见 §3.3）：`_strict_json_chat`、`search_profile` 用模块级 `llm_gateway` 单例，需改为 `self._gateway` 实例注入。LLM 调用统一走 DeepSeek（无直连模型商） |
| `src/services/official_site_browser_collector.py`（Playwright采集） | **命令行工具直接 import**，零改动（Playwright 仍需可见窗口） |
| `src/services/association_profile_extractor.py`（LLM抽取14字段） | **命令行工具直接 import**。已有 `gateway` 参数，传入 ProxyLLMGateway 即可 |
| `clients/wechat-souyisou-rpa/scripts/*.ps1`（微信RPA脚本） | **命令行工具打包时带入**，路径调整为 exe 内相对路径。`wechat-souyisou.ps1` 的 `search` 命令（搜领导姓名）和 `collect` 命令（取证手机号）均需带入 |
| `clients/wechat-souyisou-rpa/scripts/llm_judge.py` | **改造为HTTP调用**服务端代理端点 |
| `clients/association-enrichment-ui/static/index.html`（UI参考） | **UI 风格参考**，重写为 Electron renderer |
| `src/llm/gateway.py`（LLM网关） | **服务端使用**，新增的代理端点调用它 |
| `src/services/billing.py`（积分计算） | **服务端使用**，代理端点调用，增加5倍系数 |
| `src/channels/wecom_personal_rpa/auth.py`（HMAC鉴权） | **参考**，客户端激活后的令牌鉴权可简化为 Bearer Token |
| `clients/agent-desktop/electron/*`（Electron壳） | **参考** security/credentials/config 模式 |

---

## 2. 服务端改造设计

服务端是改造的核心——新增 API 端点、数据表，复用现有计费链路。

### 2.1 新增数据表

#### 2.1.1 激活码表 `client_activation_codes`

> 交付时为每个客户生成激活码，客户在客户端首次启动时输入激活码完成绑定。

```sql
-- 文件：deploy/db_update.sql 追加（CREATE TABLE IF NOT EXISTS 幂等）

CREATE TABLE IF NOT EXISTS client_activation_codes (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,                       -- 激活码明文（用户输入），格式 AC-XXXXXXXXXXXX（AC-前缀+12位大写字母数字）
    code_hash TEXT NOT NULL,                         -- bcrypt(code) 哈希，用于校验（不存明文校验值）
    tenant_id TEXT NOT NULL,                         -- 绑定的租户
    client_name TEXT,                                -- 客户端标识名（如"中国黄金协会-张三电脑"）
    status TEXT NOT NULL DEFAULT 'unused',           -- unused / used / disabled
    activated_at TIMESTAMP,                          -- 激活时间
    activated_machine TEXT,                          -- 激活机器标识（机器码，用于校验）
    expires_at TIMESTAMP,                            -- 激活码本身的有效期（过期不可激活）
    max_uses INTEGER DEFAULT 1,                      -- 最大可激活次数（默认1次，激活后作废）
    used_count INTEGER DEFAULT 0,                    -- 已激活次数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_client_activation_codes_tenant
    ON client_activation_codes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_client_activation_codes_code
    ON client_activation_codes(code);
```

**生成规则**（服务端 `POST /api/saas/client-activations` 管理端点，仅 platform_admin）：
- `code = "AC-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(12))`（去除易混淆字符 0/O/1/I/l）
- `code_hash = bcrypt.hashpw(code.encode(), bcrypt.gensalt()).decode()`
- 明文 `code` 仅在创建时返回一次（给运营人员交付给客户），DB 存 `code` 列用于快速查找 + `code_hash` 用于校验（双重：先按 code 查到记录，再用 bcrypt 校验防篡改）

> **安全说明**：`code` 列存的是明文激活码，这有泄露风险但简化了查找逻辑。激活码本质是一次性凭证（max_uses=1），且可设置 expires_at，泄露风险可控。如果要求更高安全性，可改为只存 code_hash 并用全表扫描（激活码总量小，性能可接受）。**MVP 采用存明文方案**。

#### 2.1.2 客户端绑定表 `client_bindings`

> 激活后生成的长期绑定凭证，客户端每次请求携带。

```sql
CREATE TABLE IF NOT EXISTS client_bindings (
    id SERIAL PRIMARY KEY,
    binding_id TEXT UNIQUE NOT NULL,                 -- 绑定ID，格式 cb_<32位hex>（secrets.token_hex(16)）
    tenant_id TEXT NOT NULL,                         -- 绑定的租户
    activation_code_id INTEGER,                      -- 来源激活码（可空，支持非激活码创建）
    client_name TEXT,                                -- 客户端显示名
    machine_id TEXT,                                 -- 绑定的机器码（激活时采集，用于校验）
    access_token TEXT UNIQUE NOT NULL,               -- 长期访问令牌 secrets.token_urlsafe(48)，客户端鉴权用
    status TEXT NOT NULL DEFAULT 'active',           -- active / disabled
    last_seen_at TIMESTAMP,                          -- 最后活跃时间
    expires_at TIMESTAMP,                            -- 绑定过期时间（默认null=不过期，或与租户expire_at一致）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (activation_code_id) REFERENCES client_activation_codes(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_client_bindings_tenant ON client_bindings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_client_bindings_token ON client_bindings(access_token);
```

**令牌格式**：`access_token = secrets.token_urlsafe(48)`（64字符左右）。客户端存储在本地（Electron safeStorage 加密），每次请求放在 HTTP Header `Authorization: Bearer {access_token}`。

**机器码 `machine_id`**：客户端激活时生成，`sha256(主板序列号 + CPU ID + 磁盘序列号)` 取前32位hex。激活时绑定，后续请求不强制校验机器码（MVP不校验，避免换硬件导致无法使用）。**保留字段，Phase 2 可启用强制校验**。

#### 2.1.3 客户端消耗日志表 `client_usage_logs`

> 客户端每次任务结束上报，记录消耗明细。与现有 `chat_records` 并行（client 场景不走对话表）。

```sql
CREATE TABLE IF NOT EXISTS client_usage_logs (
    id SERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,                         -- 租户
    binding_id TEXT NOT NULL,                        -- 客户端绑定
    client_name TEXT,                                -- 客户端名快照
    session_id TEXT,                                 -- 客户端会话ID（一次协会收集任务）
    association_name TEXT,                           -- 协会名
    role TEXT,                                       -- 角色（会长/秘书长，微信RPA用）
    stage TEXT,                                      -- 流水线阶段（search_profile/official_site/official_profile/wechat_search_leader/wechat_mobile/judge/ocr）
    status TEXT,                                     -- 结果状态（success/failed/not_found/inconclusive/aborted）
    model TEXT,                                      -- 使用的模型
    provider TEXT,                                   -- 提供商
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    raw_credit_cost NUMERIC(12,2) DEFAULT 0,         -- 标准积分成本（未乘5）
    credit_cost NUMERIC(12,2) DEFAULT 0,             -- 实际扣除积分（raw_credit_cost × 5）
    error_code TEXT,                                 -- 错误码
    detail TEXT,                                     -- 脱敏详情（JSON字符串）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_tenant ON client_usage_logs(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_client_usage_logs_binding ON client_usage_logs(binding_id, created_at);
```

> **注意**：积分扣减仍然走现有 `tenants.credit_balance`（同事务原子扣减，复用 `ChatRecordDB` 的扣费逻辑或新增平行的 `ClientUsageLogDB`）。`source_type = 'client'` 用于区分。`raw_credit_cost` 记录标准成本（便于审计），`credit_cost` 记录实扣（×5后）。

### 2.2 新增 API 端点

所有端点路由前缀 `/api/client/v1`，挂在 `src/main.py`。

#### 2.2.1 激活码激活

```
POST /api/client/v1/activate
```

**请求**：
```json
{
  "activation_code": "AC-XXXXXXXXXXXX",
  "machine_id": "a1b2c3...(32位hex)",
  "client_name": "我的电脑"
}
```

**处理逻辑**：
1. 按 `activation_code` 查 `client_activation_codes` 表
2. 校验：存在、status != 'disabled'、used_count < max_uses、未过期（expires_at）
3. 校验 code_hash（bcrypt）
4. 创建 `client_bindings` 记录：生成 `binding_id`、`access_token`，绑定 tenant_id（从激活码继承）、machine_id、client_name
5. 更新激活码：used_count += 1，若达到 max_uses 则 status='used'，记录 activated_at / activated_machine
6. 返回绑定凭证

**响应（200）**：
```json
{
  "binding_id": "cb_xxxxxxxxxxxx",
  "access_token": "xxxxxxxxxxxx",
  "tenant_id": "tenant_xxxxxxxxxxxx",
  "tenant_name": "中国黄金协会",
  "credit_balance": 5000.00,
  "expires_at": null
}
```

**错误响应**：
- `404 ACTIVATION_CODE_NOT_FOUND` — 激活码不存在
- `410 ACTIVATION_CODE_USED` — 已被使用（max_uses 达上限）
- `410 ACTIVATION_CODE_EXPIRED` — 已过期
- `410 ACTIVATION_CODE_DISABLED` — 已禁用

#### 2.2.2 积分余额查询

```
GET /api/client/v1/credits
Authorization: Bearer {access_token}
```

**处理逻辑**：
1. `verify_client_token(access_token)` → 获取 binding（含 tenant_id）
2. 校验 binding.status == 'active'
3. 查 `tenants.credit_balance`
4. 聚合 `client_usage_logs` 统计今日/本周消耗

**响应（200）**：
```json
{
  "balance": 4500.00,
  "today_consumed": 120.00,
  "week_consumed": 850.50,
  "total_consumed": 500.00
}
```

#### 2.2.3 LLM 代理端点（核心计费点）

```
POST /api/client/v1/llm/chat
Authorization: Bearer {access_token}
Content-Type: application/json
```

**请求**（OpenAI 兼容格式子集）：
```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0,
  "max_tokens": 4000,
  "response_format": {"type": "json_object"},
  "purpose": "profile_extraction"
}
```

`purpose` 字段用于日志归类，取值：`official_site_judge` / `profile_extraction` / `leadership_extraction` / `wechat_judge` / `parse_associations`，可选。

**处理逻辑**：
1. `verify_client_token(access_token)` → 获取 binding + tenant
2. **余额检查**：`tenants.credit_balance <= 0` → 返回 `402 NO_CREDIT`（阻断）
3. 调用 `llm_gateway.chat(messages=..., temperature=..., max_tokens=..., response_format=...)`
4. 取 `response["usage"]`（标准化后的 prompt_tokens / completion_tokens / cached_tokens）
5. **积分计算（×5 系数）**：
   ```python
   raw_credit = calculate_credit_cost(usage, model)  # 现有函数
   credit_cost = math.ceil(raw_credit * CLIENT_CREDIT_MULTIPLIER * 100) / 100  # CLIENT_CREDIT_MULTIPLIER = 5
   ```
6. **原子扣减**：同事务 INSERT `client_usage_logs` + UPDATE `tenants.credit_balance -= credit_cost`
7. 返回 LLM 响应 + 消耗信息

**响应（200）**：
```json
{
  "content": "LLM 返回内容",
  "model": "deepseek-v4-flash",
  "provider": "deepseek",
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 800,
    "cached_tokens": 0,
    "total_tokens": 2000
  },
  "billing": {
    "raw_credit_cost": 0.40,
    "credit_cost": 2.00,
    "balance_after": 4498.00
  }
}
```

**错误响应**：
- `401 UNAUTHORIZED` — access_token 无效/绑定已禁用
- `402 NO_CREDIT` — 余额不足，阻断调用（客户端必须停止任务并提示充值）
- `502 LLM_PROVIDER_ERROR` — 模型调用失败（透传错误信息）

#### 2.2.4 OCR 代理端点

```
POST /api/client/v1/ocr/parse
Authorization: Bearer {access_token}
Content-Type: multipart/form-data
```

**请求**：`image` 字段（PNG 文件，< 10MB）

**处理逻辑**：
1. 鉴权 + 余额检查
2. 调用现有 `paddleocr_doc_parsing(file_path=临时文件, file_type=1)`
3. OCR 不消耗 LLM token，但需记录调用次数（`client_usage_logs` stage='ocr'，credit_cost=0）用于统计
4. 返回识别文本

**响应（200）**：
```json
{
  "text": "识别出的文本内容",
  "image_count": 1
}
```

> **注意**：OCR 走 PaddleOCR 云端 API，成本由服务端承担，不对客户端收费（credit_cost=0）。但调用次数记录在 `client_usage_logs` 供统计。

#### 2.2.5 WebSearch 代理端点（⚠️ 已废弃，见 §3.5）

> **2026-08-06**：Qwen `enable_search` 实验失败、Tavily 已移除。协会收集不再依赖任何独立搜索服务——所有信息获取走 DeepSeek 普通对话。此端点**无需实现**。

~~POST /api/client/v1/websearch~~ — 已废弃。

#### 2.2.6 日志上报端点

```
POST /api/client/v1/logs
Authorization: Bearer {access_token}
Content-Type: application/json
```

**请求**（批量）：
```json
{
  "session_id": "uuid",
  "logs": [
    {
      "timestamp": "2026-08-06T12:00:00Z",
      "level": "INFO",
      "stage": "wechat_mobile",
      "association_name": "中国黄金协会",
      "message": "微信检索会长手机号",
      "detail": {...}
    }
  ]
}
```

**处理逻辑**：鉴权后批量写入 `client_usage_logs`（或单独的 `client_runtime_logs` 表，MVP 复用 client_usage_logs 的 detail 字段）。

**响应（200）**：`{"accepted": 5}`

#### 2.2.7 任务结果上报端点（可选，用于服务端留存）

```
POST /api/client/v1/results
Authorization: Bearer {access_token}
```

**请求**：一次协会收集的最终结果（脱敏后的结构化数据，手机号脱敏）。

> **MVP 不强制实现**。客户端本地 SQLite 已有完整结果，服务端留存可选。Phase 2 实现。

### 2.3 鉴权中间件

新增 `verify_client_token(access_token)` 函数，放在 `src/api/client_auth.py`：

```python
# src/api/client_auth.py
from src.db.models import get_db_connection
from src.saas.db.tenant_db import TenantDB

class ClientBinding:
    def __init__(self, binding_id, tenant_id, client_name, status, tenant):
        self.binding_id = binding_id
        self.tenant_id = tenant_id
        self.client_name = client_name
        self.status = status
        self.tenant = tenant  # TenantResponse 对象，含 credit_balance

def verify_client_token(access_token: str) -> ClientBinding | None:
    """
    校验客户端 access_token，返回绑定信息。
    优先查 Redis 缓存（key: client_token:{access_token}，TTL 300s），
    未命中查 DB client_bindings 表。
    返回 None 表示无效/已禁用。
    """
    # 1. Redis 缓存
    cached = redis_get(f"client_token:{access_token}")
    if cached:
        binding = ClientBinding(**cached)
        if binding.status != 'active':
            return None
        return binding

    # 2. 查 DB
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT binding_id, tenant_id, client_name, status FROM client_bindings "
            "WHERE access_token = %s AND status = 'active'",
            (access_token,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        binding_id, tenant_id, client_name, status = row

    # 3. 查租户（含余额）
    tenant = TenantDB.get_by_id(tenant_id)
    if not tenant or tenant.status != 'active':
        return None

    binding = ClientBinding(binding_id, tenant_id, client_name, status, tenant)
    redis_set(f"client_token:{access_token}", {...}, ttl=300)
    return binding
```

### 2.4 计费逻辑（×5 系数）

新增配置项 `src/config/settings.py`：

```python
class ClientConfig(BaseModel):
    """客户端场景配置"""
    credit_multiplier: float = 5.0  # 积分膨胀系数，客户端消耗 = 标准积分 × 此系数
    llm_request_timeout: int = 120  # 客户端 LLM 代理请求超时（秒）
    max_image_size_mb: int = 10     # OCR 图片大小上限

# 挂到 Settings
class Settings(BaseModel):
    ...
    client: ClientConfig = ClientConfig()
```

**扣费实现**（`src/api/client_llm_proxy.py`）：

```python
import math
from src.services.billing import calculate_credit_cost
from src.config import settings

async def proxy_llm_chat(request, binding: ClientBinding):
    # ... 鉴权 + 余额检查 ...

    # 余额阻断
    if binding.tenant.credit_balance <= 0:
        return error_response(402, "NO_CREDIT", "积分余额不足，请充值")

    # 调用 LLM
    response = await llm_gateway.chat(
        messages=payload["messages"],
        temperature=payload.get("temperature", 0),
        max_tokens=payload.get("max_tokens", 4000),
        response_format=payload.get("response_format"),
    )

    # 计算积分（标准 + ×5）
    usage = response.get("usage", {})
    model = llm_gateway.get_model_name()
    raw_credit = calculate_credit_cost(usage, model)  # 复用现有函数
    credit_cost = math.ceil(raw_credit * settings.client.credit_multiplier * 100) / 100

    # 原子扣减（同事务）
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            # INSERT usage log
            cursor.execute(
                "INSERT INTO client_usage_logs "
                "(tenant_id, binding_id, session_id, association_name, stage, status, "
                " model, provider, prompt_tokens, completion_tokens, cached_tokens, "
                " total_tokens, raw_credit_cost, credit_cost) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (binding.tenant_id, binding.binding_id, session_id,
                 association_name, stage, "success",
                 model, provider,
                 usage.get("prompt_tokens", 0),
                 usage.get("completion_tokens", 0),
                 usage.get("cached_tokens", 0),
                 usage.get("total_tokens", 0),
                 raw_credit, credit_cost)
            )
            # 扣减余额
            cursor.execute(
                "UPDATE tenants SET credit_balance = credit_balance - %s, "
                "updated_at = CURRENT_TIMESTAMP WHERE tenant_id = %s",
                (credit_cost, binding.tenant_id)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # 失效缓存
    invalidate_tenant_cache(binding.tenant_id)

    return success_response({
        "content": response.get("content", ""),
        "model": model,
        "provider": provider,
        "usage": usage,
        "billing": {
            "raw_credit_cost": raw_credit,
            "credit_cost": credit_cost,
            "balance_after": binding.tenant.credit_balance - credit_cost,
        }
    })
```

### 2.5 管理端点（平台管理员用）

路由前缀 `/api/saas/client-activations`，挂在 `src/saas/api/client_activation_mgmt.py`，仅 platform_admin：

```
POST   /api/saas/client-activations           # 生成激活码（指定 tenant_id, client_name, expires_at）
GET    /api/saas/client-activations/list       # 列出激活码（支持按 tenant 过滤）
GET    /api/saas/client-activations/{id}       # 查看激活码详情
DELETE /api/saas/client-activations/{id}       # 禁用激活码
POST   /api/saas/client-activations/{id}/revoke # 吊销已激活的绑定
```

```
GET    /api/saas/client-bindings/list          # 列出客户端绑定
POST   /api/saas/client-bindings/{id}/disable  # 禁用绑定（踢下线）
POST   /api/saas/client-bindings/{id}/rotate-token  # 轮换 access_token
```

#### 2.5.1 平台后台界面入口（激活码与租户绑定）

激活码的生成、查看、禁用**集成到平台管理后台的「租户管理」页面**，不单独做菜单。运营人员在编辑某租户时，在该租户详情页操作：

**「租户管理 - 编辑租户」页面新增区块「协会客户端激活码」**，包含：

| UI 元素 | 功能 |
|---------|------|
| 「+ 生成激活码」按钮 | 弹窗输入 `client_name`（如"中国黄金协会-张三电脑"）和可选 `expires_at` → 调 `POST /api/saas/client-activations`（tenant_id 取当前编辑的租户）→ 生成后弹窗显示激活码明文，提示"仅显示一次，请复制保存" |
| 激活码列表表格 | 列：激活码、客户端名、状态（未使用/已使用/已禁用）、已激活次数/上限、激活时间、创建时间、操作（禁用/吊销绑定） |
| 「禁用」按钮 | 将激活码 status 置为 disabled（未激活的不可再激活） |
| 「吊销绑定」按钮 | 仅对已激活的激活码显示，吊销其关联的 client_binding（下线客户端） |

**前端改动位置**（参考现有 `TenantRecharge.vue` 在编辑租户弹窗中的位置）：
- 新建 `frontend/src/components/tenant/ClientActivationManager.vue` 组件
- 在租户编辑弹窗中挂载该组件（与现有「数字员工授权」「API 配置」等区块平级）
- 组件内调上述 `/api/saas/client-activations/*` 端点

**数据流**：租户管理页面（tenant_id 已知）→ 生成激活码时携带 tenant_id → 激活码与租户绑定存入 `client_activation_codes` 表。客户激活后，`client_bindings.tenant_id` 继承自激活码，实现租户绑定。

---

## 3. 本地命令行工具设计

命令行工具是业务逻辑的载体，用 **PyInstaller 打包成单个 exe**，Electron 客户端 spawn 调用。

### 3.1 工程结构

```
clients/association-client-cli/          # 新建工程
├── build.spec                            # PyInstaller 打包配置
├── requirements.txt                      # Python 依赖
├── main.py                               # CLI 入口
├── runtime/
│   ├── proxy_gateway.py                  # ★ 走服务端代理的 LLM Gateway 实现
│   ├── progress_reporter.py              # 进度上报（stdout JSON 行）
│   ├── local_db.py                       # 本地 SQLite（结果+日志镜像）
│   ├── activation.py                     # 激活/令牌管理
│   └── powershell_runner.py              # spawn PowerShell 脚本的封装
├── scripts/                              # ★ 从 wechat-souyisou-rpa 复制
│   ├── wechat-souyisou.ps1               # 改造：judge/ocr 改为 HTTP 调用服务端代理
│   ├── wechat-souyisou-lib.ps1           # 改造：New-ExternalJudge 增加 HTTP 模式
│   ├── extract-mobile.ps1                # 原样
│   └── read-artifact.ps1                 # 原样
└── README.md
```

### 3.2 CLI 接口设计

#### 3.2.1 激活命令

```bash
association-client.exe activate --code AC-XXXXXXXXXXXX [--server-url https://agent.xxx.cn]
```

- 采集本机 `machine_id`
- 调用 `POST /api/client/v1/activate`
- 成功后将 `access_token`、`binding_id`、`server_url` 存入 `%APPDATA%\association-client\config.json`
- 失败输出错误，退出码 1

#### 3.2.2 余额查询命令

```bash
association-client.exe credits
```

- 读配置中的 access_token
- 调用 `GET /api/client/v1/credits`
- stdout 输出 JSON：`{"balance": 4500.00, "today_consumed": 120.00}`

#### 3.2.3 核心命令：协会收集

```bash
association-client.exe collect \
  --associations "中国黄金协会,中国机械工业协会" \
  --output "C:\Users\xxx\Desktop\result.xlsx" \
  [--server-url https://...] \
  [--no-wechat]              # 跳过微信RPA步骤（调试用）
```

或从文件输入：

```bash
association-client.exe collect --input input.csv --output result.xlsx
```

**进度输出协议（stdout，NDJSON）**：

命令行工具通过 stdout 输出 **换行分隔的 JSON（NDJSON）**，每行一个事件，Electron 客户端实时解析：

```jsonl
{"event":"start","session_id":"uuid","associations":["中国黄金协会","中国机械工业协会"],"timestamp":"2026-08-06T12:00:00Z"}
{"event":"progress","association":"中国黄金协会","step":"official_site","status":"running","progress":25,"message":"搜索官网","timestamp":"..."}
{"event":"progress","association":"中国黄金协会","step":"official_profile","status":"success","progress":50,"message":"官网采集完成","timestamp":"..."}
{"event":"billing","association":"中国黄金协会","stage":"profile_extraction","raw_credit_cost":0.4,"credit_cost":2.0,"balance_after":4498.0,"timestamp":"..."}
{"event":"log","level":"INFO","association":"中国黄金协会","message":"会长姓名：张三","timestamp":"..."}
{"event":"error","association":"中国黄金协会","stage":"wechat_mobile","error_code":"WECHAT_RPA_TIMEOUT","message":"微信RPA超时","timestamp":"..."}
{"event":"complete","session_id":"uuid","total_consumed":15.5,"output":"C:\\Users\\xxx\\Desktop\\result.xlsx","timestamp":"..."}
```

**事件类型**：

| event | 含义 | 关键字段 |
|-------|------|----------|
| `start` | 任务开始 | session_id, associations[], timestamp |
| `progress` | 进度更新 | association, step, status(running/success/failed), progress(0-100), message |
| `billing` | 计费事件 | association, stage, raw_credit_cost, credit_cost, balance_after |
| `log` | 日志 | level, association, message |
| `error` | 错误 | association, stage, error_code, message, session_fatal(bool) |
| `complete` | 任务完成 | session_id, total_consumed, output, summary |

**手机号脱敏**：所有 stdout 输出的手机号必须脱敏为 `1xx****xxxx`，明文只进本地 SQLite 和最终 Excel。

**退出码**：
- 0 = 全部成功
- 2 = 部分失败（有 association 成功，有失败）
- 3 = 余额不足中断
- 1 = 系统错误

### 3.3 代理 LLM Gateway 实现

命令行工具需要一个 **Gateway 适配器**，让现有的 `AssociationBatchEnricher` / `extract_association_profile` / `llm_judge.py` 等模块调用时，实际走服务端代理。

```python
# clients/association-client-cli/runtime/proxy_gateway.py

import httpx
from typing import Any

class ProxyLLMGateway:
    """
    LLM Gateway 适配器：将 llm_gateway.chat() 调用代理到服务端 /api/client/v1/llm/chat。
    接口与 src.llm.gateway.LLMGateway 保持一致（chat/stream_chat/chat_with_tools）。
    """

    def __init__(self, server_url: str, access_token: str, timeout: int = 120):
        self.server_url = server_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    async def chat(self, messages, tools=None, tool_choice=None,
                   temperature=0, max_tokens=4000, response_format=None,
                   purpose: str = "unknown") -> dict[str, Any]:
        """与 LLMGateway.chat 签名兼容，返回标准化 response dict。"""
        payload = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "purpose": purpose,
        }
        if response_format:
            payload["response_format"] = response_format
        if tools:
            payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice

        resp = self._client.post(
            f"{self.server_url}/api/client/v1/llm/chat",
            json=payload,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

        if resp.status_code == 402:
            raise NoCreditError(resp.json().get("message", "积分不足"))
        if resp.status_code == 401:
            raise UnauthorizedError("access_token 无效")
        resp.raise_for_status()

        data = resp.json()
        # 透传 billing 事件到 stdout（供 Electron 解析）
        emit_billing_event(data.get("billing", {}))

        return {
            "content": data["content"],
            "usage": data.get("usage", {}),
            "model": data.get("model"),
            "provider": data.get("provider"),
        }

    def get_model_name(self) -> str:
        return "proxy"  # 实际模型由服务端决定

    def get_provider_name(self) -> str:
        return "proxy"


class NoCreditError(Exception):
    """余额不足，客户端必须停止任务"""
    pass
```

**如何接入现有代码（⚠️ gateway 注入问题与解决方案）**：

> **现状（commit `36f575e` 后，Qwen 实验代码已删除）**：`association_enrichment_providers.py` 的所有 LLM 调用**都走 `llm_gateway`（DeepSeek）**，没有直连模型商。但有两处用的是**模块级 `llm_gateway` 单例**，不接受参数注入，客户端无法替换为 ProxyLLMGateway。必须改造。

**现状 LLM 调用入口**：

| 调用点 | 代码位置 | 当前方式 | 走代理？ |
|--------|----------|----------|----------|
| `_strict_json_chat`（静态方法） | 行 163 | `llm_gateway.chat()`（模块级单例） | ❌ 无法注入 |
| `search_profile` | 行 514 | `llm_gateway.chat()`（模块级单例） | ❌ 无法注入 |
| `extract_association_profile` | 外部函数 | `gateway` 参数（默认 None→单例） | ✅ 可注入 |
| `llm_judge.py` 的 `run_judge` | 脚本 | `gateway` 参数（默认 None→单例） | ✅ 可注入 |

`_strict_json_chat` 被 3 处间接调用：`collect_official_profile` 的 leadership 抽取、`wechat_search_leader_name`（搜领导姓名解析）、`fallback_profile` 的 evidence 校验。（原 `resolve_official_site` 已删除，不再调用 `_strict_json_chat`。）

**改造方案（静态方法→实例方法 + gateway 注入）**：

1. `ProjectAssociationProviders.__init__` 新增 `gateway` 参数：

```python
class ProjectAssociationProviders:
    def __init__(self, repository_root: Path, gateway=None):
        self._root = repository_root
        self._gateway = gateway or llm_gateway  # CLI 传 ProxyLLMGateway，服务端/CLI 内置用默认
```

2. `_strict_json_chat` 从**静态方法改为实例方法**，用 `self._gateway`：

```python
# 改造前（静态方法，硬编码 llm_gateway）
@staticmethod
async def _strict_json_chat(messages, *, max_tokens) -> dict:
    response = await llm_gateway.chat(...)  # 硬编码

# 改造后（实例方法，用注入的 gateway）
async def _strict_json_chat(self, messages, *, max_tokens) -> dict:
    response = await self._gateway.chat(messages=..., temperature=0, max_tokens=max_tokens)
```

> ⚠️ `_strict_json_chat` 改为实例方法后，调用处需加 `self.`。当前调用点：`collect_official_profile`、`wechat_search_leader_name`、`fallback_profile`（原 `_validate_fallback_evidence` 重试路径）。检查是否有 `ProjectAssociationProviders._strict_json_chat(...)` 静态调用形式，全部改为 `self._strict_json_chat(...)`。

3. `search_profile`（行 514）从模块级 `llm_gateway` 改为 `self._gateway`：

```python
# 改造前
resp = await llm_gateway.chat(messages=..., max_tokens=2500)

# 改造后
resp = await self._gateway.chat(messages=..., max_tokens=2500)
```

**改造后的计费覆盖**：

| 调用点 | 改造后路径 | 计费？ |
|--------|-----------|--------|
| `search_profile` | `self._gateway` → ProxyLLMGateway → 服务端代理 | ✅ |
| `_strict_json_chat`（含 leadership / leader_name / fallback evidence） | `self._gateway` → ProxyLLMGateway → 服务端代理 | ✅ |
| `extract_association_profile` | `gateway=self._gateway` → ProxyLLMGateway → 服务端代理 | ✅ |
| `llm_judge.py`（微信取证 judge） | 环境变量 → ProxyLLMGateway → 服务端代理 | ✅ |

> **这是整个客户端计费架构成立的前提**。开发时必须用单元测试验证：CLI 运行收集任务后，服务端 `client_usage_logs` 表的记录数应等于所有 LLM 调用次数（用 mock 模型统计）。

> **对现有代码的影响**：上述改造同时提交回主分支（`src/services/association_enrichment_providers.py`），让服务端 CLI/UI 入口也受益。改造保持向后兼容：`gateway=None` 时用默认 `llm_gateway`，行为不变。

### 3.4 微信 RPA PowerShell 脚本改造

微信 RPA 脚本（`wechat-souyisou.ps1`）中的 judge 和 OCR 调用必须改造为 HTTP 模式。

#### 3.4.1 `wechat-souyisou-lib.ps1` 的 `New-ExternalJudge` 改造

新增 `New-HttpJudge` 函数，与 `New-ExternalJudge` 接口一致（返回 scriptblock），但内部走 HTTP：

```powershell
# wechat-souyisou-lib.ps1 新增

function New-HttpJudge {
    param(
        [string]$Endpoint,           # 如 "https://xxx/api/client/v1/llm/chat"
        [string]$AccessToken,
        [ValidateRange(1,120000)][int]$TimeoutMilliseconds = 60000
    )
    if ([string]::IsNullOrWhiteSpace($Endpoint)) { return $null }

    $headers = @{
        "Authorization" = "Bearer $AccessToken"
        "Content-Type" = "application/json"
    }

    return {
        param($payload)
        # payload 结构：{"association_name":"...","person_name":"...","text":"..."}
        $body = @{
            messages = @(
                @{ role = "system"; content = "你是联系人证据核验器，只输出严格JSON。" }
                @{ role = "user"; content = ($payload | ConvertTo-Json -Depth 4 -Compress) }
            )
            temperature = 0
            max_tokens = 2500
            response_format = @{ type = "json_object" }
            purpose = "wechat_judge"
        } | ConvertTo-Json -Depth 6 -Compress

        $response = Invoke-RestMethod -Method Post -Uri $Endpoint `
            -Headers $headers -Body $body -ContentType "application/json" `
            -TimeoutSec ([math]::Floor($TimeoutMilliseconds / 1000))

        # 服务端返回 {"content":"...","usage":{...}}
        # content 是 JSON 字符串，需解析后返回（与原 python 子进程输出格式一致）
        return $response.content | ConvertFrom-Json
    }.GetNewClosure()
}
```

> **注意**：judge 的 prompt 构造（那段长中文 prompt）原本在 `llm_judge.py` 里。改造后有两种方案：
> - **方案A（推荐，MVP）**：保留 `llm_judge.py` 作为 **stdin/stdout 本地脚本**，但它内部改为调服务端代理而非直连 `llm_gateway`。即 `llm_judge.py` 的 `run_judge` 里 `gateway` 参数传入 `ProxyLLMGateway` 实例。PowerShell 仍 spawn `python llm_judge.py`，但 Python 进程调 HTTP。**改动最小**。
> - **方案B**：把 prompt 构造移到 PowerShell 侧，直接 `New-HttpJudge` 构造完整 messages。改动大，不推荐。

**MVP 采用方案A**：`llm_judge.py` 改造如下：

```python
# clients/association-client-cli/scripts/llm_judge.py（改造版）
# 关键改动：gateway 默认改为 ProxyLLMGateway，通过环境变量读取配置

async def run_judge(payload, gateway=None):
    if gateway is None:
        # 从环境变量构造 ProxyLLMGateway
        server_url = os.environ["ASSOCIATION_CLIENT_SERVER_URL"]
        access_token = os.environ["ASSOCIATION_CLIENT_ACCESS_TOKEN"]
        from runtime.proxy_gateway import ProxyLLMGateway
        gateway = ProxyLLMGateway(server_url, access_token)
    # ... 原 prompt 构造和 chat 调用逻辑不变 ...
```

PowerShell spawn 时通过环境变量传入配置：

```python
# powershell_runner.py
env = os.environ.copy()
env["ASSOCIATION_CLIENT_SERVER_URL"] = self.server_url
env["ASSOCIATION_CLIENT_ACCESS_TOKEN"] = self.access_token
env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
# spawn powershell.exe ... -UseProjectLlm
```

#### 3.4.2 OCR 改造

`ocr_adapter.py` 改造为 HTTP 调用服务端 `/api/client/v1/ocr/parse`：

```python
# clients/association-client-cli/scripts/ocr_adapter.py（改造版）
import json, sys, os, httpx, tempfile

def main():
    payload = json.loads(sys.stdin.read())
    image_paths = payload.get("image_paths", [])

    server_url = os.environ["ASSOCIATION_CLIENT_SERVER_URL"]
    access_token = os.environ["ASSOCIATION_CLIENT_ACCESS_TOKEN"]

    texts = []
    for img_path in image_paths:
        with open(img_path, "rb") as f:
            resp = httpx.post(
                f"{server_url}/api/client/v1/ocr/parse",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"image": ("ocr.png", f, "image/png")},
                timeout=60,
            )
            resp.raise_for_status()
            texts.append(resp.json().get("text", ""))

    result = {"ok": True, "text": "\n".join(texts), "image_count": len(image_paths)}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()
```

### 3.5 联网搜索说明

> **2026-08-06 现状**：Qwen `enable_search` 实验失败已废弃，Tavily 也已移除。当前所有信息获取都靠 **DeepSeek 普通对话**（`llm_gateway.chat()`），DeepSeek 接口无联网搜索能力，依赖模型自身知识。

**官网 URL 来源**：第1步 `search_profile` 让 DeepSeek 直接返回 `official_website` 字段。`resolve_official_site` 方法（原 Tavily 搜索兜底）已从 `ProjectAssociationProviders` 删除；`AssociationBatchEnricher.official_site_resolver` 注入项改为**可选**（默认 `None`），`enrich_one` 里仅在传入 resolver 且 `search_profile` 未返回官网 URL 时才调用。客户端 CLI 构造 enricher 时**不传** `official_site_resolver`，官网 URL 完全依赖 `search_profile` 的 DeepSeek 输出。

**计费影响**：所有 LLM 调用都是普通 chat（走 gateway → 代理 → 计费），不涉及搜索 API、不涉及 `enable_search`，链路最简单。Tavily 的 Key、配置在协会收集场景下不再需要。

### 3.6 本地 SQLite（结果 + 日志镜像）

客户端本地存储完整结果（含明文手机号），与现有 `association-enrichment-ui` 的 `EncryptedAuditStore` 一致（DPAPI 加密）。

```python
# clients/association-client-cli/runtime/local_db.py
# 复用 clients/association-enrichment-ui/association_enrichment_ui.py 的 EncryptedAuditStore
# 存储路径：%LOCALAPPDATA%\AidWorkAgent\association-client\runs\<session_id>\
#   - run.json（脱敏摘要）
#   - details\<sha>.dpapi（加密详情）
#   - result.xlsx（最终结果）
```

### 3.7 PyInstaller 打包配置

> **决策：Playwright 浏览器不打包进 exe，由客户在电脑上预装。** Chromium ~150MB 打包进 PyInstaller 易出错且包体过大。改为交付手册写明预装步骤（`pip install playwright` + `playwright install chromium`），CLI 启动时检测浏览器缺失则 fail-loud 提示安装命令。

```python
# clients/association-client-cli/build.spec
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Playwright Python 包本身需要带入（用于 import），但浏览器二进制不打包
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all('playwright')
datas += playwright_datas
binaries += playwright_binaries
hiddenimports += playwright_hiddenimports
# 注意：chromium 浏览器二进制不在 collect_all 范围内（它在用户目录缓存），
# 由客户预装 playwright install chromium 提供。

# 必须带入的脚本文件
datas += [
    ('scripts/wechat-souyisou.ps1', 'scripts'),
    ('scripts/wechat-souyisou-lib.ps1', 'scripts'),
    ('scripts/extract-mobile.ps1', 'scripts'),
    ('scripts/read-artifact.ps1', 'scripts'),
    ('scripts/llm_judge.py', 'scripts'),
    ('scripts/ocr_adapter.py', 'scripts'),
]

# 必须带入的 src 模块（association enrichment 相关）
datas += [
    ('../../src/services/association_batch_enrichment.py', 'src/services'),
    ('../../src/services/association_enrichment_providers.py', 'src/services'),
    ('../../src/services/association_profile_extractor.py', 'src/services'),
    ('../../src/services/official_site_browser_collector.py', 'src/services'),
    ('../../src/services/official_site_page_collector.py', 'src/services'),
    # ... 其他依赖 ...
]

a = Analysis(
    ['main.py'],
    pathex=['../..'],  # 项目根目录
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        'src.services.association_batch_enrichment',
        'src.services.association_enrichment_providers',
        'src.services.association_profile_extractor',
        'src.services.official_site_browser_collector',
        'src.services.official_site_page_collector',
        'playwright.async_api',
    ],
    ...
)
```

#### 3.7.1 Playwright 浏览器预装方案（交付手册核心内容）

客户电脑必须预装 Playwright 的 Chromium 浏览器，CLI 依赖它采集协会官网。交付手册写明以下步骤：

**方案A（推荐，要求客户装 Python）**：
```cmd
:: 1. 安装 Python 3.11+（如已装则跳过），勾选 "Add Python to PATH"
:: 2. 安装 playwright 包
pip install playwright
:: 3. 安装 Chromium 浏览器（约 150MB 下载）
playwright install chromium
```

**方案B（客户不装 Python，使用安装包内置的安装脚本）**：
客户端安装包内附一个 `install-playwright.cmd` 脚本，由 Electron 客户端首次启动时检测到 Chromium 缺失后**自动引导执行**：
```cmd
:: install-playwright.cmd（随安装包分发）
@echo off
echo 正在安装 Playwright Chromium 浏览器，请稍候...
:: 解压内嵌的 playwright python wheel 或调用内嵌 python
python -m pip install playwright
python -m playwright install chromium
echo 安装完成。
pause
```

> **方案B 需要安装包内置 Python 运行时**（Python embeddable ~30MB，比打包完整 Chromium 小得多）。客户端首次启动时检测 `%LOCALAPPDATA%\ms-playwright\chromium-*` 是否存在，不存在则弹窗引导运行 `install-playwright.cmd`。

**CLI 启动时检测逻辑**（`runtime/playwright_check.py`）：
```python
import os
from pathlib import Path

def ensure_playwright_chromium() -> None:
    """检测 Playwright Chromium 是否已安装，缺失则 fail-loud 提示。"""
    # Playwright 浏览器默认缓存路径
    cache_dir = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH",
                                     Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"))
    chromium_dirs = list(cache_dir.glob("chromium-*"))
    if not chromium_dirs:
        raise RuntimeError(
            "PLAYWRIGHT_NOT_INSTALLED: 未检测到 Playwright Chromium 浏览器。\n"
            "请在命令行执行以下命令安装：\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            "或运行安装目录下的 install-playwright.cmd"
        )
```

**打包后的目录结构**：
```
association-client/                       # 安装目录（Electron 安装包内）
├── association-client.exe                # Electron 客户端
├── resources/
│   ├── app.asar                          # Electron 应用包
│   └── cli/                              # 命令行工具
│       ├── association-cli.exe           # PyInstaller 打包的命令行
│       ├── _internal/                    # PyInstaller 依赖
│       ├── scripts/                      # PowerShell 脚本
│       └── install-playwright.cmd        # ★ Playwright 预装引导脚本
└── ...
```

---

## 4. Electron 客户端设计

### 4.1 工程结构

```
clients/association-client/               # 新建工程
├── package.json
├── electron-builder.yml
├── electron/
│   ├── main.ts                           # 主进程
│   ├── preload.cts                       # preload（窄 IPC）
│   ├── cliRunner.ts                      # ★ spawn 命令行工具的封装
│   ├── config.ts                         # 激活配置管理（safeStorage 加密）
│   ├── security.ts                       # CSP / IPC 白名单
│   └── updater.ts                        # 自动更新（可选）
├── src/                                  # Vue renderer
│   ├── App.vue
│   ├── main.ts
│   ├── views/
│   │   ├── ActivationView.vue            # 激活页（首次启动）
│   │   ├── CollectView.vue               # 主页：协会收集
│   │   ├── ProgressView.vue              # 进度/日志
│   │   ├── ResultView.vue                # 结果展示
│   │   └── CreditsView.vue              # 积分消耗
│   └── components/
│       ├── AssociationInput.vue          # 协会名输入
│       ├── ProgressTimeline.vue          # 进度时间线
│       ├── LogStream.vue                 # 日志流
│       └── CreditsCard.vue               # 积分卡片
├── tests/
└── static/                               # 静态资源
```

### 4.2 主进程核心：CLI Runner

`electron/cliRunner.ts` 负责启动命令行工具子进程、解析 NDJSON 输出、转发到 renderer：

```typescript
// electron/cliRunner.ts
import { spawn, ChildProcess } from 'child_process';
import { EventEmitter } from 'events';
import * as path from 'path';

export interface CliEvent {
  event: string;
  [key: string]: any;
}

export class CliRunner extends EventEmitter {
  private process: ChildProcess | null = null;

  constructor(private cliPath: string) {
    super();
  }

  /**
   * 启动协会收集任务。
   * @param associations 协会名数组
   * @param outputPath 结果 Excel 路径
   * @param configPath 激活配置路径（含 access_token）
   */
  async collect(
    associations: string[],
    outputPath: string,
    serverUrl: string
  ): Promise<void> {
    const args = [
      'collect',
      '--associations', associations.join(','),
      '--output', outputPath,
      '--server-url', serverUrl,
    ];

    this.process = spawn(this.cliPath, args, {
      windowsHide: false,  // 命令行工具可能弹出 Playwright/微信窗口
    });

    let buffer = '';
    this.process.stdout?.on('data', (chunk: Buffer) => {
      buffer += chunk.toString('utf-8');
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';  // 保留最后一行未完整的
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event: CliEvent = JSON.parse(line);
          this.emit('event', event);
        } catch {
          // 非 JSON 行（调试输出），作为 log 事件转发
          this.emit('event', { event: 'log', level: 'DEBUG', message: line });
        }
      }
    });

    this.process.stderr?.on('data', (chunk: Buffer) => {
      // stderr 作为 error 级日志转发
      this.emit('event', {
        event: 'log',
        level: 'ERROR',
        message: chunk.toString('utf-8'),
      });
    });

    this.process.on('close', (code: number) => {
      this.emit('close', code);
    });
  }

  /** 激活 */
  async activate(code: string, serverUrl: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const proc = spawn(this.cliPath, [
        'activate', '--code', code, '--server-url', serverUrl,
      ]);
      let stdout = '';
      let stderr = '';
      proc.stdout.on('data', (c) => stdout += c);
      proc.stderr.on('data', (c) => stderr += c);
      proc.on('close', (code) => {
        if (code === 0) {
          try { resolve(JSON.parse(stdout)); }
          catch { reject(new Error('CLI 输出解析失败')); }
        } else {
          reject(new Error(stderr || `CLI 退出码 ${code}`));
        }
      });
    });
  }

  /** 查询余额 */
  async getCredits(): Promise<any> { /* spawn credits 命令 */ }

  /** 终止当前任务 */
  kill(): void {
    this.process?.kill();
  }
}
```

### 4.3 激活配置管理

`electron/config.ts`：使用 Electron `safeStorage` 加密存储 access_token（参考 `clients/agent-desktop/electron/credentials.ts`）。

```typescript
// electron/config.ts
import { app, safeStorage } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

interface ClientConfig {
  bindingId: string;
  accessToken: string;
  tenantId: string;
  tenantName: string;
  serverUrl: string;
  activatedAt: string;
}

const CONFIG_FILE = 'client-config.json';

function getConfigPath(): string {
  return path.join(app.getPath('userData'), CONFIG_FILE);
}

export function loadConfig(): ClientConfig | null {
  const file = getConfigPath();
  if (!fs.existsSync(file)) return null;
  const raw = fs.readFileSync(file, 'utf-8');
  const encrypted = JSON.parse(raw);
  // safeStorage 解密
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error('系统不支持加密存储');
  }
  return {
    ...encrypted,
    accessToken: safeStorage.decryptString(
      Buffer.from(encrypted.accessToken, 'base64')
    ),
  };
}

export function saveConfig(config: ClientConfig): void {
  const file = getConfigPath();
  const encrypted = {
    ...config,
    accessToken: safeStorage.encryptString(config.accessToken)
      .toString('base64'),
  };
  fs.writeFileSync(file, JSON.stringify(encrypted, null, 2));
}

export function clearConfig(): void {
  const file = getConfigPath();
  if (fs.existsSync(file)) fs.unlinkSync(file);
}
```

### 4.4 Renderer 页面设计

参考 `clients/association-enrichment-ui/static/index.html` 的三栏布局，重写为 Vue 组件。

#### 4.4.1 激活页（ActivationView.vue）

首次启动或配置丢失时显示：
- 输入框：激活码（格式 AC-XXXXXXXXXXXX）
- 输入框：服务端地址（预填默认值，如 `https://agent.aidingyi.cn`）
- 按钮：激活
- 激活成功后跳转主页

#### 4.4.2 主页（CollectView.vue）

```
┌──────────────────────────────────────────────────────────┐
│  协会信息收集助手                    积分余额：4500 [充值]  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  输入协会名称                                             │
│  ┌──────────────────────────────────────────┐ [开始收集] │
│  │ 中国黄金协会                              │            │
│  │ 中国机械工业协会                          │            │
│  │ ...（每行一个，或逗号分隔）                │            │
│  └──────────────────────────────────────────┘            │
│  或 [上传CSV/Excel]                                      │
│                                                          │
│  输出位置：C:\Users\xxx\Desktop\result.xlsx [选择]        │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  进度                                                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │ ● 中国黄金协会                       ████████ 100% │  │
│  │   ├─ 搜索基础信息 ✅                消耗 2.5 积分   │  │
│  │   ├─ 采集官网 ✅                    消耗 8.0 积分   │  │
│  │   ├─ 微信搜会长 ✅                  消耗 3.0 积分   │  │
│  │   └─ 微信取证 🔄会长手机号检索中...                 │  │
│  │                                                     │  │
│  │ ○ 中国机械工业协会                   等待中          │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  日志                                                     │
│  ┌────────────────────────────────────────────────────┐  │
│  │ [12:00:01] 开始收集 中国黄金协会                     │  │
│  │ [12:00:05] 搜索官网完成，找到 xxx.com               │  │
│  │ [12:00:10] 会长：张三                               │  │
│  │ [12:00:15] 微信检索会长手机号...                    │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**关键 UI 约束**：
- 积分余额实时更新（每个 billing 事件后刷新）
- 手机号在 UI 中脱敏显示（`1xx****xxxx`），明文只在 Excel 中
- 余额为 0 时禁用"开始收集"按钮，提示"积分不足，请联系服务商充值"
- 支持中途"停止"按钮（kill 子进程）

#### 4.4.3 结果展示（ResultView.vue）

任务完成后展示汇总：
- 成功 N 条、部分成功 M 条、失败 K 条
- 下载 Excel 按钮
- 每条协会的详细结果卡片（脱敏）

#### 4.4.4 积分消耗页（CreditsView.vue）

- 当前余额
- 今日/本周/总计消耗
- 消耗明细列表（从本地 SQLite 或服务端 `/api/client/v1/credits` 查询）

### 4.5 IPC 通道设计

```typescript
// electron/preload.cts 暴露的 API
contextBridge.exposeInMainWorld('associationClient', {
  config: {
    load: () => ipcRenderer.invoke('client:config:load'),
    save: (config) => ipcRenderer.invoke('client:config:save', config),
    clear: () => ipcRenderer.invoke('client:config:clear'),
  },
  cli: {
    activate: (code, serverUrl) => ipcRenderer.invoke('client:cli:activate', code, serverUrl),
    collect: (associations, outputPath, serverUrl) =>
      ipcRenderer.invoke('client:cli:collect', associations, outputPath, serverUrl),
    onEvent: (callback) => {
      ipcRenderer.on('client:cli:event', (_, event) => callback(event));
    },
    onClose: (callback) => {
      ipcRenderer.on('client:cli:close', (_, code) => callback(code));
    },
    kill: () => ipcRenderer.invoke('client:cli:kill'),
    getCredits: () => ipcRenderer.invoke('client:cli:getCredits'),
  },
  system: {
    openExternal: (url) => ipcRenderer.invoke('client:system:openExternal', url),
    saveFile: (data, defaultName) => ipcRenderer.invoke('client:system:saveFile', data, defaultName),
  },
});
```

### 4.6 安全设计

复用 `clients/agent-desktop/electron/security.ts` 的模式：
- `nodeIntegration: false`、`contextIsolation: true`、`sandbox: true`
- CSP：`default-src 'self'; connect-src 'self' {serverUrl}`
- IPC 白名单：只允许预定义的 channel
- `safeStorage` 加密 access_token
- 不暴露原始 `ipcRenderer` 给 renderer

### 4.7 打包配置（electron-builder）

```yaml
# electron-builder.yml
appId: cn.aidingyi.association.client
productName: 协会信息收集助手
asar: true
directories:
  output: release
files:
  - dist/electron/**/*
  - dist/renderer/**/*
  - package.json
extraResources:
  - from: ../association-client-cli/dist/
    to: cli/
    filter: ["**/*"]
win:
  target:
    - target: nsis
      arch: [x64]
nsis:
  oneClick: false
  perMachine: false
  allowToChangeInstallationDirectory: true
  createDesktopShortcut: true
  createStartMenuShortcut: true
```

> **关键**：PyInstaller 打包的命令行工具作为 `extraResources` 打入 Electron 安装包，运行时通过 `process.resourcesPath/cli/association-cli.exe` 访问。

---

## 5. 数据流详解

### 5.1 激活流程

```
1. 用户安装客户端 → 首次启动 → 检测无配置 → 显示激活页
2. 用户输入激活码 AC-XXXXXXXXXXXX + 服务端地址
3. Electron main → spawn cli.exe activate --code AC-... --server-url https://...
4. CLI 采集 machine_id → POST /api/client/v1/activate
5. 服务端校验激活码 → 生成 binding + access_token → 返回
6. CLI stdout 输出 JSON → Electron 解析 → safeStorage 加密存储 → 跳转主页
```

### 5.2 收集流程（以单个协会为例）

> **2026-08-06 重构后流水线变为 5 步**（原第3步"网络兜底"删除，新增第3步"微信搜领导姓名"）。

```
1. 用户输入"中国黄金协会" → 点击"开始收集"
2. Electron → spawn cli.exe collect --associations "中国黄金协会" --output ...
3. CLI 启动 AssociationBatchEnricher.enrich_one("中国黄金协会")
4. 步骤1：search_profile（DeepSeek 提取基础信息）
   - ProxyLLMGateway.chat() → POST /api/client/v1/llm/chat
     → 服务端 llm_gateway(DeepSeek) → 扣积分（×5）→ 返回
   - 获取地址/邮箱/官网URL/主管单位等基础字段（不含人员/手机号）
   - stdout: {"event":"progress","step":"search_profile","status":"success"}
5. 步骤2：collect_official_profile（官网采集）
   - 用第1步 search_profile 返回的 official_website
   - Playwright 启动可见 Chromium → 采集官网页面（override=true 覆盖搜索结果）
   - extract_association_profile → ProxyLLMGateway.chat(抽取14字段)
     → POST /api/client/v1/llm/chat → 扣积分 → 返回
   - stdout: {"event":"progress","step":"official_profile","status":"success"}
6. 步骤3：wechat_search_leader_name（微信搜领导姓名，若会长/秘书长仍空）
   - spawn powershell.exe wechat-souyisou.ps1 -Command search ...
   - PowerShell 操作微信搜一搜 → 复制列表文本 → read-artifact.ps1 读取
   - ProxyLLMGateway.chat(解析姓名) → POST /api/client/v1/llm/chat → 扣积分 → 返回
   - stdout: {"event":"progress","step":"wechat_search_leader","status":"success"}
7. 步骤4：wechat_mobile（微信RPA取证手机号，若有会长/秘书长姓名）
   - spawn powershell.exe wechat-souyisou.ps1 -Command collect -UseProjectLlm ...
   - PowerShell 操作微信 → judge 调 llm_judge.py → ProxyLLMGateway.chat
     → POST /api/client/v1/llm/chat → 扣积分 → 返回
   - OCR 调 ocr_adapter.py → POST /api/client/v1/ocr/parse
   - stdout: {"event":"progress","step":"wechat_mobile","status":"success"}
8. CLI 写入本地 SQLite + result.xlsx
9. stdout: {"event":"complete","total_consumed":15.5}
10. Electron 收到 complete → 刷新积分 → 展示结果
```

### 5.3 计费数据流（关键）

```
客户端 ProxyLLMGateway.chat()
    │
    ▼ POST /api/client/v1/llm/chat {messages, ...}
服务端 verify_client_token(access_token)
    │
    ▼ binding = ClientBinding(...)
服务端 检查 binding.tenant.credit_balance
    │
    ├─ <= 0 → 返回 402 NO_CREDIT → 客户端 NoCreditError → 停止任务
    │
    ▼
服务端 llm_gateway.chat(messages, ...) → 模型提供商
    │
    ▼ response.usage = {prompt_tokens, completion_tokens, ...}
服务端 calculate_credit_cost(usage, model) → raw_credit
服务端 credit_cost = ceil(raw_credit * 5 * 100) / 100
    │
    ▼ 同事务：
    INSERT client_usage_logs (raw_credit_cost, credit_cost, ...)
    UPDATE tenants SET credit_balance -= credit_cost
    │
    ▼ 返回 {content, usage, billing: {raw_credit_cost, credit_cost, balance_after}}
客户端 收到 billing 事件 → stdout 输出 → Electron 更新 UI
```

---

## 6. 关键约束与风险

### 6.1 强制约束（必须遵守）

| # | 约束 | 理由 |
|---|------|------|
| C1 | 微信 PC 版必须在客户端机器运行且已登录 | RPA 操作已登录的微信进程 |
| C2 | Playwright 必须用可见窗口（headless=False） | 现有代码强制；反爬需要 |
| C3 | 同一时刻只允许一个收集任务 | RPA 会占用微信前台；`AssociationBatchEnricher` 串行执行 |
| C4 | 所有 stdout 输出手机号必须脱敏 | 防止明文进入日志 |
| C5 | access_token 用 safeStorage 加密存储 | 安全基线 |
| C6 | 5倍系数在服务端计算，客户端不可篡改 | 计费可信 |
| C7 | 余额 ≤ 0 时服务端拒绝 LLM 调用，客户端必须停止 | 硬阻断 |
| C8 | OCR 不收费（credit_cost=0），但记录调用次数；联网搜索已合并进 LLM 调用（计费） | 成本由服务端承担（OCR）；搜索随 LLM 计费 |
| C9 | Playwright Chromium 由客户预装，不打包进 exe | 包体控制；CLI 启动检测缺失则 fail-loud 提示 |
| C10 | 客户安装微信后须关闭自动更新 | 防止微信自动升级到未验证版本导致 RPA 失效（见 §6.2） |

### 6.2 已知风险与缓解

| 风险 | 影响 | 缓解方案 |
|------|------|----------|
| **客户未预装 Playwright Chromium** | 官网采集步骤无法执行 | CLI 启动检测 `%LOCALAPPDATA%\ms-playwright\chromium-*`，缺失则 fail-loud 提示安装命令；交付手册写明预装步骤；客户端首次启动引导运行 `install-playwright.cmd` |
| **微信自动更新导致 RPA 失效** | UIA 定位路径变化，取证失败 | 交付手册明确要求：客户安装微信最新版后**关闭自动更新**（微信设置 → 关于 → 关闭自动下载安装包）。当前已验证基线为微信 4.x，RPA 代码对版本变化有容错（返回 inconclusive 不崩溃），但关闭更新是最稳妥保障 |
| **DPAPI 绑定 Windows 用户** | 换用户无法解密历史结果 | 文档注明：结果 Excel 是主交付物，DPAPI 数据仅供本机复盘 |
| **网络不稳定** | LLM 代理调用失败 | CLI 内置重试（3次，指数退避） |
| **激活码泄露** | 被他人激活 | max_uses=1 + expires_at；激活后码作废 |
| **下周一交付时间紧** | 功能未完全验证 | Playwright 不打包后最高风险已消除，严格按开发计划 Phase 0-3 执行 |

---

## 7. 配置项汇总

### 7.1 服务端配置（新增）

```python
# src/config/settings.py 新增
class ClientConfig(BaseModel):
    credit_multiplier: float = 5.0
    llm_request_timeout: int = 120
    max_image_size_mb: int = 10

class Settings(BaseModel):
    ...
    client: ClientConfig = ClientConfig()
```

### 7.2 服务端 CORS 配置

服务端必须允许 Electron 客户端的 Origin。由于客户端走 CLI→HTTP（非浏览器直连），CLI 用 httpx 无 CORS 限制。**但 Electron renderer 如果直接调服务端（如积分查询），需要 CORS**。

MVP 方案：积分查询也通过 CLI 转发（`cli.exe credits`），避免 renderer 直接调服务端。Phase 2 可开放 renderer 直连。

### 7.3 客户端配置

```json
// %APPDATA%\association-client\client-config.json（safeStorage 加密后）
{
  "bindingId": "cb_xxx",
  "accessToken": "<encrypted>",
  "tenantId": "tenant_xxx",
  "tenantName": "中国黄金协会",
  "serverUrl": "https://agent.aidingyi.cn",
  "activatedAt": "2026-08-06T12:00:00Z"
}
```

### 7.4 CLI 运行时配置

```json
// %APPDATA%\association-client\cli-config.json
{
  "serverUrl": "https://agent.aidingyi.cn",
  "accessToken": "xxx",
  "outputDir": "C:\\Users\\xxx\\Documents\\协会收集结果",
  "logLevel": "INFO"
}
```

---

## 8. 文件清单（需新建/修改）

### 8.1 服务端新建

| 文件 | 用途 |
|------|------|
| `deploy/db_update.sql`（追加） | 3 张新表 DDL |
| `src/api/client_auth.py` | `verify_client_token` 中间件 |
| `src/api/client_routes.py` | 客户端 API 路由（激活/积分/LLM含enable_search/OCR/日志） |
| `src/api/client_llm_proxy.py` | LLM 代理逻辑（计费 ×5） |
| `src/saas/api/client_activation_mgmt.py` | 管理端：生成/管理激活码 |
| `src/db/client_binding_db.py` | `client_bindings` / `client_activation_codes` DB 访问层 |
| `src/db/client_usage_db.py` | `client_usage_logs` DB 访问层 |
| `frontend/src/components/tenant/ClientActivationManager.vue` | ★ 后台「租户管理-编辑租户」内的激活码管理组件 |
| `tests/unit/api/test_client_routes.py` | 单测 |
| `tests/unit/api/test_client_llm_proxy.py` | 计费单测（验证 ×5） |

### 8.2 服务端修改

| 文件 | 改动 |
|------|------|
| `src/main.py` | 注册 `/api/client/v1` 路由 + `/api/saas/client-activations` 管理路由 |
| `src/config/settings.py` | 新增 `ClientConfig` |
| `src/services/association_enrichment_providers.py` | ⚠️ **gateway 注入改造（见 §3.3）**：`_strict_json_chat` 静态方法→实例方法用 `self._gateway`；`search_profile` 的模块级 `llm_gateway` 改为 `self._gateway`。`__init__` 新增 `gateway` 参数（默认 None，向后兼容） |
| `frontend/src/views/.../TenantMgmt.vue`（或对应租户编辑弹窗） | 挂载 `ClientActivationManager.vue` 组件 |

### 8.3 客户端 CLI 新建

| 文件 | 用途 |
|------|------|
| `clients/association-client-cli/` | 整个工程 |
| `clients/association-client-cli/main.py` | CLI 入口（activate/credits/collect） |
| `clients/association-client-cli/runtime/proxy_gateway.py` | ProxyLLMGateway（含 `enable_search` 透传） |
| `clients/association-client-cli/runtime/progress_reporter.py` | NDJSON 进度输出 |
| `clients/association-client-cli/runtime/local_db.py` | 本地 SQLite（复用 EncryptedAuditStore） |
| `clients/association-client-cli/runtime/powershell_runner.py` | PowerShell spawn 封装 |
| `clients/association-client-cli/runtime/playwright_check.py` | ★ Playwright Chromium 检测 |
| `clients/association-client-cli/scripts/*.ps1` | 从 wechat-souyisou-rpa 复制并改造 |
| `clients/association-client-cli/scripts/llm_judge.py` | 改造为走代理 |
| `clients/association-client-cli/scripts/ocr_adapter.py` | 改造为走代理 |
| `clients/association-client-cli/install-playwright.cmd` | ★ Playwright 预装引导脚本（随安装包分发） |
| `clients/association-client-cli/build.spec` | PyInstaller 配置 |

### 8.4 Electron 客户端新建

| 文件 | 用途 |
|------|------|
| `clients/association-client/` | 整个工程 |
| `clients/association-client/electron/*` | 主进程、preload、安全 |
| `clients/association-client/src/*` | Vue renderer |
| `clients/association-client/electron-builder.yml` | 打包配置 |

---

## 9. 接口契约速查表

### 9.1 CLI stdout NDJSON 事件

| event | 必填字段 | 可选字段 |
|-------|----------|----------|
| `start` | session_id, associations[], timestamp | server_url |
| `progress` | association, step, status, progress, timestamp | message, detail |
| `billing` | association, stage, raw_credit_cost, credit_cost, balance_after, timestamp | model, prompt_tokens, completion_tokens |
| `log` | level, message, timestamp | association, detail |
| `error` | association, error_code, message, timestamp | stage, session_fatal |
| `complete` | session_id, total_consumed, timestamp | output, summary |

### 9.2 服务端 API 端点

| 方法 | 路径 | 鉴权 | 用途 |
|------|------|------|------|
| POST | `/api/client/v1/activate` | 无 | 激活码激活 |
| GET | `/api/client/v1/credits` | Bearer | 积分查询 |
| POST | `/api/client/v1/llm/chat` | Bearer | LLM 代理（计费，DeepSeek 普通对话） |
| POST | `/api/client/v1/ocr/parse` | Bearer | OCR 代理 |
| ~~POST~~ | ~~`/api/client/v1/websearch`~~ | ~~Bearer~~ | ~~已废弃，合并进 llm/chat~~ |
| POST | `/api/client/v1/logs` | Bearer | 日志上报 |
| POST | `/api/saas/client-activations` | platform_admin | 生成激活码 |
| GET | `/api/saas/client-activations/list` | platform_admin | 列出激活码 |

### 9.3 计费公式

```
raw_credit_cost = calculate_credit_cost(usage, model)
                = ceil((prompt_tokens × input_price_per_m
                       + completion_tokens × output_price_per_m
                       + cached_tokens × cached_input_price_per_m) / 1_000_000
                      × usage_factor(100) × 100) / 100

credit_cost（客户端实扣） = ceil(raw_credit_cost × 5 × 100) / 100
```

> `usage_factor` 默认 100，`credit_multiplier`（客户端系数）默认 5。两者都在服务端 `settings` 配置。

---

## 10. 不在本次范围内（Phase 2+）

- 在线支付/自助充值（客户端内直接充值）
- 客户端自动更新（electron-updater）
- macOS 支持
- 多客户端负载（同一租户多台电脑同时运行）
- 服务端结果留存（客户端结果上报）
- 机器码强制校验（防换机）
- 客户端 renderer 直连服务端（当前全部走 CLI 中转）

---

*文档结束。开发计划见 [association-client-dev-plan.md](association-client-dev-plan.md)。*
