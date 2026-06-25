using WeCom.PersonalRpa.ConfigTool;
using Xunit;

namespace WeCom.PersonalRpa.Tests.ConfigTool;

/// <summary>
/// ArgsParser 单元测试。
///
/// 契约来源：Program.cs 三级回退（参数 → 环境变量 → 交互）的第一层。
/// 本测试必须验证：
///   1) --key value / --key=value / -k value 三种形式等价；
///   2) 布尔开关（--yes / --from-env / --help）不接受值；
///   3) 未知参数 → Error，缺值 → Error；
///   4) --poll-interval-seconds 非正整数 → Error。
///
/// 这些是「输入决定行为」的边界，错配会让用户以为传了 client_id 但实际没生效（最危险的 bug）。
/// </summary>
public class ArgsParserTests
{
    [Fact(DisplayName = "--key=value 形式：等价于 --key value")]
    public void Parse_InlineEqual_SplitsKeyAndValue()
    {
        var r = ArgsParser.Parse(new[] { "--client-id=ABC", "--tenant-id=T1" });
        Assert.Null(r.Error);
        Assert.Equal("ABC", r.ClientId);
        Assert.Equal("T1", r.TenantId);
    }

    [Fact(DisplayName = "空格分隔形式正确解析")]
    public void Parse_SpaceSeparated_ParsesAllKnownKeys()
    {
        var r = ArgsParser.Parse(new[]
        {
            "--client-id", "CID",
            "--client-secret", "SECRET",
            "--agent-base-url", "http://x:1",
            "--tenant-id", "TID",
            "--poll-interval-seconds", "15",
            "--output", "/tmp/out.enc"
        });
        Assert.Null(r.Error);
        Assert.Equal("CID", r.ClientId);
        Assert.Equal("SECRET", r.ClientSecret);
        Assert.Equal("http://x:1", r.AgentBaseUrl);
        Assert.Equal("TID", r.TenantId);
        Assert.Equal(15, r.PollIntervalSeconds);
        Assert.Equal("/tmp/out.enc", r.Output);
    }

    [Fact(DisplayName = "布尔开关 -y / --yes / --from-env / -h 互通且不接受 =value")]
    public void Parse_BoolFlags_AreSwitches_AndRejectInlineValue()
    {
        var r = ArgsParser.Parse(new[] { "-y", "--from-env" });
        Assert.Null(r.Error);
        Assert.True(r.Yes);
        Assert.True(r.FromEnv);

        var bad = ArgsParser.Parse(new[] { "--yes=true" });
        Assert.NotNull(bad.Error);
    }

    [Fact(DisplayName = "未知参数返回 Error（防 typo 让用户误以为已传值）")]
    public void Parse_UnknownFlag_ReturnsError()
    {
        var r = ArgsParser.Parse(new[] { "--clientt-id", "X" }); // typo: clientt
        Assert.NotNull(r.Error);
        Assert.Contains("clientt", r.Error!);
    }

    [Fact(DisplayName = "缺值的参数返回 Error")]
    public void Parse_MissingValue_ReturnsError()
    {
        var r = ArgsParser.Parse(new[] { "--client-id" });
        Assert.NotNull(r.Error);
        Assert.Contains("缺少值", r.Error!);
    }

    [Fact(DisplayName = "poll-interval-seconds 非正整数返回 Error")]
    public void Parse_PollInterval_NonPositive_ReturnsError()
    {
        var r1 = ArgsParser.Parse(new[] { "--poll-interval-seconds", "0" });
        Assert.NotNull(r1.Error);
        var r2 = ArgsParser.Parse(new[] { "--poll-interval-seconds", "abc" });
        Assert.NotNull(r2.Error);
    }

    [Fact(DisplayName = "短开关 -h 等价于 --help")]
    public void Parse_ShortHelpFlag_EqualsLong()
    {
        var r = ArgsParser.Parse(new[] { "-h" });
        Assert.Null(r.Error);
        Assert.True(r.Help);
    }

    [Fact(DisplayName = "空参数列表不报错")]
    public void Parse_EmptyArgs_NoError()
    {
        var r = ArgsParser.Parse(System.Array.Empty<string>());
        Assert.Null(r.Error);
        Assert.False(r.Help);
        Assert.False(r.Yes);
        Assert.False(r.FromEnv);
    }
}
