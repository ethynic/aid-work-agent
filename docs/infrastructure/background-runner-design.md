# 独立后台运行时设计（Background Runner）

> 日期：2026-07-21
> 状态：设计中（编码前待确认部署形态）
> 取代：[tech-stack-optimization/background-tasks-externalization.md](../tech-stack-optimization/background-tasks-externalization.md)（#38）的「留在 worker 内 APScheduler」结论
> 关联消费方：[S1 发布调度执行框架](../system/digital-employee/publish-dispatcher-design.md)

## 1. 背景与目标

### 1.1 现状
- 生产部署：单容器 `aid-agent-api`，Gunicorn + UvicornWorker（默认 9 worker），入口固定 `gunicorn src.main:app`。
- 后台任务分两类：
  - **APScheduler**（`src/scheduler/manager.py`）：靠 Redis 分布式锁，仅抢到锁的 1 个 worker 启动调度器，承载用户 cron 任务 + 每日记忆总结 + 上下文压缩扫描。**但调度器线程仍跑在那个 HTTP worker 进程内。**
  - **`asyncio.create_task` 循环**（main.py lifespan）：5 个循环（memory/dedup/kf-timeout/rpa-poller/instance-lock）每个 worker 都跑一份（#38 待处理）。

### 1.2 问题（用户定调）
后台定时任务与 FastAPI worker 绑定：
1. **占用 worker**：调度器 + 后台循环跑在 HTTP worker 进程内，消耗其内存/CPU，且与请求处理争抢资源。
2. **生命周期耦合**：worker 重启/崩溃 → 后台任务中断；滚动部署时新旧 worker 的后台任务交叉。
3. **无法独立扩缩**：加 worker 处理 HTTP 会同时增加后台任务副本（即便有锁也增加争抢与开销）。

### 1.3 目标
**后台定时任务运行在完全独立于 HTTP worker 的进程中**，不占用任何 worker；HTTP worker 只管 HTTP。后台运行时可独立部署、独立重启、独立扩缩。

## 2. 与 #38 的关系（supersede）

#38 调研了「外置后台任务」，结论是「不引入 arq，把 5 个 `asyncio.create_task` 循环迁进现有 APScheduler（仍在 worker 内）」——它解决的是「多 worker 重复执行」，**没解决**「占用 worker / 生命周期耦合 / 独立扩缩」。

本设计在 #38 基础上**再前进一步**：把 APScheduler（含系统任务 + 用户 cron + 迁入的循环 + 新的发布调度）整体从 HTTP worker **搬到独立进程**。#38 的「迁循环进 APScheduler」清单仍然成立，只是 APScheduler 不再寄居 worker。

> #38 文档需加 supersede 指向本文。

## 3. 方案选型

| 方案 | 新依赖 | 是否占用 worker | 独立扩缩 | 评估 |
|------|--------|----------------|---------|------|
| **A. 独立 APScheduler 进程**（复用现有） | 0 | 否（独立进程） | 是 | ✅ **推荐**：零新依赖，复用已建好的 APScheduler+Redis 锁+系统任务+用户 cron，只是换个进程启动 |
| B. arq（Redis 队列） | arq + 新 worker 进程 | 否 | 是 | ❌ #38 已论证：当前全是周期 cron，无延迟任务/结果持久化需求，为不存在场景加依赖+双轨调度 |
| C. 自写 asyncio 轮询进程 | 0 | 否 | 是 | ❌ 丢掉 APScheduler 已有的 cron/interval/misfire/持久化能力，重复造轮子 |

**结论：方案 A**。后台运行时 = 一个独立进程，入口 `python -m src.background_runner`，启动 `BackgroundScheduler` + 注册全部后台任务，**不启动 FastAPI/Gunicorn**。最轻量、零新依赖，符合「轻量优先」。

## 4. 架构与进程模型

```
改造前：
  aid-agent-api 容器
    └── Gunicorn
          ├── Worker 1 (HTTP + APScheduler[抢到锁] + asyncio loops)  ← 后台任务寄生
          ├── Worker 2 (HTTP + asyncio loops)                         ← 循环重复
          ├── Worker 3 (HTTP + asyncio loops)                         ← 循环重复
          └── Worker 4 (HTTP + asyncio loops)                         ← 循环重复

改造后：
  aid-agent-api 容器（HTTP）
    └── Gunicorn
          ├── Worker 1 (纯 HTTP)
          ├── Worker 2 (纯 HTTP)
          └── Worker N (纯 HTTP)              ← 不再启动调度器/后台循环

  aid-agent-background 容器（后台运行时，独立）       ← 新增
    └── python -m src.background_runner
          └── BackgroundScheduler（Redis 锁单实例）
                ├── 系统任务：每日记忆总结 / 上下文压缩扫描
                ├── 用户 cron 任务（ScheduledTaskExecutor，lazy 加载 master_agent）
                ├── 迁入循环：memory_cleanup / dedup_cleanup / wecom_kf_timeout / rpa_archive_poller
                └── S1 发布调度：PublishJobDispatcher（每分钟）
```

后台运行时进程**不监听 HTTP**，只跑调度器。`master_agent` 等重资源 lazy 加载（仅在用户 cron 任务真正触发执行时才构造），启动开销可控。

## 5. 部署形态（已确认：A 新增独立 Docker 服务）

