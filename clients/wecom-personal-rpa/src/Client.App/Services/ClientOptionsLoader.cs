using System.IO;
using System.Text.Json;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.App.Services;

/// <summary>
/// 客户端启动选项加载器。
///
/// 配置分两部分（遵循"敏感信息必须加密"原则）：
///   - 非密运行参数（AgentBaseUrl / ClientId / PollIntervalSeconds / StoragePath）：
///     明文 JSON，{DataDir}/client_options.json
///   - 敏感参数（ClientSecret）：
///     与上述选项一同序列化后，整包经 <see cref="EncryptedClientConfig"/>（DPAPI）加密落盘为
///     {DataDir}/client_config.enc，首启或轮换时写入。
///
/// 本加载器负责：
///   1) 若 client_options.json 存在，读取非密默认值；
///   2) 若 client_config.enc 存在，解密整包覆盖 ClientOptions（含 ClientSecret）；
///   3) 都不存在时写一份默认非密 client_options.json，等待注册流程补齐密钥。
/// </summary>
internal static class ClientOptionsLoader
{
    private const string Tag = "ClientOptionsLoader";
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    /// <summary>加载或创建默认选项。</summary>
    public static ClientOptions Load(string dataDir)
    {
        var plainPath = Path.Combine(dataDir, "client_options.json");
        var encPath = Path.Combine(dataDir, EncryptedClientConfig.FileName);

        ClientOptions opts;
        if (File.Exists(encPath))
        {
            try
            {
                var enc = new EncryptedClientConfig();
                opts = enc.LoadAsync(encPath).GetAwaiter().GetResult();
                Serilog.Log.Information(
                    "[{Tag}] 已从加密配置加载 ClientId={ClientId} BaseUrl={Url}",
                    Tag, opts.ClientId, opts.AgentBaseUrl);
                return opts;
            }
            catch (Exception ex)
            {
                Serilog.Log.Warning(ex, "[{Tag}] 解密配置失败，回退明文/默认：{Path}", Tag, encPath);
            }
        }

        if (File.Exists(plainPath))
        {
            try
            {
                var json = File.ReadAllText(plainPath);
                opts = JsonSerializer.Deserialize<ClientOptions>(json, JsonOpts) ?? CreateDefault(dataDir);
            }
            catch (Exception ex)
            {
                Serilog.Log.Warning(ex, "[{Tag}] 读取明文配置失败，回退默认：{Path}", Tag, plainPath);
                opts = CreateDefault(dataDir);
            }
        }
        else
        {
            opts = CreateDefault(dataDir);
            try
            {
                Directory.CreateDirectory(dataDir);
                File.WriteAllText(plainPath, JsonSerializer.Serialize(opts, JsonOpts));
            }
            catch (Exception ex)
            {
                Serilog.Log.Warning(ex, "[{Tag}] 写入默认配置失败：{Path}", Tag, plainPath);
            }
        }

        Serilog.Log.Information(
            "[{Tag}] 配置加载完成 ClientId={ClientId} BaseUrl={Url} Poll={Poll}s",
            Tag, opts.ClientId, opts.AgentBaseUrl, opts.PollIntervalSeconds);
        return opts;
    }

    private static ClientOptions CreateDefault(string dataDir) => new()
    {
        AgentBaseUrl = "https://agent.example.com",
        ClientId = string.Empty,
        ClientSecret = string.Empty, // 首启无密钥，等待注册流程下发
        PollIntervalSeconds = 30,
        StoragePath = dataDir,
    };
}
