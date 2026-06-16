"""
音频格式自动检测单元测试

覆盖场景：
- AMR-NB / AMR-WB magic bytes 识别
- SILK v3 / SILK v2 magic bytes 识别
- WAV / MP3 / OGG / WebM / ID3 / MP4 (ftyp) 识别
- FLAC 降级为 wav
- 短 / 空数据的兜底
- Content-Type 辅助推断
- is_supported_by_aliyun / get_unsupported_reason 行为
"""

import pytest

from src.utils.audio_format import (
    AMR_NB_SAMPLE_RATE,
    AMR_WB_SAMPLE_RATE,
    FORMAT_AAC,
    FORMAT_AMR,
    FORMAT_AMR_WB,
    FORMAT_MP3,
    FORMAT_OPUS,
    FORMAT_PCM,
    FORMAT_SILK_V2,
    FORMAT_SILK_V3,
    FORMAT_WAV,
    detect_audio_format,
    get_unsupported_reason,
    is_supported_by_aliyun,
)

pytestmark = pytest.mark.unit


# ============================================================
# 1. Magic bytes 识别
# ============================================================


class TestDetectAudioFormatByMagicBytes:
    """根据文件头识别音频格式"""

    def test_amr_nb_magic(self):
        audio = b"#!AMR\n" + b"\x00" * 200
        fmt, sr = detect_audio_format(audio)
        assert fmt == FORMAT_AMR
        assert sr == AMR_NB_SAMPLE_RATE

    def test_amr_wb_magic(self):
        audio = b"#!AMR-WB\n" + b"\x00" * 200
        fmt, sr = detect_audio_format(audio)
        assert fmt == FORMAT_AMR_WB
        assert sr == AMR_WB_SAMPLE_RATE

    def test_silk_v3_magic(self):
        audio = b"\x02#!SILK_V3" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_SILK_V3

    def test_silk_v2_magic(self):
        audio = b"\x02#!SILK_V2" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_SILK_V2

    def test_wav_magic(self):
        audio = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 200
        fmt, sr = detect_audio_format(audio)
        assert fmt == FORMAT_WAV
        assert sr == 16000

    def test_mp3_id3_tag(self):
        audio = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200
        fmt, sr = detect_audio_format(audio)
        assert fmt == FORMAT_MP3
        assert sr == 16000

    def test_mp3_frame_sync(self):
        audio = b"\xff\xfb\x90\x00" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_MP3

    def test_ogg_magic(self):
        audio = b"OggS" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_OPUS

    def test_webm_magic(self):
        audio = b"\x1A\x45\xDF\xA3" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_OPUS

    def test_mp4_ftyp_magic(self):
        audio = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_AAC

    def test_flac_fallback_to_wav(self):
        # FLAC 不在 Aliyun 一句话识别支持列表中，应回退到 wav
        audio = b"fLaC" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio)
        assert fmt == FORMAT_WAV


# ============================================================
# 2. 边界与兜底
# ============================================================


class TestDetectAudioFormatEdgeCases:
    """边界条件"""

    def test_empty_bytes_fallback(self):
        fmt, sr = detect_audio_format(b"")
        assert fmt == FORMAT_MP3
        assert sr == 16000

    def test_too_short_fallback(self):
        fmt, sr = detect_audio_format(b"\x00\x00")
        assert fmt == FORMAT_MP3
        assert sr == 16000

    def test_unknown_magic_fallback(self):
        fmt, sr = detect_audio_format(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        assert fmt == FORMAT_MP3
        assert sr == 16000

    def test_minimal_4_bytes_works(self):
        # 4 字节以上才能进入 magic bytes 比对
        fmt, sr = detect_audio_format(b"#!AM")
        # 不够 6 字节，未命中 AMR 规则，回退到 mp3
        assert fmt == FORMAT_MP3


# ============================================================
# 3. Content-Type 辅助推断
# ============================================================


class TestDetectAudioFormatByContentType:
    """magic bytes 命中失败时，回退到 Content-Type"""

    def test_content_type_amr(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, sr = detect_audio_format(audio, content_type="audio/amr")
        assert fmt == FORMAT_AMR
        assert sr == AMR_NB_SAMPLE_RATE

    def test_content_type_mp3(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, sr = detect_audio_format(audio, content_type="audio/mpeg")
        assert fmt == FORMAT_MP3

    def test_content_type_wav(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, sr = detect_audio_format(audio, content_type="audio/x-wav")
        assert fmt == FORMAT_WAV

    def test_content_type_opus(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, _sr = detect_audio_format(audio, content_type="audio/opus")
        assert fmt == FORMAT_OPUS

    def test_content_type_aac(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, _sr = detect_audio_format(audio, content_type="audio/aac")
        assert fmt == FORMAT_AAC

    def test_content_type_mp4(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, _sr = detect_audio_format(audio, content_type="audio/mp4")
        assert fmt == FORMAT_AAC

    def test_content_type_m4a(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, _sr = detect_audio_format(audio, content_type="audio/m4a")
        assert fmt == FORMAT_AAC

    def test_content_type_silk(self):
        audio = b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        fmt, _sr = detect_audio_format(audio, content_type="audio/silk")
        assert fmt == FORMAT_SILK_V3

    def test_magic_bytes_wins_over_content_type(self):
        # 当 magic bytes 已经能识别时，content_type 不起作用
        audio = b"#!AMR\n" + b"\x00" * 200
        fmt, _sr = detect_audio_format(audio, content_type="audio/mpeg")
        assert fmt == FORMAT_AMR


# ============================================================
# 4. Aliyun ASR 支持性判定
# ============================================================


class TestAliyunSupport:
    """格式支持性判定"""

    @pytest.mark.parametrize("fmt", [
        FORMAT_PCM, FORMAT_WAV, FORMAT_MP3, FORMAT_OPUS,
        FORMAT_AMR, FORMAT_AMR_WB, FORMAT_AAC, "speex",
    ])
    def test_supported_formats(self, fmt):
        assert is_supported_by_aliyun(fmt) is True

    @pytest.mark.parametrize("fmt", [FORMAT_SILK_V3, FORMAT_SILK_V2, "unknown_fmt", "flac"])
    def test_unsupported_formats(self, fmt):
        assert is_supported_by_aliyun(fmt) is False


class TestUnsupportedReason:
    """不支持原因说明"""

    def test_silk_v3_reason(self):
        msg = get_unsupported_reason(FORMAT_SILK_V3)
        assert "SILK" in msg
        assert "微信" in msg or "QQ" in msg

    def test_silk_v2_reason(self):
        msg = get_unsupported_reason(FORMAT_SILK_V2)
        assert "SILK" in msg

    def test_other_reason(self):
        msg = get_unsupported_reason("unknown_format")
        assert "unknown_format" in msg
