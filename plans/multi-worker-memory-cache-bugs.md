# 多 Worker + Redis 环境内存缓存一致性问题

> 创建时间：2026-05-18
> 背景：生产环境使用 Gunicorn 多 worker + Redis 缓存，经全面代码审查发现的依赖内存缓存导致多 worker 状态不一致的 bug。

---

## P0：定时任务调度器重复执行

**文件**：`src/scheduler/manager.py:27` → `ScheduledTaskManager._jobs: Dict[str, str]`

**问题**：`main.py` 的 `lifespan` 中每个 Gunicorn worker 启动时都会调用 `scheduled_task_manager.start()`。如果有 4 个 worker，同一个定时任务会被注册 4 次，每次触发时 4 个 worker 各自执行一次。

```python
# src/scheduler/manager.py
class ScheduledTaskManager:
    def __init__(self):
        self._scheduler: Optional[BackgroundScheduler] = None
        self._jobs: Dict[str, str] = {}  # task_id -> apscheduler job_id
```

**影响**：
- 发送多条重复消息
- 执行多次重复操作
- 数据库写入多次

**修复方案**：
- **方案 A**：使用 Gunicorn `--preload` 模式，`scheduler_manager.start()` 只在主进程调用一次
- **方案 B**：使用 Redis 分布式锁，任务触发时只有获取锁成功的 worker 执行
- **方案 C**：定时任务调度器独立为单独进程，不依赖 Gunicorn worker 生命周期

---

## P1：实例状态内存缓存

**文件**：`src/saas/services/instance_manager.py:23-25` → `AgentInstanceManager._routers` / `._instance_info`

**问题**：实例的启动/停止/运行状态完全存储在内存中，不再更新数据库状态。多 worker 环境下：

```python
class AgentInstanceManager:
    def __init__(self):
        self._routers: Dict[str, AgentRouter] = {}
        self._instance_info: Dict[str, dict] = {}  # instance_id → DB 记录缓存
```

- Worker A 启动实例 X 后，Worker B 不知道实例 X 已在运行
- 实例锁定状态在内存中管理，跨 worker 不一致
- 如果请求路由到不同 worker，可能多个 worker 同时认为自己正在处理同一会话

**影响**：SaaS 模式下实例状态错乱，可能出现同一实例被多次启动或重复处理。

**修复方案**：
- 实例运行状态回归数据库存储（或 Redis）
- 实例启动/停止前通过 Redis 分布式锁确保互斥
- 状态变更后同步更新 Redis 和数据库

---

## P2：Standalone 子智能体缓存

**文件**：`src/core/agent_router.py:29` → `AgentRouter._standalone_cache: Dict[str, Agent]`

**问题**：standalone 子智能体按 `{session_id}:{subagent_name}` 缓存在内存中。多 worker 下，用户后续请求可能被路由到不同 worker：

```python
class AgentRouter:
    def __init__(self):
        self._standalone_cache: Dict[str, Agent] = {}
```

- 子智能体的对话上下文在 Worker A 中，Worker B 找不到
- 需要重新创建 Agent 实例，但之前的短期记忆和 Skill Session 状态在另一个 worker 内存中丢失

**影响**：session 上下文在跨 worker 时丢失，子智能体表现异常。

**修复方案**：
- 部署层配置 sticky session（基于 session_id 固定路由到同一 worker）
- 或将 Agent 上下文状态迁移到 Redis，支持跨 worker 恢复

---

## P2：登录速率限制稀释

**文件**：`src/api/rate_limit.py:66-67` → `login_rate_limiter` (SlidingWindowRateLimiter)

**问题**：每个 worker 维护独立的滑动窗口计数，速率限制被 worker 数量稀释。

```python
# 全局登录速率限制器实例
login_rate_limiter = SlidingWindowRateLimiter(max_requests=_get_login_limit())
```

**影响**：配置 `5次/分钟` 在 3 个 worker 下实际允许 `15次/分钟`，安全性下降 3 倍。

**修复方案**：
- 将 `SlidingWindowRateLimiter` 的计数存储迁移到 Redis
- 使用 Redis 的 Sorted Set 实现跨 worker 统一的滑动窗口计数

---

## P3：SSE 连接不可跨 Worker

**文件**：`src/main.py:45-46` → `SSEConnectionManager.sessions` / `.sse_connections`

**问题**：`broadcast()` 只能向当前 worker 上的 SSE 客户端推送消息：

```python
class SSEConnectionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.sse_connections: Dict[str, List[queue.Queue]] = {}
```

- 前端 SSE 连接到 Worker A，但聊天请求被负载均衡器路由到 Worker B
- Worker B 生成的消息无法推送到 Worker A 的 SSE 队列

**影响**：无 sticky session 时，前端可能收不到流式消息推送。

**修复方案**：
- 部署层（Nginx/负载均衡器）配置 sticky session，使同一 session_id 的 SSE 和 API 请求路由到同一 worker
- 或使用 Redis Pub/Sub 广播消息，每个 worker 订阅并推送到本地 SSE 连接

---

## 修复优先级汇总

| 优先级 | 问题 | 影响范围 | 建议方案 |
|--------|------|---------|---------|
| P0 | 定时任务重复执行 | 所有定时任务 | `--preload` 或 Redis 锁 |
| P1 | 实例状态内存缓存 | SaaS 实例管理 | 状态回归 Redis/DB |
| P2 | Standalone 子智能体缓存 | 子智能体会话 | sticky session 或 Redis |
| P2 | 登录速率限制稀释 | 安全防护 | Redis 滑动窗口 |
| P3 | SSE 连接跨 Worker | 流式消息推送 | sticky session 配置 |
