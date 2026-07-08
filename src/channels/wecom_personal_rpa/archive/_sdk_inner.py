"""企微会话存档 C SDK 的 ctypes 实现层（在子进程内执行）。

⚠️ 本模块**只能被子进程加载**，主进程绝不 import：
- 主进程（gunicorn worker）已加载 cryptography / psycopg 等 C 扩展
- 直接加载 libWeWorkFinanceSdk_C.so 会导致 C 堆状态冲突，worker 静默退出
- 通过 `wecom_finance_sdk.py` 的子进程池代理调用，本模块在 spawn 子进程内 import

API 与上层完全一致：
- `get_chat_data_raw(corpid, secret, seq, limit, proxy, passwd, timeout)`
- `decrypt_data_raw(encrypt_key, encrypt_msg)`
- `get_media_data_raw(corpid, secret, sdk_file_id, index_buf, proxy, passwd, timeout)`
- `is_sdk_available()`

错误用 SDKErrorPayload（可序列化的 dict）回传，主进程据此重建异常。
"""

import ctypes
import json
import os
import threading
from typing import Any, Dict, Optional

from loguru import logger


# ----------------- 路径常量 -----------------

_SDK_DIR = os.path.dirname(os.path.abspath(__file__))
_NATIVE_SDK_DIR = os.path.join(_SDK_DIR, "native_sdk")
_SDK_LIB_PATH = os.path.join(_NATIVE_SDK_DIR, "libWeWorkFinanceSdk_C.so")


# ----------------- ctypes 结构体 -----------------


class Slice_t(ctypes.Structure):
    """SDK Slice 结构体（用于 GetChatData / DecryptData 返回数据）。"""

    _fields_ = [
        ("buf", ctypes.c_char_p),
        ("len", ctypes.c_int),
    ]


class MediaData_t(ctypes.Structure):
    """SDK MediaData 结构体（用于 GetMediaData 分片返回媒体数据）。"""

    _fields_ = [
        ("outindexbuf", ctypes.c_char_p),
        ("out_len", ctypes.c_int),
        ("data", ctypes.c_char_p),
        ("data_len", ctypes.c_int),
        ("is_finish", ctypes.c_int),
    ]


# ----------------- .so 加载（进程级单例） -----------------

_lib: Optional[ctypes.CDLL] = None
_lib_lock = threading.Lock()


def _load_lib() -> ctypes.CDLL:
    """加载 libWeWorkFinanceSdk_C.so（进程级单例）。"""
    global _lib
    if _lib is not None:
        return _lib

    with _lib_lock:
        if _lib is not None:
            return _lib

        if not os.path.exists(_SDK_LIB_PATH):
            raise RuntimeError(
                f"libWeWorkFinanceSdk_C.so 不存在: {_SDK_LIB_PATH}"
            )

        try:
            lib = ctypes.CDLL(_SDK_LIB_PATH)
        except OSError as e:
            msg = str(e)
            hint = ""
            if "cannot open shared object file" in msg and "libWeWorkFinanceSdk" not in msg:
                hint = (
                    "\n疑似缺少系统依赖，请执行：\n"
                    "  apt-get install -y libssl-dev libcurl4-openssl-dev\n"
                    f"或用 ldd {_SDK_LIB_PATH} 查看缺失的依赖。"
                )
            raise RuntimeError(
                f"加载 libWeWorkFinanceSdk_C.so 失败: {msg}{hint}\n"
                f"路径: {_SDK_LIB_PATH}"
            ) from e

        _configure_signatures(lib)
        _lib = lib
        logger.info(f"[WeWorkFinanceSdk] .so 加载成功 path={_SDK_LIB_PATH}")
        return _lib


