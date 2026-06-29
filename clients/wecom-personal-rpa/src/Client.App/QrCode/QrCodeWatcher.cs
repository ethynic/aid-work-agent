using System.Text.Json;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using WeCom.PersonalRpa.App.Powershell;
using WeCom.PersonalRpa.Core.AgentApi;
using WeCom.PersonalRpa.Core.Config;
using WeCom.PersonalRpa.Core.Protocol;

namespace WeCom.PersonalRpa.App.QrCode;

/// <summary>
/// 未登录二维码截取与上报（设计文档 §F7）。
///
/// 周期性调用 PS <c>get_login_state</c> 检测企微登录态：
/// <list type="bullet">
///   <item>online → online 状态无变化：不重复上报（避免噪音）</item>
///   <item>非 online 状态切换：上报一次最新二维码（need_login / qr_expired / offline）</item>
///   <item>持续非 online：每周期推一次最新二维码（防止二维码过期/服务端 TTL 失效）</item>
/// </list>
///
/// 串行化：用 <see cref="SemaphoreSlim"/> 防止 Timer 重入导致两次轮询并发执行。
/// 二维码 base64 不打日志（敏感数据）。
/// </summary>
public class QrCodeWatcher : IHostedService, IDisposable
{
    private readonly PowershellOpsInvoker _ps;
    private readonly IAgentApiClient _apiClient;
    private readonly ILogger<QrCodeWatcher> _logger;
    private readonly TimeSpan _pollInterval;
    private Timer? _timer;

    /// <summary>上次上报的 state（"online" / "need_login" / "qr_expired" / "offline"），用于状态机判断。</summary>
    private string? _lastState;

    private readonly SemaphoreSlim _pollLock = new(1, 1);

    public QrCodeWatcher(
        PowershellOpsInvoker ps,
        IAgentApiClient apiClient,
        IOptions<ClientOptions> options,
        ILogger<QrCodeWatcher> logger)
    {
        _ps = ps ?? throw new ArgumentNullException(nameof(ps));
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
        // P1-6：默认周期 25s（小于服务端 30s TTL），避免周期与 TTL 边界抖动导致偶发空窗。
        var pollSeconds = options?.Value?.QrCode?.PollIntervalSeconds ?? 25;
        if (pollSeconds <= 0) pollSeconds = 25;
        _pollInterval = TimeSpan.FromSeconds(pollSeconds);
    }

    /// <inheritdoc />
    public Task StartAsync(CancellationToken cancellationToken)
    {
        // Timer 立即触发一次（dueTime=0），随后按 _pollInterval 周期触发。
        _timer = new Timer(async _ => await PollOnceSafeAsync(), null, TimeSpan.Zero, _pollInterval);
        return Task.CompletedTask;
    }

    /// <inheritdoc />
    public Task StopAsync(CancellationToken cancellationToken)
    {
        _timer?.Change(Timeout.Infinite, 0);
        return Task.CompletedTask;
    }

