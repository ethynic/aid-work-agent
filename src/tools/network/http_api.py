import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools._helpers import truncate_text
from src.tools._spill import spill_large_content
from src.tools.base import BaseTool
from src.utils import sanitize_error_info
from src.core.temp_logger import tlog

# 单个文件最大 20MB
MAX_FILE_SIZE = 20 * 1024 * 1024


class HttpApiInput(BaseModel):
    """通用 HTTP API 调用参数"""

    method: str = Field(
        default="GET",
        description="HTTP 方法：GET、POST、PUT、DELETE、PATCH",
    )
    url: str = Field(
        ...,
        description="请求 URL，支持包含路径参数的完整 URL",
    )
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="请求头字典，键值对形式。支持 ${ENV_VAR} 凭据占位符。",
    )
    query_params: Optional[Dict[str, str]] = Field(
        default=None,
        description="URL 查询参数，如 {'page': '1', 'size': '10'}",
    )
    body: Optional[Any] = Field(
        default=None,
        description="请求体（JSON 对象或字符串），用于 POST/PUT/PATCH 请求",
    )
    form_data: Optional[Dict[str, str]] = Field(
        default=None,
        description="表单数据（application/x-www-form-urlencoded），与 body 互斥",
    )
    files: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "文件上传，格式为 {'字段名': '文件路径'}，"
            "自动以 multipart/form-data 发送。与 body 互斥。"
        ),
    )
    timeout: Optional[int] = Field(
        default=30,
        description="请求超时时间（秒），默认 30 秒",
    )
    follow_redirects: Optional[bool] = Field(
        default=True,
        description="是否跟随重定向，默认 true",
    )


