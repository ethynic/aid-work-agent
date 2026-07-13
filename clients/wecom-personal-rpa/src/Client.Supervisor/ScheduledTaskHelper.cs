using System.Diagnostics;
using Serilog;

namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// 登录后自启监督：注册/注销一条 Windows 计划任务（schtasks /Create /TN WeComPersonalRpaSupervisor），
/// 在用户登录后自动拉起 Supervisor（或 Client.App）。
/// </summary>
/// <remarks>
/// <b>状态：占位实现 + TODO</b>。本版仅封装 schtasks 调用骨架，不在生产路径调用。
/// 调用方需具备 Administrator 权限。
///
/// 不使用 Windows Task Scheduler COM API（Microsoft.Win32.TaskScheduler 包）以避免引入额外依赖；
/// 后续可评估是否切换到 COM API 以支持触发器/条件等高级语义。
/// </remarks>
internal static class ScheduledTaskHelper
{
    public const string TaskName = "WeComPersonalRpaSupervisor_AutoStart";

    private static readonly Serilog.ILogger Logger = Log.ForContext(typeof(ScheduledTaskHelper));

    /// <summary>
    /// 注册"登录后启动"的计划任务。
    /// </summary>
    /// <param name="exePath">要拉起的可执行文件绝对路径（通常是 Supervisor.exe 或 Client.App.exe）。</param>
    /// <param name="workingDirectory">工作目录（可空）。</param>
    /// <param name="arguments">启动参数（可空）。</param>
    /// <returns>schtasks 退出是否为 0。</returns>
    public static bool RegisterLogonTask(string exePath, string? workingDirectory = null, string? arguments = null)
    {
        // TODO：评估切换到 Task Scheduler COM API，避免裸 schtasks 字符串拼接的转义陷阱。
        // TODO：当前触发器固定为 ONLOGON；未来需支持 ONSTART（机器启动）和延迟启动，需补充参数。
        if (string.IsNullOrWhiteSpace(exePath))
        {
            throw new ArgumentException("exePath 不能为空", nameof(exePath));
        }

        var args = $"\"{exePath}\"";
        if (!string.IsNullOrWhiteSpace(arguments))
        {
            args += " " + arguments;
        }

        // schtasks /Create /TN <name> /TR <cmd> /SC ONLOGON /F
        //   /SC ONLOGON：当前用户登录时触发
        //   /F：强制覆盖已存在的同名任务
        var schtasksArgs =
            $"/Create /TN \"{TaskName}\" /TR \"{args}\" /SC ONLOGON /F";

        Logger.Information("后端日志：注册登录后自启计划任务 {Task}，目标={Exe}", TaskName, exePath);
        return RunSchtasks(schtasksArgs);
    }

    /// <summary>注销计划任务。</summary>
    public static bool UnregisterLogonTask()
    {
        var schtasksArgs = $"/Delete /TN \"{TaskName}\" /F";
        Logger.Information("后端日志：注销计划任务 {Task}", TaskName);
        return RunSchtasks(schtasksArgs);
    }

    /// <summary>查询计划任务是否存在。</summary>
    public static bool TaskExists()
    {
        var schtasksArgs = $"/Query /TN \"{TaskName}\"";
        var (exitCode, _) = RunSchtasksCapture(schtasksArgs);
        return exitCode == 0;
    }

    private static bool RunSchtasks(string arguments)
    {
        var (exitCode, error) = RunSchtasksCapture(arguments);
        if (exitCode != 0)
        {
            Logger.Error("后端日志：schtasks 调用失败 exit={Exit}, args={Args}, stderr={Err}",
                exitCode, arguments, error);
        }

        return exitCode == 0;
    }

    private static (int ExitCode, string StdErr) RunSchtasksCapture(string arguments)
    {
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "schtasks.exe",
                Arguments = arguments,
                UseShellExecute = false,
                RedirectStandardError = true,
                CreateNoWindow = true,
            };

            using var proc = Process.Start(psi);
            if (proc is null)
            {
                return (-1, "Process.Start 返回 null");
            }

            var stderr = proc.StandardError.ReadToEnd();
            proc.WaitForExit(milliseconds: 15000);
            return (proc.ExitCode, stderr);
        }
        catch (Exception ex)
        {
            Logger.Error(ex, "后端日志：schtasks 启动抛出异常。args={Args}", arguments);
            return (-1, ex.Message);
        }
    }
}
