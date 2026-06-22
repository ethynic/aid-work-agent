using System.Text.Json;
using Serilog;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 入站事件上报门面。封装构造 <see cref="InboundEvent"/> 并经
/// <see cref="IAgentApiClient.PostCallbackAsync"/> 上报（请求头 HMAC 签名由 Core 的 RequestSigner 注入）。
///
/// 端点：POST /t/{tenant_id}/wecom_personal_rpa/callback/{config_id}（protocol §A.1/§A.2）
/// 幂等：event_id 全局稳定，重传相同值（protocol §A.9）。
/// </summary>
public sealed class InboundReporter
{
    private const string Tag = "InboundReporter";
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    private readonly IAgentApiClient _api;
    private readonly ClientOptions _options;

    public InboundReporter(IAgentApiClient api, ClientOptions options)
    {
        _api = api;
        _options = options;
    }

    /// <summary>上报入站消息事件（event_type=message）。</summary>
    public async Task ReportMessageAsync(MessagePayload payload, string accountId, CancellationToken ct = default)
    {
        var evt = NewEnvelope(accountId, EventType.Message);
        evt.Payload = JsonSerializer.SerializeToElement(payload, JsonOpts);
        Log.Information(
            "[{Tag}] 上报 message EventId={Eid} Conv={Conv} Sender={Sender} Text={Text}",
            Tag, evt.EventId, payload.ConversationId, payload.SenderDisplayName,
            Truncate(payload.Text, 40));
        await PostSafeAsync(evt, ct);
    }

    /// <summary>上报账号/桌面状态（event_type=status）。</summary>
    public async Task ReportStatusAsync(StatusPayload payload, string accountId, CancellationToken ct = default)
    {
        var evt = NewEnvelope(accountId, EventType.Status);
        evt.Payload = JsonSerializer.SerializeToElement(payload, JsonOpts);
        // 注意：qr_image_ref 不得入日志（protocol §A.4）
        Log.Information("[{Tag}] 上报 status Status={Status} Account={Acc} Detail={Detail}",
            Tag, payload.Status, payload.AccountDisplayName ?? "-", payload.Detail ?? "-");
        await PostSafeAsync(evt, ct);
    }

    /// <summary>上报 action 回执（event_type=action_result）。</summary>
    public async Task ReportActionResultAsync(ActionResultPayload payload, string accountId, CancellationToken ct = default)
    {
        var evt = NewEnvelope(accountId, EventType.ActionResult);
        evt.Payload = JsonSerializer.SerializeToElement(payload, JsonOpts);
        Log.Information(
            "[{Tag}] 上报 action_result RequestId={Req} Index={Idx} Success={Ok} Err={Err}",
            Tag, payload.RequestId, payload.ActionIndex, payload.Success, payload.ErrorCode ?? "-");
        await PostSafeAsync(evt, ct);
    }

    /// <summary>构造顶层信封（event_id 全局稳定）。</summary>
    private InboundEvent NewEnvelope(string accountId, EventType type)
    {
        var prefix = type switch
        {
            EventType.Message => "evt_msg",
            EventType.Status => "evt_status",
            EventType.ActionResult => "evt_res",
            _ => "evt",
        };
        return new InboundEvent
        {
            EventId = $"{prefix}_{DateTimeOffset.UtcNow.ToUnixTimeSeconds()}_{Guid.NewGuid():N}",
            ClientId = _options.ClientId,
            AccountId = accountId,
            EventType = type,
            OccurredAt = DateTimeOffset.Now,
        };
    }

    /// <summary>安全上报：失败仅记录日志，不向上抛（避免拖垮编排循环）。</summary>
    private async Task PostSafeAsync(InboundEvent evt, CancellationToken ct)
    {
        try
        {
            await _api.PostCallbackAsync(evt, ct);
        }
        catch (Exception ex)
        {
            // 安全原则：错误信息脱敏（剔除绝对路径/密钥），但保留 stack 类型便于排查
            Log.Warning(ex, "[{Tag}] 上报失败 EventId={Eid} Type={Type}", Tag, evt.EventId, evt.EventType);
        }
    }

    private static string? Truncate(string? s, int n) =>
        string.IsNullOrEmpty(s) ? s : (s.Length <= n ? s : s[..n] + "…");
}
