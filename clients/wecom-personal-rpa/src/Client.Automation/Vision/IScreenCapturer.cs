// ====================================================================================
// 协同约束（plan §A3，267-268 行 + §467-484 行）：
// A1 的 ScreenCapturer / CaptureResult / WindowFingerprint 已落地（真实 record 类型）。
// A3 的 QwenVisionLocator / OcrVisionLocator 通过 IScreenCapturer 接口消费，便于单测 stub。
//
// 集成者工作（任务 B 前必做）：让 A1 的 ScreenCapturer 类添加 ": IScreenCapturer"。
// A1 的 CaptureResult.CaptureWeComMainWindowAsync 当前签名要返回 Task<CaptureResult>，
// 与本接口完全兼容（CaptureResult 是 A1 真实类型，这里直接 using）。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 截图器抽象。供 QwenVisionLocator / OcrVisionLocator 消费，便于单测用 stub 注入。
/// <para>
/// <b>集成约束</b>：A1 的 <c>ScreenCapturer</c> 类落地后，由集成者加 <c>: IScreenCapturer</c>
/// 即可（方法签名已对齐）。Client.App 的 DI 注册时绑 <c>AddSingleton&lt;IScreenCapturer, ScreenCapturer&gt;()</c>。
/// </para>
/// </summary>
public interface IScreenCapturer
{
    /// <summary>
    /// 截取企微主窗口（design §3.4 完整流程：找 hwnd → 校验标题 → 恢复最小化 →
    /// SetForegroundWindow → 像素自检 → CopyFromScreen）。
    /// </summary>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>截图结果（含 Bitmap + 窗口指纹 + WindowRect），调用方负责 Dispose。</returns>
    Task<CaptureResult> CaptureWeComMainWindowAsync(CancellationToken cancellationToken = default);
}
