"""会话历史文件按需检出服务（清单 + 按需检出）。

背景：历史对话中的文件（含用户上传与助手生成交付物）统一以轻量元数据清单注入上下文，
不再每轮全量复制到临时工作区；模型仅在需要读取、转换或再处理时按 file_id 触发按需检出。

安全与存储约束（fail-closed）：
1. file_id 必须归属当前 session_id（查 channel_messages / chat_messages 有效窗口内记录）；
2. 物理路径解析优先 file_assets DB -> Redis -> 租户存储域扫描，全程经
   resolve_scoped_uploaded_file 校验落在当前租户存储根内；
3. 检出落盘位于当前执行工作区 session_files/{file_id}/ 下，隔离拷贝（不共用硬链接），
   随执行结束自动清理，防止技能原地编辑篡改租户持久存储原件。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from src.core.storage import configured_storage_root, resolve_scoped_uploaded_file

_SAFE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_UNSAFE_NAME_CHARS = re.compile(r"[\\/\x00-\x1f<>:\"|?*]")


def _sanitize_checkout_name(name: str, fallback_stem: str, fallback_suffix: str) -> str:
    """清洗检出文件名：保留可读名与扩展名，杜绝路径分隔符与控制字符。"""
    raw = _UNSAFE_NAME_CHARS.sub("_", str(name or "")).strip("._ ")
    if not raw:
        raw = fallback_stem
    # 防止过长文件名触发 OS NAME_MAX（255 字节）
    stem, ext = os.path.splitext(raw)
    if not ext and fallback_suffix:
        ext = fallback_suffix
    stem = (stem or fallback_stem)[:120].strip("._ ") or fallback_stem
    return f"{stem}{ext}"


def resolve_tenant_file_record(
    file_id: str,
    tenant_id: Optional[str],
    *,
    storage_root: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """在租户边界内按 file_id 解析持久文件记录。

    查找顺序：file_assets DB（持久，不受 Redis 24h TTL 影响）
    -> Redis uploaded_file:{file_id} -> 租户目录受限扫描。
    每条候选路径均经 resolve_scoped_uploaded_file 校验归属当前租户根。
    """
    if not file_id or not _SAFE_FILE_ID_RE.match(file_id) or file_id in (".", ".."):
        return None

    root = storage_root if storage_root is not None else configured_storage_root()

    # 1. 优先查 DB file_assets（持久化元数据，无 24h TTL 过期问题）
    db_meta: Optional[Dict[str, Any]] = None
    try:
        from src.services.files.asset_store import get_file_asset
        db_meta = get_file_asset(file_id)
    except Exception:
        logger.opt(exception=True).warning(
            "[session_file_checkout] 读取 file_assets 失败，降级后续路径 file_id={}", file_id
        )
    if db_meta:
        resolved = resolve_scoped_uploaded_file(
            file_id, tenant_id, storage_root=root, metadata=db_meta
        )
        if resolved and resolved.get("path"):
            return resolved

    # 2. 查 Redis uploaded_file:{file_id}
    redis_meta: Optional[Dict[str, Any]] = None
    try:
        from src.core.redis_client import redis_client
        redis_meta = redis_client.hgetall(redis_client.make_key("uploaded_file", file_id)) or None
    except Exception:
        logger.debug("[session_file_checkout] Redis 元数据读取失败，降级磁盘扫描 file_id={}", file_id)
    if redis_meta:
        resolved = resolve_scoped_uploaded_file(
            file_id, tenant_id, storage_root=root, metadata=redis_meta
        )
        if resolved and resolved.get("path"):
            return resolved

    # 3. 租户受限目录扫描兜底
    resolved = resolve_scoped_uploaded_file(file_id, tenant_id, storage_root=root)
    if resolved and resolved.get("path"):
        return resolved
    return None


def verify_session_file_ownership(
    file_id: str,
    *,
    tenant_id: Optional[str],
    session_id: Optional[str],
    session_kind: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """校验 file_id 归属当前会话（含用户上传与助手生成附件）。"""
    if not file_id or not session_id:
        return None
    from src.core.agent_engine.contracts import Identity
    from src.services.agent_runner.runtime.history_repository import HistoryRepository

    kind = session_kind
    if kind not in ("web", "channel"):
        try:
            kind = HistoryRepository.legacy_session_kind(session_id, tenant_id)
        except Exception:
            logger.opt(exception=True).warning(
                "[session_file_checkout] 推断 session_kind 失败 session_id={}", session_id
            )
            return None

    identity = Identity(
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        source="chat" if kind == "web" else "channel",
        session_kind=kind,
    )
    repo = HistoryRepository(identity)
    return repo.find_session_file(session_id, file_id)


def _copy_isolated(src: Path, dst: Path) -> None:
    """隔离拷贝到执行工作区：不使用硬链接，防止技能原地编辑篡改租户持久原件。"""
    if dst.exists():
        return
    shutil.copyfile(src, dst)


def checkout_session_file(
    file_id: str,
    *,
    workspace_dir: Path | str,
    tenant_id: Optional[str],
    session_id: Optional[str],
    session_kind: Optional[str] = None,
    user_id: Optional[str] = None,
    storage_root: Optional[Any] = None,
) -> Dict[str, Any]:
    """按 file_id 将当前会话的历史文件按需检出到执行工作区。"""
    fid = str(file_id or "").strip()
    if not fid or not _SAFE_FILE_ID_RE.match(fid) or fid in (".", ".."):
        return {"success": False, "error": "file_id 格式无效"}
    if not session_id:
        return {"success": False, "error": "当前执行缺少会话上下文，无法检出会话文件"}

    owned = verify_session_file_ownership(
        fid,
        tenant_id=tenant_id,
        session_id=session_id,
        session_kind=session_kind,
        user_id=user_id,
    )
    if not owned:
        return {
            "success": False,
            "error": f"文件 {fid} 不属于当前会话或已超出有效历史范围，请核对会话文件清单中的 file_id。",
        }

    resolved = resolve_tenant_file_record(fid, tenant_id, storage_root=storage_root)
    if not resolved or not resolved.get("path"):
        return {
            "success": False,
            "error": f"文件 {fid}（{owned.get('name') or fid}）底层文件已失效或被清理，请让用户重新上传。",
        }

    src = Path(resolved["path"])
    if not src.is_file():
        return {
            "success": False,
            "error": f"文件 {fid}（{owned.get('name') or fid}）底层文件不存在，请让用户重新上传。",
        }

    # 按 file_id 子目录隔离：同名不同 file_id 天然不冲突，同一 file_id 重复检出幂等复用。
    checkout_dir = Path(workspace_dir) / "session_files" / fid
    checkout_dir.mkdir(parents=True, exist_ok=True)
    display_name = str(owned.get("name") or resolved.get("name") or src.name)
    safe_name = _sanitize_checkout_name(display_name, fid, src.suffix)
    target = checkout_dir / safe_name

    _copy_isolated(src, target)
    size = int(resolved.get("size") or target.stat().st_size)
    mime_type = str(owned.get("mime_type") or resolved.get("mime_type") or "application/octet-stream")
    logger.info(
        "[session_file_checkout] 按需检出成功 file_id={} name={} -> {} ({} bytes)",
        fid, display_name, target, size,
    )
    return {
        "success": True,
        "file_id": fid,
        "file_name": display_name,
        "file_path": str(target),
        "mime_type": mime_type,
        "size": size,
        "origin": owned.get("origin") or "user_upload",
    }
