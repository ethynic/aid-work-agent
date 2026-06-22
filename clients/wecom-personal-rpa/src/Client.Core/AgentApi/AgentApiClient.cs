using System.Net.Http.Headers;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;
using Polly;
using Polly.Retry;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Security;

namespace WeCom.PersonalRpa.Core.AgentApi;

/// <summary>
/// 服务端 Agent API 客户端默认实现。
/// HTTP：HttpClient + Polly 指数退避重试与熔断。
/// WS：ClientWebSocket，鉴权用查询串带签名（RequestSigner.BuildWebSocketQuery）。
/// </summary>
public sealed class AgentApiClient : IAgentApiClient
{
    private readonly HttpClient _httpClient;
    private readonly ClientOptions _options;
    private readonly RequestSigner _signer;
    private readonly ILogger<AgentApiClient>? _logger;
    private readonly ResiliencePipeline _httpPipeline;
    private readonly JsonSerializerOptions _jsonOptions;

    /// <summary>构造 Agent API 客户端。</summary>
    /// <param name="httpClient">由 IHttpClientFactory 注入的 HttpClient（已配置 BaseAddress）。</param>
    /// <param name="options">客户端运行配置。</param>
    /// <param name="signer">请求签名器。</param>
    /// <param name="logger">日志（可空）。</param>
    public AgentApiClient(HttpClient httpClient, ClientOptions options, RequestSigner signer,
        ILogger<AgentApiClient>? logger = null)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _signer = signer ?? throw new ArgumentNullException(nameof(signer));
        _logger = logger;

        if (!string.IsNullOrEmpty(_options.AgentBaseUrl) &&
            (_httpClient.BaseAddress is null))
        {
            _httpClient.BaseAddress = new Uri(_options.AgentBaseUrl.TrimEnd('/') + "/");
        }

        _jsonOptions = ProtocolJsonOptions.Instance;

