// ====================================================================================
// 任务 A3 单测：BoundingBox 越界检查 / 中心点 / IoU。
// 契约：plan-wecom-personal-rpa-vision.md §A3 验收标准（454 行）。
// ====================================================================================

using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

/// <summary>BoundingBox 单测。</summary>
public sealed class BoundingBoxTests
{
    // ========================================================================
    // IsWithin —— 越界检查 4 case（左/上/右/下越界）
    // 业务意义：Qwen3-VL 偶发把屏幕绝对坐标当相对坐标返回，越界检查用于判定模型幻觉。
    // ========================================================================

    [Fact]
    public void IsWithin_LeftOverflow_ReturnsFalse()
    {
        var bbox = new BoundingBox(-10, 10, 50, 50);
        Assert.False(bbox.IsWithin(100, 100, tolerance: 5));
    }

    [Fact]
    public void IsWithin_TopOverflow_ReturnsFalse()
    {
        var bbox = new BoundingBox(10, -10, 50, 50);
        Assert.False(bbox.IsWithin(100, 100, tolerance: 5));
    }

    [Fact]
    public void IsWithin_RightOverflow_ReturnsFalse()
    {
        var bbox = new BoundingBox(50, 10, 110, 50);
        Assert.False(bbox.IsWithin(100, 100, tolerance: 5));
    }

    [Fact]
    public void IsWithin_BottomOverflow_ReturnsFalse()
    {
        var bbox = new BoundingBox(10, 50, 50, 110);
        Assert.False(bbox.IsWithin(100, 100, tolerance: 5));
    }

    [Fact]
    public void IsWithin_WithinTolerance_ReturnsTrue()
    {
        // 右下角轻微越界 3 像素 < tolerance=5，仍视为合法
        var bbox = new BoundingBox(10, 10, 103, 103);
        Assert.True(bbox.IsWithin(100, 100, tolerance: 5));
    }

    [Fact]
    public void IsWithin_FullyInside_ReturnsTrue()
    {
        var bbox = new BoundingBox(10, 10, 50, 50);
        Assert.True(bbox.IsWithin(100, 100, tolerance: 0));
    }

    [Fact]
    public void IsWithin_InvertedBox_ReturnsFalse()
    {
        // X1>X2 的非法 bbox 视为越界（防止模型乱序返回）
        var bbox = new BoundingBox(50, 10, 10, 50);
        Assert.False(bbox.IsWithin(100, 100));
    }

    // ========================================================================
    // IoU —— 缓存命中后做"窗口未变"二次校验
    // ========================================================================

    [Fact]
    public void IoU_IdenticalBoxes_ReturnsOne()
    {
        var a = new BoundingBox(10, 10, 50, 50);
        var b = new BoundingBox(10, 10, 50, 50);
        Assert.Equal(1.0, a.IoU(b), precision: 6);
    }

    [Fact]
    public void IoU_NoOverlap_ReturnsZero()
    {
        var a = new BoundingBox(0, 0, 10, 10);
        var b = new BoundingBox(100, 100, 110, 110);
        Assert.Equal(0.0, a.IoU(b));
    }

    [Fact]
    public void IoU_ContainedBox_ReturnsProperFraction()
    {
        // 小框完全在大框内：交=小，并=大
        var outer = new BoundingBox(0, 0, 100, 100);    // area=10000
        var inner = new BoundingBox(25, 25, 75, 75);    // area=2500
        // IoU = 2500 / 10000 = 0.25
        Assert.Equal(0.25, outer.IoU(inner), precision: 6);
    }

    [Fact]
    public void IoU_PartialOverlap_ReturnsCorrectValue()
    {
        // 两个 10x10 框，相交 5x5 = 25；并 = 100+100-25 = 175
        var a = new BoundingBox(0, 0, 10, 10);
        var b = new BoundingBox(5, 5, 15, 15);
        Assert.Equal(25.0 / 175.0, a.IoU(b), precision: 6);
    }

    // ========================================================================
    // Center / Width / Height
    // ========================================================================

    [Fact]
    public void Center_OfUnitBox_IsFiveFive()
    {
        var bbox = new BoundingBox(0, 0, 10, 10);
        Assert.Equal((5, 5), bbox.Center);
    }

    [Fact]
    public void WidthHeight_OfArbitraryBox_Correct()
    {
        var bbox = new BoundingBox(10, 20, 110, 220);
        Assert.Equal(100, bbox.Width);
        Assert.Equal(200, bbox.Height);
    }

    [Fact]
    public void Center_OfOddSizedBox_RoundsDownToFloor()
    {
        // (10 + 15) / 2 = 12（int 除法向零截断）
        var bbox = new BoundingBox(10, 10, 15, 15);
        Assert.Equal((12, 12), bbox.Center);
    }
}
