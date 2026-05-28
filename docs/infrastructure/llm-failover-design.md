# LLM Failover 设计文档

## 1. 问题背景

当前系统的 `LLMGateway` 在初始化时绑定单一 LLM 提供者（如 zhipu、qwen、deepseek），运行期间无法切换。如果当前提供者的 API 出现故障（服务端 500、网络超时、限流 429 等），LLM 调用直接抛异常，Agent 循环终止，用户收到错误或无响应。

**现状**：
- `LLMGateway` 绑定单一 provider，无重试，无故障转移
- Provider 实例每次调用都新建（`_build_provider`），轻量无状态
- `KeyPool` 仅解决多 Key 轮询和并发控制，不解决跨 provider 故障
- 错误直接向上传播到 `agent.py`，Agent 循环捕获后终止

**目标**：当主 provider 调用失败时，自动切换到备用 provider，对用户无感知。

## 2. 设计方案

### 2.1 核心思路：Provider Chain + 自动 Failover

在 `LLMGateway` 内部维护一个 **provider 优先级链**，主 provider 失败后按顺序尝试备用 provider。

```
请求 → LLMGateway
        ├─ 1. 尝试主 provider (zhipu)
        │   ├─ 成功 → 返回结果
        │   └─ 失败 → 记录故障
        ├─ 2. 尝试备用 provider (deepseek)
        │   ├─ 成功 → 返回结果
        │   └─ 失败 → 记录故障
        └─ 3. 全部失败 → 抛出最后一个异常
```

### 2.2 故障判定规则

不是所有错误都触发 failover。根据错误类型分类：

| 错误类型 | 是否触发 failover | 说明 |
|---------|-----------------|------|
| `asyncio.TimeoutError` | 是 | 服务端无响应或响应慢 |
| HTTP 5xx（服务端错误） | 是 | 服务端故障 |
| HTTP 429（限流） | 是 | 所有 Key 都被限流 |
| 网络错误（`httpx.ConnectError`） | 是 | DNS 解析失败、连接被拒 |
| HTTP 4xx（客户端错误，非 429） | **否** | 请求本身有问题（参数错误、token 超限），换 provider 也会失败 |
| `KeyPool` 超时（所有 Key 占满） | 是 | 可能该 provider 整体负载过高 |

### 2.3 熔断机制（Circuit Breaker）

防止频繁尝试已经挂掉的 provider：

- **关闭状态（Closed）**：正常调用
- **打开状态（Open）**：直接跳过该 provider，不尝试调用。持续 `recovery_timeout`（默认 60s）后进入半开
- **半开状态（Half-Open）**：放一个请求试探。成功则关闭，失败则继续打开

每个 provider 维护独立的熔断器：

```python
class CircuitBreaker:
    def __init__(self, failure_threshold=3, recovery_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold  # 连续失败 3 次触发熔断
        self.recovery_timeout = recovery_timeout     # 60s 后尝试恢复
        self.state = "closed"  # closed / open / half_open
        self.last_failure_time = 0
```

### 2.4 配置设计

在 `configs/config.yaml` 的 `llm` 段增加 failover 配置：

```yaml
llm:
  provider: zhipu                    # 主 provider
  failover:
    enabled: true                     # 是否启用 failover
    providers:                        # failover 优先级链（不含主 provider）
      - deepseek
      - qwen
    retry:
      max_retries: 2                  # 同一 provider 内重试次数（含首次调用）
      retry_delay: 1.0               # 重试间隔（秒），指数退避基数
    circuit_breaker:
      failure_threshold: 3            # 连续失败 N 次触发熔断
      recovery_timeout: 60            # 熔断恢复时间（秒）
```

### 2.5 类设计

```
src/llm/
├── gateway.py              # 修改：集成 FailoverGateway
├── failover.py             # 新增：FailoverGateway + CircuitBreaker
├── key_pool.py             # 不变
├── llm_call_logger.py      # 不变
└── providers/              # 不变
```

#### `failover.py` 核心类

