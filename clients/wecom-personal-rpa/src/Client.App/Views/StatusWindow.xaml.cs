using System.Diagnostics;
using System.Windows;
using System.Windows.Media;
using Serilog;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.StateMachine;
using ClientSessionManager = WeCom.PersonalRpa.Core.StateMachine.IStateManager;

namespace WeCom.PersonalRpa.App.Views;

/// <summary>
/// 状态窗口：展示账号、客户端 ID、运行时长、最近事件。
/// 周期（2 秒）从 <see cref="ClientSessionManager"/> 拉取最新状态刷新。
/// </summary>
public partial class StatusWindow : Window
{
    private new const string Tag = "StatusWindow";
    private readonly ClientSessionManager _state;
    private readonly ClientSession _session;
    private readonly ClientOptions _options;
    private System.Windows.Threading.DispatcherTimer? _timer;
    private readonly DateTimeOffset _startedAt = DateTimeOffset.Now;

    public StatusWindow(ClientSessionManager state, ClientSession session, ClientOptions options)
    {
        _state = state;
        _session = session;
        _options = options;
        InitializeComponent();
        Loaded += OnLoaded;
        Unloaded += OnUnloaded;
        Refresh();
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        _timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _timer.Tick += (_, _) => Refresh();
        _timer.Start();
    }

    private void OnUnloaded(object sender, RoutedEventArgs e) => _timer?.Stop();

    private void Refresh()
    {
        try
        {
            var current = _state.CurrentState;
            StatusText.Text = StateText(current);
            StatusDot.Fill = StateBrush(current);
            AccountText.Text = string.IsNullOrEmpty(_session.AccountId) ? "—" : _session.AccountId;
            ClientText.Text = string.IsNullOrEmpty(_options.ClientId) ? "—" : _options.ClientId;

            var span = DateTimeOffset.Now - _startedAt;
            UptimeText.Text = $"{(int)span.TotalHours}小时 {span.Minutes}分钟";

            var lastEvent = string.IsNullOrEmpty(_session.LastErrorMessage)
                ? current.ToString()
                : _session.LastErrorMessage;
            var items = new System.Collections.Generic.List<string>
            {
                lastEvent,
            };
            EventsList.ItemsSource = items;
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "[{Tag}] 刷新状态失败", Tag);
        }
    }

    private static string StateText(ClientState s) => s switch
    {
        ClientState.Starting => "启动中",
        ClientState.CheckingEnvironment => "环境自检中",
        ClientState.NeedLogin => "待登录",
        ClientState.Running => "运行中",
        ClientState.PausedByUser => "已暂停（用户）",
        ClientState.PausedByServer => "已暂停（服务端）",
        ClientState.PausedError => "已暂停（异常）",
        ClientState.Recovering => "恢复中",
        _ => s.ToString(),
    };

    private static Brush StateBrush(ClientState s)
    {
        var hex = s switch
        {
            ClientState.Running => "#34C724",
            ClientState.NeedLogin or ClientState.CheckingEnvironment
                or ClientState.Starting or ClientState.Recovering => "#FF9F1C",
            ClientState.PausedError => "#F5393C",
            _ => "#8F959E",
        };
        return new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
    }

    private void OpenLogs_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            System.IO.Directory.CreateDirectory(App.LogDirectory);
            Process.Start(new ProcessStartInfo("explorer.exe", App.LogDirectory) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 打开日志目录失败", Tag);
        }
    }

    private void Close_Click(object sender, RoutedEventArgs e) => Close();
}
