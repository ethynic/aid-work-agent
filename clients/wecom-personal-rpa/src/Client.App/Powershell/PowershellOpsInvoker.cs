using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace WeCom.PersonalRpa.App.Powershell;

/// <summary>
/// 通过 Process + stdin/stdout JSON 调用 wecom-ops.ps1 的封装。
/// 调用契约见 docs/system/wecom-personal-rpa-client-design.md §F2。
///
/// 流程：
///   1. 构造 ProcessStartInfo（powershell.exe -File wecom-ops.ps1 -Action &lt;action&gt;）
///   2. 启动进程，BeginErrorReadLine 异步收 stderr
///   3. stdin 写参数 JSON 后 Close（PS 端 ReadToEnd 返回）
///   4. 等待退出（带超时 + CancellationToken），超时则 Kill(entireProcessTree)
///   5. 读 stdout，取最后一行 { 开头的 JSON 反序列化为 PowershellResult
///
/// 错误码与设计文档 §F2 表格对齐：
///   - ps_start_failed    Process.Start 返回 false（极少见，通常是缺 powershell.exe）
///   - ps_timeout         超时未退出（脚本卡死或企微弹窗阻塞）
///   - ps_no_json         stdout 找不到 { 开头的 JSON 行（通常是 ValidateSet 报错或脚本崩）
///   - ps_script_exception PS 内部 catch 到异常（脚本主动 Write-Result）
/// </summary>
public sealed class PowershellOpsInvoker
{
    private readonly PowershellOptions _options;
    private readonly ILogger<PowershellOpsInvoker> _logger;

    public PowershellOpsInvoker(PowershellOptions options, ILogger<PowershellOpsInvoker> logger)
    {
        _options = options ?? throw new ArgumentNullException(nameof(options));
        _logger = logger ?? throw new ArgumentNullException(nameof(logger));
    }

