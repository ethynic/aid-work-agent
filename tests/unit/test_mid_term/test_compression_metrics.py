"""CompressionMetrics 指标收集器单元测试（Phase 7 §7.2）

覆盖：
- record_invocation 计数正确（success / fallback / skipped / failed 四种结果）
- record_duration / record_ratio / record_fallback_duration 样本写入
- get_stats 返回结构完整（counts / duration / ratio / fallback_duration）
- Redis 异常时降级到内存（不抛异常，业务主流程不受影响）
- _summarize 分位数计算正确
- reset 清空指标
"""

from unittest.mock import MagicMock

import pytest

from src.core.compression_metrics import CompressionMetrics


@pytest.fixture
def fresh_metrics():
    """每个测试用独立实例 + reset，避免跨测试污染。"""
    m = CompressionMetrics(client=MagicMock(), max_samples=100)
    # 默认让 client 正常工作
    m._client.hget.return_value = 0
    m._client.hgetall.return_value = {}
    m._client.hset.return_value = None
    # v3.2.1 P1-4：record_invocation 用 hincrby 原子自增
    m._client.hincrby.return_value = 1
    yield m


class TestRecordInvocation:
    def test_success(self, fresh_metrics):
        """v3.2.1 P1-4：record_invocation 调 hincrby（不再 hget+hset）。"""
        fresh_metrics.record_invocation("success", "chat")
        fresh_metrics._client.hincrby.assert_called_once()
        args = fresh_metrics._client.hincrby.call_args
        assert args.args[0].endswith(":counts")
        assert args.args[1] == "chat:success"
        assert args.args[2] == 1  # 增量

    def test_fallback(self, fresh_metrics):
        fresh_metrics.record_invocation("fallback", "wecom_kf")
        args = fresh_metrics._client.hincrby.call_args
        assert args.args[1] == "wecom_kf:fallback"

    def test_skipped(self, fresh_metrics):
        fresh_metrics.record_invocation("skipped", "chat")
        args = fresh_metrics._client.hincrby.call_args
        assert args.args[1] == "chat:skipped"

    def test_failed(self, fresh_metrics):
        fresh_metrics.record_invocation("failed", "")
        args = fresh_metrics._client.hincrby.call_args
        assert args.args[1] == "unknown:failed"

    def test_invalid_result_normalized_to_failed(self, fresh_metrics):
        fresh_metrics.record_invocation("bogus_result", "chat")
        args = fresh_metrics._client.hincrby.call_args
        assert args.args[1] == "chat:failed"


class TestRecordSamples:
    def test_record_duration(self, fresh_metrics):
        fresh_metrics.record_duration(1.23)
        args = fresh_metrics._client.hset.call_args
        assert args.args[0].endswith(":duration")
        samples = args.args[2]
        assert samples == [1.23]

    def test_record_ratio(self, fresh_metrics):
        fresh_metrics.record_ratio(0.45)
        args = fresh_metrics._client.hset.call_args
        assert args.args[1] == "samples"
        assert args.args[2] == [0.45]

    def test_record_fallback_duration(self, fresh_metrics):
        fresh_metrics.record_fallback_duration(0.18)
        args = fresh_metrics._client.hset.call_args
        assert args.args[0].endswith(":fallback_duration")

    def test_max_samples_trimming(self, fresh_metrics):
        """超过 max_samples 时丢弃最旧（保留尾部）。"""
        fresh_metrics._max_samples = 3
        # 第一次：hget 返回 []，append 后写 [1.0]
        fresh_metrics._client.hget.side_effect = [[], [1.0], [1.0, 2.0], [1.0, 2.0, 3.0]]
        fresh_metrics.record_duration(1.0)
        fresh_metrics.record_duration(2.0)
        fresh_metrics.record_duration(3.0)
        fresh_metrics.record_duration(4.0)  # 触发裁剪
        last_call = fresh_metrics._client.hset.call_args
        written = last_call.args[2]
        assert written == [2.0, 3.0, 4.0]


