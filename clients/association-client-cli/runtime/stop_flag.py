"""
协作式停止标志 —— Electron 主进程与 CLI 之间的「停止」信号。

机制：Electron 每次 spawn CLI 时生成一个唯一的停止文件路径，通过环境变量
ASSOCIATION_STOP_FILE 传入；用户点击「停止」时主进程写入该文件，CLI 各检查点
（enrich_many 每协会开始前、PowerShell 子进程等待轮询）检测到文件存在即优雅收尾：
保存已完成的部分结果、发 stopped 事件、以退出码 2 退出。

环境变量未设置时本模块所有函数均为 no-op（is_set() 恒 False、check() 不抛异常），
保证共用同一代码路径的其他 CLI / UI 入口行为零变化。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENV_VAR = "ASSOCIATION_STOP_FILE"


class UserStoppedError(BaseException):
    """用户点击「停止」的协作式中断信号。

    继承 BaseException（与 asyncio.CancelledError 同理）：批量编排与 providers 里
    大量 `except Exception` 用于吞单协会级故障继续下一个，停止信号必须穿透这些
    兜底直接到达 enrich_many 的停止收尾分支，不能被当成普通失败吞掉。
    """


def _stop_path() -> Optional[Path]:
    raw = os.environ.get(ENV_VAR, "").strip()
    return Path(raw) if raw else None


def configured() -> bool:
    """是否配置了停止文件（未配置时调用方走原有非轮询路径，行为零变化）。"""
    return _stop_path() is not None


def is_set() -> bool:
    """停止文件是否已存在（用户已点击停止）。"""
    path = _stop_path()
    if path is None:
        return False
    try:
        return path.exists()
    except OSError:
        return False


def check() -> None:
    """停止文件存在则抛 UserStoppedError。"""
    if is_set():
        raise UserStoppedError("USER_STOPPED")
