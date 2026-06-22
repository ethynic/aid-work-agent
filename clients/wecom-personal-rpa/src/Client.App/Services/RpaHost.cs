using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Serilog;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 客户端编排后台服务（Generic Host BackgroundService）。
///
/// 编排流程（对齐 docs/system/wecom-personal-rpa-design.md 与 protocol §C.5 状态机）：
///   Starting → 加载 ClientOptions
///   CheckingEnvironment → HealthSupervisor 检查
///   NeedLogin → LoginStateDetector 采集二维码 + 弹 LoginQrWindow
///   Running → MessageWatcher 产出 MessagePayload 经 InboundReporter 上报；
///             WebSocket/轮询接收 ActionEnvelope → OutboundActionSource → ISendQueue；
///             SendMessageService 串行执行；异常转 PausedError。
///
/// 暂停/恢复：暴露 <see cref="PauseAsync"/>/<see cref="ResumeAsync"/> 供托盘与服务端配置下发。
/// </summary>
public sealed class RpaHost : BackgroundService
{
    private const string Tag = "RpaHost";

    private readonly IServiceProvider _sp;
    private readonly ClientOptions _options;
    private readonly IStateManager _state;
    private readonly ClientSession _session;
    private readonly InboundReporter _reporter;
    private readonly OutboundActionSource _actionSource;
    private readonly SendMessageService _sender;
    private readonly HealthSupervisor _health;
    private readonly LoginStateDetector _login;
    private readonly MessageWatcher _watcher;

    private CancellationTokenSource? _cts;
    private Timer? _outboxPoller;

    public RpaHost(
        IServiceProvider sp,
        ClientOptions options,
        IStateManager state,
        ClientSession session,
        InboundReporter reporter,
        OutboundActionSource actionSource,
        SendMessageService sender,
        HealthSupervisor health,
        LoginStateDetector login,
        MessageWatcher watcher)
    {
        _sp = sp;
        _options = options;
        _state = state;
        _session = session;
        _reporter = reporter;
        _actionSource = actionSource;
        _sender = sender;
        _health = health;
        _login = login;
        _watcher = watcher;
    }

    /// <summary>当前状态（供托盘/状态窗口读取）。</summary>
    public ClientState CurrentState => _state.CurrentState;

    /// <summary>托盘/配置下发：暂停。</summary>
    public Task PauseAsync(string reason)
    {
        Log.Information("[{Tag}] 暂停请求 Reason={Reason}", Tag, reason);
        try
        {
            _state.TransitionTo(ClientState.PausedByUser, errorMessage: reason);
        }
        catch (Exception ex) { Log.Warning(ex, "[{Tag}] 暂停迁移失败", Tag); }
        return Task.CompletedTask;
    }

    /// <summary>托盘/配置下发：恢复（经 Recovering 重新自检）。</summary>
    public Task ResumeAsync(string reason)
    {
        Log.Information("[{Tag}] 恢复请求 Reason={Reason}", Tag, reason);
        try
        {
            _state.TransitionTo(ClientState.Recovering, errorMessage: reason);
        }
        catch (Exception ex) { Log.Warning(ex, "[{Tag}] 恢复迁移失败", Tag); }
        return Task.CompletedTask;
    }

    /// <summary>触发重新登录（托盘"重新登录"）。</summary>
    public async Task ReloginAsync()
    {
        Log.Information("[{Tag}] 触发重新登录", Tag);
        try { _state.TransitionTo(ClientState.NeedLogin, errorMessage: "用户触发重新登录"); }
        catch (Exception ex) { Log.Warning(ex, "[{Tag}] 迁移 NeedLogin 失败", Tag); }
        await _login.DetectAndCollectAsync(_cts?.Token ?? CancellationToken.None);
        ShowLoginQrWindow();
    }

    /// <summary>BackgroundService 主循环。</summary>
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _cts = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        var ct = _cts.Token;
        Log.Information("[{Tag}] 编排服务启动 ClientId={ClientId}",
            Tag, _options.ClientId);

