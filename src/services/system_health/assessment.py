"""AI 健康研判（管理后台 /portal/system-health 顶部横幅）。

设计文档：docs/system/system-health-monitor-design.md §9

- 评估任务在采集器侧后台执行（APScheduler 60s 一轮），**不在 HTTP 请求路径**：
  页面轮询只读 Redis 缓存好的结论，绝不触发 LLM
- 去重口径是「阈值带离散化」：全部指标先按 THRESHOLDS 离散为 normal/watch/critical，
  硬故障（Redis 不可用、Runner 调度停摆）直接判 error，再拼哈希（剔除采集时间戳等
  纯时变字段），阈值带组合不变则复用上次结论。
  不能对原始数值做内容哈希——最老任务时长等字段每轮必变，原始哈希永远不相等
- 双层判定分工：规则层（runner_alerts/硬阈值）即时可信零成本，负责页面红黄高亮与
  飞书推送；LLM 研判层综合判读组合异常（CPU 高/低 × 队列不推进），**仅供人看，
  不触发任何自动动作**（不重启/不重试/不发通知）
- LLM 失败/超时/解析失败一律回落规则层判定文案，数据展示不受影响
"""

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.redis_client import redis_client
from src.services.system_health.collector import (
    assessment_key,
    snapshot_key,
)

ASSESS_INTERVAL_SECONDS = 60
ASSESSMENT_TTL_SECONDS = 600
ASSESS_MAX_TOKENS = 800

# 阈值表：与规则层/页面红黄高亮同源（设计 §9.2：阈值表来自配置，避免两套口径）。
# 观察项 -> (watch 阈值, critical 阈值)；值为 None 的指标跳过离散（从哈希中缺席，
# 不参与组合——指标缺失是稳定状态，不应让哈希抖动）。
THRESHOLDS: Dict[str, Dict[str, Any]] = {
    # Runner 执行面（30 分钟口径与 RunnerAlertRepository.DEFAULT_THRESHOLD_MINUTES 对齐）
    "runner.oldest_running_seconds": {"watch": 600, "critical": 1800},
    "runner.oldest_queued_seconds": {"watch": 120, "critical": 600},
    "runner.queued": {"watch": 2, "critical": 5},
    "runner.wait_p95_seconds": {"watch": 120, "critical": 600},
    "runner.retried_share_today": {"watch": 0.2, "critical": 0.5},
    "runner.runner_stalled": {"watch": None, "critical": True},
    "runner.active_alerts": {"watch": 1, "critical": 3},
    # 系统资源
    "system.cpu_percent": {"watch": 80, "critical": 95},
    "system.load_1": {"watch": "cpu_count", "critical": "cpu_count*2"},
    "system.mem_used_percent": {"watch": 80, "critical": 92},
    "system.disk_used_percent_storage": {"watch": 80, "critical": 92},
    "system.disk_used_percent_log": {"watch": 80, "critical": 92},
    # 网络带宽占满（方向取收/发较大者；仅配置 PUBLIC_BANDWIDTH_MBPS 后产出派生指标）
    "system.net_load_ratio": {"watch": 0.8, "critical": 0.95},
    # 中间件
    "middleware.db_conn_ratio": {"watch": 0.8, "critical": 0.95},
    "middleware.db_select1_ms": {"watch": 200, "critical": 1000},
    "middleware.key_pool_ratio": {"watch": 0.8, "critical": 1.0},
    "middleware.redis_available": {"watch": None, "critical": False},
    # 业务面 / 模型面
    "business.error_rate_today": {"watch": 0.05, "critical": 0.15},
    "business.errors_last_hour": {"watch": 20, "critical": 100},
    "business.duration_p95_ms": {"watch": 60000, "critical": 120000},
    "model_error_rate_today": {"watch": 0.05, "critical": 0.2},
}

_STATE_ORDER = {"normal": 0, "watch": 1, "critical": 2, "error": 3}
_GROUP_LABELS = {
    "runner": "Runner 执行面",
    "system": "系统资源",
    "middleware": "中间件",
    "business": "业务面",
}


