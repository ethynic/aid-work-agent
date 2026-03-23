"""
OCR工具

实现图片和PDF文字识别功能，支持PaddleOCR文档解析
"""

import base64
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, unquote

import httpx
from loguru import logger

from src.tools.base import BaseTool
from src.config.settings import settings


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
            "full_text": "所有页面markdown text用\\n\\n连接",
            "result": {原始API响应},
            "message": "成功解析N页"
        }
        or on error:
        {
            "success": False,
            "error": "错误描述",
            "debug": "详细错误信息"
        }
    """
    # Validate input
    if not file_path and not file_url:
        return {
            "success": False,
            "error": "请提供文件路径或URL",
            "debug": "INPUT_ERROR: file_path or file_url required"
        }
    if file_type is not None and file_type not in (0, 1):
        return {
            "success": False,
            "error": "文件类型必须是0(PDF)或1(Image)",
            "debug": "INPUT_ERROR: file_type must be 0 (PDF) or 1 (Image)"
        }

    # Get config
    try:
        api_url, token = _get_paddleocr_config()
    except ValueError as e:
        logger.error(f"后端日志：PaddleOCR配置错误: {e}")
        return {
            "success": False,
            "error": "PaddleOCR未配置，请先配置API",
            "debug": f"CONFIG_ERROR: {str(e)}"
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
        logger.error(f"后端日志：PaddleOCR文件处理错误: {e}")
        return {
            "success": False,
            "error": "文件处理失败",
            "debug": f"INPUT_ERROR: {str(e)}"
        }

    # Call API
    try:
        result = _make_paddleocr_request(api_url, token, params)
    except RuntimeError as e:
        logger.error(f"后端日志：PaddleOCR API调用失败: {e}")
        return {
            "success": False,
            "error": "PaddleOCR API调用失败",
            "debug": f"API_ERROR: {str(e)}"
        }

    # Extract markdown.text from each page
    try:
        texts = _extract_markdown_texts(result)
        full_text = "\n\n".join(texts)
    except ValueError as e:
        logger.error(f"后端日志：PaddleOCR结果解析错误: {e}")
        return {
            "success": False,
            "error": "结果解析失败",
            "debug": f"API_ERROR: {str(e)}"
        }

    logger.info(f"后端日志：PaddleOCR文档解析成功，共{len(texts)}页")
    return {
        "success": True,
        "texts": texts,
        "full_text": full_text,
        "result": result,
        "message": f"成功解析{len(texts)}页文档"
    }


# =============================================================================
# OCR Tools
# =============================================================================


class PaddleOCRDocParsingTool(BaseTool):
    """PaddleOCR文档解析工具"""

    name = "paddleocr_doc_parsing"
    description = "使用PaddleOCR解析PDF或图片文档，返回每页的markdown文本内容。支持文件路径或URL输入。"
    category = "ocr"
    parameters_schema = {
        "type": "object",
        "properties": {
            "file_url": {
                "type": "string",
                "description": "文档URL地址，支持PDF或图片",
            },
            "file_path": {
                "type": "string",
                "description": "本地文件路径（可选）",
            },
            "file_type": {
                "type": "integer",
                "description": "文件类型：0=PDF，1=图片。不填则自动检测",
            },
        },
        "required": [],
    }

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
            return {
                "success": False,
                "error": "请提供文件URL或本地路径",
                "debug": "INPUT_ERROR: file_url or file_path required"
            }

        return paddleocr_doc_parsing(
            file_path=file_path if file_path else None,
            file_url=file_url if file_url else None,
            file_type=file_type
        )