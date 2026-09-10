"""微信营销图片素材服务（R57 P4-A：上传/列表/详情/删除/引用保护/过期清理）

微信计划 §6.2 契约：
- 上传：multipart 字节 → **PIL 头字节实测 MIME**（客户端 Content-Type 不作为依据，
  伪 mime 以实测为准）+ verify() 完整性校验（损坏拒绝）+ 大小/像素上限（可配
  asset_max_bytes/asset_max_pixels）+ sha256；落租户存储目录
  ``storage/tenants/{tid}/weixin-marketing/``（get_tenant_storage_abs_path 绝对
  路径登记，不接受任意服务器路径）；assets 行 ACL = 租户 + 属主（跨租户/非属主统一 404）。
- 引用保护：被**非过期** revision（draft/published）的内容块引用即禁删
  （409 ASSET_IN_USE）；superseded（已被新版替换）视为过期，不构成保护——
  重新编辑/发布解引用后可删。硬删文件 + 行。
- 过期清理：retention_until（上传时 = now + retention_days）已过且无 draft/published
  引用的素材，由后台清理任务批量硬删（dispatch.assets_cleanup_tick，enabled 门控）。
- Runtime 下载：serve_invocation_asset 供 local_tools 资产端点调用——设备 token
  鉴权后的 invocation 归属/未终态/场景绑定/revision 资产集校验 + 文件字节 sha256
  复核；无重定向、无任意 URL。

错误类型复用 service 层（NotFoundError/WeixinValidationError/AssetInUseError），
API 层沿既有 envelope/稳定码映射。本模块不 import 适配器/service 之外的执行链。
"""

import hashlib
import io
import os
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from loguru import logger

from src.core.storage import get_tenant_storage_abs_path
from src.db.database import get_db_connection
from src.weixin_marketing.config import WeixinMarketingConfig, get_weixin_marketing_config
from src.weixin_marketing.constants import (
    ACTOR_TYPE_SYSTEM,
    ASSET_EXTENSION_BY_MIME,
    ASSET_MIME_BY_PIL_FORMAT,
    ASSET_STATUS_ACTIVE,
    ASSET_STORAGE_SCENE,
    AUDIT_ASSET_CLEANED,
    AUDIT_ASSET_DELETED,
    AUDIT_ASSET_UPLOADED,
    BLOCK_KIND_IMAGE,
    DEFAULT_ASSETS_CLEANUP_BATCH,
    SCENARIO_KEY,
    TEST_TASK_REF_SUFFIX,
)
from src.weixin_marketing.service import (
    AssetInUseError,
    NotFoundError,
    WeixinMarketingError,
    WeixinValidationError,
    _insert_weixin_audit_on,
)

_ASSET_COLUMNS = (
    "id, tenant_id, user_id, storage_ref, sha256, mime, size, width, height, "
    "status, retention_until, created_at"
)


class AssetEndpointError(Exception):
    """Runtime 资产端点拒绝（code 对应 HTTP status；context 供审计补充）——
    与 local_tools.api.PayloadEndpointError 同构"""

    def __init__(self, code: str, http_status: int, message: str, context: Optional[Dict] = None):
        self.code = code
        self.http_status = http_status
        self.message = message
        self.context = context or {}
        super().__init__(f"{code}: {message}")


class AssetContentResolution(NamedTuple):
    """素材受控字节解析结果：(字节, mime, sha256 hex)"""

    data: bytes
    mime: str
    sha256: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _resolve_config(config: Optional[WeixinMarketingConfig]) -> WeixinMarketingConfig:
    return config if config is not None else get_weixin_marketing_config()


