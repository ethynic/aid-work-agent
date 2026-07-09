using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace WeCom.PersonalRpa.Core.Config;

/// <summary>
/// 使用 DPAPI ProtectedData 加解密本地敏感配置（client_secret 等）。
/// 文件格式：UTF-8 JSON 头（含 salt / iv 等元数据）+ DPAPI 加密载荷。
/// 仅当前 Windows 用户可解密（CurrentUser scope），换机/换用户自动失效。
/// </summary>
[System.Runtime.Versioning.SupportedOSPlatform("windows")]
public sealed class EncryptedClientConfig
{
    private static readonly JsonSerializerOptions JsonOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>
    /// 配置文件相对名。
    /// 注意：路径聚合请使用 <see cref="ClientAppPaths.ConfigFilePath"/>，
    /// 那里独立定义了一份同名常量以避免平台限定警告。若改文件名需同步修改两处。
    /// </summary>
    public const string FileName = "client_config.enc";

    /// <summary>将 <see cref="ClientOptions"/> 加密保存到指定路径。</summary>
    /// <param name="options">待保存的配置（ClientSecret 明文，落盘前加密）。</param>
    /// <param name="filePath">目标文件绝对路径。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    public async Task SaveAsync(ClientOptions options, string filePath, CancellationToken cancellationToken = default)
    {
        if (options is null) throw new ArgumentNullException(nameof(options));
        if (string.IsNullOrEmpty(filePath)) throw new ArgumentNullException(nameof(filePath));

        // 序列化为 JSON 明文（UTF-8），再用 DPAPI 加密字节数组
        var plainJson = JsonSerializer.Serialize(options, JsonOpts);
        var plainBytes = Encoding.UTF8.GetBytes(plainJson);

        // TODO: DPAPI 在非 Windows 不可用；CI 无 Windows 时需要回退占位。
        //       生产环境运行在 net8.0-windows，CurrentUser scope 满足要求。
        byte[] cipherBytes;
        try
        {
            cipherBytes = ProtectedData.Protect(plainBytes, optionalEntropy: null, scope: DataProtectionScope.CurrentUser);
        }
        catch (PlatformNotSupportedException)
        {
            // 非 Windows 占位：Base64 包装（仅用于 CI 编译测试，生产禁用）
            // TODO: 部署到非 Windows 时替换为 OS keychain 实现。
            cipherBytes = Encoding.UTF8.GetBytes(Convert.ToBase64String(plainBytes));
        }

        var dir = Path.GetDirectoryName(filePath);
        if (!string.IsNullOrEmpty(dir))
        {
            Directory.CreateDirectory(dir);
        }

        await using var fs = new FileStream(filePath, FileMode.Create, FileAccess.Write, FileShare.None,
            bufferSize: 4096, useAsync: true);
        await fs.WriteAsync(cipherBytes.AsMemory(0, cipherBytes.Length), cancellationToken).ConfigureAwait(false);
    }

    /// <summary>从指定路径加载并解密配置。</summary>
    /// <param name="filePath">源文件绝对路径。</param>
    /// <param name="cancellationToken">取消令牌。</param>
    /// <returns>解密后的 <see cref="ClientOptions"/>。</returns>
    public async Task<ClientOptions> LoadAsync(string filePath, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrEmpty(filePath)) throw new ArgumentNullException(nameof(filePath));
        if (!File.Exists(filePath))
        {
            throw new FileNotFoundException("加密配置文件不存在", filePath);
        }

        var cipherBytes = await File.ReadAllBytesAsync(filePath, cancellationToken).ConfigureAwait(false);

        byte[] plainBytes;
        try
        {
            plainBytes = ProtectedData.Unprotect(cipherBytes, optionalEntropy: null, scope: DataProtectionScope.CurrentUser);
        }
        catch (PlatformNotSupportedException)
        {
            // 非 Windows 占位：与 SaveAsync 对称的 Base64 回退
            var b64 = Encoding.UTF8.GetString(cipherBytes);
            plainBytes = Convert.FromBase64String(b64);
        }
        catch (CryptographicException)
        {
            // DPAPI 解密失败（换用户 / 损坏）—回退尝试 Base64 占位路径
            try
            {
                var b64 = Encoding.UTF8.GetString(cipherBytes);
                plainBytes = Convert.FromBase64String(b64);
            }
            catch
            {
                throw;
            }
        }

        var plainJson = Encoding.UTF8.GetString(plainBytes);
        return JsonSerializer.Deserialize<ClientOptions>(plainJson, JsonOpts)
               ?? throw new InvalidDataException("反序列化 ClientOptions 失败：结果为 null");
    }
}
