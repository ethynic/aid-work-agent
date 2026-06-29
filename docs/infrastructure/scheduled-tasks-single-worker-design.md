# Gunicorn 多 Worker 定时任务单 Worker 执行方案

## 背景

本项目生产环境使用 Gunicorn 多 worker 部署（默认 4 个 worker）。Gunicorn fork 多个 worker 进程后，每个 worker 拥有独立的 Python 内存空间，**所有 worker 都会执行应用启动代码**。

当前定时任务（`scheduled_tasks` 表内登记的任务）的调度器在应用启动时初始化，如果每个 worker 都启动调度器，会导致：

- **同一任务被多个 worker 同时触发**，重复执行
- 重复执行可能引发数据竞争、重复发送通知、外部 API 重复调用等问题
- 任务执行结果不可预测

## 约束与需求

- **时间敏感性低**：定时任务延迟 5-10 分钟可接受（用户确认）
- **单机部署**：当前仅一台服务器，无需考虑多机调度
- **零外部依赖优先**：不引入新的中间件（如 Celery、独立 scheduler 进程）
- **运维简单**：不增加额外的进程管理负担

## 方案选型

| 方案 | 原理 | 优点 | 缺点 | 适用 |
|------|------|------|------|------|
| A. Gunicorn `post_fork` + worker 序号 | 在 `post_fork` hook 中判断 `worker.age == 0`，仅 worker-0 启动调度器 | 零外部依赖，纯代码方案，Gunicorn 原生支持 | 单机方案，不支持多机；worker-0 崩溃期间调度中断 | ✅ 单机 |
| B. PostgreSQL advisory lock | 调度器启动时尝试获取 PG advisory lock，拿到的 worker 才启动 | 支持多机；崩溃自动释放锁 | 每次调度循环都要抢锁，有网络往返 | 多机 |
| C. Redis 分布式锁 | 同 B，用 Redis 替代 PG | 同 B | 依赖 Redis 可用性 | 多机 |
| D. 独立 scheduler 进程 | 单独跑一个 Python 进程专门负责调度 | 职责分离最清晰 | 需额外运维（systemd/supervisor），复用业务代码包 | 大规模 |

**选定方案 A**：Gunicorn `post_fork` + worker 序号判断。

理由：
- 当前单机部署，方案 A 足够
- 零外部依赖，不增加运维负担
- 纯代码方案，跟随应用代码版本，无需独立部署
- 时间敏感性低，worker-0 偶发重启导致的短暂中断可接受

## 选定方案详细设计

### 核心机制：`post_fork` hook

Gunicorn 在 master 进程 fork 出每个 worker 后，会调用 `post_fork(server, worker)` 钩子。每个 worker 启动时都能拿到自己的 `worker.age`（从 0 开始的序号）。

通过判断 `worker.age == 0`，只让序号为 0 的 worker 启动调度器，其他 worker 跳过。

### 配置文件

新建 `gunicorn_conf.py`（项目根目录）：

```python
"""
Gunicorn 配置文件

核心职责：
1. 通过 post_fork hook 约束定时任务调度器只在 worker-0 启动，
   避免多 worker 重复执行 scheduled_tasks 表内的任务。
"""

import logging


def post_fork(server, worker):
    """
    worker fork 完成后的钩子。

    只让 worker-0（worker.age == 0）启动定时任务调度器，
    其他 worker 跳过。
    """
    # worker.age 是从 0 开始的 worker 序号
    if worker.age == 0:
        server.log.info(f"[worker-{worker.age}] 启动定时任务调度器")
        try:
            from src.core.scheduler import start_scheduler
            start_scheduler()
            server.log.info(f"[worker-{worker.age}] 定时任务调度器启动成功")
        except Exception as e:
            server.log.error(f"[worker-{worker.age}] 调度器启动失败: {e}", exc_info=True)
    else:
        server.log.info(f"[worker-{worker.age}] 跳过定时任务调度器（仅 worker-0 启动）")


def worker_exit(server, worker):
    """
    worker 退出钩子。

    如果退出的是 worker-0，记录日志便于排查调度中断。
    """
    if worker.age == 0:
        server.log.info(f"[worker-{worker.age}] 调度器随 worker 退出而停止")


# Gunicorn 超时配置（与现有部署保持一致，可按需调整）
timeout = 120
graceful_timeout = 30
```

### 启动命令

部署脚本中将 Gunicorn 启动命令改为加载配置文件：

```bash
gunicorn -c gunicorn_conf.py \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    src.main:app
```

### 调度器入口约定

`src/core/scheduler.py`（待实现）需提供 `start_scheduler()` 函数，**幂等**且**线程内启动**：