        try
        {
            // ---- 环境自检 ----
            try { _state.TransitionTo(ClientState.CheckingEnvironment); }
            catch (Exception ex) { Log.Warning(ex, "[{Tag}] 迁移 CheckingEnvironment 失败", Tag); }

            await _health.StartAsync(ct);
            await _health.CheckAsync();

            if (ct.IsCancellationRequested) return;

            // ---- 登录态检测 ----
            var needLogin = await _login.DetectAndCollectAsync(ct);
            if (needLogin)
            {
                ShowLoginQrWindow();
                await WaitForLoginAsync(ct);
            }

            // ---- 进入 Running ----
            try { _state.TransitionTo(ClientState.Running); }
            catch (Exception ex) { Log.Warning(ex, "[{Tag}] 迁移 Running 失败", Tag); }

            // 启动消息监听
            await _watcher.StartAsync(ct);

            // 启动 outbox 轮询（离线兜底；在线时由 WebSocket 推送经 DispatchAsync 入队）
            StartOutboxPoller(ct);

            // 保持运行，直到取消或状态离开 Running
            await MonitorLoopAsync(ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            Log.Information("[{Tag}] 编排服务被取消", Tag);
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 编排服务异常，转入 PausedError", Tag);
            try
            {
                _state.TransitionTo(ClientState.PausedError,
                    errorCode: ex.GetType().Name.ToLowerInvariant(),
                    errorMessage: ex.Message);
            }
            catch { /* 忽略 */ }
        }
        finally
        {
            await _watcher.StopAsync(CancellationToken.None);
            _outboxPoller?.Dispose();
            await _health.StopAsync(CancellationToken.None);
            Log.Information("[{Tag}] 编排服务退出 FinalState={State}", Tag, _state.CurrentState);
        }
    }

    /// <summary>轮询登录态直到完成或取消。</summary>
    private async Task WaitForLoginAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested && _state.CurrentState == ClientState.NeedLogin)
        {
            await Task.Delay(5000, ct);
            var stillNeed = await _login.DetectAndCollectAsync(ct);
            if (!stillNeed)
            {
                Log.Information("[{Tag}] 登录完成", Tag);
                CloseLoginQrWindow();
                return;
            }
        }
    }

    /// <summary>主监控循环：维持 Running 直到状态变更或取消。</summary>
    private async Task MonitorLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested && _state.CurrentState == ClientState.Running)
        {
            await Task.Delay(1000, ct);
            // 连续失败计数与 PausedError 判定由 SendMessageService 标记后此处感知（TODO）
        }
    }

    /// <summary>离线 outbox 轮询：定期拉取未投递动作并入队。</summary>
    private void StartOutboxPoller(CancellationToken ct)
    {
        var interval = TimeSpan.FromSeconds(Math.Max(5, _options.PollIntervalSeconds));
        _outboxPoller = new Timer(async _ =>
        {
            try
            {
                // TODO: 调用 Core.IAgentApiClient.PullOutboxAsync(ct) 拉取 ActionEnvelope 列表，
                //       逐条经 _actionSource.DispatchAsync(env, ct) 入队
                await Task.CompletedTask;
            }
            catch (Exception ex)
            {
                Log.Warning(ex, "[{Tag}] outbox 轮询异常", Tag);
            }
        }, null, interval, interval);
        Log.Information("[{Tag}] outbox 轮询启动 间隔={Sec}s", Tag, interval.TotalSeconds);
    }

    /// <summary>弹出二维码窗口（UI 线程）。</summary>
    private void ShowLoginQrWindow()
    {
        System.Windows.Application.Current?.Dispatcher.Invoke(() =>
        {
            var win = _sp.GetService<Views.LoginQrWindow>();
            win?.Show();
        });
    }

    private void CloseLoginQrWindow()
    {
        System.Windows.Application.Current?.Dispatcher.Invoke(() =>
        {
            foreach (System.Windows.Window w in System.Windows.Application.Current.Windows)
            {
                if (w is Views.LoginQrWindow qr) qr.Close();
            }
        });
    }
}
