// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（305-313 行）。
// VisionProbeResult 是 IVisionLocator.LocateAsync 的统一返回类型，
// 覆盖 cache / api / ocr 三条命中来源，供 WeComAutomation 上层无差别处理。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉定位结果。LocateAsync 未命中时返回 null，命中时返回本对象。
/// </summary>
public sealed class VisionProbeResult
{
    /// <summary>命中元素在主窗口截图中的像素 bbox（左上原点，y 向下）。</summary>
    public BoundingBox Bbox { get; init; } = new(0, 0, 0, 0);

    /// <summary>
    /// 模型 / OCR 自报的置信度，范围 [0,1]。缓存命中时填 1.0；OCR 命中时填 0.5（仅做粗略标识）。
    /// 上层不应根据本字段做是否点击的决策——模型置信度并不稳定。
    /// </summary>
    public double Confidence { get; init; }

    /// <summary>
    /// 命中来源：<c>"api"</c>（Qwen3-VL 实时调用）、<c>"cache"</c>（SQLite 缓存命中）、
    /// <c>"ocr"</c>（Windows.Media.Ocr 降级命中）。
    /// </summary>
    public string Source { get; init; } = "api";

    /// <summary>调用方传入的元素类型（button / input / list_item / icon / text）。</summary>
    public string ElementType { get; init; } = "";

    /// <summary>调用方传入的标签关键字（如 "发送" / "搜索" / "文件传输助手"）。</summary>
    public string LabelKeyword { get; init; } = "";

    /// <summary>
    /// 实际使用的模型名（如 "qwen3-vl-plus"）。缓存命中时复制原写入值；OCR 命中时填 "windows-ocr"。
    /// </summary>
    public string? ModelUsed { get; init; }

    /// <summary>易读表达，便于日志输出。</summary>
    public override string ToString()
        => $"VisionProbeResult(src={Source}, model={ModelUsed ?? "?"}, {Bbox}, kw='{LabelKeyword}')";
}
