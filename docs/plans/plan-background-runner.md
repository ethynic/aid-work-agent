# 独立后台运行时开发计划（Background Runner）

> 日期：2026-07-21（v3：一次性完成，4 循环迁移并入主范围，不分 P0/P1）
> 状态：📋 待开发
> 设计文档：[background-runner-design.md](../infrastructure/background-runner-design.md)
> 上级条目：[ideas.md](../ideas.md) #38、#20 社媒营销 S1 消费方
> 关联：[publish-dispatcher-design.md](../system/digital-employee/publish-dispatcher-design.md) §8；循环迁移清单见本文 §3.8

---

## 0. 调研结论（编码前已澄清的事实）

1. **调度器唯一启动点**：`scheduled_task_manager.start()` 全仓仅 `src/main.py:468`（shutdown `:769`）。
2. **`SERVER_MODE` 当前未被任何 Python 读取**——只是 Docker env 标签 + Dockerfile 默认值。gradio/fastapi 靠 compose `command`/`entrypoint` 分流。
3. **`scheduled_task_manager`（manager.py:318）是轻量单例**：`import` 它不构造 `master_agent`。
4. **当前 4 个循环机制**（`instance_lock_cleanup` 已删除，仅剩 main.py:514-515 孤儿注释）：
   - `_memory_cleanup_loop`（main.py:487）：`master_agent.memory.cleanup_expired()`，**cleanup_expired 是同步**（`src/memory/manager.py:323`）。
   - `_dedup_cleanup_loop`（main.py:512）：`MessageDeduplicator.cleanup_expired()`，**同步**（`idempotency.py:86`），保留 `psycopg2.OperationalError` 降级。
   - `_wecom_kf_timeout_check_loop`（main.py:519，saas 门控）：`async def`，`await asyncio.sleep(60)` + 同步 DB（`get_db_connection`）+ 渠道适配器调用。
   - `_archive_poller`（main.py:703）：`ServerArchivePoller` 类，只有 `async def start()/stop()`（poller.py:107/126），**自带内部循环 + Redis 锁，无单次 tick 方法**。
5. **记忆类系统任务 standalone**：`run_memory_summarization()` / `run_background_compression_scan()` 都不 import master_agent，搬迁零风险。
6. **任务 CRUD 与调度器同进程耦合**：API（`src/api/scheduled_task.py:142/167/189/212/263`）+ agent 工具（`src/tools/scheduler/scheduled_task_tool.py:208/281/288/295`）直接调 `manager.register/pause/resume/remove/trigger`。`trigger` 不写 DB。→ 必须 reconcile 对账替代 + 新增 `manual_trigger_at` 列。
7. **Redis 锁无续期**（`ex=300`）；单 background 容器场景仅作防误开副本兜底，P0 不改。
8. **部署**：`docker-compose.prod.yml` 单服务 `aid-agent-api`，无显式 command；`agent_update.sh:47` 用 `up -d --wait`（新服务必须带可靠 healthcheck）；宿主 4 核。表已就绪（`scheduled_tasks`、`social_publish_jobs` 在 init-postgres.sql）。
9. **其他文件中的 `asyncio.create_task`**（原 #38 调研遗留，迁移决策输入）：
   - `src/llm/key_pool.py`：并发抢 Key 槽位，每个 worker 独立管理自己的 Key 池，**不外置**。
   - `src/tools/browser/session.py`：单 worker 内浏览器会话资源清理，**不外置**。
   - `src/subagents/executor.py`：子智能体异步执行，单 session 内执行，**不外置**。
   - `src/saas/api/channel_routes.py`：渠道消息后台处理，每个请求独立，**不外置**。
   - `src/core/session_queue.py` watchdog：与 request 生命周期绑定的锁续期，**留 HTTP worker**（§8 排除项已明确）。
   - `src/channels/wecom_personal_rpa/archive/poller.py`：已迁入 background（§3.3）。

---

## 1. 决策表（全部已拍板）

> 设计原则：**调度器 + 全部后台任务只在 background 容器跑；API/worker 只写配置到 DB，不跑任何后台任务。一次性完成，不分期。**

