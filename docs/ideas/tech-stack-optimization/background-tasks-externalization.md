# 后台定时/轮询任务外置

> **状态**：💡 灵感 | **关联**：[全链路异步化](full-async-migration.md)

---

## 1. 问题分析

### 1.1 当前现状

当前所有后台定时任务和轮询循环都在 `src/main.py` 的 `lifespan` 函数中通过 `asyncio.create_task()` 启动，**运行在 Gunicorn worker 进程内**。

#### 现有任务清单（共 12 个）

| # | 任务 | 启动方式 | 间隔 | 位置 |
|---|------|---------|------|------|
| 1 | PostgreSQL 连接池初始化 | 同步 `init_postgres_pool()` | 启动一次 | main.py 生涯 |
| 2 | 数据库初始化 | 同步 `init_database()` | 启动一次 | main.py 生涯 |
| 3 | 子智能体定义加载 | 同步加载 | 启动一次 | main.py 生涯 |
| 4 | 日志数据库池初始化 | 同步 | 启动一次 | main.py 生涯 |
| 5 | Channel Session Manager | 同步 | 启动一次 | main.py 生涯 |
| 6 | Scheduled Task Scheduler | APScheduler | 程序化 | main.py 生涯 |
| 7 | SaaS Instance Manager 恢复 | 同步 | 启动一次 | main.py 生涯 |
| 8 | **Memory Cleanup Loop** | `asyncio.create_task` | `settings.memory.cleanup_interval` 秒 | main.py:384 |
| 9 | **Dedup Cleanup Loop** | `asyncio.create_task` | 每天凌晨 03:00 | main.py:409 |
| 10 | **Instance Lock Cleanup Loop** | `asyncio.create_task` | 每 60s | main.py:435 |
| 11 | **WeCom KF Timeout Check Loop** | `asyncio.create_task` | 每 60s | main.py:615 |
| 12 | **WeCom Personal RPA Archive Poller** | `await _archive_poller.start()` | 每 60s | main.py |

#### 其他文件中的 `asyncio.create_task`（影响评估）

| # | 位置 | 用途 | 是否受多 worker 影响 |
|---|------|------|---------------------|
| 1 | `src/llm/key_pool.py:148` | 并发抢 Key 槽位 | 否（每个 worker 独立管理自己的 Key 池） |
| 2 | `src/tools/browser/session.py:128` | 关闭浏览器会话 | 否（单 worker 内资源清理） |
| 3 | `src/core/session_queue.py:780` | Session 锁续期 watchdog | ⚠️ 是（同一 Session 可能被多个 worker 处理） |
| 4 | `src/subagents/executor.py:263` | 子智能体异步执行 | 否（单 session 内执行） |
| 5 | `src/channels/wecom_personal_rpa/archive/poller.py:119` | 存档轮询主循环 | ⚠️ 是（有 Redis 分布式锁保护，但架构不干净） |
| 6-8 | `src/saas/api/channel_routes.py` | 渠道消息后台处理 | 否（每个请求独立） |

### 1.2 核心问题

#### 问题 1：多 worker 重复执行

Gunicorn 通常启动 4-8 个 worker 进程，每个 worker 都会独立运行 `lifespan` → 每个后台任务都会在**每个 worker** 中跑一份：

- Memory Cleanup Loop → 4-8 个进程同时在清理，浪费资源但功能正常
- Dedup Cleanup Loop → 4-8 个进程同时在凌晨 3 点清理去重表，可能产生锁冲突
- WeCom RPA Archive Poller → 虽然有 Redis 分布式锁保护，但 4-8 个进程争抢锁，浪费 CPU
- Instance Lock Cleanup → 4-8 个进程同时检查，属于拟废弃功能

#### 问题 2：进程生命周期耦合

- worker 重启/崩溃 → 后台任务中断
- 部署新版本时，旧 worker 的 `lifespan` cleanup 和新 worker 的 startup 交叉，可能状态不一致
- 后台任务中的错误可能导致 worker 静默退出（已知企微 SDK 子进程隔离就是为此）

#### 问题 3：无法独立扩缩

- 需要更多 worker 处理 HTTP 请求，但同时也会增加后台任务数量
- 无法将后台任务和 HTTP 服务独立部署、独立伸缩

---

## 2. 改造方案

### 2.1 推荐方案：`arq`（基于 Redis 的异步任务队列）

选择 `arq` 的理由：

| 需求 | arq 满足情况 |
|------|-------------|
| 与现有 Redis 基础设施一致 | ✅ 项目已重度使用 Redis（缓存、锁、pub/sub） |
| 原生异步 | ✅ `async/await` 一等公民 |
| 定时任务（cron） | ✅ 内置 `cron` 支持 |
| FastAPI 集成 | ✅ 可复用现有 Pydantic 模型 |
| 轻量无外部依赖（除 Redis） | ✅ 不需要 RabbitMQ/数据库额外部署 |
| 延迟任务 | ✅ `enqueue_job(defer_until=...)` |

