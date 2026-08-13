"""
钉钉媒体文件处理

支持：
- 下载用户发送的图片（通过 downloadCode）
- 下载用户发送的文件（通过 downloadCode）
- 上传媒体文件（获取 mediaId）

官方 API：
- 下载图片/文件：通过 downloadCode 调用 /v1.0/robot/messageFiles/download
- 上传媒体文件：POST /v1.0/robot/messageFiles/upload (multipart: robotCode + mediaType + media)

鉴权：Authorization: Bearer <access_token>
"""

import os
import re
import time
from typing import Awaitable, Callable, Optional, Tuple

import httpx
from loguru import logger

from src.core.storage import ensure_tenant_storage_dir

DINGTALK_API_BASE_URL = "https://api.dingtalk.com"


class DingTalkMedia:
    """钉钉媒体文件管理"""

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
        self.upload_dir = upload_dir or "./storage/uploads/dingtalk"
        self.tenant_id = tenant_id or ""
        os.makedirs(self.upload_dir, exist_ok=True)

    def set_tenant_id(self, tenant_id: str) -> None:
        """设置租户 ID（由 ChannelFactory 在创建 adapter 后注入）"""
        self.tenant_id = tenant_id or ""

    def _resolve_save_dir(self) -> str:
        """解析最终保存目录，按租户隔离规范优先"""
        if self.tenant_id:
            return ensure_tenant_storage_dir(self.tenant_id, "conversation")
        # 单租户模式兜底
        os.makedirs(self.upload_dir, exist_ok=True)
        return self.upload_dir

    async def download_image(
        self, download_code: str, robot_code: str, save_dir: Optional[str] = None
    ) -> Optional[Tuple[str, bytes]]:
        """
        下载钉钉图片

        通过 downloadCode 下载图片，需要先调用下载接口获取文件 URL

        Args:
            download_code: 图片下载码（来自图片消息的 downloadCode 字段）
            robot_code: 机器人编码
            save_dir: 保存目录（默认 self.upload_dir）

        Returns:
            (local_path, image_bytes) 或 None（失败时）
        """
        try:
            access_token = await self._get_access_token()

            # 先获取下载 URL
            url = f"{DINGTALK_API_BASE_URL}/v1.0/robot/messageFiles/download"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "downloadCode": download_code,
                "robotCode": robot_code,
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code != 200:
                    logger.error(
                        f"[DingTalk] 获取图片下载链接失败: HTTP {response.status_code}, "
                        f"download_code={download_code}, body={response.text[:200]}"
                    )
                    return None

                resp_data = response.json()
                download_url = resp_data.get("downloadUrl")

                if not download_url:
                    logger.error(
                        f"[DingTalk] 获取图片下载链接失败: download_code={download_code}, "
                        f"resp={resp_data}"
                    )
                    return None

                # 下载图片
                image_response = await client.get(download_url)

                if image_response.status_code != 200:
                    logger.error(
                        f"[DingTalk] 下载图片失败: HTTP {image_response.status_code}, "
                        f"url={download_url}"
                    )
                    return None

                # 根据 Content-Type 推断扩展名
                content_type = image_response.headers.get("Content-Type", "")
                ext = _ext_from_content_type(content_type)
                local_path = os.path.join(
                    save_dir or self._resolve_save_dir(), f"{download_code}{ext}"
                )
                with open(local_path, "wb") as f:
                    f.write(image_response.content)

                logger.info(
                    f"[DingTalk] 图片已下载: {download_code} -> {local_path} "
                    f"({len(image_response.content)} bytes)"
                )
                return local_path, image_response.content

        except Exception as e:
            logger.error(f"[DingTalk] 下载图片异常: download_code={download_code}, {e}")
            return None

    async def download_file(
        self,
        download_code: str,
        robot_code: str,
        file_name: Optional[str] = None,
        save_dir: Optional[str] = None,
    ) -> Optional[Tuple[str, bytes]]:
        """
        下载钉钉文件

        通过 downloadCode 下载文件

        Args:
            download_code: 文件下载码（来自文件消息的 downloadCode 字段）
            robot_code: 机器人编码
            file_name: 文件名（默认使用 download_code）
            save_dir: 保存目录（默认 self.upload_dir）

        Returns:
            (local_path, file_bytes) 或 None（失败时）
        """
        try:
            access_token = await self._get_access_token()

            # 先获取下载 URL
            url = f"{DINGTALK_API_BASE_URL}/v1.0/robot/messageFiles/download"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "downloadCode": download_code,
                "robotCode": robot_code,
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code != 200:
                    logger.error(
                        f"[DingTalk] 获取文件下载链接失败: HTTP {response.status_code}, "
                        f"download_code={download_code}, body={response.text[:200]}"
                    )
                    return None

                resp_data = response.json()
                download_url = resp_data.get("downloadUrl")

                if not download_url:
                    logger.error(
                        f"[DingTalk] 获取文件下载链接失败: download_code={download_code}, "
                        f"resp={resp_data}"
                    )
                    return None

                # 下载文件
                file_response = await client.get(download_url)

                if file_response.status_code != 200:
                    logger.error(
                        f"[DingTalk] 下载文件失败: HTTP {file_response.status_code}, "
                        f"url={download_url}"
                    )
                    return None

                # 文件名：优先使用传入的 file_name，否则使用 download_code
                final_name = file_name or download_code

                local_path = os.path.join(save_dir or self._resolve_save_dir(), final_name)
                with open(local_path, "wb") as f:
                    f.write(file_response.content)

                logger.info(
                    f"[DingTalk] 文件已下载: {download_code} -> {local_path} "
                    f"({len(file_response.content)} bytes)"
                )
                return local_path, file_response.content

        except Exception as e:
            logger.error(f"[DingTalk] 下载文件异常: download_code={download_code}, {e}")
            return None

    async def upload_from_url(
        self,
        download_url: str,
        file_name: str,
        robot_code: str,
        media_type: str = "file",
    ) -> Optional[str]:
        """
        从公网 URL 下载文件并上传到钉钉

        DownloadableFileInfo 只携带 download_url（不含本地路径），
        上传前需先落到本地。下载到 self.upload_dir 后委托给 upload_media。

        Args:
            download_url: 文件的公网下载 URL
            file_name: 文件名（用于决定保存路径）
            robot_code: 机器人编码
            media_type: 媒体类型：image / file / voice / video

        Returns:
            mediaId 或 None（下载或上传失败时）
        """
        if not download_url:
            logger.warning(f"[DingTalk] upload_from_url 缺少 download_url: {file_name}")
            return None

        try:
            safe_name = _sanitize_filename(file_name or f"file_{int(time.time())}")
            local_path = os.path.join(self._resolve_save_dir(), safe_name)
            async with httpx.AsyncClient(timeout=120.0) as client:
                with client.stream("GET", download_url) as resp:
                    if resp.status_code != 200:
                        logger.error(
                            f"[DingTalk] 下载待上传文件失败: HTTP {resp.status_code}, "
                            f"url={download_url}"
                        )
                        return None
                    with open(local_path, "wb") as f:
                        for chunk in resp.iter_bytes():
                            if chunk:
                                f.write(chunk)

            logger.debug(f"[DingTalk] 待上传文件已下载: {file_name} -> {local_path}")
            return await self.upload_media(local_path, robot_code, media_type)

        except Exception as e:
            logger.error(
                f"[DingTalk] upload_from_url 异常: file={file_name}, url={download_url}, {e}"
            )
            return None

    async def upload_media(
        self, file_path: str, robot_code: str, media_type: str = "file"
    ) -> Optional[str]:
        """
        上传媒体文件到钉钉

        POST /v1.0/robot/messageFiles/upload
        multipart/form-data: robotCode + mediaType + media

        Args:
            file_path: 本地文件路径
            robot_code: 机器人编码
            media_type: 媒体类型：image（图片）/ file（文件）/ voice（语音）/ video（视频）

        Returns:
            mediaId 或 None（失败时）
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"[DingTalk] 文件不存在: {file_path}")
                return None

            access_token = await self._get_access_token()
            url = f"{DINGTALK_API_BASE_URL}/v1.0/robot/messageFiles/upload"
            headers = {"Authorization": f"Bearer {access_token}"}
            filename = os.path.basename(file_path)

            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(file_path, "rb") as f:
                    files = {"media": (filename, f)}
                    data = {"robotCode": robot_code, "mediaType": media_type}
                    response = await client.post(url, headers=headers, data=data, files=files)

                resp_data = response.json()

                # 钉钉上传接口返回格式可能不同，需要适配
                media_id = resp_data.get("mediaId") or resp_data.get("data", {}).get("mediaId")

                if not media_id:
                    logger.error(f"[DingTalk] 上传媒体文件失败: resp={resp_data}")
                    return None

                logger.info(f"[DingTalk] 媒体文件已上传: {filename} -> {media_id}")
                return media_id

        except Exception as e:
            logger.error(f"[DingTalk] 上传媒体文件异常: {e}")
            return None


def _ext_from_content_type(content_type: str) -> str:
    """根据 Content-Type 推断文件扩展名"""
    ct = content_type.split(";")[0].strip().lower()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/webp": ".webp",
    }.get(ct, ".bin")


def _sanitize_filename(name: str) -> str:
    """清洗文件名：保留常见字符，去除路径分隔符等危险字符"""
    if not name:
        return f"file_{int(time.time())}"
    # 去掉路径分隔符，替换空格和特殊字符
    cleaned = re.sub(r"[^\w.\-]", "_", name)
    # 防止文件名过长
    if len(cleaned) > 128:
        root, _, ext = cleaned.rpartition(".")
        if ext:
            cleaned = cleaned[: 128 - len(ext) - 1] + "." + ext
        else:
            cleaned = cleaned[:128]
    return cleaned or f"file_{int(time.time())}"