| # | 议题 | 选定方案 |
|---|------|---------|
| D1 | 调度器归属 | background 容器独占；main.py 直接删 `start()`，无开关 |
| D2 | settings | **不新增字段**（server_mode 经评估无分支依赖、纯标签，违反「如无必要不添加」，已移除）；进程角色由入口决定 |
| D3 | healthcheck | 心跳文件法（runner 每 30s 写 `storage/.bg_runner_alive`，`find -mmin -2` 判活）；不开 HTTP、不用 pgrep |
| D4 | runner 初始化 | setup_logging → init_postgres_pool(关键) → init_database(关键) → init_logs_pool(非关键) → **channel_session_manager._ensure_tables()**（wecom_kf/poller 需要 channel 表） |
| D5 | master_agent | 懒加载；首个用户 cron / memory_cleanup / poller inbound 触发时构造 |
| D6 | 资源限额 | reserve 0.25 CPU / 256M，limit 1 CPU / 1G |
| D7 | 范围 | **一次性**：调度器搬家 + reconcile + 触发机制 + API/工具解耦 + **4 循环全部迁移** + main.py 清理 + compose + S1 钩子 |
| D8 | 单实例 | compose 隐式 1 副本 + Redis 锁兜底；禁止 scale>1 |
| D9 | 优雅停机 | SIGTERM/SIGINT → 停 poller + shutdown 调度器 + 关池 + 退出 |
| D10 | S1 接入 | `_register_system_jobs()` 预留 interval 注册位，dispatcher 随 S1 落地 |
| D11 | 配置同步 | background 每 30s 扫 `scheduled_tasks` reconcile（按 `updated_at` 变化重注册/移除）；API/工具去掉 manager 调用只写 DB |
| D12 | 手动触发 | 新增 `scheduled_tasks.manual_trigger_at` 列；reconcile 扫到非空跑一次再清空；≤30s（用户已确认接受 CRUD/触发 ≤30s） |
| **D13** | **poller 迁移方式** | **不改 poller 内部**；background runner 主线程跑 asyncio loop，把 `_archive_poller.start()` 作为兄弟异步任务启动（poller 自带 Redis 锁单实例）；停机调 `.stop()` |
| **D14** | **wecom_kf 迁移** | 抽取循环体为 async tick，APScheduler interval 60s 回调里用新 event loop 跑（同 `_run_memory_summarizer` 模式） |
| **D15** | **memory/dedup 迁移** | cleanup_expired 同步，APScheduler interval/cron 回调直接调 |

---

## 2. 改动清单

| 文件 | 动作 | 内容 |
|------|------|------|
| `src/background_runner.py` | 新 | 入口：asyncio 主 loop + init + 心跳 + 启 scheduler + 启 poller + 信号停机 |
| `src/main.py` | 改 | 删 `scheduled_task_manager.start()`(464-472)；删 4 循环定义及启动(`_memory_cleanup_loop`/`_dedup_cleanup_loop`/`_wecom_kf_timeout_check_loop`/`_archive_poller.start()+.stop()`)；清 instance_lock 孤儿注释(514-515) |
| `src/scheduler/manager.py` | 改 | 新增 `_reconcile()` + 30s 注册；新增 `_run_memory_cleanup`/`_run_dedup_cleanup`/`_run_wecom_kf_timeout` 回调 + 在 `_register_system_jobs` 注册；`_fire_once`；S1 钩子 |
| `src/scheduler/db.py` | 改 | `list_all_for_reconcile()` / `request_manual_trigger()` / `clear_manual_trigger()` |
| `src/api/scheduled_task.py` | 改 | create/update/pause/resume/cancel 去 manager 调用只写 DB；trigger 走 `request_manual_trigger` |
| `src/tools/scheduler/scheduled_task_tool.py` | 改 | 同上 |
| `deploy/init-postgres.sql` + `deploy/db_update.sql` | 改 | `scheduled_tasks` 加 `manual_trigger_at TIMESTAMP NULL` |
| `docker-compose.{prod,prod-eas,dev,test,local}.yml` | 改 | 新增 `aid-agent-background` 服务 |

---

## 3. 详细方案

### 3.1 settings + config（D2：不新增字段）

