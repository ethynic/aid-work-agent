using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;
using WeCom.PersonalRpa.Automation.Win32;

namespace WeCom.PersonalRpa.App.Health;

/// <summary>
/// 桌面健康状态监督器（设计文档 §9.1 / §F5）。
///
/// 周期性（默认 60s）轮询检查桌面环境，异常时上报 StatusPayload：
/// <list type="bullet">
///   <item>企微进程消失 → <see cref="AccountStatus.Offline"/></item>
///   <item>桌面锁屏 / 屏保 → <see cref="AccountStatus.DesktopLocked"/></item>
///   <item>企微窗口不可见 / 不在前台 → <see cref="AccountStatus.WindowNotVisible"/></item>
///   <item>剪贴板不可用 → detail 提示（不切 status）</item>
/// </list>
///
/// 状态去重：维护 <c>_lastStatus</c>，状态未变则不上报（避免每周期都推）。
/// 当检测全部恢复 → 上报 <see cref="AccountStatus.Online"/>（detail "desktop recovered"）。
///
/// 串行化：Timer 用 SemaphoreSlim 防止重入。
/// </summary>
public class DesktopHealthSupervisor : IHostedService, IDisposable
{
    private readonly IAgentApiClient _apiClient;
    private readonly ILogger<DesktopHealthSupervisor>? _logger;
    private readonly PauseState? _pauseState;
    private readonly TimeSpan _pollInterval;
    private Timer? _timer;
    private readonly SemaphoreSlim _pollLock = new(1, 1);

    /// <summary>上次上报状态（去重），null 表示尚未上报过。</summary>
    private AccountStatus? _lastReportedStatus;

    /// <summary>
    /// P1-12：本地标记 — 是否由本监督器触发了 Account 暂停。
    /// 用于 Resume 时只在"原本就是本地暂停"的情况下清掉，避免误清服务端推送的 pause。
    /// </summary>
    private volatile bool _localAccountPausedSet;

    // TODO(Phase 5)：分辨率 / DPI 变化检测。启动时缓存窗口尺寸基线，
    // 周期对比；变化时上报（涉及重新校准 PS 坐标，留待 Phase 5）。
    // private (int Width, int Height)? _baselineScreenSize;

    /// <summary>默认轮询周期 60 秒（设计文档 §9.1）。</summary>
    private const int DefaultPollSeconds = 60;

    /// <summary>企微进程名（不含扩展名）。</summary>
    private const string WeComProcessName = "WXWork";

