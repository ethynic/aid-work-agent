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

## 服务器环境说明
- 服务器：Ubuntu 16.04，4 核
- 不支持独立的 `docker-compose`（带中划线）命令
- 使用 Docker 内置插件命令：`docker compose`（空格，无中划线）

## 生产部署建议（100 用户规模）
- 单容器：Gunicorn + `(2×CPU核数)+1` 个 Uvicorn Workers
- Key 池：3~5 个 Key，每 Key 并发 2，总并发 6~10
- Nginx 前置限流（`/api/chat` 接口限每 IP 10 req/min）
- 不需要每个 Agent 独占容器（过度工程，100 用户不满足拆分条件）

## Gunicorn+Uvicorn 架构（2026-03-25 实施）
- `requirements.txt` 新增 `gunicorn>=21.2.0`
- `Dockerfile` ENTRYPOINT 已改为 `gunicorn -c deploy/gunicorn.conf.py src.main:app`
- 运行参数统一在 `deploy/gunicorn.conf.py` 中管理（workers、timeout、bind、loglevel 等）
- 支持通过环境变量覆盖：`WORKERS`（**服务器4核，默认9**）、`WORKER_TIMEOUT`（默认 120s）、`SERVER_PORT`（默认 8000）、`LOG_LEVEL`
- `docker-compose.prod.yml` 新增 `WORKERS`、`WORKER_TIMEOUT` 及 Key 池变量（`ZHIPU_API_KEYS`、`QWEN_API_KEYS`）
- 部署流程：服务器修改 `.env` 设置 `WORKERS=N`，然后 `docker compose -f docker-compose.prod.yml up -d --build`

## 代码审核结果（2026-03-25，docs/CODE_REVIEW.md）
- 🔴 严重问题 4 个：auth.py 函数名遮蔽、微信登录孤儿代码语法破坏、core/executor.py 与 tools/executor.py 同名类双重定义、SessionDB.delete() 级联删除遗漏 chat_records 表
- 🟡 中等问题 4 个：前端 ChatMessage 类型命名冲突（types/index.ts vs api/session.ts）、verify_token 过期清理逻辑位置不当、SubAgent 线程池无上限、/api/chat 同步接口潜在阻塞
- 🟢 可清理冗余 5 个：dialog_manager.py（344行）、intent_engine.py（456行）、planner.py（421行，区别于 plan_manager.py）、memory/manager.py（312行）、core/executor.py（324行）
- 完整报告见 `docs/CODE_REVIEW.md`，用户要求只记录不修改

## 前端专项审核补充（2026-03-25，docs/CODE_REVIEW.md § 前端专项）
- 🔴 F-1 `api/customer.ts`：API_BASE fallback 写成 `/api/customer` 导致请求路径翻倍，所有客户接口 404
- 🔴 F-2 `api/credentials.ts`：`listCredentials` 用 `new URL(相对路径)` 在默认配置下直接抛 TypeError 崩溃
- 🟡 F-3 `useAgent.ts` + `ChatContainer.vue`：sessionId 双轨制（前端生成 vs 后端 DB id）存在竞态，消息可能存入孤立 session
- 🟡 F-4 `ChatContainer.vue`：AI 回复从未调用 `saveMessage` 持久化，刷新后 AI 消息全消失
- 🟡 F-5 `useAuth.ts`：User 接口与 `types/index.ts` 重复定义
- 🟡 F-6 `MessageItem.vue`：`formatProgressContent` 参数类型签名错误
- 🟡 F-7 `api/credentials.ts`：所有凭据接口无 Authorization Header（后端需要鉴权）
- 🟢 F-8/F-9/F-10：测试密码硬编码、files 类型错误、switch-case 缺块级作用域