def _configure_signatures(lib: ctypes.CDLL) -> None:
    """配置 SDK 函数签名（参数类型 + 返回类型）。"""

    lib.NewSdk.argtypes = []
    lib.NewSdk.restype = ctypes.c_void_p

    lib.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.Init.restype = ctypes.c_int

    lib.GetChatData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(Slice_t),
    ]
    lib.GetChatData.restype = ctypes.c_int

    lib.DecryptData.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(Slice_t),
    ]
    lib.DecryptData.restype = ctypes.c_int

    lib.GetMediaData.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(MediaData_t),
    ]
    lib.GetMediaData.restype = ctypes.c_int

    lib.DestroySdk.argtypes = [ctypes.c_void_p]
    lib.DestroySdk.restype = None

    lib.NewSlice.argtypes = []
    lib.NewSlice.restype = ctypes.POINTER(Slice_t)
    lib.FreeSlice.argtypes = [ctypes.POINTER(Slice_t)]
    lib.FreeSlice.restype = None
    lib.GetContentFromSlice.argtypes = [ctypes.POINTER(Slice_t)]
    lib.GetContentFromSlice.restype = ctypes.c_char_p
    lib.GetSliceLen.argtypes = [ctypes.POINTER(Slice_t)]
    lib.GetSliceLen.restype = ctypes.c_int

    lib.NewMediaData.argtypes = []
    lib.NewMediaData.restype = ctypes.POINTER(MediaData_t)
    lib.FreeMediaData.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.FreeMediaData.restype = None
    lib.GetOutIndexBuf.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.GetOutIndexBuf.restype = ctypes.c_char_p
    lib.GetData.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.GetData.restype = ctypes.c_char_p
    lib.GetIndexLen.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.GetIndexLen.restype = ctypes.c_int
    lib.GetDataLen.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.GetDataLen.restype = ctypes.c_int
    lib.IsMediaDataFinish.argtypes = [ctypes.POINTER(MediaData_t)]
    lib.IsMediaDataFinish.restype = ctypes.c_int


# ----------------- 线程局部 sdk 实例 -----------------

_thread_local = threading.local()


def _get_thread_sdk(corpid: str, secret: str) -> int:
    """获取当前线程的 sdk 句柄（按 corpid+secret 缓存）。"""
    lib = _load_lib()

    cache_key = f"{corpid}:{secret}"
    cached = getattr(_thread_local, "sdk", None)
    if cached is not None and cached.get("key") == cache_key:
        return cached["handle"]

    if cached is not None:
        try:
            lib.DestroySdk(cached["handle"])
        except Exception as e:
            logger.warning(f"[WeWorkFinanceSdk] 释放旧 sdk 句柄失败: {e}")

    sdk_handle = lib.NewSdk()
    if not sdk_handle:
        raise RuntimeError("NewSdk 返回空指针")

    ret = lib.Init(sdk_handle, corpid.encode("utf-8"), secret.encode("utf-8"))
    if ret != 0:
        lib.DestroySdk(sdk_handle)
        raise RuntimeError(f"Init 失败 code={ret} corpid={corpid}")

    _thread_local.sdk = {"key": cache_key, "handle": sdk_handle}
    logger.debug(
        f"[WeWorkFinanceSdk] 线程 sdk 初始化成功 thread={threading.current_thread().name} corpid={corpid}"
    )
    return sdk_handle


# ----------------- 高层封装：GetChatData -----------------


