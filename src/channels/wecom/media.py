"""
企业微信媒体文件处理

支持:
- 下载用户发送的图片/文件（通过 media_id）
- 上传文件用于发送（获取 media_id）
"""

import os
from typing import Any, Callable, Awaitable, Optional, Tuple

import httpx
from loguru import logger

from src.core.storage import ensure_tenant_storage_dir

class WeComMedia:
    """企业微信媒体文件管理"""

    # 媒体文件类型映射
    MEDIA_TYPE_MAP = {
        "image": "image",
        "voice": "voice",
        "video": "video",
        "file": "file",
    }

    def __init__(
        self,
        access_token_getter: Callable[[], Awaitable[str]],
        upload_dir: Optional[str] = None,
        tenant_id: str = "",
    ):
        """
        Args:
            access_token_getter: 获取 access_token 的异步函数
            upload_dir: 媒体文件本地存储目录（无 tenant_id 时使用）
            tenant_id: 租户 ID，设置后文件存到
                       `storage/tenants/{tenant_id}/conversation/`
        """
        self._get_access_token = access_token_getter
        # upload_dir 仅作兼容保留，实际落盘统一走 _resolve_save_dir（租户附件存储规范）
        self.upload_dir = upload_dir or "./storage/uploads/wecom"
        self.tenant_id = tenant_id or ""

    def set_tenant_id(self, tenant_id: str) -> None:
        """设置租户 ID（由 ChannelFactory 在创建 adapter 后注入）"""
        self.tenant_id = tenant_id or ""

    def _resolve_save_dir(self) -> str:
        """解析最终保存目录，按租户隔离规范优先"""
        if self.tenant_id:
            return ensure_tenant_storage_dir(self.tenant_id, "conversation")
        # 无租户兜底落 _anonymous，禁止写 storage/uploads 旧路径
        return ensure_tenant_storage_dir("_anonymous", "conversation")

    async def download_media(
        self, media_id: str
    ) -> Optional[Tuple[bytes, str, str]]:
        """
        下载企业微信媒体文件

        API: GET https://qyapi.weixin.qq.com/cgi-bin/media/get

        Args:
            media_id: 媒体文件 ID

        Returns:
            (file_bytes, filename, content_type) 或 None（失败时）
        """
        try:
            access_token = await self._get_access_token()
            url = "https://qyapi.weixin.qq.com/cgi-bin/media/get"
            params = {
                "access_token": access_token,
                "media_id": media_id,
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, params=params)

                if response.status_code != 200:
                    logger.error(
                        f"下载媒体文件失败: HTTP {response.status_code}"
                    )
                    return None

                # WeCom 可能返回 JSON 错误或文件内容
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    data = response.json()
                    logger.error(
                        f"下载媒体文件失败: errcode={data.get('errcode')}, "
                        f"errmsg={data.get('errmsg')}"
                    )
                    return None

                # 从 Content-Disposition 提取文件名
                filename = media_id
                disposition = response.headers.get("Content-Disposition", "")
                if "filename=" in disposition:
                    filename = disposition.split("filename=")[-1].strip('"')

                # 保存到本地
                local_path = os.path.join(self._resolve_save_dir(), filename)
                with open(local_path, "wb") as f:
                    f.write(response.content)

                logger.info(f"媒体文件已下载: {filename} ({len(response.content)} bytes)")
                return response.content, filename, content_type

        except Exception as e:
            logger.error(f"下载媒体文件异常: {e}")
            return None

    async def upload_media(
        self,
        file_path: str,
        media_type: str = "file",
    ) -> Optional[str]:
        """
        上传媒体文件到企业微信

        API: POST https://qyapi.weixin.qq.com/cgi-bin/media/upload

        Args:
            file_path: 本地文件路径
            media_type: 媒体类型 (image | voice | video | file)

        Returns:
            media_id 或 None（失败时）
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"文件不存在: {file_path}")
                return None

            access_token = await self._get_access_token()
            url = "https://qyapi.weixin.qq.com/cgi-bin/media/upload"
            params = {
                "access_token": access_token,
                "type": self.MEDIA_TYPE_MAP.get(media_type, "file"),
            }

            filename = os.path.basename(file_path)

            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(file_path, "rb") as f:
                    files = {"media": (filename, f)}
                    response = await client.post(url, params=params, files=files)

                data = response.json()
                if "media_id" not in data:
                    logger.error(
                        f"上传媒体文件失败: errcode={data.get('errcode')}, "
                        f"errmsg={data.get('errmsg')}"
                    )
                    return None

                media_id = data["media_id"]
                logger.info(f"媒体文件已上传: {filename} -> {media_id}")
                return media_id

        except Exception as e:
            logger.error(f"上传媒体文件异常: {e}")
            return None

    async def upload_image_permanent(self, image_path: str) -> Optional[str]:
        """
        上传永久图片（用于 markdown 消息中的图片引用）

        API: POST https://qyapi.weixin.qq.com/cgi-bin/media/uploadimg

        Args:
            image_path: 本地图片路径

        Returns:
            图片 URL 或 None（失败时）
        """
        try:
            if not os.path.exists(image_path):
                logger.error(f"图片不存在: {image_path}")
                return None

            access_token = await self._get_access_token()
            url = "https://qyapi.weixin.qq.com/cgi-bin/media/uploadimg"
            params = {"access_token": access_token}

            filename = os.path.basename(image_path)

            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(image_path, "rb") as f:
                    files = {"media": (filename, f)}
                    response = await client.post(url, params=params, files=files)

                data = response.json()
                if "url" not in data:
                    logger.error(
                        f"上传永久图片失败: errcode={data.get('errcode')}, "
                        f"errmsg={data.get('errmsg')}"
                    )
                    return None

                img_url = data["url"]
                logger.info(f"永久图片已上传: {filename} -> {img_url}")
                return img_url

        except Exception as e:
            logger.error(f"上传永久图片异常: {e}")
            return None
