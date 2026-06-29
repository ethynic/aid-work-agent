using System.Security.Cryptography;
using System.Text;
using WeCom.PersonalRpa.App.MessageArchive;
using Xunit;

namespace WeCom.PersonalRpa.Tests.MessageArchive;

/// <summary>
/// ArchiveCryptoService 加解密单元测试。
/// 用已知向量（自己生成 RSA 密钥对 + AES key/IV）验证算法链路符合企微会话存档规范。
/// </summary>
public sealed class ArchiveCryptoServiceTests
{
    private readonly ArchiveCryptoService _svc = new();

    [Fact]
    [Trait("Category", "Unit")]
    public void DecryptRandomKey_RsaOaepSha1_ReturnsExpectedKey()
    {
        // 生成 2048 位 RSA 密钥对，用公钥加密已知的 random_key（32 字节），私钥解密回期望值。
        using var rsa = RSA.Create(2048);
        var expectedKey = RandomNumberGenerator.GetBytes(32);

        var cipherBytes = rsa.Encrypt(expectedKey, RSAEncryptionPadding.OaepSHA1);
        var cipherBase64 = Convert.ToBase64String(cipherBytes);

        var privatePem = ExportPrivateKeyPem(rsa);

        var actual = _svc.DecryptRandomKey(privatePem, cipherBase64);
        Assert.Equal(expectedKey, actual);
    }

    [Fact]
    [Trait("Category", "Unit")]
    public void DecryptChatMsg_AesCbc_Pkcs7_ReturnsPlainText()
    {
        // 已知 random_key（32 字节）+ 已知明文 JSON，按企微规范（IV 在密文前 16 字节）加密，再解密。
        var randomKey = RandomNumberGenerator.GetBytes(32);
        var plainJson = "{\"msgtype\":\"text\",\"text\":{\"content\":\"hello\"}}";
        var plainBytes = Encoding.UTF8.GetBytes(plainJson);

        var iv = RandomNumberGenerator.GetBytes(16);
        using var aes = Aes.Create();
        aes.Mode = CipherMode.CBC;
        aes.Padding = PaddingMode.PKCS7;
        aes.KeySize = 256;
        aes.Key = randomKey;
        aes.IV = iv;

        using var encryptor = aes.CreateEncryptor();
        var cipher = encryptor.TransformFinalBlock(plainBytes, 0, plainBytes.Length);
        var combined = new byte[iv.Length + cipher.Length];
        Array.Copy(iv, 0, combined, 0, iv.Length);
        Array.Copy(cipher, 0, combined, iv.Length, cipher.Length);
        var cipherBase64 = Convert.ToBase64String(combined);

        var actual = _svc.DecryptChatMsg(randomKey, cipherBase64);
        Assert.Equal(plainJson, actual);
    }

    /// <summary>导出 PKCS#8 PEM 私钥（.NET 8 RSA.Create().ExportRSAPrivateKey + Pem 写入）。</summary>
    private static string ExportPrivateKeyPem(RSA rsa)
    {
        var der = rsa.ExportRSAPrivateKey();
        var b64 = Convert.ToBase64String(der);
        var sb = new StringBuilder();
        sb.AppendLine("-----BEGIN RSA PRIVATE KEY-----");
        for (int i = 0; i < b64.Length; i += 64)
        {
            sb.AppendLine(b64.Substring(i, Math.Min(64, b64.Length - i)));
        }
        sb.AppendLine("-----END RSA PRIVATE KEY-----");
        return sb.ToString();
    }
}
