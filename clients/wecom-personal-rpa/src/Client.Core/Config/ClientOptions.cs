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

    /// <summary>
    /// 出站执行配置（Phase 3 块 C 新增）。本地 outbox SQLite 队列、附件下载、重试策略。
    /// 详见 docs/system/wecom-personal-rpa-client-design.md §F3。
    /// </summary>
    public OutboundOptions Outbound { get; set; } = new();

    /// <summary>
    /// 消息源配置（Phase 3 块 D 新增）。yaml/json key 为 message_source（camelCase 序列化）。
    /// 默认 mode=archive，即通过企微会话存档 API 拉取。详见 §F4。
    /// </summary>
    public ArchiveOptions MessageSource { get; set; } = new();

    /// <summary>
    /// 二维码监听配置（Phase 3 块 F 新增）。详见
    /// docs/system/wecom-personal-rpa-client-design.md §F7。
    /// </summary>
    public QrCodeOptions QrCode { get; set; } = new();

    /// <summary>
    /// 入站消息白名单缓存配置（Phase 4 块 E 新增）。详见
    /// docs/system/wecom-personal-rpa-client-design.md §F6。
    /// </summary>
    public MonitorUsersOptions MonitorUsers { get; set; } = new();
}

/// <summary>
/// 入站消息白名单缓存配置。对应配置段 WeComPersonalRpa:MonitorUsers。
/// </summary>
public sealed class MonitorUsersOptions
{
    /// <summary>
    /// 白名单缓存刷新周期（分钟），默认 60。
    /// 设计见 §F6：客户端每 60 分钟主动拉一次，服务端 config_invalidate 推送时强制刷新。
    /// </summary>
    public int CacheRefreshMinutes { get; set; } = 60;

    /// <summary>
    /// 当前绑定的 binding_id（首版单 binding，从配置读；用于白名单匹配 key）。
    /// 多 binding 场景留给后续版本。
    /// </summary>
    public string BindingId { get; set; } = string.Empty;
}

/// <summary>
/// 二维码监听配置。对应配置段 WeComPersonalRpa:QrCode。
/// </summary>
public sealed class QrCodeOptions
{
    /// <summary>
    /// 二维码检测周期（秒），默认 25。
    /// 设计：服务端二维码 TTL=30s，客户端周期 25s 给网络/PS 延迟留 5s 余量，避免偶发空窗。
    /// </summary>
    public int PollIntervalSeconds { get; set; } = 25;

    /// <summary>
    /// 二维码区域 bbox（相对屏幕物理坐标 [x1,y1,x2,y2]），由 PS 端
    /// Get-WeComLoginStateInternal 用 Get-WeWorkWindowOrigin origin 做平移后截图。
    /// 默认值基于企微登录窗口 [530, 200, 800, 470]，真机校准留给 Phase 5。
    /// </summary>
    public int[] RegionBboxBase { get; set; } = { 530, 200, 800, 470 };
}

/// <summary>
/// 出站执行配置。对应配置段 WeComPersonalRpa:Outbound。
/// </summary>
public sealed class OutboundOptions
{
    /// <summary>本地 outbox SQLite 数据库路径（相对客户端工作目录，默认 data/outbox.db）。</summary>
    public string DbPath { get; set; } = "data/outbox.db";

    /// <summary>附件下载临时目录（相对客户端工作目录，默认 temp/outbound-downloads）。</summary>
    public string DownloadTempDir { get; set; } = "temp/outbound-downloads";

    /// <summary>单个附件最大允许体积（MB），超过直接拒收，防 CSRF / 滥用。</summary>
    public int MaxAttachmentSizeMb { get; set; } = 100;

    /// <summary>wecom_window_not_found 等可重试错误的最大重试次数（含首次执行）。</summary>
    public int MaxRetries { get; set; } = 3;
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
