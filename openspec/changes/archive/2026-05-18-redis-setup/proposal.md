## Why

Gunicorn 多 Worker 部署环境下，每个 Worker 进程拥有独立的 Python 内存空间。当前后端大量使用内存字典/集合存储跨请求状态（如 `Agent._pending_clarifications`、`PlanManager._plans`、`SSEConnectionManager.cancelled_sessions` 等），导致不同 Worker 间状态完全隔离，引发会话上下文丢失、用户取消无效、计划执行中断、子智能体任务记录不可见等功能性缺陷。引入 Redis 作为进程间共享缓存层是解决这些问题的必要基础设施。

## What Changes

1. **本地开发环境**: 在 `deploy/` 目录添加 Redis 容器搭建脚本（Docker Compose）和操作文档
2. **生产环境**: 接入腾讯云 Redis 2G 实例，配置连接参数
3. **内存缓存迁移**: 将以下内存状态逐步迁移到 Redis：
   - `Agent._pending_clarifications` → Redis Hash（按 session_id 存储澄清上下文）
   - `SSEConnectionManager.cancelled_sessions` → Redis Set（存储被取消的 session_id）
   - `PlanManager._plans` → Redis String / Hash（序列化计划状态）
   - `SubagentExecutor._task_records` → Redis Hash（按 execution_id 存储任务记录）
   - `uploaded_files` → Redis Hash（文件元数据）
   - `AgentRouter._standalone_cache` TTL 监控 → Redis TTL + 定时清理（作为补充）
4. **新增 Redis 工具模块**: `src/core/redis_client.py` 提供统一的 Redis 连接和封装
5. **配置扩展**: `configs/config.yaml` 新增 `redis` 配置节

## Capabilities

### New Capabilities
- `redis-local-dev`: 本地开发环境 Redis 容器搭建与运维脚本
- `redis-production`: 生产环境腾讯云 Redis 接入配置与连接管理
- `memory-cache-redis`: 将后端内存缓存风险点迁移至 Redis 共享缓存

### Modified Capabilities
<!-- 无现有 spec 级需求变更，本次为纯实现层基础设施升级 -->
- (无)

## Impact

- **新增文件**: `src/core/redis_client.py`、`deploy/redis/docker-compose.yml`、`deploy/redis/README.md`
- **修改文件**: `src/config/settings.py`、`configs/config.yaml`、`src/core/agent.py`、`src/main.py`、`src/core/plan_manager.py`、`src/subagents/executor.py`、`requirements.txt`
- **新增依赖**: `redis>=5.0.0`
- **受影响系统**: Agent 消息处理、SSE 流式输出、子智能体委派、计划执行、文件上传下载
- **部署要求**: 本地开发需启动 Redis 容器；生产环境需购买并配置腾讯云 Redis
