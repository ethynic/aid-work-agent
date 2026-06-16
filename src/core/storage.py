"""
租户附件存储路径工具

按 `.claude/rules/backend_dev.md`「租户附件存储规范」统一管理
`storage/tenants/{tenant_id}/` 下的子目录路径。

目录结构：
    storage/
    └── tenants/
        ├── {tenant_id_a}/
        │   ├── conversation/    # 对话中产生的附件
        │   ├── knowledge/       # 知识库附件
        │   ├── export/          # 业务导出文件
        │   ├── report/          # 统计报表文件
        │   ├── avatar/          # 头像 / Logo
        │   └── temp/            # 临时文件
        └── {tenant_id_b}/
            └── ...
"""

import os


# 仓库根目录下的统一存储根
_STORAGE_ROOT = "storage"
_TENANTS_ROOT = os.path.join(_STORAGE_ROOT, "tenants")


def get_tenant_storage_dir(tenant_id: str, scene: str) -> str:
    """获取租户某场景的目录路径（相对路径，不保证存在）。

    Args:
        tenant_id: 租户 ID
        scene: 业务场景子目录名（conversation / knowledge / export / ...）

    Returns:
        `storage/tenants/{tenant_id}/{scene}`
    """
    return os.path.join(_TENANTS_ROOT, tenant_id, scene)


def ensure_tenant_storage_dir(tenant_id: str, scene: str) -> str:
    """确保租户某场景的目录存在，并返回其路径。

    目录不存在时会自动创建（`exist_ok=True`）。

    Args:
        tenant_id: 租户 ID
        scene: 业务场景子目录名

    Returns:
        `storage/tenants/{tenant_id}/{scene}`（绝对路径或相对路径，取决于 cwd）
    """
    if not tenant_id:
        raise ValueError("tenant_id 不能为空")

    dir_path = get_tenant_storage_dir(tenant_id, scene)
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


def get_tenant_storage_path(tenant_id: str, scene: str, filename: str) -> str:
    """获取租户某场景下某文件的完整路径（相对路径，不创建目录）。

    Args:
        tenant_id: 租户 ID
        scene: 业务场景子目录名
        filename: 文件名

    Returns:
        `storage/tenants/{tenant_id}/{scene}/{filename}`
    """
    return os.path.join(get_tenant_storage_dir(tenant_id, scene), filename)


def get_tenant_storage_abs_path(tenant_id: str, scene: str, filename: str) -> str:
    """获取租户某场景下某文件的绝对路径（不创建目录）。

    业务代码统一通过此函数获取写入路径，路径始终以仓库 cwd 为基准。
    """
    rel = get_tenant_storage_path(tenant_id, scene, filename)
    return os.path.abspath(rel)
