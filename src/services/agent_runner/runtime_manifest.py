"""Private hashes of runtime configuration; no keys or environment values stored."""

import hashlib
from pathlib import Path

from .contracts import canonical_json


def legacy_configuration_fingerprint(profile_fingerprint):
    """Recognize an unchanged v2 checkpoint; never infer an unknown old hash."""
    from src.config.settings import settings
    root = Path(__file__).resolve().parents[2] / 'prompts'
    prompts = {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(root.rglob('*')) if path.is_file() and path.suffix in {'.md','.yaml','.yml','.txt','.py'}}
    private = {'version':2,'profile_fingerprint':profile_fingerprint,
               'llm':settings.llm.model_dump(mode='json'),
               'agent':settings.agent.model_dump(mode='json'),'prompt_files':prompts}
    return hashlib.sha256(canonical_json(private).encode()).hexdigest()


def configuration_fingerprint(profile_fingerprint, profile_config=None):
    """Hash this execution's model routes; its actual prompt is in checkpoint.

    Unused prompt files, other providers and rotating credentials are not the
    execution's semantic dependencies. Profile changes remain independently
    checked by the recovery coordinator.
    """
    from src.config.settings import settings
    llm = settings.llm
    provider = getattr(profile_config, 'llm_provider', None) or llm.provider
    overrides = getattr(profile_config, 'llm_model_codes', None) or {}
    names = [provider]
    if llm.failover.enabled:
        names.extend(llm.failover.providers)
    lite_provider, lite_model = llm.get_lite_target()
    routes = []
    for name in dict.fromkeys(names):
        config = getattr(llm, name).model_dump(mode='json', exclude={'api_key', 'api_keys'})
        config['model'] = overrides.get(name) or config['model']
        routes.append({'provider': name, 'config': config,
            'max_tokens': llm.model_max_tokens.get(config['model'])})
    lite_config = getattr(llm, lite_provider).model_dump(mode='json', exclude={'api_key', 'api_keys'})
    lite_config['model'] = lite_model
    private = {'version': 3, 'profile_fingerprint': profile_fingerprint,
        'routes': routes, 'failover_enabled': llm.failover.enabled,
        'enable_thinking': llm.enable_thinking, 'context_cache': llm.context_cache,
        'lite': {'provider': lite_provider, 'config': lite_config,
                 'max_tokens': llm.model_max_tokens.get(lite_model)}}
    return hashlib.sha256(canonical_json(private).encode()).hexdigest()


def configuration_matches(stored, profile_fingerprint, profile_config=None):
    if stored == configuration_fingerprint(profile_fingerprint, profile_config):
        return True
    # v2 had no dependency manifest. Only an exact match can migrate safely.
    return stored == legacy_configuration_fingerprint(profile_fingerprint)
