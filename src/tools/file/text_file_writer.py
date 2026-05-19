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

    content: Optional[str] = Field(
        None,
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
    generate_prompt: Optional[str] = Field(
        None,
        description="内容生成指令。提供此参数时，工具内部调用 LLM 生成文件内容，无需再提供 content。"
        "应包含：角色定义、任务描述、输入素材、输出格式要求、质量标准。"
        "适用于生成 HTML 报告、Markdown 文档等长文本场景。",
    )
    content_type: Optional[str] = Field(
        None,
        description="内容类型标签，辅助 LLM 生成。已知预设："
        "report、article、outline、email、market_report。"
        "仅在使用 generate_prompt 时生效。",
    )
    language: Optional[str] = Field(
        "zh",
        description="生成内容的语言，默认中文",
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
    description = """生成文本文件并注册到下载系统。支持 Markdown、HTML、TXT、CSV、JSON 等格式。

两种使用方式：
1. 直接提供内容：file_write(content="完整内容", file_path="report.md")
2. 内部生成内容：file_write(file_path="report.html", generate_prompt="生成指令...")
   → 工具内部调用 LLM 生成内容，直接写入文件，不暴露生成内容到对话上下文

方式 2 适合生成长文本（HTML 报告、长文档等），避免生成内容占用过多上下文窗口。
生成后自动创建下载链接，用户可在前端下载/预览。"""
    display_name = "生成文本文件"
    category = "file"
    InputModel = FileWriteInput

    usage_guide = """生成文本文件时：

方式一（直接写入）：提供现成内容
  file_write(content="完整内容", file_extension="md")
  → 自动生成临时文件，用户可直接下载

方式二（内部生成）：让工具调用 LLM 生成内容
  file_write(file_path="report.html", generate_prompt="根据以下材料生成封面页 HTML：...")
  → 工具内部调 LLM 生成，内容不进入对话上下文，节省 token

方式三（控制路径 + 内部生成）：
  file_write(file_path="output/report.html", generate_prompt="...", content_type="report")
  → 指定路径和内容类型辅助生成

注意：
- 所有方式都会自动注册下载，用户都能在前端下载/预览
- 方式 2/3 的 generate_prompt 越详细，生成质量越高
- 如果同时提供 content 和 generate_prompt，优先使用 content（直接写入）
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

    async def _generate_content(
        self, prompt: str, content_type: str, language: str
    ) -> str:
        """内部调用 LLM 生成内容"""
        from src.llm.gateway import llm_gateway

        system_prompt = (
            "你是一个专业的内容生成助手。请严格遵循用户指令生成高质量内容。\n"
            "规则：\n"
            "1. 直接输出最终内容，不要添加说明性文字\n"
            "2. 确保输出符合指定的文件格式要求\n"
            "3. 内容必须完整，不要截断或使用省略号\n"
        )

        type_guidance = {
            "report": "\n你是一个专业的报告撰写专家。请生成结构清晰的专业报告。",
            "article": "\n你是一个资深的内容创作者。请撰写高质量的内容。",
            "email": "\n你是一个专业的商务邮件撰写专家。请撰写专业、自然的商务邮件。",
            "market_report": "\n你是一个专业的市场分析师。请生成专业的市场分析报告。",
            "outline": "\n你是一个专业的内容策划师。请生成结构清晰的大纲。",
        }

        if content_type in type_guidance:
            system_prompt += type_guidance[content_type]

        language_map = {
            "zh": "请使用简体中文",
            "en": "Please respond in English",
            "ru": "Пожалуйста, отвечайте на русском языке",
            "de": "Bitte antworten Sie auf Deutsch",
            "ja": "日本語でお答えください",
            "ko": "한국어로 답변해 주세요",
            "es": "Por favor responda en español",
        }
        if language in language_map:
            system_prompt += f"。{language_map[language]}。"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        response = await llm_gateway.chat(
            messages=messages, temperature=0.7, max_tokens=4096
        )

        if isinstance(response, dict):
            return response.get("content", "")
        return str(response)

    def _validate_content(self, content: str, suffix: str) -> tuple:
        """校验生成内容是否符合文件后缀要求的格式"""
        if suffix in (".html", ".htm"):
            content_lower = content.lower().strip()
            if not (
                "<html" in content_lower
                or "<!doctype" in content_lower
                or "<body" in content_lower
            ):
                return False, "生成内容不是有效的 HTML（缺少 <html> 或 <!DOCTYPE> 标签）"
        elif suffix == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError:
                return False, "生成内容不是有效的 JSON"
        elif suffix == ".csv":
            if "," not in content and "\t" not in content:
                return False, "生成内容不像是 CSV 格式（未检测到分隔符）"
        return True, ""

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
        content = kwargs.get("content")
        generate_prompt = kwargs.get("generate_prompt")
        content_type = kwargs.get("content_type") or ""
        language = kwargs.get("language") or "zh"
        file_path = kwargs.get("file_path")
        file_extension = kwargs.get("file_extension")
        encoding = kwargs.get("encoding") or "utf-8"
        overwrite = kwargs.get("overwrite", False)
        register_download = kwargs.get("register_download", True)
        display_name = kwargs.get("display_name")

        # 1. 参数互斥：content 优先
        use_generation = False
        if content:
            # 直接写入模式
            pass
        elif generate_prompt:
            # 内部生成模式
            use_generation = True
        else:
            return {"success": False, "error": "未提供文件内容（content 和 generate_prompt 至少提供一个）"}

        # 2. 路径解析
        try:
            if file_path:
                path = self._resolve_and_validate_path(file_path)
            else:
                ext = file_extension or "txt"
                if ext.startswith("."):
                    ext = ext[1:]
                fd, temp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="agent_")
                os.close(fd)
                path = Path(temp_path)
        except ValueError as e:
            return {"success": False, "error": str(e)}

        # 3. 后缀检查
        suffix = path.suffix.lower()
        if suffix in FORBIDDEN_EXTENSIONS:
            if not file_path and path.exists():
                path.unlink(missing_ok=True)
            return {"success": False, "error": f"不允许生成 {suffix} 类型的文件"}

        if suffix not in TEXT_EXTENSIONS:
            logger.warning(f"文件后缀 {suffix} 不在文本白名单中，将以纯文本写入")

        # 4. 覆盖检查
        if file_path and path.exists() and not overwrite:
            return {
                "success": False,
                "error": f"文件已存在: {path}。如需覆盖请设置 overwrite=True",
            }

        try:
            # 5. 内部生成模式：调 LLM 生成内容
            if use_generation:
                generated_content = await self._generate_with_retry(
                    generate_prompt, content_type, language, suffix
                )
                if generated_content is None:
                    # 生成失败，_generate_with_retry 已记录错误
                    return {"success": False, "error": "内容生成失败，请检查 generate_prompt 或稍后重试"}
                content = generated_content
                logger.info(f"内容生成成功: {len(content)} 字符")

            # 6. 内容大小检查
            content_size = len(content.encode(encoding))
            if content_size > 10 * 1024 * 1024:
                return {
                    "success": False,
                    "error": f"文件内容过大 ({content_size} 字节)，上限 10MB",
                }

            # 7. JSON 格式校验（直接写入模式）
            if suffix == ".json" and not use_generation:
                try:
                    json.loads(content)
                except json.JSONDecodeError as e:
                    if not file_path and path.exists():
                        path.unlink(missing_ok=True)
                    return {"success": False, "error": f"JSON 格式错误: {e}"}

            # 8. 创建目录并写入文件
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding=encoding)

            file_size = path.stat().st_size
            logger.info(f"文件已生成: {path} ({file_size} bytes)")

            # 9. 构建返回结果
            result = {
                "success": True,
                "file_path": str(path),
                "file_name": path.name,
                "file_size": file_size,
                "encoding": encoding,
                "is_temp": not bool(file_path),
                "message": f"文件已生成: {path.name}" + (" (内部生成)" if use_generation else ""),
            }

            # 10. 注册到下载系统
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

    async def _generate_with_retry(
        self, prompt: str, content_type: str, language: str, suffix: str
    ) -> Optional[str]:
        """带格式校验和重试的内容生成"""
        max_retries = 1
        current_prompt = prompt

        for attempt in range(max_retries + 1):
            try:
                generated = await self._generate_content(
                    current_prompt, content_type, language
                )
            except Exception as e:
                logger.error(f"LLM 内容生成异常 (attempt {attempt}): {e}")
                return None

            if not generated.strip():
                logger.warning(f"LLM 返回空内容 (attempt {attempt})")
                return None

            valid, error_msg = self._validate_content(generated, suffix)
            if valid:
                return generated

            if attempt < max_retries:
                current_prompt = (
                    f"{prompt}\n\n【注意】上次生成的内容格式不正确：{error_msg}。"
                    f"请确保输出符合 {suffix} 格式要求。"
                )
                logger.warning(f"内容格式校验失败，正在重试: {error_msg}")
            else:
                logger.error(f"内容格式校验失败（已重试）: {error_msg}")
                # 校验失败但仍返回内容，让调用方决定
                return generated

        return None
