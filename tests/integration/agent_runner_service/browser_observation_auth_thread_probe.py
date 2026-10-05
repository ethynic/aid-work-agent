"""Hold the original synchronous owner auth call, never replace its result."""
from pathlib import Path
import runpy
import sys
import time

report, release = map(Path,sys.argv[1:3])
arguments = sys.argv[3:]
from src.services.agent_runner.browser_web_auth import BrowserWebAuth
original = BrowserWebAuth.authorize_view
calls = 0


def held_original(self, assertion):
    global calls
    calls += 1
    if calls == 2:
        report.touch()
        while not release.exists():
            time.sleep(.01)
    return original(self,assertion)

BrowserWebAuth.authorize_view = held_original
sys.argv = ['worker',*arguments]
runpy.run_module('src.services.agent_runner.worker',run_name='__main__')
