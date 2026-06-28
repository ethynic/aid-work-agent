namespace WeCom.PersonalRpa.Core.Config;

/// <summary>
/// 客户端运行配置选项（对应 Microsoft.Extensions.Options 模式）。
/// 由 App / Supervisor 注入并绑定 IConfiguration / 配置文件。
/// </summary>
public sealed class ClientOptions
{
    /// <summary>配置绑定键前缀。</summary>
    public const string SectionName = "WeComPersonalRpa";

    /// <summary>Agent 服务端基础 URL（如 https://agent.example.com）。</summary>
    public string AgentBaseUrl { get; set; } = string.Empty;

    /// <summary>客户端 ID（注册时由服务端分配）。</summary>
    public string ClientId { get; set; } = string.Empty;

    /// <summary>客户端密钥明文（仅在内存中，落盘前必须由 EncryptedClientConfig 加密）。</summary>
    public string ClientSecret { get; set; } = string.Empty;

    /// <summary>
    /// 租户 ID（平台后台「RPA 绑定管理」下发）。当前 callback 路径未带 tenant_id，
    /// 预留给未来路由变更与租户隔离诊断使用。默认空字符串保证旧配置向前兼容。
    /// </summary>
    public string TenantId { get; set; } = string.Empty;

    /// <summary>轮询间隔（秒，离线拉取 outbox 与心跳）。</summary>
    public int PollIntervalSeconds { get; set; } = 30;

    /// <summary>本地存储根路径（队列 SQLite、临时文件、日志）。</summary>
    public string StoragePath { get; set; } = string.Empty;

    /// <summary>
    /// PowerShell 自动化后端配置（Phase 2 新增）。C# 端通过 PowershellOpsInvoker 调起
    /// wecom-ops.ps1 完成企微操作。详见 docs/system/wecom-personal-rpa-client-design.md §F2/§F3。
    /// </summary>
    public AutomationOptions Automation { get; set; } = new();
}

/// <summary>
/// PowerShell 自动化后端配置。对应配置段 WeComPersonalRpa:Automation。
/// </summary>
public sealed class AutomationOptions
{
    /// <summary>PowerShell 可执行文件路径（默认走 PATH 解析）。</summary>
    public string PowershellExecutable { get; set; } = "powershell.exe";

    /// <summary>wecom-ops.ps1 主入口脚本的相对路径（相对客户端工作目录）。</summary>
    public string PowershellOpsScript { get; set; } = "scripts/wecom-ops.ps1";

    /// <summary>单次 PS 调用超时（秒）。</summary>
    public int InvokeTimeoutSeconds { get; set; } = 30;

    /// <summary>
    /// 搜索框基准坐标（基于企微 5.0.8 / 1936x2088 / DPI=1.5 校准）。PS 脚本内同样有一份硬编码，
    /// 此处主要供 C# 端诊断/调试使用。详见 debug-navigate.ps1 Step-LocateSearch 注释。
    /// </summary>
    public int SearchBoxBaseX1 { get; set; } = 330;
    public int SearchBoxBaseY1 { get; set; } = 34;
    public int SearchBoxBaseX2 { get; set; } = 430;
    public int SearchBoxBaseY2 { get; set; } = 66;
    public int SearchBoxBaseWindowWidth { get; set; } = 1936;
}
