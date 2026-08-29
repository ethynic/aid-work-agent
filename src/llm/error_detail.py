"""LLM 异常诊断信息提取。

解决 httpx.ConnectError 等异常的 str() 为空的问题：TLS 阶段底层
anyio.BrokenResourceError 被 httpcore/httpx 逐层包装后消息丢失，日志只显示
"ConnectError: " 空白，无法诊断。repr / args / request URL / cause 链包含完整
细节，统一在此提取为单行可读字符串。
"""


def describe_exception(e: BaseException, max_cause_depth: int = 3) -> str:
    """返回异常的可诊断单行描述。

    包含：类型 + repr、args、httpx request（method + URL）、cause 链
    （逐层类型 + repr + args），定位 TCP/TLS 哪一阶段失败。
    """
    segments = [f"{type(e).__name__} {e!r}"]
    if getattr(e, "args", None):
        segments.append(f"args={e.args!r}")

    # httpx 异常的 request 是 property，未设置时访问会抛 RuntimeError，需 try 包裹
    try:
        request = e.request
        req_url = request.url if request is not None else None
    except Exception:
        request, req_url = None, None
    if request is not None and req_url is not None:
        segments.append(f"req={request.method} {req_url}")

    cause = getattr(e, "__cause__", None)
    depth = 0
    while cause is not None and depth < max_cause_depth:
        segments.append(f"cause[{depth}]={type(cause).__name__} {cause!r}")
        if getattr(cause, "args", None):
            segments.append(f"cause[{depth}].args={cause.args!r}")
        cause = getattr(cause, "__cause__", None)
        depth += 1

    return " | ".join(segments)
