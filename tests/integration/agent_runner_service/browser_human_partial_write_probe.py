"""Real original stdin receives a partial frame before an external IO failure."""
import json
from pathlib import Path
import sys


def install_partial_write(report):
    import src.tools.browser.executor.local as local
    from src.tools.browser.owner_port import current_human_action_guard
    from src.tools.browser.worker_protocol import encode_frame

    original = local.write_frame
    failed = False
    async def write(writer, message, *args, **kwargs):
        nonlocal failed
        operation = current_human_action_guard()
        if not failed and message.get('type') == 'keyboard' and message.get('key') == 'P':
            failed = True
            wire = encode_frame(message)
            writer.write(wire[:2])
            await writer.drain()
            Path(report).with_suffix('.partial.json').write_text(json.dumps({
                'actual_written_bytes': 2,
                'write_started': bool(operation and operation.write_started),
                'frame_written': bool(operation and operation.frame_written)}))
            raise OSError('Fixture external partial IPC transport failure')
        return await original(writer, message, *args, **kwargs)
    local.write_frame = write


if __name__ == '__main__':
    report, *arguments = sys.argv[1:]
    from tests.integration.agent_runner_service.browser_human_service_probe import install
    install(report)
    install_partial_write(report)
    sys.argv = ['worker', *arguments]
    from src.services.agent_runner.worker import main
    main()
