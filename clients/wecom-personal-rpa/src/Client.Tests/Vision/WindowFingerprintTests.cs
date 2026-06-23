using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A1（156-162 行，验收标准）。
//
// 覆盖 WindowFingerprint.Matches 的 8 个 case：
//   1) 字段全等 → true（基准）
//   2) WindowClass 变化 → false
//   3) X 变化 → false
//   4) Y 变化 → false
//   5) Width 变化 → false
//   6) Height 变化 → false
//   7) DpiScale 变化 → false
//   8) WeComVersion 变化 → false
//
// 同时覆盖 null 兜底（调用方传入 null 应返回 false）。
// 任何字段失配都触发整窗口缓存失效，是视觉缓存层正确性的基础，所以每个字段单独断言。
// ====================================================================================

public sealed class WindowFingerprintTests
{
    /// <summary>构造一份稳定的基准指纹，所有 case 基于它变化 1 个字段。</summary>
    private static WindowFingerprint Baseline() => new(
        WindowClass: "WeWorkWindow",
        X: 100,
        Y: 200,
        Width: 1200,
        Height: 800,
        DpiScale: 1.25,
        WeComVersion: "4.1.38.18812");

    [Fact]
    public void Matches_AllFieldsEqual_ReturnsTrue()
    {
        var a = Baseline();
        var b = Baseline();

        Assert.True(a.Matches(b));
    }

    [Fact]
    public void Matches_SameInstance_ReturnsTrue()
    {
        WindowFingerprint fp = Baseline();

        Assert.True(fp.Matches(fp));
    }

    [Fact]
    public void Matches_OtherIsNull_ReturnsFalse()
    {
        WindowFingerprint fp = Baseline();

        Assert.False(fp.Matches(null));
    }

    [Fact]
    public void Matches_WindowClassDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { WindowClass = "SomeOtherWindow" };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_XDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { X = a.X + 1 };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_YDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { Y = a.Y + 1 };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_WidthDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { Width = a.Width - 1 };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_HeightDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { Height = a.Height - 1 };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_DpiScaleDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { DpiScale = 1.5 };

        Assert.False(a.Matches(b));
    }

    [Fact]
    public void Matches_WeComVersionDifferent_ReturnsFalse()
    {
        var a = Baseline();
        var b = a with { WeComVersion = "4.1.39.10000" };

        Assert.False(a.Matches(b));
    }
}
