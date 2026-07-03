using System.IO;
using System.Text.Json;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 会话存档轮询监听器（Phase 3 块 D）。
///
/// 实现 <see cref="IMessageWatcher"/>。主循环（PeriodicTimer）：
///   1. 从 ArchiveSeqStore 拉当前 account 的 seq
///   2. 调 ArchiveHttpClient.GetChatDataAsync(seq, batch_limit) 拿一批密文
///   3. 对每条：
///      - action = "download" / "switch" → 跳过（仅审计）
///      - action = "upload" / "recall"：
///         a. 用 ArchiveCryptoService.DecryptRandomKey 私钥解密 encrypt_random_key → random_key
///         b. 用 random_key 解密 encrypt_chat_msg → 明文 JSON
///         c. 解析 msgtype，构造 ArchiveMessage，触发 NewMessageReceived
///   4. 成功处理批次后更新 ArchiveSeqStore.seq = 最后一条的 seq
///
/// 错误处理：
///   - 单条解密失败 → 跳过该条 + log warning + 不更新 seq（下次重拉）
///   - 网络异常 / 45009 → 由 ArchiveHttpClient 内部重试 / 暂停
///   - 取消令牌 → 优雅停止
///
/// 重要：监听器不在内部下载媒体（图片/文件/语音/视频）。ArchiveMessage 只透传 MediaSdkFileId +
/// MediaFileName，由 Phase 4 InboundEventBuilder 调用 ArchiveMediaDownloader 完成。
/// </summary>
public sealed class ChatArchiveListener : IMessageWatcher, IHostedService
{
    private const string Tag = "ChatArchiveListener";

    private readonly ArchiveHttpClient _http;
    private readonly ArchiveCryptoService _crypto;
    private readonly ArchiveSeqStore _seqStore;
    private readonly ArchiveOptions _opts;
    private readonly Func<string> _loadPrivateKey;
    private readonly Func<string> _resolveSecret;
    private readonly Func<string, long, int, CancellationToken, Task<ChatDataBatch>>? _fetchBatchOverride;
    private readonly Func<CancellationToken, Task<string>>? _getTokenOverride;
    private readonly ILogger<ChatArchiveListener>? _logger;

    private CancellationTokenSource? _cts;
    private Task? _loopTask;

    /// <summary>
    /// 45009 频率限制暂停期。在此时间戳之前，PollOnceAsync 直接跳过（不拉数据、不推进 seq）。
    /// 默认暂停 60s（与 ArchiveHttpClient 内部限制一致），由 WeComRateLimitException 触发。
    /// </summary>
    private DateTimeOffset _pausedUntil = DateTimeOffset.MinValue;

    /// <inheritdoc />
    public event EventHandler<InboundEventArgs>? NewMessageReceived;

    public ChatArchiveListener(
        ArchiveHttpClient http,
        ArchiveCryptoService crypto,
        ArchiveSeqStore seqStore,
        ArchiveOptions opts,
        string accountId,
        ILogger<ChatArchiveListener>? logger = null,
        Func<string>? loadPrivateKey = null,
        Func<string>? resolveSecret = null,
        Func<string, long, int, CancellationToken, Task<ChatDataBatch>>? fetchBatchOverride = null,
        Func<CancellationToken, Task<string>>? getTokenOverride = null)
    {
        _http = http ?? throw new ArgumentNullException(nameof(http));
        _crypto = crypto ?? throw new ArgumentNullException(nameof(crypto));
        _seqStore = seqStore ?? throw new ArgumentNullException(nameof(seqStore));
        _opts = opts ?? throw new ArgumentNullException(nameof(opts));
        AccountId = accountId ?? throw new ArgumentNullException(nameof(accountId));
        _logger = logger;
        _loadPrivateKey = loadPrivateKey ?? DefaultLoadPrivateKey;
        _resolveSecret = resolveSecret ?? DefaultResolveSecret;
        _fetchBatchOverride = fetchBatchOverride;
        _getTokenOverride = getTokenOverride;
    }

    /// <summary>本监听器对应的企微账号 ID（用于 seq 隔离）。</summary>
    public string AccountId { get; }

