// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（336-347 行）。
// IVisionLocator 是 WeComAutomation / SendMessageService / MessageWatcher / LoginStateDetector
// 消费视觉定位的唯一入口；QwenVisionLocator 是主实现，OcrVisionLocator 是降级实现。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉定位抽象。实现负责截图、视觉缓存、模型调用、bbox 校验等全部细节，
/// 对调用方只暴露"按 (元素类型, 标签关键字) 拿 bbox"。
/// </summary>
public interface IVisionLocator
{
    /// <summary>
    /// 在当前企微主窗口截图上定位 UI 元素。内部自动走视觉缓存。
    /// </summary>
    /// <param name="elementType">
    /// 元素类型，使用 Qwen3-VL grounding prompt 里定义的标签枚举：
    /// <c>button</c> / <c>input</c> / <c>list_item</c> / <c>icon</c> / <c>text</c>。
    /// </param>
    /// <param name="labelKeyword">元素标签关键字（如 "发送" / "搜索" / "文件传输助手"）。匹配规则为包含 + 忽略大小写。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>命中时返回 <see cref="VisionProbeResult"/>；未命中 / API 失败 / OCR 无结果时返回 <c>null</c>。</returns>
    /// <exception cref="BboxOutOfBoundsException">模型返回的 bbox 越界（疑似模型幻觉）。</exception>
    /// <exception cref="VisionApiAuthException">所有 API Key 都 401/403。</exception>
    Task<VisionProbeResult?> LocateAsync(
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// 强制失效缓存。窗口位置/尺寸变化、客户端重启、用户手动"刷新定位"时调用。
    /// </summary>
    Task InvalidateCacheAsync();
}