    public DesktopHealthSupervisor(
        IAgentApiClient apiClient,
        ILogger<DesktopHealthSupervisor>? logger = null,
        TimeSpan? pollInterval = null,
        // P1-12：可选 PauseState（DI 注入时由容器解析；测试场景传 null 跳过本地暂停）
        PauseState? pauseState = null)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _logger = logger;
        _pollInterval = pollInterval ?? TimeSpan.FromSeconds(DefaultPollSeconds);
        _pauseState = pauseState;
    }

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        _timer = new Timer(async _ => await PollOnceSafeAsync(), null, TimeSpan.Zero, _pollInterval);
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public Task StopAsync(CancellationToken cancellationToken)
    {
        _timer?.Change(Timeout.Infinite, 0);
        return Task.CompletedTask;
    }

    /// <summary>Timer 回调包装：捕获异常 + SemaphoreSlim 串行化。</summary>
    private async Task PollOnceSafeAsync()
    {
        if (!await _pollLock.WaitAsync(0))
        {
            return; // 上一次还在跑，跳过
        }
        try
        {
            await PollOnceAsync();
        }
        catch (Exception ex)
        {
            _logger?.LogError(ex, "DesktopHealthSupervisor 轮询异常");
        }
        finally
        {
            _pollLock.Release();
        }
    }

    /// <summary>
    /// 单次轮询核心逻辑。internal 供单元测试直接驱动（不经 Timer）。
    /// </summary>
    internal async Task PollOnceAsync()
    {
        var detected = Detect();
        // 不上报 Recovering / Unknown
        if (detected.Status == AccountStatus.Recovering)
        {
            return;
        }

        // P1-12：桌面异常 → 本地 SetAccountPaused(true)，避免 OutboundActionDispatcher 继续发 PS。
        // 恢复正常 → 若本地标记为 true，清掉 AccountPaused（不清服务端推送的 pause）。
        ApplyLocalPause(detected.Status);

        // 状态去重：与上次相同则不推
        if (_lastReportedStatus.HasValue && _lastReportedStatus.Value == detected.Status)
        {
            return;
        }

        try
        {
            await _apiClient.ReportStatusAsync(new StatusPayload
            {
                Status = detected.Status,
                Detail = detected.Detail,
            });
            _lastReportedStatus = detected.Status;
            _logger?.LogInformation("DesktopHealth 上报 status={Status} detail={Detail}",
                detected.Status, detected.Detail);
        }
        catch (Exception ex)
        {
            // 上报失败不抛：下次重试
            _logger?.LogWarning(ex, "DesktopHealth 上报失败 status={Status}", detected.Status);
        }
    }

    /// <summary>
    /// P1-12：根据桌面状态同步本地 pause 标记。
    /// - 检测到 Offline / DesktopLocked / WindowNotVisible → SetAccountPaused(true) + 标记本地已设
    /// - 检测到 Online → 仅在 _localAccountPausedSet=true 时清 AccountPaused（避免误清服务端推送）
    /// - PauseState 为 null 时整体跳过（测试场景兼容）
    /// </summary>
    private void ApplyLocalPause(AccountStatus status)
    {
        if (_pauseState is null) return;

        var needPause = status is AccountStatus.Offline
            or AccountStatus.DesktopLocked
            or AccountStatus.WindowNotVisible;

        if (needPause)
        {
            if (!_localAccountPausedSet)
            {
                _pauseState.SetAccountPaused(true);
                _localAccountPausedSet = true;
                _logger?.LogInformation(
                    "DesktopHealth 触发本地 AccountPaused=true status={Status}", status);
            }
        }
        else if (status == AccountStatus.Online && _localAccountPausedSet)
        {
            _pauseState.SetAccountPaused(false);
            _localAccountPausedSet = false;
            _logger?.LogInformation("DesktopHealth 恢复，清本地 AccountPaused");
        }
    }

    /// <summary>
    /// 执行所有检测项，返回最严重的状态。internal virtual 让单元测试子类替换 OS 探针。
    /// </summary>
    protected internal virtual (AccountStatus Status, string? Detail) Detect()
    {
        // 优先级：Offline（进程不存在） > DesktopLocked > WindowNotVisible > 正常
        if (!IsWeComProcessAlive())
        {
            return (AccountStatus.Offline, "企微进程未运行");
        }

        if (IsDesktopLocked())
        {
            return (AccountStatus.DesktopLocked, "桌面已锁定或屏保运行");
        }

        if (!IsWeComWindowVisible())
        {
            return (AccountStatus.WindowNotVisible, "企微主窗口不可见");
        }

        // 全部正常 → 若 _lastReportedStatus 不是 Online，则上报一次"恢复"
        // （如果从未上报过，启动初期不必主动报 Online，让 QrCodeWatcher / 状态机驱动）
        if (_lastReportedStatus.HasValue && _lastReportedStatus.Value != AccountStatus.Online)
        {
            return (AccountStatus.Online, "桌面环境恢复正常");
        }

        return (AccountStatus.Recovering, null); // 占位：表示"无需上报"
    }

    // ============================================================
    // OS 探针：internal virtual 以便单元测试用子类替换
    // ============================================================

    /// <summary>企微进程是否存在。internal virtual 让单测替换。</summary>
    protected internal virtual bool IsWeComProcessAlive()
    {
        try
        {
            var procs = Process.GetProcessesByName(WeComProcessName);
            var exists = procs.Length > 0;
            foreach (var p in procs) p.Dispose();
            return exists;
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "GetProcessesByName 失败，降级为 false");
            return false;
        }
    }

    /// <summary>桌面是否被锁屏 / 屏保运行。internal virtual。</summary>
    protected internal virtual bool IsDesktopLocked()
    {
        try
        {
            // 复用 Automation.Win32.DesktopState（已 P/Invoke SystemParametersInfo + GetShellWindow）
            var ds = new DesktopState();
            return ds.IsLocked();
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "DesktopState.IsLocked 异常，降级为 false");
            return false;
        }
    }

    /// <summary>企微主窗口是否可见且在前台。internal virtual。</summary>
    protected internal virtual bool IsWeComWindowVisible()
    {
        // 简化首版：通过 FindWindow 找 "WeWorkWindow" 类窗口，检查 IsWindowVisible。
        // TODO(Phase 5): 用 GetForegroundWindow 比对、加 EnumWindows 兜底覆盖子窗口。
        try
        {
            var hwnd = User32FindWindow("WeWorkWindow", null);
            if (hwnd == IntPtr.Zero)
            {
                // 企微窗口类名版本差异：未找到也视为不可见
                _logger?.LogDebug("未找到 WeWorkWindow 窗口类");
                return false;
            }
            return User32IsWindowVisible(hwnd);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "IsWeComWindowVisible 异常，降级为 false");
            return false;
        }
    }

    /// <summary>剪贴板是否可用（首版未集成，留 TODO）。</summary>
    protected internal virtual bool IsClipboardUsable()
    {
        // TODO(Phase 5): 集成 Clipboard.Open() 重试 3 次（注意 STA 线程要求）
        return true;
    }

    public void Dispose()
    {
        _timer?.Dispose();
        _pollLock.Dispose();
    }

    // ============================================================
    // 最小化 P/Invoke：仅 FindWindow + IsWindowVisible
    // ============================================================

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string? lpClassName, string? lpWindowName);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hWnd);

    // 包装供单元测试 mock（C# 静态 P/Invoke 无法直接 mock，但 internal virtual 方法
    // IsWeComWindowVisible 整体可被替换，调用方不直接调这些静态方法）。
    private static IntPtr User32FindWindow(string? cls, string? title) => FindWindow(cls, title);
    private static bool User32IsWindowVisible(IntPtr h) => IsWindowVisible(h);
}
