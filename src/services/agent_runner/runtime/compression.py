from __future__ import annotations
from loguru import logger
from src.config.settings import settings

class CompressionCoordinator:
    def __init__(self, source_type, repository=None):
        self.source_type, self.event, self.repository = source_type, None, repository

    def _detect_source_type(self):
        return self.source_type
    async def _run_compression_phase(self, session_id: str) -> "Optional[Any]":
        """Prepare compression and return trace facts through the caller.

        Compression is optional; an unavailable summarizer leaves original
        history intact. History reads and runner checkpoints remain authoritative.
        """
        if not settings.memory.mid_term.enabled:
            return None
        try:
            from src.memory.mid_term import get_compression_service
            if self.repository is None:
                compression_service = get_compression_service()
            else:
                from src.memory.mid_term import ContextCompressionService
                compression_service = ContextCompressionService(session_repository=self.repository)
            source_type = self._detect_source_type()
            # O(1) 单行查询 + COUNT 查询，不做完整 IO
            should_compress, reason, session_meta = await compression_service.check_threshold(
                session_id, source_type
            )
            if should_compress:
                # 仅在需要压缩时才执行（compress_now 内部会读全部 messages）
                # v3.2.1（P0-1）：透传 check_threshold 的精确 reason
                import time as _time
                _t0 = _time.perf_counter()
                result = await compression_service.compress_now(
                    session_id, source_type, session_meta, trigger_reason=reason
                )
                duration_ms = int((_time.perf_counter() - _t0) * 1000)
                if result is not None:
                    logger.info(
                        f"ContextCompression triggered: sid={session_id}, "
                        f"summary_id={result.summary_id}, "
                        f"reason={result.trigger_reason}, "
                        f"compacted={result.compressed_message_count} msgs, "
                        f"token {result.original_token_count}->{result.compressed_token_count}, "
                        f"fallback={result.fallback_used}"
                    )
                    # Trace/metrics are optional projections of compression facts.
                    try:
                        from src.core.agent_events import ContextCompressedEvent
                        from src.core.compression_metrics import get_compression_metrics
                        event = ContextCompressedEvent(
                            summary_id=result.summary_id,
                            compressed_message_count=result.compressed_message_count,
                            original_token_count=result.original_token_count,
                            compressed_token_count=result.compressed_token_count,
                            compression_ratio=result.compression_ratio,
                            fallback_used=result.fallback_used,
                            trigger_reason=result.trigger_reason,
                            llm_provider=result.llm_provider,
                            llm_model=result.llm_model,
                            duration_ms=duration_ms,
                            llm_prompt_tokens=result.llm_prompt_tokens,
                            llm_completion_tokens=result.llm_completion_tokens,
                            llm_cached_tokens=result.llm_cached_tokens,
                            summary_truncated=result.summary_truncated,
                        )
                        # The assembler publishes the compression event.
                        self.event = event.to_dict()
                        # 指标埋点
                        metrics = get_compression_metrics()
                        metrics.record_invocation(
                            "fallback" if result.fallback_used else "success",
                            source_type,
                        )
                        metrics.record_duration(duration_ms / 1000.0)
                        metrics.record_ratio(result.compression_ratio)
                    except Exception as oe:
                        logger.debug(f"compression metrics/trace emit failed: {oe}")
                    return result
            else:
                # 未触发阈值：skipped 计数
                try:
                    from src.core.compression_metrics import get_compression_metrics
                    get_compression_metrics().record_invocation("skipped", source_type)
                except Exception as oe:
                    logger.debug(f"compression metrics skipped-emit failed: {oe}")
        except Exception as e:
            if getattr(e, 'authoritative_storage_failure', False):
                raise
            # 压缩失败不影响主流程，本轮跳过压缩，下一轮再试
            # v3.2.1（P1-4）：用 action=skipped_due_to_error 明确标识「本轮因异常跳过」，
            # 便于指标埋点过滤「正常未触发阈值（reason 空）」vs「服务挂了」
            logger.exception(
                f"ContextCompression skipped_due_to_error, sid={session_id}: {e}"
            )
            try:
                from src.core.compression_metrics import get_compression_metrics
                get_compression_metrics().record_invocation("failed", "")
            except Exception as oe:
                logger.debug(f"compression metrics failed-emit failed: {oe}")
        return None
