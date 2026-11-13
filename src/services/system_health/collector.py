"""系统健康监控采集器（管理后台 /portal/system-health 数据源）。

设计文档：docs/system/system-health-monitor-design.md

- 由 APScheduler 在 SERVER_MODE=background 容器内每 15s 执行一轮（src/scheduler/manager.py
  注册 job_system_health_collect），主 API HTTP worker 不启动调度器，天然不运行本采集器
- 每轮写 Redis 快照（TTL 90s）+ 心跳键；不写任何历史序列（无曲线，2026-11-10 用户裁决）
- 所有状态走 Redis 不落进程内存（Gunicorn 多 worker 隔离规范）
- **worker 活性只认 DB 行状态推进**（owned_attempts 集合/slot 状态迁移/排队深度推进）；
  DB lease 续期与心跳文件是两个被设计明文排除的反例信号（slot 隔离后 supervisor 代续
  租约、worker 卡死时 lease 恒新鲜；slot 容器无心跳文件）——不得改为 lease/mtime 判活
- 告警判定与飞书推送归 agent-runner-monitor 容器独占，本采集器只读 active 告警展示
"""

import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client

# 采集/快照参数
COLLECT_INTERVAL_SECONDS = 15
SNAPSHOT_TTL_SECONDS = 90
HEARTBEAT_TTL_SECONDS = 90

# 调度推进停摆判定：连续 N 轮 active 指纹无变化且仍有排队任务（N×15s ≈ 5 分钟）
STALL_ROUND_THRESHOLD = 20

# 与 RunnerAlertRepository.candidates 同口径的容量上限（slot 拓扑约束，超出即异常）
RUNNER_SCAN_LIMIT = 1024

# 今日重试占比的最小样本数：任务基数小时占比被极少数重试放大（如 3/18=16.7%），
# 不足以代表调度稳定性，置 None 跳过阈值离散（缺失指标不进研判哈希）
RETRY_SHARE_MIN_SAMPLES = 10

ACTIVE_STATUSES = ("queued", "running", "waiting", "paused", "interrupted", "finalizing")
FINGERPRINT_STATUSES = ("queued", "running", "waiting", "paused", "interrupted", "finalizing")


def snapshot_key() -> str:
    return redis_client.make_key(CacheKeys.MONITOR_SNAPSHOT)


def heartbeat_key() -> str:
    return redis_client.make_key(CacheKeys.MONITOR_HEARTBEAT)


def assessment_key() -> str:
    return redis_client.make_key(CacheKeys.MONITOR_ASSESSMENT)


# ============== 纯函数（单测覆盖） ==============

def compute_stall(prev: Optional[Dict[str, Any]], fingerprint: List[str]) -> Dict[str, Any]:
    """调度推进活性：对比上一轮指纹，维护连续停摆轮数。

    指纹 = active 行的 (runner_id, attempt, status) 有序集合——worker 卡死时
    行状态不再迁移、排队深度不再推进，指纹恒定；正常执行中 attempt 迁移/
    终态流转会让指纹变化。指纹稳定且仍有排队任务时累积 stalled_rounds。
    """
    fp = sorted(fingerprint)
    active_count = len(fp)
    prev_fp = (prev or {}).get("fp")
    if fp and prev_fp == fp:
        stalled_rounds = int((prev or {}).get("stalled_rounds", 0)) + 1
    else:
        stalled_rounds = 0
    prev_counts = (prev or {}).get("active_counts") or {}
    return {
        "fp": fp,
        "stalled_rounds": stalled_rounds,
        "active_count": active_count,
        "prev_active_counts": prev_counts,
    }


def is_runner_stalled(stall: Dict[str, Any], queued_count: int) -> bool:
    """仍有排队任务且指纹连续多轮无推进 -> 调度停摆。

    只在 queued>0 时判定：无排队时「running 长时间不迁移」由规则层 30 分钟
    长任务告警（agent-runner-monitor）兜底，DB 指纹无法区分慢任务与卡死。
    """
    return queued_count > 0 and int(stall.get("stalled_rounds", 0)) >= STALL_ROUND_THRESHOLD


