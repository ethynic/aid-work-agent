// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A1（117-124 行）。
//                  design §3.4 截图采集 + 像素自检。
//
// 截图自检选项，对应 vision.screenshot 配置段（见设计 §5.3）。
// 与 auto-capture-wecom.ps1 实测阈值对齐：
//   - MaxWhiteRatio = 0.5（企微主窗口白色占比 12.9%，VSCode 浅色主题 > 50%）
//   - MinColorDiversity = 30（企微约 172 种量化色，VSCode 极简配色 < 100）
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 截图采集选项。对应 vision.screenshot 配置段（设计 §5.3）。
/// </summary>
public sealed class ScreenshotOptions
{
    /// <summary>
    /// 截图前校验企微主窗口在前台。关闭后会跳过 GetForegroundWindow 比对（仅诊断场景关闭）。
    /// 默认 true。
    /// </summary>
    public bool PreForegroundCheck { get; set; } = true;

    /// <summary>
    /// 截图后执行像素自检。关闭后不抛 SuspiciousScreenshotException（仅诊断场景关闭）。
    /// 默认 true。
    /// </summary>
    public bool PixelSanityCheck { get; set; } = true;

    /// <summary>
    /// 白色区域占比阈值（0..1），超过则判定为疑似非企微（VSCode 浅色主题会触发）。
    /// 默认 0.5（实测企微 0.129）。
    /// </summary>
    public double MaxWhiteRatio { get; set; } = 0.5;

    /// <summary>
    /// 颜色多样性阈值（量化到 16x16 色块后的唯一颜色数），低于则判定为空白截图。
    /// 默认 30（实测企微约 172）。
    /// </summary>
    public int MinColorDiversity { get; set; } = 30;

    /// <summary>
    /// SetForegroundWindow 后的等待毫秒数，给 DWM 足够时间把窗口画到屏幕。
    /// 默认 800（与 auto-capture-wecom.ps1 一致）。
    /// </summary>
    public int ForegroundSettleMs { get; set; } = 800;

    /// <summary>
    /// 主窗口最小可接受尺寸。小于此值认为是被最小化 / 折叠态，直接拒绝截图。
    /// 默认 (600, 400)（与 auto-capture-wecom.ps1 一致）。
    /// </summary>
    public (int MinWidth, int MinHeight) MinWindowSize { get; set; } = (600, 400);
}
