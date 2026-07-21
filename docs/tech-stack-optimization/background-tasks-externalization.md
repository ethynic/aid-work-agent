# 后台定时/轮询任务外置

> **状态**：💡 灵感（部分结论已被取代，见下）
> **关联**：[全链路异步化](full-async-migration.md)
> **⚠️ 取代说明（2026-07-21）**：本文「方案 2.1：留在 worker 内 APScheduler」只解决了「多 worker 重复执行」，**未解决**「后台任务占用 HTTP worker / 生命周期耦合 / 无法独立扩缩」。后续由 [独立后台运行时设计](../infrastructure/background-runner-design.md) 取代——把 APScheduler（含本文要迁的 5 循环 + 用户 cron + 发布调度）整体搬到独立于 worker 的进程。本文的「5 循环迁移清单 / 待删除内容 / 风险表」仍然有效，作为后台运行时 P1 阶段的输入。

---

## 1. 问题分析

### 1.1 当前现状

项目后台任务目前分布在两处：

**A. APScheduler（`src/scheduler/manager.py`）** —— 已经解决多 worker 重复执行问题
- 启动时通过 Redis 分布式锁（`src/scheduler/manager.py:43-51`）保证只有抢到锁的 worker 启动调度器，其它 worker 跳过
- 承载：用户在管理后台配置的 cron 任务、每日记忆总结、上下文压缩后台扫描

**B. `main.py` lifespan 中的 `asyncio.create_task`** —— 没有走分布式锁，每个 worker 都跑一份

#### 现有任务清单

| # | 任务 | 启动方式 | 间隔 | 位置 | 多 worker 是否重复 |
|---|------|---------|------|------|-------------------|
| 1 | PostgreSQL 连接池初始化 | 同步 | 启动一次 | main.py lifespan | 否（每个 worker 独立池） |
| 2 | 数据库初始化 | 同步 | 启动一次 | main.py lifespan | 否 |
| 3 | 子智能体定义加载 | 同步 | 启动一次 | main.py lifespan | 否 |
| 4 | 日志数据库池初始化 | 同步 | 启动一次 | main.py lifespan | 否 |
| 5 | Channel Session Manager | 同步 | 启动一次 | main.py lifespan | 否 |
| 6 | Scheduled Task Scheduler | APScheduler + Redis 锁 | 程序化 | src/scheduler/manager.py | 否（已用锁） |
| 7 | SaaS Instance Manager 恢复 | 同步 | 启动一次 | main.py lifespan | 否 |
| 8 | **Memory Cleanup Loop** | `asyncio.create_task` | `settings.memory.cleanup_interval` 秒 | main.py:384 | ⚠️ 是 |
| 9 | **Dedup Cleanup Loop** | `asyncio.create_task` | 每天凌晨 03:00 | main.py:409 | ⚠️ 是 |
| 10 | **Instance Lock Cleanup Loop** | `asyncio.create_task` | 每 60s | main.py:435 | ⚠️ 是（且功能拟废弃） |
| 11 | **WeCom KF Timeout Check Loop** | `asyncio.create_task` | 每 60s | main.py:615 | ⚠️ 是 |
| 12 | **WeCom Personal RPA Archive Poller** | `await _archive_poller.start()` | 每 60s | main.py | ⚠️ 是（内部有 Redis 锁防重，但架构不干净） |

> **关键事实**：APScheduler（第 6 项）已经通过 Redis 锁解决了多 worker 重复执行问题，无需外置。真正需要处理的是第 8-12 项的 `asyncio.create_task` 循环。

#### 其他文件中的 `asyncio.create_task`（影响评估）

| # | 位置 | 用途 | 是否受多 worker 影响 |
|---|------|------|---------------------|
| 1 | `src/llm/key_pool.py:148` | 并发抢 Key 槽位 | 否（每个 worker 独立管理自己的 Key 池） |
| 2 | `src/tools/browser/session.py:128` | 关闭浏览器会话 | 否（单 worker 内资源清理） |
| 3 | `src/core/session_queue.py:780` | Session 锁续期 watchdog | ⚠️ 是（同一 Session 可能被多个 worker 处理） |
| 4 | `src/subagents/executor.py:263` | 子智能体异步执行 | 否（单 session 内执行） |
| 5 | `src/channels/wecom_personal_rpa/archive/poller.py:119` | 存档轮询主循环 | ⚠️ 是（有 Redis 锁保护，但架构不干净） |
| 6-8 | `src/saas/api/channel_routes.py` | 渠道消息后台处理 | 否（每个请求独立） |