```python
class CircuitBreaker:
    """单 provider 的熔断器"""
    state: str              # "closed" | "open" | "half_open"
    failure_count: int
    last_failure_time: float
    failure_threshold: int  # 默认 3
    recovery_timeout: float # 默认 60s

    def record_success(self): ...
    def record_failure(self): ...
    def can_attempt(self) -> bool: ...


class ProviderSlot:
    """一个 provider 的完整槽位（KeyPool + CircuitBreaker）"""
    provider_name: str
    key_pool: KeyPool
    circuit_breaker: CircuitBreaker

    def is_available(self) -> bool: ...  # 熔断器允许 + KeyPool 有配置


class FailoverGateway:
    """带故障转移的 LLM 网关"""
    primary: ProviderSlot
    fallbacks: List[ProviderSlot]

    async def _call_with_failover(self, fn_name, **kwargs) -> Any:
        """按优先级尝试各 provider"""
        for slot in self._get_provider_chain():
            if not slot.is_available():
                continue
            try:
                result = await self._call_slot(slot, fn_name, **kwargs)
                slot.circuit_breaker.record_success()
                return result
            except RetryableError:
                slot.circuit_breaker.record_failure()
                logger.warning(f"Provider [{slot.provider_name}] failed, trying next...")
                continue
        raise LLMAllProvidersFailedError(...)

    async def _stream_with_failover(self, fn_name, **kwargs) -> AsyncGenerator:
        """流式调用的 failover（仅对连接阶段做 failover，流开始后不切换）"""
        ...
```

### 2.6 流式调用的特殊处理

流式调用（`stream_chat`）的 failover 有特殊限制：

- **连接阶段**：可以 failover。如果 SSE 连接建立失败（HTTP 错误、连接超时），尝试下一个 provider
- **流开始后**：不能 failover。已经开始向客户端发送 chunk，无法撤回。此时错误直接抛出，由上层（Agent → SSE 推送）处理

```python
async def _stream_with_failover(self, fn_name, **kwargs):
    for slot in self._get_provider_chain():
        if not slot.is_available():
            continue
        try:
            # 尝试建立流连接并获取第一个 chunk
            stream = self._start_stream(slot, fn_name, **kwargs)
            first_chunk = await stream.__anext__()  # 连接验证
            slot.circuit_breaker.record_success()
            # 返回生成器：先 yield 第一个 chunk，再继续流
            yield first_chunk
            async for chunk in stream:
                yield chunk
            return  # 流正常结束
        except RetryableError:
            slot.circuit_breaker.record_failure()
            continue
    raise LLMAllProvidersFailedError(...)
```

### 2.7 错误分类

```python
def _is_retryable_error(error: Exception) -> bool:
    """判断错误是否值得尝试其他 provider"""
    if isinstance(error, asyncio.TimeoutError):
        return True
    if isinstance(error, httpx.ConnectError):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        return code >= 500 or code == 429
    if isinstance(error, RuntimeError):
        # Provider 层将 HTTPStatusError 包装为 RuntimeError
        msg = str(error).lower()
        if any(kw in msg for kw in ["500", "502", "503", "429", "timeout"]):
            return True
    return False
```

### 2.8 Gateway 改造方式

**保持 `LLMGateway` 的公共接口完全不变**，内部根据配置决定是否使用 failover：

```python
class LLMGateway:
    def __init__(self, provider_name=None):
        self.provider_name = provider_name or settings.llm.provider
        if self._failover_enabled():
            self._failover = FailoverGateway(self.provider_name)
        else:
            self._key_pool = _build_key_pool(self.provider_name)

    async def chat(self, messages, tools=None, ...):
        if self._failover_enabled():
            return await self._failover.chat(messages, tools, ...)
        return await self._call_with_pool("chat", messages=messages, ...)

    async def stream_chat(self, messages, tools=None, ...):
        if self._failover_enabled():
            async for chunk in self._failover.stream_chat(messages, tools, ...):
                yield chunk
        else:
            async for chunk in self._stream_with_pool("stream_chat", ...):
                yield chunk
```

**对外接口零改动**：`chat()`、`stream_chat()`、`chat_with_tools()` 的签名和返回值完全不变。`agent.py` 和其他调用方不需要任何修改。

### 2.9 日志与监控

#### 2.9.1 复用现有 LLM 调用日志

系统已有 `src/llm/llm_call_logger.py` 的 `log_llm_invoke()` 机制，按天记录 JSONL 文件（`log/llm/llm_invoke_logs_YYYYMMDD.jsonl`），每条记录包含 `request_id`、`provider`、`model`、`duration_ms`、`error` 等字段。

**Failover 完全复用此机制**，无需新增日志文件。因为：

1. **Provider 层不变**：每个 provider（`ZhipuProvider`、`DeepSeekProvider`）内部已经调用 `log_llm_invoke()` 记录每次 API 调用
2. **Failover 只是多调用了一次**：主 provider 失败时，备用 provider 的调用同样经过 Provider 层，自动产生一条日志记录，`provider` 字段不同
3. **Failover 事件本身额外记录一条**：在 FailoverGateway 中，每次触发 failover 切换时调用 `log_llm_invoke()` 记录切换事件，标记 `type: "failover"`，便于事后分析

