using System.Text.Json;
using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Serialization;

/// <summary>
/// 通用枚举 JSON 转换器：在 PascalCase 枚举名和 snake_case 字符串之间互转。
///
/// 用途：与服务端 Pydantic Literal 字面量对齐。例如：
///   C# EventType.Status  ⇄  JSON "status"
///   C# EventType.ActionResult  ⇄  JSON "action_result"
///   C# AccountStatus.NeedLogin  ⇄  JSON "need_login"
///
/// 替代 .NET 9 才有的 JsonNamingPolicy.SnakeCaseLower（本项目目标 net8.0-windows，不可用）。
/// 比起把枚举值名直接写成 snake_case（如 enum { status, action_result }）更不侵入业务代码。
///
/// 转换规则：
/// - 写出：PascalCase → snake_case（在每对「小写→大写」边界插下划线，再整体小写）。
///   特殊处理连续大写：HttpRequest → http_request（首字母后连续大写按单字处理，避免 h_ttp 这种）。
/// - 读取：先尝试 snake_case 精确匹配，失败再 case-insensitive 匹配原始 PascalCase 名（容错）。
/// </summary>
public sealed class SnakeCaseEnumJsonConverter<T> : JsonConverter<T> where T : struct, Enum
{
    /// <inheritdoc />
    public override T Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.String)
        {
            throw new JsonException(
                $"SnakeCaseEnumJsonConverter: 期望 string token，实为 {reader.TokenType}（枚举 {typeof(T).Name}）");
        }

        var raw = reader.GetString();
        if (string.IsNullOrEmpty(raw)) throw new JsonException($"SnakeCaseEnumJsonConverter: 空字符串不能转为 {typeof(T).Name}");

        // 优先精确匹配（snake_case 输入）
        if (Enum.TryParse<T>(raw, ignoreCase: true, out var exact)) return exact;

        // 失败：尝试反推 snake_case → PascalCase 再匹配（如 "action_result" → "ActionResult"）
        var pascal = SnakeToPascal(raw);
        if (Enum.TryParse<T>(pascal, ignoreCase: true, out var fromSnake)) return fromSnake;

        throw new JsonException(
            $"SnakeCaseEnumJsonConverter: \"{raw}\" 无法转为 {typeof(T).Name}。" +
            $"有效值：{string.Join(", ", Enum.GetNames(typeof(T)))}");
    }

    /// <inheritdoc />
    public override void Write(Utf8JsonWriter writer, T value, JsonSerializerOptions options)
    {
        var pascalName = value.ToString();
        writer.WriteStringValue(PascalToSnake(pascalName));
    }

    /// <summary>PascalCase → snake_case。例：ActionResult → action_result，NeedLogin → need_login。</summary>
    private static string PascalToSnake(string pascal)
    {
        if (string.IsNullOrEmpty(pascal)) return pascal;
        var sb = new System.Text.StringBuilder(pascal.Length + 4);
        for (int i = 0; i < pascal.Length; i++)
        {
            var c = pascal[i];
            if (i > 0 && char.IsUpper(c))
            {
                // 在大写字母前插下划线（除非前一个字符已经是下划线或也是大写后跟小写的边界）
                var prev = pascal[i - 1];
                var hasNextLower = i + 1 < pascal.Length && char.IsLower(pascal[i + 1]);
                if (char.IsLower(prev) || (char.IsUpper(prev) && hasNextLower))
                {
                    sb.Append('_');
                }
            }
            sb.Append(char.ToLowerInvariant(c));
        }
        return sb.ToString();
    }

    /// <summary>snake_case → PascalCase。例：action_result → ActionResult（容错用）。</summary>
    private static string SnakeToPascal(string snake)
    {
        if (string.IsNullOrEmpty(snake)) return snake;
        var parts = snake.Split('_');
        var sb = new System.Text.StringBuilder(snake.Length);
        foreach (var p in parts)
        {
            if (p.Length == 0) continue;
            sb.Append(char.ToUpperInvariant(p[0]));
            if (p.Length > 1) sb.Append(p, 1, p.Length - 1);
        }
        return sb.ToString();
    }
}
