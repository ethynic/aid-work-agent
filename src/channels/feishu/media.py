"""
飞书媒体文件处理

支持：
- 下载用户发送的图片（通过 image_key）
- 下载用户发送的文件（通过 file_key）
- 上传图片（获取 image_key）
- 上传文件（获取 file_key）

官方 API：
- 下载图片 GET /open-apis/im/v1/images/{image_key}?image_type=message|avatar
- 下载文件 GET /open-apis/im/v1/files/{file_key}
- 上传图片 POST /open-apis/im/v1/images  (multipart: image_type + image)
- 上传文件 POST /open-apis/im/v1/files   (multipart: file_type + file_name + file)

鉴权：Authorization: Bearer <tenant_access_token>
"""

import os
from typing import Awaitable, Callable, Optional, Tuple

import httpx
from loguru import logger

FEISHU_BASE_URL = "https://open.feishu.cn"


# 文件扩展名 → 飞书 file_type 映射
# 飞书支持的类型：opus / mp4 / pdf / doc / xls / ppt / stream
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}


class FeishuMedia:
    """飞书媒体文件管理"""

    def __init__(
        self,
        access_token_getter: Callable[[], Awaitable[str]],
        upload_dir: Optional[str] = None,
    ):
        """
        Args:
            access_token_getter: 获取 tenant_access_token 的异步函数
            upload_dir: 媒体文件本地存储目录
        """
        self._get_access_token = access_token_getter
        self.upload_dir = upload_dir or "./storage/uploads/feishu"
        os.makedirs(self.upload_dir, exist_ok=True)

    async def download_image(
        self, image_key: str, save_dir: Optional[str] = None, image_type: str = "message"
    ) -> Optional[Tuple[str, bytes]]:
        """
        下载飞书图片

        GET /open-apis/im/v1/images/{image_key}?image_type={image_type}
        响应为二进制流

        Args:
            image_key: 飞书 image_key（来自图片消息 content）
            save_dir: 保存目录（默认 self.upload_dir）
            image_type: 图片类型：message（消息图片）/ avatar（头像）

        Returns:
            (local_path, image_bytes) 或 None（失败时）
        """
        try:
            access_token = await self._get_access_token()
            url = f"{FEISHU_BASE_URL}/open-apis/im/v1/images/{image_key}"
            headers = {"Authorization": f"Bearer {access_token}"}
            params = {"image_type": image_type}

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code != 200:
                    logger.error(
                        f"[Feishu] 下载图片失败: HTTP {response.status_code}, "
                        f"image_key={image_key}, body={response.text[:200]}"
                    )
                    return None

                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    # 飞书返回 JSON 表示错误
                    logger.error(
                        f"[Feishu] 下载图片失败: image_key={image_key}, "
                        f"resp={response.text[:200]}"
                    )
                    return None

                # 根据 Content-Type 推断扩展名
                ext = _ext_from_content_type(content_type)
                local_path = os.path.join(
                    save_dir or self.upload_dir, f"{image_key}{ext}"
                )
                with open(local_path, "wb") as f:
                    f.write(response.content)

                logger.info(
                    f"[Feishu] 图片已下载: {image_key} -> {local_path} "
                    f"({len(response.content)} bytes)"
                )
                return local_path, response.content

        except Exception as e:
            logger.error(f"[Feishu] 下载图片异常: image_key={image_key}, {e}")
            return None

    async def download_file(
        self, file_key: str, file_name: Optional[str] = None, save_dir: Optional[str] = None
    ) -> Optional[Tuple[str, bytes]]:
        """
        下载飞书文件

        GET /open-apis/im/v1/files/{file_key}

        Args:
            file_key: 飞书 file_key（来自文件消息 content）
            file_name: 文件名（默认使用 file_key）
            save_dir: 保存目录（默认 self.upload_dir）

        Returns:
            (local_path, file_bytes) 或 None（失败时）
        """
        try:
            access_token = await self._get_access_token()
            url = f"{FEISHU_BASE_URL}/open-apis/im/v1/files/{file_key}"
            headers = {"Authorization": f"Bearer {access_token}"}

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.get(url, headers=headers)

                if response.status_code != 200:
                    logger.error(
                        f"[Feishu] 下载文件失败: HTTP {response.status_code}, "
                        f"file_key={file_key}, body={response.text[:200]}"
                    )
                    return None

                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    logger.error(
                        f"[Feishu] 下载文件失败: file_key={file_key}, "
                        f"resp={response.text[:200]}"
                    )
                    return None

                # 文件名：优先使用传入的 file_name，其次 Content-Disposition，最后 file_key
                final_name = file_name
                if not final_name:
                    disposition = response.headers.get("Content-Disposition", "")
                    if "filename=" in disposition:
                        final_name = disposition.split("filename=")[-1].strip('"')
                if not final_name:
                    final_name = file_key

                local_path = os.path.join(save_dir or self.upload_dir, final_name)
                with open(local_path, "wb") as f:
                    f.write(response.content)

                logger.info(
                    f"[Feishu] 文件已下载: {file_key} -> {local_path} "
                    f"({len(response.content)} bytes)"
                )
                return local_path, response.content

        except Exception as e:
            logger.error(f"[Feishu] 下载文件异常: file_key={file_key}, {e}")
            return None

    async def upload_image(
        self, image_path: str, image_type: str = "message"
    ) -> Optional[str]:
        """
        上传图片到飞书

        POST /open-apis/im/v1/images
        multipart/form-data: image_type + image

        Args:
            image_path: 本地图片路径
            image_type: 图片类型：message（消息图片）/ avatar（头像）

        Returns:
            image_key 或 None（失败时）
        """
        try:
            if not os.path.exists(image_path):
                logger.error(f"[Feishu] 图片不存在: {image_path}")
                return None

            access_token = await self._get_access_token()
            url = f"{FEISHU_BASE_URL}/open-apis/im/v1/images"
            headers = {"Authorization": f"Bearer {access_token}"}
            filename = os.path.basename(image_path)

            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(image_path, "rb") as f:
                    files = {"image": (filename, f)}
                    data = {"image_type": image_type}
                    response = await client.post(url, headers=headers, data=data, files=files)

                resp_data = response.json()
                code = resp_data.get("code", -1)
                if code != 0:
                    logger.error(
                        f"[Feishu] 上传图片失败: code={code}, "
                        f"msg={resp_data.get('msg')}"
                    )
                    return None

                image_key = resp_data.get("data", {}).get("image_key")
                if not image_key:
                    logger.error(f"[Feishu] 上传图片响应缺少 image_key: {resp_data}")
                    return None

                logger.info(f"[Feishu] 图片已上传: {filename} -> {image_key}")
                return image_key

        except Exception as e:
            logger.error(f"[Feishu] 上传图片异常: {e}")
            return None

    async def upload_file(
        self, file_path: str, file_type: Optional[str] = None
    ) -> Optional[str]:
        """
        上传文件到飞书

        POST /open-apis/im/v1/files
        multipart/form-data: file_type + file_name + file

        Args:
            file_path: 本地文件路径
            file_type: 文件类型（opus/mp4/pdf/doc/xls/ppt/stream）。
                       不传则根据扩展名自动推断，未知扩展名降级为 stream。

        Returns:
            file_key 或 None（失败时）
        """
        try:
            if not os.path.exists(file_path):
                logger.error(f"[Feishu] 文件不存在: {file_path}")
                return None

            filename = os.path.basename(file_path)
            if not file_type:
                ext = os.path.splitext(filename)[1].lower()
                file_type = _FILE_TYPE_MAP.get(ext, "stream")

            access_token = await self._get_access_token()
            url = f"{FEISHU_BASE_URL}/open-apis/im/v1/files"
            headers = {"Authorization": f"Bearer {access_token}"}

            async with httpx.AsyncClient(timeout=120.0) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (filename, f)}
                    data = {"file_type": file_type, "file_name": filename}
                    response = await client.post(url, headers=headers, data=data, files=files)

                resp_data = response.json()
                code = resp_data.get("code", -1)
                if code != 0:
                    logger.error(
                        f"[Feishu] 上传文件失败: code={code}, "
                        f"msg={resp_data.get('msg')}"
                    )
                    return None

                file_key = resp_data.get("data", {}).get("file_key")
                if not file_key:
                    logger.error(f"[Feishu] 上传文件响应缺少 file_key: {resp_data}")
                    return None

                logger.info(f"[Feishu] 文件已上传: {filename} -> {file_key}")
                return file_key

        except Exception as e:
            logger.error(f"[Feishu] 上传文件异常: {e}")
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
