"""企微会话存档 C SDK 的子进程代理层（主进程入口）。

⚠️ 设计要点：
主进程（gunicorn worker）已加载 cryptography / psycopg 等 C 扩展，
若直接加载 libWeWorkFinanceSdk_C.so 会破坏 C 堆状态，导致 worker 静默退出。

解决方案：把 ctypes + .so 加载整体放到 spawn 子进程内，
主进程通过 multiprocessing.Pool 调用，子进程结束 → C 堆污染销毁。

模块结构：
- 本模块（主进程入口）：保留异常类 + 公开 API，内部走子进程池
- `_sdk_inner`（子进程实现）：ctypes 加载 .so + 真正的 SDK 调用

对外 API 与改造前完全一致，上层（http_client / verifier / fetcher）零改动。
"""

from __future__ import annotations

import multiprocessing as mp
import threading
from typing import Any, Dict, Optional

from loguru import logger


# ----------------- 异常（与原 API 兼容） -----------------


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


# ----------------- 子进程池（懒初始化，进程级单例） -----------------

# 池配置：单进程串行足够（SDK 调用本身是阻塞的，多进程只增内存）
_POOL_SIZE = 1
_POOL_TIMEOUT = 60  # 单次调用超时秒数（SDK 内部网络调用可能慢）

_pool: Optional[mp.pool.Pool] = None
_pool_lock = threading.Lock()


def _get_pool() -> mp.pool.Pool:
    """获取或创建 spawn 子进程池（线程安全）。"""
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        # spawn：子进程不继承父进程已加载的 C 扩展状态
        ctx = mp.get_context("spawn")
        _pool = ctx.Pool(_POOL_SIZE)
        logger.info(f"[WeWorkFinanceSdk] 子进程隔离池已启动 size={_POOL_SIZE}")
        return _pool


def _reset_pool() -> None:
    """重建池（子进程死掉时调用）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.terminate()
                _pool.join()
            except Exception:
                pass
        ctx = mp.get_context("spawn")
        _pool = ctx.Pool(_POOL_SIZE)
        logger.warning("[WeWorkFinanceSdk] 子进程隔离池已重建")


# ----------------- 子进程入口函数（spawn 可 pickle，必须在模块顶层） -----------------


def _child_is_available() -> bool:
    """子进程入口：探测 SDK 是否可加载。"""
    from src.channels.wecom_personal_rpa.archive import _sdk_inner
    return _sdk_inner.is_sdk_available()


def _child_get_chat_data(
    corpid: str, secret: str, seq: int, limit: int,
    proxy: str, passwd: str, timeout: int,
) -> Dict[str, Any]:
    """子进程入口：调用 GetChatData。

    Returns:
        成功：{"ok": True, "data": <sdk json dict>}
        失败：{"ok": False, "error_type": "...", "error_msg": "..."}
        （用 dict 而非 raise，避免 multiprocessing 序列化异常时的局限）
    """
    from src.channels.wecom_personal_rpa.archive import _sdk_inner
    try:
        data = _sdk_inner.get_chat_data_raw(
            corpid, secret, seq, limit, proxy, passwd, timeout,
        )
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error_type": type(e).__name__, "error_msg": str(e)}


def _child_decrypt_data(encrypt_key, encrypt_msg: str) -> Dict[str, Any]:
    """子进程入口：调用 DecryptData。

    encrypt_key 可以是 str（UTF-8 合法）或 bytes（任意 32 字节随机数据）。
    """
    from src.channels.wecom_personal_rpa.archive import _sdk_inner
    try:
        data = _sdk_inner.decrypt_data_raw(encrypt_key, encrypt_msg)
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error_type": type(e).__name__, "error_msg": str(e)}


def _child_get_media_data(
    corpid: str, secret: str, sdk_file_id: str,
    index_buf: str, proxy: str, passwd: str, timeout: int,
) -> Dict[str, Any]:
    """子进程入口：调用 GetMediaData。"""
    from src.channels.wecom_personal_rpa.archive import _sdk_inner
    try:
        data = _sdk_inner.get_media_data_raw(
            corpid, secret, sdk_file_id, index_buf, proxy, passwd, timeout,
        )
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error_type": type(e).__name__, "error_msg": str(e)}


def _unwrap(result: Dict[str, Any], default_func: str) -> Any:
    """解子进程返回，失败时按错误类型重建异常。"""
    if result.get("ok"):
        return result["data"]

    err_type = result.get("error_type", "")
    err_msg = result.get("error_msg", "")

    # 子进程内的 RuntimeError 分两类：
    # - "加载失败 / .so 不存在" → SDKLoadError
    # - "GetChatData code=xxx / Init 失败 / 空指针" → SDKCallError
    msg_lower = err_msg.lower()
    if (
        "libweworkfinancesdk" in msg_lower
        or "加载失败" in err_msg
        or ".so 不存在" in err_msg
        or "cannot open shared object" in msg_lower
    ):
        raise SDKLoadError(err_msg)

    # 形如 "GetChatData code=10001 seq=0 limit=1"
    if "code=" in err_msg:
        try:
            code_part = err_msg.split("code=")[1].split()[0]
            code = int(code_part)
        except (IndexError, ValueError):
            code = -1
        raise SDKCallError(default_func, code, err_msg)

    # 子进程崩了 / 命中 BrokenPool → 触发池重建，下次重试有机会成功
    raise SDKCallError(default_func, -1, f"子进程异常: {err_type}: {err_msg}")


# ----------------- 公开 API（与原签名完全一致） -----------------


def is_sdk_available() -> bool:
    """探测 SDK 是否可加载（通过子进程，不污染主进程）。

    主进程首次调用此函数会触发子进程池启动，可能耗时 100~300ms。
    """
    try:
        pool = _get_pool()
        result = pool.apply(_child_is_available)
        return bool(result)
    except Exception as e:
        # 子进程崩了或池死了：尝试重建一次，再失败就返回 False
        logger.warning(f"[WeWorkFinanceSdk] is_sdk_available 子进程调用失败: {e}，尝试重建池")
        try:
            _reset_pool()
            pool = _get_pool()
            return bool(pool.apply(_child_is_available))
        except Exception as e2:
            logger.error(f"[WeWorkFinanceSdk] is_sdk_available 重建后仍失败: {e2}")
            return False


def get_chat_data_raw(
    corpid: str,
    secret: str,
    seq: int,
    limit: int,
    proxy: str = "",
    passwd: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """调用 SDK GetChatData 拉取一批会话存档密文（通过子进程）。

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

    try:
        pool = _get_pool()
        result = pool.apply(
            _child_get_chat_data,
            (corpid, secret, int(seq), int(limit), proxy, passwd, timeout),
        )
    except Exception as e:
        # pool.apply 抛异常 = 子进程崩了（BrokenPoolError / 子进程异常退出）
        # SDK 业务错误（code != 0）走 _unwrap 路径，不会进这里
        logger.warning(f"[WeWorkFinanceSdk] 子进程池崩了，重建: {e}")
        _reset_pool()
        pool = _get_pool()
        result = pool.apply(
            _child_get_chat_data,
            (corpid, secret, int(seq), int(limit), proxy, passwd, timeout),
        )

    return _unwrap(result, "GetChatData")


