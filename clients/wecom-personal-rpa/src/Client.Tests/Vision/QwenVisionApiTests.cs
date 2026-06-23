// ====================================================================================
// 任务 A3 单测：QwenVisionApi。
// 契约：plan-wecom-personal-rpa-vision.md §A3 验收（455 行）。
// 用最小化 StubHttpMessageHandler 注入到 HttpClient，模拟 2xx/401/5xx/fence 包裹等场景。
// ====================================================================================

using System.Net;
using System.Text;
using System.Text.Json;
using WeCom.PersonalRpa.Automation.Vision;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Vision;

/// <summary>QwenVisionApi 单测。</summary>
public sealed class QwenVisionApiTests
{
    private static VisionConfig MakeConfig() => new()
    {
        ApiKeys = new[] { "key-1", "key-2" },
        TimeoutSeconds = 5,
        ApiEndpoint = "https://test.example.com/v1/chat/completions"
    };

    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly Func<HttpRequestMessage, HttpResponseMessage> _factory;
        public int CallCount { get; private set; }
        public List<(string AuthHeader, string Body)> Received { get; } = new();

        public StubHandler(Func<HttpRequestMessage, HttpResponseMessage> factory)
        {
            _factory = factory;
        }

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            CallCount++;
            Received.Add((
                request.Headers.Authorization?.ToString() ?? "(none)",
                request.Content?.ReadAsStringAsync(cancellationToken).Result ?? ""));
            return Task.FromResult(_factory(request));
        }
    }

    private static HttpResponseMessage JsonResp(string content, int totalTokens = 100, string model = "qwen3-vl-plus")
    {
        string body = JsonSerializer.Serialize(new
        {
            choices = new[]
            {
                new { message = new { role = "assistant", content } }
            },
            usage = new { total_tokens = totalTokens },
            model
        });
        return new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(body, Encoding.UTF8, "application/json")
        };
    }

    // ========================================================================
    // 1. 正常响应解析
    // ========================================================================

    [Fact]
    public async Task CallAsync_StandardResponse_ParsesContentAndTokens()
    {
        var handler = new StubHandler(_ => JsonResp("[{\"label\":\"发送\",\"bbox\":[1,2,3,4],\"type\":\"button\"}]", 137));
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        VisionApiResponse resp = await api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt");

        Assert.Equal("[{\"label\":\"发送\",\"bbox\":[1,2,3,4],\"type\":\"button\"}]", resp.Content);
        Assert.Equal(137, resp.TotalTokens);
        Assert.Equal("qwen3-vl-plus", resp.Model);
        Assert.True(resp.ElapsedSeconds >= 0);
        Assert.Equal(1, handler.CallCount);
    }

    // ========================================================================
    // 2. markdown fence 被剥离
    // ========================================================================

    [Fact]
    public async Task CallAsync_ContentWithFence_StripsFence()
    {
        string rawContent = "```json\n[{\"label\":\"搜索\",\"bbox\":[1,2,3,4],\"type\":\"input\"}]\n```";
        var handler = new StubHandler(_ => JsonResp(rawContent));
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        VisionApiResponse resp = await api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt");

        Assert.Equal("[{\"label\":\"搜索\",\"bbox\":[1,2,3,4],\"type\":\"input\"}]", resp.Content);
    }

    // ========================================================================
    // 3. 401 鉴权失败
    // ========================================================================

    [Fact]
    public async Task CallAsync_AllKeys401_ThrowsAuthException()
    {
        var handler = new StubHandler(_ => new HttpResponseMessage(HttpStatusCode.Unauthorized)
        {
            Content = new StringContent("{\"error\":\"invalid api key\"}", Encoding.UTF8)
        });
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        await Assert.ThrowsAsync<VisionApiAuthException>(() =>
            api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt"));

        // 2 个 key 都试过
        Assert.Equal(2, handler.CallCount);
    }

    // ========================================================================
    // 4. 5xx 触发重试，重试耗尽抛 Transient
    // ========================================================================

    [Fact]
    public async Task CallAsync_AllKeys500_ThrowsTransientException()
    {
        var handler = new StubHandler(_ => new HttpResponseMessage(HttpStatusCode.InternalServerError)
        {
            Content = new StringContent("oops", Encoding.UTF8)
        });
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        await Assert.ThrowsAsync<VisionApiTransientException>(() =>
            api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt"));

        // 每个 key 试 1 次（5xx 直接跳下一个 key，不在同 key 上重试）
        Assert.Equal(2, handler.CallCount);
    }

    // ========================================================================
    // 5. 429 跳下一个 key
    // ========================================================================

    [Fact]
    public async Task CallAsync_FirstKey429_SecondKeySucceeds()
    {
        int attempt = 0;
        var handler = new StubHandler(_ =>
        {
            attempt++;
            return attempt == 1
                ? new HttpResponseMessage(HttpStatusCode.TooManyRequests)
                : JsonResp("[]", 10);
        });
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        VisionApiResponse resp = await api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt");

        Assert.Equal("[]", resp.Content);
        Assert.Equal(2, handler.CallCount);
        // 第二次请求用了不同的 key（key-2）
        Assert.Equal("Bearer key-2", handler.Received[1].AuthHeader);
    }

    // ========================================================================
    // 6. body 格式验证（OpenAI 兼容协议）
    // ========================================================================

    [Fact]
    public async Task CallAsync_BuildsOpenAiCompatibleBody()
    {
        var handler = new StubHandler(_ => JsonResp("[]"));
        var api = new QwenVisionApi(new HttpClient(handler), MakeConfig());

        await api.CallAsync("qwen3-vl-plus", "img-base64-data", "find elements", temperature: 0.2, maxTokens: 1024);

        string body = handler.Received[0].Body;
        using var doc = JsonDocument.Parse(body);
        JsonElement root = doc.RootElement;
        Assert.Equal("qwen3-vl-plus", root.GetProperty("model").GetString());
        Assert.Equal(0.2, root.GetProperty("temperature").GetDouble(), precision: 3);
        Assert.Equal(1024, root.GetProperty("max_tokens").GetInt32());

        JsonElement content = root.GetProperty("messages")[0].GetProperty("content");
        Assert.Equal(2, content.GetArrayLength());
        Assert.Equal("image_url", content[0].GetProperty("type").GetString());
        string imgUrl = content[0].GetProperty("image_url").GetProperty("url").GetString()!;
        Assert.StartsWith("data:image/jpeg;base64,img-base64-data", imgUrl);
        Assert.Equal("text", content[1].GetProperty("type").GetString());
        Assert.Equal("find elements", content[1].GetProperty("text").GetString());
    }

    // ========================================================================
    // 7. 空 ApiKeys 抛 AuthException
    // ========================================================================

    [Fact]
    public async Task CallAsync_NoApiKeys_ThrowsAuthException()
    {
        var handler = new StubHandler(_ => JsonResp("[]"));
        var config = MakeConfig();
        config.ApiKeys = Array.Empty<string>();
        var api = new QwenVisionApi(new HttpClient(handler), config);

        await Assert.ThrowsAsync<VisionApiAuthException>(() =>
            api.CallAsync("qwen3-vl-plus", "aGVsbG8=", "prompt"));
        Assert.Equal(0, handler.CallCount);
    }

    // ========================================================================
    // 8. StripMarkdownFence 单测（internal 方法直接调）
    // ========================================================================

    [Theory]
    [InlineData("```json\n[1,2,3]\n```", "[1,2,3]")]
    [InlineData("```\nhello\n```", "hello")]
    [InlineData("plain text", "plain text")]
    [InlineData("```json[1,2]```", "[1,2]")]   // 单行 fence
    [InlineData("", "")]
    public void StripMarkdownFence_Variants(string input, string expected)
    {
        Assert.Equal(expected, QwenVisionApi.StripMarkdownFence(input));
    }
}
