"""浏览器配置一致性测试。"""

from pathlib import Path

import pytest
import yaml

from src.config.settings import BrowserToolConfig


pytestmark = [pytest.mark.unit, pytest.mark.browser]


def test_yaml_headless_matches_settings_default():
    """生产 YAML 与 Pydantic 默认值必须同为无头模式。"""
    project_root = Path(__file__).resolve().parents[4]
    config = yaml.safe_load((project_root / "configs" / "config.yaml").read_text(encoding="utf-8"))

    assert BrowserToolConfig().headless is True
    assert config["tools"]["browser"]["headless"] is BrowserToolConfig().headless
    assert config["tools"]["browser"]["task_timeout"] == BrowserToolConfig().task_timeout