def decrypt_data_raw(encrypt_key, encrypt_msg: str) -> str:
    """调用 SDK DecryptData 解密会话存档消息（通过子进程）。

    Args:
        encrypt_key: RSA 解密 encrypt_random_key 后得到的会话密钥。
            兼容 ``str``（UTF-8 合法）和 ``bytes``（任意 32 字节随机数据），
            SDK 内部按字节流处理。
        encrypt_msg: GetChatData 返回的 encrypt_chat_msg（base64 字符串）。

    Returns:
        解密后的明文 JSON 字符串。

    Raises:
        SDKCallError: DecryptData 返回非 0。
    """
    try:
        pool = _get_pool()
        result = pool.apply(_child_decrypt_data, (encrypt_key, encrypt_msg))
    except Exception as e:
        logger.warning(f"[WeWorkFinanceSdk] 子进程池崩了，重建: {e}")
        _reset_pool()
        pool = _get_pool()
        result = pool.apply(_child_decrypt_data, (encrypt_key, encrypt_msg))

    return _unwrap(result, "DecryptData")


def get_media_data_raw(
    corpid: str,
    secret: str,
    sdk_file_id: str,
    index_buf: str = "",
    proxy: str = "",
    passwd: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """调用 SDK GetMediaData 分片拉取媒体文件（通过子进程）。

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
    try:
        pool = _get_pool()
        result = pool.apply(
            _child_get_media_data,
            (corpid, secret, sdk_file_id, index_buf, proxy, passwd, timeout),
        )
    except Exception as e:
        logger.warning(f"[WeWorkFinanceSdk] 子进程池崩了，重建: {e}")
        _reset_pool()
        pool = _get_pool()
        result = pool.apply(
            _child_get_media_data,
            (corpid, secret, sdk_file_id, index_buf, proxy, passwd, timeout),
        )

    return _unwrap(result, "GetMediaData")


# ----------------- 测试辅助 -----------------


def _force_reload_for_test() -> None:
    """强制重建子进程池（仅测试用）。"""
    _reset_pool()
