"""Register an existing file in the shared download namespace."""

import re
from pathlib import Path
from typing import Any, Dict, Optional

# 上传/注册系统的落盘存储名形态（file_{12位hex}），如 file_fc9f09e93a9f.pdf
STORAGE_FILE_NAME_RE = re.compile(r"^file_[0-9a-f]{12}$")


def is_storage_file_name(name: str) -> bool:
    """判断文件名（或其去后缀 stem）是否为注册系统的存储名形态。"""
    if not name:
        return False
    return bool(STORAGE_FILE_NAME_RE.fullmatch(Path(name).stem))


def recover_original_display_name(source: str) -> Optional[str]:
    """源文件落盘名为存储名形态时，回查注册元数据中的原始显示名。

    上传接口（/api/upload 等）把文件存为 ``{file_id}{ext}``，原始文件名
    存在 Redis meta 的 ``name`` 字段；cp 等交付工具以此恢复用户可见文件名。

    Returns:
        原始显示名；源不是存储名形态、meta 不存在或 meta 名仍是存储名时返回 None。
    """
    stem = Path(source).stem
    if not STORAGE_FILE_NAME_RE.fullmatch(stem):
        return None
    from src.core.redis_client import redis_client

    try:
        meta = redis_client.hgetall(redis_client.make_key("uploaded_file", stem))
    except Exception:
        return None
    name = str((meta or {}).get("name") or "").strip()
    # meta 名仍是存储名（存量丢名副本）或无扩展名（如上传缺文件名时的
    # "unknown"，替换后反而无法识别文件类型）时无恢复价值
    if not name or is_storage_file_name(name) or not Path(name).suffix:
        return None
    return name


def register_download_metadata(
    file_path: Path,
    *,
    file_id: str,
    display_name: str,
    mime_type: str,
    visible: bool = True,
    identity: Optional[Dict[str, str]] = None,
    verify: bool = False,
) -> Dict[str, Any]:
    from src.core.redis_client import redis_client

    metadata = {
        "file_id": file_id,
        "name": display_name,
        "path": str(file_path.absolute()),
        "size": file_path.stat().st_size,
        "mime_type": mime_type,
        "type": "file",
        "visible": visible,
    }
    if identity:
        metadata.update(identity)
    key = redis_client.make_key("uploaded_file", file_id)
    for field, value in metadata.items():
        redis_client.hset(key, field, value)
    expires = redis_client.expire(key, 86400)
    # The wrapper intentionally swallows Redis write errors. Channel-created
    # attachments must verify registration before claiming a file is available.
    if verify and (not expires or redis_client.hgetall(key) != metadata):
        redis_client.delete(key)
        raise RuntimeError("DOWNLOAD_REGISTRATION_FAILED")
    return metadata
