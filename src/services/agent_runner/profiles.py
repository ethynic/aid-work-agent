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

    def resolve(self, profile_id, tenant_id=None):
        registry = self.registry()
        # 有租户上下文时先解析租户数字员工实例（直用实例与副本实例），与
        # factory.get_runtime_config 的顺序一致——父定义（内置/DB 定义）常驻
        # registry，若先 registry.get 命中，实例行的 LLM/展示覆盖永远不生效；
        # 未命中实例（普通 DB 定义/内置 agent）回退 registry.get，行为不变。
        config = None
        if tenant_id:
            from src.subagents.tenant_resolver import resolve_tenant_config
            config = resolve_tenant_config(registry, tenant_id, profile_id)
        if config is None:
            # Custom DB-defined profiles use the same configuration parser as existing
            # callers, without obtaining their shared Agent/resource objects.
            config = registry.get(profile_id)
        if not config or config.dir_name != profile_id:
            raise RunnerError("PROFILE_NOT_FOUND", 404)
        fingerprint = hashlib.sha256(canonical_json(config.model_dump(mode="json")).encode()).hexdigest()
        return config, fingerprint


class MainProfileCatalog(ProfileCatalog):
    def resolve(self, profile_id, tenant_id=None):
        if profile_id != "main":
            return super().resolve(profile_id, tenant_id)
        from src.config.settings import settings
        fingerprint = hashlib.sha256(canonical_json(settings.agent.model_dump(mode="json")).encode()).hexdigest()
        return None, fingerprint
