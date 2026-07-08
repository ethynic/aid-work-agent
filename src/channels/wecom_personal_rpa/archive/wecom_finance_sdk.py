"""企微会话存档 C SDK (libWeWorkFinanceSdk_C.so) 的 Python ctypes 封装

企微官方文档 https://developer.work.weixin.qq.com/document/path/91774 明确说明：
会话存档拉取消息必须用 C 语言 SDK 的 GetChatData 函数，**没有 HTTP REST API**。
本模块用 ctypes 加载 .so 并封装 5 个核心函数：
  - NewSdk / Init / GetChatData / DecryptData / GetMediaData
  + 辅助函数 NewSlice / FreeSlice / GetContentFromSlice / GetSliceLen
  + MediaData 系列（GetMediaData 配套）

线程安全策略（来自 SDK 示例 tool_testSdk.cpp 注释）：
  - 「每个线程要一个 sdk 实例，不能跨线程共享」
  - 用 threading.local() 给每个线程绑定独立的 sdk 实例
  - Init 后 sdk 可以一直使用（不需要每次拉取都 Init）

主流程只用到 GetChatData；DecryptData 已封装但暂不使用（解密仍走 chat_crypto.py 的
Python 实现，已验证可用）。GetMediaData 用于未来媒体下载，本期不调用。

错误码（来自头文件注释）：
  10000 参数错误；10001 网络错误；10002 数据解析失败；10003 系统失败
  10004 密钥/会话存档失败；10005 fileid 错误；10006 解密失败
  10007 找不到消息加密版本对应私钥；10008 错误 encrypt_key；10009 ip 非法
  10010 数据过期；10011 证书错误
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
# 注意：WeWorkFinanceSdk_t 在头文件中是 forward-declared（typedef struct WeWorkFinanceSdk_t
# WeWorkFinanceSdk_t;），实际定义在 .so 内部不透明。ctypes 端只需当作不透明指针使用。


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


# ----------------- 异常 -----------------


class SDKLoadError(RuntimeError):
    """.so 加载失败（文件不存在 / 依赖缺失 / 平台不符）。"""


class SDKCallError(RuntimeError):
    """SDK 函数调用返回非 0 错误码。

    Attributes:
        func: 调用的 SDK 函数名（如 "Init" / "GetChatData"）。
        code: SDK 返回的错误码（见头文件注释）。
    """

    def __init__(self, func: str, code: int, detail: str = ""):
        msg = f"SDK {func} 调用失败 code={code}"
        if detail:
            msg += f" detail={detail}"
        super().__init__(msg)
        self.func = func
        self.code = code


# ----------------- .so 加载 -----------------

# 全局 CDLL 句柄（进程级单例，所有线程共享 .so 但 sdk 实例独立）
_lib: Optional[ctypes.CDLL] = None
_lib_lock = threading.Lock()


def _load_lib() -> ctypes.CDLL:
    """加载 libWeWorkFinanceSdk_C.so（进程级单例）。

    Raises:
        SDKLoadError: 文件不存在 / 平台不符 / 依赖缺失。
    """
    global _lib
    if _lib is not None:
        return _lib

    with _lib_lock:
        if _lib is not None:
            return _lib

        if not os.path.exists(_SDK_LIB_PATH):
            raise SDKLoadError(
                f"libWeWorkFinanceSdk_C.so 不存在: {_SDK_LIB_PATH}\n"
                f"请按 docs/system/wecom-personal-rpa-sdk-deploy.md 部署 SDK 文件。"
            )

        try:
            lib = ctypes.CDLL(_SDK_LIB_PATH)
        except OSError as e:
            # 区分「文件不存在」和「依赖缺失」
            msg = str(e)
            hint = ""
            # 依赖缺失常见提示：cannot open shared object file / undefined symbol
            if "cannot open shared object file" in msg and "libWeWorkFinanceSdk" not in msg:
                # 通常缺 libssl / libcurl
                hint = (
                    "\n疑似缺少系统依赖，请执行：\n"
                    "  apt-get install -y libssl-dev libcurl4-openssl-dev\n"
                    f"或用 ldd {_SDK_LIB_PATH} 查看缺失的依赖。"
                )
            raise SDKLoadError(
                f"加载 libWeWorkFinanceSdk_C.so 失败: {msg}{hint}\n"
                f"路径: {_SDK_LIB_PATH}"
            ) from e

        # 配置函数签名（让 ctypes 正确处理参数类型 + 返回值）
        _configure_signatures(lib)
        _lib = lib
        logger.info(f"[WeWorkFinanceSdk] .so 加载成功 path={_SDK_LIB_PATH}")
        return _lib


def _configure_signatures(lib: ctypes.CDLL) -> None:
    """配置 SDK 函数签名（参数类型 + 返回类型）。"""

    # NewSdk() -> WeWorkFinanceSdk_t*
    lib.NewSdk.argtypes = []
    lib.NewSdk.restype = ctypes.c_void_p

    # Init(WeWorkFinanceSdk_t*, const char*, const char*) -> int
    lib.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    lib.Init.restype = ctypes.c_int

    # GetChatData(sdk, seq, limit, proxy, passwd, timeout, Slice_t*) -> int
    lib.GetChatData.argtypes = [
        ctypes.c_void_p,            # sdk
        ctypes.c_uint64,            # seq
        ctypes.c_uint,              # limit
        ctypes.c_char_p,            # proxy
        ctypes.c_char_p,            # passwd
        ctypes.c_int,               # timeout
        ctypes.POINTER(Slice_t),    # chatDatas
    ]
    lib.GetChatData.restype = ctypes.c_int

    # DecryptData(const char* encrypt_key, const char* encrypt_msg, Slice_t* msg) -> int
    lib.DecryptData.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.POINTER(Slice_t),
    ]
    lib.DecryptData.restype = ctypes.c_int

    # GetMediaData(sdk, indexbuf, sdkFileid, proxy, passwd, timeout, MediaData_t*) -> int
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

    # DestroySdk(WeWorkFinanceSdk_t*)
    lib.DestroySdk.argtypes = [ctypes.c_void_p]
    lib.DestroySdk.restype = None

    # Slice 系列
    lib.NewSlice.argtypes = []
    lib.NewSlice.restype = ctypes.POINTER(Slice_t)
    lib.FreeSlice.argtypes = [ctypes.POINTER(Slice_t)]
    lib.FreeSlice.restype = None
    lib.GetContentFromSlice.argtypes = [ctypes.POINTER(Slice_t)]
    lib.GetContentFromSlice.restype = ctypes.c_char_p
    lib.GetSliceLen.argtypes = [ctypes.POINTER(Slice_t)]
    lib.GetSliceLen.restype = ctypes.c_int

    # MediaData 系列
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


def is_sdk_available() -> bool:
    """探测 SDK 是否可加载（不抛异常）。

    用于运行环境检测（如开发机无 .so 时不让 import 失败）。
    """
    try:
        _load_lib()
        return True
    except SDKLoadError as e:
        logger.debug(f"[WeWorkFinanceSdk] SDK 不可用: {e}")
        return False


# ----------------- 线程局部 sdk 实例 -----------------

# 每个线程独立 sdk 实例（SDK 示例注释：每个线程要一个 sdk 实例，不能跨线程共享）
_thread_local = threading.local()


def _get_thread_sdk(corpid: str, secret: str) -> int:
    """获取当前线程的 sdk 句柄（按 corpid+secret 缓存）。

    SDK 示例注释：「Init 后 sdk 可以一直使用（不需要每次拉取都 Init）」。
    所以同一线程内同一 corpid+secret 复用 sdk 实例，避免重复 Init。

    Args:
        corpid: 企业 ID。
        secret: 会话存档 secret。

    Returns:
        sdk 指针（int，ctypes c_void_p 转 int）。

    Raises:
        SDKCallError: Init 失败。
    """
    lib = _load_lib()

    cache_key = f"{corpid}:{secret}"
    cached = getattr(_thread_local, "sdk", None)
    if cached is not None and cached.get("key") == cache_key:
        return cached["handle"]

    # 缓存 miss：若同线程之前缓存了不同 corpid+secret 的 sdk，先 DestroySdk 释放旧句柄
    # （asyncio.to_thread 默认 ThreadPoolExecutor 线程会被复用跨租户调用，不释放会泄漏）
    if cached is not None:
        try:
            lib.DestroySdk(cached["handle"])
        except Exception as e:
            logger.warning(f"[WeWorkFinanceSdk] 释放旧 sdk 句柄失败: {e}")

    # 新建 + Init
    sdk_handle = lib.NewSdk()
    if not sdk_handle:
        raise SDKCallError("NewSdk", -1, "NewSdk 返回空指针")

    ret = lib.Init(sdk_handle, corpid.encode("utf-8"), secret.encode("utf-8"))
    if ret != 0:
        # Init 失败要销毁 sdk（避免泄漏）
        lib.DestroySdk(sdk_handle)
        raise SDKCallError("Init", ret, f"corpid={corpid}")

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
    """调用 SDK GetChatData 拉取一批会话存档密文（同步阻塞）。

    Args:
        corpid: 企业 ID。
        secret: 会话存档 secret。
        seq: 起始 seq（拉取 seq > 此值的消息，首次传 0）。
        limit: 单批次上限（企微限制 ≤1000）。
        proxy: 代理地址（不用传空字符串），格式 socks5://host:port 或 http://host:port。
        passwd: 代理账号密码（不用传空字符串），格式 user:pass。
        timeout: 超时秒数（建议 ≥5）。

    Returns:
        SDK 返回的 JSON dict，结构：
            {"errcode": 0, "errmsg": "ok",
             "chatdata": [{"seq", "msgid", "publickey_ver",
                            "encrypt_random_key", "encrypt_chat_msg"}, ...]}

    Raises:
        SDKCallError: GetChatData 返回非 0。
        SDKLoadError: .so 加载失败。
    """
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
        raise SDKCallError("NewSlice", -1, "NewSlice 返回空指针")

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
            raise SDKCallError("GetChatData", ret, f"seq={seq} limit={limit}")

        # 从 slice 读 JSON
        content_ptr = lib.GetContentFromSlice(chat_datas)
        content_len = lib.GetSliceLen(chat_datas)
        if content_len <= 0 or not content_ptr:
            # 空内容（理论不应发生，errcode=0 时应有 JSON）
            return {"errcode": 0, "errmsg": "ok", "chatdata": []}

        raw_bytes = ctypes.string_at(content_ptr, content_len)
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SDKCallError(
                "GetChatData",
                -2,
                f"JSON 解析失败: {type(e).__name__}: {e}",
            ) from e
    finally:
        lib.FreeSlice(chat_datas)


# ----------------- 高层封装：DecryptData（保留，主流程不用） -----------------


def decrypt_data_raw(encrypt_key: str, encrypt_msg: str) -> str:
    """调用 SDK DecryptData 解密会话存档消息（同步阻塞）。

    注意：主流程仍用 chat_crypto.py 的 Python 实现，此函数保留供未来使用。

    Args:
        encrypt_key: 经 RSA 解密后的 random_key（base64 或原始字符串，按 SDK 要求传）。
        encrypt_msg: GetChatData 返回的 encrypt_chat_msg。

    Returns:
        解密后的明文 JSON 字符串。

    Raises:
        SDKCallError: DecryptData 返回非 0。
    """
    lib = _load_lib()
    msg_slice = lib.NewSlice()
    if not msg_slice:
        raise SDKCallError("NewSlice", -1, "NewSlice 返回空指针")

    try:
        ret = lib.DecryptData(
            encrypt_key.encode("utf-8"),
            encrypt_msg.encode("utf-8"),
            msg_slice,
        )
        if ret != 0:
            raise SDKCallError("DecryptData", ret)

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
    """调用 SDK GetMediaData 分片拉取媒体文件（同步阻塞）。

    注意：本期不实现媒体下载，此函数保留供未来使用。

    Args:
        corpid / secret: 同 get_chat_data_raw。
        sdk_file_id: 消息解密后的 sdkfileid。
        index_buf: 首次传空字符串，后续传上一次返回的 out_index_buf。
        proxy / passwd / timeout: 同 get_chat_data_raw。

    Returns:
        dict: {"data": bytes, "out_index_buf": str, "is_finish": bool}

    Raises:
        SDKCallError: GetMediaData 返回非 0。
    """
    lib = _load_lib()
    sdk_handle = _get_thread_sdk(corpid, secret)

    media_data = lib.NewMediaData()
    if not media_data:
        raise SDKCallError("NewMediaData", -1, "NewMediaData 返回空指针")

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
            raise SDKCallError("GetMediaData", ret, f"fileid={sdk_file_id}")

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


# ----------------- 测试辅助 -----------------


def _force_reload_for_test() -> None:
    """强制重新加载 .so（仅测试用：清掉单例缓存）。"""
    global _lib
    with _lib_lock:
        _lib = None