#### 备选方案对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| Celery + Redis | 生态最成熟 | 同步为主，async 支持弱；配置复杂 |
| APScheduler（保持现状） | 无外依赖 | 多 worker 问题无解 |
| **arq** ✅ | 原生 async，轻量，利用现有 Redis | 任务结果不能太大（Redis 内存）；无内置 Flower 类监控 |
| Procrastinate（PostgreSQL） | 利用现有 PG | 需要额外的 schema 管理 |

### 2.2 架构变化

```
改造前：
  Gunicorn Master
    ├── Worker 1  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...
    ├── Worker 2  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！
    ├── Worker 3  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！
    └── Worker 4  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！

改造后：
  arq Worker（独立进程，1-2 个实例）
    ├── cron: memory_cleanup       每天按配置间隔执行
    ├── cron: dedup_cleanup        每天 03:00 执行
    ├── cron: instance_lock_cleanup 每 60s 执行
    ├── cron: wecom_kf_timeout      每 60s 执行
    └── cron: rpa_archive_poller   每 60s 执行

  Gunicorn Master
    ├── Worker 1  ─ 只处理 HTTP 请求（lifespan 轻量）
    ├── Worker 2  ─ 只处理 HTTP 请求
    ├── Worker 3  ─ 只处理 HTTP 请求
    └── Worker 4  ─ 只处理 HTTP 请求
```

### 2.3 任务迁移清单

| 原任务 | 目标 | 类型 | 说明 |
|--------|------|------|------|
| Memory Cleanup Loop | arq cron 定时任务 | `cron` | 全局唯一执行 |
| Dedup Cleanup Loop | arq cron 定时任务 | `cron` | 每天凌晨 3 点，全局唯一 |
| Instance Lock Cleanup Loop | arq cron 定时任务 | `cron` | 每 60s，或直接废弃 |
| WeCom KF Timeout Check | arq cron 定时任务 | `cron` | 每 60s，全局唯一 |
| RPA Archive Poller | arq cron 定时任务 | `cron` | 兜底轮询，利用 Redis 锁防重 |
| APScheduler 任务 | arq 延迟任务 | `enqueue_job` | 按 APScheduler 现有逻辑逐个迁移 |
| Session Queue heartbeat | **保留在 worker 内** | `asyncio.create_task` | 与 request 生命周期绑定 |

### 2.4 新增依赖与配置

```bash
# requirements.txt 新增
arq>=0.26.0

# 新增文件
src/scheduler/
  __init__.py
  worker.py          # arq Worker 创建与配置
  tasks.py           # 所有定时/延迟任务函数
  settings.py        # RedisSettings 配置
```

```python
# src/scheduler/worker.py
from arq import create_worker
from arq.connections import RedisSettings

async def startup(ctx):
    # 初始化 DB 连接等
    pass

async def shutdown(ctx):
    # 清理资源
    pass

# 创建 worker（可在独立进程中启动）
worker = create_worker(
    WorkerSettings,
    redis_settings=RedisSettings(host='localhost', port=6379, database=1),
)

# src/scheduler/tasks.py
async def memory_cleanup(ctx):
    """清理过期会话记忆"""
    ...

async def dedup_cleanup(ctx):
    """清理过期消息去重记录"""
    ...

async def rpa_archive_poll(ctx):
    """RPA 存档兜底轮询"""
    ...
```

### 2.5 部署方式

```bash
# 原有的 HTTP 服务不变
gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker

# 新增：独立 arq worker（1-2 个实例即可，不需要多副本）
arq src.scheduler.worker.WorkerSettings
```

Docker Compose 中增加独立的 `scheduler` 服务，依赖 Redis。

---

## 3. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| arq 任务结果通过 Redis 传输，大对象可能 OOM | 任务返回轻量状态摘要，不返回大数据集 |
| arq worker 崩溃后 cron 任务丢失 | 配合 Redis 持久化（AOF），监控 worker 进程 |
| 原 APScheduler 任务调度语义可能不同 | 逐个排查现有 APScheduler 任务，确认迁移等价性 |
| 引入新的运维组件 | arq 是轻量 Python 库，无额外中间件部署 |
| `session_queue.py` 的 watchdog 不宜外置 | 保留在 HTTP worker 进程内 |

---

## 4. 实施步骤

| 阶段 | 内容 | 工作量 |
|------|------|-------|
| Phase 1 | 搭建 arq 基础框架（worker、tasks 骨架、Redis 配置） | 1 天 |
| Phase 2 | 迁移 Memory Cleanup → arq cron | 0.5 天 |
| Phase 3 | 迁移 Dedup Cleanup → arq cron | 0.5 天 |
| Phase 4 | 迁移 WeCom KF Timeout → arq cron | 1 天 |
| Phase 5 | 迁移 RPA Archive Poller → arq cron（保留 Redis 锁） | 1 天 |
| Phase 6 | 迁移 APScheduler 现有定时任务 → arq | 1 天 |
| Phase 7 | 清理 main.py lifespan 中的后台循环 | 0.5 天 |
| Phase 8 | Docker Compose / k8s 部署适配 | 1 天 |
| Phase 9 | 集成测试 + 监控配置 | 1 天 |

**总计**：约 7-8 人天
