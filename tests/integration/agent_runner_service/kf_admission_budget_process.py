"""Declared profile-parameter DI; original assembler/Engine/Worker still run.

Only the original get_max_iterations input is bounded to one before prepare.
No execution result/checkpoint/owner/authority/IO method is fabricated.
"""
import runpy
from pathlib import Path

from src.services.agent_runner.runtime.context_assembler import ContextAssembler

original_prepare = ContextAssembler.prepare


class OneRoundProfile:
    def __init__(self, original):
        self.original = original

    def get_max_iterations(self, *args, **kwargs):
        return 1

    def __getattr__(self, name):
        if self.original is None:
            raise AttributeError(name)
        return getattr(self.original, name)


async def bounded_original_prepare(self, *args, **kwargs):
    original_profile = self.profile_config
    self.profile_config = OneRoundProfile(original_profile)
    try:
        return await original_prepare(self, *args, **kwargs)
    finally:
        self.profile_config = original_profile


ContextAssembler.prepare = bounded_original_prepare
runpy.run_path(str(Path(__file__).with_name('kf_admission_process.py')), run_name='__main__')
