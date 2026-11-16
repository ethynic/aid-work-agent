"""
OCR工具

实现图片和PDF文字识别功能，支持PaddleOCR文档解析
"""

import asyncio
import base64
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools._helpers import truncate_text
from src.tools.base import BaseTool

# 注意：settings 不在模块级导入，否则会形成
# ocr_tool -> src.config.settings -> src.config.__init__ -> src.core.agent
# -> 注册 PaddleOCRDocParsingTool -> 回到 src.tools.ocr 的循环导入。
# 在 _get_paddleocr_config() 内部按需导入即可规避（与 pdf_process_tool 的延迟导入模式一致）。


# =============================================================================
# PaddleOCR Document Parsing
# =============================================================================

PADDLEOCR_API_GUIDE_URL = "https://paddleocr.com"
DEFAULT_TIMEOUT = 600  # seconds (10 minutes)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp")


def _load_file_as_base64(file_path: str) -> str:
    """Load local file and encode as base64."""
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def _download_url_as_base64(url: str) -> str:
    """Download file from URL and encode as base64."""
    import httpx
    try:
        with httpx.Client(timeout=60) as client:
            response = client.get(url)
            response.raise_for_status()
            return base64.b64encode(response.content).decode("utf-8")
    except httpx.RequestError as e:
        raise RuntimeError(f"Failed to download file from URL: {e}")


def _detect_file_type(path_or_url: str) -> int:
    """Detect file type: 0=PDF, 1=Image."""
    path = path_or_url.lower()
    if path.startswith(("http://", "https://")):
        path = unquote(urlparse(path).path)

    if path.endswith(".pdf"):
        return 0  # PDF
    elif path.endswith(IMAGE_EXTENSIONS):
        return 1  # Image
    else:
        raise ValueError(f"Unsupported file format: {path_or_url}")


def _get_paddleocr_config() -> tuple[str, str]:
    """Get API URL and token from settings."""
    from src.config.settings import settings
    config = settings.tools.ocr

    api_url = getattr(config, 'paddleocr_api_url', os.getenv("PADDLEOCR_DOC_PARSING_API_URL", "")).strip()
    token = getattr(config, 'paddleocr_access_token', os.getenv("PADDLEOCR_ACCESS_TOKEN", "")).strip()

    if not api_url:
        raise ValueError(
            f"PADDLEOCR_DOC_PARSING_API_URL not configured. Get your API at: {PADDLEOCR_API_GUIDE_URL}"
        )
    if not token:
        raise ValueError(
            f"PADDLEOCR_ACCESS_TOKEN not configured. Get your API at: {PADDLEOCR_API_GUIDE_URL}"
        )

    # Normalize URL
    if not api_url.startswith(("http://", "https://")):
        api_url = f"https://{api_url}"
    api_path = urlparse(api_url).path.rstrip("/")
    if not api_path.endswith("/layout-parsing"):
        raise ValueError(
            "PADDLEOCR_DOC_PARSING_API_URL must be a full endpoint ending with "
            "/layout-parsing. "
            "Example: https://your-service.paddleocr.com/layout-parsing"
        )

    return api_url, token


