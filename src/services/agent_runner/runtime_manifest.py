"""Private hashes of runtime configuration; no keys or environment values stored."""

import hashlib
from pathlib import Path

from .contracts import canonical_json


def configuration_fingerprint(profile_fingerprint):
    from src.config.settings import settings
    root = Path(__file__).resolve().parents[2] / 'prompts'
    prompts = {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(root.rglob('*')) if path.is_file() and path.suffix in {'.md','.yaml','.yml','.txt','.py'}}
    private = {'version':1,'profile_fingerprint':profile_fingerprint,
               'llm':settings.llm.model_dump(mode='json'),
               'agent':settings.agent.model_dump(mode='json'),'prompt_files':prompts}
    return hashlib.sha256(canonical_json(private).encode()).hexdigest()
