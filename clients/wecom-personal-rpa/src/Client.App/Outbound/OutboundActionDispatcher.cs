using System.IO;
using System.Threading.Channels;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Outbound;

/// <summary>
/// 出站执行调度器（docs/system/wecom-personal-rpa-client-design.md §F3）。
///
/// 设计：
///   - 实现 IHostedService：启动时 ListPendingAsync 恢复未完成 action，启动单 Worker。
///   - 监听 IActionSource（WebSocket / 内存 stub）→ 入队 outbox → 串行消费。
///   - 账号级串行：单 Worker + Channel，确保同时只跑一个 PS 调用，避免剪贴板 / 输入焦点竞态。
///   - 错误码映射：wecom_navigation_failed → unsupported_action（对齐 protocol.md §A.8）。
///   - 重试：wecom_window_not_found 重试 MaxRetries 次；其他错误不重试。
///
/// 不在范围内：
///   - handoff 真暂停（Phase 4 基础设施负责，此处只 log + 回执 success）。
///   - 实际 WebSocket 接收（由 IActionSource 实现，本类不感知）。
/// </summary>
public sealed class OutboundActionDispatcher : IHostedService, IDisposable
{
    private readonly OutboundQueue _queue;
    private readonly AttachmentDownloader _downloader;
    private readonly PowershellOpsInvoker _ps;
    private readonly IAgentApiClient _api;
    private readonly ClientOptions _options;
    private readonly IActionSource _source;
    private readonly PauseState? _pauseState;
    private readonly ILogger<OutboundActionDispatcher>? _logger;

    /// <summary>
    /// PS 调用钩子（测试注入）。null 时走真实 PowershellOpsInvoker.InvokeAsync。
    /// 引入此钩子是因为 PowershellOpsInvoker 是 sealed 类（依赖 Process + stdin/stdout），
    /// 无法用 Moq/NSubstitute 直接 mock，单元测试无法构造实例。
    /// </summary>
    internal Func<string, object?, CancellationToken, Task<PowershellResult>>? PsInvokerHook { get; set; }

    private readonly Channel<OutboxItem> _workCh = Channel.CreateUnbounded<OutboxItem>(
        new UnboundedChannelOptions { SingleReader = true, SingleWriter = false });

    private Task? _sourceLoopTask;
    private Task? _workerLoopTask;
    private CancellationTokenSource? _cts;

    public OutboundActionDispatcher(
        OutboundQueue queue,
        AttachmentDownloader downloader,
        PowershellOpsInvoker ps,
        IAgentApiClient api,
        ClientOptions options,
        IActionSource source,
        ILogger<OutboundActionDispatcher>? logger = null,
        // P0-5：可选 PauseState（DI 注入时由容器解析；测试场景传 null 跳过暂停检查）
        PauseState? pauseState = null)
    {
        _queue = queue ?? throw new ArgumentNullException(nameof(queue));
        _downloader = downloader ?? throw new ArgumentNullException(nameof(downloader));
        _ps = ps ?? throw new ArgumentNullException(nameof(ps));
        _api = api ?? throw new ArgumentNullException(nameof(api));
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _source = source ?? throw new ArgumentNullException(nameof(source));
        _logger = logger;
        _pauseState = pauseState;
    }

    /// <inheritdoc />
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        _cts = new CancellationTokenSource();

        // 1. 启动恢复：把所有未完成的 action 拉出来重新塞入工作通道
        var pending = await _queue.ListPendingAsync(cancellationToken).ConfigureAwait(false);
        foreach (var item in pending)
        {
            await _workCh.Writer.WriteAsync(item, cancellationToken).ConfigureAwait(false);
        }
        _logger?.LogInformation("OutboundDispatcher 启动完成，恢复 {Count} 个未完成 action", pending.Count);

