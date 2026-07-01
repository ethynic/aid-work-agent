"""上下文压缩指标收集器（Phase 7 §7.2）。

实现设计 §7.2 的 5 个核心指标（v3.0 删除了异步相关 4 个指标）：

  1. context_compression_invocation_total{source_type, result}
     - Counter，result ∈ {success, fallback, skipped, failed}
  2. context_compression_duration_seconds
     - Histogram 样本，记录 compress_now 总耗时（含 LLM 调用）
  3. context_compression_fallback_duration_seconds
     - Histogram 样本，降级路径单独耗时分布
  4. context_compression_ratio
     - Histogram 样本，压缩比分布
  5. session_active_messages / session_active_summary_count
     - Gauge（暂未采样，预留接口）

实现策略：
- 使用 src.core.redis_client 单例，复用项目既有 Redis 连接
- 计数：Redis Hash + HINCRBY 原子自增（v3.2.1 P1-4），field 为
  "{source_type}:{result}"，避免并发 read-modify-write 丢失
- 样本：以 Redis Hash 存储「最近 N 条」+ 滚动统计（avg / min / max / count）
- Redis 不可用时，redis_client 自动降级到内存，调用方无感知

多 worker 一致性（v3.2.1 P1-8）：
- Redis 路径：所有 Gunicorn worker 共享同一 Redis 实例，计数完全一致
- 内存兜底：Redis 完全不可用时降级到进程内 dict，**多个 worker 之间互不可见**，
  指标仅供本进程观察，多 worker 汇总后偏差可能较大；此时 get_stats 反映的是
  「当前 worker 的本地视图」，运维需要警惕；恢复 Redis 后下次写入会重建 Redis 视图
- 滚动窗口样本：samples 是 list，hset 非原子 append，并发下可能丢失少量样本，
  属可接受范围（指标用于趋势观察而非精确计费）

线程安全：本模块所有方法通过 redis_client 单例的内部锁串行化；HINCRBY 是
Redis 端原子操作，无需 Python 层加锁。
"""

import threading
from typing import Any, Dict, List, Optional

from loguru import logger

from src.core.cache_utils import CacheKeys
from src.core.redis_client import redis_client


# 滚动窗口最多保留的样本数（超出后丢弃最旧）
_MAX_SAMPLES = 1000

# 单例锁
_metrics_singleton_lock = threading.Lock()
_metrics_singleton: "Optional[CompressionMetrics]" = None


def _key(kind: str) -> str:
    """构造指标 Redis key。kind ∈ {'counts', 'duration', 'fallback_duration', 'ratio'}"""
    return f"{CacheKeys.COMPRESSION_METRICS}:{kind}"