def get_chat_data_raw(
    corpid: str,
    secret: str,
    seq: int,
    limit: int,
    proxy: str = "",
    passwd: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """调用 SDK GetChatData 拉取一批会话存档密文（同步阻塞）。"""
    if not corpid:
        raise ValueError("corpid 不能为空")
    if not secret:
        raise ValueError("secret 不能为空")
    if limit < 1 or limit > 1000:
        raise ValueError(f"limit 应在 1~1000 之间，实际 {limit}")

    lib = _load_lib()
    sdk_handle = _get_thread_sdk(corpid, secret)

    chat_datas = lib.NewSlice()
    if not chat_datas:
        raise RuntimeError("NewSlice 返回空指针")

    try:
        ret = lib.GetChatData(
            sdk_handle,
            ctypes.c_uint64(seq),
            ctypes.c_uint(limit),
            proxy.encode("utf-8"),
            passwd.encode("utf-8"),
            ctypes.c_int(timeout),
            chat_datas,
        )
        if ret != 0:
            raise RuntimeError(f"GetChatData code={ret} seq={seq} limit={limit}")

        content_ptr = lib.GetContentFromSlice(chat_datas)
        content_len = lib.GetSliceLen(chat_datas)
        if content_len <= 0 or not content_ptr:
            return {"errcode": 0, "errmsg": "ok", "chatdata": []}

        raw_bytes = ctypes.string_at(content_ptr, content_len)
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RuntimeError(
                f"GetChatData JSON 解析失败: {type(e).__name__}: {e}"
            ) from e
    finally:
        lib.FreeSlice(chat_datas)


# ----------------- 高层封装：DecryptData（保留，主流程不用） -----------------


def decrypt_data_raw(encrypt_key: str, encrypt_msg: str) -> str:
    """调用 SDK DecryptData 解密会话存档消息（同步阻塞）。"""
    lib = _load_lib()

    msg_slice = lib.NewSlice()
    if not msg_slice:
        raise RuntimeError("NewSlice 返回空指针")

    try:
        ret = lib.DecryptData(
            encrypt_key.encode("utf-8"),
            encrypt_msg.encode("utf-8"),
            msg_slice,
        )
        if ret != 0:
            raise RuntimeError(f"DecryptData code={ret}")

        content_ptr = lib.GetContentFromSlice(msg_slice)
        content_len = lib.GetSliceLen(msg_slice)
        if content_len <= 0 or not content_ptr:
            return ""

        raw_bytes = ctypes.string_at(content_ptr, content_len)
        return raw_bytes.decode("utf-8")
    finally:
        lib.FreeSlice(msg_slice)


# ----------------- 高层封装：GetMediaData（保留，主流程不用） -----------------


def get_media_data_raw(
    corpid: str,
    secret: str,
    sdk_file_id: str,
    index_buf: str = "",
    proxy: str = "",
    passwd: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """调用 SDK GetMediaData 分片拉取媒体文件（同步阻塞）。"""
    lib = _load_lib()
    sdk_handle = _get_thread_sdk(corpid, secret)

    media_data = lib.NewMediaData()
    if not media_data:
        raise RuntimeError("NewMediaData 返回空指针")

    try:
        ret = lib.GetMediaData(
            sdk_handle,
            index_buf.encode("utf-8"),
            sdk_file_id.encode("utf-8"),
            proxy.encode("utf-8"),
            passwd.encode("utf-8"),
            ctypes.c_int(timeout),
            media_data,
        )
        if ret != 0:
            raise RuntimeError(f"GetMediaData code={ret} fileid={sdk_file_id}")

        data_ptr = lib.GetData(media_data)
        data_len = lib.GetDataLen(media_data)
        out_index_ptr = lib.GetOutIndexBuf(media_data)
        is_finish = lib.IsMediaDataFinish(media_data)

        data_bytes = ctypes.string_at(data_ptr, data_len) if data_len > 0 and data_ptr else b""
        out_index = (
            ctypes.string_at(out_index_ptr).decode("utf-8") if out_index_ptr else ""
        )

        return {
            "data": data_bytes,
            "out_index_buf": out_index,
            "is_finish": bool(is_finish),
        }
    finally:
        lib.FreeMediaData(media_data)


# ----------------- 环境探测 -----------------


def is_sdk_available() -> bool:
    """探测 SDK 是否可加载（不抛异常）。"""
    try:
        _load_lib()
        return True
    except Exception as e:
        logger.debug(f"[WeWorkFinanceSdk] SDK 不可用: {e}")
        return False