def retried_share_of(started: int, retried: int) -> Optional[float]:
    """今日重试占比；样本不足 RETRY_SHARE_MIN_SAMPLES 时置 None（不参与研判）。"""
    if started < RETRY_SHARE_MIN_SAMPLES:
        return None
    return round(retried / started, 4)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    idx = (len(data) - 1) * pct
    low = int(idx)
    high = min(low + 1, len(data) - 1)
    frac = idx - low
    return data[low] * (1 - frac) + data[high] * frac


# ============== 采集实现 ==============

def _collect_runner_section() -> "tuple[Dict[str, Any], List[str]]":
    """Runner 执行面：全部来自 agent_runners / agent_runner_alerts 的 DB 轻查询。

    返回 (指标 dict, 调度推进活性指纹列表)。"""
    from src.db.database import get_db_connection

    section: Dict[str, Any] = {}
    with get_db_connection() as cur:
        cur.execute(
            "SELECT status, COUNT(*) AS cnt FROM agent_runners "
            "WHERE status = ANY(%s) GROUP BY status",
            (list(ACTIVE_STATUSES),),
        )
        counts = {r["status"]: r["cnt"] for r in cur.fetchall()}
        # 2026-11-13 重工具服务化 §9：池路由退役，全部 worker 等价，不再按
        # load_class 拆分池占用（DB 列保留恒 light，仅历史行可读）。
        section["queued"] = counts.get("queued", 0)
        section["running"] = counts.get("running", 0)
        section["finalizing"] = counts.get("finalizing", 0)
        section["waiting"] = counts.get("waiting", 0)
        section["paused"] = counts.get("paused", 0)
        section["interrupted"] = counts.get("interrupted", 0)

        cur.execute(
            "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (clock_timestamp()-execution_started_at))"
            "::bigint), 0) AS s FROM agent_runners "
            "WHERE status IN ('running','finalizing') AND execution_started_at IS NOT NULL"
        )
        section["oldest_running_seconds"] = int(cur.fetchone()["s"] or 0)

        cur.execute(
            "SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (clock_timestamp()-accepted_at))::bigint), 0) AS s "
            "FROM agent_runners WHERE status = 'queued'"
        )
        section["oldest_queued_seconds"] = int(cur.fetchone()["s"] or 0)

        # 今日排队等待分位（accepted -> 首次 claim）与 attempt 重试占比。
        # attempt 语义：acquire_claim 每次 claim 自增（首 claim 0->1），故 attempt>=2 才是重试；
        # 重试行的 execution_started_at 是最后一次 claim 时间（含重试间隔），不计入排队等待分位。
        cur.execute(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY "
            "  EXTRACT(EPOCH FROM (execution_started_at - accepted_at))) FILTER (WHERE attempt <= 1) AS p50, "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY "
            "  EXTRACT(EPOCH FROM (execution_started_at - accepted_at))) FILTER (WHERE attempt <= 1) AS p95, "
            "COUNT(*) AS started, COUNT(*) FILTER (WHERE attempt > 1) AS retried "
            "FROM agent_runners "
            "WHERE execution_started_at IS NOT NULL "
            "  AND accepted_at >= date_trunc('day', clock_timestamp())"
        )
        row = cur.fetchone()
        started = int(row["started"] or 0)
        section["wait_p50_seconds"] = round(float(row["p50"]), 1) if row["p50"] is not None else None
        section["wait_p95_seconds"] = round(float(row["p95"]), 1) if row["p95"] is not None else None
        section["runner_started_today"] = started
        section["runner_retried_today"] = int(row["retried"] or 0)
        section["retried_share_today"] = retried_share_of(started, section["runner_retried_today"])

        # 调度推进活性指纹（active 行全量；slot 拓扑约束行数，超限视为异常值）
        cur.execute(
            "SELECT runner_id, attempt, status FROM agent_runners "
            "WHERE status = ANY(%s) ORDER BY runner_id LIMIT %s",
            (list(FINGERPRINT_STATUSES), RUNNER_SCAN_LIMIT + 1),
        )
        rows = cur.fetchall()
        if len(rows) > RUNNER_SCAN_LIMIT:
            raise RuntimeError("HEALTH_MONITOR_SCAN_CAPACITY_EXCEEDED")
        fingerprint = [f"{r['runner_id']}:{r['attempt']}:{r['status']}" for r in rows]

        cur.execute("SELECT COUNT(*) AS cnt FROM agent_runner_alerts WHERE ended_at IS NULL")
        section["active_alerts"] = int(cur.fetchone()["cnt"])

    # 槽位 = 部署配置口径（与 worker 容器共用同一 .env）；
    # 配置值非容器实况（副本崩溃不扣减），仅作容量对照展示。
    # 2026-11-13 重工具服务化 §9：单一 runner-worker 服务，执行体容量
    # = worker 副本 × 每副本并发（AGENT_RUNNER_SLOT_CONCURRENCY）；
    # 重工具服务指标走 _probe_heavy_tools（配置 + best-effort 健康探测）。
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default

    section["worker_replicas"] = _env_int("AGENT_RUNNER_WORKER_SLOTS", 4)
    section["light_concurrency"] = _env_int("AGENT_RUNNER_SLOT_CONCURRENCY", 4)
    section["worker_slots"] = max(1, section["worker_replicas"] * section["light_concurrency"])
    section["heavy_tools"] = _probe_heavy_tools(_env_int)

    return section, fingerprint


