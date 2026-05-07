import os
import re
from typing import Any, Dict, Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool
from src.utils import sanitize_error_info


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
        description="请求头，如 {'Authorization': 'Bearer ${API_TOKEN}', 'Content-Type': 'application/json'}",
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
        "调用外部 HTTP API 接口。支持 GET/POST/PUT/DELETE/PATCH 方法，"
        "支持 JSON body、表单数据、自定义请求头。"
        "URL 和 headers 中的 ${VAR_NAME} 会被替换为环境变量值。"
    )
    usage_guide = """\
## http_api 工具使用指南

这是一个通用 HTTP 客户端工具，可以调用任何 HTTP API。

### 参数说明
- **method**: HTTP 方法（GET/POST/PUT/DELETE/PATCH），默认 GET
- **url**: 完整的请求 URL
- **headers**: 自定义请求头字典（可选）
- **query_params**: URL 查询参数字典（可选）
- **body**: JSON 请求体，可以是对象或数组（可选，POST/PUT 用）
- **form_data**: 表单数据字典（可选，与 body 互斥）
- **timeout**: 超时秒数，默认 30（可选）

### 凭据替换
URL 和 headers 中可以使用 `${ENV_VAR}` 占位符，运行时自动替换为实际值。

### 常见认证模式示例
1. Bearer Token: headers 中设置 `{"Authorization": "Bearer ${API_TOKEN}"}`
2. API Key Header: headers 中设置 `{"X-API-Key": "${API_KEY}"}`
3. Basic Auth: headers 中设置 `{"Authorization": "Basic ${BASIC_AUTH}"}`
4. URL 参数: url 中使用 `https://api.example.com?key=${API_KEY}`
"""
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
        timeout = kwargs.get("timeout", 30)
        follow_redirects = kwargs.get("follow_redirects", True)

        if not url:
            return {"success": False, "error": "URL 不能为空"}

        # ${ENV_VAR} 替换
        url = _substitute_env_vars(url)
        if headers:
            headers = {k: _substitute_env_vars(v) for k, v in headers.items()}
        if query_params:
            query_params = {k: _substitute_env_vars(v) for k, v in query_params.items()}

        # 构建请求参数
        request_kwargs: Dict[str, Any] = {}
        if headers:
            request_kwargs["headers"] = headers
        if query_params:
            request_kwargs["params"] = query_params
        if form_data:
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


def _substitute_env_vars(text: str) -> str:
    """替换字符串中的 ${ENV_VAR} 为环境变量值"""
    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name)
        if value is None:
            logger.warning(f"环境变量 {var_name} 未设置")
            return match.group(0)
        return value

    return re.sub(r"\$\{([^}]+)\}", replace, text)


def _parse_response(response: httpx.Response) -> Dict[str, Any]:
    """解析 HTTP 响应"""
    status_code = response.status_code
    success = 200 <= status_code < 300

    result: Dict[str, Any] = {
        "success": success,
        "status_code": status_code,
    }

    try:
        result["data"] = response.json()
    except Exception:
        text = response.text
        if len(text) > 5000:
            text = text[:5000] + "...(截断)"
        result["data"] = text

    if not success:
        error_data = str(result["data"])
        if len(error_data) > 500:
            error_data = error_data[:500]
        result["error"] = f"HTTP {status_code}: {error_data}"

    return result
