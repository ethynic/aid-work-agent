namespace WeCom.PersonalRpa.App.Powershell;

/// <summary>
/// PowerShell 操作调用器配置选项。对应 ClientOptions.Automation 段。
/// </summary>
public sealed class PowershellOptions
{
    /// <summary>配置段键名（与 ClientOptions.Automation 子段对齐）。</summary>
    public const string SectionName = "WeComPersonalRpa:Automation";

    /// <summary>PowerShell 可执行文件路径（默认走 PATH 解析）。</summary>
    public string Executable { get; set; } = "powershell.exe";

    /// <summary>wecom-ops.ps1 主入口脚本的绝对或相对路径（相对应用程序目录）。</summary>
    public string OpsScript { get; set; } = "scripts/wecom-ops.ps1";

    /// <summary>单次 PS 调用超时（秒）。超时后进程会被 Kill(entireProcessTree: true)。</summary>
    public int InvokeTimeoutSeconds { get; set; } = 30;
}
