"""
临时主题日志

用于调试时把某一类日志单独写到独立文件，避免被主日志淹没。

用法:
    from src.core.temp_logger import tlog
    tlog("语音合并", f"合并 {a} + {b} -> {c}")
    tlog("语音合并", "处理失败", level="ERROR")

日志文件位于 log/temp/{topic}.log，与主日志完全隔离。
bug 修复后删除 tlog 调用即可；整个 log/temp/ 目录可随时清空。
"""

import re
import threading
from datetime import datetime
from pathlib import Path

_TEMP_LOG_DIR = Path("log/temp")

# 文件名非法字符（Windows / Linux 通用）
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')

_lock = threading.Lock()
_file_locks: dict[str, threading.Lock] = {}


def _get_file_lock(topic: str) -> threading.Lock:
    """每个主题一个锁，避免多线程写同一文件时交错。"""
    with _lock:
        if topic not in _file_locks:
            _file_locks[topic] = threading.Lock()
        return _file_locks[topic]


def _sanitize_topic(topic: str) -> str:
    """清理文件名非法字符，并去掉首尾空白。"""
    cleaned = _INVALID_CHARS.sub("_", topic).strip()
    return cleaned or "default"


def tlog(topic: str, message, *args, level: str = "INFO", **kwargs) -> None:
    """
    写入一条临时主题日志。

    Args:
        topic: 日志主题，同时作为文件名（中英文均可，非法字符会被替换为 _）
        message: 日志内容，支持 str.format 风格占位符（如 "合并 {a} + {b}"）
        level: 日志级别字符串，默认 INFO
        *args, **kwargs: 用于填充 message 占位符
    """
    try:
        _TEMP_LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe_topic = _sanitize_topic(topic)

        msg = str(message)
        if args or kwargs:
            try:
                msg = msg.format(*args, **kwargs)
            except Exception:
                # 占位符不匹配时退化为原消息，避免日志调用本身抛错
                pass

        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} | {level:<8} | {msg}\n"
        log_file = _TEMP_LOG_DIR / f"{safe_topic}.log"
        with _get_file_lock(safe_topic):
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # 临时日志失败不影响主流程
        pass
