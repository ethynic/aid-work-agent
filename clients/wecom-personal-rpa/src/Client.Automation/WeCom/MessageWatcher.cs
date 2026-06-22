using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Automation.WeCom;

/// <summary>
/// 入站消息观察器：两条触发源
/// 1. 企微通知（系统级 / 内嵌 toast）：第一时间捕获新消息；
/// 2. 会话列表轮询（保底）：定期扫描未读会话，抓取最新一条。
/// 产出 <see cref="InboundEvent"/>（event_type=message），由上层 IAgentApiClient 上报。
/// 用快照去重避免同一事件重复上报（event_id 稳定）。
/// </summary>
public sealed class MessageWatcher : IDisposable
{
    private readonly FlaUi.FlaUiDriver _flaUi;
    private readonly INodesConfig _nodes;
    private readonly string _accountId;
    private readonly HashSet<string> _seenConversationSnapshots = new();
    private readonly object _lock = new();
    private bool _disposed;

    public MessageWatcher(FlaUi.FlaUiDriver flaUi, INodesConfig nodes, string accountId)
    {
        _flaUi = flaUi ?? throw new ArgumentNullException(nameof(flaUi));
        _nodes = nodes ?? throw new ArgumentNullException(nameof(nodes));
        _accountId = string.IsNullOrWhiteSpace(accountId) ? "unknown_account" : accountId;
    }

    /// <summary>通知触发：企微弹出新消息通知时由调用方调用，立即扫描会话列表。</summary>
    public IReadOnlyList<InboundEvent> OnTriggered()
    {
        return ScanConversations();
    }

    /// <summary>会话列表轮询：周期扫描未读会话，返回新增事件（已去重）。</summary>
    public IReadOnlyList<InboundEvent> PollOnce()
    {
        return ScanConversations();
    }

    private List<InboundEvent> ScanConversations()
    {
        var events = new List<InboundEvent>();

        try
        {
            if (!_flaUi.IsAttached && !_flaUi.AttachMainWindow())
            {
                return events;
            }

            var mainWindow = _flaUi.MainWindow;
            if (mainWindow is null)
            {
                return events;
            }

            // 简化策略：枚举会话列表（TreeView / List），取每项显示名 + 未读小红点的最新预览。
            // 真实实现需按企微 UI 结构回填（AutomationId / 子树）。
            var items = mainWindow.FindAllDescendants(
                mainWindow.Automation.ConditionFactory.ByControlType(FlaUI.Core.Definitions.ControlType.TreeItem));

            foreach (var item in items)
            {
                if (!item.IsAvailable)
                {
                    continue;
                }

                string displayName = _flaUi.GetText(item) ?? string.Empty;
                if (string.IsNullOrWhiteSpace(displayName))
                {
                    continue;
                }

                // 去重键：账号 + 显示名 + 一段稳定时间窗（分钟级），避免短时间内重复上报。
                string snapshot = $"{_accountId}:{displayName}";

                lock (_lock)
                {
                    if (_seenConversationSnapshots.Contains(snapshot))
                    {
                        continue;
                    }
                    _seenConversationSnapshots.Add(snapshot);
                }

                events.Add(BuildMessageEvent(displayName));
            }
        }
        catch (AutomationLayerException ex)
        {
            Log.Debug(ex, "MessageWatcher：扫描会话列表异常，Layer={Layer}", ex.Layer);
        }

        return events;
    }

    private InboundEvent BuildMessageEvent(string displayName)
    {
        // 首版消息内容只能拿到会话显示名 + 触达时间，真实文本 / 附件需要点击进入会话读取。
        // 上层据此先建立会话路由（避免业务阻塞）；完整内容后续由专门抓取流程补。
        return new InboundEvent
        {
            EventId = $"evt_{_accountId}_{DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()}",
            ClientId = string.Empty, // 由上层 IAgentApiClient 注入
            AccountId = _accountId,
            EventType = EventType.Message,
            OccurredAt = DateTimeOffset.Now,
            Payload = System.Text.Json.JsonSerializer.SerializeToElement(new MessagePayload
            {
                ConversationId = displayName, // 首版以显示名作临时会话 ID
                ConversationType = ConversationType.ExternalUser, // 默认外部单聊，后续按搜索结果纠正
                SenderDisplayName = displayName,
                SenderStableId = null,
                MessageType = InboundMessageType.Text,
                Text = null, // 文本需点击会话后抓取（占位）
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
