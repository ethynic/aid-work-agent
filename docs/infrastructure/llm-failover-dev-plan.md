# LLM Failover 开发计划

> 对应设计文档：[llm-failover-design.md](./llm-failover-design.md)

## 开发阶段

### 阶段 1：配置层改造

> 前置依赖：无

- [x] **1.1 settings.py 增加 Failover 配置模型**
  - 新增 `CircuitBreakerConfig`（`failure_threshold`、`recovery_timeout`）
  - 新增 `RetryConfig`（`max_retries`、`retry_delay`）
  - 新增 `AlertConfig`（`enabled`、`channel`、`cooldown`）
  - 新增 `FailoverConfig`（`enabled`、`providers`、`retry`、`circuit_breaker`、`alert`）
  - `LLMConfig` 中增加 `failover: FailoverConfig` 字段
  - [x] 完成

- [x] **1.2 create_settings() 增加 Qwen 独立环境变量加载**
  - 在 `create_settings()` 中增加 `QWEN_API_KEYS`、`QWEN_MODEL_CODE`、`QWEN_BASE_URL` 三个环境变量的加载逻辑
  - 模式与现有 `DEEPSEEK_*` 一致
  - [x] 完成

- [x] **1.3 config.yaml 增加 failover 配置段**
  - 在 `llm` 段下增加 `failover` 配置，包含 `enabled`、`providers`、`retry`、`circuit_breaker`、`alert`
  - 默认 `enabled: false`，不破坏现有行为
  - [x] 完成

### 阶段 2：Failover 核心逻辑

> 前置依赖：阶段 1

- [x] **2.1 新建 src/llm/failover.py，实现 CircuitBreaker**
  - 状态机：closed → open → half_open → closed
  - `record_success()`：重置失败计数，状态切回 closed
  - `record_failure()`：累加失败计数，达到阈值切换到 open
  - `can_attempt()`：closed 返回 True；open 检查是否超过 recovery_timeout，是则切 half_open 返回 True，否则 False；half_open 返回 True
  - [x] 完成

- [x] **2.2 实现 ProviderSlot**
  - 持有 `provider_name`、`key_pool`（KeyPool）、`circuit_breaker`（CircuitBreaker）
  - `is_available()` 方法：熔断器允许 且 key_pool 已配置
  - [x] 完成

- [x] **2.3 实现错误分类函数 `_is_retryable_error()`**
  - 根据 2.7 节的错误分类表实现
  - 覆盖：`asyncio.TimeoutError`、`httpx.ConnectError`、`httpx.HTTPStatusError`（5xx/429）、`RuntimeError`（Provider 层包装的错误）
  - [x] 完成

- [x] **2.4 实现 FailoverGateway — 非流式调用**
  - `_get_provider_chain()`：返回 [primary] + fallbacks 的可用列表
  - `_call_slot()`：从 slot 的 key_pool 获取 key，构建 provider，执行调用
  - `_call_with_failover()`：遍历 provider chain，失败时检查 `_is_retryable_error()`，是则尝试下一个，否则直接抛出
  - 全部失败抛出 `LLMAllProvidersFailedError`
  - [x] 完成

- [x] **2.5 实现 FailoverGateway — 流式调用**
  - `_stream_with_failover()`：仅连接阶段做 failover
  - 先尝试获取第一个 chunk 验证连接，成功后 yield 所有 chunk
  - 连接失败（无 chunk 产生）则尝试下一个 provider
  - 流开始后的错误直接抛出，不做 failover
  - [x] 完成

- [x] **2.6 实现 FailoverGateway — 公共接口**
  - `chat()`：委托给 `_call_with_failover("chat", ...)`
  - `stream_chat()`：委托给 `_stream_with_failover("stream_chat", ...)`
  - `chat_with_tools()`：与 `LLMGateway.chat_with_tools()` 逻辑一致，prepend system_prompt 后调用 `chat()`
  - [x] 完成

### 阶段 3：Gateway 集成

> 前置依赖：阶段 2

- [x] **3.1 LLMGateway 集成 FailoverGateway**
  - `__init__()` 中检查 `settings.llm.failover.enabled`
  - 启用时创建 `FailoverGateway` 实例，不启用时保持原有 `_key_pool` 逻辑
  - `chat()`、`stream_chat()`、`chat_with_tools()` 根据是否启用 failover 分流
  - **公共接口签名和返回值完全不变**
  - [x] 完成

- [x] **3.2 FailoverGateway 初始化时的 Key 检查**
  - 遍历配置的 provider 链，检查每个 provider 是否有 API Key
  - 无 Key 的 provider 记录 `logger.warning` 并跳过
  - 无任何可用 provider 时抛出 `ValueError`
  - 仅 1 个可用 provider 时记录 `logger.warning`（failover 无实际效果）
  - [x] 完成

- [x] **3.3 验证 agent.py 零改动**
  - 确认 `agent.py` 中对 `self.llm.chat_with_tools()` 的调用无需任何修改
  - 确认 `LLMGateway` 的 `get_provider_name()`、`get_model_name()` 在 failover 模式下仍返回主 provider 信息
  - [x] 完成