日志示例（同一次用户请求，failover 触发后产生 2 条记录）：

```jsonl
{"request_id":"abc123","provider":"zhipu","model":"glm-4","error":"HTTP 503: Service Unavailable","duration_ms":1205}
{"request_id":"abc123","provider":"deepseek","model":"deepseek-chat","response":{"content":"..."},"duration_ms":850}
{"request_id":"abc123","type":"failover","from_provider":"zhipu","to_provider":"deepseek","reason":"HTTP 503","duration_ms":2055}
```

#### 2.9.2 loguru 实时日志

每次 failover 事件通过 `loguru` 记录 `logger.warning`，输出到标准日志流：

```
WARNING | [Failover] zhipu → deepseek, reason: HTTP 503 Service Unavailable, attempt 1/3
WARNING | [CircuitBreaker] zhipu 连续失败 3 次，熔断 60s
INFO    | [CircuitBreaker] zhipu 熔断恢复，重新启用
```

#### 2.9.3 告警机制

Failover 事件需要通知运维人员，复用现有 `src/services/notification_service.py`。

**告警触发规则**：

| 事件 | 告警级别 | 说明 |
|------|---------|------|
| 主 provider 熔断 | `high` | 主 LLM 服务不可用，所有请求走备用 |
| 全部 provider 失败 | `critical` | 所有 LLM 服务不可用，系统无法响应 |
| 主 provider 熔断恢复 | `low` | 主 LLM 服务恢复正常（信息通知，可选关闭） |

**告警去重**：同一 provider 在熔断期间只发一次告警，不重复发送。熔断恢复后再发一次恢复通知。

**告警配置**（在 failover 配置段扩展）：

```yaml
llm:
  failover:
    # ... 其他配置 ...
    alert:
      enabled: true                    # 是否启用告警
      channel: webhook                 # 告警渠道：webhook / email
      cooldown: 300                    # 同一 provider 告警冷却时间（秒），防止告警轰炸
```

**告警内容示例**：

```
标题: [HIGH] LLM 主服务熔断 - zhipu
内容: 主 LLM 提供者 zhipu 连续失败 3 次，已自动切换到 deepseek。
      失败原因: HTTP 503 Service Unavailable
      当前状态: zhipu 熔断中（预计 60s 后恢复尝试）
      系统仍可用，但响应质量可能受影响。
```

#### 2.9.4 健康状态接口

扩展 `LLMGateway` 的现有 `key_pool_stats()`，增加 failover 感知的健康检查接口：

```python
def health_status(self) -> Dict:
    """返回所有 provider 的健康状态"""
    return {
        "primary": {
            "provider": "zhipu",
            "circuit_breaker": "closed",     # closed / open / half_open
            "key_pool": [...],
        },
        "fallbacks": [
            {"provider": "deepseek", "circuit_breaker": "closed", ...},
            {"provider": "qwen", "circuit_breaker": "closed", ...},
        ],
        "last_failover": {                    # 最近一次 failover 事件
            "from": "zhipu",
            "to": "deepseek",
            "reason": "HTTP 503",
            "time": "2026-05-28T14:30:00",
        },
    }
```

此接口可被管理后台或监控系统定时调用，展示 LLM 层健康状态。

## 3. Key 配置

### 3.1 配置架构

采用"主 provider 统一配置 + 各 provider 独立配置"的模式：

```
.env 文件
├── LLM_PROVIDER=deepseek           # 主 provider
├── API_KEYS=xxx                     # 主 provider 的 Key（写入 LLM_PROVIDER 对应的配置）
├── MODEL_CODE=xxx                   # 主 provider 的模型
├── BASE_URL=xxx                     # 主 provider 的地址
├── DEEPSEEK_API_KEYS=xxx            # DeepSeek 独立配置
├── DEEPSEEK_MODEL_CODE=xxx
├── DEEPSEEK_BASE_URL=xxx
├── QWEN_API_KEYS=xxx                # Qwen 独立配置（新增）
├── QWEN_MODEL_CODE=xxx
├── QWEN_BASE_URL=xxx
├── ZHIPU_API_KEYS=xxx               # Zhipu 独立配置（新增）
├── ZHIPU_MODEL_CODE=xxx
└── ZHIPU_BASE_URL=xxx
```

### 3.2 配置加载优先级

1. **YAML 模板**：`config.yaml` 中各 provider 引用自己的环境变量（`${QWEN_API_KEYS:-}`），未设置时为空
2. **主 provider 覆盖**：`API_KEYS`/`MODEL_CODE`/`BASE_URL` 根据 `LLM_PROVIDER` 写入对应 provider
3. **独立环境变量覆盖**：`DEEPSEEK_*`/`QWEN_*`/`ZHIPU_*` 覆盖各自配置（优先级高于 YAML 模板）

