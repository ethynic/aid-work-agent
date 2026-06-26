using System.Text.Json;
using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Protocol;

/// <summary>
/// 协议层共享的 System.Text.Json 选项。
/// camelCase 命名策略与 Python 端 ensure_ascii=False 的字段名一一对齐，
/// 忽略 null 值（与 Pydantic 默认 exclude_none 序列化语义兼容）。
/// </summary>
public static class ProtocolJsonOptions
{
    /// <summary>共享只读 JSON 序列化选项实例。</summary>
    public static readonly JsonSerializerOptions Instance = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNameCaseInsensitive = true,
        Converters =
        {
            new ActionJsonConverter(),
            // 不要在这里放 JsonStringEnumConverter：
            // JsonSerializerOptions.Converters 列表中的转换器优先级高于枚举上的 [JsonConverter] 特性，
            // 会让所有协议枚举（EventType / AccountStatus / ErrorCode 等）退回 PascalCase 序列化，
            // 导致服务端 Pydantic Literal 校验失败。
            // 各协议枚举自行通过 [JsonConverter(typeof(SnakeCaseEnumJsonConverter<>))] 贴特性即可。
        },
    };
}
