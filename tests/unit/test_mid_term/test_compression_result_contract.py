"""CompressionResult 数据契约与字段语义测试（v3.1 Phase 3）

目的：通过「字段契约」固化 CompressionResult 的 schema，防止字段被改名/删除/类型变化时
测试仍然通过（mutation testing 视角）。

覆盖：
- 所有字段名称存在（dataclass fields）
- 字段类型符合设计文档 §5.1（summary_id: str, fallback_used: bool 等）
- 默认值：llm_provider/llm_model 默认 None（设计文档允许）
- trigger_reason 字段存在、类型及默认值
- 构造时 summary_id 为必填字段
"""

import dataclasses
from typing import get_type_hints

import pytest

from src.memory.mid_term import CompressionResult


def test_dataclass_fields_match_design():
    """CompressionResult 必须包含设计 §5.1 列出的全部字段（含 trigger_reason）。

    设计文档 §5.1 列出：
        summary_id, trigger_reason, fallback_used, compressed_message_count,
        original_token_count, compressed_token_count, compression_ratio

    v3.1 P1-1 修复：trigger_reason 已回填到 CompressionResult。
    """
    expected = {
        "summary_id",
        "trigger_reason",
        "compressed_message_count",
        "original_token_count",
        "compressed_token_count",
        "compression_ratio",
        "fallback_used",
        "llm_provider",
        "llm_model",
    }
    actual = {f.name for f in dataclasses.fields(CompressionResult)}
    assert expected.issubset(actual), (
        f"CompressionResult 缺少设计要求的字段：missing={expected - actual}"
    )


def test_field_types():
    """字段类型契约：防止类型被改成不兼容的形式"""
    hints = get_type_hints(CompressionResult)
    # summary_id 必须是 str（不能是 Optional）
    assert hints["summary_id"] is str, "summary_id 必须是 str（必填字段）"
    assert hints["trigger_reason"] is str
    # fallback_used 必须是 bool
    assert hints["fallback_used"] is bool
    # 数字字段必须是 int / float
    assert hints["compressed_message_count"] is int
    assert hints["original_token_count"] is int
    assert hints["compressed_token_count"] is int
    assert hints["compression_ratio"] is float


def test_optional_field_defaults():
    """提供商/模型默认 None，触发原因默认空字符串。"""
    result = CompressionResult(
        summary_id="csum_x",
        compressed_message_count=10,
        original_token_count=1000,
        compressed_token_count=300,
        compression_ratio=0.3,
        fallback_used=False,
    )
    assert result.llm_provider is None
    assert result.llm_model is None
    assert result.trigger_reason == ""


def test_compression_result_construction_requires_summary_id():
    """summary_id 是必填字段，不传应抛 TypeError"""
    with pytest.raises(TypeError):
        CompressionResult(
            compressed_message_count=10,
            original_token_count=1000,
            compressed_token_count=300,
            compression_ratio=0.3,
            fallback_used=False,
        )
