"""delegate_login.py hold_token 托管输出与会话级缓存单元测试

验证自动填表场景的扩展（pre-sales 缺省行为不变）：
- hold_token=true：输出无明文 client_token，改为 token_held=true；缓存照写
- 会话级缓存写入：AID_SESSION_ID 存在时写 EXTERNAL_LOGIN_TOKEN_SESSION（TTL 23h）
- 缓存命中路径也补写会话级缓存
- 无 AID_SESSION_ID 时跳过会话级缓存（pre-sales 后台场景）
- 缺省（不传 hold_token）：输出仍含明文 client_token（兼容 pre-sales 现有消费方）
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
    "display_name": "测试用户",
    "agent_name": "自动填表",
    "created": False,
    "login_url": _LOGIN_URL,
}


def _load_module():
    spec = importlib.util.spec_from_file_location(
        f"delegate_login_hold_{id(_load_module)}", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load_module()


def _run(mod, stdin_data):
    out = io.StringIO()
    with patch.object(mod, "_read_input", return_value=stdin_data), \
         patch.object(mod, "get_tenant_id", return_value="tenant_001"), \
         contextlib.redirect_stdout(out):
        mod.delegate_login()
    return json.loads(out.getvalue())


class TestHoldToken:
    def test_hold_token_omits_plaintext(self, mod):
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_write_cache"), \
             patch.object(mod, "_write_session_cache") as session_mock, \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))), \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000",
                                "hold_token": True})

        assert result["success"] is True
        assert result["token_held"] is True
        assert "client_token" not in result
        assert result["record_id"] == 4
        session_mock.assert_called_once()

    def test_default_still_returns_plaintext(self, mod):
        """缺省不传 hold_token：pre-sales 现有消费方仍拿到明文 client_token"""
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_write_cache"), \
             patch.object(mod, "_write_session_cache"), \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))), \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["success"] is True
        assert result["client_token"] == "tok_abc"
        assert "token_held" not in result


class TestSessionCache:
    def test_session_cache_written_on_login_success(self, mod):
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_write_cache") as write_mock, \
             patch.object(mod, "_write_session_cache") as session_mock, \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))), \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        session_mock.assert_called_once_with("tenant_001", "pre-sales",
                                             dict(_TOKEN_PAYLOAD))
        write_mock.assert_called_once()

    def test_session_cache_backfilled_on_cache_hit(self, mod):
        """mobile 缓存命中时也补写会话级缓存（会话首次调用场景）"""
        with patch.object(mod, "_read_cache", return_value=dict(_TOKEN_PAYLOAD)), \
             patch.object(mod, "_write_cache") as write_mock, \
             patch.object(mod, "_write_session_cache") as session_mock, \
             patch.object(mod, "_call_login_api") as login_mock, \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            result = _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        assert result["cached"] is True
        session_mock.assert_called_once_with("tenant_001", "pre-sales",
                                             dict(_TOKEN_PAYLOAD))
        login_mock.assert_not_called()
        write_mock.assert_not_called()

    def test_session_cache_skipped_without_session_id(self, mod):
        """无 AID_SESSION_ID（pre-sales 后台调试场景）：跳过会话级缓存

        skip 判定在 _write_session_cache 内部，故走真实实现、只 mock set_cached。
        """
        with patch.object(mod, "_read_cache", return_value=None), \
             patch.object(mod, "_write_cache"), \
             patch("src.core.cache_utils.set_cached") as set_cached_mock, \
             patch.object(mod, "_call_login_api",
                          return_value=(True, dict(_TOKEN_PAYLOAD))), \
             patch.object(mod.os, "environ", {"AID_SUBAGENT_ID": "pre-sales"}):
            _run(mod, {"login_url": _LOGIN_URL, "mobile": "13800138000"})

        set_cached_mock.assert_not_called()


class TestWriteSessionCacheImpl:
    def test_writes_with_session_key_and_ttl(self, mod):
        set_cached_mock = MagicMock()
        with patch("src.core.cache_utils.set_cached", set_cached_mock), \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            mod._write_session_cache("tenant_001", "auto-form-filler",
                                     dict(_TOKEN_PAYLOAD))

        set_cached_mock.assert_called_once()
        args = set_cached_mock.call_args
        from src.core.cache_utils import CacheKeys
        assert args.args[0] == CacheKeys.EXTERNAL_LOGIN_TOKEN_SESSION
        assert args.args[1:] == ("tenant_001", "auto-form-filler", "sess_001")
        assert args.kwargs["ttl"] == 23 * 3600

    def test_write_error_not_fatal(self, mod):
        with patch("src.core.cache_utils.set_cached",
                   side_effect=Exception("redis down")), \
             patch.object(mod.os, "environ", {"AID_SESSION_ID": "sess_001"}):
            # 不抛异常即通过
            mod._write_session_cache("tenant_001", "auto-form-filler",
                                     dict(_TOKEN_PAYLOAD))
