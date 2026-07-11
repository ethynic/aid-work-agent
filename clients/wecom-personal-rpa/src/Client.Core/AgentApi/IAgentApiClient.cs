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
    /// GET outbox：拉取本 client 的待发送动作（服务端 DB outbox 是唯一权威消息源）。
    /// 走静态渠道路径（同 /config），HMAC 头鉴权，GET body 空。只读不删，at-least-once，
    /// 调用方需按 request_id + action_index 幂等。
    /// </summary>
    /// <param name="limit">最多拉取条数，会被 clamp 到 [1,100]。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    Task<OutboxResponse> GetOutboxAsync(int limit = 100, CancellationToken cancellationToken = default);

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
    /// POST 媒体上传：客户端拿到媒体文件后，上传到服务端换取短期签名 URL。
    /// 原使用方 InboundEventBuilder 已随入站消息路径一并删除，此接口保留以备未来出站附件场景使用。
    ///
    /// 协议约定（见 protocol.md §A.10）：multipart/form-data，HMAC 签名 body 用固定占位串 "media-upload"。
    /// </summary>
    /// <param name="localPath">本地文件绝对路径。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>服务端返回的短期签名 URL（24h 有效）。</returns>
    Task<string> UploadMediaAsync(string localPath, CancellationToken cancellationToken = default);
}
