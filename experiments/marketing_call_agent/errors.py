"""CLI 和图像模块共用的脱敏错误类型。"""

class ProbeError(Exception):
    def __init__(self, reason, status="BLOCKED"):
        super().__init__(reason)
        self.reason = reason
        self.status = status
