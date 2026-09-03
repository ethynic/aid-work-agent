"""pre-sales-api 委托登录脚本单元测试

验证 delegate_login.py：
- 缓存命中直接返回（cached=True，不调登录接口）
- 缓存未命中 -> 调登录接口 -> 写缓存（cached=False）
- force_refresh=True 跳过缓存读取、登录成功后覆盖缓存
- 登录接口业务错误（Code != 0）返回失败
- 参数校验（缺 login_url / 非 https / 缺 mobile / 缺 AGENT_TOKEN / 缺租户）
- 缓存读取异常降级为直接登录
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.unit]

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "skills" / "pre-sales-api-1.0.0" / "scripts" / "delegate_login.py"
)

_LOGIN_URL = "https://erp10605.example.com/api/v1/erp.delegate/login"
_TOKEN_PAYLOAD = {
    "client_token": "tok_abc",
    "record_id": 4,
    "display_name": "覃姗测试",
    "agent_name": "客户管理智能体",
}


def _load_module():
    spec = importlib.util.spec_from_file_location(
        f"delegate_login_under_test_{id(_load_module)}", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load_module()


def _run(mod, stdin_data):
    """执行 delegate_login()，返回解析后的 stdout JSON"""
    out = io.StringIO()
    with patch.object(mod, "_read_input", return_value=stdin_data), \
         patch.object(mod, "get_tenant_id", return_value="tenant_001"), \
         contextlib.redirect_stdout(out):
        mod.delegate_login()
    return json.loads(out.getvalue())


class TestCacheHit:
    def test_cached_token_returned_without_http(self, mod):
        write_mock = MagicMock()
        with patch.object(mod, "_read_cache", return_value=dict(_TOKEN_PAYLOAD)), \
             patch.object(mod, "_write_cache", write_mock), \
             patch.object(mod, "_call_login_api") as login_mock:
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is True
        assert result["cached"] is True
        assert result["client_token"] == "tok_abc"
        login_mock.assert_not_called()
        write_mock.assert_not_called()


class TestCacheMiss:
    def test_login_on_miss_then_write_cache(self, mod):
        write_mock = MagicMock()
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_write_cache", write_mock), \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is True
        assert result["cached"] is False
        assert result["client_token"] == "tok_abc"
        write_mock.assert_called_once()
        args, kwargs = write_mock.call_args
        assert kwargs.get("value", args[-1] if args else None) == _TOKEN_PAYLOAD

    def test_force_refresh_skips_cache_read_but_writes(self, mod):
        write_mock = MagicMock()
        with patch.object(mod, "_read_cache") as read_mock, \
             patch.object(mod, "_write_cache", write_mock), \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))):
            result = _run(mod, {
                "login_url": _LOGIN_URL, "mobile": "13800138000",
                "force_refresh": True,
            })

        read_mock.assert_not_called()
        assert result["success"] is True
        assert result["cached"] is False
        write_mock.assert_called_once()

    def test_cache_read_error_degrades_to_login(self, mod):
        # 模拟真实缓存层异常（Redis 不可用）：读降级为直接登录，写失败不阻断
        with patch("src.core.cache_utils.get_cached",
                   side_effect=Exception("redis down")), \
             patch("src.core.cache_utils.set_cached",
                   side_effect=Exception("redis down")), \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is True
        assert result["client_token"] == "tok_abc"


class TestLoginFailure:
    def test_business_error_code_nonzero(self, mod):
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_call_login_api",
                          return_value=(False, {"error": "委托登录失败"})):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is False
        assert "委托登录失败" in result["error"]

    def test_missing_agent_token(self, mod):
        with patch.object(mod, "os") as os_mock, \
             patch.object(mod, "_read_cache", return_value=None):
            os_mock.environ.get.return_value = ""
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is False
        assert "AGENT_TOKEN" in result["error"]


class TestParamValidation:
    def test_missing_login_url(self, mod):
        result = _run(mod, {"mobile": "13800138000"})
        assert result["success"] is False
        assert "login_url" in result["error"]

    def test_login_url_must_be_https(self, mod):
        result = _run(mod, {"login_url": "http://erp.example.com/login",
                            "mobile": "13800138000"})
        assert result["success"] is False
        assert "https" in result["error"]

    def test_missing_mobile(self, mod):
        result = _run(mod, {"login_url": _LOGIN_URL})
        assert result["success"] is False
        assert "mobile" in result["error"]

    def test_missing_tenant(self, mod):
        out = io.StringIO()
        with patch.object(mod, "_read_input",
                          return_value={"login_url": _LOGIN_URL, "mobile": "13800138000"}), \
             patch.object(mod, "get_tenant_id", return_value=None), \
             contextlib.redirect_stdout(out):
            mod.delegate_login()
        result = json.loads(out.getvalue())
        assert result["success"] is False
        assert "租户" in result["error"]


class TestCallLoginApi:
    def test_http_call_success(self, mod):
        resp_body = json.dumps({"Code": 0, "Response": _TOKEN_PAYLOAD}).encode()
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = resp_body
        with patch.object(mod.os.environ, "get",
                          side_effect=lambda k, d=None: "agt_123" if k == "AGENT_TOKEN" else d), \
             patch.object(mod.urllib.request, "urlopen", return_value=resp_mock) as urlopen_mock:
            ok, data = mod._call_login_api(_LOGIN_URL, "13800138000")

        assert ok is True
        assert data["client_token"] == "tok_abc"
        req = urlopen_mock.call_args.args[0]
        assert req.get_header("Api-authorize-token") == "agt_123"
        assert json.loads(req.data.decode()) == {"mobile": "13800138000"}

    def test_http_call_business_error(self, mod):
        resp_body = json.dumps({"Code": -1, "Error": "委托登录失败"}).encode()
        resp_mock = MagicMock()
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        resp_mock.read.return_value = resp_body
        with patch.object(mod.os.environ, "get",
                          side_effect=lambda k, d=None: "agt_123" if k == "AGENT_TOKEN" else d), \
             patch.object(mod.urllib.request, "urlopen", return_value=resp_mock):
            ok, data = mod._call_login_api(_LOGIN_URL, "13800138000")

        assert ok is False
        assert "委托登录失败" in data["error"]
