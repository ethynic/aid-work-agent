import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlsplit

import httpx
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from src.tools._helpers import truncate_text
from src.tools._spill import spill_large_content
from src.tools.base import BaseTool
from src.utils import sanitize_error_info

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
        description=(
            "请求头字典，键值对形式，需传 JSON 对象（如 {\"Authorization\": \"Bearer xxx\"}），"
            "不要整体传 JSON 字符串。支持 ${ENV_VAR} 凭据占位符。"
        ),
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

    @field_validator(
        "headers",
        "query_params",
        "form_data",
        "files",
        "body",
        mode="before",
    )
    @classmethod
    def _coerce_json_string_args(cls, value: Any) -> Any:
        """容错：LLM 有时把 headers/body 等整体序列化成 JSON 字符串传参，
        校验前先把合法 JSON 字符串解析为对象，避免 dict_type 校验失败。"""
        return _coerce_json_object(value)


class HttpApiTool(BaseTool):
    """通用 HTTP API 调用工具"""

    name = "http_api"
    description = (
        "调用外部 HTTP API（GET/POST/PUT/DELETE/PATCH、文件上传），${VAR_NAME} 替换环境变量。"
        "调用前先查上下文是否已有相同结果：历史/详情类（已发生事件）复用，实时/状态类（库存/价格/状态）重调。"
        "认证模式：Bearer Token（{\"Authorization\":\"Bearer ${API_TOKEN}\"}）、"
        "API Key Header（{\"X-API-Key\":\"${API_KEY}\"}）、Basic Auth。"
        "文件上传用 files 参数（{\"file\":\"/path/to/doc.pdf\"}，multipart/form-data），可与 form_data 同时使用。"
    )
    usage_guide = ""
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
        # LLM 有时把 headers/body 等整体序列化成 JSON 字符串传参，先统一解析为对象，
        # 否则 body 为字符串时 httpx json= 会二次编码成 JSON 字符串字面量
        headers = _coerce_json_object(kwargs.get("headers"))
        query_params = _coerce_json_object(kwargs.get("query_params"))
        body = _coerce_json_object(kwargs.get("body"))
        form_data = _coerce_json_object(kwargs.get("form_data"))
        files = _coerce_json_object(kwargs.get("files"))
        timeout = kwargs.get("timeout", 30)
        follow_redirects = kwargs.get("follow_redirects", True)

        if not url:
            return {"success": False, "error": "URL 不能为空"}

        if files and body is not None:
            return {"success": False, "error": "files 和 body 不能同时使用"}

        # ${ENV_VAR} 替换（优先租户级环境变量，兜底进程环境）
        url = _substitute_env_vars(url)
        if headers:
            headers = {k: _substitute_env_vars(v) for k, v in headers.items()}
        if query_params:
            query_params = {k: _substitute_env_vars(v) for k, v in query_params.items()}

        # fail-fast：凭证占位符（如 ${AGENT_TOKEN}）未被解析时直接拒绝请求，
        # 避免把字面量当凭证发给第三方（2026-09-03 Code=-99 事故）
        unresolved: List[str] = _find_unresolved_vars(url)
        for _map in (headers, query_params):
            if _map:
                for _v in _map.values():
                    unresolved.extend(_find_unresolved_vars(_v))
        if unresolved:
            return {
                "success": False,
                "error": (
                    f"环境变量未配置，无法解析占位符: {', '.join(sorted(set(unresolved)))}。"
                    "请在管理后台「租户管理 > 数字员工授权 > 环境变量」中配置后重试"
                ),
            }

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
            logger.error(
                f"HTTP API 请求超时: method={method} url={_safe_url_str(url)} "
                f"timeout={timeout}s{_correlation_suffix()}"
            )
            return {"success": False, "error": f"请求超时（{timeout}秒）"}
        except httpx.ConnectError as e:
            msg = sanitize_error_info(str(e))
            logger.error(
                f"HTTP API 连接失败: method={method} url={_safe_url_str(url)} "
                f"err={msg}{_correlation_suffix()}"
            )
            return {"success": False, "error": f"连接失败: {msg}"}
        except Exception as e:
            msg = sanitize_error_info(str(e))
            logger.error(
                f"HTTP API 请求异常: method={method} url={_safe_url_str(url)} "
                f"err={msg}{_correlation_suffix()}"
            )
            return {"success": False, "error": f"请求异常: {msg}"}
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


def _coerce_json_object(value: Any) -> Any:
    """把 JSON 字符串形式的参数解析为对象。

    LLM（如 qwen）在函数调用里有时会把 headers/body/query_params 等整体
    序列化成 JSON 字符串传参（例如 headers='{"Authorization": "Bearer xxx"}'、
    body='{"customer": "123"}'）。这里对合法 JSON 字符串做 json.loads，
    解析失败或非字符串则原样返回，不破坏现有行为。
    """
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            return value
    return value


def _get_tenant_env_vars() -> Dict[str, str]:
    """读取请求级租户环境变量（subagent_env_vars，经 ToolExecutionContext 传递）。

    旧实现把租户变量写入进程级 os.environ，并发消息结束时互相 pop 导致
    ${VAR} 解析失败（2026-09-03 Code=-99 事故），现已改为随请求隔离。
    """
    try:
        from src.tools.context import current_tool_execution_context
        ctx = current_tool_execution_context()
    except Exception:
        return {}
    if ctx is not None and ctx.env_vars:
        return dict(ctx.env_vars)
    return {}


