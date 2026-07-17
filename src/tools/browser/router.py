"""Phase 2 浏览器路由：只实现 server/local。"""

from .executor.base import BrowserExecutor
from .executor.local import LocalPlaywrightExecutor


class BrowserRouter:
    def create_executor(self, execution_target: str) -> BrowserExecutor:
        if execution_target != "server":
            raise RuntimeError("REMOTE_EXECUTOR_UNAVAILABLE")
        return LocalPlaywrightExecutor()

    @property
    def remote_available(self) -> bool:
        return False
