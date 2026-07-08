"""archive.wecom_finance_sdk 子进程代理层测试

覆盖：
- 异常类 API（SDKLoadError / SDKCallError）
- 参数校验（在主进程抛 ValueError，不进子进程）
- _unwrap 错误重建逻辑：
  - "不存在 / 加载失败" → SDKLoadError
  - "code=xxx" → SDKCallError(func, code, detail)
  - 其他 → SDKCallError(func, -1, ...)

不实际启动子进程池（会真加载 .so，开发机 Windows 跑不动）。
子进程端到端验证由容器内手测脚本承担。
"""
import pytest

from src.channels.wecom_personal_rpa.archive import wecom_finance_sdk
from src.channels.wecom_personal_rpa.archive.wecom_finance_sdk import (
    SDKCallError,
    SDKLoadError,
    _unwrap,
    get_chat_data_raw,
)


# ----------------- 异常类 -----------------


def test_sdk_load_error_is_runtime_error():
    """SDKLoadError 是 RuntimeError 子类（上层 catch RuntimeError 兼容）。"""
    assert issubclass(SDKLoadError, RuntimeError)


def test_sdk_call_error_attributes():
    """SDKCallError 暴露 func / code 属性（与改造前一致）。"""
    err = SDKCallError("GetChatData", 10001, "seq=0 limit=1")
    assert err.func == "GetChatData"
    assert err.code == 10001
    assert "code=10001" in str(err)
    assert "seq=0 limit=1" in str(err)


def test_sdk_call_error_no_detail():
    """SDKCallError detail 可省略。"""
    err = SDKCallError("Init", -1)
    assert err.func == "Init"
    assert err.code == -1
    assert "code=-1" in str(err)


# ----------------- 参数校验（主进程内，不进子进程） -----------------


def test_get_chat_data_raw_validates_corpid():
    with pytest.raises(ValueError, match="corpid"):
        get_chat_data_raw("", "secret", 0, 100)


def test_get_chat_data_raw_validates_secret():
    with pytest.raises(ValueError, match="secret"):
        get_chat_data_raw("ww", "", 0, 100)


def test_get_chat_data_raw_validates_limit_lower():
    with pytest.raises(ValueError, match="limit"):
        get_chat_data_raw("ww", "sec", 0, 0)


def test_get_chat_data_raw_validates_limit_upper():
    with pytest.raises(ValueError, match="limit"):
        get_chat_data_raw("ww", "sec", 0, 1001)


# ----------------- _unwrap 错误重建 -----------------


def test_unwrap_success_returns_data():
    """ok=True 时直接返回 data。"""
    result = {"ok": True, "data": {"errcode": 0, "chatdata": []}}
    assert _unwrap(result, "GetChatData") == {"errcode": 0, "chatdata": []}


def test_unwrap_load_error_so_missing():
    """.so 不存在类错误 → SDKLoadError。"""
    result = {
        "ok": False,
        "error_type": "RuntimeError",
        "error_msg": "libWeWorkFinanceSdk_C.so 不存在: /app/...",
    }
    with pytest.raises(SDKLoadError) as exc:
        _unwrap(result, "GetChatData")
    assert "不存在" in str(exc.value)


def test_unwrap_load_error_load_failed():
    """加载失败类错误 → SDKLoadError。"""
    result = {
        "ok": False,
        "error_type": "RuntimeError",
        "error_msg": "加载 libWeWorkFinanceSdk_C.so 失败: ...",
    }
    with pytest.raises(SDKLoadError):
        _unwrap(result, "GetChatData")


def test_unwrap_call_error_with_code():
    """code=xxx 类错误 → SDKCallError(func, code)。"""
    result = {
        "ok": False,
        "error_type": "RuntimeError",
        "error_msg": "GetChatData code=10001 seq=0 limit=1",
    }
    with pytest.raises(SDKCallError) as exc:
        _unwrap(result, "GetChatData")
    assert exc.value.func == "GetChatData"
    assert exc.value.code == 10001


def test_unwrap_call_error_negative_code_for_unknown():
    """未知错误 → SDKCallError(func, -1, 详情)。"""
    result = {
        "ok": False,
        "error_type": "ValueError",
        "error_msg": "something unexpected",
    }
    with pytest.raises(SDKCallError) as exc:
        _unwrap(result, "DecryptData")
    assert exc.value.func == "DecryptData"
    assert exc.value.code == -1
    assert "ValueError" in str(exc.value)


def test_unwrap_init_failure_extracts_code():
    """Init 失败的 code 也能被正确提取。"""
    result = {
        "ok": False,
        "error_type": "RuntimeError",
        "error_msg": "Init 失败 code=10003 corpid=ww_test",
    }
    with pytest.raises(SDKCallError) as exc:
        _unwrap(result, "Init")
    assert exc.value.code == 10003


# ----------------- 模块顶层属性（向后兼容） -----------------


def test_wecom_finance_sdk_exposes_expected_api():
    """代理层对外暴露的 API 必须与改造前一致（上层零改动的前提）。"""
    for name in ("SDKLoadError", "SDKCallError",
                 "is_sdk_available", "get_chat_data_raw",
                 "decrypt_data_raw", "get_media_data_raw"):
        assert hasattr(wecom_finance_sdk, name), f"代理层缺少 {name}"
