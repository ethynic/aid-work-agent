using System.Net;
using System.Text;
using System.Text.Json;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Security;
using Xunit;

namespace WeCom.PersonalRpa.Tests.AgentApi;

// ====================================================================================
// 契约来源：src/saas/api/wecom_personal_rpa_routes.py:191 / :663 / :817
//   callback 路径：POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}
//   config 路径：  GET  /api/v1/channels/wecom-personal-rpa/config
//   ws 路径：      WS   /t/{tenant_id}/wecom_personal_rpa/ws/{config_id}
//
// 客户端 AgentApiClient.CallbackPath / WsPath 必须用 /config 响应里的
// tenant_id / config_id 动态拼接（旧实现写死的 api/v1/channels/.../callback 与
// 服务端路由完全不匹配，导致客户端无法联调）。
//
// 本测试用本地 HttpMessageHandler mock 捕获出站请求 URI，验证：
//   1. GetConfigAsync 成功后 _tenantId / _configId 被填充（通过后续 PostCallbackAsync
//      能拼出正确路径反推）。
//   2. PostCallbackAsync 在已拉 config 后，POST 的 URI 路径含 tenant_id / config_id。
//   3. PostCallbackAsync 在未拉 config 时，会自动同步拉一次 /config 再发送。
// ====================================================================================

/// <summary>
/// 可编程 HttpMessageHandler：按请求 URI 返回预设响应，并记录所有出站请求 URI。
/// </summary>
internal sealed class ProgrammableHandler : HttpMessageHandler
{
    private readonly Dictionary<string, Func<HttpResponseMessage>> _routes = new();
    public List<string> RequestUris { get; } = new();

    public void WhenPathContains(string pathFragment, Func<HttpResponseMessage> respond)
    {
        _routes[pathFragment] = respond;
    }

    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var uri = request.RequestUri?.AbsolutePath ?? "";
        RequestUris.Add(uri);

        foreach (var kv in _routes)
        {
            if (uri.Contains(kv.Key, StringComparison.OrdinalIgnoreCase))
            {
                return Task.FromResult(kv.Value());
            }
        }

        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound)
        {
            Content = new StringContent("no route for " + uri),
        });
    }
}

public class AgentApiClientRoutingTests
{
    private static RpaConfigResponse BuildConfigPayload(string tenantId, string configId, string clientId)
    {
        return new RpaConfigResponse
        {
            ProtocolVersion = "1.0.0",
            MinClientVersion = "1.0.0",
            Paused = false,
            PausedScope = null,
            RateLimits = new RateLimits(),
            ServerTime = DateTimeOffset.UtcNow,
            ClientId = clientId,
            TenantId = tenantId,
            ConfigId = configId,
        };
    }

    private static AgentApiClient BuildClient(ProgrammableHandler handler, string tenantIdInOptions = "")
    {
        var httpClient = new HttpClient(handler)
        {
            BaseAddress = new Uri("https://agent.example.com/"),
        };
        var options = new ClientOptions
        {
            AgentBaseUrl = "https://agent.example.com",
            ClientId = "client_test_001",
            ClientSecret = "test-secret",
            TenantId = tenantIdInOptions,
        };
        var secretBytes = Encoding.UTF8.GetBytes(options.ClientSecret);
        var signer = new RequestSigner(options.ClientId, secretBytes);
        return new AgentApiClient(httpClient, options, signer);
    }

    private static StringContent ConfigJsonContent(RpaConfigResponse cfg)
    {
        var json = JsonSerializer.Serialize(cfg, ProtocolJsonOptions.Instance);
        return new StringContent(json, Encoding.UTF8, "application/json");
    }

    private static JsonElement EmptyPayloadElement()
    {
        // InboundEvent.Payload 是 JsonElement；用空 dict 序列化成 JsonElement
        return JsonSerializer.SerializeToElement(new Dictionary<string, object?>(),
            ProtocolJsonOptions.Instance);
    }

    [Fact(DisplayName = "GetConfigAsync 后 PostCallbackAsync 路径含 tenant_id/config_id")]
    public async Task PostCallbackAsync_AfterGetConfig_UsesDynamicCallbackPath()
    {
        var handler = new ProgrammableHandler();
        var cfg = BuildConfigPayload("tenant_acme", "cfg_acme_001", "client_test_001");
        handler.WhenPathContains("channels/wecom-personal-rpa/config",
            () => new HttpResponseMessage(HttpStatusCode.OK) { Content = ConfigJsonContent(cfg) });
        handler.WhenPathContains("wecom_personal_rpa/callback",
            () => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{\"result\":\"accepted\"}"),
            });

        var api = BuildClient(handler);

