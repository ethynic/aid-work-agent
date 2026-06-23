using System;
using Xunit;
using WeCom.PersonalRpa.Automation;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Automation.WeCom;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.Tests.Automation;

/// <summary>
/// WeComAutomation 视觉路径单测（design §3.5 + plan 任务 B）。
///
/// 用最小化手写 stub class 注入（项目未引 Moq），不依赖真实 Win32 / 视觉模型。
/// 验证：
///   1) SendText 视觉命中 + 主窗口就绪 → Click + TypeText 被调用，返回 true；
///   2) SendText 视觉未命中（LocateAsync 返回 null）→ 返回 false；
///   3) SendText 空文本 → 返回 false；
///   4) NavigateToConversation 在未注入视觉时返回失败（兼容性测试）。
/// </summary>
public sealed class WeComAutomationVisionTests
{
    [Fact]
    public void SendText_VisionHit_And_WindowReady_ReturnsTrue()
    {
        var stubLocator = new StubVisionLocator((type, kw) => new VisionProbeResult
        {
            Bbox = new BoundingBox(100, 200, 200, 240),
            Source = "stub",
            ElementType = type,
            LabelKeyword = kw,
            Confidence = 1.0,
        });
        var spyInput = new SpyInputExecutor();
        var guard = new ClipboardGuard();
        var mainWindow = new FakeReadyMainWindow();
        var automation = new WeComAutomation(stubLocator, spyInput, guard, mainWindow);

        bool ok = automation.SendText("hello");

        Assert.True(ok);
        // 验证 Click + TypeText 都被调用一次
        Assert.Single(spyInput.ClickCalls);
        Assert.Single(spyInput.TypeTextCalls);
        Assert.Equal("hello", spyInput.TypeTextCalls[0].text);
        Assert.False(spyInput.TypeTextCalls[0].pressEnterAfter);
    }

    [Fact]
    public void SendText_VisionMiss_ReturnsFalse()
    {
        var stubLocator = new StubVisionLocator((_, _) => null);
        var spyInput = new SpyInputExecutor();
        var guard = new ClipboardGuard();
        var mainWindow = new FakeReadyMainWindow();
        var automation = new WeComAutomation(stubLocator, spyInput, guard, mainWindow);

        bool ok = automation.SendText("hello");

        Assert.False(ok);
        // 视觉未命中 → 不应点击 / 不应输入
        Assert.Empty(spyInput.ClickCalls);
        Assert.Empty(spyInput.TypeTextCalls);
    }

    [Fact]
    public void SendText_EmptyText_ReturnsFalse()
    {
        var stubLocator = new StubVisionLocator((_, _) => null);
        var spyInput = new SpyInputExecutor();
        var guard = new ClipboardGuard();
        var mainWindow = new FakeReadyMainWindow();
        var automation = new WeComAutomation(stubLocator, spyInput, guard, mainWindow);

        Assert.False(automation.SendText(""));
        Assert.False(automation.SendText(string.Empty));
        Assert.Empty(spyInput.ClickCalls);
    }

    [Fact]
    public void NavigateToConversation_DefaultCtor_NoVision_ReturnsFailure()
    {
        var automation = new WeComAutomation();
        var result = automation.NavigateToConversation("文件传输助手");

        Assert.False(result.Success);
        Assert.Equal("automation_layer_error", result.ErrorCode);
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

    internal sealed class SpyInputExecutor : InputExecutor
    {
        public List<(int x, int y)> ClickCalls { get; } = new();
        public List<(string text, bool pressEnterAfter)> TypeTextCalls { get; } = new();

        public override void ClickElement(BoundingBox bbox, (int Left, int Top) windowOrigin)
        {
            (int cx, int cy) = bbox.Center;
            ClickCalls.Add((windowOrigin.Left + cx, windowOrigin.Top + cy));
        }

        public override void TypeText(string text, bool pressEnterAfter = false)
        {
            TypeTextCalls.Add((text, pressEnterAfter));
        }
    }

    /// <summary>主窗口就绪的假实现：TryFind 返回 true（设置非零句柄），GetRect 返回固定矩形。</summary>
    internal sealed class FakeReadyMainWindow : WeComMainWindow
    {
        public FakeReadyMainWindow() : base("WeWorkWindow")
        {
            // 通过反射设置 private Handle 字段（避免触发真 FindWindow）
            var prop = typeof(WeComMainWindow).GetProperty("Handle");
            prop!.SetValue(this, new IntPtr(1));
        }

        public override bool TryFind() => true;

        public override (int Left, int Top, int Width, int Height) GetRect() => (0, 0, 1280, 800);

        public override bool BringToForeground() => true;
    }
}
