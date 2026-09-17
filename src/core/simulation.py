"""仿真环境（staging）副作用防护门控

仿真环境（SIMULATION_MODE=1）与生产同机共享附件目录，本模块为
"可能污染生产数据的动作"提供统一防护判定：

- ``skip_disk_delete``: 租户附件目录内的磁盘删除一律跳过（仿真库记录照常删）

生产环境（未设置 SIMULATION_MODE 或为 0）恒返回 False，不改变任何现有行为。
"""

import os

from loguru import logger

# 租户附件根目录（容器内 /app 工作目录相对路径；旧轨 uploads 整目录共享）
_TENANT_ATTACHMENT_ROOTS = (
    os.path.abspath(os.path.join("storage", "tenants")),
    os.path.abspath("uploads"),
)


def is_simulation_mode() -> bool:
    """是否仿真环境（env SIMULATION_MODE=1/true）"""
    try:
        from src.config.settings import settings

        return bool(getattr(settings.app, "simulation_mode", False))
    except Exception:
        return False


def _is_tenant_attachment_path(path) -> bool:
    """判断路径是否位于租户附件目录（新轨 storage/tenants 或旧轨 uploads）"""
    p = os.path.abspath(str(path))
    return any(p == root or p.startswith(root + os.sep) for root in _TENANT_ATTACHMENT_ROOTS)


def skip_disk_delete(path, context: str = "") -> bool:
    """仿真环境 + 租户附件路径时返回 True，调用方应跳过磁盘删除（DB 记录删除不受影响）

    临时文件（tmp、工作区、日志等非租户附件路径）不拦截，避免磁盘泄漏。
    """
    if not is_simulation_mode():
        return False
    if not _is_tenant_attachment_path(path):
        return False
    logger.warning(f"仿真环境门控：跳过磁盘删除 context={context} path={path}")
    return True
