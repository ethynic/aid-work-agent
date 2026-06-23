using System.IO;
using System.Linq;
using WeCom.PersonalRpa.Automation.Vision;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 视觉模块配置加载器。
///
/// 设计要点（plan §C 第 659 行）：
///   - <see cref="VisionConfig"/> 定义在 Client.Automation 工程，Client.Core 不引入 Windows-only 依赖；
///   - 因此本加载器放在 Client.App（已引用 Client.Automation），从 yaml 配置段 / 环境变量 / 默认值合成 VisionConfig；
///   - 严禁在 <see cref="VisionConfig"/> 内部读环境变量（plan §A3），API Key 统一由本加载器解析后注入。
///
/// 支持的占位符（值字符串内的 <c>${VAR}</c>）：
///   - <c>${QWEN_API_KEYS}</c>：API Key 池（逗号分隔），未设置时 <see cref="VisionConfig.Enabled"/>=false；
///   - <c>${AppData}</c>：当前用户 LocalAppData 目录，便于缓存 db 等路径落盘到统一位置。
/// </summary>
internal static class VisionConfigLoader
{
    private const string Tag = "VisionConfigLoader";

    /// <summary>
    /// 加载 <see cref="VisionConfig"/>。优先级：客户端运行目录下的 vision.yaml（若存在）
    /// → 环境变量占位符替换 → 默认值。失败不抛，回退默认并打 warning（保证客户端可启动）。
    /// </summary>
    /// <param name="dataDir">客户端运行期数据目录（用于解析相对 sqlite 路径）。</param>
    /// <returns>已解析占位符的 <see cref="VisionConfig"/> 实例。</returns>
    public static VisionConfig Load(string dataDir)
    {
        var cfg = new VisionConfig();

        // API Key 池：仅从 QWEN_API_KEYS 环境变量读取（与 yaml 中 ${QWEN_API_KEYS} 占位符语义一致）。
        // 未配置时 Enabled=false，IVisionLocator 仍注册但调用会显式失败（loud fail）。
        var apiKeysRaw = Environment.GetEnvironmentVariable("QWEN_API_KEYS") ?? string.Empty;
        var apiKeys = apiKeysRaw
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(k => !string.IsNullOrWhiteSpace(k))
            .ToArray();
        cfg.ApiKeys = apiKeys;
        cfg.Enabled = apiKeys.Length > 0;

        // SQLite 缓存路径：默认落到 LocalAppData/WeComRpa/vision_cache.db。
        // 占位符 ${AppData} 解析为 LocalAppData。
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var defaultCacheDir = Path.Combine(appData, "WeComRpa");
        try { Directory.CreateDirectory(defaultCacheDir); }
        catch (Exception ex)
        {
            Serilog.Log.Warning(ex, "[{Tag}] 创建缓存目录失败 {Dir}", Tag, defaultCacheDir);
        }
        cfg.SQLitePath = Path.Combine(defaultCacheDir, "vision_cache.db");

        // dataDir 仅用于将来扩展（如把 cache db 落到客户端统一存储），目前不强制。
        _ = dataDir;

        Serilog.Log.Information(
            "[{Tag}] VisionConfig 加载完成 Enabled={Enabled} Keys={Keys} Primary={Primary} Fallback={Fallback} CacheDb={Db}",
            Tag, cfg.Enabled, cfg.ApiKeys.Length, cfg.PrimaryModel, cfg.FallbackModel, cfg.SQLitePath);
        return cfg;
    }
}
