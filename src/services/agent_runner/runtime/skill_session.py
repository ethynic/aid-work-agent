from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Optional, Dict
from loguru import logger

class SkillSession:
    def __init__(self, tenant_id, registry, memory):
        self._init_tenant_id = tenant_id
        self.skill_registry, self.memory = registry, memory
        self._loaded_tenant_id = None
        self._skills_loaded_at = 0
    def check(self, state, skill_name):
        current = self.skill_registry.get(skill_name)
        if current is None:
            return None
        historical = state.loaded_skills.get(skill_name)
        if historical is None:
            calls = {}
            for message in state.messages:
                for call in message.get("tool_calls", []):
                    fn = call.get("function", {})
                    if fn.get("name") == "use_skill":
                        arguments = fn.get("arguments", {})
                        try:
                            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                        except ValueError:
                            continue
                        if arguments.get("skill") == skill_name:
                            calls[call.get("id")] = skill_name
                if message.get("role") == "tool" and message.get("tool_call_id") in calls:
                    try:
                        payload = json.loads(message.get("content", "{}"))
                    except (ValueError, TypeError):
                        continue
                    if isinstance(payload, dict):
                        historical = payload.get("skill_version")
        if historical == current.version:
            return None
        return {"success": False, "error": f"技能指南版本不匹配，请先调用 use_skill(skill=\"{skill_name}\") 重新加载最新指南", "skill_name": skill_name,
                "historical_version": historical, "current_version": current.version}

    def _ensure_tenant_skills_loaded(self):
        """Load the explicitly bound tenant catalogue into this execution's registry."""
        tenant_id = self._init_tenant_id
        if not tenant_id:
            return

        # 已为该租户加载且缓存仍新鲜，跳过
        if self._loaded_tenant_id == tenant_id:
            from src.saas.services.tenant_skill_cache import tenant_skill_cache
            cached = tenant_skill_cache._cache.get(tenant_id)
            if cached and self._skills_loaded_at >= cached[1]:
                return

        # 加载合并后的 skills（base 部分经审批门：内置 + 已审批插件，见
        # tenant_skill_cache._load_and_merge；插件 loader 由 gate 构造）
        from src.saas.services.tenant_skill_cache import tenant_skill_cache
        from src.saas.services.skill_resolver import SkillResolver

        base_skills_dir = Path(__file__).resolve().parents[4] / "src" / "skills"
        skills_dict = tenant_skill_cache.get_or_load(
            tenant_id, base_skills_dir, self.skill_registry._allowed,
        )

        # 租户 skills 目录解析（mkdir 失败不抛垮 processor）
        try:
            tenant_dir = SkillResolver.get_tenant_skills_dir(tenant_id)
        except Exception as e:
            # mkdir 失败（权限/磁盘满）不应抛垮 processor，跳过租户自定义 skills
            logger.warning(f"获取租户 {tenant_id} skills 目录失败，跳过租户自定义 skills: {e}")
            tenant_dir = None
        tenant_loader = None
        if tenant_dir and tenant_dir.exists():
            from src.core.skill_loader import SkillLoader
            tenant_loader = SkillLoader(tenant_dir)

        # 重建 _loaders 映射：以 registry 的 base loaders 快照起底（内置 + 插件，
        # load_from_* 全量加载时维护，不含任何租户 overlay），租户 loader 覆盖其上。
        # 替代旧 `registry._loader` 单 loader 起底——多目录链下 _loader 为 None
        # （load_from_sources）或指向最后目录（load_from_directories）的错位隐患
        # （计划 §3.7）。租户覆盖同名插件后按目录身份判定为租户 skill，
        # 不被插件执行拦截/内容门误伤。
        # 不得以 registry._loaders 当前值起底：resource_cache 共享实例被前一次
        # 租户执行原地改写后，其中已含该租户的 loader 映射——下一个租户起底会
        # 带入残留（本租户未覆盖同名时不被冲掉），导致 get_content 经 stale
        # loader 串读前一租户手册、插件同名场景 is_plugin_skill=False 使 M1
        # 执行拦截失效（跨租户串读回归，CR 阻断级意见）。
        base_loaders = getattr(self.skill_registry, "_base_loaders", None)
        if base_loaders is None:
            # 兼容未维护快照的 registry：按目录身份过滤当前 _loaders，仅保留
            # 内置目录与插件目录的 loader（无法识别目录身份的 loader 保守丢弃，
            # 宁少勿串）。AgentRunner 链（load_from_sources）始终维护快照，不走此分支。
            plugin_dirs = set()
            for d in getattr(self.skill_registry, "_plugin_loader_dirs", ()) or ():
                try:
                    plugin_dirs.add(Path(d).resolve())
                except (TypeError, OSError):
                    continue
            builtin_root = base_skills_dir.resolve()
            base_loaders = {}
            for name, loader in dict(self.skill_registry._loaders).items():
                try:
                    loader_dir = Path(loader.skills_dir).resolve()
                except (TypeError, OSError):
                    continue
                if loader_dir == builtin_root or loader_dir in plugin_dirs:
                    base_loaders[name] = loader

        new_loaders = dict(base_loaders)
        if tenant_loader:
            for name in tenant_loader.skills:
                new_loaders[name] = tenant_loader  # 租户覆盖基础/插件

        # 撤销/hash 不符触发的租户 TTL 300s 半状态：缓存合并结果可能带出当前
        # registry 已剔除的插件 skill（新实例 _plugin_hashes 不含）。注回 _skills
        # 前按目录身份剔除，避免已撤销插件重新出现在工具描述与 registry.get；
        # 执行门另由 is_plugin_skill 的目录身份 fail-closed 兜底双保险（计划 §3.1）。
        current_plugin_hashes = getattr(self.skill_registry, "_plugin_hashes", None)
        is_plugin_dir = getattr(self.skill_registry, "is_plugin_dir", None)
        if current_plugin_hashes is not None and callable(is_plugin_dir):
            dropped = sorted(
                name for name, skill in skills_dict.items()
                if name not in current_plugin_hashes and is_plugin_dir(getattr(skill, "dir", None))
            )
            if dropped:
                # 构造新 dict 而非原地删：skills_dict 可能是 tenant_skill_cache
                # 缓存内的全量 dict（allowed=None 时原样返回），原地改会污染进程缓存
                skills_dict = {
                    name: skill for name, skill in skills_dict.items() if name not in set(dropped)
                }
                logger.warning(f"租户 {tenant_id} 合并结果含 {len(dropped)} 个当前审批集外"
                               f"的插件 skill（TTL 半状态残留），已剔除: {dropped}")

        # 应用到当前 registry
        self.skill_registry._skills = dict(skills_dict)
        self.skill_registry._loaders = new_loaders

        self._loaded_tenant_id = tenant_id
        self._skills_loaded_at = time.time()

    def _check_skill_version_consistency(
        self, session_id: str, skill_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        校验该 skill 在本会话上下文中的版本是否与 registry 当前版本一致。

        Args:
            session_id: 会话ID
            skill_name: 技能名称

        Returns:
            None  → 通过，可继续执行
            dict  → 拦截结果（含 success=False 和提示消息），直接作为 skill_execute 返回给 LLM
        """
        if not self.skill_registry:
            return None
        skill_obj = self.skill_registry.get(skill_name)
        if not skill_obj:
            # skill 不存在交给后续原逻辑报错
            return None
        current_version = skill_obj.version

        historical_version = self._get_last_use_skill_version(session_id, skill_name)

        if historical_version == current_version:
            return None

        # 不一致 / 无历史记录 / 历史返回缺字段 → 拦截
        if historical_version is None:
            reason = f"本会话尚未加载过该技能的最新指南（当前版本 v{current_version}）"
        else:
            reason = f"技能版本已更新（历史 v{historical_version} → 当前 v{current_version}）"
        logger.info(f"后端日志：skill_execute 被版本校验拦截", extra={
            "skill_name": skill_name,
            "historical_version": historical_version,
            "current_version": current_version,
        })
        return {
            "success": False,
            "error": (
                f"⚠️ {reason}。请先调用 use_skill(skill=\"{skill_name}\") 重新加载最新操作指南，"
                f"然后再调用 skill_execute 执行脚本。"
            ),
            "skill_name": skill_name,
            "historical_version": historical_version,
            "current_version": current_version,
        }

    def _get_last_use_skill_version(
        self, session_id: str, skill_name: str
    ) -> Optional[str]:
        """
        扫描 session 历史消息，找到该 skill 最近一次 use_skill 调用返回的 skill_version。

        识别方式：
        - 遍历 memory 中 role=assistant 且带 tool_calls 的消息，找到 function.name == "use_skill"
          且 arguments.skill == skill_name 的调用，记录其 tool_call_id。
        - 再在 role=tool 的消息里按 tool_call_id 取出 content（JSON 字符串），
          解析后取 skill_version 字段。

        Args:
            session_id: 会话ID
            skill_name: 技能名称

        Returns:
            最近一次 use_skill 返回的 skill_version；没找到返回 None。
        """
        import json as _json
        try:
            messages = self.memory.get_context(session_id) or []
        except Exception as e:
            logger.warning(f"后端日志：读取 memory 失败: {e}")
            return None

        # 第一遍：找到该 skill 最近一次 use_skill 调用的 tool_call_id
        target_tool_call_id = None
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls") or []
            for tc in tool_calls:
                try:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    if fn.get("name") != "use_skill":
                        continue
                    args_raw = fn.get("arguments", "{}")
                    args = _json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                    if args.get("skill") == skill_name:
                        target_tool_call_id = tc.get("id")
                        break
                except (ValueError, TypeError):
                    continue
            if target_tool_call_id:
                break

        if not target_tool_call_id:
            return None

        # 第二遍：按 tool_call_id 找 tool 返回中的 skill_version
        for msg in reversed(messages):
            if msg.get("role") != "tool":
                continue
            if msg.get("tool_call_id") != target_tool_call_id:
                continue
            content = msg.get("content", "")
            try:
                payload = _json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                return None
            if isinstance(payload, dict):
                return payload.get("skill_version")
        return None

