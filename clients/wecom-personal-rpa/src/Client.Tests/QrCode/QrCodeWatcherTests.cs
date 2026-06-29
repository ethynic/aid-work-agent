using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.App.QrCode;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using Xunit;

namespace WeCom.PersonalRpa.Tests.QrCode;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-client-design.md §F7 / protocol.md §A.4
//
// QrCodeWatcher 状态机：
//   online → online       静默（不重复上报）
//   online → need_login   上报一次（带二维码）
//   need_login → need_login  每周期上报一次（防止 30s TTL 失效）
//   need_login → online    上报一次「恢复 online」
//   PS 失败 → 任何状态     不抛异常（记日志，下次重试）
//
// 测试方法：QrCodeWatcher.GetLoginStateFromPsAsync 为 protected internal virtual，
// 子类替换为固定返回；IAgentApiClient 用手写 stub 记录所有 ReportStatusAsync 调用。
// ====================================================================================

/// <summary>
/// 手写 IAgentApiClient stub：记录所有 ReportStatusAsync 调用，便于断言。
/// 不引入 NSubstitute/Moq（项目无此依赖，保持最小化）。
/// </summary>
internal sealed class StubAgentApiClient : IAgentApiClient
{
    public List<StatusPayload> ReportedStatuses { get; } = new();

    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default)
    {
        ReportedStatuses.Add(payload);
        return Task.FromResult(true);
    }

    // 以下方法本测试不关注，throw NotSupported 以防误用
    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success,
        string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public void Dispose() { }
}

/// <summary>
/// QrCodeWatcher 子类，替换 PS 调用为可编程委托。
/// </summary>
internal sealed class TestableQrCodeWatcher : QrCodeWatcher
{
    private readonly Func<Task<PowershellResult>> _probe;

    public TestableQrCodeWatcher(
        Func<Task<PowershellResult>> probe,
        IAgentApiClient apiClient,
        ClientOptions options)
        : base(
            new PowershellOpsInvoker(
                new PowershellOptions { Executable = "powershell.exe", OpsScript = "noop", InvokeTimeoutSeconds = 5 },
                Microsoft.Extensions.Logging.Abstractions.NullLogger<PowershellOpsInvoker>.Instance),
            apiClient,
            Options.Create(options),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<QrCodeWatcher>.Instance)
    {
        _probe = probe;
    }

    protected override Task<PowershellResult> GetLoginStateFromPsAsync() => _probe();
}

public sealed class QrCodeWatcherTests
{
    private static ClientOptions DefaultOptions() => new()
    {
        QrCode = new QrCodeOptions { PollIntervalSeconds = 30 },
    };

    private static PowershellResult OnlineResult() => new()
    {
        Success = true,
        Action = "get_login_state",
        State = "online",
        RawJson = "{\"success\":true,\"state\":\"online\"}",
    };

    private static PowershellResult NeedLoginResult(string qrBase64 = "iVBORw0KGgoAAAANSUhEUg==") => new()
    {
        Success = true,
        Action = "get_login_state",
        State = "need_login",
        RawJson = $"{{\"success\":true,\"state\":\"need_login\",\"qr_image_base64\":\"{qrBase64}\"}}",
    };

    private static PowershellResult QrExpiredResult() => new()
    {
        Success = true,
        Action = "get_login_state",
        State = "qr_expired",
        RawJson = "{\"success\":true,\"state\":\"qr_expired\",\"qr_image_base64\":\"expired_qr==\"}",
    };

    private static PowershellResult FailedResult(string code = "ps_timeout") => new()
    {
        Success = false,
        Action = "get_login_state",
        ErrorCode = code,
        ErrorMessage = "PS 失败模拟",
    };

    /// <summary>online 且 _lastState 也是 online → 不重复上报（避免噪音）。</summary>
    [Fact]
    public async Task PollOnce_OnlineState_WhenAlreadyOnline_DoesNotReport()
    {
        var api = new StubAgentApiClient();
        var watcher = new TestableQrCodeWatcher(() => Task.FromResult(OnlineResult()), api, DefaultOptions());

        await watcher.PollOnceAsync(); // 首次：上报 online
        await watcher.PollOnceAsync(); // 第二次：状态未变，不报

        Assert.Single(api.ReportedStatuses);
        Assert.Equal(AccountStatus.Online, api.ReportedStatuses[0].Status);
    }

    /// <summary>online → need_login 状态切换 → 必须上报带二维码。</summary>
    [Fact]
    public async Task PollOnce_StateTransitionOnlineToNeedLogin_ReportsStatusWithQr()
    {
        var api = new StubAgentApiClient();
        var stateQueue = new Queue<PowershellResult>(new[]
        {
            OnlineResult(),
            NeedLoginResult("QRCODE_BASE64_PAYLOAD"),
        });
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(stateQueue.Dequeue()),
            api, DefaultOptions());

        await watcher.PollOnceAsync(); // 上报 online
        await watcher.PollOnceAsync(); // 上报 need_login + 二维码

        Assert.Equal(2, api.ReportedStatuses.Count);
        Assert.Equal(AccountStatus.Online, api.ReportedStatuses[0].Status);

