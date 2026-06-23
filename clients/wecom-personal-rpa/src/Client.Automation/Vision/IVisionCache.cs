// ====================================================================================
// 协同约束（plan §A3，267-268 行 + §467-484 行）：
// A1 已落地 WindowFingerprint（record）+ CaptureResult；A2 的 VisionCache 当前用
// WindowFingerprintPlaceholder（字段一致但类型不同）。集成者在 2B 前统一 placeholder → A1 真实类型。
//
// A3 自定义 IVisionCache 抽象供 QwenVisionLocator 消费，签名直接用 A1 的 WindowFingerprint
// （非 placeholder）+ A3 的 BoundingBox。集成者让 A2 的 VisionCache 实现 IVisionCache
// （必要时把 SetAsync 的 bbox 参数类型对齐 BoundingBox record）。
//
// 单测用最小 stub class 注入即可，不需要 A2 的真实 SQLite 实现。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉缓存抽象（A2 VisionCache 的消费契约）。
/// <para>
/// <b>集成约束</b>：A2 的 <c>VisionCache</c> 落地后，由集成者加 <c>: IVisionCache</c>。
/// 签名差异（A2 用 WindowFingerprintPlaceholder / BoundingBoxPlaceholder）由集成者在统一
/// 命名空间去重时一并修复（删 placeholder，改用 A1 / A3 真实类型）。
/// </para>
/// </summary>
public interface IVisionCache
{
    /// <summary>
    /// 查询元素级缓存。
    /// </summary>
    /// <param name="fingerprint">窗口指纹（缓存键 1，A1 真实 record）。</param>
    /// <param name="elementType">元素类型（缓存键 2）。</param>
    /// <param name="labelKeyword">标签关键字（缓存键 3）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>命中时返回 <see cref="CachedEntry"/>；未命中或 TTL 过期返回 <c>null</c>。</returns>
    Task<CachedEntry?> TryGetAsync(
        WindowFingerprint fingerprint,
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// 写入元素级缓存。
    /// </summary>
    Task SetAsync(
        WindowFingerprint fingerprint,
        string elementType,
        string labelKeyword,
        BoundingBox bbox,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// 失效特定窗口的所有缓存（窗口指纹变化时调用）。
    /// </summary>
    Task InvalidateWindowAsync(
        WindowFingerprint fingerprint,
        CancellationToken cancellationToken = default);
}

/// <summary>
/// 缓存命中返回的条目（A3 视角的最小契约，与 A2 的 CachedBbox 解耦，避免 placeholder 命名冲击）。
/// </summary>
public sealed class CachedEntry
{
    /// <summary>命中的 bbox。</summary>
    public BoundingBox Bbox { get; init; } = new(0, 0, 0, 0);

    /// <summary>写入时间（UTC），用于日志排查。</summary>
    public DateTimeOffset CachedAt { get; init; }
}