**A：新增独立 Docker 服务 `aid-agent-background`**
- `docker-compose.prod.yml` 加 `aid-agent-background`：同镜像，`command` 覆盖为 `sh -c "/usr/local/bin/fix_tmp.sh && exec gosu appuser python -m src.background_runner"`，复用同一套 volumes/env（代码、配置、日志、storage、redis），不映射 HTTP 端口。
- 健康检查：后台进程无业务 HTTP，用进程存活检查（`test: ["CMD-SHELL", "pgrep -f background_runner || exit 1"]`）。
- 资源限额独立配置（cpu/memory reservations 低于 HTTP 容器）。
- 与 HTTP 容器完全解耦：独立重启/扩缩/资源限额。部署团队多管 1 个容器。

## 6. 任务归属（分阶段）

| 任务 | 归属 | 阶段 |
|------|------|------|
| PublishJobDispatcher（S1） | background runner | **P0（本期随 S1）** |
| 每日记忆总结 / 上下文压缩扫描 | background runner | P0（已是 APScheduler 系统任务，随进程迁移） |
| 用户 cron 任务（ScheduledTaskExecutor） | background runner | P0（已是 APScheduler，随进程迁移） |
| memory_cleanup / dedup_cleanup / wecom_kf_timeout / rpa_archive_poller（#38 的 5 循环） | background runner | P1（#38 迁移工作，独立于 S1） |
| instance_lock_cleanup | 删除（#38 已定） | P1 |
| session_queue heartbeat watchdog | 留在 HTTP worker（与 request 生命周期绑定） | 不动 |

> P0 只需把现有 APScheduler 搬进程 + 接 S1；#38 的 5 循环迁移是 P1，可独立排期，不阻塞 S1。

## 7. 单实例与启停

- **Redis 锁迁移**：`scheduled_task_manager.start()` 的 Redis 锁（`CacheKeys.SCHEDULER_LOCK`）现在由 background runner 持有；HTTP worker 不再启动调度器。
- **mode gating**：新增开关控制「是否在当前进程启动调度器」：
  - HTTP worker（`SERVER_MODE=fastapi` 且未设 `RUN_BACKGROUND_IN_WORKER`）→ **不启动**调度器、不启动 5 循环。
  - background runner（`SERVER_MODE=background`）→ 启动调度器 + 注册全部后台任务。
  - 兼容回退：设 `RUN_BACKGROUND_IN_WORKER=true` 时 worker 仍可启动调度器（单容器/开发/灰度回退用）。
- **优雅停机**：background runner 捕获 SIGTERM → `scheduled_task_manager.shutdown()`（释放锁、停 APScheduler）。

## 8. S1 发布调度接入

S1 的 `PublishJobDispatcher` **注册到 background runner 的 APScheduler**（每分钟 interval 系统任务），**不再写进 main.py lifespan**。

- `src/background_runner.py`：初始化资源 → `scheduled_task_manager.start()` → 在 `_register_system_jobs` 新增 `PublishJobDispatcher` interval 任务。
- `main.py` lifespan：移除 `scheduled_task_manager.start()`（mode gating 后 worker 不启动）。
- 对应更新 [publish-dispatcher-design.md](../system/digital-employee/publish-dispatcher-design.md) §8/§9。

## 9. 配置

- `SERVER_MODE` 增 `background` 取值；`RUN_BACKGROUND_IN_WORKER`（bool，默认 false）作兼容回退。
- `settings.py` + `configs/config.yaml` 同步字段名。
- 后台运行时按需暴露极轻量 HTTP `/healthz`（仅 200 OK，供 compose healthcheck；非业务端口，可选）。

## 10. 迁移与回滚

- **渐进**：mode gating 保证可灰度。先部署 background runner 容器（跑调度器），同时保留 worker 的 `RUN_BACKGROUND_IN_WORKER=true` 兜底（双跑由 Redis 锁去重，不会重复执行）；验证稳定后关掉 worker 内启动。
- **回滚**：停 background 容器，worker 设 `RUN_BACKGROUND_IN_WORKER=true` 恢复旧行为。
- 数据无变更（纯进程/部署调整）。

## 11. 风险

| 项 | 说明 |
|----|------|
| 后台运行时需要 master_agent | 用户 cron 任务执行依赖 agent 全栈；lazy 加载缓解启动开销，但首次触发会重载（可接受） |
| 后台进程崩溃无人接管 | 靠容器 `restart: unless-stopped` + pgrep 健康检查自动拉起；Redis 锁 TTL 防止双跑 |
| 渠道回调仍走 HTTP worker | 渠道（企微/钉钉/飞书）消息处理是 HTTP 请求触发，留 worker 内，不进 background runner |
| 5 循环迁移异常语义 | P1 迁移时逐个保留原 try/except 降级（#38 §3 已列） |
| 改动触及启动链路 | P0 改 main.py/settings/compose，属高风险「提交即上线」范畴，须走完整三智能体流程 + 启动安全终检，不盲提交 |

## 12. 实施阶段

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P0.1 | `src/background_runner.py` 入口 + mode gating（settings/compose） | — |
| P0.2 | main.py lifespan 按 mode 取消 worker 内调度器启动（保留回退开关） | P0.1 |
| P0.3 | compose 新增 `aid-agent-background` 服务（按确认的 A/B） | P0.1 |
| P0.4 | S1 PublishJobDispatcher 注册到 background runner | S1 实现 |
| P1 | #38 的 5 循环迁入 APScheduler（已在 background runner 内） | P0 |
| P2 | 验证稳定后关闭 worker 回退开关；清理 instance_lock 死代码 | P1 |

> P0 是 S1 的前置依赖：S1 的 Dispatcher 要跑在 background runner 上，需先有 runner（P0.1–P0.3）。
