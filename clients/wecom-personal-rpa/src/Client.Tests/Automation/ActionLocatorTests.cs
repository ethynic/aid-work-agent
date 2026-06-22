using Xunit;

namespace WeCom.PersonalRpa.Tests.Automation;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-protocol.md §C.4（跨工程接口）与
// design.md §6.2（三层自动化策略）：
//
//   Layer 1: FlaUI UIA3/UIA2 读取控件树
//       ↓ 失败
//   Layer 2: Win32 窗口相对坐标 + SendInput + 剪贴板输入
//       ↓ 失败
//   Layer 3: OpenCvSharp 模板匹配 + 截图定位
//
// 接口契约（protocol.md §C.4）：
//   IActionExecutor   Core 定义，Automation 实现（执行单个 SendAction）
//   IWeComAutomation  Core 定义，Automation 实现（FlaUI/Win32/OpenCvSharp 三层定位与会话操作）
//   INodesConfig      Core 定义，Core 实现（加载 wecom_nodes.yaml）
//
// 本测试 mock 三层定位器，验证 ActionLocator 的降级链：
//   Layer1 命中 → 不调用 Layer2/3；
//   Layer1 失败 → Layer2 命中 → 不调用 Layer3；
//   Layer1+2 失败 → Layer3 兜底；
//   三层全失败 → 抛 AutomationLocatorException。
//
// IActionLocator / 三层接口由 Client.Core 定义、Client.Automation 实现（并行 agent）。
// 为让本测试独立可编译，这里在测试本地定义最小三层接口与 ActionLocator 合约，
// 命名/语义对齐契约。待 Core/Automation 真实类型落地后，把测试内的 mock 接口
// 替换为真实接口即可。
// ====================================================================================

internal enum LocateOutcome
{
    Hit,
    Miss,
    Throw,
}

/// <summary>三层定位器统一返回：定位成功返回坐标，失败返回 null。</summary>
internal readonly record struct LocatorResult(bool Hit, double X, double Y, string Layer);

/// <summary>三层定位器的 mock 合约。</summary>
internal interface ILocateLayer
{
    string Name { get; }
    LocateOutcome Outcome { get; set; }
    int InvokedCount { get; }
    LocatorResult Locate();
}

internal sealed class FakeLayer : ILocateLayer
{
    public string Name { get; }
    public LocateOutcome Outcome { get; set; }
    public int InvokedCount { get; private set; }
    public FakeLayer(string name, LocateOutcome outcome) { Name = name; Outcome = outcome; }
    public LocatorResult Locate()
    {
        InvokedCount++;
        return Outcome switch
        {
            LocateOutcome.Hit => new LocatorResult(true, 100, 200, Name),
            LocateOutcome.Miss => new LocatorResult(false, 0, 0, Name),
            LocateOutcome.Throw => throw new InvalidOperationException($"{Name} 定位异常"),
            _ => throw new ArgumentOutOfRangeException(),
        };
    }
}

internal sealed class AutomationLocatorException : Exception
{
    public AutomationLocatorException(string msg) : base(msg) { }
}

/// <summary>
/// ActionLocator：按 Layer1→Layer2→Layer3 顺序尝试定位，命中即返回，全失败抛异常。
/// 对齐 design.md §6.2 三层降级策略。
/// </summary>
internal sealed class ActionLocator
{
    private readonly ILocateLayer _layer1;
    private readonly ILocateLayer _layer2;
    private readonly ILocateLayer _layer3;

    public ActionLocator(ILocateLayer layer1, ILocateLayer layer2, ILocateLayer layer3)
    {
        _layer1 = layer1; _layer2 = layer2; _layer3 = layer3;
    }

    public LocatorResult Locate()
    {
        if (TryLocate(_layer1, out var r)) return r;
        if (TryLocate(_layer2, out r)) return r;
        if (TryLocate(_layer3, out r)) return r;
        throw new AutomationLocatorException("三层定位全部失败：FlaUI → Win32 → OpenCvSharp 均未命中");
    }

