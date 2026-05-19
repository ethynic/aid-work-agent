#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文本文件生成工具

支持 Agent 生成文本类文件（Markdown、HTML、TXT、CSV、JSON、XML、CSS、JS 等）。
写入磁盘后自动注册到下载系统，返回可下载 URL。
"""

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class FileWriteInput(BaseModel):
    """文本文件生成参数"""

    content: str = Field(
        ...,
        description="文件内容文本。需符合对应文件类型的语法规范（如 Markdown、HTML、JSON 等）",
    )
    file_path: Optional[str] = Field(
        None,
        description="目标文件路径（含文件名和后缀），如 'output/report.md'。"
        "支持绝对路径和相对路径，Windows 和 Linux 路径均可。"
        "不提供时自动写入系统临时目录",
    )
    file_extension: Optional[str] = Field(
        None,
        description="文件后缀名（不含点号），如 'md'、'html'、'json'。"
        "仅在 file_path 未提供时使用，默认 'txt'。"
        "如果 file_path 已提供则忽略此参数",
    )
    encoding: Optional[str] = Field(None, description="文件编码，默认 UTF-8")
    overwrite: Optional[bool] = Field(
        False, description="是否覆盖已存在的文件，默认 False（已存在时返回错误）"
    )
    register_download: Optional[bool] = Field(
        True, description="是否自动注册到下载系统（生成下载链接），默认 True"
    )
    display_name: Optional[str] = Field(
        None,
        description="注册下载时的显示文件名（可选），默认使用 file_path 中的文件名。"
        "file_path 未提供时建议设置此参数，否则使用自动生成的临时文件名",
    )


# 文本文件后缀白名单
TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".mdx",
    ".html",
    ".htm",
    ".txt",
    ".text",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".xsd",
    ".xsl",
    ".yaml",
    ".yml",
    ".css",
    ".scss",
    ".less",
    ".js",
    ".mjs",
    ".ts",
    ".sql",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".toml",
    ".svg",
    ".rtf",
}

# 禁止的后缀（可执行/脚本）
FORBIDDEN_EXTENSIONS = {
    ".exe",
    ".msi",
    ".dll",
    ".com",
    ".bat",
    ".cmd",
    ".ps1",
    ".py",
    ".pyc",
    ".pyo",
    ".sh",
    ".bash",
    ".zsh",
    ".php",
    ".jsp",
    ".asp",
    ".aspx",
}

# MIME 类型映射
MIME_MAP = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/jsonl",
    ".xml": "application/xml",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".svg": "image/svg+xml",
}


def _resolve_upload_dir(tenant_id: Optional[str], user_id: Optional[str]) -> Path:
    """根据 tenant_id 和 user_id 确定文件存储目录"""
    from src.main import UPLOAD_DIR

    if tenant_id and user_id:
        upload_dir = UPLOAD_DIR / tenant_id / user_id
    elif tenant_id:
        upload_dir = UPLOAD_DIR / tenant_id
    elif user_id:
        upload_dir = UPLOAD_DIR / user_id
    else:
        upload_dir = UPLOAD_DIR / "conversation"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


class FileWriteTool(BaseTool):
    """文本文件生成工具"""

    name = "file_write"
    description = """生成文本文件并注册到下载系统。支持 Markdown、HTML、TXT、CSV、JSON、XML、YAML、CSS、JS 等文本格式。
根据文件后缀名自动处理编码和格式。生成后自动创建下载链接，用户可在前端下载。
file_path 可选：不提供时自动写入系统临时目录并通过 file_extension 指定格式。
如果目标目录不存在会自动创建。默认不覆盖已存在的文件。支持 Windows 和 Linux 路径格式。"""
    display_name = "生成文本文件"
    category = "file"
    InputModel = FileWriteInput

    usage_guide = """生成文本文件时：
方式一（推荐，简单）：只传内容和格式
  file_write(content="完整内容", file_extension="md")
  → 自动生成临时文件，用户可直接下载

方式二（控制路径）：指定完整路径
  file_write(file_path="output/report.md", content="完整内容")
  → 文件保存到 storage/output/report.md

