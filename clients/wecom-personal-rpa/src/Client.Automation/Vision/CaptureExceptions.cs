// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A1（71-164 行）。
//
// ScreenCapturer 在两个不可恢复场景抛出专用异常，区分降级动作：
//   1) WindowNotForegroundException：企微主窗口无法带到前台（被其他进程前台锁占用）。
//      上层应触发 HealthSupervisor → PausedError，提示用户手动激活企微。
//   2) SuspiciousScreenshotException：截图像素自检失败（白色占比超阈值或颜色多样性不足）。
//      上层应立即暂停账号（设计 §10.3 误发 0 容忍），上报 vision_screenshot_suspicious。
//
// 所有异常 message 用中文，便于日志排查；不包含密钥 / 截图内容等敏感信息。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 企微主窗口无法带到前台时抛出。
/// 触发条件：SetForegroundWindow 失败且 ALT-key trick 重试后 GetForegroundWindow 仍不等于目标 hwnd。
/// 常见原因：另一进程占用前台锁、用户正在操作其他窗口、企微被卸载或崩溃。
/// </summary>
public sealed class WindowNotForegroundException : Exception
{
    /// <summary>构造函数，message 必须为中文且不含敏感信息。</summary>
    public WindowNotForegroundException(string message)
        : base(message)
    {
    }

    /// <summary>构造函数，附带内层异常用于堆栈追溯。</summary>
    public WindowNotForegroundException(string message, Exception inner)
        : base(message, inner)
    {
    }
}

/// <summary>
/// 截图像素自检失败时抛出（疑似截图被其他窗口遮挡或为空白）。
/// 触发条件：白色区域占比 &gt; MaxWhiteRatio（默认 0.5）或颜色多样性 &lt; MinColorDiversity（默认 30）。
/// 上层必须立即暂停账号并上报，不可降级继续。
/// </summary>
public sealed class SuspiciousScreenshotException : Exception
{
    /// <summary>白色像素占比（0..1），便于日志诊断。</summary>
    public double WhiteRatio { get; }

    /// <summary>量化后的唯一颜色数。</summary>
    public int ColorDiversity { get; }

    /// <summary>构造函数，message 必须为中文且不含敏感信息。</summary>
    public SuspiciousScreenshotException(string message, double whiteRatio, int colorDiversity)
        : base(message)
    {
        WhiteRatio = whiteRatio;
        ColorDiversity = colorDiversity;
    }

    /// <summary>构造函数，附带内层异常用于堆栈追溯。</summary>
    public SuspiciousScreenshotException(string message, double whiteRatio, int colorDiversity, Exception inner)
        : base(message, inner)
    {
        WhiteRatio = whiteRatio;
        ColorDiversity = colorDiversity;
    }
}
