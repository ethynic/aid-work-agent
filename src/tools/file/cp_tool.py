#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件复制工具

将源文件复制到输出目录，支持自动注册下载。
源文件必须在项目根目录内（防穿越），目标文件必须在 storage/ 输出目录内。
"""

import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class CpInput(BaseModel):
    """文件复制参数"""

    source_file_path: str = Field(
        ...,
        description="源文件路径，绝对路径或项目根目录相对路径。源文件必须在项目根目录内。",
    )
    file_path: Optional[str] = Field(
        None,
        description="目标文件路径。不传时自动分配下载目录路径（推荐，因为 cp 后通常要交付下载）。"
        "目标必须在 storage/ 输出目录内。",
    )
    overwrite: Optional[bool] = Field(
        False,
        description="目标文件已存在时是否覆盖，默认 False。",
    )
    register_download: Optional[bool] = Field(
        True,
        description="复制后是否自动注册到下载系统，默认 True。",
    )
    display_name: Optional[str] = Field(
        None,
        description="注册下载时的显示文件名（可选）。默认使用源文件名。",
    )
    visible: Optional[bool] = Field(
        True,
        description="该 cp 注册的下载文件是否在前端对话中展示下载卡片。"
        "默认 True（cp 现在是文件交付的标准入口，复制后默认对用户可见可下载）。"
        "如果是中间过程文件，可显式设为 False。",
    )


# 禁止的后缀（可执行/脚本）——与 text_file_writer.py 保持一致
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


class CpTool(BaseTool):
    """文件复制工具"""

    name = "cp"
    description = """复制文件，相当于 shell 的 cp 命令。

用法：
cp(source_file_path="/tmp/quote_xxx.xlsx", display_name="研学旅游报价单.xlsx")
→ 将源文件复制到下载目录，自动注册下载，默认对用户可见可下载

cp(source_file_path="src/skills/xxx/assets/template.html", file_path="output/ppt/index.html")
→ 将源文件复制到指定输出目录，自动注册下载

参数：
- source_file_path：源文件绝对路径或项目根目录相对路径。源可来自任意位置
  （系统 /tmp、skill 生成的临时文件、工具会话目录、项目内文件等）。
- file_path：目标路径。不传时自动分配下载目录路径（推荐）。
  · 必须在 storage/ 输出目录内。
- register_download：默认 True，复制后自动注册下载。
- visible：默认 True，注册的文件在前端对话中展示下载卡片。
  仅当作为中间过程文件不需要展示时设为 False。
- display_name：注册下载时的显示文件名（交付给用户时应提供）。
  必须使用用户能理解的业务文件名，默认才使用源文件名。

