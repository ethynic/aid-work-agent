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
# Windows 中文字体（微软雅黑），Linux/Docker 回退到文泉驿/wqy 或 dejavu
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",          # Windows 微软雅黑
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",   # 常见 Docker 中文字体
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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
            tenant_id: 租户 id（demo 模式可为空串，但 storage 需要目录，故传 "demo" 占位）
            mime_type: 显式指定（如 video/mp4 / image/jpeg）
            scene_subdir: storage 子目录，默认 videos；图片可传 images
            ttl_seconds: Redis TTL，None 表示永久；成片默认 7 天

        Returns:
            file_id（格式 file_<12hex>），下载走 GET /api/files/{file_id}/download
        """
        # tenant_id 为空时用占位目录（storage.ensure_tenant_storage_dir 不接受空串）
        tid = tenant_id or "demo"
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
        try:
            raw_path = tmp_dir / "raw.mp4"
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                raw_path.write_bytes(resp.content)
            logger.info(f"视频生成: 万相成片下载完成 ({raw_path.stat().st_size}B)")

            if burn_label:
                labeled_path = tmp_dir / "labeled.mp4"
                _burn_ai_label(str(raw_path), str(labeled_path))
                final_path = labeled_path
            else:
                final_path = raw_path

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

    白字 + 黑色半透明背景框，长期可见（满足 2025.9.1 法规"醒目、长期可见"要求）。
    若 FFmpeg 不可用或无中文字体，抛异常由调用方标记 card 失败。
    """
    fontfile = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)
    # drawtext 文字含特殊字符需转义（冒号、单引号）。中文「AI 生成内容」无特殊字符，安全。
    drawtext = (
        "drawtext=text='AI 生成内容':"
        "x=w-tw-20:y=h-th-20:"
        "fontcolor=white:fontsize=h/16:"
        "box=1:boxcolor=black@0.5:boxborderw=10"
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
        raise RuntimeError("FFmpeg 未安装，无法烧录 AI 标识") from exc
    if result.returncode != 0 or not Path(output_path).exists():
        raise RuntimeError(f"FFmpeg 烧录 AI 标识失败: {result.stderr[-500:]}")
    logger.info(f"视频生成: AI 标识烧录完成 → {output_path}")