def _probe_heavy_tools(_env_int) -> Dict[str, Any]:
    """重工具服务指标：部署配置口径 + best-effort 健康探测（失败不阻断采集）。

    remote 模式才发 HTTP 探测（local 模式无重容器，仅报告模式与配置）；
    探测返回 running/busy 等服务实况，供健康页容量对照与告警研判。"""
    mode = (os.environ.get("HEAVY_TOOLS_MODE") or "local").strip().lower()
    section: Dict[str, Any] = {
        "mode": mode,
        "replicas": _env_int("HEAVY_TOOLS_REPLICAS", 1),
        "concurrency": _env_int("HEAVY_TOOLS_CONCURRENCY", 1),
    }
    if mode != "remote":
        section["available"] = None
        return section
    try:
        import httpx

        from src.services.heavy_tool_service.client import heavy_tools_url
        response = httpx.get(
            f"{heavy_tools_url().rstrip('/')}/internal/heavy-tools/health", timeout=2.0
        )
        if response.status_code == 200:
            body = response.json()
            section["available"] = True
            section["running_jobs"] = body.get("running_jobs")
            section["busy"] = body.get("busy")
        else:
            section["available"] = False
    except Exception:
        section["available"] = False
    return section


def _collect_system_section(prev_snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """系统资源：psutil 在主 API 容器内读 /proc（容器 /proc 反映宿主机级）。"""
    try:
        import psutil
    except ImportError:
        return {"available": False}

    section: Dict[str, Any] = {"available": True}
    section["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
    section["cpu_count"] = os.cpu_count() or psutil.cpu_count() or 1
    try:
        section["load_1"] = round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        section["load_1"] = None

    mem = psutil.virtual_memory()
    section["mem_used_percent"] = round(mem.percent, 1)
    section["mem_used_gb"] = round((mem.total - mem.available) / (1024 ** 3), 2)
    section["mem_total_gb"] = round(mem.total / (1024 ** 3), 2)

    # 磁盘 IO 速率：跨轮差分（上一轮计数器存于快照，随快照 TTL 一起过期则下轮重算基线）
    try:
        io = psutil.disk_io_counters()
        if io is not None:
            prev_io = (prev_snapshot or {}).get("_io_prev")
            now = time.time()
            if prev_io and now > prev_io.get("ts", 0):
                dt = now - prev_io["ts"]
                section["disk_read_mb_per_s"] = round(
                    max(0, io.read_bytes - prev_io["read_bytes"]) / dt / (1024 ** 2), 2)
                section["disk_write_mb_per_s"] = round(
                    max(0, io.write_bytes - prev_io["write_bytes"]) / dt / (1024 ** 2), 2)
            section["_io_prev"] = {
                "ts": now,
                "read_bytes": io.read_bytes,
                "write_bytes": io.write_bytes,
            }
    except Exception as e:  # noqa: BLE001 无磁盘计数器（部分容器环境）时跳过
        logger.debug(f"后端日志：健康监控磁盘 IO 采集跳过: {e}")

    # 网络 IO 速率：跨轮差分（口径同磁盘 IO）；排除回环与本机虚拟网卡，避免内部流量污染
    try:
        recv = sent = 0
        for name, nio in (psutil.net_io_counters(pernic=True) or {}).items():
            lowered = name.lower()
            if lowered == "lo" or lowered.startswith(("docker", "veth", "br-", "tun", "tap", "virbr")):
                continue
            recv += nio.bytes_recv
            sent += nio.bytes_sent
        prev_net = (prev_snapshot or {}).get("_net_prev")
        now = time.time()
        if prev_net and now > prev_net.get("ts", 0):
            dt = now - prev_net["ts"]
            section["net_recv_mb_per_s"] = round(
                max(0, recv - prev_net["recv_bytes"]) / dt / (1024 ** 2), 2)
            section["net_sent_mb_per_s"] = round(
                max(0, sent - prev_net["sent_bytes"]) / dt / (1024 ** 2), 2)
        section["_net_prev"] = {"ts": now, "recv_bytes": recv, "sent_bytes": sent}
    except Exception as e:  # noqa: BLE001 无网络计数器时跳过
        logger.debug(f"后端日志：健康监控网络 IO 采集跳过: {e}")

    # 公网带宽基准（Mbps，PUBLIC_BANDWIDTH_MBPS 环境变量）：仅用于带宽占满研判，
    # 未配置时网络 IO 只展示不参与阈值离散
    bw_raw = os.environ.get("PUBLIC_BANDWIDTH_MBPS", "").strip()
    try:
        section["net_bandwidth_mbps"] = float(bw_raw) if bw_raw else None
    except ValueError:
        section["net_bandwidth_mbps"] = None

    # storage 与日志分区使用率（可能同分区，分别取值便于独立告警）
    for name, path in (("storage", "storage"), ("log", "log")):
        try:
            usage = shutil.disk_usage(path)
            section[f"disk_used_percent_{name}"] = round(usage.used / usage.total * 100, 1)
        except OSError:
            section[f"disk_used_percent_{name}"] = None
    return section


def _collect_business_section() -> Dict[str, Any]:
    """业务面：在线用户/今日请求量/今日对话失败率/错误日志增量。"""
    from src.db.database import get_db_connection

    section: Dict[str, Any] = {}
    with get_db_connection() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM chat_sessions "
            "WHERE updated_at >= clock_timestamp() - INTERVAL '5 minutes'"
        )
        section["online_sessions_5m"] = int(cur.fetchone()["cnt"])

        cur.execute(
            "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status = 'failed') AS failed, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) FILTER (WHERE duration_ms > 0) AS p50, "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FILTER (WHERE duration_ms > 0) AS p95 "
            "FROM chat_records WHERE created_at >= date_trunc('day', clock_timestamp())"
        )
        row = cur.fetchone()
        total = int(row["total"] or 0)
        failed = int(row["failed"] or 0)
        section["requests_today"] = total
        section["error_rate_today"] = round(failed / total, 4) if total else None
        section["duration_p50_ms"] = int(row["p50"]) if row["p50"] is not None else None
        section["duration_p95_ms"] = int(row["p95"]) if row["p95"] is not None else None

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM log_error "
            "WHERE timestamp >= clock_timestamp() - INTERVAL '1 hour'"
        )
        section["errors_last_hour"] = int(cur.fetchone()["cnt"])
    return section


