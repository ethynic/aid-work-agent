using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows;
using Hardcodet.Wpf.TaskbarNotification;
using Microsoft.Extensions.DependencyInjection;
using Serilog;
using WeCom.PersonalRpa.App.Services;
using WeCom.PersonalRpa.App.Views;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Tray;

/// <summary>
/// 系统托盘图标与右键菜单。
/// 菜单项：状态、暂停/恢复、重新登录、打开日志目录、打开状态窗口、退出。
/// 图标随 <see cref="ClientState"/> 变化（占位：首版用统一系统图标，颜色 TODO）。
/// </summary>
public sealed class TrayApp : IDisposable
{
    private const string Tag = "TrayApp";

    private readonly IServiceProvider _sp;
    private readonly IStateManager _state;
    private TaskbarIcon? _icon;
    private bool _disposed;

    public TrayApp(IServiceProvider sp, IStateManager state)
    {
        _sp = sp;
        _state = state;
    }

    /// <summary>挂载托盘图标到当前 WPF 应用。</summary>
    public void Attach()
    {
        if (_icon != null) return;

        _icon = new TaskbarIcon
        {
            Icon = LoadIcon(),
            ToolTipText = "企业微信 RPA 托管",
            Visibility = Visibility.Visible,
        };
        BuildContextMenu();

        // 周期刷新状态文本
        var refresh = new System.Windows.Threading.DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(2),
        };
        refresh.Tick += (_, _) => RefreshToolTip();
        refresh.Start();

        Log.Information("[{Tag}] 托盘已挂载", Tag);
    }

    /// <summary>卸载托盘图标。</summary>
    public void Detach()
    {
        if (_icon is null) return;
        _icon.Dispose();
        _icon = null;
        Log.Information("[{Tag}] 托盘已卸载", Tag);
    }

    /// <summary>构建右键菜单。</summary>
    private void BuildContextMenu()
    {
        if (_icon is null) return;
        var menu = new System.Windows.Controls.ContextMenu();

        var miStatus = Mi("状态：—", true, () => ShowStatusWindow());
        var miPause = Mi("暂停", false, OnPauseResume);
        var miRelogin = Mi("重新登录", false, OnRelogin);
        var miStatusWin = Mi("打开状态窗口", false, ShowStatusWindow);
        var miLogs = Mi("打开日志目录", false, OpenLogDir);
        var miExit = Mi("退出", false, OnExit);

        menu.Items.Add(miStatus);
        menu.Items.Add(Sep());
        menu.Items.Add(miPause);
        menu.Items.Add(miRelogin);
        menu.Items.Add(Sep());
        menu.Items.Add(miStatusWin);
        menu.Items.Add(miLogs);
        menu.Items.Add(Sep());
        menu.Items.Add(miExit);

        _icon.ContextMenu = menu;

        // 标记 Tag 便于刷新时定位
        miStatus.Tag = "status";
        miPause.Tag = "pause";

        // 周期刷新菜单文案
        var refresh = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        refresh.Tick += (_, _) =>
        {
            miStatus.Header = $"状态：{StateText(_state.CurrentState)}";
            miPause.Header = _state.CurrentState == ClientState.PausedByUser ? "恢复" : "暂停";
            miPause.IsEnabled = _state.CurrentState is ClientState.Running
                or ClientState.PausedByUser
                or ClientState.PausedError;
        };
        refresh.Start();
    }

    private void RefreshToolTip()
    {
        if (_icon != null)
            _icon.ToolTipText = $"企业微信 RPA 托管 · {StateText(_state.CurrentState)}";
    }

    private async void OnPauseResume()
    {
        try
        {
            // Phase 1：RpaHost 已退役，托盘的暂停/恢复直接走 IStateManager。
            // Phase 2：PowerShell 后端会重新提供编排入口。
            if (_state.CurrentState == ClientState.PausedByUser)
            {
                _state.TransitionTo(ClientState.Recovering, errorMessage: "托盘手动恢复");
            }
            else
            {
                _state.TransitionTo(ClientState.PausedByUser, errorMessage: "托盘手动暂停");
            }
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 暂停/恢复失败", Tag);
        }
        await Task.CompletedTask;
    }

    private async void OnRelogin()
    {
        // Phase 1：RpaHost 已退役，重新登录暂未接通；Phase 2 由 PowerShell 后端重写。
        Log.Information("[{Tag}] 重新登录：Phase 1 阶段 RpaHost 已退役，待 Phase 2 PowerShell 后端接通", Tag);
        await Task.CompletedTask;
    }

    private void ShowStatusWindow()
    {
        System.Windows.Application.Current?.Dispatcher.Invoke(() =>
        {
            var win = _sp.GetRequiredService<StatusWindow>();
            win.Show();
            win.Activate();
        });
    }

    private void OpenLogDir()
    {
        try
        {
            if (!Directory.Exists(App.LogDirectory))
                Directory.CreateDirectory(App.LogDirectory);
            Process.Start(new ProcessStartInfo("explorer.exe", App.LogDirectory) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 打开日志目录失败", Tag);
        }
    }

    private void OnExit()
    {
        Log.Information("[{Tag}] 用户从托盘退出", Tag);
        System.Windows.Application.Current.Shutdown(0);
    }

    /// <summary>加载托盘图标。首版使用系统默认；集成阶段替换为随状态变化的彩色图标。</summary>
    private static Icon LoadIcon()
    {
        // TODO: 根据 ClientState 切换绿色/黄色/红色图标（嵌入 .ico 资源）
        try
        {
            return SystemIcons.Application;
        }
        catch
        {
            return new Icon(System.Drawing.SystemIcons.Application, 16, 16);
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

    private static System.Windows.Controls.MenuItem Mi(string header, bool isDefault, Action onClick)
    {
        var mi = new System.Windows.Controls.MenuItem { Header = header };
        if (isDefault) mi.FontWeight = FontWeights.Bold;
        mi.Click += (_, _) => onClick();
        return mi;
    }

    private static System.Windows.Controls.Separator Sep() => new();

    public void Dispose()
    {
        if (_disposed) return;
        Detach();
        _disposed = true;
    }
}
