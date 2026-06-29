namespace WeCom.PersonalRpa.Core.Config;

/// <summary>
/// 企微会话存档（消息源）配置。对应配置段 WeComPersonalRpa:MessageSource。
/// 详细字段含义见 docs/system/wecom-personal-rpa-client-design.md §F4。
/// </summary>
public sealed class ArchiveOptions
{
    /// <summary>
    /// 消息源模式：
    /// - "archive"：通过企微会话存档 API 拉取（默认，需要企业开通会话存档权限）
    /// - "fallback"：UIA/PowerShell 抓取兜底（Phase 4 评估，未实现）
    /// </summary>
    public string Mode { get; set; } = "archive";

    /// <summary>企业 ID（corpid）。从企微管理后台「我的企业」获取。</summary>
    public string Corpid { get; set; } = string.Empty;

    /// <summary>
    /// 会话存档 Secret。**留空**：从环境变量 ARCHIVE_SECRET 读取，避免落盘。
    /// </summary>
    public string Secret { get; set; } = string.Empty;

    /// <summary>
    /// 会话存档 RSA 私钥 PEM 文件相对路径（相对客户端工作目录）。
    /// 由企微管理后台「管理工具 - 会话内容存档」生成并下载。
    /// </summary>
    public string PrivateKeyPath { get; set; } = "config/archive_private_key.pem";

    /// <summary>拉取间隔（秒）。企微建议 ≥3 秒，避免触发频率限制（45009）。</summary>
    public int PollIntervalSeconds { get; set; } = 3;

    /// <summary>单次拉取条数上限（企微上限 1000）。</summary>
    public int BatchLimit { get; set; } = 1000;

    /// <summary>是否在客户端本地下载媒体文件（false 时仅传递 fileid 给服务端）。</summary>
    public bool DownloadMedia { get; set; } = true;

    /// <summary>媒体临时目录（相对客户端工作目录）。客户端上报完成后负责清理。</summary>
    public string MediaTempDir { get; set; } = "temp/archive-media";
}
