using System;
using Xunit;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Tests.Automation;

/// <summary>
/// InputExecutor 单测。
///
/// 验证点（design §3.5）：
///   1) bbox 中心点 + windowOrigin → 屏幕坐标换算正确；
///   2) 越界 bbox 抛 ArgumentException（loud failure）；
///   3) null bbox / null text 抛 ArgumentNullException。
///
/// 注意：本测试只验证坐标换算与参数校验，不真发 SendInput（无法在 CI 沙箱里验鼠标移动）。
/// ClickElement 的换算通过反射 / 内部状态验证不可行（无状态暴露），
/// 因此核心验证依赖"越界抛 ArgumentException"——这一路径走的就是换算后的坐标。
/// </summary>
public sealed class InputExecutorTests
{
    [Fact]
    public void ClickElement_OutOfBounds_Bbox_Throws_ArgumentException()
    {
        // 故意构造一个换算后必然越出屏幕的 bbox + windowOrigin。
        // windowOrigin (Left, Top) = (50000, 50000) → 加上 bbox 中心 (150, 150) → 屏幕 (50150, 50150)，
        // 远超常见屏幕分辨率（最高 8K = 7680）。任何屏幕都会触发越界。
        var bbox = new BoundingBox(100, 100, 200, 200); // 中心 (150, 150)
        var executor = new InputExecutor();

        var ex = Assert.Throws<ArgumentException>(() =>
            executor.ClickElement(bbox, (Left: 50000, Top: 50000)));

        // 验证异常信息含换算后的坐标，确认换算逻辑确实运行了
        // 实际屏幕坐标 = 50000 + 150 = 50150
        Assert.Contains("50150", ex.Message);
    }

    [Fact]
    public void ClickElement_Null_Bbox_Throws_ArgumentNullException()
    {
        var executor = new InputExecutor();
        Assert.Throws<ArgumentNullException>(() => executor.ClickElement(null!, (Left: 0, Top: 0)));
    }

    [Fact]
    public void TypeText_Null_Text_Throws_ArgumentNullException()
    {
        var executor = new InputExecutor();
        Assert.Throws<ArgumentNullException>(() => executor.TypeText(null!));
    }

    [Fact]
    public void BoundingBox_Center_Calculation_Matches_Executor_Assumption()
    {
        // 间接验证 InputExecutor 的坐标换算假设：bbox.Center 是 ((X1+X2)/2, (Y1+Y2)/2)。
        // 这是对 BoundingBox API 契约的回归保护（若 Center 算法变化，InputExecutor 换算也要同步改）。
        var bbox = new BoundingBox(100, 100, 200, 200);
        (int cx, int cy) = bbox.Center;
        Assert.Equal(150, cx);
        Assert.Equal(150, cy);
    }
}