def _substitute_env_vars(text: str) -> str:
    """替换字符串中的 ${ENV_VAR}，优先取租户级环境变量，兜底进程环境变量"""
    tenant_env = _get_tenant_env_vars()

    def replace(match: re.Match) -> str:
        var_name = match.group(1)
        value = tenant_env.get(var_name)
        if value is None:
            value = os.environ.get(var_name)
        if value is None:
            logger.warning(f"环境变量 {var_name} 未设置（租户级环境变量与进程环境均未找到）。")
            return match.group(0)
        return value

    return re.sub(r"\$\{([^}]+)\}", replace, text)


def _find_unresolved_vars(text: str) -> List[str]:
    """找出文本中未被替换的 ${VAR} 占位符（环境变量未配置时残留）"""
    if not text or "${" not in text:
        return []
    return re.findall(r"\$\{([^}]+)\}", text)


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


def _safe_url_str(url: str) -> str:
    """从 URL 字符串剥离 query/fragment/userinfo，用于异常分支的日志。

    异常分支没有 response 对象，只有已替换过 ${VAR} 的 URL 字符串，query 里
    可能含真实凭证，写日志前必须剥离。无 scheme / 非 http(s) / 解析异常时
    退化为剥离 query 与 fragment 后的原始串，宁可不完整也不泄漏。
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return url.split("?", 1)[0].split("#", 1)[0]
        host = parts.hostname or ""
        path = parts.path or "/"
        return f"{parts.scheme}://{host}{path}"
    except Exception:
        return url.split("?", 1)[0].split("#", 1)[0]


def _safe_method(response) -> str:
    """提取请求方法用于日志；响应无 request 信息（如测试 mock）时返回空串。"""
    try:
        req = getattr(response, "request", None)
        if req is None:
            return ""
        method = getattr(req, "method", "")
        return method if isinstance(method, str) else ""
    except Exception:
        return ""


def _extract_business_error(data: Any) -> Optional[str]:
    """识别外部系统业务错误。

    第三方 ERP 等系统用 HTTP 200 承载业务失败，响应体形如
    {"Code": -1, "Error": "非空内容"}。仅当响应体为 dict 且含 Code 字段、
    且值不在 (0, 200, "0", "200") 时才判定为业务错误，避免误伤不使用
    Code 约定的第三方系统。识别到返回描述串，否则返回 None。
    """
    if not isinstance(data, dict):
        return None
    code = data.get("Code")
    if code is None:
        return None
    if code in (0, 200, "0", "200"):
        return None
    error_text = (
        data.get("Error")
        or data.get("error")
        or data.get("Message")
        or data.get("message")
        or ""
    )
    return f"Code={code}: {error_text}"


def _correlation_suffix() -> str:
    """追加请求关联上下文（租户/会话/子智能体），供错误日志定位。

    http_api 在主智能体循环内作为工具调用时，ToolExecutor 会安装
    current_tool_execution_context()。此处仅用于日志，读取失败或为空时
    返回空串，绝不干扰工具结果或测试（测试环境无工具上下文）。
    """
    try:
        from src.tools.context import current_tool_execution_context

        ctx = current_tool_execution_context()
    except Exception:
        return ""
    if not ctx:
        return ""
    parts = []
    if ctx.tenant_id:
        parts.append(f"tenant={ctx.tenant_id}")
    if ctx.session_id:
        parts.append(f"session={ctx.session_id}")
    if ctx.subagent_id:
        parts.append(f"subagent={ctx.subagent_id}")
    if not parts:
        return ""
    return " " + " ".join(parts)


def _parse_response(response: httpx.Response) -> Dict[str, Any]:
    """解析 HTTP 响应

    成功响应（2xx）下，若序列化内容超 5000 字符，则完整内容落盘到临时文件，
    返回截断预览 + file_path + full_size，供 LLM 用 read/grep 回读（消除信息黑洞）；
    小响应（≤5000）保持原样返回，不落盘，零回归。
    错误响应（非 2xx）不落盘，沿用 error 截断到 500 的既有逻辑；错误响应的 data
    也会截断（避免大错误体灌入上下文）。
    HTTP 2xx 但响应体含 Code 字段且值非 0/200（业务错误，如
    {"Code": -1, "Error": "非空内容"}）同样重分类为 success=False 并记 error 日志。
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
            # 业务错误识别：HTTP 2xx 但响应体含 Code 字段且值不为 0/200。
            # 第三方 ERP 等系统用 HTTP 200 承载业务失败（如
            # {"Code": -1, "Error": "非空内容"}）。识别到即标记 success=False
            # 并记 error 日志（进入 log_error 表，管理员在 /portal/error-logs
            # 直接可见，无需翻 LLM 上下文）。仅含 Code 字段才判定，避免误伤
            # 不使用该约定的第三方系统。
            business_error = _extract_business_error(data)
            if business_error:
                result["success"] = False
                serialized = json.dumps(data, ensure_ascii=False)
                if len(serialized) > 5000:
                    error_data, truncated = truncate_text(serialized, limit=5000)
                    result["data"] = error_data
                    if truncated:
                        result["truncated"] = True
                else:
                    result["data"] = data
                err_text, _ = truncate_text(business_error, limit=500, suffix="")
                result["error"] = f"业务错误 {err_text}"
                logger.error(
                    f"HTTP API 业务失败: method={_safe_method(response)} "
                    f"url={_safe_url_for_meta(response)} status={status_code} "
                    f"error={sanitize_error_info(business_error)}"
                    f"{_correlation_suffix()}"
                )
                return result
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
        logger.error(
            f"HTTP API 调用失败: method={_safe_method(response)} "
            f"url={_safe_url_for_meta(response)} status={status_code} "
            f"error={sanitize_error_info(error_data)}"
            f"{_correlation_suffix()}"
        )

    return result
