"""Test-owned observation around the real worker CLI, without replacing behavior.

Only integer counters are recorded; arguments, credentials, messages and results
never enter this report. Each wrapped call delegates to the original method.
"""
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import tempfile


def main():
    from src.services.agent_runner import worker
    from src.services.agent_runner.runtime.attachments import AttachmentAssembler
    from src.services.agent_runner.runtime.compression import CompressionCoordinator
    from src.services.agent_runner.runtime.context_assembler import ContextAssembler
    from src.services.agent_runner.runtime.tools import ToolDispatcher

    report = Path(os.environ["RUNNER_TEST_ACTIVITY_REPORT"])
    if not report.is_absolute() or not report.parent.is_dir():
        raise ValueError("Observation report must use the existing owned fixture directory")
    counters = {}
    restorations = []

    def count(name):
        counters[name] += 1

    def observe(owner, method, name):
        original = getattr(owner, method)
        counters[name] = 0
        if inspect.isasyncgenfunction(original):
            @wraps(original)
            async def wrapper(*args, **kwargs):
                count(name)
                async for value in original(*args, **kwargs):
                    yield value
        elif inspect.iscoroutinefunction(original):
            @wraps(original)
            async def wrapper(*args, **kwargs):
                count(name)
                return await original(*args, **kwargs)
        else:
            @wraps(original)
            def wrapper(*args, **kwargs):
                count(name)
                return original(*args, **kwargs)
        setattr(owner, method, wrapper)
        restorations.append((owner, method, original))

    observe(worker.RuntimeFactory, "create", "runtime_create")
    observe(ContextAssembler, "prepare", "context_prepare")
    observe(CompressionCoordinator, "_run_compression_phase", "compression_prepare")
    observe(AttachmentAssembler, "prepare", "attachments_prepare")
    observe(ToolDispatcher, "dispatch", "tool_dispatch")
    original_mkdtemp = tempfile.mkdtemp
    counters["skill_workspace_create"] = 0

    @wraps(original_mkdtemp)
    def observed_mkdtemp(*args, **kwargs):
        # Actual attachment owner uses this prefix. Do not report any path or
        # alter the real directory creation, permissions or cleanup behavior.
        prefix = kwargs.get("prefix", args[1] if len(args) > 1 else tempfile.template)
        if prefix == "skill_ws_":
            count("skill_workspace_create")
        return original_mkdtemp(*args, **kwargs)

    tempfile.mkdtemp = observed_mkdtemp
    try:
        worker.main()
    finally:
        tempfile.mkdtemp = original_mkdtemp
        for owner, method, original in reversed(restorations):
            setattr(owner, method, original)
        report.write_text(json.dumps(counters, sort_keys=True))


if __name__ == "__main__":
    main()
