"""Register an existing file in the shared download namespace."""

from pathlib import Path
from typing import Any, Dict, Optional


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
