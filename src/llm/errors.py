"""Provider 与网关之间共享的结构化异常。"""

import httpx


class ProviderHTTPError(RuntimeError):
    """保留 HTTP 状态码，同时兼容现有 RuntimeError 捕获逻辑。"""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


async def read_error_body(response: httpx.Response) -> None:
    """在响应关闭前读取错误正文；读取失败也不能丢弃已知 HTTP 状态。"""
    if response.is_success:
        return
    try:
        await response.aread()
    except httpx.RequestError as error:
        raise ProviderHTTPError(
            f"HTTP {response.status_code}: 错误响应正文读取失败 ({type(error).__name__})",
            status_code=response.status_code,
        ) from None
