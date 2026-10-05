"""Only external token IO timing DI before original physical ASR boundary.

Original Speech execute/before_post/POST/parser and original Worker are intact.
The original fictional token seam waits on owned fixture files so the test can
submit an actual pause before paid dispatch. No preparation phase is changed.
"""
import asyncio
import os
from pathlib import Path
import runpy
import time
from urllib.parse import urlsplit


def main():
    endpoint = os.environ['KF_VOICE_ASR_FIXTURE_URL']
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path not in ('', '/') or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise RuntimeError('FICTIONAL_ASR_ENDPOINT_REQUIRED')
    token = os.environ['KF_VOICE_ASR_FIXTURE_TOKEN']
    if not token or len(token) > 256:
        raise RuntimeError('FICTIONAL_ASR_TOKEN_REQUIRED')
    root = Path(os.environ['AGENT_RUNNER_RESOURCE_DIR']).resolve()
    ready = Path(os.environ['KF_VOICE_TOKEN_READY']).resolve()
    release = Path(os.environ['KF_VOICE_TOKEN_RELEASE']).resolve()
    for path in (ready, release):
        if path.parent != root or not path.name.startswith('voice-token-'):
            raise RuntimeError('OWNED_TOKEN_GATE_REQUIRED')
    from src.config.settings import settings
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    settings.tools.asr.endpoint = endpoint.rstrip('/')

    async def held_fictional_external_token(self, access_key_id, access_key_secret):
        ready.write_text('ready')
        deadline = time.monotonic() + 20
        while not release.is_file():
            if time.monotonic() >= deadline:
                raise RuntimeError('FICTIONAL_TOKEN_GATE_TIMEOUT')
            await asyncio.sleep(.025)
        return token

    SpeechToTextTool._get_or_refresh_token = held_fictional_external_token
    runpy.run_path(str(Path(__file__).with_name('kf_admission_process.py')), run_name='__main__')


if __name__ == '__main__':
    main()
