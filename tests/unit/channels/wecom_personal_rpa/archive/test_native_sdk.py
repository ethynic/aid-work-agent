"""archive._sdk_inner 单元测试（ctypes 实现层）

覆盖：
- .so 文件存在性检查
- ctypes 加载（Linux x86 真加载；其他平台 skip）
- 函数签名配置（NewSdk / Init / NewSlice / FreeSlice 等可调用）
- 错误处理：.so 不存在 / 加载失败 → RuntimeError
- 线程局部 sdk 实例：同线程同 corpid+secret 复用，不同线程独立
- get_chat_data_raw 参数校验 + Init 失败 + GetChatData 失败 路径

不调真实企微 API（要 corpid+secret），GetChatData 用 mock lib 验证调用流程。

注：本测试在子进程隔离改造前位于 test_native_sdk.py，测的是 wecom_finance_sdk
模块的 ctypes 部分。改造后 ctypes 实现移到 _sdk_inner.py，测试相应迁移。
主进程入口 wecom_finance_sdk.py 的测试在 test_sdk_proxy.py。
"""
import ctypes
import os
import sys
import threading
from unittest.mock import patch

import pytest

from src.channels.wecom_personal_rpa.archive import _sdk_inner
from src.channels.wecom_personal_rpa.archive._sdk_inner import (
    _SDK_LIB_PATH,
    get_chat_data_raw,
)


# ----------------- .so 文件 + 平台探测 -----------------


def test_sdk_lib_path_constant():
    """SDK .so 路径常量指向 native_sdk 子目录下的 libWeWorkFinanceSdk_C.so。"""
    assert _SDK_LIB_PATH.endswith(os.path.join("native_sdk", "libWeWorkFinanceSdk_C.so"))
    assert os.path.basename(_SDK_LIB_PATH) == "libWeWorkFinanceSdk_C.so"


def test_sdk_lib_file_exists():
    """本地开发环境应有 .so 文件（缺失时 skip 并提示部署）。"""
    if not os.path.exists(_SDK_LIB_PATH):
        pytest.skip(
            f"libWeWorkFinanceSdk_C.so 未部署（{_SDK_LIB_PATH}），"
            f"按 docs/system/wecom-personal-rpa-sdk-deploy.md 部署后跑此测试"
        )


# ----------------- ctypes 加载 + 函数签名 -----------------


@pytest.fixture
def loaded_lib():
    """加载 .so（无 .so 或非 Linux 则 skip）。"""
    if not os.path.exists(_SDK_LIB_PATH):
        pytest.skip("libWeWorkFinanceSdk_C.so 不存在")
    if sys.platform not in ("linux", "linux2"):
        pytest.skip(f"当前平台 {sys.platform} 不支持加载 Linux .so")

    # 清掉单例缓存，强制重新加载
    _sdk_inner._lib = None
    try:
        lib = _sdk_inner._load_lib()
        yield lib
    except RuntimeError as e:
        pytest.skip(f".so 加载失败（可能缺 libssl/libcurl）: {e}")
    finally:
        _sdk_inner._lib = None


def test_lib_loads_and_signatures_configured(loaded_lib):
    """.so 加载成功 + 5 个核心函数 + 辅助函数都已配置签名（可调用）。"""
    lib = loaded_lib
    for fn in ("NewSdk", "Init", "GetChatData", "DecryptData", "GetMediaData", "DestroySdk"):
        assert hasattr(lib, fn), f"SDK 缺少函数 {fn}"
    for fn in ("NewSlice", "FreeSlice", "GetContentFromSlice", "GetSliceLen",
               "NewMediaData", "FreeMediaData", "GetOutIndexBuf", "GetData",
               "GetIndexLen", "GetDataLen", "IsMediaDataFinish"):
        assert hasattr(lib, fn), f"SDK 缺少辅助函数 {fn}"


