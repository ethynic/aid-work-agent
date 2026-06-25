"""CompressionMetrics 补充测试（Phase 7 §7.2）

独立审查发现现有 test_compression_metrics.py 存在以下盲区：
- 现有 TestRecordInvocation 只校验 hset 的「字段名」，不校验写入的值是否真的累加；
  如果有人把 `int(current) + 1` 改成 `int(current)`，现有测试会全绿（Rule 6 违规）。
- record_ratio 边界（0.0 / 1.0 / 负数）未覆盖
- 4 种 result 计数独立性未覆盖
- get_stats 在 Redis hget 返回非 list 时（脏数据）容错未覆盖
- 并发场景下的「最终一致性」未覆盖

本文件补齐上述盲区，使得任何一行业务逻辑被改坏时都能被捕获。
"""

import threading
from unittest.mock import MagicMock

import pytest

from src.core.compression_metrics import CompressionMetrics


@pytest.fixture
def memory_only_metrics():
    """绕过 Redis，使用纯内存降级路径。

    通过让 Redis 调用抛 RuntimeError，强制走 _mem_counts / _mem_samples，
    这样可以精确断言「累加结果」而不是 mock 的调用次数。

    v3.2.1 P1-4：record_invocation 改用 hincrby，需要同时让 hincrby 抛异常
    才会降级到内存。
    """
    m = CompressionMetrics(client=MagicMock(), max_samples=100)
    m._client.hget.side_effect = RuntimeError("force memory mode")
    m._client.hset.side_effect = RuntimeError("force memory mode")
    m._client.hgetall.side_effect = RuntimeError("force memory mode")
    m._client.delete.side_effect = RuntimeError("force memory mode")
    m._client.hincrby.side_effect = RuntimeError("force memory mode")
    return m


class TestRecordInvocationAccumulation:
    """现有测试只校验字段名，本类校验真正的累加语义。"""

    def test_repeated_invocations_accumulate(self, memory_only_metrics):
        """连续 5 次 success → 计数 = 5（不是 1）。

        如果有人把 `int(current) + 1` 改成 `1` 或 `int(current)`，此测试会失败。
        """
        for _ in range(5):
            memory_only_metrics.record_invocation("success", "chat")
        assert memory_only_metrics._mem_counts["chat:success"] == 5

    def test_four_results_independent(self, memory_only_metrics):
        """4 种 result 计数互不干扰。

        场景：success 3 次、fallback 2 次、skipped 4 次、failed 1 次。
        如果计数 key 拼接错（如丢弃 result 维度），此测试会失败。
        """
        for _ in range(3):
            memory_only_metrics.record_invocation("success", "chat")
        for _ in range(2):
            memory_only_metrics.record_invocation("fallback", "chat")
        for _ in range(4):
            memory_only_metrics.record_invocation("skipped", "chat")
        memory_only_metrics.record_invocation("failed", "chat")

        assert memory_only_metrics._mem_counts["chat:success"] == 3
        assert memory_only_metrics._mem_counts["chat:fallback"] == 2
        assert memory_only_metrics._mem_counts["chat:skipped"] == 4
        assert memory_only_metrics._mem_counts["chat:failed"] == 1

    def test_source_type_isolated(self, memory_only_metrics):
        """不同 source_type 的计数互不干扰。"""
        memory_only_metrics.record_invocation("success", "chat")
        memory_only_metrics.record_invocation("success", "wecom_kf")
        memory_only_metrics.record_invocation("success", "chat")

        assert memory_only_metrics._mem_counts["chat:success"] == 2
        assert memory_only_metrics._mem_counts["wecom_kf:success"] == 1

    def test_empty_source_type_uses_unknown(self, memory_only_metrics):
        """source_type='' 时归入 'unknown:{result}'。

        被测代码：`field = f"{source_type or 'unknown'}:{result}"`
        """
        memory_only_metrics.record_invocation("success", "")
        assert memory_only_metrics._mem_counts["unknown:success"] == 1


class TestRecordRatioBoundaries:
    """record_ratio 边界值覆盖。"""

    def test_ratio_zero(self, memory_only_metrics):
        memory_only_metrics.record_ratio(0.0)
        assert memory_only_metrics._mem_samples["ratio"] == [0.0]

    def test_ratio_one(self, memory_only_metrics):
        """压缩比 = 1.0（极端：摘要和原文一样大）应被接受。"""
        memory_only_metrics.record_ratio(1.0)
        assert memory_only_metrics._mem_samples["ratio"] == [1.0]

    def test_ratio_negative_accepted(self, memory_only_metrics):
        """负数 ratio 在被测代码中没有被显式拒绝（float() 接受负数）。

        记录此行为，便于将来加校验时此测试会失败提醒。
        """
        memory_only_metrics.record_ratio(-0.5)
        assert memory_only_metrics._mem_samples["ratio"] == [-0.5]

    def test_ratio_none_does_not_raise(self, memory_only_metrics):
        """v3.2.1 P0-3：record_ratio(None) 不再抛 TypeError。

        原实现 record_ratio 在外层调 float(ratio)，传入 None 会在 try/except
        之外抛 TypeError，违反「不抛异常」契约。修复后 float 转换交给
        _record_sample 内部 try/except 安全吞掉。
        """
        # 不应抛异常；样本不会被写入
        memory_only_metrics.record_ratio(None)  # type: ignore[arg-type]
        assert memory_only_metrics._mem_samples["ratio"] == []

    def test_ratio_string_does_not_raise(self, memory_only_metrics):
        """v3.2.1 P0-3：record_ratio('garbage') 不抛异常。"""
        memory_only_metrics.record_ratio("garbage")  # type: ignore[arg-type]
        assert memory_only_metrics._mem_samples["ratio"] == []

    def test_duration_invalid_type_ignored(self, memory_only_metrics):
        """record_duration 接受任意类型，_record_sample 内的 float() 失败时静默丢弃。

        与 record_ratio 不同：record_duration 直接转发给 _record_sample，
        所以 float 转换在 try 块内，被 except 捕获 → 静默丢弃。
        """
        memory_only_metrics.record_duration("not-a-number")  # type: ignore[arg-type]
        assert memory_only_metrics._mem_samples["duration"] == []


