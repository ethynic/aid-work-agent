using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.ConfigTool;

/// <summary>
/// Client.ConfigTool CLI 入口。
///
/// 交互模式（无参数）：逐项引导用户输入 client_id / client_secret / agent_base_url / tenant_id / poll_interval。
/// 参数模式：--client-id / --client-secret / --agent-base-url / --tenant-id / --poll-interval-seconds / --output / --from-env / --yes
///
/// 安全约束：
///   - client_secret 输入时不回显（ReadMasked）；
///   - 完成后立即清零内存中的 secret（best-effort，string 是不可变的，但至少避免长期驻留）；
///   - 不写明文临时文件。
/// </summary>
internal static class Program
{
    private const string DefaultBaseUrl = "http://localhost:8000";
    private const int DefaultPollSeconds = 30;

    // 环境变量名（--from-env 使用）
    private const string EnvClientId = "CLIENT_ID";
    private const string EnvClientSecret = "CLIENT_SECRET";
    private const string EnvAgentBaseUrl = "AGENT_BASE_URL";
    private const string EnvTenantId = "TENANT_ID";
    private const string EnvPollSeconds = "POLL_INTERVAL_SECONDS";

    private static async Task<int> Main(string[] args)
    {
        try
        {
            return await RunAsync(args, Console.In, Console.Out, Console.Error);
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("[CANCEL] 用户中断。");
            return 3;
        }
    }

    /// <summary>可测试入口（注入 TextReader/TextWriter）。</summary>
    internal static async Task<int> RunAsync(string[] args, TextReader stdin, TextWriter stdout, TextWriter stderr)
    {
        var parsed = ArgsParser.Parse(args);

        if (parsed.Help)
        {
            PrintHelp(stdout);
            return 0;
        }
        if (parsed.Error != null)
        {
            stderr.WriteLine($"[ERROR] 参数错误：{parsed.Error}");
            PrintHelp(stderr);
            return 4;
        }

        // 默认输出路径必须等于 Client.App 实际读取路径（见 ClientAppPaths.ConfigFilePath）。
        // 2026-06-25 事故：曾经默认写到 %LOCALAPPDATA%\WeComRpa\client_config.enc，
        // 与客户端读取路径 %LOCALAPPDATA%\WeComPersonalRpa\Client.App\data\client_config.enc 不一致，
        // 导致客户端加载到旧配置（错的 BaseUrl），WebSocket 连不上。
        var outputPath = string.IsNullOrWhiteSpace(parsed.Output)
            ? ClientAppPaths.ConfigFilePath
            : Path.GetFullPath(parsed.Output);

        stdout.WriteLine("=== 企业微信个人账号 RPA 客户端 - 配置写入工具 ===");
        stdout.WriteLine();
        stdout.WriteLine($"本工具会将客户端配置加密写入 {outputPath}");
        stdout.WriteLine("加密后仅当前 Windows 用户可解密（DPAPI CurrentUser scope）");
        stdout.WriteLine();

        // 收集输入（参数 / 环境变量 / 交互式 三级回退）
        var inputs = await CollectInputsAsync(parsed, stdin, stdout, stderr);
        if (inputs == null)
        {
            return 5; // 必填缺失
        }

        // 构造 ClientOptions（绝不让 secret 落到 stdout）
        var options = new ClientOptions
        {
            AgentBaseUrl = inputs.AgentBaseUrl,
            ClientId = inputs.ClientId,
            ClientSecret = inputs.ClientSecret,
            TenantId = inputs.TenantId,
            PollIntervalSeconds = inputs.PollIntervalSeconds,
            StoragePath = Path.GetDirectoryName(outputPath) ?? string.Empty,
        };

        try
        {
            var writer = new ConfigWriter();
            // 交互模式下需要 confirm 回调（输出到 stdout，从 stdin 读 y/N）
            Func<string, bool>? confirm = parsed.Yes ? null : (msg =>
            {
                stdout.Write(msg);
                var line = stdin.ReadLine();
                return !string.IsNullOrEmpty(line)
                       && (line.Trim().Equals("y", StringComparison.OrdinalIgnoreCase)
                           || line.Trim().Equals("yes", StringComparison.OrdinalIgnoreCase));
            });

            return await writer.WriteAsync(options, outputPath, parsed.Yes, stdout, confirm, CancellationToken.None);
        }
        finally
        {
            // best-effort 清零内存中的 secret（string 不可变，但至少解除引用）
            inputs.ClientSecret = new string('x', inputs.ClientSecret.Length);
        }
    }

