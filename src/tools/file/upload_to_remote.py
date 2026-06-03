"""远程文件上传工具

支持将文件上传到 SMB/FTP 服务器
"""

import os
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.config.settings import settings


class UploadToRemoteInput(BaseModel):
    """上传文件到远程参数"""
    file_path: str = Field(..., description="要上传的本地文件路径")
    remote_path: str = Field(..., description="远程服务器路径")
    connection_type: str = Field(..., description="连接类型: smb 或 ftp")
    filename: Optional[str] = Field(None, description="上传后的文件名（可选，默认使用原文件名）")
    server_host: str = Field(..., description="服务器地址")
    server_port: int = Field(..., description="服务器端口")
    username: str = Field(..., description="连接用户名")
    password: str = Field(..., description="连接密码")
    domain: Optional[str] = Field(None, description="SMB 域（可选）")


class SMBUploader:
    """SMB 文件上传器"""

    @staticmethod
    def upload(
        server_host: str,
        server_port: int,
        username: str,
        password: str,
        remote_path: str,
        local_file_path: str,
        filename: str,
        domain: str = None
    ) -> Dict[str, Any]:
        """
        上传文件到 SMB 服务器

        Args:
            server_host: SMB 服务器地址
            server_port: SMB 端口
            username: 用户名
            password: 密码
            remote_path: 远程目录路径
            local_file_path: 本地文件路径
            filename: 保存的文件名
            domain: 域（可选）

        Returns:
            上传结果
        """
        try:
            from smb.SMBConnection import SMBConnection
        except ImportError:
            return {
                "success": False,
                "error": "缺少 SMB 依赖库，请安装: pip install pysmb",
                "debug": "ImportError: No module named 'smb'"
            }

        try:
            # 读取本地文件
            file_size = os.path.getsize(local_file_path)
            logger.info(f"SMB上传: {filename} ({file_size} bytes) -> {server_host}:{remote_path}")

            # 创建 SMB 连接
            conn = SMBConnection(
                username=username,
                password=password,
                my_name='client',
                remote_name=server_host.split('.')[0],
                domain=domain or '',
                use_ntlm_v2=True
            )

            # 连接服务器
            if not conn.connect(server_host, server_port):
                return {
                    "success": False,
                    "error": "SMB 服务器连接失败",
                    "debug": f"无法连接到 {server_host}:{server_port}"
                }

            # 共享路径处理（例如: //server/share/path -> share: /path）
            # 假设 remote_path 格式为 /share/folder 或 share/folder
            path_parts = remote_path.strip('/').split('/', 1)
            share_name = path_parts[0]
            folder_path = f"/{path_parts[1]}" if len(path_parts) > 1 else "/"

            # 上传文件
            remote_file_path = f"{folder_path}/{filename}".replace('//', '/')

            with open(local_file_path, 'rb') as local_file:
                conn.storeFile(
                    share_name,
                    remote_file_path,
                    local_file
                )

            conn.close()

            return {
                "success": True,
                "message": f"文件已成功上传到 SMB 服务器",
                "remote_path": f"//{server_host}/{share_name}{remote_file_path}",
                "file_size": file_size,
                "filename": filename
            }

        except Exception as e:
            logger.error(f"SMB 上传失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": "SMB 文件上传失败",
                "debug": str(e)
            }


class FTPUploader:
    """FTP 文件上传器"""

    @staticmethod
    def upload(
        server_host: str,
        server_port: int,
        username: str,
        password: str,
        remote_path: str,
        local_file_path: str,
        filename: str,
        use_ftps: bool = False
    ) -> Dict[str, Any]:
        """
        上传文件到 FTP 服务器

        Args:
            server_host: FTP 服务器地址
            server_port: FTP 端口
            username: 用户名
            password: 密码
            remote_path: 远程目录路径
            local_file_path: 本地文件路径
            filename: 保存的文件名
            use_ftps: 是否使用 FTPS (FTP over SSL/TLS)

        Returns:
            上传结果
        """
        try:
            from ftplib import FTP, FTP_TLS
        except ImportError:
            return {
                "success": False,
                "error": "缺少 FTP 依赖库",
                "debug": "ImportError: No module named 'ftplib'"
            }

        try:
            file_size = os.path.getsize(local_file_path)
            logger.info(f"FTP上传: {filename} ({file_size} bytes) -> {server_host}:{remote_path}")

            # 创建 FTP 连接
            if use_ftps:
                conn = FTP_TLS()
            else:
                conn = FTP()

            conn.connect(server_host, server_port)
            conn.login(username, password)

            # 如果是 FTPS，升级连接
            if use_ftps:
                conn.prot_p()

            # 切换到远程目录
            if remote_path and remote_path != '/':
                try:
                    conn.cwd(remote_path)
                except Exception as e:
                    logger.warning(f"切换目录失败，尝试创建: {remote_path}")
                    # 尝试创建目录
                    dirs_to_create = remote_path.strip('/').split('/')
                    current_path = ''
                    for dir_name in dirs_to_create:
                        current_path += '/' + dir_name
                        try:
                            conn.cwd(current_path)
                        except:
                            conn.mkd(current_path)
                            conn.cwd(current_path)

            # 上传文件
            remote_file_path = filename
            with open(local_file_path, 'rb') as local_file:
                conn.storbinary(f'STOR {remote_file_path}', local_file)

            conn.quit()

            return {
                "success": True,
                "message": f"文件已成功上传到 FTP 服务器",
                "remote_path": f"ftp://{server_host}:{server_port}{remote_path}/{filename}",
                "file_size": file_size,
                "filename": filename
            }

        except Exception as e:
            logger.error(f"FTP 上传失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": "FTP 文件上传失败",
                "debug": str(e)
            }