经评估 `server_mode` 无任何代码分支依赖（纯日志标签），违反「如无必要不添加」，**不新增**。进程角色由入口决定：`gunicorn src.main:app`（HTTP）/ `python -m src.background_runner`（后台）/ `python gradio_app.py`（调试）。settings.py / config.yaml / .env.example 净零改动。compose 内 `SERVER_MODE=background` 仅作容器角色标签（覆盖 Dockerfile 默认值），Python 不读取。

### 3.2 main.py（D1）

- **删除 `:464-472`**（`scheduled_task_manager.start()` 整块）。
- **删除 4 循环**：`_memory_cleanup_loop`(475-487) + `asyncio.create_task`、`_dedup_cleanup_loop`(490-512) + create_task、`_wecom_kf_timeout_check_loop`(519-696) + create_task、`_archive_poller.start()`(703) + shutdown 的 `.stop()`(753)。
- 清 instance_lock 孤儿注释(514-515)。
- 保留：init_postgres_pool / init_database / init_logs_pool / channel_session_manager._ensure_tables / subagent defs 加载（HTTP 仍需）。

### 3.3 src/background_runner.py（新，D4/D5/D9/D13）

主线程跑 asyncio loop（承载 poller + 心跳），APScheduler 在其后台线程跑：

```python
"""独立后台运行时：SERVER_MODE=background。不启 FastAPI。"""
import asyncio, os, signal
from loguru import logger

HEARTBEAT_FILE = os.path.join("storage", ".bg_runner_alive")
HEARTBEAT_INTERVAL = 30
_stop = asyncio.Event()

def _init_resources():
    from src.config.logging import setup_logging
    setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"), log_dir="log/agent")
    from src.db.database import init_postgres_pool, init_database, init_logs_pool
    init_postgres_pool()                       # 关键
    init_database()                            # 关键
    try: init_logs_pool()                      # 非关键
    except Exception as e: logger.warning(f"logs pool init failed: {e}")
    try:
        from src.channels.session import channel_session_manager  # wecom_kf/poller 需要 channel 表
        channel_session_manager._ensure_tables()
    except Exception as e: logger.warning(f"channel tables ensure failed: {e}")

async def _heartbeat():
    while not _stop.is_set():
        try:
            os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
            open(HEARTBEAT_FILE, "w").write(str(asyncio.get_event_loop().time()))
        except Exception as e: logger.warning(f"heartbeat write failed: {e}")
        await asyncio.sleep(HEARTBEAT_INTERVAL)

async def _run():
    logger.info("background runner 启动")
    _init_resources()
    # 1. APScheduler（线程化，非阻塞）
    from src.scheduler.manager import scheduled_task_manager
    scheduled_task_manager.start()             # 抢 Redis 锁；失败(已有副本)→return 空转
    # 2. archive poller（兄弟异步任务，自带 Redis 锁）
    _poller = None
    try:
        from src.channels.wecom_personal_rpa.archive.poller import poller as _poller
        await _poller.start()
    except Exception as e:
        logger.error(f"archive poller 启动失败（不影响 runner）: {e}", exc_info=True)
    # 3. 心跳
    asyncio.create_task(_heartbeat())
    logger.info("background runner 就绪")
    await _stop.wait()
    # 停机
    try:
        if _poller: await _poller.stop()
    except Exception as e: logger.warning(f"poller stop 异常: {e}")
    try: scheduled_task_manager.shutdown()
    except Exception as e: logger.error(f"scheduler shutdown error: {e}", exc_info=True)
    try:
        from src.db.database import close_postgres_pool, close_logs_pool
        close_postgres_pool(); close_logs_pool()
    except Exception: pass

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    def _on_signal(*_):
        logger.info("background runner 收到停止信号")
        loop.call_soon_threadsafe(_stop.set)
    signal.signal(signal.SIGTERM, _on_signal)   # Linux 容器；add_signal_handler 亦可
    signal.signal(signal.SIGINT, _on_signal)
    loop.run_until_complete(_run())
    loop.close()

if __name__ == "__main__": main()
```
要点：不 import master_agent；poller 复用其内部循环+Redis 锁不改；信号经 `call_soon_threadsafe` 跨线程唤醒 asyncio Event。

### 3.4 compose 服务（D3/D6/D8）

