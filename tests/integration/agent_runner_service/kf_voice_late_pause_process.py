"""Real Read result timing and external Token IO timing, no fabricated result.

The original Read executes on one owned text file before its unchanged result
is held. A ContextVar only observes the original DurableControl boundary.
"""
import asyncio
from contextvars import ContextVar
import json
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
    paths = {name: Path(os.environ[name]).resolve() for name in (
        'KF_VOICE_TOKEN_READY', 'KF_VOICE_TOKEN_RELEASE',
        'KF_VOICE_READ_READY', 'KF_VOICE_READ_RELEASE', 'KF_VOICE_READ_INPUT')}
    for name, path in paths.items():
        prefix = 'voice-token-' if 'TOKEN' in name else 'voice-read-'
        if path.parent != root or not path.name.startswith(prefix):
            raise RuntimeError('OWNED_VOICE_GATE_REQUIRED')
    from src.config.settings import settings
    from src.tools.asr.speech_to_text_tool import SpeechToTextTool
    from src.tools.file.read_tool import ReadTool
    from src.services.agent_runner.durable_control import DurableControl
    settings.tools.asr.endpoint = endpoint.rstrip('/')
    boundary = ContextVar('fixture_observed_original_boundary', default=('none', False))
    original_save = DurableControl._save_locked
    original_read = ReadTool.execute
    reads = []

    async def observe_original_save(self, state, name):
        mark = boundary.set((name, state.execution_id == self.attempt.runner_id))
        try:
            return await original_save(self, state, name)
        finally:
            boundary.reset(mark)

    async def wait_owned_release(path):
        deadline = time.monotonic() + 20
        while not path.is_file():
            if time.monotonic() >= deadline:
                raise RuntimeError('OWNED_VOICE_GATE_TIMEOUT')
            await asyncio.sleep(.025)

    async def original_read_then_hold_result(self, **arguments):
        result = await original_read(self, **arguments)
        if Path(arguments.get('file_path', '')).resolve() == paths['KF_VOICE_READ_INPUT']:
            # Exact original object is returned after the timing barrier.
            success = (isinstance(result, dict) and result.get('read_lines') == 1
                and result.get('total_lines') == 1 and result.get('content') == '1\tvoice-original-read-success')
            reads.append(success)
            paths['KF_VOICE_READ_READY'].write_text(json.dumps({'read_calls': len(reads), 'success': success}))
            await wait_owned_release(paths['KF_VOICE_READ_RELEASE'])
        return result

    async def held_fictional_external_token(self, access_key_id, access_key_secret):
        name, is_root = boundary.get()
        paths['KF_VOICE_TOKEN_READY'].write_text(json.dumps({
            'boundary': name, 'root': is_root, 'read_calls': len(reads), 'read_success': reads == [True]}))
        await wait_owned_release(paths['KF_VOICE_TOKEN_RELEASE'])
        return token

    DurableControl._save_locked = observe_original_save
    ReadTool.execute = original_read_then_hold_result
    SpeechToTextTool._get_or_refresh_token = held_fictional_external_token
    runpy.run_path(str(Path(__file__).with_name('kf_admission_process.py')), run_name='__main__')


if __name__ == '__main__':
    main()
