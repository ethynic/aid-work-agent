// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A1（126-136 行）。
//                  design §3.2 WindowFingerprint record。
//
// 窗口指纹：视觉缓存层的缓存键输入。任意字段变化视为窗口变化，触发整窗口缓存失效。
// 字段全部为值类型（int / double / string），record 自带值相等 + GetHashCode，适合 Dictionary key。
//
// 与 A2 协同：A2 当前用临时 WindowFingerprintPlaceholder（字段完全一致），
// 集成阶段由项目维护者统一把 Placeholder 替换为本 record（SQL 内部解耦，签名变更不影响表结构）。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 企微主窗口指纹（缓存键的输入）。任意字段变化都视为窗口变化，触发整窗口缓存失效。
/// </summary>
/// <param name="WindowClass">窗口类名，如 "WeWorkWindow"。</param>
/// <param name="X">窗口左上角屏幕 X 坐标（像素）。</param>
/// <param name="Y">窗口左上角屏幕 Y 坐标（像素）。</param>
/// <param name="Width">窗口宽度（像素）。</param>
/// <param name="Height">窗口高度（像素）。</param>
/// <param name="DpiScale">系统 DPI 缩放（1.0 / 1.25 / 1.5 / 2.0），来自 Graphics.DpiX / 96。</param>
/// <param name="WeComVersion">企微版本号，从注册表读取（如 "4.1.38.18812"），读失败填 "unknown"。</param>
public sealed record WindowFingerprint(
    string WindowClass,
    int X,
    int Y,
    int Width,
    int Height,
    double DpiScale,
    string WeComVersion)
{
    /// <summary>
    /// 逐字段精确匹配。任意字段变化（含 1 像素位移）都返回 false。
    /// DpiScale 用直接 == 比较（采集端已量化到 96 的整数倍比例，浮点误差可忽略）。
    /// </summary>
    /// <param name="other">待比较的另一指纹，null 视为失配。</param>
    /// <returns>所有字段完全相等返回 true，否则 false。</returns>
    public bool Matches(WindowFingerprint? other)
    {
        if (other is null)
        {
            return false;
        }

        return WindowClass == other.WindowClass
               && X == other.X
               && Y == other.Y
               && Width == other.Width
               && Height == other.Height
               && DpiScale == other.DpiScale
               && WeComVersion == other.WeComVersion;
    }
}