    /// <summary>
    /// Timer 回调包装：捕获所有异常 + 用 SemaphoreSlim 串行化（跳过抢不到锁的实例）。
    /// </summary>
    private async Task PollOnceSafeAsync()
    {
        if (!await _pollLock.WaitAsync(0))
        {
            // 上一次轮询还未结束，直接跳过本次（避免堆积）
            return;
        }
        try
        {
            await PollOnceAsync();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "QrCodeWatcher 轮询异常");
        }
        finally
        {
            _pollLock.Release();
        }
    }

    /// <summary>
    /// 单次轮询核心逻辑。封装为 internal virtual 以便单元测试通过子类替换 PS 调用，
    /// 同时让生产代码继续依赖 PowershellOpsInvoker（避免引入额外接口）。
    /// </summary>
    internal async Task PollOnceAsync()
    {
        var result = await GetLoginStateFromPsAsync();
        if (!result.Success)
        {
            _logger.LogWarning("PS get_login_state 失败: code={Code} msg={Msg}",
                result.ErrorCode, result.ErrorMessage);
            return;
        }

        var state = result.State;
        var isOnline = string.Equals(state, "online", StringComparison.OrdinalIgnoreCase);
        var qrBase64 = ExtractQrImageBase64(result);

        if (isOnline && _lastState != "online")
        {
            // 从未登录恢复 online
            await ReportAsync(AccountStatus.Online, qrBase64,
                detail: "扫码成功，账号已上线");
            _lastState = "online";
        }
        else if (!isOnline && _lastState != state)
        {
            // 状态切换（online→need_login / need_login→qr_expired / offline→need_login 等）
            var detail = string.Equals(state, "qr_expired", StringComparison.OrdinalIgnoreCase)
                ? "二维码已失效，等待重新展示"
                : "二维码已展示，等待扫码";
            await ReportAsync(ParseStatus(state), qrBase64, detail: detail);
            _lastState = state;
        }
        else if (!isOnline)
        {
            // 持续未登录：每周期推一次最新二维码，防止服务端 30s TTL 失效后丢失
            await ReportAsync(ParseStatus(state), qrBase64, detail: null);
            // _lastState 不变
        }
        // else: online 且 _lastState==online → 静默（不重复上报）
    }

    /// <summary>
    /// 调用 PS get_login_state。internal virtual 让单元测试子类替换为固定返回，
    /// 不引入 PowershellOpsInvoker 的 mock 接口（避免触碰块 C/D 文件）。
    /// </summary>
    protected virtual Task<PowershellResult> GetLoginStateFromPsAsync()
    {
        return _ps.InvokeAsync("get_login_state", null);
    }

    /// <summary>从 PowershellResult.RawJson 提取 qr_image_base64 字段（可能为 null）。</summary>
    private static string? ExtractQrImageBase64(PowershellResult result)
    {
        if (string.IsNullOrEmpty(result.RawJson)) return null;
        try
        {
            using var doc = JsonDocument.Parse(result.RawJson);
            return doc.RootElement.TryGetProperty("qr_image_base64", out var qr)
                   && qr.ValueKind == JsonValueKind.String
                ? qr.GetString()
                : null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    /// <summary>
    /// 把 PS 返回的 state 字符串映射为 AccountStatus 枚举。
    /// 未知值（含 null）默认 NeedLogin（保守处理：宁可多报二维码）。
    /// </summary>
    private static AccountStatus ParseStatus(string? state) => state?.ToLowerInvariant() switch
    {
        "online" => AccountStatus.Online,
        "offline" => AccountStatus.Offline,
        "need_login" => AccountStatus.NeedLogin,
        "qr_expired" => AccountStatus.QrExpired,
        "account_limited" => AccountStatus.AccountLimited,
        "desktop_locked" => AccountStatus.DesktopLocked,
        "window_not_visible" => AccountStatus.WindowNotVisible,
        "paused" => AccountStatus.Paused,
        "recovering" => AccountStatus.Recovering,
        _ => AccountStatus.NeedLogin,
    };

    /// <summary>
    /// 构造 StatusPayload 并上报。日志只记 status + 是否带二维码（true/false），
    /// 绝不打印 base64 内容。
    /// </summary>
    private async Task ReportAsync(AccountStatus status, string? qrBase64, string? detail)
    {
        var payload = new StatusPayload
        {
            Status = status,
            Detail = detail,
            QrImageBase64 = qrBase64,
        };
        _logger.LogInformation("QrCodeWatcher 上报 status={Status} has_qr={HasQr}",
            status, !string.IsNullOrEmpty(qrBase64));
        try
        {
            await _apiClient.ReportStatusAsync(payload);
        }
        catch (Exception ex)
        {
            // 上报失败不抛：下次轮询会重试，避免单次失败拖垮整个 watcher
            _logger.LogWarning(ex, "ReportStatusAsync 失败 status={Status}", status);
        }
    }

    public void Dispose()
    {
        _timer?.Dispose();
        _pollLock.Dispose();
    }
}