`docker-compose.prod.yml` 追加（其余 compose 镜像同款，调挂载路径）：
```yaml
  aid-agent-background:
    build: { context: ., dockerfile: Dockerfile }
    image: aid-agent-api:latest
    container_name: aid-agent-background
    restart: unless-stopped
    env_file: [.env]
    environment:
      - SERVER_MODE=background
      - LOG_LEVEL=${LOG_LEVEL:-INFO}
    entrypoint: ["sh", "-c"]
    command: ["/usr/local/bin/fix_tmp.sh && exec gosu appuser python -m src.background_runner"]
    volumes:
      - .:/app
      - ./log:/app/log
      - ./configs:/app/configs
      - ./skills:/app/skills
      - /var/www/qb3_upload/agent_storage:/app/storage
    networks: [aid-network]
    healthcheck:
      test: ["CMD-SHELL", "test -n \"$$(find /app/storage/.bg_runner_alive -mmin -2 2>/dev/null)\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 90s
    deploy:
      resources:
        limits: { cpus: '1', memory: 1G }
        reservations: { cpus: '0.25', memory: 256M }
    logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }
```

### 3.5 reconcile 对账（D11，核心）

`_register_system_jobs()` 注册 30s interval 任务跑 `_reconcile()`：
```python
def _reconcile(self):
    try:
        rows = ScheduledTaskDB.list_all_for_reconcile()   # active+paused，含 manual_trigger_at/updated_at
        db_ids = set()
        for t in rows:
            tid = t["task_id"]; db_ids.add(tid)
            sig = (t["schedule_type"], t.get("cron_expression"),
                   t.get("interval_seconds"), t["status"], str(t["updated_at"]))
            if t["status"] == "active":
                if self._reconcile_seen.get(tid) != sig:
                    self._register_job(t)                  # replace_existing=True 幂等
                    self._reconcile_seen[tid] = sig
            else:
                if tid in self._jobs: self.remove_task(tid)
                self._reconcile_seen.pop(tid, None)
            if t.get("manual_trigger_at"):                 # 手动触发
                self._fire_once(tid, trigger_type="manual")
                ScheduledTaskDB.clear_manual_trigger(tid)
        for tid in list(self._jobs.keys()):
            if tid not in db_ids:
                self.remove_task(tid); self._reconcile_seen.pop(tid, None)
    except Exception as e:
        logger.error(f"reconcile 失败: {e}", exc_info=True)
```
`_reconcile_seen` 在 `__init__` 初始化；按 `updated_at` 判变化避免每 30s 重置 interval 计时。

### 3.6 手动触发列（D12）

schema（init-postgres.sql + db_update.sql）：
```sql
ALTER TABLE scheduled_tasks ADD COLUMN IF NOT EXISTS manual_trigger_at TIMESTAMP NULL;
```
db.py：`request_manual_trigger(task_id)`（`SET manual_trigger_at=NOW()`）/ `clear_manual_trigger(task_id)`（`SET NULL`）。
API `trigger` 端点(:212)改调 `request_manual_trigger`；reconcile ≤30s 执行。

### 3.7 API / 工具解耦（D11）

`src/api/scheduled_task.py` + `src/tools/scheduler/scheduled_task_tool.py`：create/update/pause/resume/cancel 去掉 `manager.*` 调用只留 DB 写；trigger 走 `request_manual_trigger`。manager 仅保留 `remove_task` + `_register_job`（reconcile 内部用）；`register/pause/resume/trigger_task` 经 grep 确认无调用方，已删（避免死代码）。

### 3.8 4 循环迁移（D13/D14/D15）

