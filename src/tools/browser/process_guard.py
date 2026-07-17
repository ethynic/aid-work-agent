"""浏览器进程所有权守卫。

Phase 1 的内联 Playwright 运行方式没有公开、稳定的进程句柄。守卫因此只
接受调用方显式提供的 ``PID + create_time``，绝不通过进程名或模糊父子关系
猜测所有权。当前优雅关闭失败时，如果没有可信身份，安全地报告
``ownership_unavailable``，而不是误杀同机其他浏览器。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class OwnedProcessIdentity:
    """不可受 PID 复用影响的进程身份。"""

    pid: int
    create_time: float


class BrowserProcessGuard:
    """只管理当前 browser run 明确拥有的进程。"""

    def __init__(self) -> None:
        self._owned: set[OwnedProcessIdentity] = set()

    @property
    def owned(self) -> tuple[OwnedProcessIdentity, ...]:
        return tuple(self._owned)

    def register_owned_process(
        self, pid: Optional[int], create_time: Optional[float]
    ) -> bool:
        """登记可信进程身份；缺任一字段时保守拒绝。"""
        if not isinstance(pid, int) or pid <= 0:
            return False
        if not isinstance(create_time, (int, float)) or create_time <= 0:
            return False
        self._owned.add(OwnedProcessIdentity(pid=pid, create_time=float(create_time)))
        return True

    def track_inline_playwright(self, playwright: Any) -> bool:
        """内联 Playwright 暂无稳定公开 PID API，不读取私有属性猜测身份。"""
        del playwright
        return False

    async def cleanup_owned(self) -> Dict[str, Any]:
        """返回保守清理报告。

        Phase 2 的隔离 worker 才能提供可靠的进程组句柄。Phase 1 不引入
        ``psutil`` 生产依赖，也不做进程名批量清理。
        """
        return {
            "tracked_count": len(self._owned),
            "terminated_count": 0,
            "status": "ownership_unavailable" if not self._owned else "graceful_only",
        }