# ============== 纯函数（单测覆盖） ==============

def _derived_metrics(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """把快照原始值转成阈值表可比较的派生指标（比率/复合条件）。"""
    derived: Dict[str, Any] = {}
    mw = snapshot.get("middleware") or {}
    conns, max_conns = mw.get("db_connections"), mw.get("db_max_connections")
    if conns is not None and max_conns:
        derived["middleware.db_conn_ratio"] = round(conns / max_conns, 4)
    active, pool_max = mw.get("key_pool_active"), mw.get("key_pool_max")
    if active is not None and pool_max:
        derived["middleware.key_pool_ratio"] = round(active / pool_max, 4)
    sys_ = snapshot.get("system") or {}
    if sys_.get("available") and sys_.get("cpu_count"):
        derived["system.cpu_count"] = sys_["cpu_count"]
    bandwidth = sys_.get("net_bandwidth_mbps")
    rates = [sys_.get("net_recv_mb_per_s"), sys_.get("net_sent_mb_per_s")]
    if bandwidth and all(r is not None for r in rates):
        derived["system.net_load_ratio"] = round(max(rates) * 8 / bandwidth, 4)
    biz = snapshot.get("business") or {}
    if biz.get("duration_p95_ms") is not None:
        derived["business.duration_p95_ms"] = biz["duration_p95_ms"]
    return derived


def _resolve_threshold(threshold: Any, derived: Dict[str, Any]) -> Optional[Any]:
    if isinstance(threshold, str) and threshold == "cpu_count":
        return derived.get("system.cpu_count")
    if isinstance(threshold, str) and threshold == "cpu_count*2":
        cpu = derived.get("system.cpu_count")
        return cpu * 2 if cpu else None
    return threshold


def _discretize_one(value: Any, threshold: Dict[str, Any], derived: Dict[str, Any]) -> Optional[str]:
    if value is None:
        return None
    watch = _resolve_threshold(threshold.get("watch"), derived)
    critical = _resolve_threshold(threshold.get("critical"), derived)
    if critical is not None and value >= critical:
        return "critical"
    if watch is not None and value >= watch:
        return "watch"
    return "normal"


def discretize(snapshot: Dict[str, Any]) -> Dict[str, str]:
    """把快照离散为指标级三态映射（哈希去重与规则层判定的同源输入）。

    返回 {指标名: normal|watch|critical}，值为 None/缺失的指标不进映射。
    """
    derived = _derived_metrics(snapshot)
    states: Dict[str, str] = {}

    runner = snapshot.get("runner") or {}
    if "error" not in runner:
        for key in ("oldest_running_seconds", "oldest_queued_seconds", "queued",
                    "wait_p95_seconds", "retried_share_today", "active_alerts"):
            if key in runner:
                st = _discretize_one(runner.get(key), THRESHOLDS[f"runner.{key}"], derived)
                if st:
                    states[f"runner.{key}"] = st
        if runner.get("runner_stalled"):
            states["runner.runner_stalled"] = "error"
        else:
            states["runner.runner_stalled"] = "normal"

    sys_ = snapshot.get("system") or {}
    if sys_.get("available") and "error" not in sys_:
        for key in ("cpu_percent", "mem_used_percent",
                    "disk_used_percent_storage", "disk_used_percent_log"):
            st = _discretize_one(sys_.get(key), THRESHOLDS[f"system.{key}"], derived)
            if st:
                states[f"system.{key}"] = st
        if sys_.get("load_1") is not None:
            st = _discretize_one(sys_["load_1"], THRESHOLDS["system.load_1"], derived)
            if st:
                states["system.load_1"] = st
        if derived.get("system.net_load_ratio") is not None:
            st = _discretize_one(derived["system.net_load_ratio"], THRESHOLDS["system.net_load_ratio"], derived)
            if st:
                states["system.net_load_ratio"] = st

    mw = snapshot.get("middleware") or {}
    if "error" not in mw:
        for key in ("db_select1_ms",):
            st = _discretize_one(mw.get(key), THRESHOLDS[f"middleware.{key}"], derived)
            if st:
                states[f"middleware.{key}"] = st
        for key in ("db_conn_ratio", "key_pool_ratio"):
            if derived.get(key) is not None:
                st = _discretize_one(derived[key], THRESHOLDS[f"middleware.{key}"], derived)
                if st:
                    states[f"middleware.{key}"] = st
        states["middleware.redis_available"] = (
            "normal" if mw.get("redis_available") else "error"
        )

    biz = snapshot.get("business") or {}
    if "error" not in biz:
        for key in ("error_rate_today", "errors_last_hour"):
            st = _discretize_one(biz.get(key), THRESHOLDS[f"business.{key}"], derived)
            if st:
                states[f"business.{key}"] = st

    # 模型面：复用 chat_records 口径（status failed 与 duration 分位）
    if biz.get("error_rate_today") is not None:
        st = _discretize_one(biz["error_rate_today"], THRESHOLDS["model_error_rate_today"], derived)
        if st:
            states["model_error_rate_today"] = st

    return states


def state_hash(states: Dict[str, str]) -> str:
    """阈值带组合哈希：离散三态 JSON 的 sha256（指标名排序保证稳定）。"""
    payload = json.dumps(states, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def overall_of(states: Dict[str, str]) -> str:
    worst = "normal"
    for st in states.values():
        if _STATE_ORDER.get(st, 0) > _STATE_ORDER[worst]:
            worst = st
    return worst


def rule_fallback(states: Dict[str, str]) -> Dict[str, Any]:
    """规则层判定：LLM 失败/不可用时的回落文案（硬阈值兜底，不调 LLM）。"""
    overall = overall_of(states)
    abnormal = [(k, s) for k, s in sorted(states.items()) if s != "normal"]
    if overall == "normal":
        summary = "各项指标均在正常范围内"
        suggestion = "无需处理，保持观察即可"
    else:
        parts = [f"{_GROUP_LABELS.get(k.split('.', 1)[0], k.split('.', 1)[0])}·{k.split('.', 1)[1]} {s}"
                 for k, s in abnormal]
        summary = ("发现异常指标：" + "；".join(parts))[:300]
        suggestion = "结合下方明细卡与运行中/排队任务排查；持续恶化请联系运维"
    items = [
        {"group": group, "status": overall_of({k: s for k, s in states.items() if k.startswith(group + ".")})}
        for group in ("runner", "system", "middleware", "business")
    ]
    return {
        "overall": overall,
        "summary": summary,
        "items": items,
        "suggestion": suggestion,
        "source": "rule",
    }


def apply_error_floor(verdict: Dict[str, Any], states: Dict[str, str]) -> None:
    """错误态兜底：硬故障（error）由规则层确定性判定，LLM 结论不得低于 error。

    就地修改：整体 overall 与受影响分组的 item status 一并抬升为 error。
    """
    if "error" not in states.values():
        return
    verdict["overall"] = "error"
    for item in verdict.get("items") or []:
        group_states = {k: s for k, s in states.items() if k.startswith(item.get("group", "") + ".")}
        if "error" in group_states.values():
            item["status"] = "error"


def _build_llm_prompt(states: Dict[str, str], snapshot: Dict[str, Any]) -> str:
    compact = {
        "states": states,
        "metrics": {
            "runner": {k: v for k, v in (snapshot.get("runner") or {}).items() if k != "error"},
            "system": {k: v for k, v in (snapshot.get("system") or {}).items() if k not in ("_io_prev", "_net_prev")},
            "middleware": {k: v for k, v in (snapshot.get("middleware") or {}).items() if k != "error"},
            "business": {k: v for k, v in (snapshot.get("business") or {}).items() if k != "error"},
        },
        "thresholds": THRESHOLDS,
    }
    return (
        "你是企业智能体系统的运维研判助手。以下是系统健康监控的当前指标："
        "states 是按阈值表离散出的状态（normal/watch/critical，error 为规则层硬故障判定"
        "（Redis 不可用、调度停摆），你不需要输出 error），metrics 是原始数值，"
        "thresholds 是阈值表。请综合研判，重点捕捉组合异常（例如 CPU 高且队列不推进"
        "指向同步阻塞，CPU 低且队列不推进指向事件循环卡死；模型 P95 高但 Runner 队列"
        "正常则问题在模型侧而非调度侧）。\n"
        "只输出一个 JSON 对象，不要任何其他文字，字段：\n"
        '{"overall": "normal|watch|critical", "summary": "一句话结论（不超过80字）", '
        '"items": [{"group": "runner|system|middleware|business", "status": "normal|watch|critical", '
        '"note": "该组判读（不超过50字）"}], '
        '"suggestion": "建议动作（不超过80字，面向非技术角色）"}\n'
        f"输入数据：\n{json.dumps(compact, ensure_ascii=False, default=str)}"
    )


def _parse_llm_verdict(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        return None
    if verdict.get("overall") not in ("normal", "watch", "critical"):
        return None
    if not isinstance(verdict.get("summary"), str) or not verdict["summary"]:
        return None
    verdict["source"] = "llm"
    verdict.setdefault("items", [])
    verdict.setdefault("suggestion", "")
    return verdict


# ============== 执行入口 ==============

async def _run_llm_assessment(states: Dict[str, str], snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """调用轻量模型研判。

    计费豁免（2026-11-10 用户裁决）：本调用属平台管理后台运维监控功能，
    不向租户计费，参照 send_sms_code 豁免先例与 billing_audit.md §3.8/§5.2 口径；
    已在 billing_audit.md §5.2 豁免索引登记。研判在采集器后台执行、无 HTTP
    request 上下文，不适用 record_admin_llm_usage 模式；如需用量观测留痕，
    追踪库 span 已覆盖（trace 语义），不落 chat_records 不扣费。
    """
    from src.llm.gateway import llm_gateway

    response = await llm_gateway.chat_lite(
        messages=[{"role": "user", "content": _build_llm_prompt(states, snapshot)}],
        temperature=0.2,
        max_tokens=ASSESS_MAX_TOKENS,
    )
    return _parse_llm_verdict(response.get("content", "") if isinstance(response, dict) else "")


def assess_once() -> str:
    """执行一轮研判评估，返回本次动作：'llm' / 'rule' / 'skipped'。

    - 快照缺失（采集器未运行）-> skipped，不覆盖已有结论
    - 阈值带哈希与上次一致 -> 复用上次结论（仅续 TTL），平静时段零 LLM 调用
    - LLM 失败 -> 回落规则层判定（source=rule）
    """
    snapshot = redis_client.get(snapshot_key())
    if not isinstance(snapshot, dict) or not redis_client.is_available():
        return "skipped"

    states = discretize(snapshot)
    digest = state_hash(states)
    prev = redis_client.get(assessment_key())
    if isinstance(prev, dict) and prev.get("hash") == digest and prev.get("source") == "llm":
        redis_client.set(assessment_key(), prev, ex=ASSESSMENT_TTL_SECONDS)
        return "skipped"

    verdict: Optional[Dict[str, Any]] = None
    try:
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            verdict = loop.run_until_complete(_run_llm_assessment(states, snapshot))
        finally:
            loop.close()
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康研判 LLM 调用失败，回落规则层: {e}")

    if verdict is None:
        verdict = rule_fallback(states)
    else:
        apply_error_floor(verdict, states)

    verdict["hash"] = digest
    verdict["assessed_at"] = datetime.now().isoformat(timespec="seconds")
    redis_client.set(assessment_key(), verdict, ex=ASSESSMENT_TTL_SECONDS)
    return "llm" if verdict.get("source") == "llm" else "rule"


def run_assessment_tick() -> None:
    """APScheduler 回调：60s 一轮研判评估（异常吞掉不中断调度）。"""
    try:
        action = assess_once()
        if action != "skipped":
            logger.info(f"后端日志：健康研判已更新（{action}）")
    except Exception as e:
        logger.opt(exception=True).error(f"后端日志：健康研判评估任务异常: {e}")
