#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Registry - Skill注册表

管理已加载的Skill，提供查询、匹配和访问接口。

主要功能:
1. Skill注册和注销
2. 按名称、文件类型、关键词匹配Skill
3. 生成Skill描述供LLM使用
4. 管理Skill生命周期
"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple
from loguru import logger

from src.core.skill_loader import SkillLoader, Skill


class SkillRegistry:
    """
    Skill注册表
    
    管理所有已加载的Skill，提供统一的访问接口。
    
    使用示例:
        registry = SkillRegistry()
        registry.load_from_directory(Path("skills"))

        # 获取Skill
        skill = registry.get("pdf")

        # 匹配Skill（基于 paths 字段的文件类型匹配）
        skill_name = registry.match_by_file("document.pdf")

        # 获取描述
        descriptions = registry.get_descriptions()
    """
    
    def __init__(self, skills_dir: Optional[Path] = None):
        """
        初始化Skill注册表

        Args:
            skills_dir: Skill目录路径，如果提供则自动加载
        """
        self._skills: Dict[str, Skill] = {}
        self._all_skills: Dict[str, Skill] = {}  # 未过滤的全集（供管理后台技能选择器使用）
        self._loader: Optional[SkillLoader] = None
        self._loaders: Dict[str, SkillLoader] = {}  # skill_name -> source loader（多目录支持）
        # base loaders 快照：最近一次全量加载（load_from_*，内置/插件/企业目录链）
        # 产生的 _loaders 副本，不含租户 overlay。skill_session 起底 _loaders 必须
        # 用本快照而非当前 _loaders——后者可能已被前一次租户执行原地改写，
        # 直接起底会把前一租户的 loader 映射带给下一个租户（跨租户串读 +
        # 插件同名场景 is_plugin_skill=False 使 M1 执行拦截失效）
        self._base_loaders: Dict[str, SkillLoader] = {}
        self._allowed: Optional[Set[str]] = None  # allow 名单
        # 插件审批门（M1）：name -> (computedHash, skillMdHash)，由 load_from_sources 填充；
        # 插件来源判定按 loader 目录身份（is_plugin_skill），不按名字键控——租户
        # overlay 覆盖同名插件时 _loaders 指向租户 loader，不会被误判为插件
        self._plugin_hashes: Dict[str, Tuple[str, str]] = {}
        self._plugin_loader_dirs: Set[Path] = set()
        # load_from_sources 加载链标记：该链下 loader miss 的 _skills 旧值
        # （如 --revoke 后租户 TTL 窗口）get_content 一律 not found，不走 body
        # 兜底泄漏手册（计划 §3.5/§6 半状态定义）
        self._loaded_from_sources: bool = False

        if skills_dir:
            self.load_from_directory(skills_dir)

    def load_from_directory(
        self,
        skills_dir: Path,
        allowed: Optional[List[str]] = None,
    ) -> int:
        """
        从目录加载 Skill

        Args:
            skills_dir: Skill 目录路径
            allowed: 允许加载的 skill 名称列表，None 或空列表表示不限制

        Returns:
            加载的Skill数量
        """
        self._loader = SkillLoader(skills_dir)
        self._allowed = set(allowed) if allowed else None
        self._loaded_from_sources = False
        # 切回目录加载链时清空插件来源状态：全局单例可能先经 load_from_sources
        # 加载（skill_executor 兜底路径），残留的 hash/目录集会对 loader miss
        # 场景产生假阳性拦截（is_plugin_skill 误判 / get_content 假阴性）
        self._plugin_hashes = {}
        self._plugin_loader_dirs = set()

        # 保留未过滤的全集（供管理后台技能选择器展示全部可选 skill）
        all_skills = self._loader.skills
        self._all_skills = dict(all_skills)

        # 只加载允许的 skills
        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in all_skills.items()
                if name in self._allowed
            }
            logger.info(f"SkillRegistry loaded {len(self._skills)} skills (filtered by allowed list: {self._allowed})")
        else:
            self._skills = all_skills
            logger.info(f"SkillRegistry loaded {len(self._skills)} skills from {skills_dir}")

        # 填充 _loaders 映射
        self._loaders = {name: self._loader for name in self._skills}
        self._base_loaders = dict(self._loaders)

        return len(self._skills)

    def load_from_directories(
        self,
        dirs: List[Path],
        allowed: Optional[List[str]] = None,
    ) -> int:
        """
        从多个目录加载 Skill，按优先级从低到高加载，高优先级目录覆盖同名 Skill。

        AgentSkills 标准支持多级发现（企业 > 个人 > 项目 > 插件）。
        服务器端映射：src/skills/ (基础) → storage/skills/enterprise/ (企业)。

        Args:
            dirs: Skill 目录列表（优先级从低到高）
            allowed: 允许加载的 skill 名称列表，None 或空列表表示不限制

        Returns:
            加载的 Skill 总数
        """
        self._allowed = set(allowed) if allowed else None
        self._loaded_from_sources = False
        # 同 load_from_directory：目录链加载不携带插件来源状态，清空防残留
        self._plugin_hashes = {}
        self._plugin_loader_dirs = set()
        combined_skills: Dict[str, Skill] = {}
        combined_loaders: Dict[str, SkillLoader] = {}

        for skills_dir in dirs:
            if not skills_dir.exists():
                continue
            loader = SkillLoader(skills_dir)
            # 后加载的目录覆盖先加载的同名 skill（高优先级覆盖低优先级）
            for name, skill in loader.skills.items():
                combined_skills[name] = skill
                combined_loaders[name] = loader

        # 保留未过滤的全集（供管理后台技能选择器展示全部可选 skill）
        self._all_skills = dict(combined_skills)

        # 过滤
        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in combined_skills.items()
                if name in self._allowed
            }
        else:
            self._skills = combined_skills

        # 保存 loader 映射和最后一个有效 loader（向后兼容）
        self._loaders = combined_loaders
        self._base_loaders = dict(self._loaders)
        self._loader = loader if dirs else None

        logger.info(f"SkillRegistry loaded {len(self._skills)} skills from {len(dirs)} directories")
        return len(self._skills)

    def load_from_sources(
        self,
        builtin_loader: SkillLoader,
        plugin_loaders: List[SkillLoader],
        plugin_hashes: Dict[str, Tuple[str, str]],
        allowed: Optional[List[str]] = None,
    ) -> int:
        """M1 专用加载入口：内置 loader（gate 构造，init 照旧）+ 已审批插件 loader。

        与 load_from_directory/load_from_directories 的差异见
        docs/system/runtime-plugin-host-architecture-design.md 第9节：

        - ``_loader`` 显式置 None（fail-closed）：多目录链下「最后目录」语义只会
          造成错位；插件 skill 的读出保护由 get_content 内容门（强制 loader 路径）
          承接，match_by_file 走 _loaders 多 loader 路径不受影响；
        - 填充 ``_plugin_hashes``（审批 hash，get_content Layer 2 用）与
          ``_plugin_loader_dirs``（插件 loader 目录身份，is_plugin_skill 用）；
        - 防御性复查「插件与内置同名拒绝」（gate 扫描已拒，此处兜底）。

        Args:
            builtin_loader: 内置目录 loader（run_init 默认 True，现状行为）
            plugin_loaders: 插件 loader 低 → 高有序列表（include + run_init=False）
            plugin_hashes: skill name -> (computedHash, skillMdHash)，仅含通过审批门的插件
            allowed: 允许加载的 skill 名称列表，None 或空列表表示不限制

        Returns:
            加载的 Skill 数量
        """
        self._allowed = set(allowed) if allowed else None
        combined: Dict[str, Skill] = {}
        combined_loaders: Dict[str, SkillLoader] = {}

        for name, skill in builtin_loader.skills.items():
            combined[name] = skill
            combined_loaders[name] = builtin_loader

        plugin_dirs: Set[Path] = set()
        for loader in plugin_loaders:
            plugin_dirs.add(Path(loader.skills_dir).resolve())
            for name, skill in loader.skills.items():
                if name in builtin_loader.skills:
                    logger.warning(f"拒绝插件 skill 与内置同名（内置优先）: {name} @ {loader.skills_dir}")
                    continue
                # 插件间同名：后加载（高优先级目录）覆盖先加载
                combined[name] = skill
                combined_loaders[name] = loader

        self._all_skills = dict(combined)

        if self._allowed is not None:
            self._skills = {
                name: skill
                for name, skill in combined.items()
                if name in self._allowed
            }
        else:
            self._skills = dict(combined)

        self._loaders = {name: combined_loaders[name] for name in self._skills}
        self._base_loaders = dict(self._loaders)
        # 仅保留实际来自插件 loader 的名字（哈希与 loader 身份一致，避免名字键控误伤）
        self._plugin_hashes = {
            name: hashes
            for name, hashes in plugin_hashes.items()
            if name in combined_loaders and combined_loaders[name] is not builtin_loader
        }
        self._plugin_loader_dirs = plugin_dirs
        # fail-closed：多目录链不保留「最后目录」单 loader 兜底
        self._loader = None
        self._loaded_from_sources = True

        logger.info(f"SkillRegistry loaded {len(self._skills)} skills from sources "
                    f"(builtin={len(builtin_loader.skills)}, plugins={len(plugin_loaders)}, "
                    f"plugin_skills={len(self._plugin_hashes)})")
        return len(self._skills)

    def register(self, skill: Skill) -> bool:
        """
        注册一个Skill

        Args:
            skill: Skill对象

        Returns:
            是否注册成功
        """
        if skill.name in self._skills:
            logger.warning(f"Skill already registered: {skill.name}")
            return False

        self._skills[skill.name] = skill

        logger.info(f"Registered skill: {skill.name}")
        return True
    
    def unregister(self, name: str) -> bool:
        """
        注销一个Skill

        Args:
            name: Skill名称

        Returns:
            是否注销成功
        """
        if name not in self._skills:
            logger.warning(f"Skill not found: {name}")
            return False

        del self._skills[name]

        logger.info(f"Unregistered skill: {name}")
        return True
    
    def get(self, name: str) -> Optional[Skill]:
        """
        获取Skill
        
        Args:
            name: Skill名称
            
        Returns:
            Skill对象，如果不存在返回None
        """
        return self._skills.get(name)

    def get_user_feedback(self, name: str) -> Optional[Dict[str, Any]]:
        """读取 Skill 的 user_feedback 等待提示策略（编排侧专用，Phase 1）。

        数据来源：SKILL.md frontmatter 的 ``metadata.user_feedback``
        （long_running / start_message），由 SkillLoader 解析后单独保存在
        Skill.metadata 字段——只供本访问器读取，不渲染进 Skill 正文、
        get_descriptions 或 Agent system prompt（设计 §6.2 上下文隔离）。

        Returns:
            user_feedback 字典（如 {"long_running": True, "start_message": "..."}）；
            Skill 不存在 / metadata 缺失 / 字段非字典时返回 None。
        """
        skill = self.get(name)
        if skill is None:
            return None
        metadata = getattr(skill, "metadata", None) or {}
        feedback = metadata.get("user_feedback") if isinstance(metadata, dict) else None
        return feedback if isinstance(feedback, dict) else None

    def get_execution_decl(self, name: str) -> Optional[Dict[str, Any]]:
        """读取 SKILL.md frontmatter ``metadata`` 中的执行声明（M1，计划 §3.8）。

        数据来源：``metadata.execution``（"server" | "device"）及可选的
        ``device_requirements`` / ``entry``。Skill.metadata 由 SkillLoader 解析，
        不新增 dataclass 字段。

        Returns:
            声明字典（如 {"execution": "device", "device_requirements": ...}）；
            skill 不存在 / metadata 无 execution 键 → None（调用方按默认 server 处理）；
            execution 值非法（非 server/device）→ warning + None（按 server 处理，
            fail-safe 不炸加载链）。
        """
        skill = self.get(name)
        if skill is None:
            return None
        metadata = getattr(skill, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        execution = metadata.get("execution")
        if execution is None:
            return None
        if execution not in ("server", "device"):
            logger.warning(f"Skill '{name}' metadata.execution 非法值: {execution!r}，按 server 处理")
            return None
        decl: Dict[str, Any] = {"execution": execution}
        for key in ("device_requirements", "entry"):
            value = metadata.get(key)
            if value is not None:
                decl[key] = value
        return decl

    def is_plugin_skill(self, name: str) -> bool:
        """判定 skill 是否来自外部插件目录（M1 执行边界 / 内容门共用）。

        按 loader 目录身份判定（loader.skills_dir ∈ 插件目录集），不按名字键控：
        租户 overlay 覆盖同名插件后 ``_loaders[name]`` 指向租户 loader（目录不在
        插件目录集），该 skill 按租户 skill 处理——可读可执行，不被插件拦截
        误伤（计划 §3.4 租户覆盖语义不动）。

        loader miss 但名字在审批插件集（``_plugin_hashes``）：视为插件残留
        （如租户 TTL 窗口内被撤销插件的旧 ``_skills`` 值），fail-closed 判为插件
        ——执行被拦截、get_content 不走 body 兜底泄漏手册（计划 §3.6 第 2 点）。

        loader 与审批 hash 均未命中时（如 --revoke/hash 不符触发签名链重建后，
        新实例 ``_plugin_hashes``/``_plugin_loader_dirs`` 为空，而租户 TTL 300s
        半状态又把旧合并结果注回 ``_skills``）：按 ``skill.dir`` 目录身份兜底
        ——落在插件根目录链（resolve_plugin_dirs）之下即判为插件，不依赖可被
        覆写/过期的哈希与 loader 状态，执行门保持 fail-closed（计划 §3.1
        「插件来源全量拦截」在半状态窗口内不失效）。
        """
        loader = self._loaders.get(name)
        if loader is not None:
            skills_dir = Path(getattr(loader, "skills_dir", "")).resolve()
            return skills_dir in self._plugin_loader_dirs
        if name in self._plugin_hashes:
            return True
        skill = self._skills.get(name)
        if skill is not None and self.is_plugin_dir(getattr(skill, "dir", None)):
            logger.warning(f"skill '{name}' loader/审批 hash 均未命中但目录身份为插件"
                           f"（疑似租户 TTL 半状态残留），按插件 fail-closed 处理")
            return True
        return False

    def is_plugin_dir(self, skill_dir) -> bool:
        """判定 skill 目录是否落在插件根目录链（resolve_plugin_dirs）之下。

        纯目录身份判定，不依赖 ``_plugin_hashes``/``_plugin_loader_dirs`` 的
        当前值（二者在签名链重建后为空、在 skill_session 覆写后可能过期）。
        供 ``is_plugin_skill`` 的 fail-closed 兜底与 skill_session 注回
        ``_skills`` 前的 stale 插件剔除共用。
        """
        if not skill_dir:
            return False
        try:
            resolved = Path(skill_dir).resolve()
        except (TypeError, OSError) as e:
            logger.warning(f"skill 目录无法解析，不按插件目录处理: {skill_dir!r}: {e}")
            return False
        for root in self._plugin_root_dirs():
            if resolved == root or root in resolved.parents:
                return True
        return False

    def _plugin_root_dirs(self) -> Set[Path]:
        """解析插件根目录链（仓库根 skills/ → storage skills/plugins/）。

        实时经 gate 解析（审批门唯一可信来源）；解析失败时退回当前
        ``_plugin_loader_dirs``（可能为空集，此时兜底判定趋于放行——settings
        为进程级单例，读取失败概率极低，如发生以 warning 暴露）。
        """
        try:
            from src.core.skill_plugin_gate import resolve_plugin_dirs
            return {Path(d).resolve() for d in resolve_plugin_dirs()}
        except Exception as e:
            logger.warning(f"插件根目录链解析失败，退回已注册插件 loader 目录: {e}")
            return set(self._plugin_loader_dirs)

    @property
    def plugin_hashes(self) -> Mapping[str, Tuple[str, str]]:
        """审批插件 hash 只读视图（name -> (computedHash, skillMdHash)）。"""
        return dict(self._plugin_hashes)
    
    def environment(self, name, *, tenant_id=None, env_vars=None, context=None):
        from src.core.skill_environment import resolve_skill_environment
        skill = self.get(name)
        if skill is None:
            raise ValueError("UNKNOWN_SKILL")
        return resolve_skill_environment(skill, tenant_id=tenant_id, env_vars=env_vars, context=context)

    def get_content(self, name: str, substitutions: Optional[Dict[str, str]] = None, *, tenant_id=None, env_vars=None, context=None) -> Optional[str]:
        """
        获取Skill内容

        Args:
            name: Skill名称
            substitutions: 可选的替换上下文，用于 $ARGUMENTS 等变量替换

        Returns:
            Skill内容字符串
        """
        # 优先使用 _loaders 精确定位（多目录场景）
        loader = self._loaders.get(name)
        if loader:
            # Layer 2 内容门（M1，计划 §3.3）：插件来源 skill 强制走 loader 路径并
            # 复核 SKILL.md sha256 == 审批 skillMdHash，不符返回 None（fail-closed，
            # 对抗审批后篡改；use_skill 侧转为「插件内容与审批时不符」错误）
            if self.is_plugin_skill(name):
                return self._get_plugin_content_checked(name, loader, substitutions,
                                                        tenant_id=tenant_id,
                                                        env_vars=env_vars, context=context)
            return loader.get_skill_content(name, substitutions=substitutions, tenant_id=tenant_id, env_vars=env_vars, context=context)

        # 插件残留（loader miss 但名字在审批插件集）：不走 _loader/body 兜底，
        # body 为 SKILL.md 正文全文，放行即泄漏篡改后手册
        if name in self._plugin_hashes:
            logger.warning(f"插件 skill '{name}' 无可用 loader（可能已被撤销/覆盖），内容不可读（fail-closed）")
            return None

        # Fallback 到单一 _loader（向后兼容；load_from_sources 链 _loader 为 None）
        if self._loader:
            return self._loader.get_skill_content(name, substitutions=substitutions, tenant_id=tenant_id, env_vars=env_vars, context=context)

        # load_from_sources 链的半状态（如 --revoke 后租户 TTL 窗口内 _skills
        # 旧值、loader 已随重建消失）：fail-closed 返回 None，不走 body 兜底
        if self._loaded_from_sources:
            return None

        # Last resort: 直接使用 skill body
        skill = self.get(name)
        if skill:
            return f"# Skill: {skill.name}\n\n{skill.body}"
        return None

    def _get_plugin_content_checked(
        self,
        name: str,
        loader: SkillLoader,
        substitutions: Optional[Dict[str, str]] = None,
        *,
        tenant_id=None, env_vars=None, context=None,
    ) -> Optional[str]:
        """插件手册读出前校验 SKILL.md sha256 与审批时一致（Layer 2 内容门）。

        已知接受的窄窗口（M1 CR nit）：校验对象为磁盘当前字节，返回对象为
        loader 构建期解析缓存的正文——构建期解析与审批 hash 之间存在极窄
        TOCTOU，需插件目录写权限且精确卡重建时序；插件目录受运维控制，接受
        该残留风险（如需收紧可改为按读出的字节重建正文）。
        """
        entry = self._plugin_hashes.get(name)
        if entry is not None:
            import hashlib
            skill = loader.skills.get(name) or self.get(name)
            skill_md_path = (skill.dir / "SKILL.md") if skill else None
            if skill_md_path is None or not skill_md_path.exists():
                logger.warning(f"插件 skill '{name}' SKILL.md 不可读: {skill_md_path}")
                return None
            try:
                md_hash = hashlib.sha256(skill_md_path.read_bytes()).hexdigest()
            except OSError as e:
                logger.warning(f"插件 skill '{name}' SKILL.md 读取失败: {e}")
                return None
            if md_hash != entry[1]:
                logger.warning(f"插件 skill '{name}' 内容与审批时不符（SKILL.md hash 不匹配），已暂停提供")
                return None
        return loader.get_skill_content(name, substitutions=substitutions,
                                        tenant_id=tenant_id, env_vars=env_vars, context=context)
    
    def get_descriptions(self) -> str:
        """
        获取所有Skill的描述
        
        用于LLM系统提示中展示可用Skill。
        
        Returns:
            Skill描述字符串
        """
        if not self._skills:
            return "(no skills available)"

        return "\n".join(
            f"- {name} (v{skill.version}): {skill.description}"
            for name, skill in self._skills.items()
        )
    
    def list_skills(self) -> List[str]:
        """
        列出所有Skill名称

        Returns:
            Skill名称列表
        """
        return list(self._skills.keys())

    def list_all_loaded_skills(self) -> List[str]:
        """返回所有已加载 skill 名称（未经 allowed 过滤）。

        供管理后台技能选择器使用，需展示全部可选 skill，
        而不是仅主智能体白名单内的 skill。
        """
        return list(self._all_skills.keys())

    def get_all_skill(self, name: str) -> Optional[Skill]:
        """按名称获取未过滤全集中的 skill（供 API 取 description）。

        与 get() 的区别：get() 只在 allowed 过滤后的 _skills 中查找，
        本方法在未过滤的全集 _all_skills 中查找，能取到被白名单排除的 skill。
        """
        return self._all_skills.get(name)
    
    def match_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Skill（基于 paths 字段）

        Args:
            filename: 文件名

        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        # 多 loader 场景：遍历所有 loader 查找匹配
        for loader in set(self._loaders.values()):
            result = loader.match_by_file(filename)
            if result and result in self._skills:
                return result

        # Fallback 到单一 loader
        if self._loader:
            return self._loader.match_by_file(filename)
        return None

    def is_allowed(self, skill_name: str) -> bool:
        """检查 skill 是否在允许列表中"""
        if self._allowed is None:
            return True  # 无限制时都允许
        return skill_name in self._allowed

    def get_allowed_list(self) -> Optional[List[str]]:
        """获取允许的 skills 列表"""
        return list(self._allowed) if self._allowed is not None else None

    def get_skill_tool_definition(self) -> Dict:
        """
        获取Skill工具定义（仅包含允许的 skills）

        用于LLM function calling。

        Returns:
            工具定义字典
        """
        if not self._skills:
            return {
                "name": "use_skill",
                "description": "暂无可用技能",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill": {
                            "type": "string",
                            "description": "要加载的技能名称"
                        }
                    },
                    "required": ["skill"],
                },
            }

        # 只生成允许的 skills 描述（含 argument_hint）
        skill_list = "\n".join(
            f"- {name}: {skill.description}" + (f" (args: {skill.argument_hint})" if skill.argument_hint else "")
            for name, skill in self._skills.items()
        )

        return {
            "name": "use_skill",
            "description": f"""加载技能，获取完整的操作指南（SKILL.md 正文）。技能本质是给 LLM 的操作手册，加载后根据手册指引决定下一步操作。

**使用流程：**
1. 调用 use_skill(skill="技能名") 加载技能，获取操作指南
2. 仔细阅读返回的操作指南，根据其中的指引执行下一步：
   - 手册要求执行脚本/命令 → 调用 skill_execute
   - 手册要求生成内容 → 调用 content_generate
   - 手册要求搜索信息 → 调用 web_search
   - 手册给出多步骤工作流 → 按步骤逐步执行
3. 所有步骤完成后，直接给出最终回复

**注意：** 不要跳过步骤，严格按操作指南执行。不同技能的行为完全由其操作指南决定（有些需要执行脚本，有些是纯引导式的工作流）。

可用技能：
{skill_list}""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "要加载的技能名称"
                    }
                },
                "required": ["skill"],
            },
        }
    
    def reload(self) -> int:
        """
        重新加载所有Skill

        load_from_sources 加载链（M1 插件目录链）不支持 reload：_loader 为 None，
        调用时 warning 并返回当前数量；插件/审批变更由 resource_cache 签名刷新
        整体重建（docs/system/runtime-plugin-host-architecture-design.md 第9节）。

        Returns:
            加载的Skill数量
        """
        if self._plugin_loader_dirs:
            logger.warning("reload() 不支持 load_from_sources 加载链（插件目录链），"
                           "已忽略；等待进程缓存签名刷新重建")
            return len(self._skills)
        if self._loader:
            self._loader.reload_skills()
            self._skills = self._loader.skills
            logger.info(f"SkillRegistry reloaded {len(self._skills)} skills")
        return len(self._skills)
    
    def __len__(self) -> int:
        return len(self._skills)
    
    def __contains__(self, name: str) -> bool:
        return name in self._skills
    
    def __iter__(self):
        return iter(self._skills.items())


# 全局Skill注册表实例
skill_registry = SkillRegistry()
