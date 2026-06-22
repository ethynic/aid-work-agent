using Serilog;
using WeCom.PersonalRpa.Automation.Contracts;

namespace WeCom.PersonalRpa.Automation.Nodes;

/// <summary>
/// <see cref="INodesConfig"/> 的 Automation 实现：从 wecom_nodes.yaml 加载节点常量，
/// 加载失败 / 字段缺失时回退到「占位默认值」（带 // TODO 回填自准入验证 标注）。
/// </summary>
public sealed class WeComNodesConfig : INodesConfig
{
    // ============== 占位默认值（yaml 缺失时使用，全部需准入验证回填） ==============
    private const string DefaultMainWindowClassName = "WeWorkWindow"; // TODO 回填自准入验证
    private const string DefaultSearchBoxAutomationId = "search_input"; // TODO 回填自准入验证
    private const string DefaultMessageInputAutomationId = "message_input"; // TODO 回填自准入验证
    private const string DefaultSendButtonName = "发送"; // TODO 回填自准入验证
    private const int DefaultSearchBoxOffsetX = 400; // TODO 回填自准入验证
    private const int DefaultSearchBoxOffsetY = 50;  // TODO 回填自准入验证
    private const int DefaultMessageInputOffsetX = 400; // TODO 回填自准入验证
    private const int DefaultMessageInputOffsetY = 580; // TODO 回填自准入验证
    private const int DefaultQrX = 520;   // TODO 回填自准入验证
    private const int DefaultQrY = 240;   // TODO 回填自准入验证
    private const int DefaultQrW = 200;   // TODO 回填自准入验证
    private const int DefaultQrH = 200;   // TODO 回填自准入验证
    private const double DefaultTemplateThreshold = 0.85;
    private const string DefaultTemplatesRoot = "assets/templates";

    public string MainWindowClassName { get; }
    public string SearchBoxAutomationId { get; }
    public string MessageInputAutomationId { get; }
    public string SendButtonName { get; }
    public Rect QrRegion { get; }
    public Point SearchBoxClickOffset { get; }
    public Point MessageInputClickOffset { get; }
    public double TemplateMatchThreshold { get; }
    public string TemplatesRoot { get; }

    /// <summary>从指定 yaml 路径加载；路径为空走默认输出目录。</summary>
    public WeComNodesConfig(string? yamlPath = null)
    {
        var yaml = NodesConfigLoader.Load(yamlPath);

        MainWindowClassName = Coalesce(yaml?.Main_window?.Class_name, DefaultMainWindowClassName);
        SearchBoxAutomationId = Coalesce(yaml?.Controls?.Search_box?.Automation_id, DefaultSearchBoxAutomationId);
        MessageInputAutomationId = Coalesce(yaml?.Controls?.Message_input?.Automation_id, DefaultMessageInputAutomationId);
        SendButtonName = Coalesce(yaml?.Controls?.Send_button?.Name, DefaultSendButtonName);

        SearchBoxClickOffset = new Point(
            yaml?.Controls?.Search_box?.Click_offset?.X ?? DefaultSearchBoxOffsetX,
            yaml?.Controls?.Search_box?.Click_offset?.Y ?? DefaultSearchBoxOffsetY);

        MessageInputClickOffset = new Point(
            yaml?.Controls?.Message_input?.Click_offset?.X ?? DefaultMessageInputOffsetX,
            yaml?.Controls?.Message_input?.Click_offset?.Y ?? DefaultMessageInputOffsetY);

        QrRegion = new Rect(
            yaml?.Qr_region?.X ?? DefaultQrX,
            yaml?.Qr_region?.Y ?? DefaultQrY,
            yaml?.Qr_region?.Width ?? DefaultQrW,
            yaml?.Qr_region?.Height ?? DefaultQrH);

        double threshold = yaml?.Vision?.Template_match_threshold ?? DefaultTemplateThreshold;
        TemplateMatchThreshold = threshold > 0 && threshold <= 1.0 ? threshold : DefaultTemplateThreshold;

        TemplatesRoot = Coalesce(yaml?.Vision?.Templates_root, DefaultTemplatesRoot);

        Log.Information(
            "后端日志：WeComNodesConfig 加载完成，MainWindowClass={MainCls}, " +
            "SearchBox={SearchId}, QrRegion=({QrX},{QrY},{QrW},{QrH})",
            MainWindowClassName, SearchBoxAutomationId,
            QrRegion.X, QrRegion.Y, QrRegion.Width, QrRegion.Height);
    }

    private static string Coalesce(string? value, string defaultValue)
        => string.IsNullOrWhiteSpace(value) ? defaultValue : value!;
}
