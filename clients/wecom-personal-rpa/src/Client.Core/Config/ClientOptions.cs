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

    /// <summary>轮询间隔（秒，离线拉取 outbox 与心跳）。</summary>
    public int PollIntervalSeconds { get; set; } = 30;

    /// <summary>本地存储根路径（队列 SQLite、临时文件、日志）。</summary>
    public string StoragePath { get; set; } = string.Empty;
}