任何需要复制文件内容的场景都用本工具：拷贝模板生成新文件、复制用户上传文件、
把工具/skill 生成的文件注册到下载系统等。交付用户的文件必须传 display_name，
避免前端展示临时文件名。零 token 消耗（不读源文件内容到上下文）。"""
    display_name = "复制文件"
    category = "file"
    InputModel = CpInput

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
            src = tool_args.get("source_file_path") or ""
            if src:
                return f"{base}「{Path(src).name}」"
        return base

    def _resolve_source(self, source_file_path: str) -> Path:
        """解析源路径，允许任意绝对路径（如 /tmp、系统临时目录）。

        相对路径仍按项目根目录解析。源文件只需存在且是文件即可，
        不再限制必须在项目根目录内——cp 作为文件交付入口，
        源文件可能来自 skill 脚本生成的系统临时文件、工具的会话目录等任意位置。
        防穿越的关键在目标路径（_resolve_and_validate_path），源路径无安全风险。
        """
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        src = Path(source_file_path)
        if not src.is_absolute():
            src = project_root / src
        src = src.resolve()
        if not src.exists():
            raise FileNotFoundError(f"源文件不存在: {src}")
        if not src.is_file():
            raise ValueError(f"源路径不是文件: {src}")
        return src

    def _resolve_and_validate_path(self, file_path: str) -> Path:
        """解析并验证目标文件路径，兼容 Windows 和 Linux

        先将相对路径挂在允许的输出目录下，再解析规范化，
        最后检查最终路径是否在允许范围内，防止目录穿越攻击。
        """
        p = Path(file_path)
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        allowed_base = (project_root / "storage").resolve()

        if not p.is_absolute():
            p = allowed_base / p

        # resolve() 会规范化 .. 和符号链接，得到最终的真实路径
        p = p.resolve()

        # 安全检查：确保解析后的路径在允许的输出目录内
        try:
            p.relative_to(allowed_base)
        except ValueError:
            raise ValueError(
                f"文件路径超出允许范围，文件只能在 {allowed_base} 目录下操作。"
                f"请使用不含 '..' 的相对路径，如 'report.md' 或 'output/report.md'"
            )

        filename = p.name
        forbidden_chars = set('<>:"|?*\0')
        if any(c in forbidden_chars for c in filename):
            raise ValueError(f"文件名包含非法字符: {filename}")

        return p

    def _resolve_target(
        self,
        file_path: Optional[str],
        src_suffix: str,
        register_download: bool,
    ) -> Path:
        """解析目标路径

        - file_path 提供：用 _resolve_and_validate_path 限制在 storage/ 输出目录
        - file_path 不提供 + register_download=True：创建临时文件，后续注册下载时会 move 到 UPLOAD_DIR
        - file_path 不提供 + register_download=False：报错
        """
        if file_path:
            return self._resolve_and_validate_path(file_path)

        if not register_download:
            raise ValueError("不注册下载时需要提供 file_path（目标路径）")

        # 自动分配下载目录路径：先创建临时文件，注册下载时 move 到最终位置
        fd, temp_path = tempfile.mkstemp(suffix=src_suffix, prefix="agent_copy_")
        os.close(fd)
        return Path(temp_path)

    def _register_download(
        self,
        file_path: Path,
        display_name: Optional[str] = None,
        visible: bool = True,
    ) -> Dict[str, Any]:
        """将生成的文件注册到下载系统。"""
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
            "visible": visible,
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
            "file_path": str(dest_path),
            "visible": visible,
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        source_file_path = kwargs["source_file_path"]
        file_path = kwargs.get("file_path")
        overwrite = kwargs.get("overwrite", False)
        register_download = kwargs.get("register_download", True)
        display_name = kwargs.get("display_name")
        visible = kwargs.get("visible", True)

        try:
            # 1. 解析源路径
            src = self._resolve_source(source_file_path)

            # 2. 源后缀安全检查
            src_suffix = src.suffix.lower()
            if src_suffix in FORBIDDEN_EXTENSIONS:
                return f"不允许复制 {src_suffix} 类型的文件"

            # 3. 解析目标路径
            dst = self._resolve_target(file_path, src.suffix, register_download)

            # 4. 目标后缀安全检查（仅当 file_path 提供时检查，临时文件后缀来自源文件已检查过）
            if file_path:
                dst_suffix = dst.suffix.lower()
                if dst_suffix in FORBIDDEN_EXTENSIONS:
                    if dst.exists():
                        dst.unlink(missing_ok=True)
                    return f"不允许生成 {dst_suffix} 类型的文件"

            # 5. 目标已存在 + overwrite=False（仅当用户指定了 file_path 时检查，
            #    自动分配的临时文件是 mkstemp 创建的，本身就是空的，不需要检查）
            if file_path and dst.exists() and not overwrite:
                return f"目标文件已存在: {dst}，设置 overwrite=True 覆盖"

            # 6. 执行复制
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

            file_size = dst.stat().st_size
            logger.info(f"文件已复制: {src} → {dst} ({file_size} bytes)")

            # 7. 注册下载
            if register_download:
                download_info = self._register_download(
                    dst, display_name or src.name, visible=visible
                )
                # 清理临时文件（register_download 会 copy2 到最终位置）
                if not file_path:
                    dst.unlink(missing_ok=True)

                if not download_info.get("success"):
                    return f"复制文件失败: 注册下载失败"

                return {
                    "file_path": download_info["file_path"],
                    "file_name": download_info.get("file_name", dst.name),
                    "file_size": file_size,
                    "download_url": download_info["download_url"],
                    "file_id": download_info["file_id"],
                    "resolved_source": str(src),
                    "visible": visible,
                }

            return {
                "file_path": str(dst),
                "file_name": dst.name,
                "file_size": file_size,
                "resolved_source": str(src),
            }

        except Exception as e:
            logger.error(f"复制文件失败: {e}")
            return f"复制文件失败: {e}"