def _to_uuid_text(value) -> Optional[str]:
    """参数侧 UUID 规整（非法形态返回 None，按不存在处理，照 adapters._to_uuid_text）"""
    try:
        return str(_uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


# ==================== MIME 实测（PIL 头字节嗅探 + 完整性校验）====================


def sniff_image(content: bytes) -> Tuple[str, int, int]:
    """字节 → (规范 MIME, width, height)。

    - 实测优先：PIL 解析实际格式，白名单外格式拒绝（TIFF/ICO/SVG 等不支持）；
    - verify() 校验数据完整性（截断/损坏拒绝）；
    - 客户端声明的 Content-Type 完全不参与判定（伪 mime 防线）。
    非图片/损坏/不支持格式 → WeixinValidationError。
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as img:
            pil_format = (img.format or "").upper()
            if pil_format not in ASSET_MIME_BY_PIL_FORMAT:
                raise WeixinValidationError(
                    f"不支持的图片格式: {pil_format or '未知'}"
                    f"（仅支持 {'/'.join(sorted(set(ASSET_MIME_BY_PIL_FORMAT.values())))}）"
                )
            width, height = img.size
            img.verify()  # 截断/损坏数据在此抛错
    except WeixinValidationError:
        raise
    except Exception as e:  # noqa: BLE001 UnidentifiedImageError/截断/解压炸弹统一 422
        raise WeixinValidationError(
            f"图片数据损坏或无法解码，已拒绝（{type(e).__name__}）"
        ) from e
    if width <= 0 or height <= 0:
        raise WeixinValidationError("图片尺寸非法")
    return ASSET_MIME_BY_PIL_FORMAT[pil_format], int(width), int(height)


# ==================== 上传 ====================


def upload_asset(
    tenant_id: str,
    user_id: str,
    filename: str,
    content: bytes,
    *,
    declared_mime: Optional[str] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """上传素材（MIME 实测/上限/hash/落盘/行 ACL=租户+属主；返回元数据 dict）。

    declared_mime 仅用于日志比对（实测为准，不参与判定）。
    """
    cfg = _resolve_config(config)
    if not cfg.images_enabled:
        raise WeixinValidationError("图片内容未启用（images_enabled=false），不允许上传素材")
    if not content:
        raise WeixinValidationError("上传内容为空")
    if len(content) > cfg.asset_max_bytes:
        raise WeixinValidationError(
            f"图片大小 {len(content)} 字节超过上限 {cfg.asset_max_bytes} 字节"
        )
    mime, width, height = sniff_image(content)
    if width * height > cfg.asset_max_pixels:
        raise WeixinValidationError(
            f"图片像素 {width}x{height}（{width * height}）超过上限 {cfg.asset_max_pixels}"
        )
    sha256 = hashlib.sha256(content).hexdigest()
    asset_id = str(_uuid.uuid4())
    extension = ASSET_EXTENSION_BY_MIME[mime]
    # 全库约定（backend_dev.md）：文件写入用绝对路径（get_tenant_storage_abs_path，
    # 以仓库 cwd 为基准锚定），storage_ref 直接登记该绝对路径——读回不再受进程
    # cwd 漂移影响；无线上存量数据，不做相对路径兼容
    storage_ref = get_tenant_storage_abs_path(
        tenant_id, ASSET_STORAGE_SCENE, f"{asset_id}{extension}"
    )
    os.makedirs(os.path.dirname(storage_ref), exist_ok=True)
    with open(storage_ref, "xb") as fh:  # 独占创建：同 id 重名不可能（uuid4）
        fh.write(content)
    retention_until = _utcnow() + timedelta(days=cfg.retention_days)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO bs_weixin_marketing_assets
                    (id, tenant_id, user_id, storage_ref, sha256, mime, size,
                     width, height, status, retention_until)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    asset_id, tenant_id, user_id, storage_ref, sha256, mime, len(content),
                    width, height, ASSET_STATUS_ACTIVE, retention_until,
                ),
            )
            _insert_weixin_audit_on(
                cursor, tenant_id, AUDIT_ASSET_UPLOADED, user_id=user_id,
                details={
                    "asset_id": asset_id, "mime": mime, "size": len(content),
                    "width": width, "height": height, "sha256": sha256,
                },
            )
            conn.commit()
        except Exception:
            conn.rollback()
            # 行落库失败：回收已写文件（不留无主磁盘垃圾）
            try:
                os.remove(storage_ref)
            except OSError:
                logger.warning(f"后端日志：素材行落库失败后文件回收失败 ref={storage_ref}")
            raise
    if declared_mime and declared_mime != mime:
        logger.info(
            f"后端日志：素材上传声明 MIME 与实测不一致（以实测为准）"
            f"declared={declared_mime} actual={mime} tenant={tenant_id}"
        )
    logger.info(
        f"后端日志：weixin_marketing 素材上传 tenant={tenant_id} asset={asset_id} "
        f"mime={mime} size={len(content)} {width}x{height}"
    )
    return {
        "id": asset_id,
        "mime": mime,
        "size": len(content),
        "width": width,
        "height": height,
        "sha256": sha256,
        "status": ASSET_STATUS_ACTIVE,
        "retention_until": retention_until.isoformat(),
        "reference_count": 0,
    }


# ==================== 查询（ACL：租户 + 属主；跨租户/非属主统一 404）====================


def _reference_count_on(cursor, tenant_id: str, asset_id: str) -> int:
    """非过期 revision（draft/published）对素材的引用数（引用保护口径）"""
    cursor.execute(
        """
        SELECT COUNT(*) AS c
        FROM bs_weixin_marketing_content_blocks cb
        JOIN bs_weixin_marketing_revisions r
          ON r.tenant_id = cb.tenant_id AND r.id = cb.revision_id
        WHERE cb.tenant_id = %s AND cb.asset_id = %s
          AND r.status IN ('draft', 'published')
        """,
        (tenant_id, asset_id),
    )
    return int(cursor.fetchone()["c"])


def _load_asset_on(
    cursor, tenant_id: str, user_id: str, asset_id: str, *, for_update: bool = False
) -> Dict[str, Any]:
    """加载素材行（ACL：租户+属主；跨租户/非属主统一 404）。

    for_update=True 时行级排它锁（P1-2 锁协议：素材行为「删除 vs 草稿/发布引用
    校验」的共同锁根——删除事务开头即持锁，与 assert_blocks_assets_on 的
    FOR SHARE 互斥，闭合「查零引用→并发保存引用→删除→草稿悬空」竞态）。
    """
    key = _to_uuid_text(asset_id)
    if key is None:
        raise NotFoundError("素材不存在")
    cursor.execute(
        f"SELECT {_ASSET_COLUMNS} FROM bs_weixin_marketing_assets "
        "WHERE tenant_id = %s AND id = %s AND user_id = %s"
        + (" FOR UPDATE" if for_update else ""),
        (tenant_id, key, user_id),
    )
    row = cursor.fetchone()
    if row is None:
        # 跨租户/非属主/不存在统一 404（不泄露存在性）
        raise NotFoundError("素材不存在")
    return dict(row)


def list_assets(
    tenant_id: str,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 24,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """属主素材列表（分页；附引用计数与 images_enabled 开关供前端门禁展示）"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS c FROM bs_weixin_marketing_assets "
            "WHERE tenant_id = %s AND user_id = %s AND status = %s",
            (tenant_id, user_id, ASSET_STATUS_ACTIVE),
        )
        total = int(cursor.fetchone()["c"])
        cursor.execute(
            f"""
            SELECT a.id, a.mime, a.size, a.width, a.height, a.sha256, a.status,
                   a.retention_until, a.created_at,
                   (
                       SELECT COUNT(*) FROM bs_weixin_marketing_content_blocks cb
                       JOIN bs_weixin_marketing_revisions r
                         ON r.tenant_id = cb.tenant_id AND r.id = cb.revision_id
                       WHERE cb.tenant_id = a.tenant_id AND cb.asset_id = a.id
                         AND r.status IN ('draft', 'published')
                   ) AS reference_count
            FROM bs_weixin_marketing_assets a
            WHERE a.tenant_id = %s AND a.user_id = %s AND a.status = %s
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, user_id, ASSET_STATUS_ACTIVE, page_size, (page - 1) * page_size),
        )
        items = [dict(r) for r in cursor.fetchall()]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "images_enabled": _resolve_config(config).images_enabled,
    }


def get_asset(tenant_id: str, user_id: str, asset_id: str) -> Dict[str, Any]:
    """素材详情元数据（属主）"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        asset = _load_asset_on(cursor, tenant_id, user_id, asset_id)
        asset["reference_count"] = _reference_count_on(cursor, tenant_id, str(asset["id"]))
    return asset


def read_asset_content(tenant_id: str, user_id: str, asset_id: str) -> AssetContentResolution:
    """受控读取素材字节（属主预览用）：读文件 + sha256 与行登记值复核，不符即 500。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        asset = _load_asset_on(cursor, tenant_id, user_id, asset_id)
    resolution = _read_asset_bytes(asset)
    return resolution


def _read_asset_bytes(asset: Dict[str, Any]) -> AssetContentResolution:
    """行 → 文件字节（hash 复核；行在文件失联/漂移时抛 WeixinMarketingError→500）"""
    storage_ref = asset.get("storage_ref") or ""
    try:
        with open(storage_ref, "rb") as fh:
            data = fh.read()
    except OSError as e:
        logger.error(
            f"后端日志：素材文件读取失败 tenant={asset.get('tenant_id')} "
            f"asset={asset.get('id')} ref={storage_ref}: {e}"
        )
        raise WeixinMarketingError("素材文件不可用") from e
    actual = hashlib.sha256(data).hexdigest()
    expected = asset.get("sha256")
    if expected and actual != expected:
        logger.error(
            f"后端日志：素材文件 hash 与登记不一致 tenant={asset.get('tenant_id')} "
            f"asset={asset.get('id')} expected={expected} actual={actual}"
        )
        raise WeixinMarketingError("素材文件校验失败")
    return AssetContentResolution(data=data, mime=str(asset.get("mime") or "application/octet-stream"), sha256=actual)


# ==================== 删除（引用保护 409；硬删文件+行）====================


def delete_asset(tenant_id: str, user_id: str, asset_id: str) -> Dict[str, Any]:
    """删除素材：非过期 revision（draft/published）引用即 409 ASSET_IN_USE；
    通过后行删除与审计同事务提交，文件随后尽力回收（失败仅告警，不留 500）。

    并发双删败者（DELETE 影响 0 行——胜者已删）：按幂等语义统一 404 NOT_FOUND
    （与顺序双删的资源不存在路径一致），不追加审计行、不触发文件回收。
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # P1-2：事务开头对素材行 FOR UPDATE（引用计数与删除在同一行锁窗口内；
        # 并发的草稿/发布素材校验在 FOR SHARE 上阻塞——两种交错均闭合）
        asset = _load_asset_on(cursor, tenant_id, user_id, asset_id, for_update=True)
        references = _reference_count_on(cursor, tenant_id, str(asset["id"]))
        if references > 0:
            raise AssetInUseError(
                f"素材正被 {references} 个内容版本引用（草稿或已发布），"
                "请先在任务中移除引用后再删除"
            )
        cursor.execute(
            "DELETE FROM bs_weixin_marketing_assets WHERE tenant_id = %s AND id = %s",
            (tenant_id, str(asset["id"])),
        )
        if cursor.rowcount != 1:
            # 并发窗口：行已被并发删除请求（或清理任务）移除——败者幂等 404，
            # 不写审计、不回收文件（胜者已负责）
            raise NotFoundError("素材不存在")
        _insert_weixin_audit_on(
            cursor, tenant_id, AUDIT_ASSET_DELETED, user_id=user_id,
            details={"asset_id": str(asset["id"]), "sha256": asset.get("sha256")},
        )
        conn.commit()
    _remove_asset_file_safely(asset)
    logger.info(
        f"后端日志：weixin_marketing 素材删除 tenant={tenant_id} asset={asset['id']}"
    )
    return {"asset_id": str(asset["id"]), "deleted": True}


def _remove_asset_file_safely(asset: Dict[str, Any]) -> None:
    storage_ref = asset.get("storage_ref") or ""
    if not storage_ref:
        return
    try:
        os.remove(storage_ref)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning(
            f"后端日志：素材删除后文件回收失败（行已删，磁盘残留待人工核对）"
            f"tenant={asset.get('tenant_id')} asset={asset.get('id')} ref={storage_ref}: {e}"
        )


# ==================== 过期清理（后台任务核心）====================


def cleanup_expired_assets(
    now: Optional[datetime] = None,
    batch: Optional[int] = None,
    config: Optional[WeixinMarketingConfig] = None,
) -> Dict[str, Any]:
    """过期无引用素材批量硬删（行+文件）。

    - 候选：retention_until 非空且已过期，且**未被 draft/published revision 引用**
      （NOT EXISTS 下推进 WHERE，LIMIT 之前——P2 清理饥饿修复：被引用素材不再
      占用批量配额阻塞后续可清理行）；FOR UPDATE SKIP LOCKED 防多 worker 重复；
    - 事务内引用复核保留（锁窗口防护：候选扫描与删除之间新落引用仍拒删）；
    - enabled=false（模块总门控）直接零动作。
    """
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {"enabled": False, "deleted": 0, "skipped_referenced": 0}
    now = _aware(now or _utcnow())
    batch = int(batch or DEFAULT_ASSETS_CLEANUP_BATCH)
    deleted_refs: List[Dict[str, Any]] = []
    skipped = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_ASSET_COLUMNS} FROM bs_weixin_marketing_assets a
            WHERE a.retention_until IS NOT NULL AND a.retention_until < %s
              AND NOT EXISTS (
                  SELECT 1 FROM bs_weixin_marketing_content_blocks cb
                  JOIN bs_weixin_marketing_revisions r
                    ON r.tenant_id = cb.tenant_id AND r.id = cb.revision_id
                  WHERE cb.tenant_id = a.tenant_id AND cb.asset_id = a.id
                    AND r.status IN ('draft', 'published')
              )
            ORDER BY a.retention_until
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (now, batch),
        )
        stale = [dict(r) for r in cursor.fetchall()]
        for asset in stale:
            if _reference_count_on(cursor, asset["tenant_id"], str(asset["id"])) > 0:
                skipped += 1
                continue
            cursor.execute(
                "DELETE FROM bs_weixin_marketing_assets "
                "WHERE tenant_id = %s AND id = %s AND retention_until < %s",
                (asset["tenant_id"], str(asset["id"]), now),
            )
            if cursor.rowcount != 1:
                continue  # 并发窗口被删/被改：跳过
            _insert_weixin_audit_on(
                cursor, asset["tenant_id"], AUDIT_ASSET_CLEANED,
                user_id=asset.get("user_id"), actor_type=ACTOR_TYPE_SYSTEM,
                details={"asset_id": str(asset["id"]), "sha256": asset.get("sha256")},
            )
            deleted_refs.append(asset)
        conn.commit()
    for asset in deleted_refs:
        _remove_asset_file_safely(asset)
    if deleted_refs or skipped:
        logger.info(
            f"后端日志：weixin_marketing 过期素材清理 deleted={len(deleted_refs)} "
            f"skipped_referenced={skipped}"
        )
    return {
        "enabled": True,
        "deleted": len(deleted_refs),
        "skipped_referenced": skipped,
    }


