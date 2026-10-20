"""腾讯文档 OpenAPI HTTP 客户端

- 鉴权三元组请求头：Access-Token / Client-Id / Open-Id（官方要求所有请求由后台服务发起）。
- 错误形态两种：V2 接口 `{"ret": 0, "msg": ...}`；V3 接口 `{"code": 400010, "message": ...}`。
  统一映射为 TencentDocsError；V3 400010（文档解析失败，实测含表格+图片的文档触发）
  单独映射为 TencentDocsUnmarshalError，供导出兜底自动降级。
- 频控错误（V2 9998 / V3 400007）标记 retryable，调用方可据此退避重试。
- URL 路径中的 fileID 含 `$`，必须编码为 %24（2026-10-20 实测）。
"""

from typing import Any, Dict, List, Optional, Tuple

import asyncio
from pathlib import Path

import httpx
from loguru import logger

from .models import DriveFile, TencentDocsCredentials, parse_v2_file_id

BASE_URL = "https://docs.qq.com"

# V2 频控 / V3 频控业务码
RETRYABLE_CODES = {9998, 400007, 400002, 400009}
# V3 doc 接口文档解析失败（实测：含表格+图片的文档稳定触发，纯文本正常）
CODE_UNMARSHAL_ERROR = 400010


class TencentDocsError(Exception):
    """腾讯文档 OpenAPI 调用异常基类。"""

    def __init__(self, code: int, message: str, retryable: bool = False):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"[tdoc {code}] {message}")


class TencentDocsUnmarshalError(TencentDocsError):
    """doc v3 文档解析失败（400010）：文档内容形态超出 doc v3 解析器能力，
    应降级 async-export 导出 DOCX 兜底。"""


class TencentDocsRateLimitError(TencentDocsError):
    """频控超限（V2 9998 / V3 400007），可退避重试。"""


