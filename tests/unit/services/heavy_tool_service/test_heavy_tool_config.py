"""重工具服务配置读取单测（设计 §4.1/§6.1）。

覆盖：模式开关解析（local 默认 / remote）、URL 缺省与跨机覆盖、并发钳制、
路径 allowlist 前缀、token 缺失 fail-closed。"""

import pytest

from src.services.heavy_tool_service import config


class TestHeavyToolsMode:
    def test_default_local_when_unset(self, monkeypatch):
        monkeypatch.delenv("HEAVY_TOOLS_MODE", raising=False)
        assert config.heavy_tools_mode() == "local"
        assert config.is_remote_mode() is False

    @pytest.mark.parametrize("value", ["local", "LOCAL", "", "unexpected"])
    def test_non_remote_values(self, monkeypatch, value):
        monkeypatch.setenv("HEAVY_TOOLS_MODE", value)
        assert config.is_remote_mode() is False

    def test_remote_mode(self, monkeypatch):
        monkeypatch.setenv("HEAVY_TOOLS_MODE", "remote")
        assert config.is_remote_mode() is True


class TestHeavyToolsUrl:
    def test_default_service_dns(self, monkeypatch):
        monkeypatch.delenv("HEAVY_TOOLS_URL", raising=False)
        assert config.heavy_tools_url() == "http://heavy-tools:8093"

    def test_cross_machine_override(self, monkeypatch):
        # §6.1：跨机部署改内网地址（沿用 AGENT_RUNNER_API_URL 注入先例）
        monkeypatch.setenv("HEAVY_TOOLS_URL", "http://10.0.0.9:8093/")
        assert config.heavy_tools_url() == "http://10.0.0.9:8093"


class TestConcurrency:
    def test_default_one(self, monkeypatch):
        monkeypatch.delenv("HEAVY_TOOLS_CONCURRENCY", raising=False)
        assert config.heavy_tools_concurrency() == 1

    def test_invalid_value_falls_back_to_one(self, monkeypatch):
        monkeypatch.setenv("HEAVY_TOOLS_CONCURRENCY", "not-a-number")
        assert config.heavy_tools_concurrency() == 1

    def test_zero_clamped_to_one(self, monkeypatch):
        monkeypatch.setenv("HEAVY_TOOLS_CONCURRENCY", "0")
        assert config.heavy_tools_concurrency() == 1


class TestAllowedPathRoots:
    def test_default_roots(self, monkeypatch):
        monkeypatch.delenv("HEAVY_TOOLS_STORAGE_ROOT", raising=False)
        monkeypatch.delenv("HEAVY_TOOLS_UPLOADS_ROOT", raising=False)
        assert config.allowed_path_roots() == ("/app/storage", "/app/uploads")

    def test_custom_roots(self, monkeypatch):
        monkeypatch.setenv("HEAVY_TOOLS_STORAGE_ROOT", "/data/storage/")
        monkeypatch.setenv("HEAVY_TOOLS_UPLOADS_ROOT", "/data/uploads/")
        roots = config.allowed_path_roots()
        assert roots == ("/data/storage", "/data/uploads")


class TestServiceToken:
    def test_missing_token_is_fail_closed(self, monkeypatch):
        monkeypatch.delenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", raising=False)
        assert config.service_token() == ""
        assert config.require_remote_token() is False

    def test_token_present(self, monkeypatch):
        monkeypatch.setenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", "tok")
        assert config.require_remote_token() is True
