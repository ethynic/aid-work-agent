#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件下载注册工具

将生成的文件注册到下载系统（main.uploaded_files），
使前端可以通过 /api/files/{file_id}/download 下载。

适用于所有需要生成文件供用户下载的场景：
- Word/Excel/PPT 文档生成
- PDF 导出
- 图片处理输出
- 数据导出文件
"""

import shutil
import uuid
from pathlib import Path
from typing import Dict, Any

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class RegisterDownloadFileInput(BaseModel):
    """注册下载文件参数"""
    file_path: str = Field(..., description="生成的文件绝对路径")
    display_name: str = Field(..., description="用户看到的文件名，例如：会议纪要.docx")


class RegisterDownloadFileTool(BaseTool):
    """注册下载文件工具"""

    name = "register_download_file"
    description = "注册生成的文件到下载系统，返回可下载的文件ID和URL。在生成Word、Excel、PDF等文件后调用此工具，用户即可在前端下载。"
    display_name = "注册下载文件"
    category = "file"
    InputModel = RegisterDownloadFileInput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        注册文件到下载系统。

        流程：
        1. 验证文件存在
        2. 生成唯一 file_id
        3. 复制到 uploads/ 目录
        4. 注册到 main.uploaded_files 字典
        5. 返回 file_id 和下载 URL
        """
        file_path = kwargs.get("file_path", "")
        display_name = kwargs.get("display_name", "")

        if not file_path:
            return {"success": False, "error": "未提供文件路径"}
        if not display_name:
            return {"success": False, "error": "未提供显示文件名"}

        src = Path(file_path)
        if not src.exists():
            return {"success": False, "error": f"文件不存在: {file_path}"}
        if not src.is_file():
            return {"success": False, "error": f"路径不是文件: {file_path}"}

        try:
            # 延迟导入，避免循环依赖
            from src.main import uploaded_files, UPLOAD_DIR

            # 生成 file_id
            file_id = f"file_{uuid.uuid4().hex[:12]}"

            # MIME 类型映射
            suffix = src.suffix.lower()
            mime_type_map = {
                '.pdf': 'application/pdf',
                '.doc': 'application/msword',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.xls': 'application/vnd.ms-excel',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.ppt': 'application/vnd.ms-powerpoint',
                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                '.txt': 'text/plain',
                '.csv': 'text/csv',
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.gif': 'image/gif',
                '.zip': 'application/zip',
            }
            mime_type = mime_type_map.get(suffix, 'application/octet-stream')

            # 确保 uploads 目录存在
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

            # 确保文件名有正确后缀
            if not display_name.lower().endswith(suffix):
                display_name += suffix

            # 复制文件到 uploads 目录
            dest_path = UPLOAD_DIR / f"{file_id}_{display_name}"
            shutil.copy2(str(src), str(dest_path))

            file_size = dest_path.stat().st_size

            # 注册到 uploaded_files
            file_info = {
                "file_id": file_id,
                "name": display_name,
                "path": str(dest_path.absolute()),
                "size": file_size,
                "mime_type": mime_type,
                "type": "image" if mime_type.startswith("image/") else "file",
            }
            uploaded_files[file_id] = file_info

            download_url = f"/api/files/{file_id}/download"

            logger.info(f"文件已注册到下载系统: file_id={file_id}, name={display_name}, size={file_size}")

            return {
                "success": True,
                "file_id": file_id,
                "file_name": display_name,
                "file_size": file_size,
                "download_url": download_url,
                "message": f"文件已注册，用户可通过 {download_url} 下载",
            }

        except ImportError as e:
            logger.error(f"无法导入 main 模块: {e}")
            return {"success": False, "error": f"无法访问下载系统: {e}"}
        except Exception as e:
            logger.error(f"注册下载文件失败: {e}")
            return {"success": False, "error": f"注册失败: {str(e)}"}
