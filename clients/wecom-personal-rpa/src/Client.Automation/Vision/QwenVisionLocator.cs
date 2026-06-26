// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（371-385 行 + 422-438 行）。
// 设计文档：docs/system/wecom-personal-rpa-vision-design.md §3.3 视觉缓存策略。
// Python 原型：scripts/probe-qwen-vl-full.py task2_grounding（174-194 行）+ parse_bbox_json（91-116 行）。
//
// LocateAsync 流程（design §3.3）：
//   1. IScreenCapturer.CaptureWeComMainWindowAsync → Bitmap + fingerprint + windowRect
//   2. IVisionCache.TryGetAsync → 命中直接返回 Source="cache"
//   3. 未命中 → Bitmap 转 JPEG base64 → IVisionApi.CallAsync(PrimaryModel, ...)
//   4. 解析 JSON（parse_bbox_json 鲁棒解析）→ 按 (elementType, labelKeyword) 过滤
//   5. bbox 越界检查（tolerance=5）→ 越界抛 BboxOutOfBoundsException + cache.InvalidateWindowAsync
//   6. IVisionCache.SetAsync → 返回 Source="api"
//   7. 失败：5xx/超时按 MaxRetries 指数退避，仍失败切 FallbackModel；空数组 → 重试 1 次 → 仍空返回 null
// ====================================================================================

using System.Drawing;
using System.Drawing.Imaging;
using System.Text.Json;
using System.Text.RegularExpressions;
using Serilog;

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// Qwen3-VL 视觉定位主策略实现。
/// </summary>
public sealed class QwenVisionLocator : IVisionLocator, IDisposable
{
    private static readonly ILogger Logger = Log.ForContext<QwenVisionLocator>();

    private readonly IVisionApi _visionApi;
    private readonly IScreenCapturer _capturer;
    private readonly IVisionCache _cache;
    private readonly VisionConfig _config;
    private bool _disposed;

    /// <summary>构造。</summary>
    public QwenVisionLocator(
        IVisionApi visionApi,
        IScreenCapturer capturer,
        IVisionCache cache,
        VisionConfig config)
    {
        _visionApi = visionApi ?? throw new ArgumentNullException(nameof(visionApi));
        _capturer = capturer ?? throw new ArgumentNullException(nameof(capturer));
        _cache = cache ?? throw new ArgumentNullException(nameof(cache));
        _config = config ?? throw new ArgumentNullException(nameof(config));
    }

