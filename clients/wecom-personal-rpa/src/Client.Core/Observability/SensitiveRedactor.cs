using System.Text.RegularExpressions;

namespace WeCom.PersonalRpa.Core.Observability;

/// <summary>
/// 敏感字段脱敏器。用正则剔除 secret / token / signature / 绝对路径，
/// 用于日志与错误 debug 字段（protocol.md §A.8 / backend_dev.md sanitize_error_info）。
/// </summary>
public sealed class SensitiveRedactor
{
    /// <summary>默认脱敏正则集合（按 backend_dev.md 思路扩展）。</summary>
    public static readonly IReadOnlyList<Regex> DefaultPatterns = new Regex[]
    {
        // secret / api key / token 形如  key=xxx, key:xxx, "key":"xxx"
        new(@"(?i)(secret|api[_-]?key|access[_-]?token|refresh[_-]?token|bearer)[""\s:=]+[^\s,""']+"),
        // signature
        new(@"(?i)signature[""\s:=]+[0-9a-fA-F]{16,}"),
        // Windows 绝对路径
        new(@"[A-Za-z]:\\[^\s""']*"),
        // POSIX 绝对路径
        new(@"/(?:Users|home|root|var|etc|opt|tmp)[^\s""']*"),
    }.Select(r => new Regex(r.ToString(), RegexOptions.Compiled | RegexOptions.CultureInvariant)).ToArray();

    private readonly IReadOnlyList<Regex> _patterns;

    /// <summary>用默认正则集合构造。</summary>
    public SensitiveRedactor() : this(DefaultPatterns)
    {
    }

    /// <summary>用自定义正则集合构造。</summary>
    public SensitiveRedactor(IReadOnlyList<Regex> patterns)
    {
        _patterns = patterns ?? throw new ArgumentNullException(nameof(patterns));
    }

    /// <summary>
    /// 脱敏输入文本。命中模式的部分替换为对应键名 + =***。
    /// </summary>
    public string Redact(string? input)
    {
        if (string.IsNullOrEmpty(input)) return input ?? string.Empty;
        var result = input;
        foreach (var p in _patterns)
        {
            result = p.Replace(result, m =>
            {
                var raw = m.Value;
                // 取首段键名（到 = : 空格 为止）
                var sepIdx = raw.IndexOfAny(new[] { '=', ':', ' ', '\t', '"' });
                var key = sepIdx > 0 ? raw[..sepIdx] : raw;
                return key + "=***";
            });
        }
        return result;
    }
}
