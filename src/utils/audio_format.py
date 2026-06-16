"""
音频格式自动检测工具

通过 magic bytes（文件头的二进制特征）识别常见音频格式，
避免因文件扩展名错误（如微信 SILK 标记为 .mp3）导致的 ASR 识别失败。

支持的格式：
- AMR / AMR-WB
- SILK v3 / SILK v2
- WAV
- MP3
- OGG (Opus/Vorbis)
- FLAC
- AAC / M4A
- Opus (in WebM)
"""

from typing import Optional, Tuple


# 各格式对应的 Aliyun ASR format 参数
# 官方文档：https://help.aliyun.com/zh/isi/developer-reference/restful-api-2
FORMAT_AMR = "amr"          # AMR-NB 采样率 8000
FORMAT_AMR_WB = "amr-wb"   # AMR-WB 采样率 16000
FORMAT_WAV = "wav"
FORMAT_MP3 = "mp3"
FORMAT_OPUS = "opus"
FORMAT_PCM = "pcm"
FORMAT_AAC = "aac"

# Aliyun ASR 不直接支持的格式
# SILK 是腾讯/微信专有格式，ASR 端不支持，需要预处理或提示用户
FORMAT_SILK_V3 = "silk_v3"
FORMAT_SILK_V2 = "silk_v2"

# AMR 的标准采样率
AMR_NB_SAMPLE_RATE = 8000
AMR_WB_SAMPLE_RATE = 16000


def detect_audio_format(
    audio_bytes: bytes, content_type: Optional[str] = None
) -> Tuple[str, int]:
    """
    自动检测音频格式与推荐采样率。

    优先使用 magic bytes 判定，content_type 仅作为辅助线索。
    当无法识别时，回退到 (mp3, 16000)。

    Args:
        audio_bytes: 音频原始字节
        content_type: 可选的 HTTP Content-Type，作为辅助判别

    Returns:
        (format, sample_rate) 元组，例如 ("amr", 8000) / ("mp3", 16000) / ("silk_v3", 16000)
    """
    if not audio_bytes or len(audio_bytes) < 4:
        return FORMAT_MP3, 16000

    head = audio_bytes[:16]

    # AMR-NB：文件头为 "#!AMR\n" (0x23 0x21 0x41 0x4D 0x52 0x0A)
    if head[:6] == b"#!AMR\n":
        return FORMAT_AMR, AMR_NB_SAMPLE_RATE

    # AMR-WB：文件头为 "#!AMR-WB\n" (0x23 0x21 0x41 0x4D 0x52 0x2D 0x57 0x42 0x0A)
    if head[:9] == b"#!AMR-WB\n":
        return FORMAT_AMR_WB, AMR_WB_SAMPLE_RATE

    # SILK v3：前 10 字节为 "\x02#!SILK_V3"（"\x02" 1 字节 + "#!SILK_V3" 9 字节）
    if head[:10] == b"\x02#!SILK_V3":
        return FORMAT_SILK_V3, AMR_NB_SAMPLE_RATE

    # SILK v2：前 10 字节为 "\x02#!SILK_V2"
    if head[:10] == b"\x02#!SILK_V2":
        return FORMAT_SILK_V2, AMR_NB_SAMPLE_RATE

    # WAV：RIFF 头 "RIFF....WAVE"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return FORMAT_WAV, 16000

    # FLAC：前 4 字节为 "fLaC"
    if head[:4] == b"fLaC":
        return FORMAT_WAV, 16000

    # OGG 容器：前 4 字节为 "OggS"
    if head[:4] == b"OggS":
        # OGG 可包含 Vorbis / Opus；区分较复杂，统一按 opus 上送（Aliyun 支持）
        return FORMAT_OPUS, 16000

    # WebM 容器：前 4 字节为 "\x1A\x45\xDF\xA3"（EBML 头）
    if head[:4] == b"\x1A\x45\xDF\xA3":
        return FORMAT_OPUS, 16000

    # ID3 标签 → 内部是 MP3
    if head[:3] == b"ID3":
        return FORMAT_MP3, 16000

    # MP3 帧同步：0xFFE/0xFFF 开头（11/12 bit 同步字 + 版本/层/比特率）
    if head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3", b"\xff\xe2"):
        return FORMAT_MP3, 16000

    # MP4 / M4A / AAC：第 5-8 字节为 "ftyp"
    if head[4:8] == b"ftyp":
        return FORMAT_AAC, 16000

    # 辅助：通过 Content-Type 推断
    if content_type:
        ct_lower = content_type.lower()
        if "amr" in ct_lower:
            return FORMAT_AMR, AMR_NB_SAMPLE_RATE
        if "wav" in ct_lower or "x-wav" in ct_lower:
            return FORMAT_WAV, 16000
        if "mpeg" in ct_lower or "mp3" in ct_lower:
            return FORMAT_MP3, 16000
        if "ogg" in ct_lower or "opus" in ct_lower:
            return FORMAT_OPUS, 16000
        if "aac" in ct_lower or "mp4" in ct_lower or "m4a" in ct_lower:
            return FORMAT_AAC, 16000
        if "silk" in ct_lower:
            return FORMAT_SILK_V3, AMR_NB_SAMPLE_RATE

    # 无法识别：默认按 mp3 + 16k 兜底
    return FORMAT_MP3, 16000


def is_supported_by_aliyun(audio_format: str) -> bool:
    """
    判断 Aliyun 一句话识别 RESTful API 是否支持该格式。

    Aliyun 一句话识别支持：pcm, wav, mp3, opus, amr, amr-wb, aac, speex
    不支持：silk（腾讯/微信专有）
    """
    return audio_format in {
        FORMAT_PCM,
        FORMAT_WAV,
        FORMAT_MP3,
        FORMAT_OPUS,
        FORMAT_AMR,
        FORMAT_AMR_WB,
        FORMAT_AAC,
        "speex",
    }


def get_unsupported_reason(audio_format: str) -> str:
    """
    获取不支持的音频格式说明。
    """
    if audio_format in (FORMAT_SILK_V3, FORMAT_SILK_V2):
        return "SILK 是微信/QQ 专有音频格式，阿里云语音识别服务不支持该格式"
    return f"阿里云 ASR 暂不支持 {audio_format} 格式"