class TencentDocsClient:
    """OpenAPI 客户端。用法：

        client = TencentDocsClient(credentials)
        try:
            files, next_start = await client.list_folder()
        finally:
            await client.aclose()

    或 async with TencentDocsClient(credentials) as client: ...
    """

    def __init__(
        self,
        credentials: TencentDocsCredentials,
        timeout: float = 30.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.credentials = credentials
        headers = {
            "Access-Token": credentials.access_token,
            "Client-Id": credentials.client_id,
            "Open-Id": credentials.open_id,
            "Accept": "application/json",
        }
        self._http = httpx.AsyncClient(
            base_url=BASE_URL, headers=headers, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "TencentDocsClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    # ---------- 响应解析 ----------

    async def _request_v2(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        resp = await self._http.request(method, path, **kwargs)
        resp.raise_for_status()
        data = resp.json() or {}
        ret = data.get("ret", -1)
        if ret != 0:
            self._raise_v2(int(ret), data.get("msg") or "unknown v2 error")
        return data.get("data") or {}

    async def _request_v3(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        resp = await self._http.request(method, path, **kwargs)
        # V3 错误也可能以 HTTP 状态码表达（401/403/429），先解析 JSON body
        try:
            data = resp.json() or {}
        except Exception:
            data = {}
        if resp.status_code >= 400 or ("code" in data and data.get("code") not in (None, 0)):
            code = int(data.get("code") or resp.status_code)
            message = data.get("message") or resp.text[:200]
            self._raise_v3(code, message)
        # doc v3 成功响应顶层即 {"document": ...}；其余 V3 接口包 {"data": ...}，原样返回由调用方取
        return data

    @staticmethod
    def _raise_v2(ret: int, msg: str) -> None:
        if ret == 9998:
            raise TencentDocsRateLimitError(ret, msg, retryable=True)
        raise TencentDocsError(ret, msg, retryable=False)

    @staticmethod
    def _raise_v3(code: int, message: str) -> None:
        if code == CODE_UNMARSHAL_ERROR:
            raise TencentDocsUnmarshalError(
                code, "文档解析失败（doc v3 不支持该内容形态），请降级导出路径", retryable=False
            )
        if code in RETRYABLE_CODES:
            raise TencentDocsRateLimitError(code, message, retryable=True)
        raise TencentDocsError(code, message, retryable=False)

    # ---------- drive 文件管理（V2） ----------

    async def list_folder(
        self,
        folder_id: str = "",
        start: int = 0,
        limit: int = 20,
        sort_type: str = "browse",
        asc: int = 0,
    ) -> Tuple[List[DriveFile], int]:
        """列出目录内容。返回 (文件列表, 下一页 start 值；0 表示已到末页)。

        folder_id 传空串 = 「我的文档」根目录。
        """
        params = {"sortType": sort_type, "asc": asc, "start": start, "limit": limit}
        path = f"/openapi/drive/v2/folders/{folder_id}" if folder_id else "/openapi/drive/v2/folders/"
        data = await self._request_v2("GET", path, params=params)
        items = [DriveFile.from_api(i) for i in (data.get("list") or [])]
        next_start = int(data.get("next") or 0)
        return items, next_start

    async def list_all(self, folder_id: str = "", page_limit: int = 50) -> List[DriveFile]:
        """翻页拉取目录全部内容（列表页一般几十到几百条，无需并发限速考虑）。"""
        all_items: List[DriveFile] = []
        start = 0
        while True:
            items, next_start = await self.list_folder(folder_id, start=start, limit=page_limit)
            all_items.extend(items)
            # next 不前进或无数据时终止，防 API 异常导致死循环
            if not next_start or not items or next_start == start:
                break
            start = next_start
        return all_items

    async def get_metadata(self, file_id: str) -> Dict[str, Any]:
        return await self._request_v2(
            "GET", f"/openapi/drive/v2/files/{parse_v2_file_id(file_id)}/metadata"
        )

    async def convert_encoded_id(self, encoded_id: str) -> str:
        """分享链接中的 encodedID（如 DXXXX）-> fileID（如 300000000$XXXX）。

        建议调用方自行缓存映射，避免重复消耗频控额度。
        """
        data = await self._request_v2(
            "GET", "/openapi/drive/v2/util/converter", params={"type": 2, "value": encoded_id}
        )
        file_id = data.get("fileID") or ""
        if not file_id:
            raise TencentDocsError(-1, f"converter 未返回 fileID: {encoded_id}")
        return file_id

    async def search(self, keyword: str, **params: Any) -> Dict[str, Any]:
        """关键字搜索。注意：查询参数名官方文档未给出明确示例，
        2026-10-20 实测 search_string 报 10002，参数名待真机确认后修正。"""
        params = {"search_string": keyword, **params}
        return await self._request_v2("GET", "/openapi/drive/v2/search", params=params)

    # ---------- 在线文档内容（V3） ----------

    async def get_doc_content(self, file_id: str) -> Dict[str, Any]:
        """读取 doc 内容属性树。返回 {"document": {...}, "version": N}。

        文档含 doc v3 不支持的内容形态时抛 TencentDocsUnmarshalError（400010），
        调用方应降级 exporter.export_document() 导出 DOCX 兜底。
        """
        data = await self._request_v3("GET", f"/openapi/doc/v3/{parse_v2_file_id(file_id)}")
        if not data.get("document"):
            raise TencentDocsError(-1, f"doc v3 响应缺少 document 字段: {file_id}")
        return data

    # ---------- 导出（V2） ----------

    async def start_export(self, file_id: str, export_type: Optional[str] = None) -> str:
        """发起异步导出，返回 operationID（20 分钟有效）。

        export_type=None 时不转换导出原类型（doc 实测导出 DOCX）；
        "pdf" 将 doc/slide 转 PDF。每 openID 每天 9 次配额由 exporter 层闸门控制。
        """
        kwargs: Dict[str, Any] = {}
        if export_type:
            kwargs["data"] = {"exportType": export_type}
        data = await self._request_v2(
            "POST", f"/openapi/drive/v2/files/{parse_v2_file_id(file_id)}/async-export", **kwargs
        )
        operation_id = data.get("operationID") or ""
        if not operation_id:
            raise TencentDocsError(-1, f"async-export 未返回 operationID: {file_id}")
        logger.debug(f"腾讯文档导出任务已发起 file_id={file_id} operation_id={operation_id}")
        return operation_id

    async def get_export_progress(self, file_id: str, operation_id: str) -> Tuple[int, str]:
        """查询导出进度。返回 (progress 0-100, 下载 URL；未完成时为空串)。"""
        data = await self._request_v2(
            "GET",
            f"/openapi/drive/v2/files/{parse_v2_file_id(file_id)}/export-progress",
            params={"operationID": operation_id},
        )
        progress = int(data.get("progress") or 0)
        url = data.get("url") or ""
        return progress, url

    async def download(self, url: str, dest_path: str) -> int:
        """下载导出文件（COS 签名 URL）。返回字节数。

        复用客户端 http 实例（单测可经 MockTransport 拦截）；下载目标是
        第三方存储，必须剥离三元组鉴权头，不能把 Access-Token 发往外部主机。
        """
        request = self._http.build_request("GET", url)
        for header in ("Access-Token", "Client-Id", "Open-Id"):
            request.headers.pop(header, None)
        resp = await self._http.send(request)
        resp.raise_for_status()
        content = resp.content
        await asyncio.to_thread(Path(dest_path).write_bytes, content)
        return len(content)