def test_is_sdk_available_returns_bool():
    """is_sdk_available 不抛异常，返回 bool。"""
    saved = _sdk_inner._lib
    _sdk_inner._lib = None
    try:
        result = _sdk_inner.is_sdk_available()
        assert isinstance(result, bool)
    finally:
        _sdk_inner._lib = saved


# ----------------- 错误处理：.so 不存在 / 加载失败 -----------------


def test_load_lib_raises_when_so_missing(tmp_path):
    """.so 路径不存在时抛 RuntimeError（主进程代理层会包成 SDKLoadError）。"""
    fake_path = str(tmp_path / "nonexistent.so")
    with patch.object(_sdk_inner, "_SDK_LIB_PATH", fake_path):
        _sdk_inner._lib = None
        with pytest.raises(RuntimeError) as exc:
            _sdk_inner._load_lib()
        assert "不存在" in str(exc.value)


def test_load_lib_wraps_oserror_into_runtimeerror(tmp_path):
    """ctypes.CDLL 抛 OSError（依赖缺失/格式错）时，包装为 RuntimeError。"""
    fake_so = tmp_path / "fake.so"
    fake_so.write_bytes(b"\x7fELF")

    with patch.object(_sdk_inner, "_SDK_LIB_PATH", str(fake_so)):
        _sdk_inner._lib = None
        with pytest.raises(RuntimeError) as exc:
            _sdk_inner._load_lib()
        assert "加载失败" in str(exc.value) or "libWeWorkFinanceSdk" in str(exc.value)


# ----------------- 线程局部 sdk 实例 -----------------


def test_thread_local_sdk_cached_per_thread(loaded_lib):
    """同线程同 corpid+secret 复用 sdk 实例（NewSdk 只调一次）。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()

    new_sdk_calls = []
    orig_new_sdk = lib.NewSdk

    def _spy_new_sdk():
        new_sdk_calls.append(threading.current_thread().name)
        return orig_new_sdk()

    with patch.object(lib, "NewSdk", side_effect=_spy_new_sdk), \
         patch.object(lib, "Init", return_value=0), \
         patch.object(lib, "DestroySdk"):
        h1 = _sdk_inner._get_thread_sdk("ww_corp_1", "secret_1")
        h2 = _sdk_inner._get_thread_sdk("ww_corp_1", "secret_1")
        assert h1 == h2
        assert len(new_sdk_calls) == 1

        h3 = _sdk_inner._get_thread_sdk("ww_corp_2", "secret_1")
        assert h3 != h1
        assert len(new_sdk_calls) == 2


def test_thread_local_sdk_destroys_old_handle_on_credential_change(loaded_lib):
    """同线程 corpid+secret 变化时，旧 sdk 句柄被 DestroySdk 释放。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()

    handles_created = []

    def _new_sdk_tracker():
        h = ctypes.c_void_p(10000 + len(handles_created))
        handles_created.append(h)
        return h

    with patch.object(lib, "NewSdk", side_effect=_new_sdk_tracker), \
         patch.object(lib, "Init", return_value=0), \
         patch.object(lib, "DestroySdk") as mock_destroy:
        _sdk_inner._get_thread_sdk("ww_corp_a", "secret_a")
        _sdk_inner._get_thread_sdk("ww_corp_b", "secret_b")

    mock_destroy.assert_called_with(handles_created[0])