    /// <inheritdoc />
    public async Task<VisionProbeResult?> LocateAsync(
        string elementType,
        string labelKeyword,
        CancellationToken cancellationToken = default)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);

        if (string.IsNullOrWhiteSpace(elementType))
            throw new ArgumentException("elementType 不能为空", nameof(elementType));
        if (string.IsNullOrWhiteSpace(labelKeyword))
            throw new ArgumentException("labelKeyword 不能为空", nameof(labelKeyword));

        // 1. 截企微主窗口
        CaptureResult capture = await _capturer.CaptureWeComMainWindowAsync(cancellationToken).ConfigureAwait(false);
        if (capture.Bitmap is null)
            throw new InvalidOperationException("ScreenCapturer 返回 Bitmap=null");

        try
        {
        int imgW = capture.WindowRect.Width > 0 ? capture.WindowRect.Width : capture.Bitmap.Width;
        int imgH = capture.WindowRect.Height > 0 ? capture.WindowRect.Height : capture.Bitmap.Height;

        // 2. 查缓存
        CachedEntry? cached = await _cache.TryGetAsync(
            capture.Fingerprint, elementType, labelKeyword, cancellationToken).ConfigureAwait(false);
        if (cached is not null)
        {
            Logger.Information(
                "后端日志：QwenVisionLocator 缓存命中 type={Type} kw={Kw} bbox={Bbox}",
                elementType, labelKeyword, cached.Bbox);
            return new VisionProbeResult
            {
                Bbox = cached.Bbox,
                Confidence = 1.0,
                Source = "cache",
                ElementType = elementType,
                LabelKeyword = labelKeyword,
                ModelUsed = _config.PrimaryModel
            };
        }

        // 3. 未命中 → 转 JPEG base64
        string imgB64 = ImageEncoder.EncodeJpegBase64(capture.Bitmap);
        string prompt = BuildGroundingPrompt(imgW, imgH);

        // 4-6. 调 API + 过滤 + 越界检查 + 写缓存
        VisionProbeResult? result = await CallAndExtractAsync(
            capture.Fingerprint, imgW, imgH, imgB64, prompt,
            elementType, labelKeyword, cancellationToken).ConfigureAwait(false);

        if (result is null) return null;

        // 越界检查后写入缓存
        if (!result.Bbox.IsWithin(imgW, imgH, tolerance: 5))
        {
            Logger.Error(
                "后端日志：QwenVisionLocator bbox 越界 bbox={Bbox} imgW={W} imgH={H}，失效窗口缓存",
                result.Bbox, imgW, imgH);
            await _cache.InvalidateWindowAsync(capture.Fingerprint, cancellationToken).ConfigureAwait(false);
            throw new BboxOutOfBoundsException(
                $"模型返回 bbox 越界：{result.Bbox} 超出图像 {imgW}x{imgH} ±5px")
            {
                Bbox = result.Bbox,
                ImageWidth = imgW,
                ImageHeight = imgH
            };
        }

        await _cache.SetAsync(
            capture.Fingerprint, elementType, labelKeyword, result.Bbox, cancellationToken)
            .ConfigureAwait(false);
        return result;
        }
        finally
        {
            capture.Dispose();
        }
    }

    /// <inheritdoc />
    public Task InvalidateCacheAsync()
    {
        // 不知道当前窗口指纹，需要由 WeComAutomation 拿到 fingerprint 后直接调 IVisionCache.InvalidateWindowAsync
        Logger.Information("后端日志：QwenVisionLocator.InvalidateCacheAsync（无指纹参数，需调用方走 IVisionCache.InvalidateWindowAsync）");
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        // _visionApi 的 HttpClient 由 DI 容器管理，这里不释放
    }

    // =================================================================================
    // 私有：调用 API + 鲁棒解析 + 过滤 + 重试 / fallback 切换
    // =================================================================================

    private async Task<VisionProbeResult?> CallAndExtractAsync(
        WindowFingerprint fingerprint,
        int imgW,
        int imgH,
        string imgB64,
        string prompt,
        string elementType,
        string labelKeyword,
        CancellationToken ct)
    {
        string[] models = new[] { _config.PrimaryModel, _config.FallbackModel }
            .Where(m => !string.IsNullOrWhiteSpace(m)).Distinct().ToArray();
        if (models.Length == 0) models = new[] { "qwen3-vl-plus" };

        Exception? lastEx = null;
        foreach (string model in models)
        {
            try
            {
                VisionApiResponse resp = await CallWithRetryAsync(model, imgB64, prompt, ct).ConfigureAwait(false);
                List<VisionElement> elements = BboxJsonParser.Parse(resp.Content);
                if (elements.Count == 0)
                {
                    Logger.Warning("后端日志：QwenVisionLocator model={Model} 返回 0 元素", model);
                    // 主模型空数组时换 fallback；fallback 也空 → 返回 null（上层降级 OCR）
                    lastEx = null;
                    continue;
                }

                VisionElement? hit = MatchElement(elements, elementType, labelKeyword, imgW, imgH);
                if (hit is null)
                {
                    Logger.Warning(
                        "后端日志：QwenVisionLocator 模型返回 {Count} 元素但无匹配 type={Type} kw={Kw}",
                        elements.Count, elementType, labelKeyword);
                    // 临时 debug：dump 前 10 个元素的 role/type/label/bbox，帮助定位匹配失败原因
                    foreach (var el in elements.Take(10))
                    {
                        double cx = el.Bbox is null ? -1 : (el.Bbox.X1 + el.Bbox.X2) / 2.0;
                        double cy = el.Bbox is null ? -1 : (el.Bbox.Y1 + el.Bbox.Y2) / 2.0;
                        double relY = (imgH > 0 && el.Bbox is not null) ? cy / imgH : -1;
                        Logger.Warning(
                            "后端日志：[DEBUG] el role={Role} type={Type} label={Label} relY={RelY:F2} bbox={Bbox}",
                            el.Role ?? "(null)", el.Type ?? "(null)", el.Label ?? "(null)", relY, el.Bbox);
                    }
                    return null;
                }

                return new VisionProbeResult
                {
                    Bbox = hit.Bbox!,
                    Confidence = hit.Confidence,
                    Source = "api",
                    ElementType = elementType,
                    LabelKeyword = labelKeyword,
                    ModelUsed = resp.Model
                };
            }
            catch (VisionApiTransientException ex)
            {
                lastEx = ex;
                Logger.Warning(ex, "后端日志：QwenVisionLocator model={Model} 暂时性失败，切下一个模型", model);
                continue;
            }
            catch (BboxOutOfBoundsException) { throw; }
            catch (VisionApiAuthException) { throw; }
        }

        if (lastEx is not null)
        {
            // 暂时性异常吞下返回 null（上层走 OCR 降级），更上层的 SendMessageService 会转 Success=false
            Logger.Warning("后端日志：QwenVisionLocator 所有模型失败，返回 null 让上层降级 OCR");
            return null;
        }
        return null;
    }

    private async Task<VisionApiResponse> CallWithRetryAsync(
        string model, string imgB64, string prompt, CancellationToken ct)
    {
        int maxRetries = _config.MaxRetries > 0 ? _config.MaxRetries : 3;
        Exception? lastTransient = null;

        for (int attempt = 0; attempt <= maxRetries; attempt++)
        {
            try
            {
                return await _visionApi.CallAsync(model, imgB64, prompt, temperature: 0.1, maxTokens: 4096, ct)
                    .ConfigureAwait(false);
            }
            catch (VisionApiTransientException ex)
            {
                lastTransient = ex;
                if (attempt >= maxRetries) break;
                // 指数退避：500ms / 1000ms / 2000ms ...
                int delayMs = (int)Math.Min(8000, 250 * (1 << attempt));
                Logger.Warning(
                    "后端日志：QwenVisionLocator model={Model} 第 {Attempt} 次失败，{DelayMs}ms 后重试：{Msg}",
                    model, attempt + 1, delayMs, ex.Message);
                try { await Task.Delay(delayMs, ct).ConfigureAwait(false); }
                catch (OperationCanceledException) { throw; }
            }
        }
        throw lastTransient ?? new VisionApiTransientException("CallWithRetryAsync 无异常但未返回");
    }

    /// <summary>
    /// 按 (elementType, labelKeyword) 匹配元素。
    /// 方案 B（阶段 3B.1）：不依赖 label 文本，改由模型返回的结构化 role 字段精确匹配。
    ///   1. 先把 (elementType, labelKeyword) 映射到 expected_role（如 "input/搜索" → "search_box"）；
    ///   2. 元素必须有 role 字段且等于 expected_role；老 prompt 没有 role → 直接 fail（不崩）；
    ///   3. conversation_item 角色额外用 conversation_name / label 包含 labelKeyword 过滤（labelKeyword="*" 表示取第一个）；
    ///   4. 多个命中取置信度最高，并列取第一个（Qwen 默认从上到下输出）。
    /// </summary>
    internal static VisionElement? MatchElement(IReadOnlyList<VisionElement> elements, string elementType, string labelKeyword, int imageWidth = 0, int imageHeight = 0)
    {
        string expectedRole = ResolveExpectedRole(elementType, labelKeyword);
        bool conversationWildcard = expectedRole == "conversation_item" && labelKeyword == "*";

        // ===== Pass 1：严格 role 字段匹配（首选，最准确） =====
        VisionElement? best = null;
        foreach (var el in elements)
        {
            if (el.Bbox is null) continue;

            // 没有 role 字段的老 prompt / 模型未按新格式响应：在 Pass 1 跳过，留给 Pass 2 兜底
            if (string.IsNullOrWhiteSpace(el.Role))
                continue;

            if (!string.Equals(el.Role, expectedRole, StringComparison.OrdinalIgnoreCase))
                continue;

            // conversation_item 且非通配：必须 conversation_name 或 label 包含关键字
            if (expectedRole == "conversation_item" && !conversationWildcard)
            {
                string name = el.ConversationName ?? "";
                string label = el.Label ?? "";
                bool nameHit = !string.IsNullOrWhiteSpace(name) &&
                    name.IndexOf(labelKeyword, StringComparison.OrdinalIgnoreCase) >= 0;
                bool labelHit = !string.IsNullOrWhiteSpace(label) &&
                    label.IndexOf(labelKeyword, StringComparison.OrdinalIgnoreCase) >= 0;
                if (!nameHit && !labelHit) continue;
            }

            if (best is null || el.Confidence > best.Confidence)
            {
                best = el;
            }
        }
        if (best is not null) return best;

        // ===== Pass 2：几何特征 + type 兜底（模型未按新 prompt 响应 role 时用） =====
        // 设计 §0.3 三层降级最后一道：完全用元素 type + bbox 在图像中的相对位置判定语义角色
        // - search_box：type 含 "input" 且 bbox 中心 Y < 图像高度的 15%（顶部）
        // - message_input：type 含 "input" 且 bbox 中心 Y > 图像高度的 65%（底部）
        // - send_button：type 含 "button" 且 bbox 中心 Y > 图像高度的 65%（底部，紧邻消息输入框）
        // - nav_icon：type 含 "icon" 且 bbox 中心 X < 图像宽度的 10%（左侧导航列）
        // - conversation_item：type 含 "list" 且 bbox 在图像中部偏左
        // 几何兜底仅在有图像尺寸信息时启用
        if (imageWidth <= 0 || imageHeight <= 0) return null;

        VisionElement? fallback = null;
        double fallbackBestScore = -1;
        foreach (var el in elements)
        {
            if (el.Bbox is null) continue;

            // 仅用未填 role 的元素做兜底（避免与 Pass 1 冲突）
            if (!string.IsNullOrWhiteSpace(el.Role)) continue;

            string typeStr = (el.Type ?? "").Trim();
            double cx = (el.Bbox.X1 + el.Bbox.X2) / 2.0;
            double cy = (el.Bbox.Y1 + el.Bbox.Y2) / 2.0;
            double relX = cx / imageWidth;  // 0..1
            double relY = cy / imageHeight; // 0..1

            double score = -1;
            if (expectedRole == "search_box" && typeStr.IndexOf("input", StringComparison.OrdinalIgnoreCase) >= 0 && relY < 0.15)
            {
                score = 1.0 - relY; // 越靠上分数越高
            }
            else if (expectedRole == "message_input" && relY > 0.65 &&
                     (typeStr.IndexOf("input", StringComparison.OrdinalIgnoreCase) >= 0 ||
                      typeStr.IndexOf("text", StringComparison.OrdinalIgnoreCase) >= 0 ||
                      typeStr.IndexOf("area", StringComparison.OrdinalIgnoreCase) >= 0 ||
                      typeStr.IndexOf("edit", StringComparison.OrdinalIgnoreCase) >= 0))
            {
                score = relY; // 越靠下分数越高
            }
            else if (expectedRole == "send_button" && typeStr.IndexOf("button", StringComparison.OrdinalIgnoreCase) >= 0 && relY > 0.65)
            {
                score = relY;
            }
            else if (expectedRole == "nav_icon" && (typeStr.IndexOf("icon", StringComparison.OrdinalIgnoreCase) >= 0 || typeStr.IndexOf("image", StringComparison.OrdinalIgnoreCase) >= 0) && relX < 0.10)
            {
                score = 1.0 - relX;
            }
            else if (expectedRole == "conversation_item" && (typeStr.IndexOf("list", StringComparison.OrdinalIgnoreCase) >= 0 || typeStr.IndexOf("listitem", StringComparison.OrdinalIgnoreCase) >= 0))
            {
                // 会话项：左中部
                if (conversationWildcard)
                {
                    score = 1.0 - Math.Abs(relY - 0.3); // 接近 30% 高度
                }
                else
                {
                    // 命中关键字
                    string label = el.Label ?? "";
                    if (!string.IsNullOrWhiteSpace(label) &&
                        label.IndexOf(labelKeyword, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        score = 1.0;
                    }
                }
            }

            if (score > fallbackBestScore)
            {
                fallbackBestScore = score;
                fallback = el;
            }
        }
        return fallback;
    }

    /// <summary>
    /// 把 (elementType, labelKeyword) 映射到 grounding prompt 的语义 role 枚举。
    /// 这样匹配完全基于结构化字段，不依赖 label 是中文还是英文。
    /// </summary>
    internal static string ResolveExpectedRole(string elementType, string labelKeyword)
    {
        if (string.IsNullOrWhiteSpace(elementType)) return "other";
        string et = elementType.Trim();
        string kw = labelKeyword ?? "";

        // 搜索框
        if (et.Equals("input", StringComparison.OrdinalIgnoreCase) &&
            (kw.Contains("搜索") || kw.Contains("查找") ||
             kw.Equals("search", StringComparison.OrdinalIgnoreCase) ||
             kw.Contains("search", StringComparison.OrdinalIgnoreCase)))
            return "search_box";

        // 消息输入框
        if (et.Equals("input", StringComparison.OrdinalIgnoreCase) &&
            (kw.Contains("消息") || kw.Contains("输入") || kw.Contains("聊天") ||
             kw.Contains("message", StringComparison.OrdinalIgnoreCase) ||
             kw.Contains("input", StringComparison.OrdinalIgnoreCase) ||
             kw.Contains("text", StringComparison.OrdinalIgnoreCase)))
            return "message_input";

        // 兜底：input 但没匹配到上面两组 → message_input（企微中 input 只有这两类，message 更常用）
        if (et.Equals("input", StringComparison.OrdinalIgnoreCase))
            return "message_input";

        // 发送按钮
        if (et.Equals("button", StringComparison.OrdinalIgnoreCase) &&
            (kw.Contains("发送") || kw.Contains("发") ||
             kw.Equals("send", StringComparison.OrdinalIgnoreCase) ||
             kw.Contains("send", StringComparison.OrdinalIgnoreCase)))
            return "send_button";

        // 兜底：button → 当成 send_button（业务上 button 类型目前只用于 send）
        if (et.Equals("button", StringComparison.OrdinalIgnoreCase))
            return "send_button";

        // 导航 / 侧边栏图标
        if (et.Equals("icon", StringComparison.OrdinalIgnoreCase) &&
            (kw.Contains("nav") || kw.Contains("导航") || kw.Contains("侧边") ||
             kw.Contains("menu", StringComparison.OrdinalIgnoreCase)))
            return "nav_icon";

        // 兜底：icon → nav_icon
        if (et.Equals("icon", StringComparison.OrdinalIgnoreCase))
            return "nav_icon";

        // 会话列表项（labelKeyword 可能是具体会话名或 "*"）
        if (et.Equals("list_item", StringComparison.OrdinalIgnoreCase))
            return "conversation_item";

        return "other";
    }

    /// <summary>
    /// 构建 grounding prompt（方案 B 版本，阶段 3B.1）。
    /// 改造点：要求模型返回结构化 role 字段（语义角色枚举）+ 可选 conversation_name，
    /// 不再依赖 label 文本做关键字匹配（真机实测模型 label 多为英文，中文关键词匹配失败）。
    /// </summary>
    internal static string BuildGroundingPrompt(int width, int height)
    {
        return
$"Look at this screenshot carefully. Image dimensions are {width}x{height} pixels.\n\n" +
"Identify all visible UI elements and return ONLY a JSON array. Each element must have:\n" +
"- \"role\": one of \"search_box\" | \"message_input\" | \"send_button\" | \"conversation_item\" | \"nav_icon\" | \"other\"\n" +
"- \"type\": also include the original semantic type (input/button/textarea/list_item/icon/text/etc.)\n" +
"- \"bbox\": [x1, y1, x2, y2] pixel coordinates relative to top-left of the image\n" +
"- \"label\": short human-readable description (any language)\n" +
"- \"conversation_name\": ONLY for conversation_item role, the conversation name shown; omit for other roles\n\n" +
"Element definitions:\n" +
"- search_box: the input box at the top for searching contacts/messages\n" +
"- message_input: the bottom input box (textarea or edit) where you type a new message\n" +
"- send_button: the button to send the typed message (icon or text label, e.g. 发送(S))\n" +
"- conversation_item: each row in the middle conversation list (set conversation_name to the row's name)\n" +
"- nav_icon: each icon in the far-left navigation column\n" +
"- other: any other notable element\n\n" +
"Focus especially on:\n" +
"- The bottom-right message input area (textarea where you type a new message)\n" +
"- The send button right next to or below the message input area\n" +
"If the bottom-right area has a textarea/edit for typing, classify it as message_input.\n" +
"If there is any button labeled 发送/Send near the message input, classify it as send_button.\n\n" +
"Format (output strictly this JSON, no markdown, no extra text):\n" +
"[{\"role\": \"search_box\", \"type\": \"input\", \"bbox\": [x1,y1,x2,y2], \"label\": \"...\"},\n" +
" {\"role\": \"message_input\", \"type\": \"textarea\", \"bbox\": [x1,y1,x2,y2], \"label\": \"...\"},\n" +
" {\"role\": \"send_button\", \"type\": \"button\", \"bbox\": [x1,y1,x2,y2], \"label\": \"...\"},\n" +
" {\"role\": \"conversation_item\", \"type\": \"list_item\", \"bbox\": [x1,y1,x2,y2], \"label\": \"...\", \"conversation_name\": \"张三\"},\n" +
" ...]\n\n" +
"bbox must be pixel coordinates. If you cannot find an element, omit it. Do not make up elements.";
    }
}

