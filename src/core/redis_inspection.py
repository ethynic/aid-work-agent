"""Redis 夜间巡检（生产本地容器实例专用）

每日 00:30 由 background_runner 调度器触发，检查生产 Redis（容器 mem_limit 1g）
的内存水位、持久化状态、连接数与无 TTL 键增长。结果以「[Redis巡检]」前缀写入
主日志：一条 INFO 汇总 + 每条越界项一条 WARNING/ERROR，grep 该前缀即可筛查。

测试环境 Redis 为腾讯云托管（云监控覆盖），通过 REDIS_INSPECTION_ENABLED 门控，
默认关闭、不注册任务。

巡检项与阈值（依据 2026-09-18 实测基线：used 1.6MB / dbsize 155 / clients 19）：
- 内存水位：>= 600MB WARNING（1g 的 60%）/ >= 800MB ERROR（80%，逼近 OOM kill）
- 碎片率：used > 100MB 且 ratio > 1.5（小数据集下该比值是分配器开销噪音，不判）
- AOF：last_write_status != ok ERROR；last_bgrewrite_status != ok WARNING
- 淘汰：evicted_keys > 0 WARNING（noeviction 策略下不应发生）
- 连接数：> 200 WARNING（基线 ~20，突增多为连接泄漏）
- 无 TTL 键：抽样最多 1000 键，无 TTL 超 200 个疑似泄漏（uploaded_file:* 文件
  注册表与 comp_metrics:* 固定键为设计内永久键，属正常）
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

MEM_WARN_MB = 600
MEM_CRIT_MB = 800
FRAG_RATIO_WARN = 1.5
FRAG_MIN_USED_MB = 100
CLIENTS_WARN = 200
NO_TTL_SCAN_MAX = 1000
NO_TTL_WARN = 200
NO_TTL_EXAMPLES = 5


def _scan_no_ttl_keys(client) -> Tuple[int, List[str]]:
    """SCAN 抽样统计无 TTL 键（persist 键 ttl=-1），返回 (数量, 样例键名)。"""
    no_ttl = 0
    examples: List[str] = []
    scanned = 0
    cursor = 0
    while scanned < NO_TTL_SCAN_MAX:
        cursor, keys = client.scan(cursor, match="*", count=200)
        scanned += len(keys)
        for key in keys:
            if client.ttl(key) == -1:
                no_ttl += 1
                if len(examples) < NO_TTL_EXAMPLES:
                    examples.append(key)
        if cursor == 0:
            break
    return no_ttl, examples


def inspect_redis(client) -> Dict[str, Any]:
    """执行一轮巡检检查（纯逻辑，client 需提供 info/dbsize/scan/ttl 方法）。

    返回 {"alerts": [(level, msg), ...], "summary": str, "metrics": {...}}。
    """
    alerts: List[Tuple[str, str]] = []

    mem = client.info("memory") or {}
    used_mb = round((mem.get("used_memory") or 0) / 1024 / 1024, 1)
    frag = mem.get("mem_fragmentation_ratio") or 0
    if used_mb >= MEM_CRIT_MB:
        alerts.append(("ERROR", f"内存 {used_mb}MB >= {MEM_CRIT_MB}MB（容器上限 1g 的 80%），逼近 OOM kill"))
    elif used_mb >= MEM_WARN_MB:
        alerts.append(("WARNING", f"内存 {used_mb}MB >= {MEM_WARN_MB}MB（容器上限 1g 的 60%），排查键增长来源"))
    if used_mb > FRAG_MIN_USED_MB and frag > FRAG_RATIO_WARN:
        alerts.append(("WARNING", f"碎片率 {frag} > {FRAG_RATIO_WARN}（used={used_mb}MB），考虑低峰重启"))

    pers = client.info("persistence") or {}
    aof_enabled = bool(pers.get("aof_enabled"))
    aof_size_mb = round((pers.get("aof_current_size") or 0) / 1024 / 1024, 1)
    if aof_enabled:
        if pers.get("aof_last_write_status") != "ok":
            alerts.append(("ERROR", f"AOF 最后写入状态异常: {pers.get('aof_last_write_status')}，有丢数据风险"))
        if pers.get("aof_last_bgrewrite_status") != "ok":
            alerts.append(("WARNING", f"AOF 后台重写状态异常: {pers.get('aof_last_bgrewrite_status')}"))

    stats = client.info("stats") or {}
    evicted = stats.get("evicted_keys") or 0
    if evicted:
        alerts.append(("WARNING", f"发生键淘汰 evicted_keys={evicted}（noeviction 策略下不应发生，确认 maxmemory 配置）"))

    conn = (client.info("clients") or {}).get("connected_clients") or 0
    if conn > CLIENTS_WARN:
        alerts.append(("WARNING", f"连接数 {conn} > {CLIENTS_WARN}，排查客户端连接泄漏"))

    dbsize = client.dbsize()
    no_ttl, no_ttl_examples = _scan_no_ttl_keys(client)
    if no_ttl > NO_TTL_WARN:
        alerts.append((
            "WARNING",
            f"无 TTL 键疑似泄漏: 抽样上限 {NO_TTL_SCAN_MAX} 内命中 {no_ttl} 个，"
            f"样例: {', '.join(no_ttl_examples)}",
        ))

    summary = (
        f"used={used_mb}MB/1024MB frag={frag} clients={conn} dbsize={dbsize} "
        f"no_ttl={no_ttl} aof={'on' if aof_enabled else 'off'} aof_size={aof_size_mb}MB "
        f"evicted={evicted} alerts={len(alerts)}"
    )
    metrics = {
        "used_memory_mb": used_mb,
        "frag_ratio": frag,
        "connected_clients": conn,
        "dbsize": dbsize,
        "no_ttl_keys": no_ttl,
        "aof_enabled": aof_enabled,
        "aof_size_mb": aof_size_mb,
        "evicted_keys": evicted,
    }
    return {"alerts": alerts, "summary": summary, "metrics": metrics}


def run_redis_inspection() -> Optional[Dict[str, Any]]:
    """调度器入口：巡检本进程连接的 Redis 并记录日志（APScheduler 回调）。

    Redis 不可用（内存降级中）时跳过——降级本身已由 redis_client 打 warning，
    无需巡检重复报告。异常向上抛给调度器回调兜底记录。
    """
    from src.core.redis_client import redis_client

    if not redis_client.is_available():
        logger.warning("[Redis巡检] Redis 不可用（内存降级中），跳过本轮巡检")
        return None

    result = inspect_redis(redis_client)
    logger.info(f"[Redis巡检] {result['summary']}")
    for level, msg in result["alerts"]:
        log = logger.error if level == "ERROR" else logger.warning
        log(f"[Redis巡检] {level}: {msg}")
    return result
