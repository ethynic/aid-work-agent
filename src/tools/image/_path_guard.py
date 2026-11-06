"""图片工具本地路径安全沙箱（image_read / image_render 共用）。

本地绝对路径分支只允许三类来源（对齐 read_tool 项目根+tmp 沙箱先例，
并叠加租户目录约束防跨租户读取）：
- 当前租户存储目录 storage/tenants/{tenant_id}/
- 项目根目录内、租户存储区之外（会话工作区、技能脚本产物）
- 系统临时目录（attachments 落盘、tool_generated 临时图）
其余路径（/etc/passwd、/proc、其他租户目录等）一律拒绝。
"""
import tempfile
from pathlib import Path


def _within(resolved: Path, base: Path) -> bool:
    try:
        resolved.relative_to(base)
        return True
    except ValueError:
        return False


def guard_local_image_path(path: Path, tenant_id: str) -> Path:
    """校验本地图片路径在允许范围内，返回 resolve 后的路径；越界抛 ValueError。"""
    resolved = path.resolve()
    project_root = Path(__file__).resolve().parents[3]
    tmp_dir = Path(tempfile.gettempdir()).resolve()
    tenants_root = (project_root / "storage" / "tenants").resolve()
    tenant_dir = tenants_root / tenant_id

    if _within(resolved, tenant_dir) or _within(resolved, tmp_dir):
        return resolved
    if not _within(resolved, project_root):
        raise ValueError(
            f"图片路径超出允许范围（须在租户存储目录、项目根或临时目录内）: {path}"
        )
    # 项目根内的租户存储区：只放行当前租户目录，其他租户目录一律拒绝
    if _within(resolved, tenants_root):
        raise ValueError(f"图片路径属于其他租户，禁止读取: {path}")
    return resolved
