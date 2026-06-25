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

    private static readonly string ConfigPath = "api/v1/channels/wecom-personal-rpa/config";
    private static readonly string FilesPath = "api/v1/channels/wecom-personal-rpa/files";

    /// <summary>
    /// 回调路径所需 tenant_id（拉 /config 后缓存，ClientOptions.TenantId 兜底）。
    /// volatile：GetConfigAsync（写）与 PostCallbackAsync（读）可能跨线程。
    /// </summary>
    private volatile string? _tenantId;

    /// <summary>
    /// 回调路径所需 config_id（拉 /config 后缓存，来自 tenant_channel_configs 记录 id）。
    /// volatile：同上。
    /// </summary>
    private volatile string? _configId;

    /// <summary>
    /// 构造 callback 路径：``t/{tenant_id}/wecom_personal_rpa/callback/{config_id}``。
    /// 服务端路由：src/saas/api/wecom_personal_rpa_routes.py:191。
    /// </summary>
    /// <exception cref="InvalidOperationException">尚未拉到 tenant_id / config_id。</exception>
    private string CallbackPath
    {
        get
        {
            var tid = _tenantId;
            var cid = _configId;
            if (string.IsNullOrEmpty(tid) || string.IsNullOrEmpty(cid))
            {
                throw new InvalidOperationException(
                    "RPA callback 路径缺少 tenant_id 或 config_id："
                    + "请在 PostCallbackAsync 前先调用 GetConfigAsync 拉取服务端配置。"
                    + "若持续失败，请检查服务端 tenant_channel_configs 表是否已写入该 client 的记录。");
            }

            return $"t/{tid}/wecom_personal_rpa/callback/{cid}";
        }
    }

    /// <inheritdoc />
    public async Task<bool> PostCallbackAsync(InboundEvent env, CancellationToken cancellationToken = default)
    {
        // 首启顺序保证：callback 路径需要 tenant_id / config_id，未拉过 config 时先同步拉一次。
        // 拉取失败则抛清晰异常，避免发出路径不匹配的请求被服务端 404/401。
        await EnsureCallbackRoutingAsync(cancellationToken).ConfigureAwait(false);

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
        var resp = await _httpPipeline.ExecuteAsync(async token =>
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

        // 缓存 callback/ws 路径所需的 tenant_id / config_id（优先服务端下发，
        // ClientOptions.TenantId 作为旧服务端（字段缺失）兜底）。
        ApplyConfigRouting(resp);

        return resp;
    }

    /// <summary>
    /// 把 /config 响应里的 tenant_id / config_id 缓存到字段，供 callback/ws 路径拼接。
    /// 服务端旧版本（未下发这两个字段）时，tenant_id 回退到 ClientOptions.TenantId。
    /// </summary>
    private void ApplyConfigRouting(RpaConfigResponse resp)
    {
        var respTid = resp.TenantId;
        if (string.IsNullOrEmpty(respTid))
        {
            respTid = _options.TenantId;
        }

        if (!string.IsNullOrEmpty(respTid))
        {
            _tenantId = respTid;
        }
        else
        {
            _logger?.LogWarning(
                "RPA /config 响应未返回 tenant_id 且 ClientOptions.TenantId 为空，callback 路径将无法构造");
        }

        if (!string.IsNullOrEmpty(resp.ConfigId))
        {
            _configId = resp.ConfigId;
        }
        else
        {
            _logger?.LogWarning(
                "RPA /config 响应未返回 config_id（服务端 tenant_channel_configs 表可能未写入该 client 记录），"
                + "callback 路径将无法构造");
        }
    }

    /// <summary>
    /// 确保 callback 路由参数（tenant_id / config_id）已就绪；未就绪时同步拉一次 /config。
    /// 拉取后仍缺失则抛清晰异常，由调用方决定是否重试 / 退避。
    /// </summary>
    private async Task EnsureCallbackRoutingAsync(CancellationToken cancellationToken)
    {
        if (!string.IsNullOrEmpty(_tenantId) && !string.IsNullOrEmpty(_configId))
        {
            return;
        }

        _logger?.LogInformation("RPA callback 首次发送前同步拉取 /config 以获取 tenant_id/config_id");
        try
        {
            await GetConfigAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (Exception e)
        {
            throw new InvalidOperationException(
                "RPA callback 路径参数缺失，且同步拉取 /config 失败，无法发送 callback。"
                + "请检查 AgentBaseUrl / ClientId / ClientSecret 配置与服务端可达性。", e);
        }

        if (string.IsNullOrEmpty(_tenantId) || string.IsNullOrEmpty(_configId))
        {
            throw new InvalidOperationException(
                "RPA callback 路径参数仍缺失："
                + $"tenant_id={(string.IsNullOrEmpty(_tenantId) ? "<空>" : _tenantId)}"
                + $", config_id={(string.IsNullOrEmpty(_configId) ? "<空>" : _configId)}。"
                + "请确认服务端已为该 client 在 tenant_channel_configs 表写入记录。");
        }
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
        // WS 路径同样需要 tenant_id / config_id（服务端路由：
        // /t/{tenant_id}/wecom_personal_rpa/ws/{config_id}），首连前确保就绪。
        await EnsureCallbackRoutingAsync(cancellationToken).ConfigureAwait(false);

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

        // 动态拼接：t/{tenant_id}/wecom_personal_rpa/ws/{config_id}
        var query = _signer.BuildWebSocketQuery();
        var uri = new Uri($"{wsBase}/{WsPath}?{query}");

        var ws = new ClientWebSocket();
        // TODO: 实际心跳 / 重连由 App 层的消息循环驱动，此处仅完成握手。
        await ws.ConnectAsync(uri, cancellationToken).ConfigureAwait(false);
        return ws;
    }

    /// <summary>
    /// 构造 ws 路径：``t/{tenant_id}/wecom_personal_rpa/ws/{config_id}``。
    /// 服务端路由：src/saas/api/wecom_personal_rpa_routes.py:817。
    /// 需在 EnsureCallbackRoutingAsync 后调用。
    /// </summary>
    private string WsPath
    {
        get
        {
            var tid = _tenantId;
            var cid = _configId;
            if (string.IsNullOrEmpty(tid) || string.IsNullOrEmpty(cid))
            {
                throw new InvalidOperationException(
                    "RPA ws 路径缺少 tenant_id 或 config_id："
                    + "请在 ConnectWebSocketAsync 前先调用 GetConfigAsync。");
            }

            return $"t/{tid}/wecom_personal_rpa/ws/{cid}";
        }
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
