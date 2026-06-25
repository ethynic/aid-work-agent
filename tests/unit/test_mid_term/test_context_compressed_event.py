"""ContextCompressedEvent 单元测试（Phase 7 §7.1）

覆盖：
- 事件字段完整（含 fallback_used / trigger_reason / llm_provider / llm_model / duration_ms）
- to_dict() 转出的事件含 type='context_compressed' + timestamp
- TraceCollector.on_event 收到该事件时创建紫色 span（span_type='context_compressed'）
- compression_info 字段完整
- fallback_used=True 时事件正确标记 + trace 加上 compression_fallback tag
"""

import time
from unittest.mock import MagicMock

import pytest

from src.core.agent_events import ContextCompressedEvent
from src.core.trace_collector import TraceCollector


class TestContextCompressedEvent:
    def test_default_fields(self):
        e = ContextCompressedEvent()
        assert e.event_type == "context_compressed"
        assert e.summary_id == ""
        assert e.compressed_message_count == 0
        assert e.original_token_count == 0
        assert e.compressed_token_count == 0
        assert e.compression_ratio == 0.0
        assert e.fallback_used is False
        assert e.trigger_reason == ""
        assert e.llm_provider is None
        assert e.llm_model is None
        assert e.duration_ms == 0

    def test_full_fields(self):
        e = ContextCompressedEvent(
            summary_id="csum_abc",
            compressed_message_count=42,
            original_token_count=10000,
            compressed_token_count=3000,
            compression_ratio=0.3,
            fallback_used=False,
            trigger_reason="token_threshold(10000/7000, 71%, cached=True)",
            llm_provider="deepseek",
            llm_model="deepseek-chat",
            duration_ms=4500,
        )
        assert e.summary_id == "csum_abc"
        assert e.compressed_message_count == 42
        assert e.compression_ratio == 0.3
        assert e.duration_ms == 4500

    def test_to_dict_format(self):
        e = ContextCompressedEvent(
            summary_id="csum_xyz",
            compressed_message_count=5,
            original_token_count=500,
            compressed_token_count=100,
            compression_ratio=0.2,
            fallback_used=True,
            trigger_reason="force",
            llm_provider="deepseek",
            llm_model="deepseek-chat",
            duration_ms=200,
        )
        d = e.to_dict()
        # 顶层字段
        assert d["type"] == "context_compressed"
        assert "timestamp" in d
        assert isinstance(d["timestamp"], int)
        # 业务字段
        assert d["summary_id"] == "csum_xyz"
        assert d["compressed_message_count"] == 5
        assert d["original_token_count"] == 500
        assert d["compressed_token_count"] == 100
        assert d["compression_ratio"] == 0.2
        assert d["fallback_used"] is True
        assert d["trigger_reason"] == "force"
        assert d["llm_provider"] == "deepseek"
        assert d["llm_model"] == "deepseek-chat"
        assert d["duration_ms"] == 200

    def test_timestamp_is_recent(self):
        """to_dict() 生成的 timestamp 接近当前时间。"""
        before = int(time.time() * 1000)
        d = ContextCompressedEvent().to_dict()
        after = int(time.time() * 1000)
        assert before <= d["timestamp"] <= after


class TestTraceCollectorHandleContextCompressed:
    def _make_collector(self):
        return TraceCollector(
            session_id="sess_1",
            tenant_id="t_a",
            user_id="u_1",
            input_msg="hello",
            source_type="chat",
        )

    def test_span_created_with_correct_type(self):
        c = self._make_collector()
        event = ContextCompressedEvent(
            summary_id="csum_1",
            compressed_message_count=10,
            original_token_count=1000,
            compressed_token_count=300,
            compression_ratio=0.3,
            fallback_used=False,
            trigger_reason="token_threshold(...)",
            duration_ms=2000,
        ).to_dict()
        c.on_event(event)
        spans = [s for s in c.trace.spans if s.span_type == "context_compressed"]
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "context_compressed"
        assert span.duration_ms == 2000
        assert span.success is True
        assert span.model is None  # 未传 llm_model
        assert span.provider is None

    def test_compression_info_complete(self):
        c = self._make_collector()
        event = ContextCompressedEvent(
            summary_id="csum_2",
            compressed_message_count=5,
            original_token_count=500,
            compressed_token_count=150,
            compression_ratio=0.3,
            fallback_used=False,
            trigger_reason="force",
            llm_provider="deepseek",
            llm_model="deepseek-chat",
            duration_ms=100,
        ).to_dict()
        c.on_event(event)
        span = next(s for s in c.trace.spans if s.span_type == "context_compressed")
        assert span.compression_info is not None
        assert span.compression_info["summary_id"] == "csum_2"
        assert span.compression_info["compressed_message_count"] == 5
        assert span.compression_info["original_token_count"] == 500
        assert span.compression_info["compressed_token_count"] == 150
        assert span.compression_info["compression_ratio"] == 0.3
        assert span.compression_info["fallback_used"] is False
        assert span.compression_info["trigger_reason"] == "force"
        # provider/model 通过 SpanRecord 顶层字段保留
        assert span.model == "deepseek-chat"
        assert span.provider == "deepseek"

    def test_fallback_adds_tag(self):
        """fallback_used=True 时 trace.tags 应包含 'compression_fallback'。"""
        c = self._make_collector()
        event = ContextCompressedEvent(
            summary_id="csum_3",
            fallback_used=True,
            trigger_reason="force",
            duration_ms=80,
        ).to_dict()
        c.on_event(event)
        assert "context_compressed" in c.trace.tags
        assert "compression_fallback" in c.trace.tags

    def test_success_does_not_add_fallback_tag(self):
        c = self._make_collector()
        event = ContextCompressedEvent(
            summary_id="csum_4",
            fallback_used=False,
            duration_ms=3000,
        ).to_dict()
        c.on_event(event)
        assert "compression_fallback" not in c.trace.tags
        assert "context_compressed" in c.trace.tags

    def test_trigger_reason_recorded_in_tool_args(self):
        """trigger_reason 同时写入 tool_args（便于 trace 列表页展示）。"""
        c = self._make_collector()
        event = ContextCompressedEvent(
            summary_id="csum_5",
            trigger_reason="message_threshold(160/150)",
            duration_ms=4000,
        ).to_dict()
        c.on_event(event)
        span = next(s for s in c.trace.spans if s.span_type == "context_compressed")
        import json
        args = json.loads(span.tool_args)
        assert args["trigger_reason"] == "message_threshold(160/150)"