/// <summary>
/// 解析后的元素中间结构。
/// 方案 B（阶段 3B.1）：新增 Role（语义角色枚举）+ ConversationName（仅 conversation_item 用）。
/// 老格式的 Type / Label 字段保留，便于 prompt 兼容降级（MatchElement 不再依赖它们）。
/// </summary>
internal sealed class VisionElement
{
    public string Label { get; set; } = "";
    public string Type { get; set; } = "";
    /// <summary>语义角色（方案 B 新增）。模型按 prompt 返回 search_box/message_input/send_button/conversation_item/nav_icon/other。</summary>
    public string Role { get; set; } = "";
    /// <summary>会话名（仅 conversation_item 角色会填）。用于按名称定位具体会话。</summary>
    public string ConversationName { get; set; } = "";
    public BoundingBox? Bbox { get; set; }
    public double Confidence { get; set; } = 0.5;
}

/// <summary>
/// 鲁棒 JSON bbox 解析器（复刻 probe-qwen-vl-full.py parse_bbox_json 91-116 行）。
/// </summary>
internal static class BboxJsonParser
{
    private static readonly Regex SingleObjectRegex = new(@"\{[^{}]*\}", RegexOptions.Compiled | RegexOptions.Singleline);

    /// <summary>
    /// 从模型返回的原始 content 中抽取所有元素。
    /// 鲁棒处理：剥 markdown fence → 整体 JSON → 抓所有 {..} 单独解析。
    /// </summary>
    public static List<VisionElement> Parse(string content)
    {
        var result = new List<VisionElement>();
        if (string.IsNullOrWhiteSpace(content)) return result;

        string s = QwenVisionApi.StripMarkdownFence(content);

        // 尝试整体解析
        List<VisionElement>?整体 = TryParseAsJson(s);
        if (整体 is not null) return 整体;

        // 抓所有 {...}
        foreach (Match m in SingleObjectRegex.Matches(content))
        {
            VisionElement? el = TryParseSingleObject(m.Value);
            if (el is not null && el.Bbox is not null) result.Add(el);
        }
        return result;
    }

