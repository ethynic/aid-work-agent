"""download_registry 存储名检测与原始显示名回查测试"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.files.download_registry import (
    is_storage_file_name,
    recover_original_display_name,
)

pytestmark = [pytest.mark.tools]


class TestIsStorageFileName:
    def test_matches_file_id_stem(self):
        assert is_storage_file_name("file_fc9f09e93a9f.pdf") is True
        assert is_storage_file_name("file_abc123def456") is True

    def test_rejects_normal_names(self):
        assert is_storage_file_name("大地系列产品解读-新(2026).pdf") is False
        assert is_storage_file_name("report.pdf") is False
        assert is_storage_file_name("") is False
        # 长度/字符不符
        assert is_storage_file_name("file_abc123.pdf") is False
        assert is_storage_file_name("file_ABC123DEF456.pdf") is False


class TestRecoverOriginalDisplayName:
    def test_recovers_original_name_from_meta(self):
        with patch("src.core.redis_client.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"name": "大地系列产品解读-新(2026).pdf"}
            name = recover_original_display_name("/x/y/file_fc9f09e93a9f.pdf")
        assert name == "大地系列产品解读-新(2026).pdf"

    def test_returns_none_for_normal_name(self):
        with patch("src.core.redis_client.redis_client") as mock_redis:
            name = recover_original_display_name("/x/y/report.pdf")
        assert name is None
        mock_redis.hgetall.assert_not_called()

    def test_returns_none_when_meta_missing(self):
        with patch("src.core.redis_client.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {}
            name = recover_original_display_name("/x/y/file_fc9f09e93a9f.pdf")
        assert name is None

    def test_returns_none_when_meta_name_also_storage_name(self):
        """meta 名仍是存储名（存量丢名副本）时视为无恢复价值"""
        with patch("src.core.redis_client.redis_client") as mock_redis:
            mock_redis.make_key.return_value = "k"
            mock_redis.hgetall.return_value = {"name": "file_fc9f09e93a9f.pdf"}
            name = recover_original_display_name("/x/y/file_fc9f09e93a9f.pdf")
        assert name is None

    def test_returns_none_on_redis_error(self):
        with patch("src.core.redis_client.redis_client") as mock_redis:
            mock_redis.make_key.side_effect = RuntimeError("redis down")
            name = recover_original_display_name("/x/y/file_fc9f09e93a9f.pdf")
        assert name is None
