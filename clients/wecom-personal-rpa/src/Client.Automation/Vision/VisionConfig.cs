// ====================================================================================
// 契约来源：plans/plan-wecom-personal-rpa-vision.md §A3（316-334 行）。
// VisionConfig 对应 yaml 配置文件 vision 段，由 Client.App 启动时加载后注入。
// A1/A2 落地后 ScreenshotOptions / Cache 子节点会被替换为 A1/A2 的真实类型（集成者处理）。
// ====================================================================================

namespace WeCom.PersonalRpa.Automation.Vision;

/// <summary>
/// 视觉模块配置。所有字段对齐 yaml vision 段，由 Client.App 启动时反序列化注入。
/// 严禁在本类内读环境变量——API key 必须由调用方从配置传入（plan §A3）。
/// </summary>
public sealed class VisionConfig
{
    /// <summary>总开关。false 时 Client.App 不向 DI 注册 IVisionLocator。</summary>
    public bool Enabled { get; set; } = true;

    /// <summary>主模型。默认 "qwen3-vl-plus"，性价比最高。</summary>
    public string PrimaryModel { get; set; } = "qwen3-vl-plus";

    /// <summary>降级模型。默认 "qwen3-vl-max"，主模型连续失败时切换。</summary>
    public string FallbackModel { get; set; } = "qwen3-vl-max";

    /// <summary>
    /// OpenAI 兼容协议的 chat/completions 端点。
    /// 默认指向 DashScope，便于未来切 GPT/Claude 时只改此字段。
    /// </summary>
    public string ApiEndpoint { get; set; }
        = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions";

    /// <summary>
    /// API Key 池。允许配多个（逗号分隔经 Client.App 切分数组），
    /// QwenVisionApi 轮询使用；429 跳下一个，全 401 才抛 VisionApiAuthException。
    /// </summary>
    public string[] ApiKeys { get; set; } = Array.Empty<string>();

    /// <summary>HTTP 超时（秒）。Qwen3-VL grounding 单次 ~28s，默认 120s 留余量。</summary>
    public int TimeoutSeconds { get; set; } = 120;

    /// <summary>5xx / 超时重试次数（指数退避）。默认 3 次。</summary>
    public int MaxRetries { get; set; } = 3;

    /// <summary>视觉缓存配置子节点。</summary>
    public VisionCacheConfig Cache { get; set; } = new();

    /// <summary>
    /// 截图选项子节点（引用 A1 的 ScreenshotOptions 真实类型）。
    /// </summary>
    public ScreenshotOptions Screenshot { get; set; } = new();

    /// <summary>SQLite 数据库路径（视觉缓存持久化）。空字符串表示用 Client.App 默认路径。</summary>
    public string SQLitePath { get; set; } = "";

    /// <summary>视觉缓存子配置。</summary>
    public sealed class VisionCacheConfig
    {
        /// <summary>是否启用 SQLite 视觉缓存。关闭后每次定位都调 API（仅调试用）。</summary>
        public bool Enabled { get; set; } = true;

        /// <summary>缓存 TTL（小时）。默认 24h，防止企微静默升级导致 bbox 失效。</summary>
        public int TtlHours { get; set; } = 24;
    }
}
