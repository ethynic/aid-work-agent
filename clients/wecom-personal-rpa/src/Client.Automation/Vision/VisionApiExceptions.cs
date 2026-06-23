// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（433 行）。
// 设计 §4.4：4xx → 抛 VisionApiAuthException（上层暂停账号）；
//            5xx / 超时 → 抛 VisionApiTransientException（指数退避重试 / 切 fallback 模型）。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉 API 鉴权 / 配额类异常。对应 HTTP 401 / 403 / 所有 key 都失败时抛出。
/// 上层（SendMessageService）捕获后应暂停账号并发告警，不要重试。
/// </summary>
public sealed class VisionApiAuthException : Exception
{
    /// <summary>构造。</summary>
    public VisionApiAuthException(string message) : base(message) { }

    /// <summary>构造（带内嵌异常）。</summary>
    public VisionApiAuthException(string message, Exception innerException) : base(message, innerException) { }
}

/// <summary>
/// 视觉 API 暂时性异常。对应 HTTP 5xx / 网络超时 / 429 限流等。
/// QwenVisionLocator 内部按 MaxRetries 指数退避重试，仍失败切 FallbackModel。
/// </summary>
public sealed class VisionApiTransientException : Exception
{
    /// <summary>构造。</summary>
    public VisionApiTransientException(string message) : base(message) { }

    /// <summary>构造（带内嵌异常）。</summary>
    public VisionApiTransientException(string message, Exception innerException) : base(message, innerException) { }
}

/// <summary>
/// 模型返回的 bbox 越界异常。当 Qwen3-VL 返回的 bbox 全部或部分超出截图尺寸 ±tolerance 时抛出。
/// 调用方应触发 VisionCache.InvalidateWindowAsync 失效该窗口缓存。
/// </summary>
public sealed class BboxOutOfBoundsException : Exception
{
    /// <summary>越界的 bbox（便于日志排查）。</summary>
    public BoundingBox? Bbox { get; init; }

    /// <summary>参考图像宽度。</summary>
    public int ImageWidth { get; init; }

    /// <summary>参考图像高度。</summary>
    public int ImageHeight { get; init; }

    /// <summary>构造。</summary>
    public BboxOutOfBoundsException(string message) : base(message) { }
}
