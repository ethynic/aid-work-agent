# M0.3 实施规格：云端本地工具基础设施

> 关联：[plan-recruiting-cli-agent-integration.md](plan-recruiting-cli-agent-integration.md) M0.3；上位设计：[recruiting-cli-agent-integration-design.md](../../design/recruiting/recruiting-cli-agent-integration-design.md) §6-§8、§12。
>
> 本文件是 M0.3 开发的具体实施决策。router.py / local_proxy_tool.py（Agent 路由与代理工具）属 M0.5，本阶段不实现。

## 1. 环境事实（已勘察）

- 本机无 docker 容器、无本地 PG 监听 5432，但 `tests/integration/test_billing_balance_api.py` 4 passed —— 说明测试实际连的 DATABASE_URL 可用（dev 机器环境变量）。集成测试模式：**真实 DB + 不可用时 pytest.skip**（参照该文件 fixture）。
- DB 访问：`from src.db.database import get_db_connection`（contextmanager，psycopg2，raw SQL `%s` 参数化）。
- 用户认证：`from src.api.auth import get_current_user`（Bearer token → user dict，带 Redis 缓存）。
- 租户解析：`/api/local-tools/*` 走 middleware `_resolve_user_tenant`（其他 /api/* 分支），Web API 从 `request.state.tenant_id` 取。**先验证 middleware 对该路径不 401 放行到端点**；Runtime API 用设备 token 自认证，不依赖 middleware 的 tenant。
- 异步规范：endpoint 调同步 psycopg2 必须 `await asyncio.to_thread(...)`。
- 日志 loguru；错误响应 `{success: False, error, debug: sanitize_error_info(...)}`（sanitize 函数参照 backend_dev.md 模式，可复用现有实现——先 grep `sanitize_error_info` 是否已有公共实现，有则复用）。
- router 注册：src/main.py 尾部 import + `app.include_router(...)`。

## 2. 数据库（系统表，非 bs_）

四表都是**系统表**（Agent 运行基础设施，类比 tenants/subscriptions），不带 bs_ 前缀，但都含 tenant_id/user_id 做隔离。`deploy/init-postgres.sql` 与 `deploy/db_update.sql` **同步**添加（db_update.sql 条目带日期注释 `2026-08-10`）。

### local_tool_devices
```sql
CREATE TABLE IF NOT EXISTS local_tool_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    name TEXT,
    platform TEXT,
    runtime_version TEXT,
    token_hash TEXT UNIQUE NOT NULL,
    machine_fingerprint_hash TEXT,
    capabilities_json JSONB,
    manifest_digest TEXT,
    selected BOOLEAN DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',   -- active / revoked
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_devices_tenant_user ON local_tool_devices(tenant_id, user_id);
```
### local_tool_pairing_tickets
```sql
CREATE TABLE IF NOT EXISTS local_tool_pairing_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    code_hash TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
### local_tool_invocations
```sql
CREATE TABLE IF NOT EXISTS local_tool_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    -- queued/claimed/running/succeeded/failed/cancel_requested/cancelled/unknown/expired
    effect TEXT,                              -- none/applied/partial/unknown，终态时填
    claim_token_hash TEXT,
    lease_expires_at TIMESTAMP,
    result_json JSONB,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lt_inv_device_state ON local_tool_invocations(device_id, state);
CREATE INDEX IF NOT EXISTS idx_lt_inv_tenant_user ON local_tool_invocations(tenant_id, user_id);
```
### local_tool_events
```sql
CREATE TABLE IF NOT EXISTS local_tool_events (
    id BIGSERIAL PRIMARY KEY,
    invocation_id UUID NOT NULL,
    tenant_id TEXT NOT NULL,
    seq INT NOT NULL,
    stage TEXT,
    current INT,
    total INT,
    message TEXT,                             -- 脱敏后进度文案
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (invocation_id, seq)
);
```

## 3. src/local_tools/ 模块

```
src/local_tools/
├─ __init__.py            # 懒加载 __getattr__（遵守包初始化副作用规范，禁止顶层实例化）
├─ models.py              # Pydantic：PairRequest/HeartbeatRequest/ClaimResponse/ProgressRequest/ResultRequest/InvocationView/DeviceView
├─ security.py            # 配对码生成(8 位大写字母+数字，去易混淆 0/O/1/I)、token 生成(secrets.token_hex(32))、sha256 哈希工具
├─ repository.py          # 同步 DB 访问：devices/tickets/invocations/events 全部 CRUD + claim + 终态幂等
├─ pairing.py             # 配对业务：创建 ticket、code 换 device+token、撤销设备
├─ catalog.py             # 受信 Provider manifest 注册表（MVP 静态内置 boss-recruiting）
└─ api.py                 # APIRouter：Web 用户 API + Runtime API
```

### repository.py 关键方法
- `create_ticket(tenant_id, user_id, code_hash, expires_at)`
- `consume_ticket(code_hash) -> ticket | None`：`UPDATE ... SET used_at=NOW() WHERE code_hash=%s AND used_at IS NULL AND expires_at > NOW() RETURNING ...`（原子单次消费）
- `create_device(...) / get_device_by_token_hash(hash) / list_devices(tenant_id, user_id) / revoke_device(tenant_id, user_id, device_id) / touch_device_seen(device_id, version, capabilities, manifest_digest)`
- `select_device(tenant_id, user_id, device_id)`：事务内 `UPDATE ... SET selected=FALSE WHERE tenant/user` + `UPDATE ... SET selected=TRUE WHERE id=%s AND status='active'`（单选）
- `create_invocation(tenant_id, user_id, device_id, tool_name, arguments) -> id`（M0.5 用，本阶段测试用）
- `claim_next(device_id, tenant_id, claim_token_hash, lease_seconds) -> invocation | None`：**事务** `SELECT ... FOR UPDATE SKIP LOCKED`（`state='queued' AND device_id=%s AND tenant_id=%s ORDER BY created_at LIMIT 1`）→ `UPDATE state='claimed', claim_token_hash, lease_expires_at, claimed_at` → commit
- `mark_started(invocation_id, tenant_id, claim_token_hash)`：仅 `state='claimed'` 且 hash 匹配 → running；否则返回当前状态（幂等/冲突由 API 层判 409）
- `append_event(invocation_id, tenant_id, claim_token_hash, stage, current, total, message, lease_seconds) -> (seq, cancel_flag) | None`：校验 hash + state in (claimed,running)，seq = COALESCE(MAX(seq),0)+1，同时续租 lease_expires_at；返回 invocation.state == 'cancel_requested' 作为 cancel_flag
- `write_result(invocation_id, tenant_id, claim_token_hash, result)`：hash 匹配时——若已终态**幂等返回当前记录**（不报错）；否则 `UPDATE state/error/effect/result_json/finished_at`。state 映射：success→succeeded；code='EXECUTION_UNKNOWN'→unknown；其余失败→failed
- `request_cancel(invocation_id, tenant_id)`（M0.5 用）：queued → **cancelled**（终态，effect=none；设备永远不会领取，直接落终态，避免行永久卡在 cancel_requested）；claimed/running → cancel_requested（等设备在 progress 响应里感知 cancel=true 后写终态）
- `expire_stale_claims()`：lease 过期的 claimed/running → state='unknown', effect='unknown'（供 claim 路径顺带调用，MVP 无后台任务）
- 所有查询/更新**必带 tenant_id**（claim 同时带 device_id）。

### pairing.py
- `create_pairing_ticket(tenant_id, user_id) -> {code, expires_at}`：明文 code 只返回这一次，库存 hash。同一用户未使用的旧 ticket 作废止（UPDATE 全部 used_at=NOW() 防止多码并存）。
- `pair(code, name, platform, runtime_version, capabilities, fingerprint) -> {device_id, device_token}`：consume_ticket 成功才建设备；token 明文只返回这一次。
- Web access token 与 device token 完全隔离：device token 只能调 `/api/local-tools/runtime/*`（api.py 内依赖实现，不给其它端点用）。

### catalog.py
```python
TRUSTED_PROVIDERS = {
  "boss-recruiting": {
    "provider_id": "ai.aidwork.boss-recruiting",
    "min_provider_version": "1.0.0",
    "execution_target": "local_required",
    "tools": ["boss_filter","boss_clear_filter","boss_goto","boss_greet","boss_accept_resume","boss_reject_current","boss_interview_demo"],
    # schema_digest 由 M0.5 接 LLM schema 时校验；MVP claim 只校验 tool_name ∈ tools
  }
}
```
claim 时校验 invocation.tool_name 在该设备 Provider 的 tools 内（设备 capabilities_json 含 provider_id）。

## 4. API（api.py，`APIRouter(prefix="/api/local-tools", tags=["local-tools"])`）

### Web 用户 API（get_current_user + request.state.tenant_id，401/400 中文文案）
| 路由 | 说明 |
|---|---|
| `POST /pairing-tickets` | 创建配对码 → `{success, code, expires_at}`（code 仅此一次） |
| `GET /devices` | 当前 tenant+user 设备列表 + `online`（last_seen_at 距今 ≤30s）+ `selected` |
| `POST /devices/{device_id}/select` | 选定设备（校验归属与 active） |
| `DELETE /devices/{device_id}` | 撤销（status=revoked；selected 清除） |

### Runtime API（`Authorization: Bearer <device_token>` 自定义依赖 `_require_device`）
依赖：sha256(token) 查 active 设备 → 把 `device` 注入；所有端点用 device.tenant_id 做隔离键，**不信任何请求体里的 tenant/user**。
| 路由 | 说明 |
|---|---|
| `POST /runtime/pair` | 无设备 token，body `{code, name, platform, runtime_version, capabilities, machine_fingerprint}` → `{device_id, device_token}`（明文仅此一次） |
| `POST /runtime/heartbeat` | 更新 last_seen/version/capabilities/manifest_digest → `{selected, server_time}` |
| `POST /runtime/claim?wait=20` | 长轮询：async 循环（每 0.5s `asyncio.to_thread(claim_next)` + `asyncio.sleep`）直到拿到或超时；拿到 → `{invocation_id, tool_name, arguments, claim_token, lease_expires_at, provider}`；超时 → `{invocation: null}`（200，不用 204，简化客户端）。claim_token 明文仅此处返回 |
| `POST /runtime/invocations/{id}/started` | body `{claim_token}` → `{state}` |
| `POST /runtime/invocations/{id}/progress` | body `{claim_token, stage, current, total, message}` → `{seq, cancel}`；message 服务端截断 500 字符 |
| `POST /runtime/invocations/{id}/result` | body `{claim_token, success, code, message, effect, data, retryable}` → 幂等 `{state, effect}` |

所有 endpoint：`await asyncio.to_thread(...)` 包同步 repository；loguru 日志（不含 token/code 明文）；错误响应带 sanitize 后 debug。

### main.py 注册
尾部 `from src.local_tools import api as local_tools_api` + `app.include_router(local_tools_api.router)`。

## 5. 测试

`tests/unit/local_tools/`（mock get_db_connection）：
- security：code 字符集/长度、token 长度、hash 稳定。
- 状态机：write_result 幂等（已终态重复写返回原状态）、started 非法状态不迁移、claim token 不匹配拒绝。
- pairing：过期/已用 code 不可消费。

`tests/integration/test_local_tools_api.py`（真实 DB，不可用 skip；TestClient）：
- 全链路：建 ticket → pair → heartbeat → create_invocation（直接调 repository）→ claim 领取 → started → progress×2（seq 递增、cancel=false）→ result（succeeded）→ 重复 result 幂等。
- 配对码单次/过期；撤销设备后 token 失效（401）。
- 租户隔离：另一 tenant 的设备 claim 不到本 tenant invocation；Web API 拿不到别的 tenant 设备。
- 并发 claim：两个线程同时 claim 同一 device 的多条 invocation，无重复领取（SKIP LOCKED）。
- 取消：request_cancel 后 progress 返回 cancel=true。
- 跨连接可见性：worker A 写入、worker B 读取一致（两个独立连接模拟多 worker）。

## 6. 不做（本阶段）

- router.py / local_proxy_tool.py（M0.5）。
- WSS/Redis transport。
- Web 前端页面（M0.6）。
- manifest schema_digest 强校验（M0.5 接 LLM schema 时）。
