"""媒体资产注册（MediaRegistry）。

独立于 ImageRegistry（后者不支持视频），复用 uploaded_file:{file_id} Redis 命名空间
和 /api/files/{file_id}/download 路由（main.py），零新增路由。

关键能力：
- register_local: 本地文件复制到租户 storage 并注册 file_id（兼容 _get_file_info 读取端）
- read_as_base64: 读本地图片转 data:image base64（spike 验证万相支持 base64，无需公网URL/OSS）
- download_and_register: 下载万相临时 video_url 并注册（含烧录 AI 标识）

设计依据：docs/system/content-production/mvp-design.md §7、§11。
"""
from __future__ import annotations

import base64
import mimetypes
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from src.core.redis_client import redis_client
from src.core.storage import (
    ensure_tenant_storage_dir,
    get_tenant_storage_abs_path,
)

# file_id Redis hash 字段集，与 main.py _get_file_info 读取端 + ImageRegistry 兼容
_REQUIRED_FIELDS = ("file_id", "name", "path", "size", "mime_type", "type")

# AI 标识烧录（2025.9.1 法规）：右下角常驻文字 + 半透明背景框
# 字体候选路径：覆盖 Linux/Docker（Noto CJK / wqy）、Windows（msyh）、macOS（PingFang）
# Docker 镜像装的是 fonts-noto-cjk，路径与 wqy 不同，必须显式列出
_FONT_CANDIDATES = [
    # Linux/Docker（Dockerfile apt install fonts-noto-cjk）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
]

_DEFAULT_TTL_SECONDS = 86400 * 7   # 成片默认保留 7 天（Redis TTL；磁盘恢复不覆盖 storage/tenants）