class HttpApiTool(BaseTool):
    """通用 HTTP API 调用工具"""

    name = "http_api"
    description = (
        "调用外部 HTTP API（GET/POST/PUT/DELETE/PATCH、文件上传），"
        "${VAR_NAME} 替换环境变量。调用前先查上下文是否已有结果。"
    )
    usage_guide = """\
## http_api 工具使用指南

### 调用决策：上下文已有结果时是否复用
调用前先看本会话是否已有相同 API（同 URL 同参数）的近期结果，按数据特性决定：

1. 历史/详情类（订单商品明细、交易记录、基础资料、已发生事件）：数据产生即固定
   → 同会话已查过，直接复用上下文结果，不重复调用
2. 实时/状态类（当前状态、库存、价格、位置、余额）：随时间变化
   → 每次询问都应调用获取最新值
3. 终态判定：若上下文已显示实体进入终态（订单已完成、流程已关闭、物流已签收）
   → 该实体后续所有查询无需再调，终态不可逆

不确定数据属于哪类时，倾向于调用（宁可多调一次，不要用陈旧数据回答）。

### 文件上传
使用 files 参数上传文件，自动以 multipart/form-data 编码发送：
```
files: {"file": "/path/to/document.pdf"}
```
也可以与 form_data 同时使用，实现带额外字段的文件上传。

### 常见认证模式
1. Bearer Token: `{"Authorization": "Bearer ${API_TOKEN}"}`
2. API Key Header: `{"X-API-Key": "${API_KEY}"}`
3. Basic Auth: `{"Authorization": "Basic ${BASIC_AUTH}"}`"""
    display_name = "HTTP API 调用"
    category = "network"
    InputModel = HttpApiInput

    def get_display_name(self, tool_args=None) -> str:
        if tool_args:
            method = tool_args.get("method", "GET")
            url = tool_args.get("url", "")
            if url:
                display_url = re.sub(r"\$\{[^}]+\}", "***", url)
                return f"HTTP {method} {display_url}"
        return self.display_name

    async def execute(self, **kwargs) -> Dict[str, Any]:
        method = kwargs.get("method", "GET").upper()
        url = kwargs.get("url", "")
        headers = kwargs.get("headers")
        query_params = kwargs.get("query_params")
        body = kwargs.get("body")
        form_data = kwargs.get("form_data")
        files = kwargs.get("files")
        timeout = kwargs.get("timeout", 30)
        follow_redirects = kwargs.get("follow_redirects", True)

        if not url:
            return {"success": False, "error": "URL 不能为空"}

        if files and body is not None:
            return {"success": False, "error": "files 和 body 不能同时使用"}

        # ${ENV_VAR} 替换
        url = _substitute_env_vars(url)
        if headers:
            headers = {k: _substitute_env_vars(v) for k, v in headers.items()}
        if query_params:
            query_params = {k: _substitute_env_vars(v) for k, v in query_params.items()}

        # 准备文件上传
        opened_files: List = []
        httpx_files = None
        if files:
            httpx_files, opened_files = self._prepare_files(files)
            if httpx_files is None:
                return {"success": False, "error": "文件准备失败"}

        # 构建请求参数
        request_kwargs: Dict[str, Any] = {}
        if headers:
            request_kwargs["headers"] = headers
        if query_params:
            request_kwargs["params"] = query_params
        if httpx_files is not None:
            # multipart/form-data：files + 可选 data
            request_kwargs["files"] = httpx_files
            if form_data:
                request_kwargs["data"] = form_data
        elif form_data:
            request_kwargs["data"] = form_data
        elif body is not None:
            request_kwargs["json"] = body

        # 执行请求
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=follow_redirects,
            ) as client:
                response = await client.request(method, url, **request_kwargs)
                return _parse_response(response)

        except httpx.TimeoutException:
            return {"success": False, "error": f"请求超时（{timeout}秒）"}
        except httpx.ConnectError as e:
            return {"success": False, "error": f"连接失败: {sanitize_error_info(str(e))}"}
        except Exception as e:
            return {"success": False, "error": f"请求异常: {sanitize_error_info(str(e))}"}
        finally:
            for f in opened_files:
                try:
                    f.close()
                except Exception:
                    pass

    @staticmethod
    def _prepare_files(
        files: Dict[str, str],
    ) -> tuple:
        """将文件路径字典转为 httpx files 参数格式。

        Args:
            files: {'字段名': '文件路径'} 字典

        Returns:
            (httpx_files_dict, opened_file_handles) 元组。
            调用方负责在请求完成后关闭文件句柄。
        """
        httpx_files: Dict[str, Any] = {}
        opened: List = []

        for field_name, file_path in files.items():
            path = _resolve_file_path(file_path)
            if not path:
                for f in opened:
                    f.close()
                return None, []

            # 检查文件大小
            try:
                size = path.stat().st_size
                if size > MAX_FILE_SIZE:
                    logger.warning(f"文件过大: {path} ({size} bytes > {MAX_FILE_SIZE})")
                    for f in opened:
                        f.close()
                    return None, []
            except OSError:
                for f in opened:
                    f.close()
                return None, []

            try:
                f = open(path, "rb")
                opened.append(f)
                filename = path.name
                httpx_files[field_name] = (filename, f)
            except OSError as e:
                logger.warning(f"无法打开文件 {path}: {e}")
                for fh in opened:
                    fh.close()
                return None, []

        return httpx_files, opened


def _resolve_file_path(file_path: str) -> Optional[Path]:
    """解析文件路径，支持绝对路径和相对路径。

    解析顺序：
    1. 绝对路径直接使用
    2. 相对路径先尝试当前工作目录
    3. 再尝试项目根目录
    """
    path = Path(file_path)
    if path.is_absolute() and path.exists():
        return path

    if not path.is_absolute():
        # 尝试当前工作目录
        if path.exists():
            return path

        # 尝试项目根目录
        project_root = Path(__file__).resolve().parent.parent.parent
        candidate = project_root / file_path
        if candidate.exists():
            return candidate

    logger.warning(f"文件不存在: {file_path}")
    return None


def _substitute_env_vars(text: str) -> str:
    """替换字符串中的 ${ENV_VAR} 为环境变量值"""
    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            logger.warning(f"环境变量 {var_name} 未设置。")
            tlog(
                "环境变量",
                "环境变量 {var_name} 未设置",
                environ=os.environ,
            )
            return match.group(0)
        return value

    return re.sub(r"\$\{([^}]+)\}", replace, text)