    /// <summary>收集输入：优先参数，其次 --from-env 环境变量，最后交互式提示。</summary>
    private static async Task<InputData?> CollectInputsAsync(
        ParsedArgs parsed, TextReader stdin, TextWriter stdout, TextWriter stderr)
    {
        string? clientId = parsed.ClientId;
        string? clientSecret = parsed.ClientSecret;
        string? agentBaseUrl = parsed.AgentBaseUrl;
        string? tenantId = parsed.TenantId;
        int? pollSeconds = parsed.PollIntervalSeconds;

        if (parsed.FromEnv)
        {
            clientId ??= Environment.GetEnvironmentVariable(EnvClientId);
            clientSecret ??= Environment.GetEnvironmentVariable(EnvClientSecret);
            agentBaseUrl ??= Environment.GetEnvironmentVariable(EnvAgentBaseUrl);
            tenantId ??= Environment.GetEnvironmentVariable(EnvTenantId);
            var pollStr = Environment.GetEnvironmentVariable(EnvPollSeconds);
            if (pollStr != null && int.TryParse(pollStr, out var pe) && pe > 0)
            {
                pollSeconds ??= pe;
            }
        }

        // 判断是否必填项缺失 → 缺失则进入交互模式（stdin 是 TTY 时）
        var missingRequired = string.IsNullOrEmpty(clientId)
                              || string.IsNullOrEmpty(clientSecret)
                              || string.IsNullOrEmpty(agentBaseUrl)
                              || string.IsNullOrEmpty(tenantId);

        // stdin 被重定向（管道/自动化）→ 无法交互，必填项缺失即失败
        if (missingRequired && Console.IsInputRedirected)
        {
            stderr.WriteLine("[ERROR] 非交互模式（stdin 重定向）且必填参数缺失。请用 --client-id / --client-secret / --agent-base-url / --tenant-id 或 --from-env。");
            return null;
        }

        if (missingRequired && !Console.IsInputRedirected)
        {
            stdout.WriteLine("请输入以下信息（从平台后台「RPA 绑定管理」→「新增绑定」或「轮换密钥」获取）：");
            stdout.WriteLine();

            clientId = await PromptAsync(stdin, stdout, "  client_id", clientId, mask: false);
            clientSecret = await PromptAsync(stdin, stdout, "  client_secret（明文，输入时不显示）", clientSecret, mask: true);
            agentBaseUrl = await PromptAsync(stdin, stdout, $"  agent_base_url [默认 {DefaultBaseUrl}]", agentBaseUrl, mask: false);
            if (string.IsNullOrEmpty(agentBaseUrl)) agentBaseUrl = DefaultBaseUrl;
            tenantId = await PromptAsync(stdin, stdout, "  tenant_id", tenantId, mask: false);
            var pollStr = await PromptAsync(stdin, stdout, $"  poll_interval_seconds [默认 {DefaultPollSeconds}]",
                pollSeconds?.ToString(System.Globalization.CultureInfo.InvariantCulture), mask: false);
            if (string.IsNullOrEmpty(pollStr))
            {
                pollSeconds = DefaultPollSeconds;
            }
            else if (!int.TryParse(pollStr, out var pe) || pe <= 0)
            {
                stderr.WriteLine($"[ERROR] poll_interval_seconds 必须是正整数，得到：{pollStr}");
                return null;
            }
            else
            {
                pollSeconds = pe;
            }
        }

        // 最终校验必填
        if (string.IsNullOrEmpty(clientId))
        {
            stderr.WriteLine("[ERROR] client_id 不能为空。");
            return null;
        }
        if (string.IsNullOrEmpty(clientSecret))
        {
            stderr.WriteLine("[ERROR] client_secret 不能为空。");
            return null;
        }
        if (string.IsNullOrEmpty(agentBaseUrl))
        {
            stderr.WriteLine("[ERROR] agent_base_url 不能为空。");
            return null;
        }
        if (string.IsNullOrEmpty(tenantId))
        {
            stderr.WriteLine("[ERROR] tenant_id 不能为空。");
            return null;
        }
        if (!Uri.TryCreate(agentBaseUrl, UriKind.Absolute, out _))
        {
            stderr.WriteLine($"[ERROR] agent_base_url 不是合法 URL：{agentBaseUrl}");
            return null;
        }

        return new InputData
        {
            ClientId = clientId!,
            ClientSecret = clientSecret!,
            AgentBaseUrl = agentBaseUrl!,
            TenantId = tenantId!,
            PollIntervalSeconds = pollSeconds ?? DefaultPollSeconds,
        };
    }