在 `_register_system_jobs()` 注册 3 个 APScheduler 任务（poller 单独，见 3.3）：
```python
# memory_cleanup（同步，interval）
self._scheduler.add_job(self._run_memory_cleanup,
    IntervalTrigger(seconds=settings.memory.cleanup_interval),
    id="job_system_memory_cleanup", max_instances=1, coalesce=True)
# dedup_cleanup（同步，cron 03:00）
self._scheduler.add_job(self._run_dedup_cleanup,
    CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
    id="job_system_dedup_cleanup", max_instances=1, coalesce=True)
# wecom_kf_timeout（async，interval 60s，套 event loop）—— 仅 saas.enabled
if settings.saas.enabled:
    self._scheduler.add_job(self._run_wecom_kf_timeout,
        IntervalTrigger(seconds=60),
        id="job_system_wecom_kf_timeout", max_instances=1, coalesce=True)
```
回调：
- `_run_memory_cleanup`：`from src.core.agent import master_agent`（懒）→ `master_agent.memory.cleanup_expired()`（同步，直接调）。
- `_run_dedup_cleanup`：`MessageDeduplicator(ttl_seconds=300).cleanup_expired()`，**保留 `psycopg2.OperationalError` 降级**。
- `_run_wecom_kf_timeout`：抽取 `_wecom_kf_timeout_check_loop` 循环体为 `async def _wecom_kf_tick()`，回调内新建 event loop `run_until_complete(_wecom_kf_tick())`（同 `_run_memory_summarizer` 模式）。需 `channel_session_manager` + `ChannelFactory`（background 已 init channel 表，验证 ChannelFactory 无 HTTP 依赖）。
- poller：见 3.3，不改其内部，background asyncio loop 启动。

> **`instance_lock_cleanup` 删除范围**（原 #38 §2.6 已确认）：`main.py` 的 `_instance_lock_cleanup_loop` 函数定义及 `asyncio.create_task` 调用已删除（现仅剩 514-515 孤儿注释，本计划 §3.2 已清理）；`src/saas/services/instance_service.py:603` 的 `cleanup_all_expired_locks` 静态方法全项目仅原 `main.py:426` 一处调用，删除 loop 后该方法无引用，可一并清理（P2 阶段处理，避免遗留死代码）。删除前需再次确认 `instance_service.py` 中其它与实例锁相关的代码是否也属于废弃范围（如 `auto_release_expired_lock` 等），若整体功能废弃应一并清理。

### 3.9 S1 接入钩子（D10）

`_register_system_jobs()` 末尾：
```python
try:
    from src.social_media.publishing.dispatcher import PublishJobDispatcher
    self._scheduler.add_job(lambda: self._run_publish_dispatch(),
        IntervalTrigger(seconds=60), id="job_system_publish_dispatch",
        name="Social Publish Dispatcher", max_instances=1, coalesce=True)
except ImportError:
    logger.debug("PublishJobDispatcher 尚未实现，跳过（S1 未落地）")
except Exception as e:
    logger.error(f"注册发布调度任务失败: {e}")
```

---

## 4. 任务序列（单阶段，按序执行）

| # | 任务 | 验收（必须可证） |
|---|------|----------------|
| 1 | settings/config | 无需改动（server_mode 已移除，见 D2）— 跳过 | — |
| 2 | schema 加 `manual_trigger_at` + db.py 三方法 | 容器内 init 后列存在；三方法单测 |
| 3 | `manager._reconcile` + 注册 + `_fire_once` | 单测（mock DB）：新增/暂停/删除/触发/幂等全分支 |
| 4 | API + 工具解耦 | 回归：CRUD 只写 DB；trigger 写 `manual_trigger_at`；无 manager 跨进程调用 |
| 5 | 3 循环迁移进 manager（memory/dedup/wecom_kf） | 单测：回调按间隔触发；dedup 保留 psycopg2 降级；wecom_kf 仅 saas.enabled 注册 |
| 6 | `background_runner.py`（asyncio + poller + 心跳 + 信号） | 单测（mock）：start scheduler + poller；SIGTERM→poller.stop + scheduler.shutdown + 心跳停。**import 安全**：`import src.background_runner` 不拉起 master_agent |
| 7 | main.py 删 start() + 删 4 循环 + 清注释 | main.py import 链路无破坏；启动日志无调度器/循环；HTTP 正常 |
| 8 | compose `aid-agent-background`（5 文件） | 容器起来：日志就绪 + 心跳刷新 + Redis 锁持有 + `up --wait` 不卡；**端到端**：UI 建任务→≤30s 注册→到点执行；trigger→≤30s 执行；4 循环只在 background 跑一次（查日志确认无多 worker 重复） |
| 9 | S1 钩子 | 未实现时 ImportError 跳过不报错 |

---

## 5. 测试策略

