using System.IO;
using System.Threading;
using System.Threading.Tasks;
using WeCom.PersonalRpa.ConfigTool;
using WeCom.PersonalRpa.Core.Config;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Config;

/// <summary>
/// ConfigWriter 单元测试。
///
/// 契约来源：本工具填补「无配置写入工具」空白——平台后台给用户的 yaml 片段是误导，客户端
/// 只读 EncryptedClientConfig（src/Client.App/Services/ClientOptionsLoader.cs）。
/// 本测试必须验证：
///   1) ConfigWriter 写入的文件能被 EncryptedClientConfig.LoadAsync 正确读回（核心 bug 直接证据）；
///   2) 覆盖 confirm 流程：默认调 confirm 回调，回调 false 不写；
///   3) secret 不出现在 stdout（敏感信息脱敏）。
/// </summary>
public class ConfigWriterTests
{
    private sealed class CapturingWriter : StringWriter
    {
        public bool ContainsSecret(string secret) => GetStringBuilder().ToString().Contains(secret, System.StringComparison.Ordinal);
    }

    private static ClientOptions MakeOptions(string? secret = null) => new()
    {
        AgentBaseUrl = "http://localhost:8000",
        ClientId = "rpa_client_test_001",
        ClientSecret = secret ?? "super-secret-xyz",
        TenantId = "tenant_test_001",
        PollIntervalSeconds = 30,
        StoragePath = "/tmp/fake",
    };

    [Fact(DisplayName = "写入成功：EncryptedClientConfig.LoadAsync 能读回所有字段（核心契约验证）")]
    public async Task WriteAsync_NewFile_RoundtripsThroughEncryptedClientConfig()
    {
        // 安排：临时文件路径
        var path = Path.Combine(Path.GetTempPath(), $"cfg_{System.Guid.NewGuid():N}.enc");
        try
        {
            var writer = new ConfigWriter();
            var stdout = new CapturingWriter();

            // 行动
            var rc = await writer.WriteAsync(MakeOptions(), path, overwrite: false, stdout, confirm: null);

            // 断言
            Assert.Equal(0, rc);
            Assert.True(File.Exists(path));

            // 读回验证（EncryptedClientConfig 自身 DPAPI round-trip）
            var enc = new EncryptedClientConfig();
            var loaded = await enc.LoadAsync(path);
            Assert.Equal("rpa_client_test_001", loaded.ClientId);
            Assert.Equal("super-secret-xyz", loaded.ClientSecret);
            Assert.Equal("http://localhost:8000", loaded.AgentBaseUrl);
            Assert.Equal("tenant_test_001", loaded.TenantId);
            Assert.Equal(30, loaded.PollIntervalSeconds);
            Assert.Equal("/tmp/fake", loaded.StoragePath);

            // 安全：secret 不得出现在 stdout
            Assert.False(stdout.ContainsSecret("super-secret-xyz"));
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }

    [Fact(DisplayName = "文件已存在且 overwrite=false 时调 confirm；confirm=false 不写")]
    public async Task WriteAsync_FileExists_NoOverwrite_ConfirmFalse_Returns1AndDoesNotOverwrite()
    {
        var path = Path.Combine(Path.GetTempPath(), $"cfg_{System.Guid.NewGuid():N}.enc");
        try
        {
            // 先写一份原始
            var enc = new EncryptedClientConfig();
            await enc.SaveAsync(MakeOptions("ORIGINAL-SECRET"), path);

            var writer = new ConfigWriter();
            var stdout = new CapturingWriter();
            var confirmCalled = false;

            var rc = await writer.WriteAsync(
                MakeOptions(secret: "NEW-SECRET-ATTEMPT"),
                path,
                overwrite: false,
                stdout,
                confirm: _ =>
                {
                    confirmCalled = true;
                    return false;
                });

            Assert.Equal(1, rc);
            Assert.True(confirmCalled);

            // 读回验证：内容仍是 ORIGINAL
            var loaded = await new EncryptedClientConfig().LoadAsync(path);
            Assert.Equal("ORIGINAL-SECRET", loaded.ClientSecret);
            Assert.False(stdout.ContainsSecret("NEW-SECRET-ATTEMPT"));
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }

    [Fact(DisplayName = "文件已存在且 overwrite=true 时直接覆盖，不调 confirm")]
    public async Task WriteAsync_OverwriteTrue_SkipsConfirmAndOverwrites()
    {
        var path = Path.Combine(Path.GetTempPath(), $"cfg_{System.Guid.NewGuid():N}.enc");
        try
        {
            await new EncryptedClientConfig().SaveAsync(MakeOptions(secret: "OLD"), path);

            var writer = new ConfigWriter();
            var stdout = new CapturingWriter();
            var confirmCalled = false;

            var rc = await writer.WriteAsync(
                MakeOptions(secret: "NEW-SECRET"),
                path,
                overwrite: true,
                stdout,
                confirm: _ =>
                {
                    confirmCalled = true;
                    return true;
                });

            Assert.Equal(0, rc);
            Assert.False(confirmCalled); // overwrite=true 时不应调 confirm

            var loaded = await new EncryptedClientConfig().LoadAsync(path);
            Assert.Equal("NEW-SECRET", loaded.ClientSecret);
        }
        finally
        {
            if (File.Exists(path)) File.Delete(path);
        }
    }

    [Fact(DisplayName = "输出路径父目录不存在时自动创建")]
    public async Task WriteAsync_CreatesMissingParentDirectory()
    {
        var dir = Path.Combine(Path.GetTempPath(), $"cfgdir_{System.Guid.NewGuid():N}", "nested");
        var path = Path.Combine(dir, "client_config.enc");
        try
        {
            var writer = new ConfigWriter();
            var stdout = new CapturingWriter();

            var rc = await writer.WriteAsync(MakeOptions(), path, overwrite: false, stdout, confirm: null);

            Assert.Equal(0, rc);
            Assert.True(File.Exists(path));
            Assert.True(Directory.Exists(dir));
        }
        finally
        {
            var rootDir = Path.GetDirectoryName(Path.GetDirectoryName(path)!)!;
            if (Directory.Exists(rootDir)) Directory.Delete(rootDir, recursive: true);
        }
    }
}