# ==================== 草稿/发布素材引用校验 ====================


def assert_blocks_assets_on(cursor, tenant_id: str, user_id: str, blocks: List[Dict[str, Any]]) -> None:
    """内容块图片引用校验（游标级，**必须**与块写入同一事务调用）：素材存在/
    active/属主一致；缺失/停用/非属主 → WeixinValidationError(422)。

    P1-2 锁协议：每行 SELECT ... FOR SHARE——多草稿校验可并发（共享锁），与
    delete_asset 的 FOR UPDATE 互斥。调用方事务在块写入提交前一直持有共享锁，
    故「删除计数零引用」与「草稿落引用」不可能交错出现：
    - 删除先锁并提交（行删）→ 本校验 FOR SHARE 无行 → 422 fail-closed；
    - 本校验先锁（行在）→ 块写入同事务提交 → 删除阻塞后计数见引用 → 409。
    仅提供游标版（无自开连接版：锁在连接关闭即释放，闭不住竞态，属不安全路径）。
    """
    asset_ids = sorted({
        str(b["asset_id"]) for b in blocks
        if b.get("kind") == BLOCK_KIND_IMAGE and b.get("asset_id")
    })
    if not asset_ids:
        return
    # asset_ids 已排序：多素材并发校验的加锁顺序确定（防 AB-BA）
    cursor.execute(
        """
        SELECT id, status, user_id FROM bs_weixin_marketing_assets
        WHERE tenant_id = %s AND id IN %s
        FOR SHARE
        """,
        (tenant_id, tuple(asset_ids)),
    )
    rows = {str(r["id"]): dict(r) for r in cursor.fetchall()}
    for asset_id in asset_ids:
        row = rows.get(asset_id)
        if row is None:
            raise WeixinValidationError(f"图片素材不存在或不可用: {asset_id}")
        if row.get("status") != ASSET_STATUS_ACTIVE:
            raise WeixinValidationError(f"图片素材已不可用: {asset_id}")
        if row.get("user_id") != user_id:
            # 素材 ACL=租户+属主：不允许多用户混用（共享 ACL 留后续扩展）
            raise WeixinValidationError(f"图片素材不存在或不可用: {asset_id}")