### 1.2 核心问题

#### 问题 1：5 个 `asyncio.create_task` 循环未走分布式锁

- Memory Cleanup Loop -> 4-8 个进程同时在清理，浪费资源但功能正常
- Dedup Cleanup Loop -> 4-8 个进程同时在凌晨 3 点清理去重表，可能产生锁冲突
- WeCom RPA Archive Poller -> 虽然有 Redis 分布式锁保护，但 4-8 个进程争抢锁，浪费 CPU
- Instance Lock Cleanup -> 4-8 个进程同时检查，且功能拟废弃
- WeCom KF Timeout Check -> 4-8 个进程同时检查超时

#### 问题 2：双轨调度并存

APScheduler 和 `asyncio.create_task` 两套调度机制并存，运维和监控口径不一致。

#### 问题 3：进程生命周期耦合

- worker 重启/崩溃 -> 后台任务中断
- 部署新版本时，旧 worker 的 `lifespan` cleanup 和新 worker 的 startup 交叉，可能状态不一致
- 后台任务中的错误可能导致 worker 静默退出（已知企微 SDK 子进程隔离就是为此）

#### 问题 4：无法独立扩缩

- 需要更多 worker 处理 HTTP 请求，但同时也会增加后台任务数量
- 无法将后台任务和 HTTP 服务独立部署、独立伸缩

---

## 2. 改造方案

### 2.1 推荐方案：迁移到现有 APScheduler + Redis 锁

**不引入 arq**，把 `main.py` lifespan 中的 5 个 `asyncio.create_task` 循环迁移到现有 `src/scheduler/manager.py` 的 APScheduler 中，复用已有的 Redis 锁。

#### 不选 arq 的理由

| 维度 | arq 方案 | 复用现有 APScheduler |
|------|---------|---------------------|
| 新依赖 | `arq>=0.26.0` | 0 |
| 新进程/部署单元 | 1 个独立 arq worker 进程 + Docker service | 0 |
| 命名冲突 | 与现有 `src/scheduler/` 目录冲突（该目录已承载完整的用户配置 cron 任务系统） | 无 |
| 多 worker 防重 | arq cron + Redis | 已有 Redis 锁（`manager.py:43-51`） |
| 与现有任务一致性 | 双轨并存（APScheduler + arq） | 单一调度器 |
| 5 个候选任务场景匹配度 | 一般（arq 优势在延迟任务/结果持久化） | 高（全是 cron 周期任务） |
| 迁移工作量 | 约 7-8 人天 | 约 2-3 人天 |

5 个候选任务全部是周期性 cron 任务，没有任何一个是「延迟任务」或「用户触发的异步任务」——而后者才是 arq 真正擅长的场景。引入 arq 等于为不存在的需求增加新依赖、新部署单元和双轨调度复杂度。

#### arq 何时才值得引入（未来场景）

如果未来出现以下场景，再考虑引入 arq：

1. **大量延迟任务**：用户操作后 N 分钟/小时执行（如「2 小时后自动关闭会话」），arq 的 `enqueue_job(defer_until=...)` 比 APScheduler `DateTrigger` 更优雅，且任务状态可查。
2. **任务结果需要持久化与查询**：当前 5 个清理任务都只返回 `cleaned_count`，无需 arq 的结果持久化。
3. **任务需独立扩缩容**：当前清理任务负载极低，独立扩缩容价值不大。

### 2.2 架构变化