class CompressionMetrics:
    """上下文压缩指标收集器（单例，通过 get_compression_metrics 获取）。

    契约（v3.2.1 P0-3）：所有 public 方法都**不抛异常**。传入 None / 字符串 /
    其他非法类型时由内部 `_record_sample` 安全吞掉（debug 日志），绝不影响业务主流程。
    因此 `record_ratio` / `record_fallback_duration` 不在外层做 float 转换，
    避免非法输入直接抛 TypeError 给调用方。

    多 worker 一致性（v3.2.1 P1-8）：Redis 路径下 HINCRBY 原子自增保证跨 worker
    一致；Redis 不可用时降级到内存 dict，**指标仅供本进程观察，多 worker 不一致**
    （见模块 docstring）。
    """

    def __init__(self, client: Any = None, max_samples: int = _MAX_SAMPLES):
        self._client = client or redis_client
        self._max_samples = max_samples
        # 内存兜底（Redis 完全不可用时）：与 Redis 结构一致
        self._mem_counts: Dict[str, int] = {}
        self._mem_samples: Dict[str, List[float]] = {
            "duration": [],
            "fallback_duration": [],
            "ratio": [],
        }
        self._lock = threading.Lock()

    # ============== 计数 ==============

    def record_invocation(self, result: str, source_type: str = "") -> None:
        """记录一次 compress_now 调用结果。

        Args:
            result: success / fallback / skipped / failed
                   - success: 摘要 LLM 调用成功
                   - fallback: 摘要 LLM 失败，走降级路径
                   - skipped: check_threshold 未通过（本轮未触发）
                   - failed: compress_now 抛异常
            source_type: 渠道来源（chat / wecom_kf / dingtalk / feishu / wecom_personal_rpa）

        v3.2.1 P1-4：Redis 路径改用 HINCRBY 原子自增，消除并发 read-modify-write
        在多 worker 高并发下的计数丢失。Redis 不可用时仍走内存兜底（不保证跨
        worker 一致，仅供单进程趋势观察）。
        """
        if result not in ("success", "fallback", "skipped", "failed"):
            logger.warning(f"invalid compression metrics result: {result}")
            result = "failed"
        field = f"{source_type or 'unknown'}:{result}"
        try:
            new_val = self._client.hincrby(_key("counts"), field, 1)
            if new_val is None:
                # redis_client 在 Redis 异常时返回 None（已记 warning）→ 降级内存
                raise RuntimeError("hincrby returned None")
        except Exception as e:
            logger.debug(f"compression metrics record_invocation failed: {e}")
            with self._lock:
                self._mem_counts[field] = self._mem_counts.get(field, 0) + 1

    # ============== 样本 ==============

    def record_duration(self, seconds: float) -> None:
        """记录 compress_now 总耗时（秒）"""
        self._record_sample("duration", seconds)

    def record_fallback_duration(self, seconds: float) -> None:
        """记录降级路径耗时（秒）"""
        # v3.2.1 P0-3：float 转换交给 _record_sample 内部 try/except 处理，
        # 避免传入 None / 字符串时在本方法抛 TypeError 给调用方（违反"不抛异常"契约）
        self._record_sample("fallback_duration", seconds)

    def record_ratio(self, ratio: float) -> None:
        """记录压缩比（compressed / original）"""
        # v3.2.1 P0-3：同上，不在外层做 float 转换，由 _record_sample 内部安全吞掉
        self._record_sample("ratio", ratio)

    def _record_sample(self, kind: str, value: float) -> None:
        """记录一个样本到滚动窗口。

        存储策略：保留最近 N 条原始样本（用于 P95 等分位数计算），
        同时维护 sum / count / min / max 便于平均值快速计算。
        """
        if value is None:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        try:
            existing = self._client.hget(_key(kind), "samples") or []
            if not isinstance(existing, list):
                existing = []
            existing.append(v)
            # 裁剪到 max_samples
            if len(existing) > self._max_samples:
                existing = existing[-self._max_samples:]
            self._client.hset(_key(kind), "samples", existing)
        except Exception as e:
            logger.debug(f"compression metrics record_sample failed: {e}")
            with self._lock:
                buf = self._mem_samples.setdefault(kind, [])
                buf.append(v)
                if len(buf) > self._max_samples:
                    del buf[: len(buf) - self._max_samples]

    # ============== 统计读取 ==============

    def get_stats(self) -> Dict[str, Any]:
        """管理后台读取统计。

        Returns:
            {
              "counts": {"chat:success": 12, "chat:fallback": 1, ...},
              "duration": {"count": 13, "avg": 2.3, "p50": 2.1, "p95": 5.8, "min":..., "max":...},
              "ratio": {...},
              "fallback_duration": {...},
            }
        """
        counts: Dict[str, int] = {}
        duration_stats: Dict[str, Any] = {}
        ratio_stats: Dict[str, Any] = {}
        fallback_stats: Dict[str, Any] = {}

        try:
            raw_counts = self._client.hgetall(_key("counts")) or {}
            for k, v in raw_counts.items():
                try:
                    counts[k] = int(v)
                except (TypeError, ValueError):
                    counts[k] = 0
        except Exception as e:
            logger.debug(f"compression metrics get counts failed: {e}")
            with self._lock:
                counts = dict(self._mem_counts)

        for kind, target in (
            ("duration", "duration_stats"),
            ("ratio", "ratio_stats"),
            ("fallback_duration", "fallback_stats"),
        ):
            samples: List[float] = []
            try:
                raw = self._client.hget(_key(kind), "samples")
                if isinstance(raw, list):
                    samples = [float(x) for x in raw if isinstance(x, (int, float))]
            except Exception as e:
                logger.debug(f"compression metrics get {kind} failed: {e}")
                with self._lock:
                    samples = list(self._mem_samples.get(kind, []))
            if kind == "duration":
                duration_stats = self._summarize(samples)
            elif kind == "ratio":
                ratio_stats = self._summarize(samples)
            else:
                fallback_stats = self._summarize(samples)

        return {
            "counts": counts,
            "duration": duration_stats,
            "ratio": ratio_stats,
            "fallback_duration": fallback_stats,
        }

    @staticmethod
    def _summarize(samples: List[float]) -> Dict[str, Any]:
        """计算样本的 count/avg/min/max/p50/p95。"""
        if not samples:
            return {"count": 0, "avg": 0.0, "min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0}
        s = sorted(samples)
        n = len(s)

        def _percentile(p: float) -> float:
            if n == 1:
                return s[0]
            idx = int(round((p / 100.0) * (n - 1)))
            idx = max(0, min(n - 1, idx))
            return s[idx]

        return {
            "count": n,
            "avg": sum(s) / n,
            "min": s[0],
            "max": s[-1],
            "p50": _percentile(50),
            "p95": _percentile(95),
        }

    def reset(self) -> None:
        """清空所有指标（测试 / 运维重置用）"""
        for kind in ("counts", "duration", "ratio", "fallback_duration"):
            try:
                self._client.delete(_key(kind))
            except Exception as e:
                logger.debug(f"compression metrics reset {kind} failed: {e}")
        with self._lock:
            self._mem_counts.clear()
            self._mem_samples = {"duration": [], "fallback_duration": [], "ratio": []}


def get_compression_metrics() -> CompressionMetrics:
    """获取 CompressionMetrics 单例（线程安全）。"""
    global _metrics_singleton
    if _metrics_singleton is None:
        with _metrics_singleton_lock:
            if _metrics_singleton is None:
                _metrics_singleton = CompressionMetrics()
    return _metrics_singleton
