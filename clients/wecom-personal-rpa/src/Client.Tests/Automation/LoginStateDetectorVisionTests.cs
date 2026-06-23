using Xunit;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.WeCom;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Tests.Automation;

/// <summary>
/// LoginStateDetector 视觉路径单测（design §5.1）。
///
/// 验证：
///   1) IVisionLocator 定位到"搜索"input → 返回 Online；
///   2) 搜索框未命中 + "二维码"icon 命中 → 返回 NeedLogin（QrImageRef 可能 null，因主窗口未真附加 → 截图失败 fallback）；
///   3) 两者均未命中 → 返回 Offline（保守）。
/// </summary>
public sealed class LoginStateDetectorVisionTests
{
    [Fact]
    public void Detect_SearchBoxHit_ReturnsOnline()
    {
        var stub = new StubVisionLocator((type, kw) =>
        {
            if (type == "input" && kw == "搜索")
            {
                return new VisionProbeResult
                {
                    Bbox = new BoundingBox(10, 10, 100, 40),
                    Source = "stub",
                    ElementType = type,
                    LabelKeyword = kw,
                };
            }
            return null;
        });
        var mainWindow = new ReadyFakeMainWindow();
        var detector = new LoginStateDetector(stub, mainWindow);

        var state = detector.Detect();

        Assert.Equal(AccountStatus.Online, state.Status);
    }

    [Fact]
    public void Detect_QrIconHit_NoSearchBox_ReturnsNeedLogin()
    {
        var stub = new StubVisionLocator((type, kw) =>
        {
            if (type == "icon" && kw == "二维码")
            {
                return new VisionProbeResult
                {
                    Bbox = new BoundingBox(100, 100, 300, 300),
                    Source = "stub",
                    ElementType = type,
                    LabelKeyword = kw,
                };
            }
            return null; // 搜索框未命中
        });
        var mainWindow = new ReadyFakeMainWindow();
        var detector = new LoginStateDetector(stub, mainWindow);

        var state = detector.Detect();

        Assert.Equal(AccountStatus.NeedLogin, state.Status);
        // QrImageRef 可能为 null（主窗口未真附加 → GetRect 抛 → 截图失败 fallback）
        // 不强制非 null，只验证状态正确
    }

    [Fact]
    public void Detect_NeitherHit_ReturnsOffline()
    {
        var stub = new StubVisionLocator((_, _) => null);
        var mainWindow = new ReadyFakeMainWindow();
        var detector = new LoginStateDetector(stub, mainWindow);

        var state = detector.Detect();

        Assert.Equal(AccountStatus.Offline, state.Status);
    }

    // -------- Stubs --------

    internal sealed class StubVisionLocator : IVisionLocator
    {
        private readonly Func<string, string, VisionProbeResult?> _handler;
        public StubVisionLocator(Func<string, string, VisionProbeResult?> handler) => _handler = handler;
        public Task<VisionProbeResult?> LocateAsync(string elementType, string labelKeyword, CancellationToken ct = default)
            => Task.FromResult(_handler(elementType, labelKeyword));
        public Task InvalidateCacheAsync() => Task.CompletedTask;
    }

    /// <summary>主窗口就绪假实现：Handle 非 0 + IsVisible=true + GetRect 返回固定矩形。</summary>
    internal sealed class ReadyFakeMainWindow : WeComMainWindow
    {
        public ReadyFakeMainWindow() : base("WeWorkWindow")
        {
            var prop = typeof(WeComMainWindow).GetProperty("Handle");
            prop!.SetValue(this, new IntPtr(1));
        }

        public override bool IsVisible => true;

        public override bool TryFind() => true;

        public override (int Left, int Top, int Width, int Height) GetRect() => (0, 0, 1280, 800);

        public override bool BringToForeground() => true;
    }
}