```
改造前：
  Gunicorn Master
    ├── Worker 1  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...
    ├── Worker 2  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！
    ├── Worker 3  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！
    └── Worker 4  ─ lifespan: memory_cleanup + dedup_cleanup + rpa_poller + ...  ← 重复！

  （APScheduler 走 Redis 锁，仅在抢到锁的 worker 中运行 —— 这部分无需改）

改造后：
  Gunicorn Master
    ├── Worker 1  ─ lifespan: 轻量（只启动资源初始化）+ APScheduler 抢锁启动
    ├── Worker 2  ─ lifespan: 轻量（APScheduler 抢锁失败，跳过）
    ├── Worker 3  ─ lifespan: 轻量（APScheduler 抢锁失败，跳过）
    └── Worker 4  ─ lifespan: 轻量（APScheduler 抢锁失败，跳过）

  APScheduler（抢到锁的 worker 中运行）
    ├── cron/interval: memory_cleanup       按 settings.memory.cleanup_interval
    ├── cron:           dedup_cleanup        每天 03:00
    ├── interval:       wecom_kf_timeout     每 60s
    └── interval:       rpa_archive_poller   每 60s
```

### 2.3 任务迁移清单

| 原任务 | 目标 | 类型 | 说明 |
|--------|------|------|------|
| Memory Cleanup Loop | APScheduler `IntervalTrigger` | `interval` | 复用现有 Redis 锁，全局唯一执行 |
| Dedup Cleanup Loop | APScheduler `CronTrigger` | `cron` | 每天 03:00，全局唯一 |
| ~~Instance Lock Cleanup Loop~~ | **不迁移，待删除** | - | 智能体实例并发控制功能拟废弃，直接删除 `main.py:435` 及对应函数 |
| WeCom KF Timeout Check | APScheduler `IntervalTrigger` | `interval` | 每 60s，全局唯一 |
| RPA Archive Poller | APScheduler `IntervalTrigger` | `interval` | 每 60s，兜底轮询，复用现有内部 Redis 锁防重 |
| APScheduler 用户配置任务 | 保持现状 | - | 已有锁保护 |
| Session Queue heartbeat | **保留在 worker 内** | `asyncio.create_task` | 与 request 生命周期绑定 |

### 2.4 实现要点

参照现有 `_register_system_jobs`（`src/scheduler/manager.py:80`）和 `_run_memory_summarizer`（`src/scheduler/manager.py:127`）的模式：

```python
# src/scheduler/manager.py: _register_system_jobs() 中新增

# 1. Memory Cleanup（按配置间隔）
self._scheduler.add_job(
    self._run_memory_cleanup,
    IntervalTrigger(seconds=settings.memory.cleanup_interval),
    id="job_system_memory_cleanup",
    name="Memory Cleanup",
    max_instances=1,
    coalesce=True,
)

# 2. Dedup Cleanup（每天 03:00）
self._scheduler.add_job(
    self._run_dedup_cleanup,
    CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
    id="job_system_dedup_cleanup",
    name="Daily Dedup Cleanup",
    max_instances=1,
    coalesce=True,
)

# 3. WeCom KF Timeout Check（每 60s）
self._scheduler.add_job(
    self._run_wecom_kf_timeout_check,
    IntervalTrigger(seconds=60),
    id="job_system_wecom_kf_timeout",
    name="WeCom KF Timeout Check",
    max_instances=1,
    coalesce=True,
)

# 4. RPA Archive Poller（每 60s）
self._scheduler.add_job(
    self._run_rpa_archive_poll,
    IntervalTrigger(seconds=60),
    id="job_system_rpa_archive_poll",
    name="RPA Archive Poller",
    max_instances=1,
    coalesce=True,
)
```

回调函数沿用现有 `_run_memory_summarizer` 的 `asyncio.new_event_loop()` 模式（APScheduler `BackgroundScheduler` 在后台线程中执行回调，不能直接 `await`）：

```python
# src/scheduler/manager.py
def _run_memory_cleanup(self):
    """Memory Cleanup（APScheduler 回调，后台线程执行）"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cleaned = master_agent.memory.cleanup_expired()
        if cleaned > 0:
            logger.debug(f"后端日志：Memory cleanup: cleaned {cleaned} expired sessions")
    except Exception as e:
        logger.error(f"后端日志：Memory cleanup error: {e}", exc_info=True)
    finally:
        try:
            loop.close()
        except Exception:
            pass
```

> **注意**：迁移时需保留原循环中的异常隔离逻辑（如 `_dedup_cleanup_loop` 对 `psycopg2.OperationalError` 的降级处理、`_instance_lock_cleanup_loop` 的 `error_cooldown` 暂停机制——后者随功能一并删除）。

