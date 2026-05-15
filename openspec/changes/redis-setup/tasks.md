## 1. Infrastructure & Dependencies

- [ ] 1.1 Add `redis>=5.0.0` to `requirements.txt`
- [ ] 1.2 Create `src/core/redis_client.py` with `RedisClient` class (connection pool, JSON serialize, TTL, fallback to memory)
- [ ] 1.3 Extend `src/config/settings.py` with `RedisConfig` model (host, port, password, db, ssl, enabled)
- [ ] 1.4 Add `redis` section to `configs/config.yaml` with local defaults
- [ ] 1.5 Create `deploy/redis/docker-compose.yml` for local Redis 7.x container (port 6379, persistent volume)
- [ ] 1.6 Create `deploy/redis/README.md` with setup/stop/connect/troubleshoot instructions
- [ ] 1.7 Add environment variable placeholders for production Redis (`REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`, `REDIS_SSL`) to `.env.example` if it exists

## 2. High-Priority Memory Cache Migration

- [ ] 2.1 Replace `SSEConnectionManager.cancelled_sessions` with Redis Set `cancelled_session:{session_id}` (TTL 300s)
- [ ] 2.2 Update `SSEConnectionManager.cancel_session()`, `is_cancelled()`, `clear_cancelled()` to use Redis
- [ ] 2.3 Replace `Agent._pending_clarifications` with Redis Hash `pending_clarification:{session_id}` (TTL 3600s)
- [ ] 2.4 Update `Agent.process_message()` clarification detection logic to read from Redis
- [ ] 2.5 Update subagent clarification save/clear logic in `Agent.py` to write to Redis
- [ ] 2.6 Replace `uploaded_files` global dict with Redis Hash `uploaded_file:{file_id}` (TTL 86400s)
- [ ] 2.7 Update `upload_file()`, `get_file_info()`, `delete_file()` endpoints in `src/main.py` to use Redis

## 3. Medium-Priority Memory Cache Migration

- [ ] 3.1 Replace `SubagentExecutor._task_records` with Redis Hash `task_record:{execution_id}` (TTL 7200s)
- [ ] 3.2 Update `_create_task_record()`, `_get_task_record()`, `_update_task_record()` in `src/subagents/executor.py`
- [ ] 3.3 Replace `PlanManager._plans` with Redis String `execution_plan:{session_id}` (TTL 3600s, JSON serialized)
- [ ] 3.4 Update `PlanManager.create_plan()`, `get_plan()`, `update_task_status()`, `mark_task_*()` to read/write Redis
- [ ] 3.5 Ensure `PlanManager` loads plan from Redis on `get_plan()` and writes back on every state change

## 4. Testing & Validation

- [ ] 4.1 Add unit tests for `RedisClient` (serialize, deserialize, TTL, fallback)
- [ ] 4.2 Add integration test: simulate 2 workers accessing same session cancellation flag via Redis
- [ ] 4.3 Add integration test: simulate 2 workers accessing same pending clarification via Redis
- [ ] 4.4 Add integration test: file upload in one worker, read metadata in another
- [ ] 4.5 Manually test the full 4-message flow that previously lost messages (to verify no regression)

## 5. Documentation & Cleanup

- [ ] 5.1 Update `CLAUDE.md` or `.claude/rules/backend_dev.md` to document "禁止依赖内存变量存储跨请求状态"原则
- [ ] 5.2 Remove or deprecate any dead code related to old memory caches
- [ ] 5.3 Update `deploy/README.md` (if exists) to include Redis startup step
- [ ] 5.4 Verify all temporary `[DEBUG]` logs added during troubleshooting are removed or converted to proper `logger.debug`

## 6. Production Deployment

- [ ] 6.1 Purchase and configure Tencent Cloud Redis 2G instance
- [ ] 6.2 Obtain connection parameters (host, port, password, SSL)
- [ ] 6.3 Set production environment variables (`REDIS_HOST`, `REDIS_PASSWORD`, etc.)
- [ ] 6.4 Deploy and monitor Redis connection health in production logs