def _collect_middleware_section() -> Dict[str, Any]:
    """中间件：DB 连接水位与 SELECT 1 延迟、Redis 可用性、KeyPool 并发水位。"""
    from src.db.database import get_db_connection

    section: Dict[str, Any] = {}
    started = time.perf_counter()
    with get_db_connection() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
        section["db_select1_ms"] = round((time.perf_counter() - started) * 1000, 1)
        cur.execute(
            "SELECT (SELECT COUNT(*) FROM pg_stat_activity) AS conns, "
            "(SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_conns"
        )
        row = cur.fetchone()
        section["db_connections"] = int(row["conns"])
        section["db_max_connections"] = int(row["max_conns"]) if row["max_conns"] else None

    section["redis_available"] = redis_client.is_available()

    try:
        from src.llm.gateway import llm_gateway
        pool = getattr(llm_gateway, "_key_pool", None)
        stats = pool.stats() if pool else []
        section["key_pool_active"] = sum(s.get("active", 0) for s in stats)
        section["key_pool_max"] = sum(s.get("max_concurrent", 0) for s in stats)
    except Exception as e:  # noqa: BLE001 模型网关未初始化时跳过该指标
        logger.debug(f"后端日志：健康监控 KeyPool 采集跳过: {e}")
        section["key_pool_active"] = None
        section["key_pool_max"] = None
    return section


