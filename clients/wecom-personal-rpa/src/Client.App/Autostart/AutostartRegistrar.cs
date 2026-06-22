using Microsoft.Win32;
using Serilog;

namespace WeCom.PersonalRpa.App.Autostart;

/// <summary>
/// 开机自启注册器（注册表 Run 键最小实现）。
/// 写入 HKCU\Software\Microsoft\Windows\CurrentVersion\Run，键名 WeComPersonalRpaClient，
/// 值为当前可执行路径（带 --autostart 参数）。
///
/// TODO（后续增强）：
///   - 提供任务计划程序（schtasks）方案，支持未登录时启动与失败重启
///   - 与 Client.Supervisor 的 ScheduledTaskHelper 协同（避免双重自启）
///   - 支持按租户/账号维度选择是否自启
/// </summary>
public sealed class AutostartRegistrar
{
    private const string Tag = "AutostartRegistrar";
    private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "WeComPersonalRpaClient";

    /// <summary>注册开机自启。</summary>
    public bool Register()
    {
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(RunKeyPath);
            var exePath = Environment.ProcessPath ?? GetCurrentExePath();
            var cmd = $"\"{exePath}\" --autostart";
            key?.SetValue(ValueName, cmd, RegistryValueKind.String);
            Log.Information("[{Tag}] 已注册开机自启：{Cmd}", Tag, cmd);
            return true;
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 注册开机自启失败", Tag);
            return false;
        }
    }

    /// <summary>注销开机自启。</summary>
    public bool Unregister()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: true);
            if (key?.GetValue(ValueName) is null)
            {
                Log.Information("[{Tag}] 开机自启未注册，无需注销", Tag);
                return true;
            }
            key.DeleteValue(ValueName, throwOnMissingValue: false);
            Log.Information("[{Tag}] 已注销开机自启", Tag);
            return true;
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] 注销开机自启失败", Tag);
            return false;
        }
    }

    /// <summary>查询当前是否已注册。</summary>
    public bool IsRegistered()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, writable: false);
            return key?.GetValue(ValueName) is not null;
        }
        catch
        {
            return false;
        }
    }

    private static string GetCurrentExePath()
    {
        var module = System.Diagnostics.Process.GetCurrentProcess().MainModule;
        return module?.FileName ?? Environment.ProcessPath ?? AppDomain.CurrentDomain.BaseDirectory;
    }
}
