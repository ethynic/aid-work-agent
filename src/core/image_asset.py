"""
图片资产注册与寻址（系统级横切能力）

所有图片资产（知识库 / 工具生成 / 用户上传 / Web 抓取 / 截图）必须通过
`ImageRegistry` 注册，业务接口之间传递图片必须使用 `ImageRef`（禁止
裸 file_path / url / base64）。

设计文档：docs/system/image-asset-pipeline-design.md
开发计划：docs/plans/plan-image-asset-pipeline.md（Phase 0）

复用 `cp_tool._register_download` 的 Redis 协议（`uploaded_file:{file_id}` +
hset + expire），磁盘写入走 `src/core/storage.py` 的租户目录规范
（`storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}`）。
"""

import hashlib
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path


# ============================================================
# 敏感信息过滤（错误响应中过滤密码/Token）
# ============================================================

_SENSITIVE_PATTERNS = [
    re.compile(r'password["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'api[_-]?key["\s:=]+\S+', re.IGNORECASE),
    re.compile(r'token["\s:=]+\S+', re.IGNORECASE),
]


def _sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感字段，遵循 backend_dev.md 错误处理规范。"""
    if not error_msg:
        return ""
    for pattern in _SENSITIVE_PATTERNS:
        error_msg = pattern.sub(
            lambda m: m.group(0).split('=')[0].split(':')[0].split('"')[0] + '=***',
            error_msg,
        )
    return error_msg


# ============================================================
# ImageRef：系统内传递图片的唯一契约
# ============================================================

class ImageRef(BaseModel):
    """统一图片资产引用卡 - 系统内传递图片的唯一契约。

    所有工具结果、SSE 事件、API 响应中传递图片都必须使用此结构，
    禁止用裸 file_path / url / base64。
    """

    file_id: str
    """复用 cp 的 file_id 体系（file_xxxxxxxxxxxx），与 /api/files/{file_id}/download 兼容"""

    download_url: str
    """下载 URL，格式 `/api/files/{file_id}/download`，复用现有路由"""

    display_name: str
    """业务显示名（如 `黄果树瀑布.jpg`）"""

    width: Optional[int] = None
    """像素宽（损坏图片或读取失败时为 None）"""

    height: Optional[int] = None
    """像素高（损坏图片或读取失败时为 None）"""

    mime_type: str = "image/*"
    """MIME 类型，缺省 `image/*`"""

    size_bytes: int = 0
    """文件字节大小"""

    source: Literal[
        "knowledge_base",   # 知识库资产
        "tool_generated",   # 工具生成（x_to_image 等）
        "user_upload",      # 用户对话中上传
        "web_fetch",        # 从外部 URL 下载
        "screenshot",       # 浏览器/RPA 截图
    ]
    """来源溯源，决定清理策略与 UI 提示"""

    source_ref: Optional[str] = None
    """来源具体引用（doc_id / tool_call_id / url / session_id）"""

    usage: Literal[
        "inline",       # 行内图片（Agent 回复中的图）
        "attachment",   # 附件形式
        "embedded",     # 嵌入文档内部（Word/PDF）
        "thumbnail",    # 缩略图（列表预览）
    ] = "inline"
    """用途语义，决定 UI 渲染方式与清理策略"""

    placement: Literal[
        "after_text",   # 文本之后（默认）
        "before_text",  # 文本之前
        "inline",       # 文本流中行内
    ] = "after_text"
    """位置语义：Web 端决定渲染位置；渠道端降级为占位符策略"""

    linked_doc_id: Optional[int] = None
    """业务关联：知识库 doc_id（可选）"""

    linked_chunk_id: Optional[int] = None
    """业务关联：知识库 chunk_id（可选）"""


# ============================================================
# ImageRegistry：注册 / 寻址 / 抓取 / 清理
# ============================================================

class ImageRegistry:
    """图片资产注册与寻址（系统级横切能力）。

    所有方法均为协程，调用方使用 `await`。复用 `cp_tool._register_download`
    的 Redis 协议（`uploaded_file:{file_id}` + hset + expire），磁盘写入走
    `src/core/storage.py` 标准函数。

    单例通过 `get_image_registry()` 惰性获取，避免模块顶层副作用。
    """

    # 默认 TTL（24 小时，适用于工具生成/上传等临时图片）
    DEFAULT_TTL = 86400
    # 永久 TTL 标记（仅知识库资产使用，语义为「不调 expire」，Redis hash 默认无 TTL）
    PERMANENT_TTL = -1
    # Web 抓取 URL 去重缓存 TTL（7 天）
    FETCH_CACHE_TTL = 7 * 86400
    # 单文件抓取上限（10MB，防止误抓视频）
    MAX_FETCH_SIZE = 10 * 1024 * 1024

    def __init__(self):
        # redis_client 模块顶层已有全局单例（redis_client = RedisClient()），
        # import 安全，不会触发 master_agent 构造。
        from src.core.redis_client import redis_client
        self._redis = redis_client

    # ------------------------------------------------------------
    # register：注册图片到租户目录
    # ------------------------------------------------------------

    async def register(
        self,
        source_path: Union[str, Path],
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        source: str = "tool_generated",
        usage: str = "inline",
        source_ref: Optional[str] = None,
        linked_doc_id: Optional[int] = None,
        linked_chunk_id: Optional[int] = None,
        move: bool = False,
        ttl_seconds: Optional[int] = None,
    ) -> ImageRef:
        """注册图片到租户目录，返回 ImageRef。

        存储路径：`storage/tenants/{tenant_id}/images/{yyyy-mm}/{file_id}{ext}`
        Redis 写入与 cp_tool 一致的 `uploaded_file:{file_id}` 命名空间。

        Args:
            source_path: 源图片路径
            tenant_id: 租户 ID（必填，租户隔离）
            user_id: 创建用户 ID（写入 Redis 元信息，可选）
            display_name: 业务显示名；缺省时用源文件名；不以 ext 结尾时自动补上
            source: 来源（见 ImageRef.source 枚举）
            usage: 用途（见 ImageRef.usage 枚举）
            source_ref: 来源具体引用（url / doc_id / tool_call_id 等）
            linked_doc_id: 关联知识库 doc_id（知识库资产场景）
            linked_chunk_id: 关联知识库 chunk_id
            move: True=移动源文件，False=复制源文件
            ttl_seconds: 显式 TTL；None 时按 source 推断（knowledge_base→永久，其他→24h）

        Returns:
            ImageRef（含完整元信息）
        """
        if not tenant_id:
            raise ValueError("tenant_id 不能为空")

        src_path = Path(source_path)
        if not src_path.exists():
            raise FileNotFoundError(f"源文件不存在: {src_path}")

        # 1. 生成 file_id 与目标路径
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        ext = src_path.suffix.lower()
        scene = f"images/{datetime.now().strftime('%Y-%m')}"
        ensure_tenant_storage_dir(tenant_id, scene)
        abs_path = get_tenant_storage_abs_path(tenant_id, scene, f"{file_id}{ext}")

        # 2. 复制或移动文件
        if move:
            shutil.move(str(src_path), str(abs_path))
        else:
            shutil.copy2(str(src_path), str(abs_path))

        # 3. 文件元信息
        file_size = os.path.getsize(abs_path)

        # 4. display_name 处理：缺省用源文件名；不以 ext 结尾时补上
        if not display_name:
            display_name = src_path.name
        if ext and not display_name.lower().endswith(ext):
            display_name = display_name + ext

        # 5. 尝试用 Pillow 读取宽高；失败不阻断注册（图片可能损坏但仍可用作占位）
        width, height = self._read_image_size(abs_path)

        # 6. mime_type 推断
        mime_type = self._guess_mime_type(ext)

        # 7. Redis hset（与 cp_tool 同命名空间 uploaded_file:{file_id}）
        key = self._redis.make_key("uploaded_file", file_id)
        redis_fields: Dict[str, Any] = {
            "file_id": file_id,
            "name": display_name,
            "path": str(abs_path),
            "size": file_size,
            "mime_type": mime_type,
            "type": "image",
            "source": source,
            "usage": usage,
            "source_ref": source_ref or "",
            "linked_doc_id": str(linked_doc_id) if linked_doc_id is not None else "",
            "linked_chunk_id": str(linked_chunk_id) if linked_chunk_id is not None else "",
            "visible": True,
            "width": width if width is not None else "",
            "height": height if height is not None else "",
            "registered_at": datetime.now().isoformat(),
        }
        if user_id is not None:
            redis_fields["user_id"] = user_id

        for field, value in redis_fields.items():
            self._redis.hset(key, field, value)

        # 8. TTL 处理：
        #   - ttl_seconds 显式传入优先
        #   - 否则 source=="knowledge_base" → 永久（不调 expire，Redis hash 默认无 TTL）
        #   - 否则 DEFAULT_TTL(86400)
        #   注意：redis_client.expire 在 seconds<=0 时会立即删除键（见 redis_client.py
        #   _InMemoryFallback.expire），所以 PERMANENT_TTL=-1 绝对不能传给 expire。
        if ttl_seconds is None:
            if source == "knowledge_base":
                effective_ttl = self.PERMANENT_TTL
            else:
                effective_ttl = self.DEFAULT_TTL
        else:
            effective_ttl = ttl_seconds

        if effective_ttl and effective_ttl > 0:
            self._redis.expire(key, effective_ttl)

        # 9. 构造并返回 ImageRef
        download_url = f"/api/files/{file_id}/download"
        ref = ImageRef(
            file_id=file_id,
            download_url=download_url,
            display_name=display_name,
            width=width,
            height=height,
            mime_type=mime_type,
            size_bytes=file_size,
            source=source,  # type: ignore[arg-type]
            source_ref=source_ref,
            usage=usage,  # type: ignore[arg-type]
            linked_doc_id=linked_doc_id,
            linked_chunk_id=linked_chunk_id,
        )

        logger.info(
            f"[ImageRegistry] register file_id={file_id} tenant={tenant_id} "
            f"source={source} usage={usage} size={file_size} "
            f"w={width} h={height} path={abs_path}"
        )
        return ref

    # ------------------------------------------------------------
    # resolve_local_path：根据 ImageRef 查 Redis 拿本地路径
    # ------------------------------------------------------------

    async def resolve_local_path(self, ref: ImageRef) -> Path:
        """从 ImageRef.file_id 查 Redis 拿本地绝对路径。

        Args:
            ref: ImageRef（至少要有 file_id）

        Returns:
            本地绝对路径 Path 对象

        Raises:
            KeyError: Redis 中找不到该 file_id 的元信息（字段缺失）
            FileNotFoundError: Redis 记录的路径在磁盘上不存在
        """
        key = self._redis.make_key("uploaded_file", ref.file_id)
        path_str = self._redis.hget(key, "path")
        if not path_str:
            raise KeyError(f"Redis 中找不到 file_id={ref.file_id} 的 path 字段")

        path = Path(path_str)
        if not path.exists():
            raise FileNotFoundError(
                f"file_id={ref.file_id} 对应的磁盘文件不存在: {path}"
            )
        return path

    # ------------------------------------------------------------
    # get_ref_by_file_id：从 Redis 反组装 ImageRef
    # ------------------------------------------------------------

    async def get_ref_by_file_id(self, file_id: str) -> Optional[ImageRef]:
        """从 Redis hgetall 反组装 ImageRef。

        Redis 不存在、字段不全（缺 file_id/path/source/usage）时返回 None，
        不抛异常，让上游优雅降级。

        Args:
            file_id: 文件 ID

        Returns:
            ImageRef 或 None
        """
        key = self._redis.make_key("uploaded_file", file_id)
        data = self._redis.hgetall(key)
        if not data:
            return None

        # 必须字段：file_id / path / source / usage
        if not data.get("file_id") or not data.get("path"):
            return None
        src_val = data.get("source")
        usage_val = data.get("usage")
        if not src_val or not usage_val:
            return None

        # size_bytes / width / height / linked_doc_id / linked_chunk_id 都是可选数值
        def _to_int(v: Any) -> Optional[int]:
            if v in (None, "", "None"):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        size_bytes = _to_int(data.get("size")) or 0
        width = _to_int(data.get("width"))
        height = _to_int(data.get("height"))
        linked_doc_id = _to_int(data.get("linked_doc_id"))
        linked_chunk_id = _to_int(data.get("linked_chunk_id"))

        mime_type = data.get("mime_type") or "image/*"
        display_name = data.get("name") or f"{file_id}"

        try:
            return ImageRef(
                file_id=str(data["file_id"]),
                download_url=f"/api/files/{file_id}/download",
                display_name=display_name,
                width=width,
                height=height,
                mime_type=mime_type,
                size_bytes=size_bytes,
                source=src_val,  # type: ignore[arg-type]
                source_ref=(data.get("source_ref") or None),
                usage=usage_val,  # type: ignore[arg-type]
                linked_doc_id=linked_doc_id,
                linked_chunk_id=linked_chunk_id,
            )
        except Exception as e:
            logger.warning(
                f"[ImageRegistry] get_ref_by_file_id 反组装 ImageRef 失败 "
                f"file_id={file_id}: {_sanitize_error_info(str(e))}"
            )
            return None

    # ------------------------------------------------------------
    # fetch_to_local：从外部 URL 下载图片并注册
    # ------------------------------------------------------------

    async def fetch_to_local(
        self,
        url: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        display_name: Optional[str] = None,
        timeout: int = 15,
    ) -> ImageRef:
        """从外部 URL 下载图片并注册为 web_fetch 图片资产。

        - URL 去重缓存：同 URL 二次调用复用上次下载结果，TTL 7 天
        - 单文件大小上限 MAX_FETCH_SIZE（10MB），超过拒绝
        - 超时控制：默认 15 秒

        Args:
            url: 远程图片 URL
            tenant_id: 租户 ID
            user_id: 创建用户 ID
            display_name: 业务显示名；缺省从 URL basename 推断
            timeout: 单次请求超时秒数

        Returns:
            ImageRef（source=web_fetch, usage=embedded）

        Raises:
            ValueError: 超过 MAX_FETCH_SIZE 或 URL 非法
            httpx.TimeoutException: 请求超时
            httpx.HTTPStatusError: HTTP 非 2xx
        """
        if not url or not url.startswith(("http://", "https://")):
            raise ValueError(f"非法 URL: {url}")

        # 1. URL 去重缓存：命中直接复用
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_key = self._redis.make_key("image_fetch_url", url_hash)
        cached_file_id = self._redis.get(cache_key)
        if cached_file_id:
            # 命中缓存：尝试从 Redis 反组装 ImageRef
            cached_ref = await self.get_ref_by_file_id(str(cached_file_id))
            if cached_ref is not None:
                logger.info(
                    f"[ImageRegistry] fetch_to_local cache hit url={url} "
                    f"file_id={cached_file_id}"
                )
                return cached_ref
            # 缓存指向的 file_id 已过期/被清理 → 重新下载

        # 2. 从 URL 推断文件名
        if not display_name:
            parsed = urlparse(url)
            url_basename = os.path.basename(parsed.path) if parsed.path else ""
            display_name = url_basename or f"fetched_{url_hash[:12]}.jpg"

        # 3. 下载到临时文件，校验大小
        # 用 url_hash 作为临时文件名前缀，避免并发碰撞
        tmp_dir = ensure_tenant_storage_dir(tenant_id, "temp")
        tmp_path = os.path.join(tmp_dir, f"fetch_{url_hash[:12]}_{display_name}")

        try:
            await self._download_with_size_check(url, tmp_path, timeout)
        except Exception as e:
            logger.warning(
                f"[ImageRegistry] fetch_to_local 下载失败 url={url}: "
                f"{_sanitize_error_info(str(e))}"
            )
            raise

        # 4. 注册为 web_fetch 资产
        try:
            ref = await self.register(
                source_path=tmp_path,
                tenant_id=tenant_id,
                user_id=user_id,
                display_name=display_name,
                source="web_fetch",
                usage="embedded",
                source_ref=url,
                move=True,  # 下载文件用完即移入正式目录
            )
        except Exception:
            # register 失败时清理临时文件
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

        # 5. 写 URL 缓存（指向 file_id，TTL 7 天）
        self._redis.set(cache_key, ref.file_id, ex=self.FETCH_CACHE_TTL)

        logger.info(
            f"[ImageRegistry] fetch_to_local ok url={url} file_id={ref.file_id} "
            f"size={ref.size_bytes}"
        )
        return ref

    # ------------------------------------------------------------
    # cleanup_temp：清理临时图片资产
    # ------------------------------------------------------------

    async def cleanup_temp(self, older_than_hours: int = 24) -> int:
        """清理临时图片资产（usage=inline/embedded 且 source=tool_generated/web_fetch）。

        知识库资产（source=knowledge_base）永久保留，不受此清理影响。

        Args:
            older_than_hours: 注册时间超过多少小时的临时资产才清理（默认 24 小时）

        Returns:
            清理的资产数量
        """
        if older_than_hours <= 0:
            return 0

        cutoff = datetime.now().timestamp() - older_than_hours * 3600
        cleaned = 0

        # 扫描所有 uploaded_file:* 键
        pattern = self._redis.make_key("uploaded_file", "*")
        keys = self._redis.keys(pattern)

        for key in keys:
            try:
                data = self._redis.hgetall(key)
                if not data:
                    continue

                src_val = data.get("source")
                usage_val = data.get("usage")
                # 仅清理 tool_generated / web_fetch 来源，且 inline / embedded 用途
                if src_val not in ("tool_generated", "web_fetch"):
                    continue
                if usage_val not in ("inline", "embedded"):
                    continue

                # 按注册时间过滤
                registered_at_str = data.get("registered_at")
                if not registered_at_str:
                    continue
                try:
                    registered_at_ts = datetime.fromisoformat(registered_at_str).timestamp()
                except (ValueError, TypeError):
                    continue

                if registered_at_ts > cutoff:
                    # 未超时，跳过
                    continue

                # 删除磁盘文件
                path_str = data.get("path")
                if path_str:
                    try:
                        if os.path.exists(path_str):
                            os.remove(path_str)
                    except OSError as e:
                        logger.warning(
                            f"[ImageRegistry] cleanup_temp 删除磁盘文件失败 "
                            f"path={path_str}: {e}"
                        )

                # 删除 Redis 键
                self._redis.delete(key)
                cleaned += 1
            except Exception as e:
                logger.warning(
                    f"[ImageRegistry] cleanup_temp 处理 key={key} 失败: "
                    f"{_sanitize_error_info(str(e))}"
                )
                continue

        if cleaned > 0:
            logger.info(
                f"[ImageRegistry] cleanup_temp cleaned={cleaned} "
                f"older_than_hours={older_than_hours}"
            )
        return cleaned

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _read_image_size(self, path: Union[str, Path]) -> tuple[Optional[int], Optional[int]]:
        """用 Pillow 读取图片宽高。失败时只记 warning，返回 (None, None)。"""
        try:
            from PIL import Image
            with Image.open(path) as img:
                w, h = img.size
            return int(w), int(h)
        except Exception as e:
            logger.warning(
                f"[ImageRegistry] 读取图片宽高失败 path={path}: "
                f"{_sanitize_error_info(str(e))}"
            )
            return None, None

    def _guess_mime_type(self, ext: str) -> str:
        """根据扩展名推断 MIME 类型，未知扩展名返回 `image/*`。"""
        mapping = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
            ".svg": "image/svg+xml",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
            ".ico": "image/x-icon",
        }
        return mapping.get(ext.lower(), "image/*")

    async def _download_with_size_check(
        self,
        url: str,
        dest_path: str,
        timeout: int,
    ) -> None:
        """用 httpx 下载到 dest_path，超过 MAX_FETCH_SIZE 抛 ValueError。

        采用流式读取 + 累计字节数的方式，避免大文件先下载再丢弃。
        """
        # httpx stream 模式：边读边写，累计字节
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()

                # 优先用 Content-Length 预检
                # 注意：int() 失败和「超过上限」都是 ValueError，必须分开捕获，
                # 否则「超过上限」会被「非数字」分支吞掉，预检完全失效。
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        cl_int = int(content_length)
                    except (TypeError, ValueError):
                        # content-length 非数字，忽略预检，走累计校验
                        cl_int = None
                    if cl_int is not None and cl_int > self.MAX_FETCH_SIZE:
                        raise ValueError(
                            f"图片大小 {cl_int} 超过上限 "
                            f"{self.MAX_FETCH_SIZE} 字节"
                        )

                # 流式写入并累计字节
                written = 0
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                        written += len(chunk)
                        if written > self.MAX_FETCH_SIZE:
                            f.close()
                            try:
                                os.remove(dest_path)
                            except OSError:
                                pass
                            raise ValueError(
                                f"图片累计大小 {written} 超过上限 "
                                f"{self.MAX_FETCH_SIZE} 字节"
                            )
                        f.write(chunk)


# ============================================================
# 模块级惰性单例（避免模块顶层实例化的副作用）
# ============================================================

_registry_instance: Optional[ImageRegistry] = None


def get_image_registry() -> ImageRegistry:
    """返回 ImageRegistry 单例，第一次调用时构造。

    遵循 backend_dev.md「包初始化无副作用」规范——避免模块顶层
    `_registry = ImageRegistry()` 触发 redis_client 全局副作用传播。
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ImageRegistry()
    return _registry_instance
