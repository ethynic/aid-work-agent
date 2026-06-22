using System.Text;
using WeCom.PersonalRpa.Core.Security;
using Xunit;

namespace WeCom.PersonalRpa.Tests.Security;

/// <summary>
/// HmacSigner 确定性测试。
///
/// 契约来源：docs/system/wecom-personal-rpa-protocol.md §A.1 与
/// src/channels/wecom_personal_rpa/schemas.py（SIGNATURE_ALGORITHM = "HMAC-SHA256"）：
///   sig = hex_lowercase(hmac_sha256(key = client_secret_bytes,
///                                    msg = client_id + timestamp + nonce + raw_body))
/// 全部 ASCII/UTF-8 原始字节直接拼接，无分隔符；raw_body 不得 re-serialize。
///
/// 本测试只验证确定性（同输入同输出）与与协议一致的拼接顺序，**不**在此处
/// 固化具体哈希字面量——哈希字面量应作为跨语言一致性校验的回归基线单独维护
/// （建议在服务端 auth.compute_signature 与本 HmacSigner 之间用一组固定向量做
/// 字节对字节比对，避免任一侧悄悄改动拼接/编码导致签名静默漂移）。
/// </summary>
public class HmacSignerTests
{
    private static readonly byte[] Secret = Encoding.UTF8.GetBytes("test-secret-do-not-log");

    [Fact(DisplayName = "同输入两次计算结果一致（确定性）")]
    public void ComputeSignature_IsDeterministic_SameInputsProduceSameOutput()
    {
        var sig1 = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "{\"hello\":1}", Secret);
        var sig2 = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "{\"hello\":1}", Secret);

        Assert.NotNull(sig1);
        Assert.Equal(sig1, sig2);
    }

    [Fact(DisplayName = "任一输入变化则签名变化（敏感性）")]
    public void ComputeSignature_Changes_WhenAnyInputChanges()
    {
        var baseline = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "body", Secret);

        Assert.NotEqual(baseline, HmacSigner.ComputeSignature("client_002", "1782100000", "nonce_abc", "body", Secret));
        Assert.NotEqual(baseline, HmacSigner.ComputeSignature("client_001", "1782100001", "nonce_abc", "body", Secret));
        Assert.NotEqual(baseline, HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abd", "body", Secret));
        Assert.NotEqual(baseline, HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "body2", Secret));
        Assert.NotEqual(baseline, HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "body", Encoding.UTF8.GetBytes("other-secret")));
    }

    [Fact(DisplayName = "签名恒为小写十六进制且长度为 64（HMAC-SHA256）")]
    public void ComputeSignature_Always_LowercaseHex_OfSha256Length()
    {
        var sig = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", "body", Secret);

        Assert.Equal(64, sig.Length);
        Assert.Matches("^[0-9a-f]{64}$", sig);
    }

    [Fact(DisplayName = "byte[] body 重载与等价 string body 结果一致（禁止 re-serialize）")]
    public void ComputeSignature_ByteArrayOverload_MatchesStringOverload_ForSameUtf8Bytes()
    {
        var bodyStr = "{\"text\":\"你好\",\"n\":1}"; // 含非 ASCII，验证 UTF-8 字节一致性
        var bodyBytes = Encoding.UTF8.GetBytes(bodyStr);

        var viaString = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", bodyStr, Secret);
        var viaBytes = HmacSigner.ComputeSignature("client_001", "1782100000", "nonce_abc", bodyBytes, Secret);

        Assert.Equal(viaString, viaBytes);
    }

    [Fact(DisplayName = "拼接顺序为 clientId+timestamp+nonce+body（协议锁定，无分隔符）")]
    public void ComputeSignature_ConcatenationOrder_MatchesProtocol_NoSeparator()
    {
        // 用一组可手工推导的输入验证拼接顺序：若顺序或分隔符被改动，此断言将失败。
        var clientId = "client_001";
        var timestamp = "1782100000";
        var nonce = "nonce_abc";
        var body = "body";

        var expected = HmacSigner.ComputeSignature(clientId, timestamp, nonce, body, Secret);

        // 等价手算：直接对 clientId+timestamp+nonce+body（无分隔）做 HMAC-SHA256。
        var manualMsg = clientId + timestamp + nonce + body;
        using var hmac = new System.Security.Cryptography.HMACSHA256(Secret);
        var manual = System.Convert.ToHexString(hmac.ComputeHash(Encoding.UTF8.GetBytes(manualMsg)))
            .ToLowerInvariant();

        Assert.Equal(manual, expected);
    }
}

/*
 * ---------------------------------------------------------------------------
 * 跨语言一致性校验（建议项，非本测试固化）：
 *
 * 服务端 src/channels/wecom_personal_rpa/auth.py::compute_signature 与客户端
 * WeCom.PersonalRpa.Core.Security.HmacSigner.ComputeSignature 必须对同一组
 * (client_id, timestamp, nonce, body, secret) 产生完全相同的十六进制输出。
 *
 * 建议在阶段4（Verify）增设一个共享向量回归测试：把一组固定向量放在
 *   tests/fixtures/hmac_vectors.json
 * 由服务端 pytest 与客户端 xUnit 同时加载，任一侧实现漂移即失败。
 * 这里不固化字面量，避免客户端单边维护的伪基线掩盖服务端实现差异。
 * ---------------------------------------------------------------------------
 */
