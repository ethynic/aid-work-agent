# 发布调度执行框架设计（Publish Dispatcher & Executor）

> 日期：2026-07-21
> 阶段：共享底座 S1（内容/广告共用）
> 上级设计：[social-media-marketing-agent-design.md](social-media-marketing-agent-design.md) §5.3 发布状态机
> 开发计划：[social-media-marketing-agent-dev-plan.md](social-media-marketing-agent-dev-plan.md) §4 S1

## 1. 目标与边界

把 `social_publish_jobs` 从「创建后停在 QUEUED 不可达」（现状）推进到「调度到期 → 原子领取 → 调连接器发布 → 重试/回查 → 终态」全链路自动执行。

**本期交付（S1）**：
- `PublishJobDispatcher`：每分钟扫描到期任务 + 原子领取。
- `PublishExecutor`：调连接器 `publish`/`query_publish_status`，重试分类、退避抖动、状态回查。
- 状态机 `can_transition` 在执行链路强制校验。
- 租约过期恢复：优先查平台状态，**禁止盲重发**，`status_unknown` 不自动标成功。
- 写 `social_publish_attempts`。
- 多 Worker 防重 + 崩溃恢复。

**本期不做（留给后续）**：公众号/视频号连接器真实 HTTP（C1/C2，届时补回 `API_PUBLISH` 能力声明）；广告动作执行器（A5，复用本框架的状态机/领取/护栏思路但独立实现）；运营数据同步（C3）。

**核心约束**：S1 框架对 stub 连接器必须**安全失败**——`wechat_official` 现仅声明 `ACCOUNT_CREDENTIALS`，Executor 调用前 `CapabilityResolver.require(account, API_PUBLISH)` 抛 `CapabilityNotSupported` → 任务转 `FAILED`（error_category=permission），不卡死、不盲试。

## 2. 数据模型现状（零改动）

`social_publish_jobs` 已内置 S1 全部字段，**本期不建表不改表**：

| 字段 | S1 用途 |
|------|---------|
| `status` | 状态机流转 |
| `scheduled_at` / `timezone` | 到期判定（与 `timezone` 无关，DB 用本地 TIMESTAMP，按 `scheduled_at <= NOW()` 判到期） |
| `lease_owner` / `lease_expires_at` | 原子领取租约（+ 索引 `idx_social_publish_jobs_lease`） |
| `external_task_id` | 平台异步任务 ID，回查用（+ 索引） |
| `retry_count` / `max_retries`(默认3) / `next_retry_at` | 重试计数与退避 |
| `last_error_code` / `last_error_message` | 最近错误分类 |
| `idempotency_key UNIQUE` | 防重复创建 |
| `publish_snapshot_json` | 冻结快照，执行时不重读最新版本 |

`social_publish_attempts` 记录每次尝试：`attempt_no` / `trigger_type`(scheduled/retry/recovery) / `request|response_summary_json` / `error_category` / `platform_error_code` / `duration_ms`。

## 3. 架构

```
scheduled_task_manager（BackgroundScheduler，Redis 锁保证单实例）
  └─ 系统任务：PublishJobDispatcher.run()，每 60s 触发（后台线程 + 新 event loop）
       │
       ├─ step1 promote：SCHEDULED(due) → QUEUED        # 批量 UPDATE，到期定时任务入队
       ├─ step2 recover：过期租约恢复                    # lease_expires_at<NOW() AND status=publishing
       │     └─ 查平台 query_publish_status → published/failed/status_unknown（不盲重发）
       └─ step3 claim+execute：领取 QUEUED/RETRY_WAIT(due) → PUBLISHING → PublishExecutor
             ├─ DB 原子领取：条件 UPDATE 设 lease_owner/lease_expires_at，RETURNING job
             ├─ can_transition(queued|retry_wait → publishing) 强制校验
             └─ PublishExecutor.execute(job)
                  ├─ CapabilityResolver.require(account, API_PUBLISH)  # S0 门禁接入点
                  ├─ connector.publish(snapshot, idempotency_key)
                  ├─ connector.map_error(error) → 分类
                  │     transient        → RETRY_WAIT + 退避抖动（next_retry_at）
                  │     auth             → FAILED（凭证问题，转人工，不重试）
                  │     validation       → FAILED（内容规格，不重试）
                  │     permission       → FAILED（能力/权限，转人工）
                  │     duplicate_risk   → 走幂等回查（query_publish_status）定终态，不盲重发
                  │     permanent        → FAILED
                  ├─ 写 social_publish_attempts
                  └─ 成功 → submitted → polling → published（异步任务回查）
```

