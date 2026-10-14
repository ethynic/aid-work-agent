"""form-filling-api 技能脚本单元测试：get_user_phone.py + load_form_schema.py

get_user_phone.py：
- 无参查询：已绑定返回 bound=true + phone；未绑定返回 bound=false + 引导提示
- 无用户上下文（AID_USER_ID 缺失）拒绝
- verify 模式：验证通过绑定手机号；验证码错误不绑定；绑定失败返回 error
- 手机号格式校验

load_form_schema.py：
- 参数校验（缺 module / base_url 非 https）
- 缓存命中直接返回（cached=True，不调接口）
- 缓存未命中 -> 调 schema 接口 -> 精简后写缓存
- 缺 AGENT_TOKEN / 缺委托会话（client_token 未托管）拒绝
- Code=-99 提示重新委托登录；Code!=0 业务错误透传
- _slim_schema 精简字段与 _no_truncate 声明
"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.skills, pytest.mark.unit]

_SKILL_DIR = Path(__file__).resolve().parents[3] / "src" / "skills" / "form-filling-api-1.0.0" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(
        f"{name}_{id(_load)}", _SKILL_DIR / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, func, stdin_data=None):
    out = io.StringIO()
    with patch.object(mod, "sys") as sys_mock:
        sys_mock.stdin.read.return_value = json.dumps(stdin_data or {})
        with contextlib.redirect_stdout(out):
            func()
    return json.loads(out.getvalue())


# ============== get_user_phone.py ==============

class TestQueryBoundPhone:
    def test_bound_returns_phone(self):
        mod = _load("get_user_phone")
        with patch.object(mod, "_get_user_id", return_value="user_001"), \
             patch("src.db.models.UserDB", **{
                 "get_by_id.return_value": {"phone": "13800138000"}}):
            result = _run(mod, mod.query_bound_phone)

        assert result == {"success": True, "bound": True, "phone": "13800138000"}

    def test_unbound_returns_hint(self):
        mod = _load("get_user_phone")
        with patch.object(mod, "_get_user_id", return_value="user_001"), \
             patch("src.db.models.UserDB", **{"get_by_id.return_value": {"phone": None}}):
            result = _run(mod, mod.query_bound_phone)

        assert result["success"] is True
        assert result["bound"] is False
        assert "验证码" in result["hint"]

    def test_no_user_context_rejected(self):
        mod = _load("get_user_phone")
        with patch.object(mod, "_get_user_id", return_value=""):
            result = _run(mod, mod.query_bound_phone)

        assert result["success"] is False
        assert result["bound"] is False

    def test_db_error_degrades(self):
        mod = _load("get_user_phone")
        with patch.object(mod, "_get_user_id", return_value="user_001"), \
             patch("src.db.models.UserDB", **{
                 "get_by_id.side_effect": Exception("db down")}):
            result = _run(mod, mod.query_bound_phone)

        assert result["success"] is False
        assert result["bound"] is False


class TestVerifyAndBind:
    def _verify(self, stdin_data, verify_ok=True, update_ok=True, user_id="user_001",
               verify_exc=None):
        mod = _load("get_user_phone")
        with patch.object(mod, "_get_user_id", return_value=user_id), \
             patch("src.db.models.verify_sms_code",
                   return_value=verify_ok, side_effect=verify_exc), \
             patch("src.db.models.UserDB", **{
                 "update_info.return_value": update_ok}):
            result = _run(mod, mod.verify_and_bind, stdin_data)
        return result

    def test_verify_success_binds_phone(self):
        result = self._verify({"mobile": "13800138000", "code": "123456"})
        assert result["success"] is True
        assert result["bound"] is True
        assert result["phone"] == "13800138000"

    def test_invalid_mobile_rejected(self):
        result = self._verify({"mobile": "12345", "code": "123456"})
        assert result["success"] is False
        assert result["reason"] == "invalid_mobile"

    def test_missing_code_rejected(self):
        result = self._verify({"mobile": "13800138000", "code": ""})
        assert result["success"] is False
        assert result["reason"] == "missing_code"

    def test_no_user_cannot_bind(self):
        result = self._verify({"mobile": "13800138000", "code": "123456"}, user_id="")
        assert result["success"] is False
        assert result["reason"] == "no_user"

    def test_wrong_code_not_bound(self):
        result = self._verify({"mobile": "13800138000", "code": "123456"}, verify_ok=False)
        assert result["success"] is False
        assert result["reason"] == "invalid"

    def test_verify_exception_reported(self):
        result = self._verify({"mobile": "13800138000", "code": "123456"},
                              verify_exc=Exception("db down"))
        assert result["success"] is False
        assert result["reason"] == "error"

    def test_update_failure_reported(self):
        result = self._verify({"mobile": "13800138000", "code": "123456"}, update_ok=False)
        assert result["success"] is False
        assert result["reason"] == "error"


# ============== load_form_schema.py ==============

_BASE_URL = "https://erp10605.example.com"
_SCHEMA_RESPONSE = {
    "module": "kehuxinxi",
    "display_name": "客户信息",
    "primary_key": "id",
    "tables": [
        {
            "table_name": "t_kehuxinxi",
            "display_name": "客户信息",
            "role": "main",
            "foreign_key": None,
            "attrs": [
                {"attr_name": "xingming", "display_name": "客户名称",
                 "component": "input", "required": True,
                 "create_hidden": None, "extra_key": "should_be_dropped"},
                {"attr_name": "", "display_name": "无效字段"},
            ],
        }
    ],
}


def _http_response(body: dict):
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(body).encode()
    return resp


class TestLoadFormSchema:
    def _run_schema(self, stdin_data, env=None, cached=None, urlopen=None):
        """执行 load_form_schema()。

        get_cached 按前缀分流：form_schema 前缀返回 cached，
        external_login_token_session 前缀返回托管 client_token。
        """
        mod = _load("load_form_schema")
        env = env if env is not None else {
            "AID_TENANT_ID": "tenant_001", "AID_SUBAGENT_ID": "auto-form-filler",
            "AID_SESSION_ID": "sess_001", "AGENT_TOKEN": "agt_123",
        }

        def get_cached_side_effect(prefix, *args):
            from src.core.cache_utils import CacheKeys
            if prefix == CacheKeys.EXTERNAL_LOGIN_TOKEN_SESSION:
                return {"client_token": "tok_held"}
            return cached

        with patch.dict(mod.os.environ, env, clear=False), \
             patch("src.core.cache_utils.get_cached",
                   side_effect=get_cached_side_effect), \
             patch("src.core.cache_utils.set_cached") as set_cached_mock, \
             patch.object(mod.urllib.request, "urlopen",
                          side_effect=urlopen) if urlopen else \
             patch.object(mod.urllib.request, "urlopen"):
            result = _run(mod, mod.load_form_schema, stdin_data)
        return result, set_cached_mock

    def test_missing_module_rejected(self):
        result, _ = self._run_schema({"base_url": _BASE_URL})
        assert result["success"] is False
        assert "module" in result["error"]

    def test_base_url_must_be_https(self):
        result, _ = self._run_schema({"module": "kehuxinxi",
                                      "base_url": "http://erp.example.com"})
        assert result["success"] is False
        assert "https" in result["error"]

    def test_missing_tenant_rejected(self):
        env = {"AID_TENANT_ID": "", "CURRENT_TENANT_ID": "",
               "AGENT_TOKEN": "agt_123", "AID_SESSION_ID": "sess_001"}
        result, _ = self._run_schema({"module": "kehuxinxi", "base_url": _BASE_URL},
                                     env=env)
        assert result["success"] is False
        assert "租户" in result["error"]

    def test_cache_hit_returns_without_http(self):
        cached_schema = {
            "module": "kehuxinxi", "display_name": "客户信息",
            "primary_key": "id",
            "tables": [{"table_name": "t_kehuxinxi", "display_name": "客户信息",
                        "role": "main", "foreign_key": None, "attrs": []}],
        }
        result, set_mock = self._run_schema(
            {"module": "kehuxinxi", "base_url": _BASE_URL}, cached=cached_schema)

        assert result["success"] is True
        assert result["cached"] is True
        assert result["_no_truncate"] is True
        set_mock.assert_not_called()

    def test_cache_miss_calls_api_and_writes_cache(self):
        result, set_mock = self._run_schema(
            {"module": "kehuxinxi", "base_url": _BASE_URL}, cached=None,
            urlopen=lambda *a, **k: _http_response(
                {"Code": 0, "Response": _SCHEMA_RESPONSE}))

        assert result["success"] is True
        assert result["cached"] is False
        assert result["module"] == "kehuxinxi"
        # 精简：非白名单键被剔除，空 attr_name 被丢弃
        attrs = result["tables"][0]["attrs"]
        assert attrs[0]["attr_name"] == "xingming"
        assert "extra_key" not in attrs[0]
        assert len(attrs) == 1
        set_mock.assert_called_once()
        assert set_mock.call_args.kwargs["ttl"] == 3600

    def test_missing_agent_token_rejected(self):
        env = {"AID_TENANT_ID": "tenant_001", "AGENT_TOKEN": "",
               "AID_SESSION_ID": "sess_001"}
        result, _ = self._run_schema({"module": "kehuxinxi", "base_url": _BASE_URL},
                                     env=env)
        assert result["success"] is False
        assert "AGENT_TOKEN" in result["error"]

    def test_missing_session_token_rejected(self):
        """无 AID_SESSION_ID -> 委托会话缓存读不到 -> 提示先 delegate_login"""
        env = {"AID_TENANT_ID": "tenant_001", "AGENT_TOKEN": "agt_123",
               "AID_SESSION_ID": ""}

        def get_cached_no_session(prefix, *args):
            from src.core.cache_utils import CacheKeys
            if prefix == CacheKeys.EXTERNAL_LOGIN_TOKEN_SESSION:
                return None
            return None

        mod = _load("load_form_schema")
        with patch.dict(mod.os.environ, env, clear=False), \
             patch("src.core.cache_utils.get_cached",
                   side_effect=get_cached_no_session), \
             patch("src.core.cache_utils.set_cached"):
            result = _run(mod, mod.load_form_schema,
                          {"module": "kehuxinxi", "base_url": _BASE_URL})

        assert result["success"] is False
        assert "delegate_login" in result["hint"]

    def test_code_minus99_requests_relogin(self):
        result, _ = self._run_schema(
            {"module": "kehuxinxi", "base_url": _BASE_URL}, cached=None,
            urlopen=lambda *a, **k: _http_response({"Code": -99}))

        assert result["success"] is False
        assert "-99" in result["error"]
        assert "delegate_login" in result["hint"]

    def test_business_error_passthrough(self):
        result, _ = self._run_schema(
            {"module": "kehuxinxi", "base_url": _BASE_URL}, cached=None,
            urlopen=lambda *a, **k: _http_response(
                {"Code": -1, "Error": "模块不存在"}))

        assert result["success"] is False
        assert "模块不存在" in result["error"]

    def test_empty_tables_reported(self):
        result, _ = self._run_schema(
            {"module": "kehuxinxi", "base_url": _BASE_URL}, cached=None,
            urlopen=lambda *a, **k: _http_response(
                {"Code": 0, "Response": {"module": "kehuxinxi", "tables": []}}))

        assert result["success"] is False
        assert "未返回字段元数据" in result["error"]

    def test_unexpected_exception_returns_json(self):
        """__main__ 兜底包装：未预期异常以 JSON 失败返回，不抛栈"""
        mod = _load("load_form_schema")
        out = io.StringIO()
        with patch.object(mod, "_read_input", side_effect=Exception("boom")), \
             contextlib.redirect_stdout(out):
            try:
                mod.load_form_schema()
            except Exception as e:
                print(json.dumps(
                    {"success": False, "error": f"schema 加载脚本异常: {e}"},
                    ensure_ascii=False))
        result = json.loads(out.getvalue())
        assert result["success"] is False
        assert "异常" in result["error"]