    /// <summary>
    /// 会话存档拉取模式开关（Phase 7+）：
    /// - ListenMode.Server（默认，第一期）：服务端拉取，客户端**跳过本地轮询**
    /// - ListenMode.Client：客户端本地拉取（第一期不开放，前端禁用）
    ///
    /// 设置时机：ClientSession 在 GET /config 拿到 RpaConfigResponse.ListenMode 后
    /// 设置此属性。但 DI 阶段 ChatArchiveListener 已注册为 IHostedService，StartAsync
    /// 在 ClientSession 之前被 Host 调起——所以采用「StartAsync 时不立即启动 PollLoop，
    /// 改为由 ClientSession 在拉 config 后显式调用 EnablePolling()」。
    ///
    /// 第一期简化：默认 DisablePolling=true（不启动 PollLoop），等 listen_mode='client'
    /// 时才 EnablePolling。这与服务端「永远下发 server」一致。
    /// </summary>
    public bool DisablePolling { get; set; } = true;

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken = default)
    {
        if (_loopTask is not null)
        {
            _logger?.LogWarning("[{Tag}] 已在运行，忽略重复 Start", Tag);
            return Task.CompletedTask;
        }
        if (DisablePolling)
        {
            // Phase 7+：服务端拉取模式下客户端不启动本地轮询
            // listen_mode=client 时由 ClientSession 在拉 config 后调 EnablePolling()
            _logger?.LogInformation(
                "[{Tag}] DisablePolling=true，跳过本地轮询启动（服务端拉取模式）account={Aid}",
                Tag, AccountId
            );
            return Task.CompletedTask;
        }
        _cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _loopTask = Task.Run(() => PollLoopAsync(_cts.Token), _cts.Token);
        _logger?.LogInformation("[{Tag}] 启动监听 account={Aid} 间隔={Interval}s", Tag, AccountId, _opts.PollIntervalSeconds);
        return Task.CompletedTask;
    }

    /// <summary>
    /// 由 ClientSession 在 GET /config 后调用，启用本地轮询（仅 listen_mode=client 时）。
    /// 第一期服务端永远不下发 client，此方法实际不会被调用。
    /// </summary>
    public Task EnablePollingAsync(CancellationToken cancellationToken = default)
    {
        DisablePolling = false;
        return StartAsync(cancellationToken);
    }

    /// <inheritdoc />
    public async Task StopAsync(CancellationToken cancellationToken = default)
    {
        if (_cts is null) return;
        _cts.Cancel();
        try
        {
            if (_loopTask is not null) await _loopTask.ConfigureAwait(false);
        }
        catch (OperationCanceledException) { /* 期望 */ }
        catch (Exception ex)
        {
            _logger?.LogWarning(ex, "[{Tag}] 轮询任务异常退出", Tag);
        }
        _cts.Dispose();
        _cts = null;
        _loopTask = null;
        _logger?.LogInformation("[{Tag}] 已停止监听 account={Aid}", Tag, AccountId);
    }

    private async Task PollLoopAsync(CancellationToken ct)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(Math.Max(1, _opts.PollIntervalSeconds)));
        do
        {
            try
            {
                await PollOnceAsync(ct).ConfigureAwait(false);
            }
            catch (OperationCanceledException) { throw; }
            catch (WeComRateLimitException ex)
            {
                // 45009：设置暂停期（默认 60s，由异常 RetryAfterSeconds 控制）。
                // PeriodicTimer 仍按原间隔 tick，但 PollOnceAsync 开头检查 _pausedUntil 会跳过实际请求。
                var pauseSec = ex.RetryAfterSeconds > 0 ? ex.RetryAfterSeconds : 60;
                _pausedUntil = DateTimeOffset.UtcNow.AddSeconds(pauseSec);
                _logger?.LogWarning("[{Tag}] 企微返回 45009，暂停 {Pause}s", Tag, pauseSec);
            }
            catch (Exception ex)
            {
                _logger?.LogWarning(ex, "[{Tag}] 轮询一次失败，等待下一周期", Tag);
            }
        } while (!ct.IsCancellationRequested && await timer.WaitForNextTickAsync(ct).ConfigureAwait(false));
    }

    /// <summary>
    /// 单次轮询：拉一批 → 解密每条 → 触发事件 → 推进 seq。
    /// 测试可直接调用此方法（不走 PeriodicTimer）。
    /// </summary>
    internal async Task PollOnceAsync(CancellationToken ct)
    {
        var privateKey = _loadPrivateKey();
        var secret = _resolveSecret();
        if (string.IsNullOrEmpty(privateKey))
        {
            _logger?.LogWarning("[{Tag}] 私钥未加载，跳过本次轮询（path={Path}）", Tag, _opts.PrivateKeyPath);
            return;
        }
        if (string.IsNullOrEmpty(_opts.Corpid) || string.IsNullOrEmpty(secret))
        {
            _logger?.LogWarning("[{Tag}] corpid/secret 缺失，跳过本次轮询", Tag);
            return;
        }

        var token = _getTokenOverride is not null
            ? await _getTokenOverride(ct).ConfigureAwait(false)
            : await _http.GetAccessTokenAsync(_opts.Corpid, secret, ct).ConfigureAwait(false);

        // 45009 频率限制：本周期内跳过实际拉数据（拉数据本身就会触发 45009）。
        // 测试场景下 _fetchBatchOverride 注入也走此分支，避免在暂停期内被调用。
        if (DateTimeOffset.UtcNow < _pausedUntil)
        {
            _logger?.LogInformation("[{Tag}] 处于 45009 暂停期（剩余 {Sec}s），跳过本次轮询",
                Tag, (_pausedUntil - DateTimeOffset.UtcNow).TotalSeconds);
            return;
        }

        var seq = await _seqStore.GetSeqAsync(AccountId, ct).ConfigureAwait(false);
        var batch = _fetchBatchOverride is not null
            ? await _fetchBatchOverride(token, seq, _opts.BatchLimit, ct).ConfigureAwait(false)
            : await _http.GetChatDataAsync(token, seq, _opts.BatchLimit, ct).ConfigureAwait(false);

        if (batch.Items.Count == 0)
        {
            return;
        }

        // P0-1：逐条推进 seq。每成功处理一条立刻 SetSeqAsync，
        // 这样即使后续条目解密失败，前 N 条已推进 seq，下次重拉不会重复触发事件。
        // SetSeqAsync 内部有 SemaphoreSlim 串行化 + UPSERT 原子写，逐条调用安全。
        foreach (var item in batch.Items)
        {
            // download / switch action 不触发事件，但仍推进 seq（已确认无需处理）。
            if (item.Action != "upload" && item.Action != "recall")
            {
                if (item.Seq > seq)
                {
                    await _seqStore.SetSeqAsync(AccountId, item.Seq, ct).ConfigureAwait(false);
                }
                continue;
            }

            try
            {
                var randomKey = _crypto.DecryptRandomKey(privateKey, item.EncryptRandomKey);
                var plain = _crypto.DecryptChatMsg(randomKey, item.EncryptChatMsg);
                var msg = ParseArchiveMessage(item, plain);
                NewMessageReceived?.Invoke(this, new InboundEventArgs { Message = msg });
                // 成功一条立刻推进 seq（避免重拉重复触发）
                if (item.Seq > seq)
                {
                    await _seqStore.SetSeqAsync(AccountId, item.Seq, ct).ConfigureAwait(false);
                }
            }
            catch (WeComRateLimitException)
            {
                // 45009：由 PollOnceAsync 顶层处理（设置 _pausedUntil），此处直接抛出向上传播
                throw;
            }
            catch (Exception ex)
            {
                // 单条解密失败 → 不推进当前条 seq（下次重拉同一条再试）。
                // 前面成功的条目 seq 已推进，不会重复触发。
                _logger?.LogWarning(ex, "[{Tag}] 解密失败 msgid={MsgId} seq={Seq}，停止本批次（已成功条目已推进 seq）",
                    Tag, item.MsgId, item.Seq);
                break;
            }
        }
    }

    private static ArchiveMessage ParseArchiveMessage(ChatDataItem item, string plainJson)
    {
        using var doc = JsonDocument.Parse(plainJson);
        var root = doc.RootElement;

        string? text = null;
        string? mediaFileId = null;
        string? mediaFileName = null;

        if (root.TryGetProperty("text", out var tEl) && tEl.ValueKind == JsonValueKind.String)
        {
            text = tEl.GetString();
        }

        // msgtype 切换：text.content / image.sdkfileid / file.sdkfileid+filename / voice.sdkfileid / video.sdkfileid
        var msgType = item.MsgType;
        if (root.TryGetProperty(msgType, out var payloadEl) && payloadEl.ValueKind == JsonValueKind.Object)
        {
            if (msgType == "text")
            {
                if (payloadEl.TryGetProperty("content", out var cEl) && cEl.ValueKind == JsonValueKind.String)
                {
                    text = cEl.GetString();
                }
            }
            else if (msgType is "image" or "file" or "voice" or "video")
            {
                if (payloadEl.TryGetProperty("sdkfileid", out var sidEl) && sidEl.ValueKind == JsonValueKind.String)
                {
                    mediaFileId = sidEl.GetString();
                }
                if (payloadEl.TryGetProperty("filename", out var fnEl) && fnEl.ValueKind == JsonValueKind.String)
                {
                    mediaFileName = fnEl.GetString();
                }
            }
        }

        return new ArchiveMessage
        {
            MsgId = item.MsgId,
            Action = item.Action,
            From = item.From,
            MsgType = msgType,
            MsgTime = item.MsgTime,
            Text = text,
            RoomId = item.RoomId,
            ToList = item.ToList,
            MediaSdkFileId = mediaFileId,
            MediaFileName = mediaFileName,
        };
    }

    private string DefaultLoadPrivateKey()
    {
        var path = _opts.PrivateKeyPath;
        return File.Exists(path) ? File.ReadAllText(path) : string.Empty;
    }

    private string DefaultResolveSecret()
    {
        // 优先配置；其次环境变量 ARCHIVE_SECRET。
        if (!string.IsNullOrEmpty(_opts.Secret)) return _opts.Secret;
        return Environment.GetEnvironmentVariable("ARCHIVE_SECRET") ?? string.Empty;
    }

    /// <inheritdoc />
    public void Dispose()
    {
        _cts?.Cancel();
        _cts?.Dispose();
    }
}