# ==================== Runtime 资产下载（设备 token 链路核心）====================


def serve_invocation_asset(
    tenant_id: str, device_id: str, invocation_id: str, asset_id: str
) -> AssetContentResolution:
    """Runtime 资产下载校验链 + 受控字节（local_tools 资产端点的同步服务）。

    校验链（与 payload 端点同构）：invocation 归属租户/设备（跨租户/跨设备统一 404）
    → v2 业务类型 → 场景绑定 fail-closed（business_ref 缺 scenario/task 一律 409）
    → 已领取未终态（queued 拒绝/终态拒绝）→ revision 绑定资产集校验（素材必须被
    本 invocation 绑定 revision 的内容块引用，不接受任意 asset_id）→ 文件字节
    sha256 复核（不符 500 + 上下文供审计）。无重定向/无任意 URL。
    """
    from src.desktop_automation.constants import BUSINESS_KIND_DESKTOP_AUTOMATION
    from src.desktop_automation.payload_resolver import PAYLOAD_ALLOWED_INVOCATION_STATES
    from src.local_tools import repository as lt_repository

    inv = lt_repository.get_invocation(invocation_id, tenant_id)
    if inv is None or str(inv["device_id"]) != device_id:
        raise AssetEndpointError("INVOCATION_NOT_FOUND", 404, "invocation 不存在或不属于当前设备")
    if inv["business_kind"] != BUSINESS_KIND_DESKTOP_AUTOMATION:
        raise AssetEndpointError(
            "NOT_DESKTOP_AUTOMATION", 409, "仅桌面自动任务 v2 invocation 支持素材下载"
        )
    if inv["state"] not in PAYLOAD_ALLOWED_INVOCATION_STATES:
        if inv["state"] == "queued":
            raise AssetEndpointError(
                "INVOCATION_NOT_CLAIMED", 409, "invocation 尚未被设备领取，拒绝下载素材"
            )
        raise AssetEndpointError(
            "INVOCATION_TERMINATED", 409, f"invocation 已终态，拒绝下载素材: {inv['state']}"
        )
    business_ref = inv["business_ref"] or {}
    bound_scenario = business_ref.get("scenario_key") or ""
    revision_ref = str(business_ref.get("revision_ref") or "")
    task_ref = str(business_ref.get("task_ref") or "")
    if not bound_scenario or not task_ref or not revision_ref:
        # fail-closed：无场景/任务/版本绑定的 invocation 一律拒绝（同 payload 端点口径）
        raise AssetEndpointError(
            "SCENARIO_BINDING_MISSING", 409, "invocation 未绑定 scenario/task/revision，拒绝下载素材"
        )
    if bound_scenario != SCENARIO_KEY:
        raise AssetEndpointError(
            "ASSET_SCENARIO_MISMATCH", 409, "素材下载仅支持微信固定内容场景"
        )
    # :test 后缀的试发 task_ref 复用同一资产集（试发 run 绑定同一 revision）
    automation_ref = (
        task_ref[: -len(TEST_TASK_REF_SUFFIX)]
        if task_ref.endswith(TEST_TASK_REF_SUFFIX)
        else task_ref
    )
    if _to_uuid_text(automation_ref) is None or _to_uuid_text(revision_ref) is None:
        raise AssetEndpointError("SCENARIO_BINDING_MISSING", 409, "invocation 绑定引用非法")
    key = _to_uuid_text(asset_id)
    if key is None:
        raise AssetEndpointError("ASSET_NOT_FOUND", 404, "素材不存在或不属于当前任务")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # revision 归属核验（防伪造 business_ref）：revision 必须属于 task_ref 指向的自动化
        cursor.execute(
            """
            SELECT r.id, r.automation_id FROM bs_weixin_marketing_revisions r
            JOIN bs_weixin_marketing_automations a
              ON a.tenant_id = r.tenant_id AND a.id = r.automation_id
            WHERE r.tenant_id = %s AND r.id = %s
            """,
            (tenant_id, revision_ref),
        )
        revision_row = cursor.fetchone()
        if (
            revision_row is None
            or str(revision_row["automation_id"]) != _to_uuid_text(automation_ref)
        ):
            raise AssetEndpointError(
                "SCENARIO_BINDING_MISSING", 409, "invocation 绑定的 revision 不属于当前任务"
            )
        # 素材行（租户域）+ 绑定资产集校验：素材必须被该 revision 的 image 块引用
        cursor.execute(
            f"SELECT {_ASSET_COLUMNS} FROM bs_weixin_marketing_assets "
            "WHERE tenant_id = %s AND id = %s",
            (tenant_id, key),
        )
        row = cursor.fetchone()
        asset = dict(row) if row else None
        if asset is None or asset.get("status") != ASSET_STATUS_ACTIVE:
            raise AssetEndpointError("ASSET_NOT_FOUND", 404, "素材不存在或不属于当前任务")
        cursor.execute(
            """
            SELECT 1 FROM bs_weixin_marketing_content_blocks
            WHERE tenant_id = %s AND revision_id = %s AND asset_id = %s AND kind = 'image'
            LIMIT 1
            """,
            (tenant_id, revision_ref, key),
        )
        if cursor.fetchone() is None:
            # 命中租户内他人素材/本任务未引用的素材：统一 404（不泄露素材存在性）
            raise AssetEndpointError("ASSET_NOT_FOUND", 404, "素材不存在或不属于当前任务")
    try:
        return _read_asset_bytes(asset)
    except WeixinMarketingError as e:
        context = {"asset_id": key, "invocation_id": str(inv["id"])}
        code = "ASSET_HASH_MISMATCH"
        if "文件不可用" in str(e):
            code = "ASSET_FILE_MISSING"
        raise AssetEndpointError(
            code, 500, "素材文件校验失败，拒绝下发", context=context
        ) from e
