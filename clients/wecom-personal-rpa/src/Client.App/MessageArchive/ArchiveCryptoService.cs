using System.Security.Cryptography;
using System.Text;
using Microsoft.Extensions.Logging;

namespace WeCom.PersonalRpa.App.MessageArchive;

/// <summary>
/// 会话存档加解密服务（Phase 3 块 D）。
///
/// 企微会话存档双层加密（文档 https://developer.work.weixin.qq.com/document/path/91360 §拉取会话内容）：
///   1. RSA-OAEP-SHA1：用企业管理后台生成的会话存档私钥解密 encrypt_random_key，得到 random_key（32 字节）。
///   2. AES-256-CBC + PKCS7：以 random_key 作为密钥；IV 取 base64 解码后的 encrypt_chat_msg 的前 16 字节。
///
/// 用 .NET 内置 RSA.Create() + ImportFromPem（.NET 8 已支持 PEM 导入）与 Aes.Create()。
/// </summary>
public sealed class ArchiveCryptoService
{
    private const string Tag = "ArchiveCryptoService";
    private readonly ILogger<ArchiveCryptoService>? _logger;

    public ArchiveCryptoService(ILogger<ArchiveCryptoService>? logger = null)
    {
        _logger = logger;
    }

    /// <summary>
    /// RSA-OAEP-SHA1 解密 encrypt_random_key（base64），返回 random_key（典型 32 字节）。
    /// </summary>
    /// <param name="rsaPrivateKeyPem">PEM 格式 PKCS#1 或 PKCS#8 RSA 私钥。</param>
    /// <param name="encryptRandomKeyBase64">企微下发的 encrypt_random_key（base64）。</param>
    public byte[] DecryptRandomKey(string rsaPrivateKeyPem, string encryptRandomKeyBase64)
    {
        if (string.IsNullOrEmpty(rsaPrivateKeyPem)) throw new ArgumentException("私钥 PEM 不能为空", nameof(rsaPrivateKeyPem));
        if (string.IsNullOrEmpty(encryptRandomKeyBase64)) throw new ArgumentException("encryptRandomKey 不能为空", nameof(encryptRandomKeyBase64));

        var cipherBytes = Convert.FromBase64String(encryptRandomKeyBase64);

        using var rsa = RSA.Create();
        rsa.ImportFromPem(rsaPrivateKeyPem);
        // OAEP-SHA1：企微官方规范（Java 默认也是 SHA1）。.NET 8 用 RSAEncryptionPadding.OaepSHA1。
        var plainBytes = rsa.Decrypt(cipherBytes, RSAEncryptionPadding.OaepSHA1);
        _logger?.LogDebug("[{Tag}] random_key 解密成功，长度 {Len}", Tag, plainBytes.Length);
        return plainBytes;
    }

    /// <summary>
    /// AES-256-CBC + PKCS7 解密 encrypt_chat_msg（base64）。
    /// key = random_key 前 32 字节；IV = base64 解码后 encrypt_chat_msg 的前 16 字节；密文为剩余字节。
    /// </summary>
    /// <param name="randomKey">由 DecryptRandomKey 返回，典型 32 字节。</param>
    /// <param name="encryptChatMsgBase64">企微下发的 encrypt_chat_msg（base64）。</param>
    /// <returns>明文 UTF-8 JSON。</returns>
    public string DecryptChatMsg(byte[] randomKey, string encryptChatMsgBase64)
    {
        if (randomKey is null || randomKey.Length < 32) throw new ArgumentException("randomKey 至少 32 字节", nameof(randomKey));
        if (string.IsNullOrEmpty(encryptChatMsgBase64)) throw new ArgumentException("encryptChatMsg 不能为空", nameof(encryptChatMsgBase64));

        var allBytes = Convert.FromBase64String(encryptChatMsgBase64);
        if (allBytes.Length < 16) throw new InvalidOperationException("encrypt_chat_msg 长度不足 16 字节（缺少 IV）");

        var iv = new byte[16];
        Array.Copy(allBytes, 0, iv, 0, 16);
        var cipher = new byte[allBytes.Length - 16];
        Array.Copy(allBytes, 16, cipher, 0, cipher.Length);

        // AES-256-CBC：key 必须 32 字节；从 random_key 取前 32 字节。
        var aesKey = new byte[32];
        Array.Copy(randomKey, 0, aesKey, 0, 32);

        using var aes = Aes.Create();
        aes.Mode = CipherMode.CBC;
        aes.Padding = PaddingMode.PKCS7;
        aes.KeySize = 256;
        aes.Key = aesKey;
        aes.IV = iv;

        using var decryptor = aes.CreateDecryptor();
        var plainBytes = decryptor.TransformFinalBlock(cipher, 0, cipher.Length);
        var plain = Encoding.UTF8.GetString(plainBytes);
        _logger?.LogDebug("[{Tag}] chat_msg 解密成功，明文长度 {Len}", Tag, plain.Length);
        return plain;
    }
}