        // 先拉 config，缓存 tenant_id / config_id
        var resp = await api.GetConfigAsync();
        Assert.Equal("tenant_acme", resp.TenantId);
        Assert.Equal("cfg_acme_001", resp.ConfigId);

        // 再发 callback，路径应为 t/tenant_acme/wecom_personal_rpa/callback/cfg_acme_001
        var env = new InboundEvent
        {
            EventId = "evt_test_001",
            ClientId = "client_test_001",
            AccountId = "acct_001",
            EventType = EventType.Message,
            OccurredAt = DateTimeOffset.UtcNow,
            Payload = EmptyPayloadElement(),
        };
        var ok = await api.PostCallbackAsync(env);

        Assert.True(ok);
        // 最后一个请求 URI 是 callback 路径
        var callbackUri = RequestUrisEndsWith(handler, "wecom_personal_rpa/callback/cfg_acme_001");
        Assert.True(callbackUri, $"expected callback path, got: {string.Join(" | ", handler.RequestUris)}");
        Assert.Contains("/t/tenant_acme/wecom_personal_rpa/callback/cfg_acme_001", handler.RequestUris[^1]);
    }

    [Fact(DisplayName = "未拉 config 直接 PostCallbackAsync 会自动同步拉取")]
    public async Task PostCallbackAsync_WithoutPriorConfig_AutoFetchesConfig()
    {
        var handler = new ProgrammableHandler();
        var cfg = BuildConfigPayload("tenant_beta", "cfg_beta_002", "client_test_001");
        handler.WhenPathContains("channels/wecom-personal-rpa/config",
            () => new HttpResponseMessage(HttpStatusCode.OK) { Content = ConfigJsonContent(cfg) });
        handler.WhenPathContains("wecom_personal_rpa/callback",
            () => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{\"result\":\"accepted\"}"),
            });

        var api = BuildClient(handler);

        var env = new InboundEvent
        {
            EventId = "evt_test_002",
            ClientId = "client_test_001",
            AccountId = "acct_002",
            EventType = EventType.Status,
            OccurredAt = DateTimeOffset.UtcNow,
            Payload = EmptyPayloadElement(),
        };
        var ok = await api.PostCallbackAsync(env);

        Assert.True(ok);
        // 两个请求：先 /config，再 callback
        Assert.True(handler.RequestUris.Count >= 2,
            $"expected >=2 requests (config + callback), got {handler.RequestUris.Count}");
        Assert.Contains(handler.RequestUris,
            u => u.Contains("channels/wecom-personal-rpa/config", StringComparison.OrdinalIgnoreCase));
        Assert.Contains(handler.RequestUris,
            u => u.EndsWith("/t/tenant_beta/wecom_personal_rpa/callback/cfg_beta_002",
                StringComparison.OrdinalIgnoreCase));
    }

    [Fact(DisplayName = "服务端未返回 tenant_id 时回退到 ClientOptions.TenantId")]
    public async Task GetConfigAsync_ServerOmitsTenantId_FallsBackToOptions()
    {
        var handler = new ProgrammableHandler();
        // 服务端旧版本：不返回 tenant_id / config_id
        var cfg = new RpaConfigResponse
        {
            ProtocolVersion = "1.0.0",
            MinClientVersion = "1.0.0",
            Paused = false,
            RateLimits = new RateLimits(),
            ServerTime = DateTimeOffset.UtcNow,
            ClientId = "client_test_001",
            TenantId = null,
            ConfigId = null,
        };
        handler.WhenPathContains("channels/wecom-personal-rpa/config",
            () => new HttpResponseMessage(HttpStatusCode.OK) { Content = ConfigJsonContent(cfg) });
        handler.WhenPathContains("wecom_personal_rpa/callback",
            () => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("{\"result\":\"accepted\"}"),
            });

        // ClientOptions.TenantId 作为兜底
        var api = BuildClient(handler, tenantIdInOptions: "tenant_fallback");

        var resp = await api.GetConfigAsync();
        Assert.Null(resp.TenantId);

        var env = new InboundEvent
        {
            EventId = "evt_test_003",
            ClientId = "client_test_001",
            AccountId = "acct_003",
            EventType = EventType.Status,
            OccurredAt = DateTimeOffset.UtcNow,
            Payload = EmptyPayloadElement(),
        };

        // config_id 仍缺失（服务端没返回，也没兜底）→ 应抛清晰异常
        var ex = await Assert.ThrowsAsync<InvalidOperationException>(
            () => api.PostCallbackAsync(env));
        Assert.Contains("config_id", ex.Message);
    }

    private static bool RequestUrisEndsWith(ProgrammableHandler handler, string fragment)
    {
        return handler.RequestUris.Any(u => u.Contains(fragment, StringComparison.OrdinalIgnoreCase));
    }
}
