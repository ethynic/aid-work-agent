"""Profile configuration resolution without a legacy Agent or process global runner."""

import hashlib
from pathlib import Path
from .contracts import RunnerError, canonical_json


class ProfileCatalog:
    @staticmethod
    def registry():
        from src.services.agent_runner.runtime.resource_cache import cached_subagent_registry
        return cached_subagent_registry(Path(__file__).resolve().parents[3] / "subagents")

    def default_profile(self, identity):
        if identity.tenant_id is None or identity.user_id is None:
            return "main"
        from src.db.models import UserDB
        from src.saas.permissions.checker import get_allowed_agent_ids_for_user
        user = UserDB.get_by_id(identity.user_id)
        allowed = set(get_allowed_agent_ids_for_user(user, target_tenant_id=identity.tenant_id))
        available = [item['agent_id'] for item in self.registry().get_all_subagents_with_type()
                     if item['agent_id'] in allowed]
        return available[0] if len(available) == 1 else "main"

    def resolve(self, profile_id):
        registry = self.registry()
        # Custom DB-defined profiles use the same configuration parser as existing
        # callers, without obtaining their shared Agent/resource objects.
        config = registry.get(profile_id)
        if not config or config.dir_name != profile_id:
            raise RunnerError("PROFILE_NOT_FOUND", 404)
        fingerprint = hashlib.sha256(canonical_json(config.model_dump(mode="json")).encode()).hexdigest()
        return config, fingerprint


class MainProfileCatalog(ProfileCatalog):
    def resolve(self, profile_id):
        if profile_id != "main":
            return super().resolve(profile_id)
        from src.config.settings import settings
        fingerprint = hashlib.sha256(canonical_json(settings.agent.model_dump(mode="json")).encode()).hexdigest()
        return None, fingerprint
