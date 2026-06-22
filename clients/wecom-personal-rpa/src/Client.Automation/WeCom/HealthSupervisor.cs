using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Win32;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 健康监督器（IHealthSupervisor 的 Automation 实现）。
/// 组合 DesktopState + WeComMainWindow + LoginStateDetector，产出可上报的 <see cref="StatusPayload"/>。
/// Supervisor / App 周期调用 <see cref="CaptureStatus"/> 把状态推给服务端。
/// </summary>
public sealed class HealthSupervisor : IHealthSupervisor
{
    private readonly DesktopState _desktop;
    private readonly WeComMainWindow _mainWindow;
    private readonly LoginStateDetector _loginState;

    internal HealthSupervisor(
        DesktopState desktop,
        WeComMainWindow mainWindow,
        LoginStateDetector loginState)
    {
        _desktop = desktop ?? throw new ArgumentNullException(nameof(desktop));
        _mainWindow = mainWindow ?? throw new ArgumentNullException(nameof(mainWindow));
        _loginState = loginState ?? throw new ArgumentNullException(nameof(loginState));
    }

    /// <inheritdoc />
    public StatusPayload CaptureStatus()
    {
        // 1. 桌面锁定优先（最坏情况，所有后续探测都无意义）。
        try
        {
            if (_desktop.IsLocked())
            {
                return new StatusPayload
                {
                    Status = AccountStatus.DesktopLocked,
                    AccountDisplayName = null,
                    Detail = "桌面已锁定或屏保运行",
                    QrImageRef = null,
                };
            }
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：DesktopState.IsLocked 异常");
        }

        // 2. 主窗口可达性。
        try
        {
            if (!_mainWindow.TryFind())
            {
                return new StatusPayload
                {
                    Status = AccountStatus.Offline,
                    AccountDisplayName = null,
                    Detail = "企微主窗口未找到",
                    QrImageRef = null,
                };
            }
        }
        catch (AutomationLayerException ex)
        {
            Log.Warning(ex, "后端日志：WeComMainWindow.TryFind 异常，Layer={Layer}", ex.Layer);
            return new StatusPayload
            {
                Status = AccountStatus.Recovering,
                AccountDisplayName = null,
                Detail = "主窗口探测异常",
                QrImageRef = null,
            };
        }

        // 3. 登录态。
        var login = _loginState.Detect();
        return new StatusPayload
        {
            Status = login.Status,
            AccountDisplayName = login.AccountDisplayName,
            Detail = login.Detail,
            QrImageRef = login.QrImageRef,
        };
    }
}