    private static bool TryLocate(ILocateLayer layer, out LocatorResult result)
    {
        try
        {
            result = layer.Locate();
            return result.Hit;
        }
        catch (AutomationLocatorException) { throw; }
        catch
        {
            // 某层抛异常视为该层失败，继续降级到下一层（design.md §6.2）
            result = default;
            return false;
        }
    }
}

public class ActionLocatorTests
{
    [Fact(DisplayName = "Layer1 命中：不调用 Layer2 / Layer3")]
    public void Layer1_Hit_SkipsLayer2And3()
    {
        var l1 = new FakeLayer("FlaUI", LocateOutcome.Hit);
        var l2 = new FakeLayer("Win32", LocateOutcome.Hit);
        var l3 = new FakeLayer("OpenCvSharp", LocateOutcome.Hit);
        var locator = new ActionLocator(l1, l2, l3);

        var result = locator.Locate();

        Assert.True(result.Hit);
        Assert.Equal("FlaUI", result.Layer);
        Assert.Equal(1, l1.InvokedCount);
        Assert.Equal(0, l2.InvokedCount);
        Assert.Equal(0, l3.InvokedCount);
    }

    [Fact(DisplayName = "Layer1 失败 → Layer2 命中：不调用 Layer3")]
    public void Layer1_Miss_Layer2_Hit_SkipsLayer3()
    {
        var l1 = new FakeLayer("FlaUI", LocateOutcome.Miss);
        var l2 = new FakeLayer("Win32", LocateOutcome.Hit);
        var l3 = new FakeLayer("OpenCvSharp", LocateOutcome.Hit);
        var locator = new ActionLocator(l1, l2, l3);

        var result = locator.Locate();

        Assert.True(result.Hit);
        Assert.Equal("Win32", result.Layer);
        Assert.Equal(1, l1.InvokedCount);
        Assert.Equal(1, l2.InvokedCount);
        Assert.Equal(0, l3.InvokedCount);
    }

    [Fact(DisplayName = "Layer1 抛异常 → 降级到 Layer2 命中")]
    public void Layer1_Throw_FallsBack_To_Layer2()
    {
        var l1 = new FakeLayer("FlaUI", LocateOutcome.Throw);
        var l2 = new FakeLayer("Win32", LocateOutcome.Hit);
        var l3 = new FakeLayer("OpenCvSharp", LocateOutcome.Hit);
        var locator = new ActionLocator(l1, l2, l3);

        var result = locator.Locate();

        Assert.True(result.Hit);
        Assert.Equal("Win32", result.Layer);
        Assert.Equal(1, l1.InvokedCount);
    }

    [Fact(DisplayName = "Layer1+2 失败 → Layer3 兜底命中")]
    public void Layer1_And_Layer2_Fail_Layer3_Fallback_Hits()
    {
        var l1 = new FakeLayer("FlaUI", LocateOutcome.Miss);
        var l2 = new FakeLayer("Win32", LocateOutcome.Throw);
        var l3 = new FakeLayer("OpenCvSharp", LocateOutcome.Hit);
        var locator = new ActionLocator(l1, l2, l3);

        var result = locator.Locate();

        Assert.True(result.Hit);
        Assert.Equal("OpenCvSharp", result.Layer);
        Assert.Equal(1, l1.InvokedCount);
        Assert.Equal(1, l2.InvokedCount);
        Assert.Equal(1, l3.InvokedCount);
    }

    [Fact(DisplayName = "三层全部失败 → 抛 AutomationLocatorException")]
    public void All_Three_Layers_Fail_Throws_AutomationLocatorException()
    {
        var l1 = new FakeLayer("FlaUI", LocateOutcome.Miss);
        var l2 = new FakeLayer("Win32", LocateOutcome.Miss);
        var l3 = new FakeLayer("OpenCvSharp", LocateOutcome.Miss);
        var locator = new ActionLocator(l1, l2, l3);

        var ex = Assert.Throws<AutomationLocatorException>(() => locator.Locate());
        Assert.Contains("三层定位全部失败", ex.Message);
        Assert.Equal(1, l1.InvokedCount);
        Assert.Equal(1, l2.InvokedCount);
        Assert.Equal(1, l3.InvokedCount);
    }
}