### 阶段 4：日志与告警

> 前置依赖：阶段 3

- [x] **4.1 Failover 事件日志**
  - 每次触发 failover 切换时，调用 `log_llm_invoke()` 记录一条 `type: "failover"` 日志，包含 `from_provider`、`to_provider`、`reason`
  - 每次切换时 `logger.warning("[Failover] {from} → {to}, reason: {reason}")`
  - [x] 完成

- [x] **4.2 熔断器状态变更日志**
  - 熔断触发时 `logger.warning("[CircuitBreaker] {provider} 连续失败 {n} 次，熔断 {timeout}s")`
  - 熔断恢复时 `logger.info("[CircuitBreaker] {provider} 熔断恢复，重新启用")`
  - [x] 完成

- [x] **4.3 告警逻辑**
  - 在 `FailoverGateway` 中集成 `notification_service`
  - 主 provider 熔断时发送 `high` 级别告警
  - 全部 provider 失败时发送 `critical` 级别告警
  - 熔断恢复时发送 `low` 级别通知（可选）
  - 告警去重：同一 provider 在 `cooldown` 时间内不重复发送
  - [x] 完成

- [x] **4.4 健康状态接口**
  - `FailoverGateway` 增加 `health_status()` 方法，返回所有 provider 的熔断器状态、key_pool 统计、最近 failover 事件
  - `LLMGateway` 透传此接口
  - [x] 完成

### 阶段 5：单元测试

> 前置依赖：阶段 2（可与阶段 3/4 并行）

- [x] **5.1 CircuitBreaker 单元测试**
  - closed 状态下成功/失败
  - 连续失败达到阈值触发熔断（closed → open）
  - open 状态下 `can_attempt()` 返回 False
  - recovery_timeout 后进入 half_open
  - half_open 下成功 → closed；失败 → open
  - [x] 完成

- [x] **5.2 错误分类测试**
  - 各类错误是否正确判定为 retryable / non-retryable
  - 边界：HTTP 400（不重试）、429（重试）、500（重试）、502（重试）
  - [x] 完成

- [x] **5.3 FailoverGateway 非流式调用测试**
  - 主 provider 成功：直接返回，不尝试备用
  - 主 provider 失败（retryable）：自动切到备用
  - 主 provider 失败（non-retryable）：不切换，直接抛出
  - 全部失败：抛出 `LLMAllProvidersFailedError`
  - 熔断状态下的 provider 被跳过
  - [x] 完成

- [x] **5.4 FailoverGateway 流式调用测试**
  - 连接阶段失败：切到备用 provider
  - 连接成功：流式返回所有 chunk
  - 流中途失败：不切换，直接抛出
  - [x] 完成

- [x] **5.5 Gateway 集成测试**
  - failover 禁用时走原有逻辑
  - failover 启用时走 FailoverGateway
  - 公共接口签名不变
  - [x] 完成

- [x] **5.6 告警逻辑测试**
  - 熔断触发告警发送
  - 冷却期内不重复发送
  - 熔断恢复发送恢复通知
  - [x] 完成

### 阶段 6：集成验证

> 前置依赖：阶段 3、4、5 全部完成

- [x] **6.1 本地环境验证**
  - 配置 `failover.enabled: true`，主 provider 用 deepseek（错误 key），备用用 qwen
  - 主 provider 401 认证失败后自动 failover 到 qwen（DashScope）
  - 日志中记录了 failover 事件（`[Failover] deepseek → 下一个`）
  - [x] 完成

- [x] **6.2 熔断恢复验证**
  - 连续触发主 provider 失败 3 次，观察熔断触发
  - 等待 recovery_timeout（60s），观察自动恢复
  - 验证告警发送和去重
  - [x] 完成

- [x] **6.3 现有功能回归**
  - 前端页面集成测试通过，failover 正常工作
  - agent 对话、工具调用、流式输出正常
  - [x] 完成

## 文件变更清单

| 文件 | 阶段 | 操作 | 状态 |
|------|------|------|------|
| `src/config/settings.py` | 1 | 修改：增加 FailoverConfig 系列配置模型 + 各 provider 独立环境变量加载 | ✅ |
| `configs/config.yaml` | 1 | 修改：各 provider 引用独立环境变量 + 增加 `llm.failover` 配置段 | ✅ |
| `src/llm/failover.py` | 2 | 新增：CircuitBreaker + ProviderSlot + FailoverGateway | ✅ |
| `src/llm/gateway.py` | 3 | 修改：LLMGateway 集成 FailoverGateway | ✅ |
| `src/llm/llm_call_logger.py` | — | 不变 | — |
| `src/services/notification_service.py` | — | 不变（复用） | — |
| `tests/unit/test_llm_failover.py` | 5 | 新增：全量单元测试（42 tests） | ✅ |
| `.env` / `.env.example` | 1 | 修改：增加 QWEN_*、ZHIPU_* 环境变量配置 | ✅ |
| `docs/infrastructure/llm-failover-design.md` | — | 修改：更新第 3 节配置架构 | ✅ |
