using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.Health;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Health;

// ====================================================================================
// 契约来源：docs/system/wecom-personal-rpa-client-design.md §9.1 / §F5
//
// DesktopHealthSupervisor：
//   - 60s 周期 Timer + SemaphoreSlim 串行化
//   - 检测项优先级：进程消失(offline) > 锁屏(desktop_locked) > 窗口不可见(window_not_visible)
//   - 状态去重：与上次相同则不上报
//   - 全部恢复 → 上报 online（仅当上次非 online）
//
// 测试方法：子类替换 IsWeComProcessAlive / IsDesktopLocked / IsWeComWindowVisible，
// IAgentApiClient 用手写 stub 记录所有 ReportStatusAsync 调用。
// ====================================================================================

/// <summary>手写 IAgentApiClient stub：记录所有 ReportStatusAsync 调用。</summary>
internal sealed class HealthStubAgentApiClient : IAgentApiClient
{
    public List<StatusPayload> ReportedStatuses { get; } = new();

    public Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default)
    {
        ReportedStatuses.Add(payload);
        return Task.FromResult(true);
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
    public Task<bool> ReportInboundAsync(InboundEvent env, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public void Dispose() { }
}

/// <summary>
/// 可编程探针的 DesktopHealthSupervisor 子类。
/// 通过 public 字段注入每个探针的返回值。
/// </summary>
internal sealed class TestableDesktopHealthSupervisor : DesktopHealthSupervisor
{
    public Func<bool> ProbeProcessAlive { get; set; } = () => true;
    public Func<bool> ProbeDesktopLocked { get; set; } = () => false;
    public Func<bool> ProbeWindowVisible { get; set; } = () => true;

    public TestableDesktopHealthSupervisor(IAgentApiClient apiClient)
        : base(apiClient, Microsoft.Extensions.Logging.Abstractions.NullLogger<DesktopHealthSupervisor>.Instance,
            TimeSpan.FromMilliseconds(50)) // 测试用极短周期（不实际等待）
    {
    }

    protected internal override bool IsWeComProcessAlive() => ProbeProcessAlive();
    protected internal override bool IsDesktopLocked() => ProbeDesktopLocked();
    protected internal override bool IsWeComWindowVisible() => ProbeWindowVisible();
}

public sealed class DesktopHealthSupervisorTests
{
    /// <summary>StartAsync 后 Timer 应立即触发首次轮询（dueTime=0）。</summary>
    [Fact]
    public async Task StartAsync_LaunchesTimer_ImmediateFirstPoll()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => false; // 触发 offline 上报

        await sup.StartAsync(CancellationToken.None);
        // 等 100ms 让首次 Timer tick（dueTime=0）完成
        await Task.Delay(100);
        await sup.StopAsync(CancellationToken.None);

        // 至少有 1 次上报（offline）
        Assert.NotEmpty(api.ReportedStatuses);
        Assert.Contains(api.ReportedStatuses, p => p.Status == AccountStatus.Offline);
    }

    /// <summary>企微进程不存在 → 上报 offline。</summary>
    [Fact]
    public async Task PollOnce_WecomProcessMissing_ReportsOfflineStatus()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => false;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => true;

        await sup.PollOnceAsync();

        Assert.Single(api.ReportedStatuses);
        Assert.Equal(AccountStatus.Offline, api.ReportedStatuses[0].Status);
    }

    /// <summary>企微进程存在 + 桌面锁屏 → 上报 desktop_locked（offline 优先级更高，但进程在则不会判 offline）。</summary>
    [Fact]
    public async Task PollOnce_DesktopLocked_ReportsDesktopLockedStatus()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => true;
        sup.ProbeDesktopLocked = () => true;
        sup.ProbeWindowVisible = () => true;

        await sup.PollOnceAsync();

        Assert.Single(api.ReportedStatuses);
        Assert.Equal(AccountStatus.DesktopLocked, api.ReportedStatuses[0].Status);
    }

    /// <summary>企微窗口不可见 → 上报 window_not_visible。</summary>
    [Fact]
    public async Task PollOnce_WindowNotVisible_ReportsWindowNotVisibleStatus()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => true;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => false;

        await sup.PollOnceAsync();

        Assert.Single(api.ReportedStatuses);
        Assert.Equal(AccountStatus.WindowNotVisible, api.ReportedStatuses[0].Status);
    }

    /// <summary>全部正常 + 从未上报过 → 不上报（避免启动初期噪音）。</summary>
    [Fact]
    public async Task PollOnce_AllHealthyFirstTime_DoesNotReport()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => true;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => true;

        await sup.PollOnceAsync();

        Assert.Empty(api.ReportedStatuses);
    }

    /// <summary>状态未变化 → 不重复上报（去重）。</summary>
    [Fact]
    public async Task PollOnce_StateUnchanged_DoesNotDuplicateReport()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => false; // 持续 offline

        await sup.PollOnceAsync(); // 首次：上报 offline
        await sup.PollOnceAsync(); // 第二次：状态未变，不报

        Assert.Single(api.ReportedStatuses);
    }

    /// <summary>从 offline 恢复正常 → 上报 online（detail 含"恢复"）。</summary>
    [Fact]
    public async Task PollOnce_RecoveryFromOffline_ReportsOnline()
    {
        var api = new HealthStubAgentApiClient();
        var procAlive = false;
        var sup = new TestableDesktopHealthSupervisor(api);
        using var __d = sup;
        sup.ProbeProcessAlive = () => procAlive;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => true;

        procAlive = false;
        await sup.PollOnceAsync(); // offline
        procAlive = true;
        await sup.PollOnceAsync(); // 恢复 online

        Assert.Equal(2, api.ReportedStatuses.Count);
        Assert.Equal(AccountStatus.Offline, api.ReportedStatuses[0].Status);
        Assert.Equal(AccountStatus.Online, api.ReportedStatuses[1].Status);
        Assert.Contains("恢复", api.ReportedStatuses[1].Detail ?? "");
    }

    /// <summary>ReportStatusAsync 抛异常 → 不应传播（下次重试）。</summary>
    [Fact]
    public async Task PollOnce_ApiReportThrows_DoesNotPropagate()
    {
        var throwingApi = new ThrowingHealthApiClient();
        var sup = new TestableDesktopHealthSupervisor(throwingApi);
        using var __d = sup;
        sup.ProbeProcessAlive = () => false;

        await sup.PollOnceAsync(); // 不抛异常
        await sup.PollOnceAsync(); // 仍可继续

        Assert.Equal(2, throwingApi.CallCount);
    }

    /// <summary>Dispose 后 Timer 应停止（多次 Dispose 不抛）。</summary>
    [Fact]
    public void Dispose_StopsTimer_CanCallMultipleTimes()
    {
        var api = new HealthStubAgentApiClient();
        var sup = new TestableDesktopHealthSupervisor(api);
        sup.Dispose();
        sup.Dispose(); // 不抛 ObjectDisposedException
    }

    // ===== P1-12：桌面异常时本地 SetAccountPaused，恢复时清本地标记 =====

    /// <summary>可注入 PauseState 的子类。基类 protected internal 探针可重写。</summary>
    internal sealed class TestableDesktopHealthSupervisorWithPause : DesktopHealthSupervisor
    {
        public Func<bool> ProbeProcessAlive { get; set; } = () => true;
        public Func<bool> ProbeDesktopLocked { get; set; } = () => false;
        public Func<bool> ProbeWindowVisible { get; set; } = () => true;

        public TestableDesktopHealthSupervisorWithPause(IAgentApiClient apiClient, PauseState pause)
            : base(apiClient, Microsoft.Extensions.Logging.Abstractions.NullLogger<DesktopHealthSupervisor>.Instance,
                  TimeSpan.FromMilliseconds(50), pauseState: pause)
        {
        }

        protected internal override bool IsWeComProcessAlive() => ProbeProcessAlive();
        protected internal override bool IsDesktopLocked() => ProbeDesktopLocked();
        protected internal override bool IsWeComWindowVisible() => ProbeWindowVisible();
    }

    /// <summary>P1-12：企微进程消失 → 上报 offline + 本地 SetAccountPaused(true)。</summary>
    [Fact]
    public async Task PollOnce_Offline_SetsLocalAccountPaused()
    {
        var api = new HealthStubAgentApiClient();
        var pause = new PauseState();
        var sup = new TestableDesktopHealthSupervisorWithPause(api, pause);
        using var __d = sup;
        sup.ProbeProcessAlive = () => false;

        await sup.PollOnceAsync();

        Assert.True(pause.AccountPaused);
        Assert.Contains(api.ReportedStatuses, p => p.Status == AccountStatus.Offline);
    }

    /// <summary>P1-12：从 offline 恢复 → SetAccountPaused(false)，清本地标记。</summary>
    [Fact]
    public async Task PollOnce_RecoversFromOffline_ClearsLocalAccountPaused()
    {
        var api = new HealthStubAgentApiClient();
        var pause = new PauseState();
        var sup = new TestableDesktopHealthSupervisorWithPause(api, pause);
        using var __d = sup;
        var procAlive = false;
        sup.ProbeProcessAlive = () => procAlive;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => true;

        await sup.PollOnceAsync(); // offline → SetAccountPaused(true)
        Assert.True(pause.AccountPaused);

        procAlive = true;
        await sup.PollOnceAsync(); // online → SetAccountPaused(false)
        Assert.False(pause.AccountPaused);
    }

    /// <summary>P1-12：服务端推送的 AccountPaused 不被 DesktopHealth 清掉（避免误清）。
    /// 场景：服务端已 SetAccountPaused(true)，本地从未触发暂停；此时本地"恢复正常"不应清掉。</summary>
    [Fact]
    public async Task PollOnce_ServerPushedPause_NotClearedByLocalRecovery()
    {
        var api = new HealthStubAgentApiClient();
        var pause = new PauseState();
        pause.SetAccountPaused(true); // 模拟服务端推送
        var sup = new TestableDesktopHealthSupervisorWithPause(api, pause);
        using var __d = sup;
        sup.ProbeProcessAlive = () => true;
        sup.ProbeDesktopLocked = () => false;
        sup.ProbeWindowVisible = () => true;

        await sup.PollOnceAsync(); // 全部正常 → 进入"恢复"分支，但本地标记为 false，不清服务端 pause

        Assert.True(pause.AccountPaused, "服务端推送的 AccountPaused 不应被本地清理");
    }
}

/// <summary>ReportStatusAsync 总是抛异常的 stub，用于验证容错。</summary>
internal sealed class ThrowingHealthApiClient : IAgentApiClient
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
    public Task<bool> ReportInboundAsync(InboundEvent env, CancellationToken cancellationToken = default)
        => throw new NotSupportedException();
    public void Dispose() { }
}