def _safe_url_for_meta(response: httpx.Response) -> str:
    """提取响应 URL 的「安全部分」写入落盘 meta。

    落盘 meta 仅用于排查（哪个接口的大响应被落盘了），不需要 query/fragment。
    而 response.url 是 httpx 合并 query_params 后的完整 URL，此时 ${API_KEY} 等
    占位符已在上游被替换为真实凭证，直接写入会泄漏到临时文件。因此这里只保留
    scheme://host[:port]/path，剥离 query 与 fragment 与 userinfo（user:pass@），
    杜绝凭证泄漏。

    任何异常或非 http(s) URL 退化为空串——宁可不写排查信息，也不冒泄漏风险。
    """
    try:
        url = response.url
        scheme = url.scheme
        host = url.host
        # 仅 http/https 才有意义；host 为空说明 URL 不规范（如 file://、data:）
        if scheme not in ("http", "https") or not host:
            return ""
        # host 在 httpx 里是 str，但保险起见统一转 str（个别版本返回 bytes）
        host = str(host)
        port = url.port
        # 显式非默认端口才拼接（避免把 443/80 写出来制造噪声）
        if port and not (
            (scheme == "https" and port == 443)
            or (scheme == "http" and port == 80)
        ):
            authority = f"{host}:{port}"
        else:
            authority = host
        path = url.path or "/"
        return f"{scheme}://{authority}{path}"
    except Exception:
        return ""


def _parse_response(response: httpx.Response) -> Dict[str, Any]:
    """解析 HTTP 响应

    成功响应（2xx）下，若序列化内容超 5000 字符，则完整内容落盘到临时文件，
    返回截断预览 + file_path + full_size，供 LLM 用 read/grep 回读（消除信息黑洞）；
    小响应（≤5000）保持原样返回，不落盘，零回归。
    错误响应（非 2xx）不落盘，沿用 error 截断到 500 的既有逻辑；错误响应的 data
    也会截断（避免大错误体灌入上下文）。
    """
    status_code = response.status_code
    success = 200 <= status_code < 300

    result: Dict[str, Any] = {
        "success": success,
        "status_code": status_code,
    }

    # 错误响应：data 走截断通道（不落盘），保证错误大体不灌入上下文。
    # 仅成功响应才考虑落盘，因此成功/错误两条路径分开处理。
    if success:
        try:
            data = response.json()
            # 序列化后判断是否超长。小响应（≤5000）保留原始 JSON 对象语义，
            # 不转字符串——这是 Phase 2 已确立的契约。
            serialized = json.dumps(data, ensure_ascii=False)
            if len(serialized) > 5000:
                # 大响应：完整内容落盘，返回截断预览 + file_path
                spill = spill_large_content(
                    serialized,
                    prefix="httpapi_response_",
                    suffix=".json",
                    meta={"url": _safe_url_for_meta(response)},
                )
                result["data"] = spill["preview"]
                result["truncated"] = True
                result["file_path"] = spill["file_path"]
                result["full_size"] = spill["full_size"]
            else:
                # 小响应：原样返回原始 JSON 对象，不落盘
                result["data"] = data
        except Exception:
            text = response.text
            if len(text) > 5000:
                # 大文本响应：落盘（suffix=.txt）
                spill = spill_large_content(
                    text,
                    prefix="httpapi_response_",
                    suffix=".txt",
                    meta={"url": _safe_url_for_meta(response)},
                )
                result["data"] = spill["preview"]
                result["truncated"] = True
                result["file_path"] = spill["file_path"]
                result["full_size"] = spill["full_size"]
            else:
                # 小文本响应：原样返回，不落盘
                result["data"] = text
    else:
        # 错误响应：不落盘，data 截断到 5000（与成功小响应一致的截断上限），
        # 后续 error 字段会进一步截断到 500。
        try:
            data = response.json()
            serialized = json.dumps(data, ensure_ascii=False)
            if len(serialized) > 5000:
                truncated_data, truncated = truncate_text(serialized, limit=5000)
                result["data"] = truncated_data
                if truncated:
                    result["truncated"] = True
            else:
                result["data"] = data
        except Exception:
            text = response.text
            truncated_text, truncated = truncate_text(text, limit=5000)
            result["data"] = truncated_text
            if truncated:
                result["truncated"] = True

    if not success:
        error_data, _ = truncate_text(str(result["data"]), limit=500, suffix="")
        result["error"] = f"HTTP {status_code}: {error_data}"

    return result
