// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（349-369 行 + 422-438 行）。
// Qwen3-VL OpenAI 兼容协议调用，messages.content 数组混合 image_url + text。
//
// 设计要点：
//   1. HttpClient 走构造函数注入（plan §A3，438 行），便于单测 mock HttpMessageHandler，
//      也便于 Client.App 通过 IHttpClientFactory 配置超时 / 重试策略。
//   2. API Key 池：从 VisionConfig.ApiKeys 取，Interlocked 轮询；429 跳下一个；
//      所有 key 都 401 才抛 VisionApiAuthException（plan §A3，434 行）。
//   3. OpenAI 兼容协议：messages[0].content 是数组，包含 image_url + text 两个 part。
//   4. 日志脱敏：API key 永远是 ***，base64 图片只打长度（plan §A3 硬约束 3）。
// ====================================================================================

using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using Serilog;

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// Qwen3-VL / OpenAI 兼容协议的视觉 API 实现。
/// </summary>
public sealed class QwenVisionApi : IVisionApi
{
    private static readonly ILogger Logger = Log.ForContext<QwenVisionApi>();

    private readonly HttpClient _httpClient;
    private readonly VisionConfig _config;
    private readonly string _apiEndpoint;
    private readonly string[] _apiKeys;
    private int _keyIndex = -1; // 第一次 Increment 后变 0

    /// <summary>
    /// 构造。
    /// </summary>
    /// <param name="httpClient">由 DI 注入的 HttpClient（建议由 IHttpClientFactory 创建）。</param>
    /// <param name="config">视觉配置。读 ApiEndpoint / ApiKeys / TimeoutSeconds。</param>
    public QwenVisionApi(HttpClient httpClient, VisionConfig config)
    {
        _httpClient = httpClient ?? throw new ArgumentNullException(nameof(httpClient));
        _config = config ?? throw new ArgumentNullException(nameof(config));
        _apiEndpoint = string.IsNullOrWhiteSpace(config.ApiEndpoint)
            ? "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            : config.ApiEndpoint;
        _apiKeys = (config.ApiKeys ?? Array.Empty<string>())
            .Where(k => !string.IsNullOrWhiteSpace(k))
            .Select(k => k.Trim())
            .ToArray();

        if (_httpClient.BaseAddress is null)
        {
            _httpClient.Timeout = TimeSpan.FromSeconds(
                config.TimeoutSeconds > 0 ? config.TimeoutSeconds : 120);
        }
    }

    /// <inheritdoc />
    public async Task<VisionApiResponse> CallAsync(
        string model,
        string imageJpegBase64,
        string prompt,
        double temperature = 0.1,
        int maxTokens = 4096,
        CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(model))
            throw new ArgumentException("model 不能为空", nameof(model));
        if (string.IsNullOrWhiteSpace(imageJpegBase64))
            throw new ArgumentException("imageJpegBase64 不能为空", nameof(imageJpegBase64));
        if (string.IsNullOrWhiteSpace(prompt))
            throw new ArgumentException("prompt 不能为空", nameof(prompt));
        if (_apiKeys.Length == 0)
            throw new VisionApiAuthException("VisionConfig.ApiKeys 未配置任何 API Key");

        var body = BuildRequestBody(model, imageJpegBase64, prompt, temperature, maxTokens);
        // 匿名类型序列化：用 reflection-based options（source-gen 对匿名类型不支持）
        var bodyJson = JsonSerializer.Serialize(body, VisionJsonOptions.Instance);
        Logger.Information(
            "后端日志：QwenVisionApi.CallAsync model={Model} prompt_len={PromptLen} img_len={ImgLen} endpoint={Ep}",
            model, prompt.Length, imageJpegBase64.Length, _apiEndpoint);

        Exception? lastTransient = null;
        var deadKeys = new HashSet<int>();
        var startedAt = DateTimeOffset.UtcNow;