    private static List<VisionElement>? TryParseAsJson(string s)
    {
        try
        {
            using var doc = JsonDocument.Parse(s);
            JsonElement root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Array)
            {
                return ExtractFromArray(root);
            }
            if (root.ValueKind == JsonValueKind.Object)
            {
                if (root.TryGetProperty("elements", out var els) && els.ValueKind == JsonValueKind.Array)
                    return ExtractFromArray(els);
                if (root.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
                    return ExtractFromArray(items);
                // 单个对象
                VisionElement? single = TryParseSingleElement(root);
                if (single is not null && single.Bbox is not null)
                    return new List<VisionElement> { single };
            }
        }
        catch (JsonException)
        {
            // 整体解析失败 → 返回 null 让外层走正则
        }
        return null;
    }

    private static List<VisionElement> ExtractFromArray(JsonElement array)
    {
        var list = new List<VisionElement>();
        foreach (JsonElement item in array.EnumerateArray())
        {
            VisionElement? el = TryParseSingleElement(item);
            if (el is not null && el.Bbox is not null) list.Add(el);
        }
        return list;
    }

    private static VisionElement? TryParseSingleObject(string jsonText)
    {
        try
        {
            using var doc = JsonDocument.Parse(jsonText);
            return TryParseSingleElement(doc.RootElement);
        }
        catch (JsonException) { return null; }
    }