**并发**：单 tick 内领取的任务用 `asyncio.gather` 并发执行，上限可配（默认 5），避免少量任务串行拖慢；平台限流由连接器 `map_error` 返回 transient 触发退避，不在框架层硬限速。

## 4. 状态机强制校验接入点

`publishing/state_machine.py` 的 `ALLOWED_TRANSITIONS` + `can_transition()` 现已实现但未被调用。S1 在以下每次状态变更前强制校验，非法转换抛 `InvalidStateTransition`（新增异常）并记 `last_error`，防止 DB 被外部直接改坏后执行器盲目推进：

| 变更点 | 断言 |
|--------|------|
| promote SCHEDULED→QUEUED | `can_transition(scheduled, queued)` |
| claim →PUBLISHING | `can_transition(queued\|retry_wait, publishing)` |
| 发布受理 →SUBMITTED | `can_transition(publishing, submitted)` |
| 回查中 →POLLING | `can_transition(submitted, polling)` |
| 成功 →PUBLISHED | `can_transition(submitted\|polling, published)` |
| 重试 →RETRY_WAIT | `can_transition(publishing, retry_wait)` |
| 失败 →FAILED/STATUS_UNKNOWN | `can_transition(publishing\|submitted\|polling, failed\|status_unknown)` |

## 5. 原子领取 SQL（多 Worker 防重核心）

领取用单条条件 UPDATE，PostgreSQL 行级锁保证并发只有一个 worker 抢到：

```sql
UPDATE social_publish_jobs
SET status = 'publishing',
    lease_owner = %s,
    lease_expires_at = NOW() + INTERVAL '%s seconds',
    updated_at = NOW()
WHERE job_id = %s
  AND status IN ('queued', 'retry_wait')
  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
  AND (lease_expires_at IS NULL OR lease_expires_at < NOW())
RETURNING *;
```

`cur.rowcount == 1` 才执行；否则视为被别处抢走/状态已变，跳过。

**双层防重**：
1. 调度器层：`scheduled_task_manager` 已用 Redis 分布式锁保证 Dispatcher 单实例运行（`src/scheduler/manager.py` start()）。
2. 任务层：上述条件 UPDATE 原子领取，即使将来 Dispatcher 多实例也不重复发布。

## 6. 租约过期恢复（崩溃恢复）

进程崩溃会留下 `status=publishing AND lease_expires_at<NOW()` 的悬空任务。恢复策略：

```sql
SELECT * FROM social_publish_jobs
WHERE status = 'publishing' AND lease_expires_at < NOW()
LIMIT %s;
```

对每个悬空任务：
1. 优先 `connector.query_publish_status(external_task_id)`：
   - 已发布 → `PUBLISHED`；
   - 明确失败 → `FAILED`；
   - 仍在处理 / 查不到 → `STATUS_UNKNOWN`（**不自动标成功**，留人工）。
2. 仅当 `external_task_id` 为空（根本没提交成功）且 `retry_count < max_retries` → 退回 `RETRY_WAIT` 重发；否则保持 `STATUS_UNKNOWN`。

> **红线**：`duplicate_risk` 错误与租约恢复都遵循「先查平台定终态，禁止盲重发」，避免重复发布。

## 7. 重试与退避

- 退避：`next_retry_at = NOW() + base * 2^min(retry_count, cap) + jitter`（base=60s，cap=5，jitter=±20%）。
- `max_retries` 默认 3（表默认值），达上限 → `FAILED`。
- 只对 `transient`（含限流/超时）重试；`auth/validation/permission/permanent` 直接 `FAILED`；`duplicate_risk` 走幂等回查。
- 每次尝试写一条 `social_publish_attempts`（attempt_no 递增）。