### 3.3 FailoverGateway 初始化时的 Key 检查

```python
class FailoverGateway:
    def __init__(self, primary_name, fallback_names):
        self.slots = []
        for name in [primary_name] + fallback_names:
            cfg = getattr(settings.llm, name)
            keys = cfg.get_effective_keys()
            if not keys:
                logger.warning(f"[Failover] Provider [{name}] 未配置 API Key，跳过")
                continue
            self.slots.append(ProviderSlot(
                provider_name=name,
                key_pool=KeyPool(keys, ...),
                circuit_breaker=CircuitBreaker(),
            ))

        if not self.slots:
            raise ValueError("没有可用的 LLM Provider")
        if len(self.slots) == 1:
            logger.warning("[Failover] 仅有 1 个可用 Provider，failover 无实际效果")
```

### 3.4 典型部署场景

| 场景 | 主 Provider | 备用 Provider | 需要的额外配置 |
|------|-----------|-------------|-------------|
| 生产环境（推荐） | deepseek | qwen | 需配置 `QWEN_API_KEYS` |
| 生产环境（双保险） | deepseek | qwen + zhipu | 需配置 `QWEN_API_KEYS` + `ZHIPU_API_KEYS` |
| zhipu 为主 | zhipu | deepseek | 需配置 `ZHIPU_API_KEYS`（主）+ `DEEPSEEK_API_KEYS`（备用） |

## 4. 关键约束

### 4.1 模型能力差异

不同 provider 的模型能力不同（如 Qwen 和 GLM 的 tool calling 格式兼容，但 DeepSeek 的 reasoning_content 需要 `_format_messages` 特殊处理）。Failover 后的响应格式已通过各 Provider 的 `_parse_response()` 标准化，所以上层代码不受影响。

但需注意：如果主 provider 支持某些特殊参数（如 `reasoning_content`），备用 provider 可能不支持。FailoverGateway 应传通用参数，不传 provider 特有参数。

### 4.2 幂等性

所有 LLM 调用都是只读的（生成文本/工具调用），天然幂等。同一个请求发给不同 provider 不会产生副作用。

### 4.3 不改造的范围

- **Provider 层**（`qwen.py`、`zhipu.py`、`deepseek.py`）不改动
- **`KeyPool`** 不改动
- **Agent 层**（`agent.py`）不改动
- **配置结构**只做增量，不破坏现有配置

## 5. 改动范围

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/llm/failover.py` | **新增** | `CircuitBreaker` + `ProviderSlot` + `FailoverGateway` + 告警逻辑 |
| `src/llm/gateway.py` | **修改** | `LLMGateway` 内部集成 failover，公共接口不变 |
| `src/llm/llm_call_logger.py` | **不变** | Failover 复用现有日志，通过 Provider 层自动记录 |
| `src/services/notification_service.py` | **不变** | 复用现有通知服务发送告警 |
| `src/config/settings.py` | **修改** | 增加 `FailoverConfig`/`AlertConfig` 配置模型；`create_settings()` 增加 `QWEN_*` 环境变量加载 |
| `configs/config.yaml` | **修改** | 增加 `llm.failover` 配置段（含告警配置） |
| `.env` | **可选修改** | 如需 qwen 作为备用，增加 `QWEN_API_KEYS`/`QWEN_MODEL_CODE`/`QWEN_BASE_URL` |
| `tests/unit/test_llm_failover.py` | **新增** | Failover 逻辑、熔断器、告警触发单元测试 |

## 6. 决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| Failover 位置 | A. Gateway 层 B. Agent 层 | A | Gateway 层统一处理，Agent 无感知 |
| 熔断实现 | A. 简单计数 B. 滑动窗口 | A | 简单够用，避免过度设计 |
| 流式 failover | A. 完全不支持 B. 仅连接阶段 | B | 连接阶段可以安全切换，流开始后不能撤回 |
| 配置方式 | A. 代码硬编码备用链 B. 配置文件 | B | 不同环境可用不同备用策略 |
| Key 管理 | A. 统一一套 Key B. 各 provider 独立环境变量 | B | DeepSeek 已有独立 `DEEPSEEK_*`，Qwen 新增 `QWEN_*`，互不干扰 |
| Key 缺失处理 | A. 启动报错 B. 跳过无 Key 的 provider | B | 未配置 Key 的 provider 自动跳过，降级为单 provider 模式 |
