"""集成测试 fixtures — 真实组件组合，控制外部依赖"""

import os
from pathlib import Path

import pytest


@pytest.fixture
def test_db_path(tmp_path: Path) -> str:
    """临时 SQLite 数据库路径"""
    return str(tmp_path / "test.db")


@pytest.fixture
def test_db_url(test_db_path: str) -> str:
    """临时 SQLite 数据库 URL"""
    return f"sqlite:///{test_db_path}"