def collect_once(prev_snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """采集一轮全部指标，返回快照 dict（不写 Redis，写入由 run_collection_tick 负责）。"""
    snapshot: Dict[str, Any] = {"collected_at": datetime.now().isoformat(timespec="seconds")}

    try:
        runner_section, fingerprint = _collect_runner_section()
        stall = compute_stall(prev_snapshot, fingerprint)
        runner_section["stalled_rounds"] = stall["stalled_rounds"]
        runner_section["runner_stalled"] = is_runner_stalled(stall, runner_section.get("queued", 0))
        snapshot["runner"] = runner_section
        snapshot["fp"] = fingerprint
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康监控 Runner 指标采集失败: {e}")
        snapshot["runner"] = {"error": str(e)[:200]}

    for name, fn, prev_needed in (
        ("system", _collect_system_section, True),
        ("business", _collect_business_section, False),
        ("middleware", _collect_middleware_section, False),
    ):
        try:
            snapshot[name] = fn(prev_snapshot) if prev_needed else fn()
        except Exception as e:
            logger.opt(exception=True).error(f"后端日志：健康监控 {name} 指标采集失败: {e}")
            snapshot[name] = {"error": str(e)[:200]}

    return snapshot


def run_collection_tick() -> None:
    """APScheduler 回调：采集一轮并写 Redis 快照与心跳。

    Redis 降级期间不写（写入会落单 worker 内存，跨 worker 不一致且无法充当
    数据源，设计 §4.5）；跳过本轮即可，恢复后下一轮自动续上。
    """
    try:
        if not redis_client.is_available():
            logger.warning("后端日志：健康监控采集跳过本轮（Redis 不可用，不写内存降级快照）")
            return
        prev = redis_client.get(snapshot_key())
        snapshot = collect_once(prev if isinstance(prev, dict) else None)
        redis_client.set(snapshot_key(), snapshot, ex=SNAPSHOT_TTL_SECONDS)
        redis_client.set(
            heartbeat_key(),
            {"collected_at": snapshot["collected_at"]},
            ex=HEARTBEAT_TTL_SECONDS,
        )
        stalled = snapshot.get("runner", {}).get("runner_stalled")
        if stalled:
            logger.warning(
                f"后端日志：健康监控检测到调度推进停摆 "
                f"(连续 {snapshot.get('runner', {}).get('stalled_rounds')} 轮无状态迁移，"
                f"排队 {snapshot.get('runner', {}).get('queued')})"
            )
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康监控采集任务异常: {e}")