class TestGetStats:
    def test_empty_stats(self, fresh_metrics):
        fresh_metrics._client.hgetall.return_value = {}
        fresh_metrics._client.hget.return_value = []
        stats = fresh_metrics.get_stats()
        assert stats["counts"] == {}
        assert stats["duration"]["count"] == 0
        assert stats["ratio"]["count"] == 0
        assert stats["fallback_duration"]["count"] == 0

    def test_with_data(self, fresh_metrics):
        fresh_metrics._client.hgetall.return_value = {
            "chat:success": "5",
            "chat:fallback": "1",
            "chat:skipped": "10",
        }

        def hget_side_effect(key, field=None):
            if key.endswith(":counts"):
                return None
            if field == "samples":
                if key.endswith(":duration"):
                    return [1.0, 2.0, 3.0, 10.0]
                if key.endswith(":ratio"):
                    return [0.2, 0.3, 0.4]
                if key.endswith(":fallback_duration"):
                    return [0.1, 0.2]
            return None

        fresh_metrics._client.hget.side_effect = hget_side_effect
        stats = fresh_metrics.get_stats()
        assert stats["counts"]["chat:success"] == 5
        assert stats["counts"]["chat:fallback"] == 1
        assert stats["duration"]["count"] == 4
        assert stats["duration"]["avg"] == 4.0
        assert stats["duration"]["min"] == 1.0
        assert stats["duration"]["max"] == 10.0
        assert stats["ratio"]["count"] == 3
        assert stats["fallback_duration"]["count"] == 2


class TestRedisFailure:
    def test_record_invocation_fallback_to_memory(self, fresh_metrics):
        """Redis 异常时降级到内存，不抛错。

        v3.2.1 P1-4：hincrby 抛异常或返回 None（redis_client 在 Redis 故障时返回 None）
        → 走内存兜底。
        """
        fresh_metrics._client.hincrby.side_effect = RuntimeError("redis down")
        # 不应抛异常
        fresh_metrics.record_invocation("success", "chat")
        # 内存中应该有计数
        assert fresh_metrics._mem_counts.get("chat:success") == 1
        # 再调一次，累加
        fresh_metrics.record_invocation("success", "chat")
        assert fresh_metrics._mem_counts.get("chat:success") == 2

    def test_record_invocation_fallback_to_memory_on_none_return(self, fresh_metrics):
        """redis_client.hincrby 在 Redis 故障时返回 None（不抛异常）→ 应降级到内存。

        v3.2.1 P1-4：redis_client.hincrby 把 Redis 异常包装成返回 None。
        """
        fresh_metrics._client.hincrby.return_value = None
        fresh_metrics.record_invocation("success", "chat")
        assert fresh_metrics._mem_counts.get("chat:success") == 1

    def test_record_duration_fallback_to_memory(self, fresh_metrics):
        fresh_metrics._client.hget.side_effect = RuntimeError("redis down")
        fresh_metrics._client.hset.side_effect = RuntimeError("redis down")
        fresh_metrics.record_duration(1.5)
        fresh_metrics.record_duration(2.5)
        assert fresh_metrics._mem_samples["duration"] == [1.5, 2.5]

    def test_get_stats_from_memory_when_redis_down(self, fresh_metrics):
        fresh_metrics._client.hgetall.side_effect = RuntimeError("redis down")
        fresh_metrics._client.hget.side_effect = RuntimeError("redis down")
        with fresh_metrics._lock:
            fresh_metrics._mem_counts = {"chat:success": 3}
            fresh_metrics._mem_samples["duration"] = [1.0, 2.0, 3.0]
        stats = fresh_metrics.get_stats()
        assert stats["counts"] == {"chat:success": 3}
        assert stats["duration"]["count"] == 3
        assert stats["duration"]["avg"] == 2.0


class TestSummarize:
    def test_empty(self):
        result = CompressionMetrics._summarize([])
        assert result == {"count": 0, "avg": 0.0, "min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0}

    def test_single(self):
        result = CompressionMetrics._summarize([5.0])
        assert result["count"] == 1
        assert result["avg"] == 5.0
        assert result["p50"] == 5.0
        assert result["p95"] == 5.0

    def test_percentiles(self):
        samples = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        result = CompressionMetrics._summarize(samples)
        assert result["count"] == 10
        assert result["min"] == 1.0
        assert result["max"] == 10.0
        # P50 = ~5.x，P95 = ~10
        assert 5.0 <= result["p50"] <= 6.0
        assert result["p95"] >= 9.0


class TestReset:
    def test_reset_clears_all(self, fresh_metrics):
        with fresh_metrics._lock:
            fresh_metrics._mem_counts = {"chat:success": 5}
            fresh_metrics._mem_samples["duration"] = [1.0]
        fresh_metrics.reset()
        assert fresh_metrics._mem_counts == {}
        assert fresh_metrics._mem_samples == {"duration": [], "fallback_duration": [], "ratio": []}
        # delete 被调用 4 次（counts/duration/ratio/fallback_duration）
        assert fresh_metrics._client.delete.call_count == 4


class TestGetCompressionMetricsSingleton:
    def test_singleton(self, monkeypatch):
        """get_compression_metrics 返回同一个实例。"""
        import src.core.compression_metrics as mod
        # 重置单例
        monkeypatch.setattr(mod, "_metrics_singleton", None)
        m1 = mod.get_compression_metrics()
        m2 = mod.get_compression_metrics()
        assert m1 is m2
