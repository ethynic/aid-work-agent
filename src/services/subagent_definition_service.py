"""
子智能体定义服务层

编排 SubagentDefinitionDB（定义）+ PromptRegistryService（Prompt 版本），
提供子智能体的完整生命周期管理。
"""

from typing import Optional, Dict, Any, List

from loguru import logger

from src.db.subagent_definition_db import SubagentDefinitionDB
from src.db.subagent_prompt_section_db import SubagentPromptSectionDB
from src.prompts.prompt_registry_service import PromptRegistryService


class SubagentDefinitionService:
    """子智能体定义服务"""

    # ========== 定义 CRUD ==========

    @staticmethod
    def create_definition(
        agent_id: str,
        name: str,
        system_prompt: str,
        description: Optional[str] = None,
        version: str = "1.0.0",
        author: Optional[str] = None,
        triggers: Optional[dict] = None,
        tools: Optional[dict] = None,
        skills: Optional[dict] = None,
        context: Optional[dict] = None,
        delegatable_to: Optional[list] = None,
        allow_delegation: bool = True,
        llm_provider: Optional[str] = None,
        reply_style: Optional[str] = None,
        business_pages: Optional[list] = None,
        knowledge_sources: Optional[list] = None,
        created_by: Optional[str] = None,
        commit_message: str = "初始版本",
    ) -> Optional[Dict[str, Any]]:
        """
        创建子智能体定义 + 注册 Prompt + 提交 V1 + 标记 production。
        整体操作：定义和 system_prompt 同时创建。
        """
        # 1. 创建定义
        definition = SubagentDefinitionDB.create(
            agent_id=agent_id,
            name=name,
            description=description,
            version=version,
            author=author,
            triggers=triggers or {},
            tools=tools or {},
            skills=skills or {},
            context=context or {},
            delegatable_to=delegatable_to or [],
            allow_delegation=allow_delegation,
            llm_provider=llm_provider,
            reply_style=reply_style,
            business_pages=business_pages,
            knowledge_sources=knowledge_sources or [],
            created_by=created_by,
        )
        if not definition:
            logger.error(f"创建子智能体定义失败: {agent_id}")
            return None

        # 2. 注册 Prompt
        prompt = PromptRegistryService.register_prompt(
            scope="subagent",
            scope_id=agent_id,
            display_name=name,
            description=description,
            created_by=created_by,
        )
        if not prompt:
            logger.error(f"注册 Prompt 失败: {agent_id}")
            SubagentDefinitionDB.delete(agent_id)
            return None

        prompt_id = str(prompt["id"])

        # 3. 提交 V1
        version_result = PromptRegistryService.commit_version(
            prompt_id=prompt_id,
            content=system_prompt,
            commit_message=commit_message,
            created_by=created_by,
        )
        if not version_result.get("version"):
            logger.error(f"提交初始版本失败: {agent_id}")
            SubagentDefinitionDB.delete(agent_id)
            PromptRegistryService.delete_prompt(prompt_id)
            return None

        # 4. 标记 production
        PromptRegistryService.set_label(
            prompt_id=prompt_id,
            label="production",
            version=version_result["version"]["version"],
            created_by=created_by,
        )

        return {
            "definition": definition,
            "prompt_id": prompt_id,
            "version": version_result["version"]["version"],
        }

    @staticmethod
    def get_definition(agent_id: str) -> Optional[Dict[str, Any]]:
        """获取子智能体定义 + 当前 production prompt"""
        definition = SubagentDefinitionDB.get_by_agent_id(agent_id)
        if not definition:
            return None

        prompt_info = SubagentDefinitionService._get_prompt_info(agent_id)
        return {**_serialize(definition), **(prompt_info or {})}

    @staticmethod
    def list_definitions(
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """列出子智能体定义（带 prompt 版本信息）"""
        result = SubagentDefinitionDB.list_definitions(status, page, page_size)
        for item in result["items"]:
            prompt_info = SubagentDefinitionService._get_prompt_info(item["agent_id"])
            if prompt_info:
                item.update(prompt_info)
        result["items"] = [_serialize(i) for i in result["items"]]
        return result

    @staticmethod
    def update_definition(
        agent_id: str,
        created_by: Optional[str] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """更新子智能体定义（不含 system_prompt，prompt 变更走版本管理 API）"""
        return SubagentDefinitionDB.update(agent_id, updated_by=created_by, **kwargs)

    @staticmethod
    def delete_definition(agent_id: str) -> bool:
        """删除子智能体定义 + 级联删除关联的 Prompt + 分段"""
        # 删除 prompt 分段
        SubagentPromptSectionDB.delete_sections(agent_id)

        # 删除 prompt（包含版本、标签、草稿）
        prompt = PromptRegistryService.get_prompt_by_scope(None, "subagent", agent_id)
        if prompt:
            PromptRegistryService.delete_prompt(str(prompt["id"]))

        return SubagentDefinitionDB.delete(agent_id)

    @staticmethod
    def update_system_prompt(
        agent_id: str,
        content: str,
        commit_message: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """提交 system_prompt 新版本并标记 production"""
        prompt = PromptRegistryService.get_prompt_by_scope(None, "subagent", agent_id)
        if not prompt:
            logger.error(f"未找到子智能体 {agent_id} 的 Prompt 注册")
            return None

        prompt_id = str(prompt["id"])
        result = PromptRegistryService.commit_version(
            prompt_id=prompt_id,
            content=content,
            commit_message=commit_message,
            created_by=created_by,
        )
        if result.get("version") and not result.get("dedup"):
            PromptRegistryService.set_label(
                prompt_id=prompt_id,
                label="production",
                version=result["version"]["version"],
                created_by=created_by,
            )
        return result

    # ========== Prompt 分段管理 ==========

    @staticmethod
    def get_sections(agent_id: str) -> List[Dict[str, Any]]:
        """获取智能体的 prompt 分段列表"""
        sections = SubagentPromptSectionDB.get_sections(agent_id)
        return [_serialize(s) for s in sections]

    @staticmethod
    def save_section(
        agent_id: str,
        section_key: str,
        content: str,
        updated_by: str = None,
    ) -> Optional[Dict[str, Any]]:
        """保存单个分段值（写入 subagent_prompt_sections 表）"""
        result = SubagentPromptSectionDB.upsert_section(agent_id, section_key, content, updated_by)
        if not result:
            return None
        # 刷新 Redis 缓存
        from src.core.cache_utils import CacheKeys, delete_cached
        delete_cached(CacheKeys.PROMPT_SECTIONS, agent_id)
        return _serialize(result)

    @staticmethod
    def get_section_keys(agent_id: str) -> List[str]:
        """从 production 版本的模板中解析出所有 {{section_key}} 变量名"""
        prompt_info = SubagentDefinitionService._get_prompt_info(agent_id)
        if not prompt_info:
            return []
        from src.prompts.prompt_registry_service import PromptRegistryService
        version_data = PromptRegistryService.get_version(
            str(prompt_info["prompt_id"]), prompt_info["production_version"]
        )
        if not version_data:
            return []
        import re
        template = version_data.get("content", "")
        # 匹配 {{variable}} 双花括号变量（Phase 4.0 起改用双花括号，避免与字面花括号冲突）
        keys = re.findall(r'\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}', template)
        # 去重保序
        seen = set()
        result = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                result.append(k)
        return result

    # ========== 辅助方法 ==========

    @staticmethod
    def _get_prompt_info(agent_id: str) -> Optional[Dict[str, Any]]:
        """获取子智能体关联的 prompt 信息"""
        from src.prompts.prompt_resolver import prompt_resolver

        registry = prompt_resolver._resolve_registry("subagent", agent_id, None)
        if not registry:
            return None

        prompt_id = str(registry["id"])

        # 获取 production 标签
        prod_label = PromptRegistryService.get_label(prompt_id, "production")

        return {
            "prompt_id": prompt_id,
            "latest_version": registry.get("latest_version", 0),
            "production_version": prod_label.get("version") if prod_label else None,
        }


def _serialize(record: Dict) -> Dict:
    """UUID/timestamp 转字符串，保留原生 JSONB 类型"""
    import uuid
    from datetime import datetime
    result = {}
    for k, v in record.items():
        if v is None:
            result[k] = None
        elif isinstance(v, (uuid.UUID, datetime)):
            result[k] = str(v)
        else:
            result[k] = v
    return result