class TestSamplesTrimming:
    """样本裁剪到 max_samples 的精确语义（保留尾部）。"""

    def test_keep_most_recent(self, memory_only_metrics):
        """max_samples=3，写入 5 个样本 → 保留 [3.0, 4.0, 5.0]（最旧 2 个被丢）。

        现有 test_max_samples_trimming 用 mock side_effect 模拟 Redis 读取，
        本测试用纯内存路径直接断言裁剪后的列表。
        """
        memory_only_metrics._max_samples = 3
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            memory_only_metrics.record_duration(v)
        assert memory_only_metrics._mem_samples["duration"] == [3.0, 4.0, 5.0]


class TestGetStatsStructure:
    def test_empty_does_not_raise(self, memory_only_metrics):
        """空数据时 get_stats 必须返回完整结构（不抛异常）。

        管理后台前端依赖固定字段，缺字段会 KeyError。
        """
        stats = memory_only_metrics.get_stats()
        assert set(stats.keys()) == {"counts", "duration", "ratio", "fallback_duration"}
        assert stats["counts"] == {}
        for kind in ("duration", "ratio", "fallback_duration"):
            assert stats[kind] == {
                "count": 0, "avg": 0.0, "min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0
            }

    def test_with_data_end_to_end(self, memory_only_metrics):
        """完整链路：record_* → get_stats → 字段齐全。"""
        memory_only_metrics.record_invocation("success", "chat")
        memory_only_metrics.record_invocation("success", "chat")
        memory_only_metrics.record_invocation("fallback", "chat")
        memory_only_metrics.record_duration(1.0)
        memory_only_metrics.record_duration(3.0)
        memory_only_metrics.record_ratio(0.3)

        stats = memory_only_metrics.get_stats()
        assert stats["counts"] == {"chat:success": 2, "chat:fallback": 1}
        assert stats["duration"]["count"] == 2
        assert stats["duration"]["avg"] == 2.0
        assert stats["duration"]["min"] == 1.0
        assert stats["duration"]["max"] == 3.0
        assert stats["ratio"]["count"] == 1
        assert stats["ratio"]["avg"] == 0.3


class TestConcurrentRecording:
    """并发场景：多线程同时 record_invocation 不应丢数据 / 崩溃。

    被测代码注释明确说「并发计数的小概率丢失属于可接受范围」，
    因此我们只校验「最终计数 >= 单线程基线的一半」（弱一致性），
    且不抛异常。如果锁逻辑被破坏导致 KeyError / 数据结构损坏，会被捕获。
    """

    def test_concurrent_record_invocation_no_crash(self, memory_only_metrics):
        N_THREADS = 8
        PER_THREAD = 50
        barrier = threading.Barrier(N_THREADS)

        def worker():
            barrier.wait()
            for _ in range(PER_THREAD):
                memory_only_metrics.record_invocation("success", "chat")

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # 不应抛任何异常；计数应是合理的（至少 >= 一半）
        total = memory_only_metrics._mem_counts.get("chat:success", 0)
        assert total >= N_THREADS * PER_THREAD // 2, \
            f"并发丢数据过多: expected ~{N_THREADS*PER_THREAD}, got {total}"


class TestRedisCorruptedData:
    """Redis 中存了脏数据时 get_stats 容错。"""

    def test_corrupted_count_value_falls_back_to_zero(self):
        """hgetall 返回的 value 不是数字 → counts[k]=0，不抛异常。"""
        m = CompressionMetrics(client=MagicMock(), max_samples=100)
        m._client.hgetall.return_value = {"chat:success": "not-a-number"}
        m._client.hget.return_value = []
        stats = m.get_stats()
        assert stats["counts"].get("chat:success") == 0

    def test_samples_non_list_value_falls_back_to_empty(self):
        """hget 返回的 samples 不是 list → 视为空。"""
        m = CompressionMetrics(client=MagicMock(), max_samples=100)
        m._client.hgetall.return_value = {}
        # hget 返回字符串而非 list（异常情况）
        m._client.hget.return_value = "corrupted"
        stats = m.get_stats()
        for kind in ("duration", "ratio", "fallback_duration"):
            assert stats[kind]["count"] == 0


class TestResetClearsMemoryAndRedis:
    def test_reset_clears_memory_counts(self, memory_only_metrics):
        memory_only_metrics.record_invocation("success", "chat")
        assert memory_only_metrics._mem_counts != {}
        memory_only_metrics.reset()
        assert memory_only_metrics._mem_counts == {}
        assert memory_only_metrics._mem_samples == {
            "duration": [], "fallback_duration": [], "ratio": []
        }

    def test_reset_calls_redis_delete_for_all_kinds(self):
        """Redis 正常时，reset 应 delete counts/duration/ratio/fallback_duration 4 个 key。"""
        m = CompressionMetrics(client=MagicMock(), max_samples=100)
        # Redis 正常路径
        m._client.delete.return_value = 1
        m.reset()
        assert m._client.delete.call_count == 4