        // 轮询 key 池：每个 key 试一遍，429/5xx 跳下一个，401 标 dead，全 401 抛 AuthException。
        for (int attempt = 0; attempt < _apiKeys.Length; attempt++)
        {
            int keyIdx = Interlocked.Increment(ref _keyIndex) % _apiKeys.Length;
            if (deadKeys.Contains(keyIdx)) continue;
            string apiKey = _apiKeys[keyIdx];

            using var req = new HttpRequestMessage(HttpMethod.Post, _apiEndpoint)
            {
                Content = new StringContent(bodyJson, System.Text.Encoding.UTF8, "application/json")
            };
            req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", apiKey);
            req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));

            HttpResponseMessage resp;
            try
            {
                resp = await _httpClient.SendAsync(req, HttpCompletionOption.ResponseContentRead, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (TaskCanceledException tce) when (!cancellationToken.IsCancellationRequested)
            {
                // HTTP 超时（区别于外部 CancellationToken 触发的取消）
                lastTransient = new VisionApiTransientException(
                    $"QwenVisionApi HTTP 超时（{(int)_httpClient.Timeout.TotalSeconds}s）", tce);
                Logger.Warning("后端日志：QwenVisionApi HTTP 超时 key_idx={Idx}", keyIdx);
                continue;
            }

            try
            {
                if (resp.StatusCode == HttpStatusCode.Unauthorized || resp.StatusCode == HttpStatusCode.Forbidden)
                {
                    deadKeys.Add(keyIdx);
                    Logger.Warning("后端日志：QwenVisionApi key_idx={Idx} 返回 {Status}（鉴权失败，跳过）",
                        keyIdx, (int)resp.StatusCode);
                    continue;
                }
                if (resp.StatusCode == HttpStatusCode.TooManyRequests)
                {
                    lastTransient = new VisionApiTransientException("QwenVisionApi 429 限流，换下一个 key");
                    Logger.Warning("后端日志：QwenVisionApi key_idx={Idx} 429 限流", keyIdx);
                    continue;
                }
                if ((int)resp.StatusCode >= 500)
                {
                    string body5xx = await SafeReadAsStringAsync(resp, cancellationToken);
                    lastTransient = new VisionApiTransientException(
                        $"QwenVisionApi 5xx status={(int)resp.StatusCode} body={Truncate(body5xx, 200)}");
                    Logger.Warning("后端日志：QwenVisionApi key_idx={Idx} 5xx={Status}", keyIdx, (int)resp.StatusCode);
                    continue;
                }
                if (!resp.IsSuccessStatusCode)
                {
                    // 4xx 其它（400/404 等）：直接抛 Auth（参数错误也算不可恢复）
                    string body4xx = await SafeReadAsStringAsync(resp, cancellationToken);
                    throw new VisionApiAuthException(
                        $"QwenVisionApi {(int)resp.StatusCode}: {Truncate(body4xx, 500)}");
                }

                // 2xx：解析 OpenAI 标准响应
                string respJson = await SafeReadAsStringAsync(resp, cancellationToken);
                double elapsed = (DateTimeOffset.UtcNow - startedAt).TotalSeconds;
                return ParseOpenAiResponse(respJson, model, elapsed);
            }
            finally
            {
                resp.Dispose();
            }
        }

        if (deadKeys.Count == _apiKeys.Length)
        {
            throw new VisionApiAuthException(
                $"所有 {_apiKeys.Length} 个 API Key 全部鉴权失败（401/403）");
        }
        if (lastTransient is not null) throw lastTransient;
        throw new VisionApiTransientException("QwenVisionApi 调用失败但无具体异常（不应发生）");
    }

    private static object BuildRequestBody(
        string model, string imageJpegBase64, string prompt, double temperature, int maxTokens)
    {
        // OpenAI 兼容协议：messages[0].content 是数组，混合 image_url + text
        // 参考阿里云 Model Studio 文档 qwen-vl-compatible-with-openai
        return new
        {
            model,
            messages = new[]
            {
                new
                {
                    role = "user",
                    content = new object[]
                    {
                        new
                        {
                            type = "image_url",
                            image_url = new { url = $"data:image/jpeg;base64,{imageJpegBase64}" }
                        },
                        new { type = "text", text = prompt }
                    }
                }
            },
            temperature,
            max_tokens = maxTokens
        };
    }

    private static VisionApiResponse ParseOpenAiResponse(string respJson, string requestedModel, double elapsed)
    {
        if (string.IsNullOrWhiteSpace(respJson))
            throw new VisionApiTransientException("QwenVisionApi 收到空响应体");

        using var doc = JsonDocument.Parse(respJson);
        JsonElement root = doc.RootElement;

        // choices[0].message.content —— OpenAI 标准路径
        string content = "";
        if (root.TryGetProperty("choices", out var choices) && choices.GetArrayLength() > 0)
        {
            JsonElement firstChoice = choices[0];
            if (firstChoice.TryGetProperty("message", out var msg) &&
                msg.TryGetProperty("content", out var contentEl))
            {
                // content 可能是 string，也可能是数组（部分 provider 返回结构不同）
                content = contentEl.ValueKind switch
                {
                    JsonValueKind.String => contentEl.GetString() ?? "",
                    JsonValueKind.Array => ExtractTextFromContentArray(contentEl),
                    _ => contentEl.ToString()
                };
            }
        }

        int totalTokens = 0;
        if (root.TryGetProperty("usage", out var usage) &&
            usage.TryGetProperty("total_tokens", out var tt) &&
            tt.TryGetInt32(out int parsedTokens))
        {
            totalTokens = parsedTokens;
        }

        string actualModel = requestedModel;
        if (root.TryGetProperty("model", out var mEl) && mEl.ValueKind == JsonValueKind.String)
        {
            actualModel = mEl.GetString() ?? requestedModel;
        }

        // 剥 markdown fence（与 parse_bbox_json 入口预处理一致）
        content = StripMarkdownFence(content);

        Logger.Information(
            "后端日志：QwenVisionApi 响应解析成功 model={Model} tokens={Tokens} elapsed={Elapsed}s content_len={Len}",
            actualModel, totalTokens, elapsed.ToString("0.00"), content.Length);

        return new VisionApiResponse
        {
            Content = content,
            TotalTokens = totalTokens,
            ElapsedSeconds = elapsed,
            Model = actualModel
        };
    }

    private static string ExtractTextFromContentArray(JsonElement contentArray)
    {
        // 兼容 content 返回为 [{"type":"text","text":"..."}, ...] 的情况
        var sb = new System.Text.StringBuilder();
        foreach (JsonElement part in contentArray.EnumerateArray())
        {
            if (part.TryGetProperty("type", out var tEl) &&
                tEl.ValueKind == JsonValueKind.String &&
                tEl.GetString() == "text" &&
                part.TryGetProperty("text", out var txtEl) &&
                txtEl.ValueKind == JsonValueKind.String)
            {
                sb.Append(txtEl.GetString());
            }
        }
        return sb.ToString();
    }

    /// <summary>
    /// 剥除模型响应外层的 ```json ... ``` 或 ``` ... ``` 代码块围栏。
    /// 完整复刻 probe-qwen-vl-full.py 的 parse_bbox_json 入口预处理（91-97 行）：
    /// <c>re.sub(r"^```(?:json)?\s*", "", s)</c> + <c>re.sub(r"\s*```$", "", s)</c>
    /// </summary>
    public static string StripMarkdownFence(string content)
    {
        if (string.IsNullOrEmpty(content)) return string.Empty;
        string s = content;

        // 去开头：``` + 可选语言标识 + 空白字符
        // 与 Python `^```(?:json)?\s*` 等价：要求 s 开头是 ```
        if (s.Length >= 3 && s.StartsWith("```"))
        {
            int i = 3;
            // 可选语言标识（json/text/... 等 ASCII 字母），直到遇到换行或非字母
            while (i < s.Length && char.IsLetter(s[i])) i++;
            // 吃掉空白字符（含换行）
            while (i < s.Length && char.IsWhiteSpace(s[i])) i++;
            s = s.Substring(i);
        }

        // 去结尾：吃掉末尾的空白 + ```
        s = s.TrimEnd();
        if (s.Length >= 3 && s.EndsWith("```"))
        {
            s = s.Substring(0, s.Length - 3);
        }
        return s.Trim();
    }

    private static async Task<string> SafeReadAsStringAsync(HttpResponseMessage resp, CancellationToken ct)
    {
        try { return await resp.Content.ReadAsStringAsync(ct).ConfigureAwait(false); }
        catch { return string.Empty; }
    }

    private static string Truncate(string s, int max)
        => string.IsNullOrEmpty(s) ? "" : (s.Length <= max ? s : s.Substring(0, max) + "...");
}

/// <summary>
/// JSON 序列化选项（reflection-based，因 BuildRequestBody 用匿名类型无法用 source-gen）。
/// snake_case 命名对齐 OpenAI 兼容协议（max_tokens / image_url 等）。
/// </summary>
internal static class VisionJsonOptions
{
    public static readonly JsonSerializerOptions Instance = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        WriteIndented = false
    };
}
