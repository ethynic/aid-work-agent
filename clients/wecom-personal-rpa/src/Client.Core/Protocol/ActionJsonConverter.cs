using System.Text.Json;
using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 出站动作判别联合的自定义 JSON 转换器。
/// 读取时按 type 字段分发到 SendTextAction / SendImageAction / SendFileAction / NoopAction / HandoffAction；
/// 写入时按具体子类默认序列化（已带 [JsonPropertyName("type")] 与各自字段）。
/// </summary>
internal sealed class ActionJsonConverter : JsonConverter<RpaAction>
{
    /// <inheritdoc />
    public override RpaAction? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.StartObject)
        {
            throw new JsonException("RpaAction 必须是 JSON 对象");
        }

        using var doc = JsonDocument.ParseValue(ref reader);
        if (!doc.RootElement.TryGetProperty("type", out var typeProp))
        {
            throw new JsonException("RpaAction 缺少 type 判别字段");
        }

        var type = typeProp.GetString();
        // 使用 camelCase 兼容选项反序列化具体子类，避免与 options 中本转换器递归注册冲突。
        var subOptions = new JsonSerializerOptions(options) { Converters = { } };
        foreach (var c in options.Converters)
        {
            if (c is not ActionJsonConverter)
            {
                subOptions.Converters.Add(c);
            }
        }

        return type switch
        {
            ActionTypeNames.SendText => JsonSerializer.Deserialize<SendTextAction>(doc.RootElement.GetRawText(), subOptions),
            ActionTypeNames.SendImage => JsonSerializer.Deserialize<SendImageAction>(doc.RootElement.GetRawText(), subOptions),
            ActionTypeNames.SendFile => JsonSerializer.Deserialize<SendFileAction>(doc.RootElement.GetRawText(), subOptions),
            ActionTypeNames.Noop => JsonSerializer.Deserialize<NoopAction>(doc.RootElement.GetRawText(), subOptions),
            ActionTypeNames.Handoff => JsonSerializer.Deserialize<HandoffAction>(doc.RootElement.GetRawText(), subOptions),
            _ => throw new JsonException($"未知的 RpaAction type: {type}"),
        };
    }

    /// <inheritdoc />
    public override void Write(Utf8JsonWriter writer, RpaAction value, JsonSerializerOptions options)
    {
        // 直接按具体类型序列化（子类已声明 type 与各字段的 [JsonPropertyName]），
        // 避免基类 abstract Type 属性导致输出顺序异常。
        var subOptions = new JsonSerializerOptions(options) { Converters = { } };
        foreach (var c in options.Converters)
        {
            if (c is not ActionJsonConverter)
            {
                subOptions.Converters.Add(c);
            }
        }

        switch (value)
        {
            case SendTextAction t:
                JsonSerializer.Serialize(writer, t, subOptions);
                break;
            case SendImageAction i:
                JsonSerializer.Serialize(writer, i, subOptions);
                break;
            case SendFileAction f:
                JsonSerializer.Serialize(writer, f, subOptions);
                break;
            case NoopAction n:
                JsonSerializer.Serialize(writer, n, subOptions);
                break;
            case HandoffAction h:
                JsonSerializer.Serialize(writer, h, subOptions);
                break;
            default:
                throw new JsonException($"无法序列化的 RpaAction 子类: {value?.GetType().FullName}");
        }
    }
}
