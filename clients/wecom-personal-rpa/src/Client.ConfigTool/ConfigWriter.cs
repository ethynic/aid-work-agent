using System.IO;
using System.Threading;
using System.Threading.Tasks;
using WeCom.PersonalRpa.Core.Config;

namespace WeCom.PersonalRpa.ConfigTool;

/// <summary>
/// 配置写入业务逻辑层。
///
/// 职责：把已构造好的 <see cref="ClientOptions"/> 通过 <see cref="EncryptedClientConfig"/>
/// DPAPI 加密落盘到 <paramref name="outputPath"/>。负责目录创建、覆盖确认、敏感字段脱敏日志。
///
/// 安全约束（遵循 backend_dev.md「敏感信息必须加密，不以明文形式返回给用户」）：
///   - client_secret 在本类内不得出现在任何 TextWriter 输出或异常消息中；
///   - 仅写入 EncryptedClientConfig 期望的加密文件，绝不写明文临时文件；
///   - 覆盖现有 client_config.enc 前必须经 confirm 回调确认。
/// </summary>
public sealed class ConfigWriter
{
    private const string Tag = "ConfigWriter";

    /// <summary>
    /// 写入加密配置文件。
    /// </summary>
    /// <param name="options">已构造好的配置（含 ClientSecret 明文，落盘前由 EncryptedClientConfig 加密）。</param>
    /// <param name="outputPath">目标绝对路径（client_config.enc）。</param>
    /// <param name="overwrite">是否跳过覆盖确认（true=直接覆盖，false=文件存在时回调 confirm）。</param>
    /// <param name="stdout">标准输出（用于成功提示，绝不含 secret）。</param>
    /// <param name="confirm">覆盖确认回调（返回 true 表示允许覆盖）。仅当文件已存在且 <paramref name="overwrite"/>=false 时调用。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>进程退出码：0=成功，1=用户取消覆盖，2=IO/加密异常。</returns>
    public async Task<int> WriteAsync(
        ClientOptions options,
        string outputPath,
        bool overwrite,
        TextWriter stdout,
        Func<string, bool>? confirm,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        if (string.IsNullOrWhiteSpace(outputPath))
        {
            throw new ArgumentException("输出路径不能为空", nameof(outputPath));
        }
        ArgumentNullException.ThrowIfNull(stdout);

        try
        {
            // 1. 文件已存在时确认覆盖
            if (File.Exists(outputPath) && !overwrite)
            {
                var msg = $"文件已存在：{outputPath}\r\n确认覆盖？这会破坏现有 client_config.enc (y/N): ";
                var allow = confirm?.Invoke(msg) ?? false;
                if (!allow)
                {
                    stdout.WriteLine("[SKIP] 用户取消，未写入。");
                    return 1;
                }
            }

            // 2. 调 EncryptedClientConfig 加密落盘（内部负责 DPAPI + 目录创建）
            var enc = new EncryptedClientConfig();
            await enc.SaveAsync(options, outputPath, cancellationToken).ConfigureAwait(false);

            // 3. 成功提示：绝不输出 ClientSecret
            stdout.WriteLine($"[OK] 配置已加密写入：{outputPath}");
            stdout.WriteLine($"     ClientId={options.ClientId}, BaseUrl={options.AgentBaseUrl}, TenantId={options.TenantId}");
            stdout.WriteLine($"     PollInterval={options.PollIntervalSeconds}s, StoragePath={options.StoragePath}");
            stdout.WriteLine("     现在可以启动 Client.App 或 Client.Supervisor");
            return 0;
        }
        catch (Exception ex)
        {
            // 异常消息不得包含 secret；options.ClientSecret 不在 ex.Message 中（DPAPI 异常只涉及字节数组）
            stdout.WriteLine($"[FAIL] 写入失败：{ex.GetType().Name}: {ex.Message}");
            return 2;
        }
    }
}
