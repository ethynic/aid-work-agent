"""Prepared original admission CLI, external IO + return scheduling seam only.

The existing CLI signal handler remains original. Original repository.start
actually commits, then its return is held outside all SQL locks. This models the
small before_post callback window, never a fake operation/permission/result.
"""
import json
import os
from pathlib import Path
import time
from urllib.parse import urlsplit


def main():
    endpoint = os.environ['CONTEXT_VOICE_PEER_URL']
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path not in ('', '/') or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise RuntimeError('FICTIONAL_CONTEXT_ENDPOINT_REQUIRED')
    gate = Path(os.environ['CONTEXT_VOICE_CALLBACK_GATE'])
    if not gate.is_absolute() or not gate.parent.is_dir():
        raise RuntimeError('OWN_CALLBACK_GATE_REQUIRED')
    from src.config.settings import settings
    from src.channels.wecom_kf.api_client import WeComKfApiClient
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    WeComKfApiClient.BASE_URL = endpoint
    settings.tools.asr.endpoint = endpoint

    async def fictional_external_token(self, access_key_id, access_key_secret):
        return os.environ['CONTEXT_VOICE_FICTIONAL_TOKEN']

    SpeechToTextTool._get_or_refresh_token = fictional_external_token
    original = ContextVoiceRepository.start

    def hold_committed_return(self, *args, **kwargs):
        operation = original(self, *args, **kwargs)
        if not gate.with_suffix('.ready.json').exists():
            gate.with_suffix('.ready.json').write_text(json.dumps({
                'operation_ref': operation['operation_ref'],
                'record_id': operation['record_id'], 'phase': operation['phase'],
                'authorized_epoch': operation['authorized_epoch']}))
            deadline = time.monotonic() + 15
            while not gate.with_suffix('.release').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('OWN_CALLBACK_GATE_RELEASE_TIMEOUT')
                time.sleep(.01)
        return operation

    ContextVoiceRepository.start = hold_committed_return
    from src.channels.wecom_kf.admission_worker import main as original_main
    original_main()


if __name__ == '__main__':
    main()