- `start_scheduler()` 内部启动一个后台线程运行调度循环
- 调度循环每分钟扫描 `scheduled_tasks` 表，到点的任务触发执行
- **幂等**：多次调用不会启动多个调度线程（用模块级标志位保护）

```python
# src/core/scheduler.py（伪代码示意，待实现）
import threading
from src.db.scheduler_db import SchedulerDB

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="scheduler")
    thread.start()


def _scheduler_loop():
    """调度循环，每分钟扫描 scheduled_tasks 表。"""
    import time
    while True:
        try:
            SchedulerDB.scan_and_execute_due_tasks()
        except Exception as e:
            logger.error(f"调度循环异常: {e}", exc_info=True)
        time.sleep(60)
```

## 容错与兜底

### 场景 1：worker-0 崩溃重启

**现象**：worker-0 进程崩溃，Gunicorn 自动 fork 新的 worker 接替（新 worker 的 `worker.age` 仍为 0）。

**影响**：
- 崩溃到新 worker 启动期间（通常 < 5s），调度器短暂中断
- 新 worker-0 启动后，`start_scheduler()` 重新执行，调度器恢复
- `scheduled_tasks` 是 DB 表驱动，重启后从表里恢复未执行任务

**结论**：时间敏感性低（5-10 分钟可接受），这种秒级中断完全可接受。

### 场景 2：Gunicorn 滚动重启（graceful reload）

**现象**：执行 `kill -HUP <gunicorn_pid>` 触发滚动重启，Gunicorn 逐个重启 worker。期间可能出现**新旧 worker-0 短暂并存**（旧 worker-0 还没退出，新 worker-0 已启动）。

**风险**：两个 worker-0 同时运行调度器，可能重复触发任务。

**兜底方案**：任务执行层加分布式锁，确保同一任务同一时刻只执行一次。

```python
# src/db/scheduler_db.py（伪代码，待实现）
import redis
from src.core.redis_client import RedisClient

def scan_and_execute_due_tasks():
    due_tasks = _query_due_tasks()
    for task in due_tasks:
        _execute_task_with_lock(task)


def _execute_task_with_lock(task):
    """
    任务执行前抢 Redis 锁，防止多 worker-0 并存期间重复执行。
    锁 TTL 设置为任务预期最大执行时长 + 缓冲（如 1 小时）。
    """
    lock_key = f"scheduler:task_lock:{task.id}"
    # SET NX + EX，拿不到锁说明已有 worker 在执行，跳过
    acquired = RedisClient.set(lock_key, "1", nx=True, ex=3600)
    if not acquired:
        logger.info(f"任务 {task.id} 已被其他 worker 执行，跳过")
        return
    
    try:
        _execute_task(task)
        _mark_task_executed(task)
    finally:
        RedisClient.delete(lock_key)
```

**说明**：Redis 锁是兜底，常态下只有 worker-0 一个调度器，锁几乎总能拿到。仅在滚动重启的短暂窗口期内生效。

### 场景 3：Redis 不可用

`RedisClient` 已有降级到内存的实现（见 `.claude/rules/backend_dev.md`）。但内存锁无法跨 worker，滚动重启期间可能失效。

**权衡**：Redis 不可用是罕见情况，且时间敏感性低，可接受极端情况下的偶发重复执行。不为此引入更复杂的方案。

## 落地步骤

| 步骤 | 内容 | 状态 |
|------|------|------|
| 1 | 新建 `gunicorn_conf.py`，实现 `post_fork` hook | 待实现 |
| 2 | 实现 `src/core/scheduler.py` 调度器入口（幂等 + 线程化） | 待实现 |
| 3 | 实现 `src/db/scheduler_db.py` 任务扫描 + Redis 锁兜底 | 待实现 |
| 4 | 修改部署脚本，Gunicorn 启动命令加 `-c gunicorn_conf.py` | 待实现 |
| 5 | 在 `scheduled_tasks` 表登记首批定时任务 | 待实现 |
| 6 | 服务器验证：重启服务后确认仅 worker-0 日志出现"启动调度器" | 待实现 |

## 验证方法

部署后在服务器执行：

```bash
# 查看调度器启动日志，应只在一个 worker 出现
grep "启动定时任务调度器" log/agent/aid-work-agent_*.log | head

# 期望输出（4 worker 场景）：
# [worker-0] 启动定时任务调度器
# [worker-1] 跳过定时任务调度器（仅 worker-0 启动）
# [worker-2] 跳过定时任务调度器（仅 worker-0 启动）
# [worker-3] 跳过定时任务调度器（仅 worker-0 启动）
```

## 关联文档

- [服务器部署现状](../../deploy/服务器部署现状.md) — 当前 Gunicorn 部署架构
- [.claude/rules/backend_dev.md](../../.claude/rules/backend_dev.md) §Gunicorn 多 Worker 进程内存隔离
