using System.Text.Json;
using Serilog;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.Supervisor;

/// <summary>
/// 客户端离线上报。
/// 当 <see cref="SupervisorService"/> 连续拉起 Client.App 失败次数超过阈值后触发：
/// 构造一条 <c>event_type=status, status=offline</c> 的 <see cref="InboundEvent"/>（这里 InboundEvent
/// 仅作为客户端到服务端的通用 callback 信封复用，与已删除的入站消息路径无关），
/// 经 <see cref="IAgentApiClient.PostCallbackAsync"/> 上报（内部复用 <see cref="WeCom.PersonalRpa.Core.Security.RequestSigner"/>，
/// 按 protocol.md §A.1 签名），服务端据此将该客户端标记为不可达，停止下发 actions。
/// </summary>
/// <remarks>
/// 配置只读：<see cref="SupervisorOptions"/> 与 <see cref="ClientOptions"/> 在启动时冻结；
/// 本类不持有任何跨调用可变业务状态（仅节流时间戳），单实例 Windows Service 下无多 worker 漂移问题。
/// </remarks>
internal sealed class OfflineReporter
{
    private readonly IAgentApiClient _agentApiClient;
    private readonly ClientOptions _clientOptions;
    private readonly SupervisorOptions _supervisorOptions;
    private readonly Serilog.ILogger _logger;

    // 上报去重：避免在同一离线窗口内重复上报（类比 protocol.md §A.9 幂等键思路，仅本地内存）。
    private DateTimeOffset _lastReportAt = DateTimeOffset.MinValue;
    private static readonly TimeSpan MinReportInterval = TimeSpan.FromMinutes(5);

    public OfflineReporter(
        IAgentApiClient agentApiClient,
        ClientOptions clientOptions,
        SupervisorOptions supervisorOptions,
        Serilog.ILogger logger)
    {
        _agentApiClient = agentApiClient ?? throw new ArgumentNullException(nameof(agentApiClient));
        _clientOptions = clientOptions ?? throw new ArgumentNullException(nameof(clientOptions));
        _supervisorOptions = supervisorOptions ?? throw new ArgumentNullException(nameof(supervisorOptions));
        _logger = logger.ForContext<OfflineReporter>();
    }

    /// <summary>
    /// 上报客户端离线。失败不抛异常（Supervisor 自身不能因网络问题崩溃）。
    /// </summary>
    /// <param name="reason">脱敏后的离线原因（不含密钥/绝对路径）。</param>
    /// <param name="consecutiveFailures">触发本次上报时的连续失败次数。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    public async Task ReportAsync(string reason, int consecutiveFailures, CancellationToken cancellationToken)
    {
        if (!_supervisorOptions.OfflineReportEnabled)
        {
            _logger.Debug("后端日志：离线上报已禁用（OfflineReportEnabled=false），跳过。failures={Failures}",
                consecutiveFailures);
            return;
        }

        if (string.IsNullOrEmpty(_clientOptions.ClientId))
        {
            // 未配置 ClientId：无法上报（服务端按 client_id 归属），降级为本地日志。
            _logger.Warning(
                "后端日志：ClientOptions.ClientId 为空，离线事件无法上报，仅本地记录。failures={Failures}",
                consecutiveFailures);
            return;
        }

        var now = DateTimeOffset.UtcNow;
        if (now - _lastReportAt < MinReportInterval)
        {
            _logger.Warning(
                "后端日志：距上次离线上报不足 {Min}s，节流跳过。failures={Failures}",
                MinReportInterval.TotalSeconds, consecutiveFailures);
            return;
        }

        // 构造 status=offline 的入站事件（对应 Python RpaStatusPayload.status="offline"）。
        // account_id：Supervisor 不感知具体账号，用 ClientId 作为占位归属，服务端按 client_id 聚合。
        var payload = new StatusPayload
        {
            Status = AccountStatus.Offline,
            AccountDisplayName = null,
            Detail = reason,
            QrImageRef = null,
        };

        var envelope = new InboundEvent
        {
            EventId = $"supervisor_offline_{now:yyyyMMddHHmmss}_{Guid.NewGuid():N}",
            ClientId = _clientOptions.ClientId,
            AccountId = _clientOptions.ClientId, // 占位：Supervisor 层级不区分账号
            EventType = EventType.Status,
            OccurredAt = now,
            Payload = JsonSerializer.SerializeToElement(payload),
        };

        try
        {
            var ok = await _agentApiClient.PostCallbackAsync(envelope, cancellationToken).ConfigureAwait(false);
            _lastReportAt = now;
            _logger.Error(
                "后端日志：客户端离线事件已上报服务端。clientId={ClientId}, ok={Ok}, reason={Reason}, failures={Failures}",
                envelope.ClientId, ok, reason, consecutiveFailures);
        }
        catch (Exception ex)
        {
            // 上报失败：不重置 _lastReportAt，让节流窗口内继续尝试。
            // 网络/服务端故障期间，Supervisor 仍能维持拉起重试逻辑，不被上报通道拖垮。
            _logger.Error(ex,
                "后端日志：离线上报失败（不阻塞拉起循环）。clientId={ClientId}, failures={Failures}",
                envelope.ClientId, consecutiveFailures);
        }
    }
}
