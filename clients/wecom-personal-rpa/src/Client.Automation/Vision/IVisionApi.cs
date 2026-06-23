// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（349-369 行）。
// IVisionApi 抽象 OpenAI 兼容协议调用，便于单测 mock HttpMessageHandler，
// 也便于未来切 GPT-4V / Claude 等其他多模态模型。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉多模态 API 客户端抽象（OpenAI chat/completions 兼容协议）。
/// QwenVisionApi 是默认实现；单测通过实现本接口或注入 mock HttpMessageHandler 替换。
/// </summary>
public interface IVisionApi
{
    /// <summary>
    /// 调用多模态模型，返回已剥 markdown fence 的原始 content 文本。
    /// </summary>
    /// <param name="model">模型名（qwen3-vl-plus / qwen3-vl-max）。</param>
    /// <param name="imageJpegBase64">RGB JPEG base64（<b>不含</b> data: 前缀）。</param>
    /// <param name="prompt">提示词。grounding prompt / OCR prompt 等。</param>
    /// <param name="temperature">采样温度，默认 0.1（grounding 场景追求稳定）。</param>
    /// <param name="maxTokens">单次最大 tokens，默认 4096。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>响应 DTO，Content 字段已剥除 markdown fence。</returns>
    /// <exception cref="VisionApiAuthException">所有 key 都 401 / 403 时抛出。</exception>
    /// <exception cref="VisionApiTransientException">5xx / 超时 / 429（重试耗尽后）抛出。</exception>
    Task<VisionApiResponse> CallAsync(
        string model,
        string imageJpegBase64,
        string prompt,
        double temperature = 0.1,
        int maxTokens = 4096,
        CancellationToken cancellationToken = default);
}

/// <summary>
/// 视觉 API 响应 DTO。承载模型返回的原始文本和 token 用量。
/// </summary>
public sealed class VisionApiResponse
{
    /// <summary>模型返回的原始文本内容（已剥 markdown fence，但未做 JSON 解析）。</summary>
    public string Content { get; init; } = "";

    /// <summary>本次调用的总 token 数（usage.total_tokens）。用于成本统计。</summary>
    public int TotalTokens { get; init; }

    /// <summary>本次调用耗时（秒，含网络 + 模型推理）。</summary>
    public double ElapsedSeconds { get; init; }

    /// <summary>实际命中的模型名（便于 fallback 切换后回查）。</summary>
    public string Model { get; init; } = "";
}
