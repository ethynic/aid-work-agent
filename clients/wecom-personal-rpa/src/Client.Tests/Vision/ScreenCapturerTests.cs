using System.Drawing;
using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A1（156-162 行，验收标准）。
//
// 本测试只覆盖 PixelSanityChecker 纯算法（不依赖真实企微运行）：
//   1) 纯白 100x100 图 → WhiteRatio = 1.0（全部量化为 240,240,240）
//   2) 3x3 单色图 → ColorDiversity = 1
//   3) 16x16 灰阶图（每像素 RGB 都不同）→ ColorDiversity ≥ 16
//
// ScreenCapturer 主类依赖真实企微运行（窗口查找 / SetForegroundWindow / 注册表），
// 不写真实截图单测，留给阶段 3B 真机回归覆盖。
// ====================================================================================

public sealed class ScreenCapturerTests
{
    [Fact]
    public void PixelSanityChecker_PureWhiteImage_WhiteRatioIsOne()
    {
        // 100x100 全白图，所有像素 RGB(255,255,255) → 量化后都是 (240,240,240)
        using var bmp = new Bitmap(100, 100);
        using (var g = Graphics.FromImage(bmp))
        {
            g.Clear(Color.White);
        }

        var (whiteRatio, colorDiversity) = PixelSanityChecker.Analyze(bmp);

        Assert.Equal(1.0, whiteRatio, precision: 6);
        Assert.Equal(1, colorDiversity);
    }

    [Fact]
    public void PixelSanityChecker_Monochrome3x3_ColorDiversityIsOne()
    {
        // 3x3 纯色图（单一颜色），量化后只有 1 种 key
        // 注：3x3 的 step = max(1, 3/100) = 1，所以会采集全部 9 个像素
        using var bmp = new Bitmap(3, 3);
        using (var g = Graphics.FromImage(bmp))
        {
            g.Clear(Color.FromArgb(120, 60, 200));
        }

        var (whiteRatio, colorDiversity) = PixelSanityChecker.Analyze(bmp);

        Assert.Equal(1, colorDiversity);
        Assert.True(whiteRatio < 0.01, $"非白色图白色占比应接近 0，实际 {whiteRatio}");
    }

    [Fact]
    public void PixelSanityChecker_16StepGrayscale_ColorDiversityAtLeast16()
    {
        // 16x16 灰阶图：每列一种灰度，共 16 种不同的 R 值（0,16,32,...,240）
        // step = max(1, 16/100) = 1，全部像素都被采样
        // 每种灰度量化后落在不同的 16x16 桶，ColorDiversity ≥ 16
        using var bmp = new Bitmap(16, 16);
        for (int x = 0; x < 16; x++)
        {
            int gray = x * 16; // 0, 16, 32, ..., 240
            for (int y = 0; y < 16; y++)
            {
                bmp.SetPixel(x, y, Color.FromArgb(gray, gray, gray));
            }
        }

        var (whiteRatio, colorDiversity) = PixelSanityChecker.Analyze(bmp);

        Assert.True(colorDiversity >= 16,
            $"16 种灰阶图的颜色多样性应 ≥ 16，实际 {colorDiversity}");
        // 最后一列 gray=240 → 量化为 (240,240,240) 占 1/16 ≈ 6.25%
        Assert.True(whiteRatio < 0.5,
            $"16 种灰阶图的白色占比应 < 0.5（仅最后一列计入白色），实际 {whiteRatio}");
    }

    [Fact]
    public void PixelSanityChecker_NullBitmap_ThrowsArgumentNullException()
    {
        Assert.Throws<ArgumentNullException>(() => PixelSanityChecker.Analyze(null!));
    }

    [Fact]
    public void ScreenshotOptions_Defaults_MatchPlanSpec()
    {
        // 验收：默认阈值与 plan §A1（118-124 行）一致
        var opts = new ScreenshotOptions();

        Assert.True(opts.PreForegroundCheck);
        Assert.True(opts.PixelSanityCheck);
        Assert.Equal(0.5, opts.MaxWhiteRatio);
        Assert.Equal(30, opts.MinColorDiversity);
    }

    [Fact]
    public void ScreenCapturer_DefaultConstants_MatchPlanSpec()
    {
        // 验收：默认类名与 plan 一致（新版 ScreenCapturer 把窗口查找外包给 PowerShell 脚本，
        // C# 不再持有 ExpectedTitleKeyword 字段）
        Assert.Equal("WeWorkWindow", ScreenCapturer.DefaultWindowClassName);
    }
}
