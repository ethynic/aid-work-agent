using System.IO;
using System.Text.Json;
using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;
using WeCom.PersonalRpa.Core.Protocol;
using WeCom.PersonalRpa.Core.Queue;
using WeCom.PersonalRpa.Core.StateMachine;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 出站消息串行发送服务。
///
/// 规则（对齐 protocol §A.6）：
///   - 同一账号全局串行，同一会话 actions 顺序执行。
///   - 仅当 <see cref="IStateManager.CanSend"/> 为 true（Running）时才执行。
///   - 任一 action 失败按策略上报回执，停止后续或转人工。
///   - 文件类 action 先下载到客户端临时目录再发送，完成后删除。
///
/// 底层 UI 操作委托 Client.Automation 的 <see cref="IActionExecutor"/>（FlaUI/UIA3 操作企微窗口）。
/// 队列消费依赖 Core 的 <see cref="ISendQueue"/>（SQLite 持久化，每个 action 一行）。
/// </summary>
public sealed class SendMessageService
{
    private const string Tag = "SendMessageService";

    private readonly IStateManager _state;
    private readonly ISendQueue _queue;
    private readonly InboundReporter _reporter;
    private readonly IActionExecutor _executor;
    private readonly SemaphoreSlim _drainLock = new(1, 1);

    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
    };

    public SendMessageService(
        IStateManager state,
        ISendQueue queue,
        InboundReporter reporter,
        IActionExecutor executor)
    {
        _state = state;
        _queue = queue;
        _reporter = reporter;
        _executor = executor;
    }

    /// <summary>串行消费队列中的 Pending 项，直到空或状态变化。</summary>
    public async Task DrainAsync(string accountId, CancellationToken ct)
    {
        if (!_state.CanSend())
        {
            Log.Debug("[{Tag}] 非 Running 状态，跳过发送 Account={Acc}", Tag, accountId);
            return;
        }

        await _drainLock.WaitAsync(ct);
        try
        {
            while (!ct.IsCancellationRequested && _state.CanSend())
            {
                var batch = await _queue.ClaimPendingAsync(limit: 5, ct);
                if (batch.Count == 0) break;

                foreach (var item in batch)
                {
                    if (!_state.CanSend())
                    {
                        // 状态变更：回退为 Paused，等待恢复
                        await _queue.MarkAsync(item.Id, QueueStatus.Paused, "状态变更中断", null, ct);
                        continue;
                    }
                    await ExecuteOneAsync(item, accountId, ct);
                }
            }
        }
        finally
        {
            _drainLock.Release();
        }
    }

    /// <summary>执行单个队列项并产出回执。</summary>
    private async Task ExecuteOneAsync(SendAction item, string accountId, CancellationToken ct)
    {
        RpaAction? action;
        try
        {
            action = JsonSerializer.Deserialize<RpaAction>(item.ActionJson, JsonOpts);
        }
        catch (Exception ex)
        {
            Log.Error(ex, "[{Tag}] action JSON 解析失败 Id={Id} Req={Req}", Tag, item.Id, item.RequestId);
            await _queue.MarkAsync(item.Id, QueueStatus.Failed, "action 解析失败", null, ct);
            await _reporter.ReportActionResultAsync(FailResult(item, "bad_action_json", "action 解析失败"), accountId, ct);
            return;
        }

        if (action is null)
        {
            await _queue.MarkAsync(item.Id, QueueStatus.Failed, "action 为空", null, ct);
            return;
        }

        Log.Information("[{Tag}] 执行 Req={Req} Index={Idx} Type={Type}",
            Tag, item.RequestId, item.ActionIndex, action.Type);

        var (success, errCode, errMsg) = await DispatchAsync(item, action, ct);

        var result = new ActionResultPayload
        {
            RequestId = item.RequestId,
            ActionResultId = $"res_{Guid.NewGuid():N}",
            ActionIndex = item.ActionIndex,
            ActionType = action.Type,
            Success = success,
            ErrorCode = errCode,
            ErrorMessage = errMsg,
            ExecutedAt = DateTimeOffset.Now,
        };
        await _reporter.ReportActionResultAsync(result, accountId, ct);

        var finalStatus = success ? QueueStatus.Succeeded : QueueStatus.Failed;
        await _queue.MarkAsync(item.Id, finalStatus, errMsg, null, ct);
    }

    /// <summary>按 action 类型分发到 <see cref="IActionExecutor"/>。</summary>
    private async Task<(bool success, string? errCode, string? errMsg)> DispatchAsync(
        SendAction item, RpaAction action, CancellationToken ct)
    {
        try
        {
            var key = item.ConversationId; // 客户端本地会话定位键
            switch (action)
            {
                case Core.Protocol.SendTextAction t:
                    {
                        var r = await _executor.SendTextAsync(key, t.Text, ct);
                        return (r.Success, r.ErrorCode, r.ErrorMessage);
                    }
                case Core.Protocol.SendImageAction img:
                    {
                        // 文件类：先下载到临时目录再发送，完成后删除（protocol §A.6）
                        var localPath = await DownloadToTempAsync(img.FileUrl, img.Filename ?? "image.png", ct);
                        try
                        {
                            var r = await _executor.SendImageAsync(key, localPath, ct);
                            return (r.Success, r.ErrorCode, r.ErrorMessage);
                        }
                        finally { SafeDelete(localPath); }
                    }
                case Core.Protocol.SendFileAction f:
                    {
                        if (string.IsNullOrEmpty(f.Filename))
                            return (false, "bad_action", "send_file 缺少 filename");
                        var localPath = await DownloadToTempAsync(f.FileUrl, f.Filename, ct);
                        try
                        {
                            var r = await _executor.SendFileAsync(key, localPath, ct);
                            return (r.Success, r.ErrorCode, r.ErrorMessage);
                        }
                        finally { SafeDelete(localPath); }
                    }
                case Core.Protocol.NoopAction:
                    Log.Information("[{Tag}] noop 跳过 Req={Req}", Tag, item.RequestId);
                    return (true, null, null);
                case Core.Protocol.HandoffAction h:
                    Log.Information("[{Tag}] handoff 暂停会话 Req={Req} Reason={Reason}",
                        Tag, item.RequestId, h.Reason ?? "-");
                    // 转人工：停止该会话后续（首版仅记录，真实暂停 TODO）
                    return (true, null, null);
                default:
                    return (false, "unsupported_action", $"不支持的 action 类型 {action.Type}");
            }
        }
        catch (OperationCanceledException)
        {
            return (false, "cancelled", "执行被取消");
        }
        catch (Exception ex)
        {
            return (false, "execution_failed", Sanitize(ex.Message));
        }
    }

    /// <summary>下载文件到临时目录（委托 Core.IAgentApiClient.DownloadFileAsync，首版占位）。</summary>
    private async Task<string> DownloadToTempAsync(string fileUrl, string filename, CancellationToken ct)
    {
        // TODO: 通过注入的 IAgentApiClient.DownloadFileAsync(fileUrl, ct) 写入临时目录
        var tmp = Path.Combine(Path.GetTempPath(), $"wecom_rpa_{Guid.NewGuid():N}_{filename}");
        await File.WriteAllTextAsync(tmp, "", ct); // 占位空文件
        return tmp;
    }

    private static void SafeDelete(string path)
    {
        try { if (File.Exists(path)) File.Delete(path); }
        catch (Exception ex) { Log.Warning(ex, "[{Tag}] 删除临时文件失败 {Path}", Tag, path); }
    }

    /// <summary>错误信息脱敏（剔除绝对路径/密钥）。</summary>
    private static string Sanitize(string msg)
    {
        if (string.IsNullOrEmpty(msg)) return msg;
        msg = System.Text.RegularExpressions.Regex.Replace(
            msg, @"[A-Za-z]:\\[^\s""']+", "<path>");
        msg = System.Text.RegularExpressions.Regex.Replace(
            msg, "(?i)(secret|token|signature)[\"'\\s:=]+\\S+", "$1=***");
        return msg;
    }

    private static ActionResultPayload FailResult(SendAction item, string code, string msg) => new()
    {
        RequestId = item.RequestId,
        ActionResultId = $"res_{Guid.NewGuid():N}",
        ActionIndex = item.ActionIndex,
        ActionType = "?",
        Success = false,
        ErrorCode = code,
        ErrorMessage = msg,
        ExecutedAt = DateTimeOffset.Now,
    };
}
