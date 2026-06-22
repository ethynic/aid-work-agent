using System.Text.Json;
using Serilog;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Queue;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 服务端下发动作的接收/入队门面。
///
/// WebSocket 推送或离线轮询收到 <see cref="ActionEnvelope"/> 后：
///   1) 按 action 逐条写入 <see cref="ISendQueue"/>（dedup_key = wecom_rpa:{request_id}:{index}
///      保证幂等，protocol §A.9）
///   2) 触发 <see cref="SendMessageService"/> 串行消费
/// </summary>
public sealed class OutboundActionSource
{
    private const string Tag = "OutboundActionSource";
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly ISendQueue _queue;
    private readonly SendMessageService _sender;

    public OutboundActionSource(ISendQueue queue, SendMessageService sender)
    {
        _queue = queue;
        _sender = sender;
    }

    /// <summary>解析并入队一条 ActionEnvelope，随后异步触发消费。</summary>
    public async Task DispatchAsync(ActionEnvelope envelope, CancellationToken ct = default)
    {
        if (envelope is null || envelope.Actions.Count == 0)
        {
            Log.Information("[{Tag}] 空信封，跳过 Req={Req}", Tag, envelope?.RequestId ?? "-");
            return;
        }

        Log.Information("[{Tag}] 接收信封 Req={Req} Account={Acc} Actions={N}",
            Tag, envelope.RequestId, envelope.AccountId, envelope.Actions.Count);

        for (var i = 0; i < envelope.Actions.Count; i++)
        {
            var action = envelope.Actions[i];
            var actionJson = JsonSerializer.Serialize(action, JsonOpts);
            var dedupKey = $"wecom_rpa:{envelope.RequestId}:{i}";
            await _queue.EnqueueAsync(
                envelope.RequestId,
                envelope.AccountId,
                envelope.ConversationId,
                envelope.SessionId,
                i,
                actionJson,
                dedupKey,
                ct);
        }

        // 触发消费（fire-and-forget，消费循环自带账号串行闸门）
        _ = Task.Run(() => _sender.DrainAsync(envelope.AccountId, CancellationToken.None), ct);
    }
}
