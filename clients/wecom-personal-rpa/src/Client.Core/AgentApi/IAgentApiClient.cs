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
}
