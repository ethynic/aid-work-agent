"""Prepared original Worker entry with fictional external ASR/token IO only.

Token retrieval is explicit external IO DI, not an actual GetToken exchange or
paid dispatch. Original Speech execute/POST/observer/parser and all Runner ports
are untouched. Endpoint assignment uses the existing ASR settings field.
"""
import os
from pathlib import Path
import runpy
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
    from src.config.settings import settings
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    settings.tools.asr.endpoint = endpoint.rstrip('/')

    async def fictional_external_token(self, access_key_id, access_key_secret):
        return token

    SpeechToTextTool._get_or_refresh_token = fictional_external_token
    # Explicit original profile-parameter DI only for the bounded budget node.
    # It still delegates to the original assembler/Engine/Worker.
    name = 'kf_admission_budget_process.py' if os.environ.get('KF_VOICE_ONE_ROUND') == 'true' else 'kf_admission_process.py'
    runpy.run_path(str(Path(__file__).with_name(name)), run_name='__main__')


if __name__ == '__main__':
    main()
