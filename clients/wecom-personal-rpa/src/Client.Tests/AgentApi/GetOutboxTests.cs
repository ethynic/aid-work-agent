using System.Net;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Security;
using Xunit;

namespace WeCom.PersonalRpa.Tests.AgentApi;

/// <summary>
/// GetOutboxAsync HTTP 契约测试：GET /outbox?limit=N 路径 + 签名 + 响应反序列化。
/// 镜像 AgentApiClientRoutingTests 的 ProgrammableHandler + 真 AgentApiClient 模式。
/// </summary>
public sealed class GetOutboxTests
{
    private const string SampleOutboxJson = """
        {
          "protocol_version": "1.2.0",
          "server_time": "2026-07-10T12:00:00Z",
          "poll_interval_seconds": 5,
          "items": [
            {
              "type": "actions",
              "request_id": "req_1",
              "session_id": "sess1",
              "account_id": "acct1",
              "conversation_id": "conv1",
              "reply_context": {
                "sender_display_name": "陆伟@微信",
                "sender_stable_id": "wmS6abc",
                "conversation_search_name": "陆伟",
                "inbound_text": "晚上好",
                "agent_reply_text": "晚上好～"
              },
              "actions": [
                { "type": "send_text", "text": "晚上好～" }
              ]
            }
          ]
        }
        """;

    private static AgentApiClient BuildClient(Func<HttpRequestMessage, HttpResponseMessage> respond, out RecordingHandler handler)
    {
        handler = new RecordingHandler(respond);
        var http = new HttpClient(handler) { BaseAddress = new Uri("https://agent.example.com/") };
        var options = new ClientOptions
        {
            AgentBaseUrl = "https://agent.example.com",
            ClientId = "rpa_client_test",
            ClientSecret = "test-secret",
            TenantId = "tenant_t",
        };
        var signer = new RequestSigner(options.ClientId, Encoding.UTF8.GetBytes(options.ClientSecret));
        return new AgentApiClient(http, options, signer);
    }

    [Fact]
    public async Task GetOutboxAsync_BuildsPathWithLimitAndSigns_SendsGet()
    {
        var client = BuildClient(_ => JsonResp(SampleOutboxJson), out var handler);

        var resp = await client.GetOutboxAsync(100);

        Assert.Single(handler.Calls);
        var (method, uri, headers) = handler.Calls[0];
        Assert.Equal(HttpMethod.Get, method);
        Assert.Contains("channels/wecom-personal-rpa/outbox", uri);
        Assert.Contains("limit=100", uri);
        // HMAC 鉴权头存在
        Assert.True(headers.Contains("X-Client-Id"));
        Assert.True(headers.Contains("X-Signature"));

        Assert.Equal("1.2.0", resp.ProtocolVersion);
    }

    [Fact]
    public async Task GetOutboxAsync_DeserializesItems_IgnoresTypeField()
    {
        var client = BuildClient(_ => JsonResp(SampleOutboxJson), out _);

        var resp = await client.GetOutboxAsync();

        var item = Assert.Single(resp.Items);
        Assert.Equal("req_1", item.RequestId);
        Assert.Equal("conv1", item.ConversationId);
        Assert.NotNull(item.ReplyContext);
        Assert.Equal("陆伟", item.ReplyContext!.ConversationSearchName);
        var sendText = Assert.IsType<SendTextAction>(Assert.Single(item.Actions));
        Assert.Equal("晚上好～", sendText.Text);
    }

    [Fact]
    public async Task GetOutboxAsync_PreservesPollIntervalSeconds()
    {
        var client = BuildClient(_ => JsonResp(SampleOutboxJson), out _);
        var resp = await client.GetOutboxAsync();
        Assert.Equal(5, resp.PollIntervalSeconds);
    }

    [Fact]
    public async Task GetOutboxAsync_EmptyItems_ReturnsEmptyList()
    {
        var client = BuildClient(_ => JsonResp("""
            {"protocol_version":"1.2.0","server_time":"2026-07-10T12:00:00Z","poll_interval_seconds":5,"items":[]}
            """), out _);
        var resp = await client.GetOutboxAsync();
        Assert.Empty(resp.Items);
    }

    [Fact]
    public async Task GetOutboxAsync_LimitClampedToRange()
    {
        // limit 越界应被 clamp 到 [1,100]，避免服务端 400（Polly 不重试 400）
        var client = BuildClient(_ => JsonResp(SampleOutboxJson), out var handler);
        await client.GetOutboxAsync(9999);
        Assert.Contains("limit=100", handler.Calls[0].Uri);
    }

    [Fact]
    public async Task GetOutboxAsync_NonSuccess_Throws()
    {
        // 401 → EnsureSuccessStatusCode 抛 HttpRequestException（Polly 重试后仍抛），调用方据此退避
        var client = BuildClient(_ => new HttpResponseMessage(HttpStatusCode.Unauthorized), out _);
        await Assert.ThrowsAnyAsync<HttpRequestException>(() => client.GetOutboxAsync());
    }

    private static HttpResponseMessage JsonResp(string json)
        => new(HttpStatusCode.OK)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json"),
        };

    private sealed class RecordingHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, HttpResponseMessage> _respond;
        public List<(HttpMethod Method, string Uri, System.Net.Http.Headers.HttpRequestHeaders Headers)> Calls { get; } = new();

        public RecordingHandler(Func<HttpRequestMessage, HttpResponseMessage> respond) => _respond = respond;

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Calls.Add((request.Method, request.RequestUri?.PathAndQuery ?? "", request.Headers));
            return Task.FromResult(_respond(request));
        }
    }
}
