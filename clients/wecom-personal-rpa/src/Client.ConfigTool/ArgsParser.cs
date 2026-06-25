using System;
using System.Collections.Generic;

namespace WeCom.PersonalRpa.ConfigTool;

/// <summary>
/// 极简参数解析器（避免引入 System.CommandLine 依赖）。
///
/// 支持形式：--key value / --key=value / -k value。
/// 不支持的输入返回 <see cref="Error"/>，帮助信息由调用方决定输出。
/// </summary>
internal sealed class ParsedArgs
{
    public string? ClientId { get; set; }
    public string? ClientSecret { get; set; }
    public string? AgentBaseUrl { get; set; }
    public string? TenantId { get; set; }
    public int? PollIntervalSeconds { get; set; }
    public string? Output { get; set; }
    public bool FromEnv { get; set; }
    public bool Yes { get; set; }
    public bool Help { get; set; }
    public string? Error { get; set; }
}

internal static class ArgsParser
{
    private static readonly IReadOnlyDictionary<string, string> LongShort = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
        ["--client-id"] = "client-id",
        ["--client-secret"] = "client-secret",
        ["--agent-base-url"] = "agent-base-url",
        ["--tenant-id"] = "tenant-id",
        ["--poll-interval-seconds"] = "poll-interval-seconds",
        ["--output"] = "output",
        ["--from-env"] = "from-env",
        ["--yes"] = "yes",
        ["-y"] = "yes",
        ["--help"] = "help",
        ["-h"] = "help",
    };

    public static ParsedArgs Parse(string[] args)
    {
        var result = new ParsedArgs();
        for (var i = 0; i < args.Length; i++)
        {
            var a = args[i];

            // --key=value 形式
            var eq = a.IndexOf('=');
            string key, value;
            bool hasInlineValue = false;
            if (eq >= 0 && a.StartsWith("--", StringComparison.Ordinal))
            {
                key = a[..eq];
                value = a[(eq + 1)..];
                hasInlineValue = true;
            }
            else
            {
                key = a;
                value = string.Empty;
            }

            if (!LongShort.TryGetValue(key, out var canonical))
            {
                result.Error = $"未知参数：{a}";
                return result;
            }

            // 布尔开关
            if (canonical == "from-env" || canonical == "yes" || canonical == "help")
            {
                if (hasInlineValue)
                {
                    result.Error = $"参数 {key} 不接受值";
                    return result;
                }
                switch (canonical)
                {
                    case "from-env": result.FromEnv = true; break;
                    case "yes": result.Yes = true; break;
                    case "help": result.Help = true; break;
                }
                continue;
            }

            // 需要 value 的参数
            if (!hasInlineValue)
            {
                if (i + 1 >= args.Length)
                {
                    result.Error = $"参数 {key} 缺少值";
                    return result;
                }
                value = args[++i];
            }

            switch (canonical)
            {
                case "client-id":
                    result.ClientId = value;
                    break;
                case "client-secret":
                    result.ClientSecret = value;
                    break;
                case "agent-base-url":
                    result.AgentBaseUrl = value;
                    break;
                case "tenant-id":
                    result.TenantId = value;
                    break;
                case "poll-interval-seconds":
                    if (!int.TryParse(value, out var pe) || pe <= 0)
                    {
                        result.Error = $"--poll-interval-seconds 必须是正整数，得到：{value}";
                        return result;
                    }
                    result.PollIntervalSeconds = pe;
                    break;
                case "output":
                    result.Output = value;
                    break;
            }
        }

        return result;
    }
}