    /// <summary>调用一次 PS 脚本。</summary>
    /// <param name="action">ValidateSet 内的 action 名（search_user/send_text/...）。</param>
    /// <param name="parameters">序列化为 stdin JSON 的参数对象，可为 null。</param>
    /// <param name="ct">取消令牌。</param>
    /// <returns>永远返回非 null 的 PowershellResult（失败信息在 ErrorCode/ErrorMessage 里）。</returns>
    public async Task<PowershellResult> InvokeAsync(string action, object? parameters, CancellationToken ct = default)
    {
        var opsScriptPath = ResolveOpsScriptPath(_options.OpsScript);

        // 1. 构造 ProcessStartInfo
        var psi = new ProcessStartInfo
        {
            FileName = _options.Executable,
            Arguments = $"-ExecutionPolicy Bypass -NoProfile -File \"{opsScriptPath}\" -Action {action}",
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true,
        };

        // 2. 启动进程
        var process = new Process { StartInfo = psi };
        var stderrBuf = new StringBuilder();
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data != null)
            {
                stderrBuf.AppendLine(e.Data);
            }
        };

        if (!process.Start())
        {
            return new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "ps_start_failed",
                ErrorMessage = "Process.Start returned false",
            };
        }
        process.BeginErrorReadLine();

        // 3. stdin 写 JSON
        try
        {
            if (parameters is not null)
            {
                var json = JsonSerializer.Serialize(parameters);
                await process.StandardInput.WriteLineAsync(json);
            }
        }
        catch (InvalidOperationException)
        {
            // StandardInput 已关闭，忽略（PS 进程已退出）
        }
        finally
        {
            try { process.StandardInput.Close(); } catch { }
        }

        // 4. 等待退出（带超时）
        var timeoutMs = _options.InvokeTimeoutSeconds * 1000;
        var exited = await WaitForExitAsync(process, timeoutMs, ct);
        if (!exited)
        {
            try { process.Kill(entireProcessTree: true); } catch { }
            return new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "ps_timeout",
                ErrorMessage = $"PS 调用超时 ({timeoutMs}ms)",
            };
        }

        // 5. 读 stdout，取最后一行 { 开头的 JSON
        string stdout;
        try
        {
            stdout = await process.StandardOutput.ReadToEndAsync();
        }
        catch (Exception ex)
        {
            return new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "ps_no_json",
                ErrorMessage = $"读取 stdout 失败：{ex.Message}",
            };
        }

        var lastJson = stdout.Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .LastOrDefault(line => line.TrimStart().StartsWith("{"));

        if (string.IsNullOrEmpty(lastJson))
        {
            return new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "ps_no_json",
                ErrorMessage = $"PS 无 JSON 输出。stderr: {stderrBuf}",
            };
        }

        // PS 返回是扁平 hashtable，业务字段（keyword/sent_text/state/window_origin 等）散落在顶层。
        // 先把整行解析为 JsonElement，提取统一字段后，整份原始 JSON 留在 RawJson 供调用方按需深入读取。
        JsonElement root;
        try
        {
            root = JsonSerializer.Deserialize<JsonElement>(lastJson);
        }
        catch (JsonException ex)
        {
            return new PowershellResult
            {
                Success = false,
                Action = action,
                ErrorCode = "ps_no_json",
                ErrorMessage = $"PS JSON 解析失败：{ex.Message}。原始：{lastJson}",
            };
        }

        var result = new PowershellResult
        {
            Action = action,
            RawJson = lastJson,
            Success = root.TryGetProperty("success", out var sProp) && sProp.GetBoolean(),
        };
        if (root.TryGetProperty("error_code", out var ecProp))
        {
            result.ErrorCode = ecProp.GetString();
        }
        if (root.TryGetProperty("error_message", out var emProp))
        {
            result.ErrorMessage = emProp.GetString();
        }
        if (root.TryGetProperty("duration_ms", out var durProp) && durProp.TryGetInt64(out var durMs))
        {
            result.DurationMs = durMs;
        }
        // 透传常用业务字段，避免调用方重复解析
        if (root.TryGetProperty("state", out var stProp))
        {
            result.State = stProp.GetString();
        }

        if (!result.Success && _logger.IsEnabled(LogLevel.Warning))
        {
            _logger.LogWarning("PS {Action} 失败: code={Code} msg={Msg}",
                action, result.ErrorCode, result.ErrorMessage);
        }
        return result;
    }

    /// <summary>相对脚本路径固定以应用程序目录为基准，避免启动器工作目录变化导致找不到脚本。</summary>
    internal static string ResolveOpsScriptPath(string opsScript)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(opsScript);
        return Path.IsPathRooted(opsScript)
            ? Path.GetFullPath(opsScript)
            : Path.GetFullPath(opsScript, AppContext.BaseDirectory);
    }

    /// <summary>
    /// 带超时的等待退出。优先用 .NET 5+ 的 WaitForExitAsync（内部已处理 CancellationToken），
    /// 超时则返回 false（不抛 OperationCanceledException，让调用方决定 Kill 还是返回）。
    /// </summary>
    private static async Task<bool> WaitForExitAsync(Process process, int timeoutMs, CancellationToken externalCt)
    {
        using var timeoutCts = new CancellationTokenSource(timeoutMs);
        using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(externalCt, timeoutCts.Token);
        try
        {
            await process.WaitForExitAsync(linkedCts.Token);
            return true;
        }
        catch (OperationCanceledException)
        {
            // 区分是外部取消还是超时：外部取消往上抛，超时返回 false
            if (externalCt.IsCancellationRequested)
            {
                throw;
            }
            return false;
        }
    }
}

/// <summary>
/// PS 脚本统一返回结构。对应 wecom-ops.ps1 的 Write-Result hashtable。
/// RawJson 保留完整 JSON 字符串，调用方可按需自行 JsonSerializer.Deserialize&lt;JsonElement&gt; 深入读取。
/// </summary>
public sealed class PowershellResult
{
    public bool Success { get; set; }
    public string? Action { get; set; }
    public string? ErrorCode { get; set; }
    public string? ErrorMessage { get; set; }
    public long DurationMs { get; set; }

    /// <summary>get_login_state 透传字段（online/offline）。其他 action 为 null。</summary>
    public string? State { get; set; }

    /// <summary>PS 返回的原始 JSON 字符串（业务字段未结构化映射时供调用方自行解析）。</summary>
    public string? RawJson { get; set; }
}