    private static VisionElement? TryParseSingleElement(JsonElement obj)
    {
        if (obj.ValueKind != JsonValueKind.Object) return null;

        var el = new VisionElement();

        if (obj.TryGetProperty("label", out var lblEl) && lblEl.ValueKind == JsonValueKind.String)
            el.Label = lblEl.GetString() ?? "";
        if (obj.TryGetProperty("type", out var typeEl))
        {
            el.Type = typeEl.ValueKind == JsonValueKind.String
                ? (typeEl.GetString() ?? "")
                : typeEl.ToString();
        }
        // 方案 B 新增：role（语义角色枚举）+ conversation_name（仅 conversation_item 用）
        if (obj.TryGetProperty("role", out var roleEl))
        {
            el.Role = roleEl.ValueKind == JsonValueKind.String
                ? (roleEl.GetString() ?? "")
                : roleEl.ToString();
        }
        if (obj.TryGetProperty("conversation_name", out var convEl) &&
            convEl.ValueKind == JsonValueKind.String)
        {
            el.ConversationName = convEl.GetString() ?? "";
        }
        if (obj.TryGetProperty("confidence", out var confEl) &&
            confEl.TryGetDouble(out double c))
        {
            el.Confidence = c;
        }

        // bbox 可能叫 bbox / box / rect，值是 4 元素数组
        int[]? coords = null;
        foreach (string key in new[] { "bbox", "box", "rect", "rectangle", "bounding_box" })
        {
            if (obj.TryGetProperty(key, out var bbEl) && bbEl.ValueKind == JsonValueKind.Array)
            {
                coords = TryExtract4Ints(bbEl);
                if (coords is not null) break;
            }
        }
        if (coords is null || coords.Length != 4) return null;

        // 标准化：保证 x1<=x2, y1<=y2
        int x1 = coords[0], y1 = coords[1], x2 = coords[2], y2 = coords[3];
        if (x1 > x2) (x1, x2) = (x2, x1);
        if (y1 > y2) (y1, y2) = (y2, y1);
        el.Bbox = new BoundingBox(x1, y1, x2, y2);
        return el;
    }