        // Polly 8.x ResiliencePipeline：指数退避重试（3 次），暂不接熔断器（TODO 熔断策略）
        _httpPipeline = new ResiliencePipelineBuilder()
            .AddRetry(new RetryStrategyOptions
            {
                ShouldHandle = new PredicateBuilder().Handle<HttpRequestException>(),
                MaxRetryAttempts = 3,
                BackoffType = DelayBackoffType.Exponential,
                UseJitter = true,
                Delay = TimeSpan.FromMilliseconds(500),
                OnRetry = args =>
                {
                    _logger?.LogWarning("HTTP 重试 {Attempt}，异常：{Exception}",
                        args.AttemptNumber, args.Outcome.Exception?.Message);
                    return ValueTask.CompletedTask;
                },
            })
            .Build();
    }

    private static readonly string CallbackPath = "api/v1/channels/wecom-personal-rpa/callback";
    private static readonly string ConfigPath = "api/v1/channels/wecom-personal-rpa/config";
    private static readonly string FilesPath = "api/v1/channels/wecom-personal-rpa/files";

    /// <inheritdoc />
    public async Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default)
    {
        var json = JsonSerializer.Serialize(env, _jsonOptions);
        var bodyBytes = Encoding.UTF8.GetBytes(json);

        using var request = new HttpRequestMessage(HttpMethod.Post, CallbackPath)
        {
            Content = new ByteArrayContent(bodyBytes),
        };
        request.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json") { CharSet = "utf-8" };
        _signer.Sign(request, bodyBytes);

        return await _httpPipeline.ExecuteAsync(async token =>
        {
            // HttpRequestMessage 不可重用，每次重试克隆
            using var cloned = await CloneRequestAsync(request, bodyBytes);
            _signer.Sign(cloned, bodyBytes); // 重新签名（timestamp / nonce 更新）
            using var resp = await _httpClient.SendAsync(cloned, HttpCompletionOption.ResponseHeadersRead, token)
                .ConfigureAwait(false);
            if (resp.IsSuccessStatusCode) return true;
            _logger?.LogWarning("PostCallback 失败：{Status}", (int)resp.StatusCode);
            return false;
        }, cancellationToken).ConfigureAwait(false);
    }

    /// <inheritdoc />
    public async Task<RpaConfigResponse> GetConfigAsync(CancellationToken cancellationToken = default)
    {
        return await _httpPipeline.ExecuteAsync(async token =>
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, ConfigPath);
            _signer.SignNoBody(request);
            using var resp = await _httpClient.SendAsync(request, token).ConfigureAwait(false);
            resp.EnsureSuccessStatusCode();
            await using var stream = await resp.Content.ReadAsStreamAsync(token).ConfigureAwait(false);
            return (await JsonSerializer.DeserializeAsync<RpaConfigResponse>(stream, _jsonOptions, token)
                    .ConfigureAwait(false))
                   ?? throw new InvalidDataException("GetConfig 反序列化结果为 null");
        }, cancellationToken).ConfigureAwait(false);
    }

    /// <inheritdoc />
    public async Task<Stream> DownloadFileAsync(string fileId, CancellationToken cancellationToken = default)
    {
        // fileId 既可以是完整 URL 也可以是相对路径
        Uri uri;
        if (Uri.TryCreate(fileId, UriKind.Absolute, out var abs))
        {
            uri = abs;
        }
        else
        {
            uri = new Uri(_httpClient.BaseAddress ?? new Uri(_options.AgentBaseUrl), $"{FilesPath}/{Uri.EscapeDataString(fileId)}");
        }

        using var request = new HttpRequestMessage(HttpMethod.Get, uri);
        _signer.SignNoBody(request);
        using var resp = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
            .ConfigureAwait(false);
        resp.EnsureSuccessStatusCode();
        // 读入内存流返回（避免短签名 URL 在流未读完前过期）
        var ms = new MemoryStream();
        await resp.Content.CopyToAsync(ms, cancellationToken).ConfigureAwait(false);
        ms.Position = 0;
        return ms;
    }

    /// <inheritdoc />
    public async Task<ClientWebSocket> ConnectWebSocketAsync(CancellationToken cancellationToken = default)
    {
        var wsBase = (_options.AgentBaseUrl ?? string.Empty).TrimEnd('/');
        if (wsBase.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            wsBase = "wss://" + wsBase["https://".Length..];
        }
        else if (wsBase.StartsWith("http://", StringComparison.OrdinalIgnoreCase))
        {
            wsBase = "ws://" + wsBase["http://".Length..];
        }
        else if (!wsBase.StartsWith("wss://", StringComparison.OrdinalIgnoreCase)
                 && !wsBase.StartsWith("ws://", StringComparison.OrdinalIgnoreCase))
        {
            wsBase = "wss://" + wsBase;
        }

        var query = _signer.BuildWebSocketQuery();
        var uri = new Uri($"{wsBase}/api/v1/channels/wecom-personal-rpa/ws?{query}");

        var ws = new ClientWebSocket();
        // TODO: 实际心跳 / 重连由 App 层的消息循环驱动，此处仅完成握手。
        await ws.ConnectAsync(uri, cancellationToken).ConfigureAwait(false);
        return ws;
    }

    private static async Task<HttpRequestMessage> CloneRequestAsync(HttpRequestMessage src, byte[] bodyBytes)
    {
        var clone = new HttpRequestMessage(src.Method, src.RequestUri)
        {
            Content = new ByteArrayContent(bodyBytes),
        };
        clone.Content.Headers.ContentType = src.Content?.Headers.ContentType;
        foreach (var h in src.Headers)
        {
            // 鉴权头由调用方重新 Sign 注入，跳过避免重复
            if (Protocol.RequestHeaders.ClientId.Equals(h.Key, StringComparison.OrdinalIgnoreCase)
                || Protocol.RequestHeaders.Timestamp.Equals(h.Key, StringComparison.OrdinalIgnoreCase)
                || Protocol.RequestHeaders.Nonce.Equals(h.Key, StringComparison.OrdinalIgnoreCase)
                || Protocol.RequestHeaders.Signature.Equals(h.Key, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }
            clone.Headers.TryAddWithoutValidation(h.Key, h.Value);
        }
        return await Task.FromResult(clone);
    }

    /// <inheritdoc />
    public void Dispose()
    {
        // HttpClient 由 IHttpClientFactory 管理生命周期，此处不释放。
    }
}