注意：
- 两种方式都会自动注册下载，用户都能在前端下载，无需再调 register_download_file
- 确保 content 符合文件后缀对应的语法（如 JSON 格式正确、HTML 标签闭合等）
- 如需覆盖已有文件，设置 overwrite=True
- 路径支持 Windows 和 Linux 格式"""

    def __init__(self):
        self._user_id: Optional[str] = None
        self._tenant_id: Optional[str] = None

    def set_user_id(self, user_id: str):
        self._user_id = user_id

    def set_tenant_id(self, tenant_id: str):
        self._tenant_id = tenant_id

    def get_display_name(self, tool_args=None) -> str:
        base = self.display_name
        if tool_args:
            path = tool_args.get("file_path") or ""
            if path:
                filename = Path(path).name
                return f"{base}「{filename}」"
        return base

    def _resolve_and_validate_path(self, file_path: str) -> Path:
        """解析并验证文件路径，兼容 Windows 和 Linux

        先将相对路径挂在允许的输出目录下，再解析规范化，
        最后检查最终路径是否在允许范围内，防止目录穿越攻击。
        """
        p = Path(file_path)
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        allowed_base = (project_root / "storage" / "output").resolve()

        if not p.is_absolute():
            p = allowed_base / p

        # resolve() 会规范化 .. 和符号链接，得到最终的真实路径
        p = p.resolve()

        # 安全检查：确保解析后的路径在允许的输出目录内
        try:
            p.relative_to(allowed_base)
        except ValueError:
            raise ValueError(
                f"文件路径超出允许范围，文件只能生成在 {allowed_base} 目录下。"
                f"请使用不含 '..' 的相对路径，如 'report.md' 或 'output/report.md'"
            )

        filename = p.name
        forbidden_chars = set('<>:"|?*\0')
        if any(c in forbidden_chars for c in filename):
            raise ValueError(f"文件名包含非法字符: {filename}")

        return p

    def _register_download(
        self, file_path: Path, display_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """将生成的文件注册到下载系统"""
        from src.core.redis_client import redis_client

        file_id = f"file_{uuid.uuid4().hex[:12]}"
        suffix = file_path.suffix.lower()

        if not display_name:
            display_name = file_path.name

        if not display_name.lower().endswith(suffix):
            display_name += suffix

        mime_type = MIME_MAP.get(suffix, "text/plain")

        upload_dir = _resolve_upload_dir(self._tenant_id, self._user_id)
        dest_path = upload_dir / f"{file_id}{suffix}"
        shutil.copy2(str(file_path), str(dest_path))

        file_size = dest_path.stat().st_size

        file_info = {
            "file_id": file_id,
            "name": display_name,
            "path": str(dest_path.absolute()),
            "size": file_size,
            "mime_type": mime_type,
            "type": "file",
        }

        key = redis_client.make_key("uploaded_file", file_id)
        for field, value in file_info.items():
            redis_client.hset(key, field, value)
        redis_client.expire(key, 86400)

        download_url = f"/api/files/{file_id}/download"

        return {
            "success": True,
            "file_id": file_id,
            "file_name": display_name,
            "file_size": file_size,
            "download_url": download_url,
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        content = kwargs.get("content", "")
        file_path = kwargs.get("file_path")
        file_extension = kwargs.get("file_extension")
        encoding = kwargs.get("encoding") or "utf-8"
        overwrite = kwargs.get("overwrite", False)
        register_download = kwargs.get("register_download", True)
        display_name = kwargs.get("display_name")

        # 1. 参数校验
        if not content:
            return {"success": False, "error": "未提供文件内容"}

        # 2. 内容大小检查
        content_size = len(content.encode(encoding))
        if content_size > 10 * 1024 * 1024:
            return {
                "success": False,
                "error": f"文件内容过大 ({content_size} 字节)，上限 10MB",
            }

        try:
            # 3. 路径解析
            if file_path:
                path = self._resolve_and_validate_path(file_path)
            else:
                ext = file_extension or "txt"
                if ext.startswith("."):
                    ext = ext[1:]
                fd, temp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="agent_")
                os.close(fd)
                path = Path(temp_path)

            # 4. 后缀检查
            suffix = path.suffix.lower()
            if suffix in FORBIDDEN_EXTENSIONS:
                if not file_path and path.exists():
                    path.unlink(missing_ok=True)
                return {"success": False, "error": f"不允许生成 {suffix} 类型的文件"}

            if suffix not in TEXT_EXTENSIONS:
                logger.warning(f"文件后缀 {suffix} 不在文本白名单中，将以纯文本写入")

            # 5. 覆盖检查
            if file_path and path.exists() and not overwrite:
                return {
                    "success": False,
                    "error": f"文件已存在: {path}。如需覆盖请设置 overwrite=True",
                }

            # 6. JSON 格式校验
            if suffix == ".json":
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    if not file_path and path.exists():
                        path.unlink(missing_ok=True)
                    return {"success": False, "error": f"JSON 格式错误: {e}"}

            # 7. 创建目录并写入文件
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)

            file_size = path.stat().st_size
            logger.info(f"文件已生成: {path} ({file_size} bytes)")

            # 8. 构建返回结果
            result = {
                "success": True,
                "file_path": str(path),
                "file_name": path.name,
                "file_size": file_size,
                "encoding": encoding,
                "is_temp": not bool(file_path),
                "message": f"文件已生成: {path.name}",
            }

            # 9. 注册到下载系统
            if register_download:
                download_info = self._register_download(path, display_name)
                if download_info.get("success"):
                    result["download_url"] = download_info["download_url"]
                    result["file_id"] = download_info["file_id"]
                    result["download_file_name"] = download_info.get(
                        "file_name", path.name
                    )
                else:
                    logger.warning(f"注册下载失败: {download_info.get('error')}")
                    result["download_warning"] = "文件已生成但注册下载失败"

            return result

        except Exception as e:
            logger.error(f"生成文件失败: {e}")
            return {"success": False, "error": f"生成文件失败: {str(e)}"}
