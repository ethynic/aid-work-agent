using Serilog;
using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace WeCom.PersonalRpa.Automation.Nodes;

/// <summary>
/// YamlDotNet 反序列化用的 wecom_nodes.yaml 数据模型（结构同 assets/wecom_nodes.yaml）。
/// </summary>
internal sealed class WeComNodesYaml
{
    public MainWindowYaml? Main_window { get; set; }
    public ControlsYaml? Controls { get; set; }
    public QrRegionYaml? Qr_region { get; set; }
    public VisionYaml? Vision { get; set; }
}

internal sealed class MainWindowYaml
{
    public string? Class_name { get; set; }
}

internal sealed class ControlsYaml
{
    public ControlNodeYaml? Search_box { get; set; }
    public ControlNodeYaml? Message_input { get; set; }
    public ButtonNodeYaml? Send_button { get; set; }
}

internal sealed class ControlNodeYaml
{
    public string? Automation_id { get; set; }
    public OffsetYaml? Click_offset { get; set; }
}

internal sealed class ButtonNodeYaml
{
    public string? Name { get; set; }
}

internal sealed class OffsetYaml
{
    public int X { get; set; }
    public int Y { get; set; }
}

internal sealed class QrRegionYaml
{
    public int X { get; set; }
    public int Y { get; set; }
    public int Width { get; set; }
    public int Height { get; set; }
}

internal sealed class VisionYaml
{
    public double Template_match_threshold { get; set; } = 0.85;
    public string? Templates_root { get; set; }
}

/// <summary>
/// wecom_nodes.yaml 加载器（YamlDotNet）。
/// 失败时记录警告并回退到 <see cref="WeComNodesConfig"/> 的硬编码默认值，
/// 保证自动化层在 yaml 丢失 / 格式错误时仍能启动（再由准入验证回填）。
/// </summary>
internal static class NodesConfigLoader
{
    private const string DefaultFileName = "wecom_nodes.yaml";

    /// <summary>从指定路径加载；找不到或解析失败返回 null（调用方走默认值）。</summary>
    public static WeComNodesYaml? Load(string? path)
    {
        string resolved = ResolvePath(path);

        if (!File.Exists(resolved))
        {
            Log.Warning("后端日志：wecom_nodes.yaml 不存在，路径={Path}，使用硬编码默认值", resolved);
            return null;
        }

        try
        {
            var yaml = File.ReadAllText(resolved);
            var deserializer = new DeserializerBuilder()
                .WithNamingConvention(UnderscoredNamingConvention.Instance)
                .IgnoreUnmatchedProperties()
                .Build();

            return deserializer.Deserialize<WeComNodesYaml>(yaml);
        }
        catch (Exception ex)
        {
            // yaml 解析失败属于配置错误，但不阻断启动（走默认值）。
            Log.Warning(ex, "后端日志：wecom_nodes.yaml 解析失败，路径={Path}，使用硬编码默认值", resolved);
            return null;
        }
    }

    private static string ResolvePath(string? path)
    {
        if (!string.IsNullOrWhiteSpace(path) && Path.IsPathRooted(path))
        {
            return path;
        }

        string baseDir = AppContext.BaseDirectory;
        string rel = string.IsNullOrWhiteSpace(path) ? DefaultFileName : path;
        return Path.Combine(baseDir, rel);
    }
}