    /// <summary>提示一行；mask=true 时读字符回显 *，回车结束。已有 defaultValue 则直接复用。</summary>
    private static async Task<string> PromptAsync(
        TextReader stdin, TextWriter stdout, string label, string? defaultValue, bool mask)
    {
        stdout.Write($"{label}: ");
        // 已有默认值（来自 --from-env / 参数）→ 不再问，直接 echo（不 echo secret）
        if (!string.IsNullOrEmpty(defaultValue))
        {
            if (!mask) stdout.WriteLine(defaultValue);
            else stdout.WriteLine("(沿用环境变量/参数提供的值)");
            return defaultValue;
        }
        stdout.Flush();

        if (mask)
        {
            // 不回显模式：从 Console 直接读键（无法注入测试，但 secret 安全优先）
            // 若 stdin 重定向（测试/管道）则回退到 ReadLine（不回显但也不星号）
            if (Console.IsInputRedirected)
            {
                var line = stdin.ReadLine();
                return line ?? string.Empty;
            }
            var sb = new System.Text.StringBuilder();
            while (true)
            {
                var key = Console.ReadKey(intercept: true);
                if (key.Key == ConsoleKey.Enter) break;
                if (key.Key == ConsoleKey.Backspace && sb.Length > 0)
                {
                    sb.Remove(sb.Length - 1, 1);
                    stdout.Write("\b \b");
                }
                else if (!char.IsControl(key.KeyChar))
                {
                    sb.Append(key.KeyChar);
                    stdout.Write('*');
                }
            }
            stdout.WriteLine();
            return sb.ToString();
        }
        else
        {
            var line = await stdin.ReadLineAsync();
            return line?.Trim() ?? string.Empty;
        }
    }

    private static void PrintHelp(TextWriter w)
    {
        w.WriteLine("Client.ConfigTool - 企业微信个人账号 RPA 客户端配置写入工具");
        w.WriteLine();
        w.WriteLine("用法：");
        w.WriteLine("  Client.ConfigTool.exe                              # 交互模式");
        w.WriteLine("  Client.ConfigTool.exe --client-id ID --client-secret SECRET \\");
        w.WriteLine("      --agent-base-url URL --tenant-id TENANT        # 参数模式");
        w.WriteLine("  Client.ConfigTool.exe --from-env                   # 从环境变量读取");
        w.WriteLine();
        w.WriteLine("参数：");
        w.WriteLine("  --client-id <ID>              客户端 ID（必填）");
        w.WriteLine("  --client-secret <SECRET>      客户端密钥（必填，明文输入）");
        w.WriteLine("  --agent-base-url <URL>        Agent 服务端基础 URL（默认 http://localhost:8000）");
        w.WriteLine("  --tenant-id <ID>              租户 ID（必填）");
        w.WriteLine("  --poll-interval-seconds <N>   轮询间隔秒（默认 30）");
        w.WriteLine("  --output <PATH>               输出路径（默认 %LOCALAPPDATA%\\WeComPersonalRpa\\Client.App\\data\\client_config.enc，即 Client.App 实际读取路径）");
        w.WriteLine("  --from-env                    从 CLIENT_ID/CLIENT_SECRET/AGENT_BASE_URL/TENANT_ID/POLL_INTERVAL_SECONDS 读取");
        w.WriteLine("  --yes, -y                     覆盖现有文件时不询问确认");
        w.WriteLine("  --help, -h                    显示帮助");
        w.WriteLine();
        w.WriteLine("环境变量（仅 --from-env 启用）：");
        w.WriteLine($"  {EnvClientId}, {EnvClientSecret}, {EnvAgentBaseUrl}, {EnvTenantId}, {EnvPollSeconds}");
    }

    private sealed class InputData
    {
        public string ClientId { get; set; } = string.Empty;
        public string ClientSecret { get; set; } = string.Empty;
        public string AgentBaseUrl { get; set; } = string.Empty;
        public string TenantId { get; set; } = string.Empty;
        public int PollIntervalSeconds { get; set; } = DefaultPollSeconds;
    }
}