- **单元**：settings；db.py 三方法；`_reconcile` 全分支；3 循环回调（mock master_agent/DB）；`background_runner._run`（mock scheduler+poller）。
- **import 安全**（关键）：`python -c "import src.background_runner"` 后 `sys.modules` 不含 `src.core.agent`。
- **集成**（容器内）：起 runner，断言 Redis 锁 + 心跳 + scheduler `_running` + poller 任务在跑；建任务后 ≤30s reconcile 注册；4 循环各触发一次且全仓仅 background 一份日志。
- **回归**：API CRUD/trigger 端到端；HTTP worker 不再有任何后台循环/调度器。
- 命令统一走 `./scripts/dev_test.sh`。

---

## 6. 风险与上线

- **高风险「提交即上线」**：改 main.py/settings/manager/api/schema/compose + 迁循环，触及启动链路与任务执行核心。**必须走完整三智能体流程**，主控者亲自 import/build 终检。
- **wecom_kf 迁移**（中风险）：需验证 `ChannelFactory` 在 background 容器（无 HTTP 请求上下文）可用；适配器调用（end_human_service/send_text/get_service_state）均为出站 API，理论可独立运行，需实测。
- **poller 迁移**（高风险）：poller 的 `_process_inbound_message` 可能触发 agent 处理（需 master_agent）+ 写 channel_messages；asyncio loop + 信号停机需实测不泄漏任务、不丢消息。
- **background 容器变重**：迁入 4 循环后需 master_agent（cron/memory_cleanup/poller inbound）+ channel 基建，接近 HTTP worker 体量（仍省 HTTP 服务）。1G limit 需监控，不够则上调。
- **灰度**：先上 background 容器跑全部后台任务，HTTP worker 同步删循环/调度器；验证 UI CRUD/触发/到点执行/4 循环全在 background 正常 → 全量。
- **回滚**：停 background 容器 + git revert（恢复 main.py 的 start()+4 循环 + API manager 调用）。`manual_trigger_at` 列留着无害。
- **`agent_update.sh --wait`**：healthcheck 必须可靠转健康，start_period 90s。
- **dev/test/local**：必须都加 background 服务，否则无定时任务/4 循环。

---

## 7. 后台任务全量清单与影响

### 7.1 清单（4 类）

| 类 | 任务 | 依赖 master_agent |
|---|---|---|
| A. APScheduler 系统任务 | ① 每日记忆总结 ② 上下文压缩扫描 | ❌ standalone |
| B. APScheduler 用户任务 | ③ 用户前端定时任务 | ✅ 执行时 |
| C. 原 main.py 循环 | ④ memory_cleanup ⑤ dedup_cleanup ⑥ wecom_kf_timeout ⑦ rpa_archive_poller | ④⑦✅ ⑤⑥❌ |
| D. HTTP 请求级 | ⑧ session_queue watchdog | — 留 worker |

### 7.2 一次性迁移后的影响（无中间态）

| 任务 | 迁移后 | 风险 |
|---|---|---|
| ①② 记忆/压缩 | background APScheduler | ✅ 低（standalone） |
| ③ 用户定时任务 | background 执行 + reconcile | ⚠️ 中（CRUD/触发 ≤30s；master_agent 多实例 cron session 隔离不串） |
| ④ memory_cleanup | background APScheduler interval | 低-中（需 master_agent） |
| ⑤ dedup_cleanup | background APScheduler cron | ✅ 低（无 master_agent） |
| ⑥ wecom_kf_timeout | background APScheduler interval | ⚠️ 中（需 ChannelFactory 验证） |
| ⑦ rpa_archive_poller | background asyncio 兄弟任务 | ⚠️ 高（inbound 可能触发 agent + asyncio 停机） |
| ⑧ session watchdog | 不动 | ✅ 无 |

> 一次性完成后，**HTTP worker 不再跑任何后台任务**（纯 HTTP），所有后台任务在 background 单实例运行，#38 多 worker 重复执行问题彻底消除。

---

## 8. 明确不做（排除项）

- 不引入 arq / 新依赖。
- 不加 gating/回退开关（D1）。
- 不在 background 开 HTTP /healthz。
- 不改 Redis 锁 TTL/续期。
- 不动 session_queue watchdog（请求级，留 worker）。
- 不改 poller 内部实现（D13，仅改宿主进程）。
- S1 dispatcher 实现归 S1 计划，本计划只留注册钩子。