### 2.5 部署方式

**无需新增部署单元**。原有的 Gunicorn + Worker 模式不变：

```bash
# 不变
gunicorn src.main:app -w 4 -k uvicorn.workers.UvicornWorker
```

APScheduler 仍在抢到 Redis 锁的 worker 中运行，Docker Compose / k8s 不需要新增 `scheduler` 服务。

### 2.6 待删除内容

`main.py` lifespan 中删除以下 `asyncio.create_task` 循环及其对应的内部函数定义：

- `_memory_cleanup_loop()` 及其 `asyncio.create_task` 调用（main.py:384）
- `_dedup_cleanup_loop()` 及其 `asyncio.create_task` 调用（main.py:409）
- `_instance_lock_cleanup_loop()` 及其 `asyncio.create_task` 调用（main.py:435）—— **直接删除，不迁移**
- `_wecom_kf_timeout_check_loop()` 及其 `asyncio.create_task` 调用（main.py:615）
- RPA Archive Poller 的 `await _archive_poller.start()` 调用

**`instance_lock_cleanup` 处理**：该任务对应的智能体实例并发控制功能已标记为拟废弃，**不迁移到 APScheduler，直接删除**。

删除范围（已确认调用方）：
- `main.py:414-435` —— `_instance_lock_cleanup_loop` 函数定义及 `asyncio.create_task` 调用
- `src/saas/services/instance_service.py:603` —— `cleanup_all_expired_locks` 静态方法（全项目仅 `main.py:426` 一处调用，删除 loop 后该方法无引用，可一并清理）

> 删除前需再次确认 `instance_service.py` 中其它与实例锁相关的代码是否也属于废弃范围（如 `auto_release_expired_lock` 等），若整体功能废弃应一并清理，避免遗留死代码。

---

## 3. 风险与注意事项

| 风险 | 缓解措施 |
|------|---------|
| APScheduler 回调在后台线程中执行，不能直接 `await` | 沿用现有 `_run_memory_summarizer` 的 `asyncio.new_event_loop()` 模式 |
| Redis 锁过期（ex=300）后多个 worker 同时启动调度器 | 锁续期机制已存在；任务 `max_instances=1` + `coalesce=True` 双重保险 |
| 原 `asyncio.create_task` 循环的异常处理语义可能不同 | 逐个排查现有循环的 try/except 逻辑，迁移时保留同等异常隔离（如 `psycopg2.OperationalError` 降级） |
| RPA Archive Poller 内部已有 Redis 锁防重 | 迁移后 APScheduler 全局唯一 + 内部 Redis 锁，两层防重冗余但不冲突 |
| `session_queue.py` 的 watchdog 不宜外置 | 保留在 HTTP worker 进程内，与本方案无关 |
| `instance_lock_cleanup` 删除需确认无其他调用方 | 已确认 `cleanup_all_expired_locks` 仅 `main.py:426` 一处调用；删除时连带清理 `instance_service.py` 中的死代码 |
| SaaS 模式下 `instance_lock_cleanup` 受 `settings.saas.enabled` 开关保护 | 删除时连同 `if settings.saas.enabled:` 分支一并清理 |

---

## 4. 实施步骤

| 阶段 | 内容 | 工作量 |
|------|------|-------|
| Phase 1 | 在 `_register_system_jobs` 中新增 Memory Cleanup 任务 | 0.25 天 |
| Phase 2 | 在 `_register_system_jobs` 中新增 Dedup Cleanup 任务 | 0.25 天 |
| Phase 3 | 在 `_register_system_jobs` 中新增 WeCom KF Timeout Check 任务 | 0.5 天 |
| Phase 4 | 在 `_register_system_jobs` 中新增 RPA Archive Poller 任务（保留内部 Redis 锁） | 0.5 天 |
| Phase 5 | **删除** Instance Lock Cleanup Loop 及 `cleanup_all_expired_locks` 死代码 | 0.25 天 |
| Phase 6 | 清理 main.py lifespan 中的 4 个 `asyncio.create_task` 循环及内部函数 | 0.25 天 |
| Phase 7 | 集成测试：验证多 worker 环境下任务仅执行一次 | 0.5 天 |

**总计**：约 2-3 人天
