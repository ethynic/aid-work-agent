# -*- coding: utf-8 -*-
"""SubagentConfig.get_max_iterations 轮数上限配置解析测试"""
import pytest

from src.models.subagent import SubagentConfig


def _config(context=None) -> SubagentConfig:
    return SubagentConfig(name="测试智能体", description="测试", context=context or {})


class TestGetMaxIterations:
    def test_default_when_missing(self):
        assert _config().get_max_iterations() == 20

    def test_valid_value(self):
        assert _config({"max_iterations": 60}).get_max_iterations() == 60

    @pytest.mark.parametrize("raw", [None, "abc", [], 1.5])
    def test_invalid_type_falls_back(self, raw):
        assert _config({"max_iterations": raw}).get_max_iterations() == 20

    def test_zero_and_negative_fall_back(self):
        assert _config({"max_iterations": 0}).get_max_iterations() == 20
        assert _config({"max_iterations": -5}).get_max_iterations() == 20

    def test_above_max_is_clamped(self):
        assert _config({"max_iterations": 500}).get_max_iterations() == 100

    def test_string_number_is_accepted(self):
        assert _config({"max_iterations": "45"}).get_max_iterations() == 45

    def test_custom_default_and_max(self):
        cfg = _config({"max_iterations": 0})
        assert cfg.get_max_iterations(default=10) == 10
        assert _config({"max_iterations": 50}).get_max_iterations(max_allowed=30) == 30
