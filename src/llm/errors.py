"""Provider 与网关之间共享的结构化异常。"""

import httpx


class ProviderHTTPError(RuntimeError):
    """保留 HTTP 状态码，同时兼容现有 RuntimeError 捕获逻辑。"""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class ProviderResponseInvalid(RuntimeError):
    """供应商已返回完整响应但内容不可解析（非 JSON 对象 / JSON 解析失败）。

    与超时、连接中断等无响应异常区分：本类表示「有响应」，构成可审计的
    单份失败事实；重试是一次新的物理调用，不落入结果未知的恢复保护。
    """

    outcome = "unparseable"


def provider_outcome(error: BaseException) -> str | None:
    """按异常类型分类供应商结果，供单份失败收敛与诊断使用。

    返回 'rejected'（收到完整 HTTP 错误响应）、'unparseable'（有响应但内容
    不可解析）或 None（无响应/结果未知，维持验证保护不放松）。
    """
    if isinstance(error, ProviderHTTPError):
        return "rejected"
    if isinstance(error, ProviderResponseInvalid):
        return "unparseable"
    return None


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
