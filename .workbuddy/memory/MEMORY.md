# AID Work Agent - 长期记忆

## 项目技术栈
- 后端：FastAPI + Uvicorn（开发）/ Gunicorn + Uvicorn Workers（生产）
- LLM：通义千问（Qwen）或 智谱 GLM，通过 `src/llm/gateway.py` 统一管理
- 前端：Vue 3 + TypeScript + Vite + TailwindCSS
- 数据库：SQLite（开发）/ PostgreSQL（生产可切换）

## LLM API Key 池方案（2026-03-25 实施）
- 新增 `src/llm/key_pool.py`：`KeyPool` 类，支持多 Key 轮询 + `asyncio.Semaphore` 控制每 Key 并发上限
- `LLMProviderConfig`（`src/config/settings.py`）新增 `api_keys` 字段（逗号分隔字符串或 List），含向后兼容 validator
- `LLMGateway`（`src/llm/gateway.py`）每次调用通过 `KeyPool.acquire()` 上下文管理器动态获取 Key
- `.env` 配置规则：
  - 单 Key（旧）：`ZHIPU_API_KEY=xxx`（无需修改现有配置）
  - 多 Key 池（新）：`ZHIPU_API_KEYS=key1,key2,key3`（`api_keys` 非空时优先）
- `config.yaml` 新增 `max_concurrent_per_key`（默认 2）和 `queue_timeout`（默认 30s）配置项

## KeyPool 调试日志（已还原，2026-03-25）
- 临时调试日志已验证通过，acquire/release 日志已从 INFO 改回 DEBUG
- 如需重新开启，将 `key_pool.py` 中 `[KeyPool]` 相关的 `logger.debug` 改为 `logger.info` 即可

## 生产部署建议（100 用户规模）
- 单容器：Gunicorn + `(2×CPU核数)+1` 个 Uvicorn Workers
- Key 池：3~5 个 Key，每 Key 并发 2，总并发 6~10
- Nginx 前置限流（`/api/chat` 接口限每 IP 10 req/min）
- 不需要每个 Agent 独占容器（过度工程，100 用户不满足拆分条件）