        var needLoginReport = api.ReportedStatuses[1];
        Assert.Equal(AccountStatus.NeedLogin, needLoginReport.Status);
        Assert.Equal("QRCODE_BASE64_PAYLOAD", needLoginReport.QrImageBase64);
        Assert.Contains("等待扫码", needLoginReport.Detail ?? "");
    }

    /// <summary>持续 need_login → 每周期都推一次（防 30s TTL 失效）。</summary>
    [Fact]
    public async Task PollOnce_ContinuousNeedLogin_ReportsEveryCycle()
    {
        var api = new StubAgentApiClient();
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(NeedLoginResult("QR_V1")), api, DefaultOptions());

        await watcher.PollOnceAsync();
        await watcher.PollOnceAsync();
        await watcher.PollOnceAsync();

        // 3 周期 3 次上报（首次切换 + 后续 2 周期持续推送）
        Assert.Equal(3, api.ReportedStatuses.Count);
        Assert.All(api.ReportedStatuses, p => Assert.Equal(AccountStatus.NeedLogin, p.Status));
    }

    /// <summary>need_login → online 恢复 → 上报「恢复上线」。</summary>
    [Fact]
    public async Task PollOnce_RecoveryFromNeedLoginToOnline_ReportsOnline()
    {
        var api = new StubAgentApiClient();
        var stateQueue = new Queue<PowershellResult>(new[]
        {
            NeedLoginResult("QR_RECOVERY"),
            OnlineResult(),
        });
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(stateQueue.Dequeue()), api, DefaultOptions());

        await watcher.PollOnceAsync();
        await watcher.PollOnceAsync();

        Assert.Equal(2, api.ReportedStatuses.Count);
        Assert.Equal(AccountStatus.NeedLogin, api.ReportedStatuses[0].Status);
        Assert.Equal(AccountStatus.Online, api.ReportedStatuses[1].Status);
        Assert.Contains("扫码成功", api.ReportedStatuses[1].Detail ?? "");
        // 恢复 online 时不应再带二维码
        Assert.Null(api.ReportedStatuses[1].QrImageBase64);
    }

    /// <summary>PS 失败（success=false）→ 不抛异常，记 warning，不调 ReportStatusAsync。</summary>
    [Fact]
    public async Task PollOnce_PsFailure_DoesNotCrashAndDoesNotReport()
    {
        var api = new StubAgentApiClient();
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(FailedResult("ps_timeout")), api, DefaultOptions());

        // 不应抛异常
        await watcher.PollOnceAsync();

        Assert.Empty(api.ReportedStatuses);
    }

    /// <summary>qr_expired 状态 → Detail 提示「已失效」。</summary>
    [Fact]
    public async Task PollOnce_QrExpiredState_ReportsWithExpiredDetail()
    {
        var api = new StubAgentApiClient();
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(QrExpiredResult()), api, DefaultOptions());

        await watcher.PollOnceAsync();

        Assert.Single(api.ReportedStatuses);
        Assert.Equal(AccountStatus.QrExpired, api.ReportedStatuses[0].Status);
        Assert.Contains("已失效", api.ReportedStatuses[0].Detail ?? "");
    }

    /// <summary>PS 返回的 RawJson 不含 qr_image_base64 字段 → QrImageBase64 应为 null（不抛）。</summary>
    [Fact]
    public async Task PollOnce_NeedLoginWithoutQrField_ReportsNullQr()
    {
        var api = new StubAgentApiClient();
        var result = new PowershellResult
        {
            Success = true,
            Action = "get_login_state",
            State = "need_login",
            RawJson = "{\"success\":true,\"state\":\"need_login\"}", // 无 qr_image_base64
        };
        var watcher = new TestableQrCodeWatcher(() => Task.FromResult(result), api, DefaultOptions());

        await watcher.PollOnceAsync();

        Assert.Single(api.ReportedStatuses);
        Assert.Null(api.ReportedStatuses[0].QrImageBase64);
    }

    /// <summary>ReportStatusAsync 自身抛异常时不应拖垮 watcher（下一周期继续工作）。</summary>
    [Fact]
    public async Task PollOnce_ApiReportThrows_DoesNotPropagate()
    {
        var throwingApi = new ThrowingAgentApiClient();
        var watcher = new TestableQrCodeWatcher(
            () => Task.FromResult(NeedLoginResult("QR_THROW")), throwingApi, DefaultOptions());

        // 第一周期：API 抛异常，被吞掉（不向上传播）
        await watcher.PollOnceAsync();
        // 第二周期：watcher 仍可继续工作（不卡死）
        await watcher.PollOnceAsync();

        Assert.Equal(2, throwingApi.CallCount);
    }
}

/// <summary>ReportStatusAsync 总是抛异常的 stub，用于验证 watcher 的容错。</summary>
internal sealed class ThrowingAgentApiClient : IAgentApiClient
{
    public int CallCount;

    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default)
    {
        CallCount++;
        throw new InvalidOperationException("模拟服务端不可用");
    }

    public Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<System.Net.WebSockets.ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<bool> ReportActionResultAsync(string requestId, bool success,
        string? errorCode = null, string? errorMessage = null, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public void Dispose() { }
}
