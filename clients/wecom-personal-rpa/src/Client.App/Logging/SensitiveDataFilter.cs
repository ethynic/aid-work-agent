using System.Text.RegularExpressions;
using Serilog.Core;
using Serilog.Events;

namespace WeCom.PersonalRpa.App.Logging;

/// <summary>
/// Serilog 脱敏过滤器 + Enricher（设计文档 §9.4 / 对齐 Python 端 _sanitize_debug）。
///
/// 应用范围：渲染后的消息文本 + 字符串属性 + Exception.Message。剔除以下敏感信息：
/// <list type="bullet">
///   <item><c>secret=xxx</c> / <c>api_key=xxx</c> / <c>token=xxx</c> / <c>signature=xxx</c>
///     （含中英文冒号 / 空格分隔） → 替换为 <c>***</c></item>
///   <item>Windows 绝对路径（<c>C:\...</c>）/ POSIX 绝对路径（<c>/...</c>） → 替换为 <c>***</c></item>
///   <item>base64 PNG 二维码（长 base64 + 可能含 <c>iVBORw0KG</c> 前缀） → 替换为 <c>&lt;qr_base64_omitted&gt;</c></item>
/// </list>
///
/// 设计：实现 <see cref="ILogEventFilter"/>（返回 true 保留事件，副作用式替换渲染消息 / 属性）
/// 同时实现 <see cref="ILogEventEnricher"/>（用于 Enrich.With）。在 Program/App 配置中通过
/// <c>.Filter.With(new SensitiveDataFilter())</c> 启用。
/// </summary>
public sealed class SensitiveDataFilter : ILogEventFilter, ILogEventEnricher
{
    // 与 Python 端 _SENSITIVE_PATTERNS 等价
    private static readonly Regex SecretPattern = new(
        @"(secret|api[_-]?key|token|signature)[""\s:=]+[^\s,""']+",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private static readonly Regex AbsolutePathPattern = new(
        @"([A-Za-z]:\\[^\s""']+|/[^\s""']+)",
        RegexOptions.Compiled);

    // base64 PNG 二维码：典型的长 base64 字符串（≥ 100 字符），可能以 iVBORw0KG 开头（PNG magic）
    private static readonly Regex Base64QrPattern = new(
        @"iVBORw0KG[A-Za-z0-9+/=]{100,}",
        RegexOptions.Compiled);

    /// <summary>
    /// 静态脱敏函数，可在 Serilog 配置外的地方复用（如直接对字符串清洗）。
    /// </summary>
    public static string Sanitize(string? text)
    {
        if (string.IsNullOrEmpty(text)) return text ?? string.Empty;
        text = SecretPattern.Replace(text, "***");
        text = AbsolutePathPattern.Replace(text, "***");
        text = Base64QrPattern.Replace(text, "<qr_base64_omitted>");
        return text;
    }

    /// <summary>
    /// ILogEventEnricher 实现：把渲染后的 sanitized 消息写入 <c>MessageSanitized</c> 属性，
    /// 同时把 sanitized Exception 整段（含 type + message + stacktrace）写入 <c>ExceptionSanitized</c> 属性，
    /// 便于 sink 端用 <c>{MessageSanitized:lj}</c> / <c>{ExceptionSanitized}</c> 替代原占位符输出。
    /// 不修改原 MessageTemplate / Message，避免破坏 sink 对模板的依赖。
    /// </summary>
    public void Enrich(LogEvent logEvent, ILogEventPropertyFactory propertyFactory)
    {
        var rendered = logEvent.RenderMessage();
        var sanitized = Sanitize(rendered);
        logEvent.AddOrUpdateProperty(propertyFactory.CreateProperty("MessageSanitized", sanitized));

        // 同步对字符串属性做脱敏（直接覆盖原 property）
        var keys = new List<string>(logEvent.Properties.Count);
        foreach (var kv in logEvent.Properties)
        {
            if (kv.Value is ScalarValue sv && sv.Value is string s)
            {
                keys.Add(kv.Key);
            }
        }
        foreach (var k in keys)
        {
            if (logEvent.Properties[k] is ScalarValue sv && sv.Value is string s)
            {
                var s2 = Sanitize(s);
                if (s2 != s)
                {
                    logEvent.AddOrUpdateProperty(propertyFactory.CreateProperty(k, s2));
                }
            }
        }

        // P0-3：Exception 整段（含 type + Message + StackTrace） sanitized 版本。
        // outputTemplate 用 {ExceptionSanitized} 替代 {Exception}，确保 stacktrace 内
        // 出现的 base64/secret/路径也被脱敏。
        if (logEvent.Exception is not null)
        {
            var exStr = logEvent.Exception.ToString();
            if (!string.IsNullOrEmpty(exStr))
            {
                var exSan = Sanitize(exStr);
                if (exSan != exStr)
                {
                    logEvent.AddOrUpdateProperty(propertyFactory.CreateProperty("ExceptionSanitized", exSan));
                }
                else
                {
                    // 未变化也填一份，避免 outputTemplate 的 {ExceptionSanitized} 渲染成 "null"
                    logEvent.AddOrUpdateProperty(propertyFactory.CreateProperty("ExceptionSanitized", exStr));
                }
            }
        }
        else
        {
            // 无 Exception 也填空串，保证 outputTemplate 渲染稳定（避免属性缺失显示 "null"）
            logEvent.AddOrUpdateProperty(propertyFactory.CreateProperty("ExceptionSanitized", string.Empty));
        }
    }

    /// <summary>
    /// ILogEventFilter 实现：始终返回 true（保留事件）。Filter 用作 Sink 之前的 hook
    /// 不可单独修改 LogEvent，建议把 Enricher 注册方式更优。
    /// 这里保留以兼容 <c>.Filter.With(...)</c> 调用，仅 no-op。
    /// </summary>
    public bool IsEnabled(LogEvent logEvent) => true;
}
