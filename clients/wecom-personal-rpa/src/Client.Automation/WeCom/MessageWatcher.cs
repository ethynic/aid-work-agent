using System.Runtime.InteropServices.WindowsRuntime;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Automation.Vision;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 入站消息观察器（阶段 2B 视觉路径重构）。
/// 两条触发源：
/// 1. 企微通知（系统级 / 内嵌 toast）：第一时间捕获新消息；
/// 2. 会话列表轮询（保底）：定期扫描未读会话，抓取最新一条。
///
/// 文本抓取策略（design §5.1）：注入 IScreenCapturer，截一张企微主窗口图，
/// 跑 Windows.Media.Ocr，遍历 Lines 找候选消息文本（启发式：取靠下方的非空白行）。
/// OCR 失败或返回空时 Text=null 不报错（只 log warning）。
/// 用快照去重避免同一事件重复上报（account_id + conversation_id + last_message_text_hash）。
/// </summary>
public sealed class MessageWatcher : IDisposable
{
    private readonly IScreenCapturer _capturer;
    private readonly string _accountId;
    private readonly HashSet<string> _seenConversationSnapshots = new();
    private readonly object _lock = new();
    private bool _disposed;

    public MessageWatcher(IScreenCapturer capturer, string accountId)
    {
        _capturer = capturer ?? throw new ArgumentNullException(nameof(capturer));
        _accountId = string.IsNullOrWhiteSpace(accountId) ? "unknown_account" : accountId;
    }

    /// <summary>通知触发：企微弹出新消息通知时由调用方调用，立即扫描会话列表。</summary>
    public IReadOnlyList<InboundEvent> OnTriggered() => ScanConversations();

    /// <summary>会话列表轮询：周期扫描未读会话，返回新增事件（已去重）。</summary>
    public IReadOnlyList<InboundEvent> PollOnce() => ScanConversations();

    private List<InboundEvent> ScanConversations()
    {
        var events = new List<InboundEvent>();

        try
        {
            // 截图 + OCR
            using var capture = _capturer.CaptureWeComMainWindowAsync().GetAwaiter().GetResult();
            if (capture.Bitmap is null)
            {
                return events;
            }

            string? lastText = ExtractLastMessageText(capture.Bitmap);
            // OCR 返回空：保留 Text=null 不报错（design §5.1）。
            if (string.IsNullOrWhiteSpace(lastText))
            {
                Log.Debug("后端日志：MessageWatcher OCR 未识别到任何文本");
                return events;
            }

            // 首版简化：以截图指纹（WindowRect + OCR 首字符）作临时会话标识。
            // 真实生产需要 ConversationNavigator 把当前会话信息回填到 watcher。
            string conversationId = $"w_{capture.WindowRect.Width}x{capture.WindowRect.Height}";
            string dedupKey = $"{_accountId}:{conversationId}:{lastText.GetHashCode():X}";

            lock (_lock)
            {
                if (_seenConversationSnapshots.Contains(dedupKey))
                {
                    return events;
                }
                _seenConversationSnapshots.Add(dedupKey);
            }

            events.Add(BuildMessageEvent(conversationId, lastText));
        }
        catch (Exception ex)
        {
            // OCR / 截图失败：保留 Text=null 不报错，记录 warning
            Log.Warning(ex, "后端日志：MessageWatcher 扫描异常");
        }

        return events;
    }

    /// <summary>
    /// 用 Windows.Media.Ocr 跑一遍截图，取最后一条非空白 Line 作为消息文本。
    /// OCR 引擎不可用时返回 null（不抛异常）。
    /// </summary>
    private string? ExtractLastMessageText(System.Drawing.Bitmap bmp)
    {
        try
        {
            var engine = Windows.Media.Ocr.OcrEngine.AvailableRecognizerLanguages
                .Where(l => l.LanguageTag.StartsWith("zh", StringComparison.OrdinalIgnoreCase))
                .Select(Windows.Media.Ocr.OcrEngine.TryCreateFromLanguage)
                .FirstOrDefault(e => e is not null);
            if (engine is null)
            {
                Log.Warning("后端日志：MessageWatcher 系统未安装中文 OCR 语言包");
                return null;
            }

            // Bitmap → SoftwareBitmap（Bgra8 for OCR）
            using var pngMs = new MemoryStream();
            bmp.Save(pngMs, System.Drawing.Imaging.ImageFormat.Png);
            pngMs.Position = 0;

            using var stream = new Windows.Storage.Streams.InMemoryRandomAccessStream();
            stream.WriteAsync(pngMs.ToArray().AsBuffer()).AsTask().GetAwaiter().GetResult();
            stream.Seek(0);

            var decoder = Windows.Graphics.Imaging.BitmapDecoder.CreateAsync(stream).AsTask().GetAwaiter().GetResult();
            using var softwareBmp = decoder.GetSoftwareBitmapAsync(
                Windows.Graphics.Imaging.BitmapPixelFormat.Bgra8,
                Windows.Graphics.Imaging.BitmapAlphaMode.Premultiplied).AsTask().GetAwaiter().GetResult();

            var ocrResult = engine.RecognizeAsync(softwareBmp).AsTask().GetAwaiter().GetResult();
            string? last = null;
            foreach (var line in ocrResult.Lines)
            {
                if (!string.IsNullOrWhiteSpace(line.Text))
                {
                    last = line.Text;
                }
            }
            return last;
        }
        catch (Exception ex)
        {
            Log.Warning(ex, "后端日志：MessageWatcher OCR 失败");
            return null;
        }
    }

    private InboundEvent BuildMessageEvent(string conversationId, string text)
    {
        return new InboundEvent
        {
            EventId = $"evt_{_accountId}_{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}",
            ClientId = string.Empty, // 由上层 IAgentApiClient 注入
            AccountId = _accountId,
            EventType = EventType.Message,
            OccurredAt = DateTimeOffset.Now,
            Payload = System.Text.Json.JsonSerializer.SerializeToElement(new MessagePayload
            {
                ConversationId = conversationId,
                ConversationType = ConversationType.ExternalUser,
                SenderDisplayName = string.Empty,
                SenderStableId = null,
                MessageType = InboundMessageType.Text,
                Text = text,
                Attachments = new(),
            }, MessagePayloadJsonOptions.Instance),
        };
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        lock (_lock)
        {
            _seenConversationSnapshots.Clear();
        }
    }
}

/// <summary>MessageWatcher 本地 JSON 选项（与 Core camelCase 对齐）。</summary>
internal static class MessagePayloadJsonOptions
{
    public static readonly System.Text.Json.JsonSerializerOptions Instance = new()
    {
        PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase,
    };
}