class UploadToRemoteTool(BaseTool):
    """远程文件上传工具

    将本地文件或上传的文件上传到 SMB/FTP 服务器
    """

    name: str = "upload_to_remote"
    description: str = "将文件上传到 SMB 或 FTP 服务器。如果目标路径的凭据未配置，工具会返回凭据配置链接，用户完成配置后可继续上传。"
    display_name: str = "上传文件到远程"
    category: str = "file"
    InputModel = UploadToRemoteInput

    def __init__(self):
        super().__init__()

    def _resolve_file_path(self, file_path: str) -> tuple[str, str]:
        """
        解析文件路径

        Args:
            file_path: 文件路径

        Returns:
            (绝对路径, 文件名)
        """
        # 如果是相对路径，尝试在 uploads 目录查找
        if not os.path.isabs(file_path):
            # 尝试多个可能的目录（新路径优先，旧路径向后兼容）
            possible_paths = [
                os.path.join(settings.storage.uploads_dir, file_path),  # 新目录: storage/uploads
                os.path.join('uploads', file_path),  # 旧目录（向后兼容）
                os.path.join('test_uploads', file_path),
                file_path  # 直接使用
            ]

            for path in possible_paths:
                if os.path.isfile(path):
                    abs_path = os.path.abspath(path)
                    return abs_path, os.path.basename(abs_path)

            # 如果都找不到，返回第一个
            abs_path = os.path.abspath(possible_paths[0])
            return abs_path, os.path.basename(abs_path)
        else:
            abs_path = os.path.abspath(file_path)
            return abs_path, os.path.basename(abs_path)

    async def execute(self, user_id: str = None, **kwargs) -> Dict[str, Any]:
        """
        执行远程文件上传

        Args:
            user_id: 用户ID
            **kwargs: 工具参数

        Returns:
            上传结果
        """
        file_path = kwargs.get('file_path')
        remote_path = kwargs.get('remote_path')
        connection_type = kwargs.get('connection_type')
        filename = kwargs.get('filename')
        server_host = kwargs.get('server_host')
        server_port = kwargs.get('server_port')
        username = kwargs.get('username')
        password = kwargs.get('password')
        domain = kwargs.get('domain')

        if not file_path or not remote_path or not connection_type:
            return {
                "success": False,
                "error": "缺少必需参数: file_path, remote_path, connection_type",
                "debug": f"提供的参数: {kwargs}"
            }

        if connection_type not in ['smb', 'ftp']:
            return {
                "success": False,
                "error": "不支持的连接类型，必须是 'smb' 或 'ftp'",
                "debug": f"连接类型: {connection_type}"
            }

        if not server_host or not server_port or not username or not password:
            return {
                "success": False,
                "error": "缺少连接参数: server_host, server_port, username, password",
                "debug": f"提供的参数: {kwargs}"
            }

        # 解析文件路径
        abs_file_path, original_filename = self._resolve_file_path(file_path)

        # 验证文件是否存在
        if not os.path.isfile(abs_file_path):
            return {
                "success": False,
                "error": "文件不存在",
                "debug": f"文件路径: {abs_file_path}"
            }

        final_filename = filename or original_filename

        if connection_type == 'smb':
            return SMBUploader.upload(
                server_host=server_host,
                server_port=server_port,
                username=username,
                password=password,
                remote_path=remote_path,
                local_file_path=abs_file_path,
                filename=final_filename,
                domain=domain
            )
        else:
            return FTPUploader.upload(
                server_host=server_host,
                server_port=server_port,
                username=username,
                password=password,
                remote_path=remote_path,
                local_file_path=abs_file_path,
                filename=final_filename
            )