    private static int[]? TryExtract4Ints(JsonElement array)
    {
        if (array.GetArrayLength() < 4) return null;
        var arr = new int[4];
        int i = 0;
        foreach (JsonElement v in array.EnumerateArray())
        {
            if (i >= 4) break;
            double d;
            if (v.ValueKind == JsonValueKind.Number)
            {
                if (!v.TryGetDouble(out d)) return null;
            }
            else if (v.ValueKind == JsonValueKind.String)
            {
                if (!double.TryParse(v.GetString(), out d)) return null;
            }
            else return null;
            arr[i++] = (int)Math.Round(d);
        }
        return i == 4 ? arr : null;
    }
}

/// <summary>
/// 图片编码工具（复刻 probe-qwen-vl-full.py encode_jpeg_b64 49-56 行）。
/// PNG → RGB JPEG quality=92 → base64。
/// </summary>
internal static class ImageEncoder
{
    /// <summary>
    /// 把 Bitmap 编码为 RGB JPEG base64（不含 data: 前缀），quality=92。
    /// </summary>
    public static string EncodeJpegBase64(Bitmap source)
    {
        using var rgb = EnsureRgb24(source);
        using var ms = new MemoryStream();
        var jpegEncoder = ImageCodecInfo.GetImageEncoders()
            .FirstOrDefault(c => string.Equals(c.MimeType, "image/jpeg", StringComparison.OrdinalIgnoreCase))
            ?? throw new InvalidOperationException("系统未找到 JPEG 编码器");
        using var encoderParams = new EncoderParameters(1);
        encoderParams.Param[0] = new EncoderParameter(Encoder.Quality, 92L);
        rgb.Save(ms, jpegEncoder, encoderParams);
        return Convert.ToBase64String(ms.ToArray());
    }

    private static Bitmap EnsureRgb24(Bitmap source)
    {
        if (source.PixelFormat == PixelFormat.Format24bppRgb) return new Bitmap(source);
        var converted = new Bitmap(source.Width, source.Height, PixelFormat.Format24bppRgb);
        try
        {
            using (var g = Graphics.FromImage(converted))
            {
                g.DrawImage(source, 0, 0, source.Width, source.Height);
            }
            return converted;
        }
        catch
        {
            converted.Dispose();
            throw;
        }
    }
}
