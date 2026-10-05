"""Observe original known-ASR tuple before a real owned result SQL failure.

No result, phase, receipt, permission or SQL return is changed. The private file
allows the test to explicitly redeliver that same typed observation later;
this does not claim a naturally lost provider response can be reconstructed.
"""
import json
import os
from pathlib import Path
import runpy


def main():
    from src.channels.wecom_kf.voice_repository import VoiceRepository
    root = Path(os.environ['AGENT_RUNNER_RESOURCE_DIR']).resolve()
    output = Path(os.environ['KF_COMPLETION_ASR_RESULT_OBSERVATION']).resolve()
    expected_ref = os.environ['KF_COMPLETION_ASR_INPUT_REF']
    if output.parent != root or output.name != 'completion-original-asr-result.json' or not expected_ref:
        raise RuntimeError('OWNED_ASR_OBSERVATION_REQUIRED')
    original = VoiceRepository.result

    def observed_result(self, input_ref, *, success, text, status):
        if input_ref == expected_ref:
            if type(success) is not bool or not isinstance(text, str) or type(status) is not int:
                raise RuntimeError('ORIGINAL_ASR_TUPLE_REQUIRED')
            temporary = output.with_suffix('.pending')
            temporary.write_text(json.dumps({'input_ref': input_ref,
                'success': success, 'text': text, 'status': status}))
            temporary.chmod(0o600)
            temporary.replace(output)
        return original(self, input_ref, success=success, text=text, status=status)

    VoiceRepository.result = observed_result
    runpy.run_path(str(Path(__file__).with_name('kf_voice_process.py')), run_name='__main__')


if __name__ == '__main__':
    main()
