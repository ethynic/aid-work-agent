"""Redis 夜间巡检单元测试（纯逻辑，fake client）"""

from typing import Dict, List, Tuple

import pytest

from src.core.redis_inspection import (
    CLIENTS_WARN,
    MEM_CRIT_MB,
    MEM_WARN_MB,
    NO_TTL_WARN,
    inspect_redis,
)

pytestmark = pytest.mark.unit


class FakeRedisClient:
    """模拟 RedisClient 的 info/dbsize/scan/ttl 最小接口"""

    def __init__(self, sections: Dict[str, dict], keys: List[str] = None,
                 key_ttls: Dict[str, int] = None, dbsize: int = None):
        self._sections = sections
        self._keys = keys or []
        self._ttls = key_ttls or {}
        self._dbsize = dbsize if dbsize is not None else len(self._keys)

    def info(self, section: str = None) -> dict:
        return self._sections.get(section, {})

    def dbsize(self) -> int:
        return self._dbsize

    def scan(self, cursor: int, match: str = "*", count: int = 100) -> Tuple[int, List[str]]:
        # 简化：一次返回全部键，cursor 归零
        return 0, list(self._keys)

    def ttl(self, key: str) -> int:
        return self._ttls.get(key, -1)


def _baseline_sections() -> Dict[str, dict]:
    """接近生产基线（2026-09-18 实测）的 INFO section"""
    return {
        "memory": {
            "used_memory": int(1.6 * 1024 * 1024),
            "mem_fragmentation_ratio": 5.78,
        },
        "persistence": {
            "aof_enabled": 1,
            "aof_last_write_status": "ok",
            "aof_last_bgrewrite_status": "ok",
            "aof_current_size": 9152734,
        },
        "stats": {"evicted_keys": 0},
        "clients": {"connected_clients": 19},
    }


def test_baseline_no_alerts():
    result = inspect_redis(FakeRedisClient(_baseline_sections(), keys=[f"k{i}" for i in range(10)],
                                           key_ttls={f"k{i}": 300 for i in range(10)}))
    assert result["alerts"] == []
    assert result["metrics"]["dbsize"] == 10
    assert "alerts=0" in result["summary"]


def test_memory_warning_and_critical():
    sections = _baseline_sections()
    sections["memory"]["used_memory"] = MEM_WARN_MB * 1024 * 1024
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "WARNING" and "600MB" in msg for lv, msg in result["alerts"])

    sections["memory"]["used_memory"] = MEM_CRIT_MB * 1024 * 1024
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "ERROR" and "800MB" in msg for lv, msg in result["alerts"])
    # 超过 CRIT 后不再重复报 WARNING
    assert not any(lv == "WARNING" and "600MB" in msg for lv, msg in result["alerts"])


def test_fragmentation_ignored_on_small_dataset():
    # 基线 used=1.6MB、frag=5.78：小数据集下碎片率是噪音，不应告警
    result = inspect_redis(FakeRedisClient(_baseline_sections()))
    assert not any("碎片率" in msg for _, msg in result["alerts"])


def test_fragmentation_alert_on_large_dataset():
    sections = _baseline_sections()
    sections["memory"]["used_memory"] = 300 * 1024 * 1024
    sections["memory"]["mem_fragmentation_ratio"] = 2.0
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "WARNING" and "碎片率" in msg for lv, msg in result["alerts"])


def test_aof_write_failure_is_error():
    sections = _baseline_sections()
    sections["persistence"]["aof_last_write_status"] = "NOMATCH"
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "ERROR" and "AOF" in msg for lv, msg in result["alerts"])


def test_aof_bgrewrite_failure_is_warning():
    sections = _baseline_sections()
    sections["persistence"]["aof_last_bgrewrite_status"] = "ERR"
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "WARNING" and "重写" in msg for lv, msg in result["alerts"])


def test_evicted_keys_alert():
    sections = _baseline_sections()
    sections["stats"]["evicted_keys"] = 3
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "WARNING" and "evicted_keys=3" in msg for lv, msg in result["alerts"])


def test_clients_alert():
    sections = _baseline_sections()
    sections["clients"]["connected_clients"] = CLIENTS_WARN + 1
    result = inspect_redis(FakeRedisClient(sections))
    assert any(lv == "WARNING" and "连接数" in msg for lv, msg in result["alerts"])


def test_no_ttl_leak_alert():
    keys = [f"leak:{i}" for i in range(NO_TTL_WARN + 1)] + ["ok:1"]
    ttls = {"ok:1": 300}
    result = inspect_redis(FakeRedisClient(_baseline_sections(), keys=keys, key_ttls=ttls))
    assert any(lv == "WARNING" and "无 TTL" in msg for lv, msg in result["alerts"])


def test_no_ttl_permanent_keys_normal():
    # 设计内永久键（文件注册表等）少量存在不应告警
    keys = [f"uploaded_file:file_{i}" for i in range(50)] + ["tmp:1"]
    ttls = {"tmp:1": 300}
    result = inspect_redis(FakeRedisClient(_baseline_sections(), keys=keys, key_ttls=ttls))
    assert not any("无 TTL" in msg for _, msg in result["alerts"])


def test_multiple_alerts_aggregated():
    sections = _baseline_sections()
    sections["stats"]["evicted_keys"] = 1
    sections["clients"]["connected_clients"] = CLIENTS_WARN + 5
    result = inspect_redis(FakeRedisClient(sections))
    levels = [lv for lv, _ in result["alerts"]]
    assert "WARNING" in levels
    assert len(result["alerts"]) >= 2