def test_thread_local_sdk_independent_per_thread(loaded_lib):
    """不同线程各自独立的 sdk 实例。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()

    handles = {}
    barrier = threading.Barrier(2)

    def _worker(name):
        barrier.wait()
        handles[name] = _sdk_inner._get_thread_sdk("ww_corp", "secret")

    handles_seq = iter([1001, 2002])
    with patch.object(lib, "NewSdk", side_effect=lambda: next(handles_seq)), \
         patch.object(lib, "Init", return_value=0), \
         patch.object(lib, "DestroySdk"):
        t1 = threading.Thread(target=_worker, args=("t1",), name="t1")
        t2 = threading.Thread(target=_worker, args=("t2",), name="t2")
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert handles["t1"] != handles["t2"]


# ----------------- get_chat_data_raw 参数校验 -----------------


def test_get_chat_data_raw_validates_params():
    """空 corpid / secret / 非法 limit 抛 ValueError（不调 SDK）。"""
    with pytest.raises(ValueError, match="corpid"):
        get_chat_data_raw("", "secret", 0, 100)
    with pytest.raises(ValueError, match="secret"):
        get_chat_data_raw("ww", "", 0, 100)
    with pytest.raises(ValueError, match="limit"):
        get_chat_data_raw("ww", "sec", 0, 0)
    with pytest.raises(ValueError, match="limit"):
        get_chat_data_raw("ww", "sec", 0, 1001)


# ----------------- get_chat_data_raw 调用流程（mock lib） -----------------


def _make_fake_slice_with_content(content_bytes: bytes):
    """构造一个真实的 ctypes Slice_t，buf 指向 content_bytes。"""
    buf = ctypes.create_string_buffer(content_bytes, len(content_bytes))
    slice_obj = _sdk_inner.Slice_t(buf=ctypes.cast(buf, ctypes.c_char_p), len=len(content_bytes))
    return slice_obj, buf


def test_get_chat_data_raw_parses_json_response(loaded_lib):
    """GetChatData 返回 0 + Slice 内容是 JSON → 正确解析为 dict。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()

    fake_json = b'{"errcode":0,"errmsg":"ok","chatdata":[]}'
    fake_slice, _keep_buf = _make_fake_slice_with_content(fake_json)
    slice_ptr = ctypes.pointer(fake_slice)

    with patch.object(lib, "NewSdk", return_value=ctypes.c_void_p(12345)), \
         patch.object(lib, "Init", return_value=0), \
         patch.object(lib, "NewSlice", return_value=slice_ptr), \
         patch.object(lib, "GetChatData", return_value=0) as mock_get, \
         patch.object(lib, "GetContentFromSlice", return_value=fake_slice.buf), \
         patch.object(lib, "GetSliceLen", return_value=len(fake_json)), \
         patch.object(lib, "FreeSlice") as mock_free, \
         patch.object(lib, "DestroySdk"):
        result = get_chat_data_raw("ww_corp", "secret", 100, 50)

    assert result["errcode"] == 0
    assert result["chatdata"] == []
    mock_get.assert_called_once()
    mock_free.assert_called_once()


def test_get_chat_data_raw_raises_on_nonzero_return(loaded_lib):
    """GetChatData 返回非 0 → 抛 RuntimeError（含 code 信息），但仍 FreeSlice。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()
    fake_slice, _keep_buf = _make_fake_slice_with_content(b"")
    slice_ptr = ctypes.pointer(fake_slice)

    with patch.object(lib, "NewSdk", return_value=ctypes.c_void_p(12345)), \
         patch.object(lib, "Init", return_value=0), \
         patch.object(lib, "NewSlice", return_value=slice_ptr), \
         patch.object(lib, "GetChatData", return_value=10002), \
         patch.object(lib, "FreeSlice") as mock_free, \
         patch.object(lib, "DestroySdk"):
        with pytest.raises(RuntimeError) as exc:
            get_chat_data_raw("ww_corp", "secret", 0, 10)
        assert "code=10002" in str(exc.value)

    mock_free.assert_called_once()


def test_get_thread_sdk_init_failure_destroys_sdk(loaded_lib):
    """Init 返回非 0 → 抛 RuntimeError + DestroySdk 被调。"""
    lib = loaded_lib
    _sdk_inner._thread_local = threading.local()

    sdk_handle = ctypes.c_void_p(12345)
    with patch.object(lib, "NewSdk", return_value=sdk_handle), \
         patch.object(lib, "Init", return_value=10003), \
         patch.object(lib, "DestroySdk") as mock_destroy:
        with pytest.raises(RuntimeError) as exc:
            _sdk_inner._get_thread_sdk("ww_corp", "bad_secret")
        assert "Init" in str(exc.value)
    mock_destroy.assert_called_once_with(sdk_handle)