        // 2. 启动两个后台循环
        _sourceLoopTask = Task.Run(() => SourceLoopAsync(_cts.Token));
        _workerLoopTask = Task.Run(() => WorkerLoopAsync(_cts.Token));
    }

    /// <inheritdoc />
    public async Task StopAsync(CancellationToken cancellationToken)
    {
        _cts?.Cancel();
        _workCh.Writer.TryComplete();
        try
        {
            if (_sourceLoopTask is not null)
                await _sourceLoopTask.ConfigureAwait(false);
        }
        catch (OperationCanceledException) { }
        try
        {
            if (_workerLoopTask is not null)
                await _workerLoopTask.ConfigureAwait(false);
        }
        catch (OperationCanceledException) { }
    }

    /// <summary>
    /// 外部推送一个 ActionEnvelope（用于直接入队，不经 IActionSource，便于测试）。
    /// 拆 actions 后入队。
    /// </summary>
    public async Task EnvelopeEnqueueAsync(ActionEnvelope env, CancellationToken ct = default)
    {
        if (env is null) throw new ArgumentNullException(nameof(env));
        var convKey = ResolveConversationSearchName(env);
        for (var i = 0; i < env.Actions.Count; i++)
        {
            var act = env.Actions[i];
            var item = ToOutboxItem(env, i, act, convKey);
            var inserted = await _queue.EnqueueAsync(item, ct).ConfigureAwait(false);
            if (inserted)
            {
                await _workCh.Writer.WriteAsync(item, ct).ConfigureAwait(false);
            }
            else
            {
                // 重复项（action_id 已存在）：at-least-once 轮询下回执可能丢失，
                // 据本地终态重报回执（服务端幂等，收到后停止返回该信封）。
                await RereportIfTerminalAsync(item, ct).ConfigureAwait(false);
            }
        }
    }

    /// <summary>
    /// 重复信封处理：查本地终态并重报回执（at-least-once：服务端在回执丢失时会重复返回该信封）。
    /// done → 重报 success；failed → 重报失败带 stored error_code/error_message；pending/running → 跳过（在途）。
    /// 行已被 PruneTerminalAsync 清理（null）不处理，下次按新项插入。
    /// 日志只出 ActionId + 状态，**不含** text/file_url/reply_context（规约#7：不打印消息内容）。
    /// </summary>
    private async Task RereportIfTerminalAsync(OutboxItem item, CancellationToken ct)
    {
        OutboxItem? existing;
        try
        {
            existing = await _queue.GetByActionIdAsync(item.ActionId, ct).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "重复项 GetByActionId 失败 ActionId={Id}", item.ActionId);
            return;
        }
        if (existing is null) return;
        if (existing.Status == "done")
        {
            _logger?.LogInformation("重复 outbox 项已 done，重报 success ActionId={Id}", item.ActionId);
            await ReportActionResultAsync(item, success: true, null, null, ct).ConfigureAwait(false);
        }
        else if (existing.Status == "failed")
        {
            _logger?.LogInformation("重复 outbox 项 failed，重报失败 ActionId={Id} code={Code}",
                item.ActionId, existing.ErrorCode);
            await ReportActionResultAsync(item, success: false, existing.ErrorCode, existing.ErrorMessage, ct)
                .ConfigureAwait(false);
        }
        // pending / running：worker 在途，跳过（避免重复派发）
    }

    private string ResolveConversationSearchName(ActionEnvelope env)
    {
        var explicitName = env.ReplyContext?.ConversationSearchName?.Trim();
        if (!string.IsNullOrEmpty(explicitName)) return explicitName;

        var displayName = NormalizeConversationSearchName(env.ReplyContext?.SenderDisplayName);
        if (!string.IsNullOrEmpty(displayName)) return displayName;

        throw new InvalidOperationException(
            $"ActionEnvelope 缺少可用的会话搜索名 request_id={env.RequestId}");
    }

    internal static string? NormalizeConversationSearchName(string? displayName)
    {
        var value = displayName?.Trim();
        if (string.IsNullOrEmpty(value)) return null;
        const string suffix = "@微信";
        if (value.EndsWith(suffix, StringComparison.Ordinal))
            value = value[..^suffix.Length].Trim();
        return string.IsNullOrEmpty(value) ? null : value;
    }

    private static OutboxItem ToOutboxItem(ActionEnvelope env, int idx, RpaAction act,
        string convKey)
    {
        var item = new OutboxItem
        {
            ActionId = env.Actions.Count > 1 ? $"{env.RequestId}#{idx}" : env.RequestId,
            ActionType = act.Type,
            ConversationKey = convKey,
            CreatedAt = DateTimeOffset.Now,
        };
        switch (act)
        {
            case SendTextAction t:
                item.Text = t.Text;
                break;
            case SendImageAction img:
                item.FileUrl = img.FileUrl;
                item.FileName = img.Filename;
                break;
            case SendFileAction f:
                item.FileUrl = f.FileUrl;
                item.FileName = f.Filename;
                break;
        }
        return item;
    }

    /// <summary>从 IActionSource 拉取信封并拆解入队。</summary>
    private async Task SourceLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            ActionEnvelope? env;
            try
            {
                env = await _source.ReadAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { break; }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "SourceLoop 读取失败，5s 后重试");
                try { await Task.Delay(TimeSpan.FromSeconds(5), ct); } catch { }
                continue;
            }
            if (env is null)
            {
                // source 关闭，等待新数据（5s 退避避免忙等）
                try { await Task.Delay(TimeSpan.FromSeconds(1), ct); } catch { }
                continue;
            }
            await EnvelopeEnqueueAsync(env, ct);
        }
    }

    /// <summary>单 Worker：从工作通道出队 → 调 PS → 回执上报。</summary>
    private async Task WorkerLoopAsync(CancellationToken ct)
    {
        await foreach (var item in _workCh.Reader.ReadAllAsync(ct).ConfigureAwait(false))
        {
            try
            {
                await DispatchOneAsync(item, ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { throw; }
            catch (Exception ex)
            {
                _logger?.LogError(ex, "WorkerLoop 处理异常 ActionId={Id}", item.ActionId);
            }
        }
    }

    /// <summary>分发一个 outbox 项；包含重试逻辑。public 仅供测试直接驱动单条 action（不经 timer/Channel）。</summary>
    public async Task DispatchOneAsync(OutboxItem item, CancellationToken ct)
    {
        var maxRetries = Math.Max(1, _options.Outbound.MaxRetries);

        // P0-5：PauseState 命中检查（Tenant/Account 级或 Conversation 级暂停时跳过执行）。
        // item 已从队列出队（status='running'），命中暂停则 RequeueAsync 回 pending 让下次 Resume
        // 后重新出队；同时不上报失败回执（保留 action 等待 Resume）。
        if (_pauseState is not null)
        {
            if (_pauseState.IsPaused)
            {
                _logger?.LogDebug("客户端账号/租户暂停中，Requeue action: {ActionId}", item.ActionId);
                await _queue.RequeueAsync(item.ActionId, ct).ConfigureAwait(false);
                return;
            }
            if (!string.IsNullOrEmpty(item.ConversationKey) &&
                _pauseState.IsConversationPaused(item.ConversationKey))
            {
                _logger?.LogDebug("会话暂停中，Requeue action: {ActionId} conv={Conv}",
                    item.ActionId, item.ConversationKey);
                await _queue.RequeueAsync(item.ActionId, ct).ConfigureAwait(false);
                return;
            }
        }

        // noop / handoff 不经 PS，直接回执 success
        if (item.ActionType == ActionTypeNames.Noop)
        {
            _logger?.LogInformation("noop 跳过执行 ActionId={Id}", item.ActionId);
            await ReportAndCleanupAsync(item, success: true).ConfigureAwait(false);
            return;
        }
        if (item.ActionType == ActionTypeNames.Handoff)
        {
            // Phase 3 简化：只 log，不真暂停。Phase 4 接会话暂停基础设施。
            _logger?.LogWarning("handoff 简化处理：ActionId={Id}（Phase 4 接入真暂停）", item.ActionId);
            await ReportAndCleanupAsync(item, success: true).ConfigureAwait(false);
            return;
        }

        // send_text / send_image / send_file 走 PS 调用链
        for (var attempt = 1; attempt <= maxRetries; attempt++)
        {
            var (ok, errCode, errMsg) = await InvokePsOnceAsync(item, ct).ConfigureAwait(false);

            if (ok)
            {
                await ReportAndCleanupAsync(item, success: true).ConfigureAwait(false);
                return;
            }

            // 错误码映射：PS wecom_navigation_failed → 回执 unsupported_action
            var reportCode = MapErrorCode(errCode);

            // 仅 wecom_window_not_found 允许重试
            if (errCode != "wecom_window_not_found" || attempt >= maxRetries)
            {
                _logger?.LogWarning("action 失败终态 ActionId={Id} Code={Code}", item.ActionId, reportCode);
                await _queue.MarkFailedAsync(item.ActionId, reportCode, errMsg, ct).ConfigureAwait(false);
                await ReportActionResultAsync(item, success: false, reportCode, errMsg, ct).ConfigureAwait(false);
                return;
            }

            _logger?.LogWarning("action 重试 ActionId={Id} attempt={A} reason={Code}",
                item.ActionId, attempt, errCode);
            try { await Task.Delay(TimeSpan.FromMilliseconds(500 * attempt), ct); }
            catch (OperationCanceledException) { throw; }
        }
    }

    private async Task<(bool ok, string? code, string? msg)> InvokePsOnceAsync(OutboxItem item, CancellationToken ct)
    {
        // 测试钩子：注入时调用 hook 替代真实 PS。生产路径走 _ps.InvokeAsync。
        var psInvoke = PsInvokerHook ?? ((action, p, token) => _ps.InvokeAsync(action, p, token));

        switch (item.ActionType)
        {
            case ActionTypeNames.SendText:
            {
                var psParams = new { keyword = item.ConversationKey, text = item.Text ?? string.Empty };
                var r = await psInvoke("send_text", psParams, ct).ConfigureAwait(false);
                return (r.Success, r.ErrorCode, r.ErrorMessage);
            }
            case ActionTypeNames.SendImage:
            case ActionTypeNames.SendFile:
            {
                var localPath = item.LocalPath;
                if (string.IsNullOrEmpty(localPath) && !string.IsNullOrEmpty(item.FileUrl))
                {
                    try
                    {
                        localPath = await _downloader.DownloadAsync(
                            item.FileUrl, item.FileName, item.ActionType == ActionTypeNames.SendImage, ct)
                            .ConfigureAwait(false);
                    }
                    catch (AttachmentRejectedException ex)
                    {
                        return (false, "attachment_rejected", ex.Message);
                    }
                    catch (Exception ex)
                    {
                        return (false, "attachment_download_failed", ex.Message);
                    }
                }
                if (string.IsNullOrEmpty(localPath))
                {
                    return (false, "invalid_params",
                        $"{item.ActionType} 缺少 file_url / local_path");
                }

                var psAction = item.ActionType == ActionTypeNames.SendImage ? "send_image" : "send_file";
                var pathParam = item.ActionType == ActionTypeNames.SendImage ? "image_path" : "file_path";
                var psParams = new Dictionary<string, object?>
                {
                    ["keyword"] = item.ConversationKey,
                    [pathParam] = localPath,
                };
                try
                {
                    var r = await psInvoke(psAction, psParams, ct).ConfigureAwait(false);
                    return (r.Success, r.ErrorCode, r.ErrorMessage);
                }
                finally
                {
                    // PS 被取消或抛异常时也必须清理下载副本。
                    TryDeleteLocal(localPath);
                }
            }
            default:
                return (false, "unsupported_action", $"未支持的 action_type: {item.ActionType}");
        }
    }

    private static string MapErrorCode(string? psCode) => psCode switch
    {
        "wecom_navigation_failed" => "unsupported_action",
        null or "" => "execution_failed",
        _ => psCode,
    };

    private async Task ReportAndCleanupAsync(OutboxItem item, bool success)
    {
        await _queue.MarkDoneAsync(item.ActionId).ConfigureAwait(false);
        await ReportActionResultAsync(item, success, null, null).ConfigureAwait(false);
    }

    private async Task ReportActionResultAsync(OutboxItem item, bool success,
        string? code, string? msg, CancellationToken ct = default)
    {
        try
        {
            await _api.ReportActionResultAsync(item.ActionId, success, code, msg, ct)
                .ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "上报 action_result 失败 ActionId={Id}", item.ActionId);
        }
    }

    private static void TryDeleteLocal(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch { /* 吞掉清理失败 */ }
    }

    public void Dispose()
    {
        _cts?.Dispose();
    }
}