## 8. 启动接入（独立后台运行时，不占用 HTTP worker）

**Dispatcher 不注册进 main.py lifespan**，而是注册进**独立后台运行时**（[background-runner-design.md](../../infrastructure/background-runner-design.md)）的 APScheduler——后台定时任务与 HTTP worker 解耦，不占用 worker。

- `src/background_runner.py`（后台运行时入口，mode `SERVER_MODE=background`）启动 `scheduled_task_manager`，在其 `_register_system_jobs()` 注册 Dispatcher 为 interval 系统任务。
- HTTP worker（`main.py`）按 mode gating **不再启动调度器**（详见后台运行时设计 §7）。
- shutdown 时随 `scheduled_task_manager.shutdown()` 一并停止。

Dispatcher 注册复用 `ScheduledTaskManager._register_system_jobs()` 的 interval 模式（参考现有 `job_system_compression_scan`）：`IntervalTrigger(seconds=60)`，`max_instances=1`，`coalesce=True`。回调在后台线程新建 event loop 跑 `PublishJobDispatcher.run()`（与现有 `_execute_task` 同款）。

> **依赖前置**：S1 的 P0.1–P0.3（后台运行时入口 + mode gating + compose 服务）必须先就绪，Dispatcher 才有地方跑。

## 9. 文件清单

| 文件 | 动作 | 内容 |
|------|------|------|
| `src/social_media/publishing/dispatcher.py` | 新 | `PublishJobDispatcher`：promote/recover/claim 三步，APScheduler 回调入口 |
| `src/social_media/publishing/executor.py` | 新 | `PublishExecutor`：连接器调用、重试分类、状态机校验、写 attempts |
| `src/social_media/publishing/state_machine.py` | 改 | 加 `InvalidStateTransition` 异常 + `assert_transition()` 强制校验封装 |
| `src/social_media/services.py` | 改 | 加领取/续租/attempts 写入的 DB helper（或单独 `publishing/repo.py`） |
| `src/background_runner.py` | 改（随后台运行时 P0） | 注册 Dispatcher 为 APScheduler 系统任务（**非 main.py**） |
| `configs/config.yaml` + `settings.py` | 改 | Dispatcher 配置（scan_interval/batch_size/lease_ttl/concurrency），字段名同步 |

## 10. 日志与可观测

- 用 `tlog("发布调度", ...)` 隔离主题日志（`log/temp/发布调度.log`），记录每 tick 扫描数/领取数/执行结果/恢复数。
- 关键节点 `logger.info`：tick 开始、领取成功、终态、恢复决策。
- 失败/STATUS_UNKNOWN `logger.warning` 带 job_id + error_category。

## 11. 测试策略

**单元（mock DB + 连接器）**：
- `assert_transition` 非法转换抛 `InvalidStateTransition`。
- 领取 SQL 的 rowcount 语义（mock：rowcount=0 跳过、=1 执行）。
- 重试分类：6 类 error_category 各自的正确流转（transient→RETRY_WAIT；permission→FAILED；duplicate_risk→回查）。
- 退避公式与抖动区间。
- CapabilityResolver 门禁对 stub 账号 → FAILED(permission)。
- 租约恢复：external_task_id 有/无两条分支；STATUS_UNKNOWN 不标成功。

**集成（Mock 连接器）**：
- 全链路：QUEUED → publish 返回 submitted → polling → published。
- 多 Worker 防重：并发两次领取同 job，仅一次执行（mock DB 条件 UPDATE）。
- 崩溃恢复：构造悬空 publishing 任务，恢复后正确终态。

> 真实公众号异步发布/轮询验收留 C1（真实 HTTP 连接器就绪后）。

## 12. 退出条件

- 崩溃恢复、多 Worker 防重、状态机校验单元测试通过。
- Mock 连接器全链路 + 防重 + 恢复集成测试通过。
- Dispatcher 在单实例调度器内稳定每分钟跑，不漏扫不重发。
- 启动安全：`main.py` import 链路无循环、无副作用；Dispatcher 失败不影响主进程。