def _make_paddleocr_request(api_url: str, token: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Make PaddleOCR document parsing API request."""
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Client-Platform": "official-skill",
    }

    timeout = float(os.getenv("PADDLEOCR_DOC_PARSING_TIMEOUT", str(DEFAULT_TIMEOUT)))

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(api_url, json=params, headers=headers)
    except httpx.TimeoutException:
        raise RuntimeError(f"API request timed out after {timeout}s")
    except httpx.RequestError as e:
        raise RuntimeError(f"API request failed: {e}")

    # Handle HTTP errors
    if resp.status_code != 200:
        error_detail = ""
        try:
            error_body = resp.json()
            if isinstance(error_body, dict):
                error_detail = str(error_body.get("errorMsg", "")).strip()
        except Exception:
            pass

        if not error_detail:
            error_detail = (resp.text[:200] or "No response body").strip()

        if resp.status_code == 403:
            raise RuntimeError(f"Authentication failed (403): {error_detail}")
        elif resp.status_code == 429:
            raise RuntimeError(f"API rate limit exceeded (429): {error_detail}")
        elif resp.status_code >= 500:
            raise RuntimeError(
                f"API service error ({resp.status_code}): {error_detail}"
            )
        else:
            raise RuntimeError(f"API error ({resp.status_code}): {error_detail}")

    # Parse response
    try:
        result = resp.json()
    except Exception:
        raise RuntimeError(f"Invalid JSON response: {resp.text[:200]}")

    # Check API-level error
    if result.get("errorCode", 0) != 0:
        raise RuntimeError(f"API error: {result.get('errorMsg', 'Unknown error')}")

    return result


def _extract_markdown_texts(result: Dict[str, Any]) -> List[str]:
    """Extract markdown.text from result.layoutParsingResults array."""
    if not isinstance(result, dict):
        raise ValueError(
            "Invalid response schema: top-level response must be an object"
        )

    raw_result = result.get("result")
    if not isinstance(raw_result, dict):
        raise ValueError("Invalid response schema: missing result object")

    pages = raw_result.get("layoutParsingResults")
    if not isinstance(pages, list):
        raise ValueError(
            "Invalid response schema: result.layoutParsingResults must be an array"
        )

    texts = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(
                f"Invalid response schema: result.layoutParsingResults[{i}] must be an object"
            )

        markdown = page.get("markdown")
        if not isinstance(markdown, dict):
            raise ValueError(
                f"Invalid response schema: result.layoutParsingResults[{i}].markdown must be an object"
            )

        text = markdown.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"Invalid response schema: result.layoutParsingResults[{i}].markdown.text must be a string"
            )
        texts.append(text)

    return texts


def paddleocr_doc_parsing(
    file_path: Optional[str] = None,
    file_url: Optional[str] = None,
    file_type: Optional[int] = None,
) -> Dict[str, Any]:
    """
    使用PaddleOCR API解析文档，直接返回markdown.text结果。

    Args:
        file_path: 本地文件路径
        file_url: 文件URL地址
        file_type: 文件类型 (0=PDF, 1=Image)

    Returns:
        {
            "success": True,
            "texts": ["page1 markdown text", "page2 markdown text", ...],
            "full_text": "所有页面markdown text用\\n\\n连接（≤5000字符，超出截断）",
            "truncated": bool,
            "message": "成功解析N页"
        }
        or on error:
        {
            "success": False,
            "error": "<脱敏一句话>"
        }
    """
    # Validate input
    if not file_path and not file_url:
        logger.warning("PaddleOCR 调用缺少 file_path/file_url 参数")
        return {
            "success": False,
            "error": "请提供文件路径或URL",
        }
    if file_type is not None and file_type not in (0, 1):
        logger.warning(f"PaddleOCR 非法 file_type={file_type}")
        return {
            "success": False,
            "error": "文件类型必须是0(PDF)或1(Image)",
        }

    # Get config
    try:
        api_url, token = _get_paddleocr_config()
    except ValueError as e:
        logger.error(f"PaddleOCR配置错误: {e}")
        return {
            "success": False,
            "error": "PaddleOCR未配置，请先配置API",
        }

    # Build request params
    params: Dict[str, Any] = {}
    try:
        resolved_file_type: Optional[int] = None

        if file_path:
            # 本地文件：读取并转为base64
            resolved_file_type = (
                file_type if file_type is not None else _detect_file_type(file_path)
            )
            params = {
                "file": _load_file_as_base64(file_path),
            }
        elif file_url:
            # URL：下载并转为base64
            resolved_file_type = file_type
            params = {
                "file": _download_url_as_base64(file_url),
            }

        if resolved_file_type is not None:
            params["fileType"] = resolved_file_type

    except (ValueError, FileNotFoundError, RuntimeError) as e:
        logger.opt(exception=True).error(f"PaddleOCR文件处理错误: {e}")
        return {
            "success": False,
            "error": "文件处理失败",
        }

    # Call API
    try:
        result = _make_paddleocr_request(api_url, token, params)
    except RuntimeError as e:
        logger.opt(exception=True).error(f"PaddleOCR API调用失败: {e}")
        return {
            "success": False,
            "error": "PaddleOCR API调用失败",
        }

    # Extract markdown.text from each page
    try:
        texts = _extract_markdown_texts(result)
    except ValueError as e:
        logger.opt(exception=True).error(f"PaddleOCR结果解析错误: {e}")
        return {
            "success": False,
            "error": "结果解析失败",
        }

    # full_text 截断到 5000 字符 + truncated 标记（统一规范 §1.4）
    full_text = "\n\n".join(texts)
    full_text, truncated = truncate_text(full_text, limit=5000)

    logger.info(f"PaddleOCR文档解析成功，共{len(texts)}页")
    return {
        "success": True,
        "texts": texts,
        "full_text": full_text,
        "truncated": truncated,
        "message": f"成功解析{len(texts)}页",
    }


# =============================================================================
# OCR Tools
# =============================================================================


class PaddleOCRDocParsingInput(BaseModel):
    """PaddleOCR文档解析参数"""
    file_url: Optional[str] = Field(None, description="文档URL地址，支持PDF或图片")
    file_path: Optional[str] = Field(None, description="本地文件路径")
    file_type: Optional[int] = Field(None, description="文件类型：0=PDF，1=图片。不填则自动检测")


class PaddleOCRDocParsingTool(BaseTool):
    """PaddleOCR文档解析工具"""

    name = "paddleocr_doc_parsing"
    description = "使用PaddleOCR解析PDF或图片文档，返回每页的markdown文本内容。支持文件路径或URL输入。"
    display_name = "解析文档"
    category = "ocr"
    InputModel = PaddleOCRDocParsingInput

    def get_display_name(self, tool_args: Optional[Dict[str, Any]] = None) -> str:
        """动态显示名，展示文件来源"""
        if tool_args:
            file_path = tool_args.get("file_path", "")
            file_url = tool_args.get("file_url", "")
            source = file_path if file_path else file_url
            if source:
                return f"解析文档「{source}」"
        return self.display_name

    def _remote_dispatch_enabled(self) -> bool:
        """远程派发开关（重工具服务设计 §3/§4.2）：HEAVY_TOOLS_MODE=remote 且本工具
        在 agent_runner.heavy_tools 白名单。本工具零 LLM（§3 核实），整体远程；
        代理语义以「类内模式分支」实现——name/description/InputModel 与本地版
        恒为同一类，schema 生成链路与 LLM 感知不变（§4.2）。"""
        from src.services.heavy_tool_service.config import is_remote_mode

        if not is_remote_mode():
            return False
        try:
            from src.config.settings import settings

            return "paddleocr_doc_parsing" in set(settings.agent_runner.heavy_tools or [])
        except Exception:
            return False

    async def _execute_remote(self, file_path: str, file_url: str, file_type) -> Dict[str, Any]:
        """整体远程执行（§4.1 同步长调用）：路径先解析为共享卷绝对路径再派发；
        重容器侧执行同一 paddleocr_doc_parsing 函数（同镜像，结果与本地执行一致）。"""
        from src.services.heavy_tool_service import client
        from src.services.heavy_tool_service.client import HeavyToolBusy, HeavyToolUnavailable
        from src.services.heavy_tool_service.config import PADDLEOCR_TIMEOUT_SECONDS

        if file_path and not os.path.exists(file_path):
            # 入参可能是 file_id/相对路径：本地解析为磁盘绝对路径（远程只认绝对路径）
            try:
                from src.tools.excel.excel_lib import ExcelFileHandler

                resolved = ExcelFileHandler.resolve_path(file_path)
                if os.path.exists(resolved):
                    file_path = resolved
            except Exception:
                pass

        try:
            return await client.execute(
                tool=self.name,
                params={
                    "file_path": os.path.abspath(file_path) if file_path else None,
                    "file_url": file_url or None,
                    "file_type": file_type,
                },
                timeout_seconds=PADDLEOCR_TIMEOUT_SECONDS,
            )
        except HeavyToolBusy as e:
            # busy 透传（§8）：message 已带「稍后重试」引导，代理不做自动立即重试
            logger.warning(f"PaddleOCR 重工具服务繁忙: {e}")
            return {"success": False, "error": str(e)}
        except HeavyToolUnavailable as e:
            # 中性失败（§8）：不回退本地执行（全页识别正是要隔离的重负载）
            logger.error(f"PaddleOCR 重工具服务不可用: {e}")
            return {"success": False, "error": "处理未完成，请稍后重试"}

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行PaddleOCR文档解析

        Args:
            file_url: 文档URL
            file_path: 本地文件路径
            file_type: 文件类型

        Returns:
            解析结果，包含每页的markdown.text
        """
        file_url = kwargs.get("file_url", "")
        file_path = kwargs.get("file_path", "")
        file_type = kwargs.get("file_type")

        if not file_url and not file_path:
            logger.warning("PaddleOCR 工具调用缺少 file_url/file_path 参数")
            return {
                "success": False,
                "error": "请提供文件URL或本地路径",
            }

        if self._remote_dispatch_enabled():
            return await self._execute_remote(file_path, file_url, file_type)

        # 同步函数 paddleocr_doc_parsing 内部用 httpx.Client 同步请求远端 API（单次可达 60s），
        # 直接调用会阻塞事件循环导致 Gunicorn worker 心跳超时被杀。必须用 asyncio.to_thread 包裹。
        return await asyncio.to_thread(
            paddleocr_doc_parsing,
            file_path=file_path if file_path else None,
            file_url=file_url if file_url else None,
            file_type=file_type,
        )