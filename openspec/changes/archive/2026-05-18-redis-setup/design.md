## Context

本项目使用 Gunicorn + FastAPI 部署，默认启动多个 Worker 进程。每个 Worker 拥有独立的 Python 内存空间，导致基于内存字典/集合的跨请求状态无法共享。此前已发现以下高优先级风险点：

- `Agent._pending_clarifications` — 子智能体澄清上下文在多 Worker 下丢失
- `SSEConnectionManager.cancelled_sessions` — 用户"停止生成"按钮在多 Worker 下大概率无效
- `PlanManager._plans` — 执行计划状态跨 Worker 不可见
- `SubagentExecutor._task_records` — 子智能体任务记录跨 Worker 丢失
- `uploaded_files` — 文件上传元数据跨 Worker 不可见

此前对 `ShortTermMemory` 的修复（每次从 DB 重建）已解决会话历史问题，但其余内存状态仍待处理。

## Goals / Non-Goals

**Goals:**
1. 在 `deploy/` 提供本地 Redis 容器搭建脚本和文档
2. 接入生产环境腾讯云 Redis（2G 实例）
3. 为后端提供统一的 Redis 客户端封装
4. 将所有跨请求内存状态迁移至 Redis

**Non-Goals:**
- 不引入 Redis Cluster 或 Sentinel 高可用方案（初期单机/单实例足够）
- 不迁移纯性能优化型缓存（如 `PromptManager._cache`、`TenantSkillCache._cache`）——这些只影响性能，不影响功能正确性
- 不将 LLM 的 `KeyPool` 并发控制改为 Redis 分布式锁（信号量已够用）
- 不改写 `SemanticSnapshotGenerator._cache`（浏览器工具使用频率低）

## Decisions

### 1. 统一 Redis 客户端封装 vs 各处直接使用 `redis.Redis`
**决策**: 统一封装 `src/core/redis_client.py`，提供 `RedisClient` 类，封装连接池、序列化（JSON）、TTL 管理、错误降级（连接失败时降级到内存，记录 warning）。
**理由**: 避免各处重复处理连接异常、序列化、编码问题；便于后续切换 Redis 实例或切换到 Redis Cluster。
**替代方案**: 各处直接使用 `redis.Redis()`，简单但重复代码多，错误处理不一致。

### 2. 数据序列化格式：JSON vs MessagePack
**决策**: 使用 JSON。
**理由**: 项目已有大量 JSON 使用，无需新增依赖；数据量小（澄清上下文、计划状态等），JSON 性能足够。MessagePack 性能更好但增加依赖复杂度。

### 3. Redis Key 命名规范
**决策**: 采用 `{prefix}:{tenant_id}:{session_id}:{type}` 分层命名。
**理由**: 便于按前缀批量清理（如 `flushdb` 不安全时，可用 `SCAN + DEL`）；支持多租户隔离。
**示例**:
- `pending_clarification:{session_id}`
- `cancelled_session:{session_id}`
- `execution_plan:{session_id}`
- `task_record:{execution_id}`
- `uploaded_file:{file_id}`

### 4. 本地开发环境：Docker Compose vs 本地安装
**决策**: Docker Compose。
**理由**: 与项目已有的 PostgreSQL 容器化方式一致，方便统一启动；避免开发者本地环境差异。

### 5. 生产环境：腾讯云 Redis vs 自建 Redis
**决策**: 腾讯云 Redis 2G。
**理由**: 用户已有明确采购计划；托管服务减少运维负担；项目当前数据量小，2G 足够。

### 6. 渐进迁移 vs 一次性全量迁移
**决策**: 渐进迁移，按风险优先级分批实施。
**理由**: 降低单批次改动风险，便于回滚；高优先级问题（`cancelled_sessions`、`pending_clarifications`）先修复，低优先级后续处理。

## Risks / Trade-offs

- **[Risk] Redis 连接失败导致服务不可用** → **Mitigation**: `RedisClient` 封装层在连接失败时降级到内存操作，记录 warning，服务不中断。
- **[Risk] Redis 成为单点故障** → **Mitigation**: 腾讯云 Redis 提供高可用（主从+自动故障转移）；本地开发为单实例，可接受。
- **[Risk] 迁移后性能下降** → **Mitigation**:  Redis 操作均为 O(1) 的 Hash/Set/String 操作，延迟 < 5ms；若出现性能问题，可引入本地 L1 缓存（内存）+ Redis L2 缓存架构。
- **[Risk] 本地开发与生产配置差异** → **Mitigation**: 统一通过 `configs/config.yaml` + 环境变量配置，本地和生产使用相同的 `RedisClient` 接口。
- **[Trade-off] 增加了基础设施依赖** → 项目从"只需 PostgreSQL"变为"PostgreSQL + Redis"，部署复杂度略有上升。收益是消除多 Worker 下的功能缺陷。

## Migration Plan

1. **Phase 0（基础设施）**: 搭建 Redis（本地 Docker / 生产腾讯云）
2. **Phase 1（高优先级）**: 迁移 `cancelled_sessions`、`pending_clarifications`
3. **Phase 2（中优先级）**: 迁移 `uploaded_files`、`task_records`
4. **Phase 3（低优先级）**: 迁移 `PlanManager._plans`
5. **Phase 4（清理）**: 验证所有迁移点，移除废弃的内存缓存代码，更新文档

**Rollback**: 若 Redis 出现问题，回滚到使用内存缓存的版本（保留 fallback 逻辑）。

## Open Questions

1. 腾讯云 Redis 2G 的具体连接地址、密码、端口待采购后确认
2. 是否需要 Redis 持久化（RDB/AOF）？生产环境默认开启 RDB，可接受少量数据丢失
3. `PlanManager._plans` 的序列化体积可能较大（含 Task 列表），是否超过 Redis String 的推荐大小？预计不超过 100KB，可接受