class MediaRegistry:
    """注册视频/图片文件为 file_id，与 ImageRegistry 并存、互不干扰。"""

    async def register_local(
        self,
        source_path: str | Path,
        tenant_id: str,
        mime_type: str,
        user_id: str | None = None,
        display_name: str | None = None,
        scene_subdir: str = "videos",
        ttl_seconds: int | None = _DEFAULT_TTL_SECONDS,
    ) -> str:
        """将本地文件复制到租户 storage 并注册 file_id。

        Args:
            source_path: 源文件路径
            tenant_id: 租户 id（后台无租户上下文时可为空串，但 storage 需要目录，故传 "_anonymous" 占位）
            mime_type: 显式指定（如 video/mp4 / image/jpeg）
            scene_subdir: storage 子目录，默认 videos；图片可传 images
            ttl_seconds: Redis TTL，None 表示永久；成片默认 7 天

        Returns:
            file_id（格式 file_<12hex>），下载走 GET /api/files/{file_id}/download
        """
        # tenant_id 为空时用占位目录（storage.ensure_tenant_storage_dir 不接受空串）
        tid = tenant_id or "_anonymous"
        file_id = f"file_{uuid.uuid4().hex[:12]}"
        ext = Path(source_path).suffix or ".mp4"
        scene = f"{scene_subdir}/{datetime.now().strftime('%Y-%m')}"

        ensure_tenant_storage_dir(tid, scene)
        dest = get_tenant_storage_abs_path(tid, scene, f"{file_id}{ext}")
        shutil.copy2(source_path, dest)
        size = Path(dest).stat().st_size

        key = redis_client.make_key("uploaded_file", file_id)
        redis_client.hset(key, mapping={
            "file_id": file_id,
            "name": display_name or Path(source_path).name,
            "path": str(dest),
            "size": str(size),
            "mime_type": mime_type,
            "type": "video" if mime_type.startswith("video/") else "image",
            "registered_at": datetime.now().isoformat(),
        })
        # 显式 None 才跳过 TTL（永久）；注意 expire <=0 会删 key，绝不能传非正数
        if ttl_seconds is not None and ttl_seconds > 0:
            redis_client.expire(key, ttl_seconds)

        logger.info(f"视频生成: 注册 file_id={file_id} ({mime_type}, {size}B) → {dest}")
        return file_id

    async def download_and_register(
        self,
        url: str,
        tenant_id: str,
        display_name: str | None = None,
        burn_label: bool = True,
    ) -> str:
        """下载万相返回的临时 video_url（24h 有效）到本地并注册。

        成片在注册前用 FFmpeg 烧录醒目「AI 生成内容」标识（2025.9.1 法规，§11）。
        """
        tmp_dir = Path(tempfile.mkdtemp(prefix="vg_dl_"))
        logger.info(
            f"视频生成: 开始下载万相成片 url={url} tenant={tenant_id} "
            f"display_name={display_name} burn_label={burn_label} tmp={tmp_dir}"
        )
        try:
            raw_path = tmp_dir / "raw.mp4"
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        body_preview = (resp.text or "")[:500]
                        logger.error(
                            f"视频生成: 万相成片下载 HTTP 非 200 status={resp.status_code} "
                            f"url={url} body_preview={body_preview!r}"
                        )
                    resp.raise_for_status()
                    raw_path.write_bytes(resp.content)
                logger.info(
                    f"视频生成: 万相成片下载完成 size={raw_path.stat().st_size}B path={raw_path}"
                )
            except Exception as exc:
                # 临时调试：万相 URL 24h 过期 / 404 / 超时 / 网络断均会走这里
                logger.exception(
                    f"视频生成: 万相成片下载失败 url={url} tenant={tenant_id}: {exc!r}"
                )
                raise

            final_path = raw_path
            if burn_label:
                labeled_path = tmp_dir / "labeled.mp4"
                try:
                    _burn_ai_label(str(raw_path), str(labeled_path))
                    final_path = labeled_path
                except Exception as exc:
                    # 临时调试：FFmpeg 未装 / 中文字体缺失 / 滤镜失败均会走这里
                    logger.exception(
                        f"视频生成: AI 标识烧录失败 raw={raw_path} "
                        f"raw_size={raw_path.stat().st_size}B: {exc!r}"
                    )
                    raise

            return await self.register_local(
                final_path,
                tenant_id=tenant_id,
                mime_type="video/mp4",
                display_name=display_name or "ai_video.mp4",
                scene_subdir="videos",
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def read_as_base64(file_id: str) -> str:
        """读本地图片转 base64 data URL。

        spike 验证万相 media.url 支持 base64（无需公网URL/OSS/图床）。
        """
        path = redis_client.hget(redis_client.make_key("uploaded_file", file_id), "path")
        if not path:
            raise ValueError(f"file_id 不存在或已过期: {file_id}")
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return f"data:image/jpeg;base64,{b64}"

    @staticmethod
    def get_local_path(file_id: str) -> str:
        """取 file_id 对应的本地磁盘路径（供预处理读图等用）。"""
        path = redis_client.hget(redis_client.make_key("uploaded_file", file_id), "path")
        if not path:
            raise ValueError(f"file_id 不存在或已过期: {file_id}")
        return str(path)


def _burn_ai_label(input_path: str, output_path: str) -> None:
    """用 FFmpeg drawtext 在视频右下角烧录「AI 生成内容」标识。

    浅灰字 + 半透明黑色背景框，长期可见（满足 2025.9.1 法规"醒目、长期可见"要求）。
    若 FFmpeg 不可用或无中文字体，抛异常由调用方标记 card 失败。
    """
    fontfile = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)
    logger.info(
        f"视频生成: 开始烧录 AI 标识 input={input_path} output={output_path} "
        f"fontfile={fontfile or '未命中候选列表（将用 FFmpeg 默认字体，可能无中文字形导致方框）'}"
    )
    # drawtext 文字含特殊字符需转义（冒号、单引号）。中文「AI 生成内容」无特殊字符，安全。
    # 字号 h/24 + 浅灰字 + 较淡背景框，降低刺眼感但仍满足"醒目、长期可见"。
    drawtext = (
        "drawtext=text='AI 生成内容':"
        "x=w-tw-16:y=h-th-16:"
        "fontcolor=0xC0C0C0:fontsize=h/24:"
        "box=1:boxcolor=black@0.35:boxborderw=6"
    )
    if fontfile:
        # fontfile 路径里的冒号在 ffmpeg filter 中需转义（Windows C:）
        fontfile_esc = fontfile.replace("\\", "/").replace(":", "\\:")
        drawtext += f":fontfile='{fontfile_esc}'"

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path,
        "-vf", drawtext,
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        logger.error(f"视频生成: FFmpeg 未安装 cmd={cmd}")
        raise RuntimeError("FFmpeg 未安装，无法烧录 AI 标识") from exc
    if result.returncode != 0 or not Path(output_path).exists():
        # 临时调试：把完整 stdout/stderr/cmd 都打出来，便于定位滤镜/字体/编码失败原因
        logger.error(
            f"视频生成: FFmpeg 烧录失败 returncode={result.returncode} "
            f"output_exists={Path(output_path).exists()} "
            f"input_size={Path(input_path).stat().st_size}B "
            f"stdout={result.stdout!r} stderr={result.stderr!r} cmd={cmd}"
        )
        raise RuntimeError(f"FFmpeg 烧录 AI 标识失败: {result.stderr[-500:]}")
    logger.info(
        f"视频生成: AI 标识烧录完成 -> {output_path} size={Path(output_path).stat().st_size}B"
    )
