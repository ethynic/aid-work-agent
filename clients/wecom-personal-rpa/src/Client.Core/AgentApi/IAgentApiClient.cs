using System.Net.WebSockets;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Security;

namespace WeCom.PersonalRpa.Core.AgentApi;

/// <summary>
/// 服务端 Agent API 客户端接口（protocol.md §C.4，Core 定义 / Core 实现）。
/// 所有出站请求由 <see cref="RequestSigner"/> 注入鉴权头，Polly 提供重试与熔断。
/// </summary>
public interface IAgentApiClient : IDisposable
{
    /// <summary>
    /// POST 回调：上报入站事件（message / status / action_result）。
    /// </summary>
    /// <param name="env">入站事件信封。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>成功返回 true；失败抛异常或返回 false（详见实现）。</returns>
    Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default);

    /// <summary>
    /// GET 配置：拉取客户端运行配置。
    /// </summary>
    Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// GET 文件：下载文件附件到流（短期签名 URL）。
    /// </summary>
    /// <param name="fileId">文件引用（URL 或 id）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>文件内容流。</returns>
    Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default);

    /// <summary>
    /// 建立 WebSocket 连接（在线推送通道）。WS 鉴权用查询串带签名。
    /// </summary>
    /// <param name="cancellationToken">取消令牌（用于取消连接建立）。</param>
    /// <returns>已连接的 ClientWebSocket。</returns>
    Task<ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default);

    // ============================================================
    // Phase 3 扩展方法（C/D/F 三块共享）
    // ============================================================

    /// <summary>
    /// POST status 事件：上报账号/桌面状态（含 need_login 时的 qr_image_base64）。
    /// Phase 3 块 F（QrCodeWatcher）使用。
    /// </summary>
    /// <param name="payload">status payload（QrImageBase64 可空）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    Task<bool> ReportStatusAsync(StatusPayload payload, CancellationToken cancellationToken = default);

    /// <summary>
    /// POST action_result 回执：客户端执行完 outbox action 后上报结果。
    /// Phase 3 块 C（OutboundActionDispatcher）使用。
    /// </summary>
    /// <param name="requestId">对应 ActionEnvelope.request_id。</param>
    /// <param name="success">是否成功。</param>
    /// <param name="errorCode">失败时的错误码（对齐 protocol.md §A.8）。</param>
    /// <param name="errorMessage">失败时的脱敏说明。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    Task<bool> ReportActionResultAsync(
        string requestId,
        bool success,
        string? errorCode = null,
        string? errorMessage = null,
        CancellationToken cancellationToken = default);

    /// <summary>
    /// POST 媒体上传：客户端拿到会话存档的图片/文件后，上传到服务端换取短期签名 URL。
    /// Phase 3 块 D（InboundEventBuilder）使用。
    ///
    /// 协议约定（见 protocol.md §A.10）：multipart/form-data，HMAC 签名 body 用固定占位串 "media-upload"。
    /// </summary>
    /// <param name="localPath">本地文件绝对路径。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>服务端返回的短期签名 URL（24h 有效）。</returns>
    Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default);

    // ============================================================
    // Phase 4 扩展方法（块 E：Inbound 入站解析 + 白名单）
    // ============================================================

    /// <summary>
    /// 拉取绑定级监控白名单（启动时 + 每 60 分钟刷新 + 服务端 config_invalidate 推送时强制刷新）。
    /// Phase 4 块 E（MonitorUsersCache）使用。
    /// </summary>
    /// <returns>binding_id -> MonitorUsersEntry 字典（仅含白名单非空的 binding）。</returns>
    Task<Dictionary<string, MonitorUsersEntry>> GetMonitorUsersAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// POST callback：上报入站消息事件（event_type=message）。
    /// Phase 4 块 E（InboundEventReporter）使用。复用 PostCallbackAsync 的 HMAC 鉴权 + 重试。
    /// </summary>
    /// <param name="evt">入站消息事件信封（event_type 必须 = Message）。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>成功返回 true；失败抛异常或返回 false。</returns>
    Task<bool> ReportInboundAsync(InboundEvent evt, CancellationToken cancellationToken = default);
}
