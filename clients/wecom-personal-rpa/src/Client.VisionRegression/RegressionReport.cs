// ====================================================================================
// RegressionReport —— 真机视觉回归 YAML 报告 DTO。
//
// 设计：
//   1) 5 个场景各自独立的子报告，每个记录 status / pass / 详情；
//   2) 顶层汇总 pass 计数、企微前台状态、API key 是否可用；
//   3) 序列化为 YAML（YamlDotNet snake_case），便于人读 + diff。
//
// 反向关联：plans/plan-wecom-personal-rpa-vision.md 任务 E（阶段 3B）。
// ====================================================================================

using YamlDotNet.Serialization;
using YamlDotNet.Serialization.NamingConventions;

namespace WeCom.PersonalRpa.VisionRegression;

/// <summary>顶层回归报告。</summary>
public sealed class RegressionReport
{
    /// <summary>报告生成时间戳（本地时区 ISO 8601）。</summary>
    public string GeneratedAt { get; set; } = "";

    /// <summary>客户端主机名。</summary>
    public string HostName { get; set; } = "";

    /// <summary>报告产物目录绝对路径。</summary>
    public string OutDir { get; set; } = "";

    /// <summary>是否成功从 .env 加载到至少一个 API Key。</summary>
    public bool ApiKeysAvailable { get; set; }

    /// <summary>API Key 数量（脱敏后只给数量，不给明文）。</summary>
    public int ApiKeyCount { get; set; }

    /// <summary>企微主窗口是否被检测到（截图自检能否启动前置条件）。</summary>
    public bool WeComMainWindowFound { get; set; }

    /// <summary>视觉模块主配置摘要（脱敏）。</summary>
    public VisionConfigSummary Config { get; set; } = new();

    /// <summary>场景 1：视觉定位准确率。</summary>
    public Scenario1Report Scenario1 { get; set; } = new();

    /// <summary>场景 2：缓存命中率。</summary>
    public Scenario2Report Scenario2 { get; set; } = new();

    /// <summary>场景 3：窗口指纹失效。</summary>
    public Scenario3Report Scenario3 { get; set; } = new();

    /// <summary>场景 4：截图前置校验。</summary>
    public Scenario4Report Scenario4 { get; set; } = new();

    /// <summary>场景 5：失败降级（空 API Key 应抛异常或返回 null，不静默吞）。</summary>
    public Scenario5Report Scenario5 { get; set; } = new();

    /// <summary>汇总通过的场景数（0..5）。</summary>
    public int TotalPassed { get; set; }

    /// <summary>汇总场景总数（5）。</summary>
    public int TotalScenarios { get; set; } = 5;

    /// <summary>顶层结论（pass / partial / fail）。</summary>
    public string Verdict { get; set; } = "unknown";
}

/// <summary>视觉配置脱敏摘要。</summary>
public sealed class VisionConfigSummary
{
    /// <summary>主模型名。</summary>
    public string PrimaryModel { get; set; } = "";

    /// <summary>降级模型名。</summary>
    public string FallbackModel { get; set; } = "";

    /// <summary>API 端点 URL。</summary>
    public string ApiEndpoint { get; set; } = "";

    /// <summary>缓存是否启用。</summary>
    public bool CacheEnabled { get; set; }

    /// <summary>缓存 TTL 小时数。</summary>
    public int CacheTtlHours { get; set; }

    /// <summary>SQLite 缓存文件路径。</summary>
    public string CacheSqlitePath { get; set; } = "";
}

/// <summary>场景状态枚举（字符串便于 YAML 阅读）。</summary>
public static class ScenarioStatus
{
    public const string Pass = "pass";
    public const string Fail = "fail";
    public const string Skipped = "skipped";
    public const string Blocked = "blocked";
}

/// <summary>场景 1：单元素定位结果。</summary>
public sealed class ElementLocateResult
{
    public string ElementType { get; set; } = "";
    public string LabelKeyword { get; set; } = "";
    public string Source { get; set; } = "";
    public string? ModelUsed { get; set; }
    public double Confidence { get; set; }
    public int? BboxX1 { get; set; }
    public int? BboxY1 { get; set; }
    public int? BboxX2 { get; set; }
    public int? BboxY2 { get; set; }
    public bool BboxValid { get; set; }
    public bool InBounds { get; set; }
    public double ElapsedSeconds { get; set; }
    public string? Error { get; set; }
}

/// <summary>场景 1 报告。</summary>
public sealed class Scenario1Report
{
    public string Status { get; set; } = ScenarioStatus.Skipped;
    public string StatusReason { get; set; } = "";
    public int TotalElements { get; set; }
    public int HitCount { get; set; }
    public List<ElementLocateResult> Results { get; set; } = new();
    public string? AnnotatedImagePath { get; set; }
    public string? WindowFingerprint { get; set; }
}

/// <summary>场景 2：单次缓存查询结果。</summary>
public sealed class CacheLookupRecord
{
    public int Attempt { get; set; }
    public string Source { get; set; } = "";
    public double ElapsedMs { get; set; }
    public bool BboxValid { get; set; }
    public string? Error { get; set; }
}

/// <summary>场景 2 报告。</summary>
public sealed class Scenario2Report
{
    public string Status { get; set; } = ScenarioStatus.Skipped;
    public string StatusReason { get; set; } = "";
    public List<CacheLookupRecord> Attempts { get; set; } = new();
    public bool CacheHitOnSecondCall { get; set; }
    public bool FastEnoughAfterHit { get; set; }
}

/// <summary>场景 3 报告。</summary>
public sealed class Scenario3Report
{
    public string Status { get; set; } = ScenarioStatus.Skipped;
    public string StatusReason { get; set; } = "";
    public string? OriginalFingerprint { get; set; }
    public string? ForgedFingerprint { get; set; }
    public string SourceAfterInvalidate { get; set; } = "";
    public bool CacheInvalidated { get; set; }
}

/// <summary>场景 4 报告。</summary>
public sealed class Scenario4Report
{
    public string Status { get; set; } = ScenarioStatus.Skipped;
    public string StatusReason { get; set; } = "";
    public bool CaptureSucceeded { get; set; }
    public string? ExceptionType { get; set; }
    public string? ExceptionMessage { get; set; }
    public string? WindowFingerprint { get; set; }
    public int? WindowWidth { get; set; }
    public int? WindowHeight { get; set; }
}

/// <summary>场景 5 报告。</summary>
public sealed class Scenario5Report
{
    public string Status { get; set; } = ScenarioStatus.Skipped;
    public string StatusReason { get; set; } = "";
    public bool ThrewExpectedException { get; set; }
    public string? ExceptionType { get; set; }
    public string? ExceptionMessage { get; set; }
    public bool ReturnedNull { get; set; }
    public bool DidNotCrash { get; set; }
}

/// <summary>YAML 序列化辅助。</summary>
internal static class ReportSerializer
{
    private static readonly ISerializer Serializer = new SerializerBuilder()
        .WithNamingConvention(UnderscoredNamingConvention.Instance)
        .Build();

    public static string ToYaml(RegressionReport report)
        => Serializer.Serialize(report);
}
