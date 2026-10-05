"""Original Worker with explicit summary configuration parameters only.

Threshold/count/tail parameters keep the fixture bounded. The original 40k
summary economy gate, compression algorithm, HTTP call, physical Usage observer
and Engine remain intact. Default summary retry remains unchanged.
"""
import os


def main():
    from src.config.settings import settings, SummaryLLMConfig
    model = os.environ['KF_VOICE_SUMMARY_FIXTURE_MODEL']
    if not model or len(model) > 256:
        raise RuntimeError('FICTIONAL_SUMMARY_MODEL_REQUIRED')
    settings.memory.mid_term = settings.memory.mid_term.model_copy(update={
        'enabled': True, 'message_count_threshold': 4, 'header_keep': 0,
        'tail_keep': 2, 'summary_llm': SummaryLLMConfig(provider='qwen', model=model)})
    from tests.integration.agent_runner_service.kf_voice_process import main as original_voice_entry
    original_voice_entry()


if __name__ == '__main__':
    main()
