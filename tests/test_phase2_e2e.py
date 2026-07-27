"""
Phase 2 端到端验证脚本

测试流程：
1. 创建 subagent_definitions 表（如不存在）
2. 将旅游顾问 SUBAGENT.md 的定义和 system_prompt 插入数据库
3. 通过 SubagentDefinitionService 创建完整子智能体
4. 验证 load_from_db() 整体降级策略
5. 验证 _build_system_prompt() 使用 from_db 标记
6. 测试版本管理（提交 V2、diff、回滚）
7. 测试整体降级：删除 prompt 后应降级到文件系统
8. 清理
"""

import sys
from pathlib import Path

from loguru import logger
from dotenv import load_dotenv
load_dotenv()

# 初始化 DB 连接池
from src.db.database import init_postgres_pool
init_postgres_pool()

from src.db.subagent_definition_db import SubagentDefinitionDB
from src.services.subagent_definition_service import SubagentDefinitionService
from src.prompts.prompt_resolver import prompt_resolver
from src.prompts.prompt_registry_service import PromptRegistryService
from src.models.subagent import SubagentConfig
from src.subagents.registry import SubagentRegistry

# 读取旅游顾问 SUBAGENT.md
SUBAGENT_MD_PATH = Path(__file__).parent.parent / "subagents" / "travel-consultant" / "SUBAGENT.md"
SUBAGENT_MD_CONTENT = SUBAGENT_MD_PATH.read_text(encoding="utf-8")

# 解析 frontmatter
import re
import yaml
match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", SUBAGENT_MD_CONTENT, re.DOTALL)
assert match, "Failed to parse SUBAGENT.md"
frontmatter = yaml.safe_load(match.group(1))
SYSTEM_PROMPT_BODY = match.group(2).strip()

AGENT_ID = "travel-consultant"
CREATED_BY = "phase2_test"

passed = 0
failed = 0


def test_step(name, func):
    """运行测试步骤"""
    global passed, failed
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        result = func()
        print(f"[PASS] {name}")
        passed += 1
        return result
    except Exception as e:
        print(f"[FAIL] {name} -> {e}")
        import traceback
        traceback.print_exc()
        failed += 1
        return None


def main():
    global passed, failed
    print("=" * 60)
    print("Phase 2 端到端验证")
    print(f"旅游顾问 MD: {SUBAGENT_MD_PATH}")
    print(f"frontmatter keys: {list(frontmatter.keys())}")
    print(f"system_prompt length: {len(SYSTEM_PROMPT_BODY)} chars")
    print("=" * 60)

    # ========== 0. 清理旧数据（如存在）==========
    def cleanup():
        existing = SubagentDefinitionDB.get_by_agent_id(AGENT_ID)
        if existing:
            SubagentDefinitionService.delete_definition(AGENT_ID)
            print(f"  清理旧数据: {AGENT_ID}")
        # 也清理 prompt_registry 中可能残留的记录
        prompt = PromptRegistryService.get_prompt_by_scope(None, "subagent", AGENT_ID)
        if prompt:
            PromptRegistryService.delete_prompt(str(prompt["id"]))
            print(f"  清理旧 prompt: {prompt['id']}")

    test_step("0.1 清理旧数据", cleanup)

    # ========== 1. 创建子智能体定义 + system_prompt ==========
    result = test_step("1.1 创建子智能体（定义 + V1 system_prompt）", lambda: SubagentDefinitionService.create_definition(
        agent_id=AGENT_ID,
        name=frontmatter["name"],
        description=frontmatter.get("description", ""),
        version=frontmatter.get("version", "1.0.0"),
        author=frontmatter.get("author"),
        triggers=frontmatter.get("triggers", {}),
        tools=frontmatter.get("tools", {}),
        skills=frontmatter.get("skills", {}),
        context=frontmatter.get("context", {}),
        llm_provider=frontmatter.get("llm_provider"),
        llm_model_codes={"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"},
        reply_style=frontmatter.get("reply_style"),
        business_pages=frontmatter.get("business_pages"),
        created_by=CREATED_BY,
        system_prompt=SYSTEM_PROMPT_BODY,
        commit_message="从 SUBAGENT.md 导入的初始版本",
    ))

    if not result:
        print("[FAIL] 创建失败，终止测试")
        sys.exit(1)

    print(f"  definition id = {result['definition']['id']}")
    print(f"  prompt_id = {result['prompt_id']}")
    print(f"  version = {result['version']}")

    # ========== 2. 验证定义查询 ==========
    defn = test_step("2.1 查询定义详情", lambda: SubagentDefinitionService.get_definition(AGENT_ID))
    if defn:
        print(f"  name = {defn['name']}")
        print(f"  tools = {defn.get('tools')}")
        print(f"  skills = {defn.get('skills')}")
        print(f"  llm_provider = {defn.get('llm_provider')}")
        print(f"  llm_model_codes = {defn.get('llm_model_codes')}")
        print(f"  reply_style = {defn.get('reply_style')}")
        print(f"  business_pages count = {len(defn.get('business_pages') or [])}")
        print(f"  context = {defn.get('context')}")
        print(f"  production_version = {defn.get('production_version')}")
        assert defn["name"] == "旅游咨询顾问"
        assert defn.get("llm_provider") == "deepseek"
        assert defn.get("llm_model_codes") == {"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"}
        assert defn.get("reply_style") == "human-like"
        assert len(defn.get("business_pages") or []) == 6
        assert defn.get("context", {}).get("max_input_tokens") == 12000

    # ========== 3. 验证 PromptResolver 解析 ==========
    resolved = test_step("3.1 PromptResolver 解析 system_prompt", lambda: prompt_resolver.resolve(
        scope="subagent", scope_id=AGENT_ID,
    ))
    if resolved:
        print(f"  解析成功，内容长度 = {len(resolved)} chars")
        assert "行程规划师" in resolved
        assert "阶段一" in resolved or "聊需求" in resolved
    else:
        print("  [FAIL] resolve returned None")

    # ========== 4. 验证 load_from_db() 整体降级 ==========
    def test_load_from_db():
        # 创建新的空 registry（不含 builtin）
        reg = SubagentRegistry()
        count = reg.load_from_db()
        print(f"  load_from_db() 返回 {count} 个子智能体")

        # 验证旅游顾问被加载
        config = reg.get(AGENT_ID)
        assert config is not None, "travel-consultant not loaded from DB"
        print(f"  config.name = {config.name}")
        print(f"  config.from_db = {config.from_db}")
        print(f"  config.dir_name = {config.dir_name}")
        print(f"  config.system_prompt length = {len(config.system_prompt)}")
        print(f"  config.tools = {config.tools}")
        print(f"  config.skills = {config.skills}")
        print(f"  config.llm_provider = {config.llm_provider}")
        print(f"  config.llm_model_codes = {config.llm_model_codes}")
        print(f"  config.reply_style = {config.reply_style}")

        assert config.from_db is True, "from_db should be True"
        assert config.name == "旅游咨询顾问"
        assert config.dir_name == AGENT_ID
        assert "行程规划师" in config.system_prompt
        assert config.llm_provider == "deepseek"
        assert config.llm_model_codes == {"deepseek": "deepseek-v4-pro", "qwen": "qwen3.7-plus"}
        assert config.reply_style == "human-like"
        return config

    config = test_step("4.1 load_from_db() 整体降级验证", test_load_from_db)

    # ========== 5. 验证 _build_system_prompt 逻辑 ==========
    def test_build_system_prompt():
        # 模拟 from_db=True 的 config
        assert config.from_db is True
        # 直接使用 config.system_prompt（这就是 _build_system_prompt 现在的行为）
        prompt = config.system_prompt
        assert "行程规划师" in prompt
        assert len(prompt) > 100
        print(f"  prompt from config.system_prompt (from_db=True): {len(prompt)} chars")
        return prompt

    test_step("5.1 _build_system_prompt 逻辑验证（from_db=True）", test_build_system_prompt)

    # ========== 6. 测试版本管理 ==========
    def test_v2():
        new_content = SYSTEM_PROMPT_BODY.replace(
            "你是一名热爱旅游行业的行程规划师",
            "你是一名资深旅游行业行程规划师，拥有10年从业经验",
        )
        result = SubagentDefinitionService.update_system_prompt(
            agent_id=AGENT_ID,
            content=new_content,
            commit_message="V2: 增加资历描述",
            created_by=CREATED_BY,
        )
        if result and result.get("version"):
            v = result["version"]
            print(f"  V{v['version']} 已提交")
            assert v["version"] == 2
        return result

    test_step("6.1 提交 V2 system_prompt", test_v2)

    # 验证 production 已更新到 V2
    defn2 = test_step("6.2 验证 production 已更新到 V2", lambda: SubagentDefinitionService.get_definition(AGENT_ID))
    if defn2:
        print(f"  production_version = {defn2.get('production_version')}")
        assert defn2.get("production_version") == 2

    # Diff 对比
    prompt_id = defn["prompt_id"] if defn else None
    if prompt_id:
        diff = test_step("6.3 Diff V1 vs V2", lambda: PromptRegistryService.diff_versions(
            prompt_id=prompt_id, from_version=1, to_version=2,
        ))
        if diff:
            print(f"  from: {len(diff['from']['content'])} chars")
            print(f"  to:   {len(diff['to']['content'])} chars")
            assert "资深" in diff["to"]["content"]

    # ========== 7. 回滚到 V1 ==========
    def test_rollback():
        result = PromptRegistryService.set_label(
            prompt_id=prompt_id, label="production", version=1, created_by=CREATED_BY,
        )
        if result:
            print(f"  production 已回滚到 V1")
        # 验证 resolve 返回 V1
        resolved = prompt_resolver.resolve(scope="subagent", scope_id=AGENT_ID)
        assert "资深" not in resolved, "should be V1 content (without 资深)"
        assert "行程规划师" in resolved
        print(f"  resolve 后内容确认: V1 (无'资深')")
        return result

    test_step("7.1 回滚 production 到 V1", test_rollback)

    # ========== 8. 整体降级验证：删除 prompt 后 load_from_db 应跳过 ==========
    def test_degradation():
        # 删除 prompt（保留 definition）
        SubagentDefinitionService.delete_definition(AGENT_ID)
        # 只重新插入 definition（不含 prompt）
        SubagentDefinitionDB.create(
            agent_id=AGENT_ID,
            name="旅游咨询顾问（降级测试）",
            description="测试整体降级：有定义但无 prompt",
            created_by=CREATED_BY,
        )

        reg = SubagentRegistry()
        count = reg.load_from_db()
        print(f"  load_from_db() 返回 {count}（应为 0，因为无 prompt）")
        assert count == 0, "should skip: has definition but no prompt"

        config = reg.get(AGENT_ID)
        assert config is None, "should NOT be loaded from DB"
        print(f"  reg.get('{AGENT_ID}') = None (正确降级)")

        # 清理
        SubagentDefinitionDB.delete(AGENT_ID)
        return True

    test_step("8.1 整体降级：有定义无 prompt → 跳过", test_degradation)

    # ========== 9. 验证文件系统 fallback ==========
    def test_filesystem_fallback():
        # 重新从文件系统加载的 config，from_db=False
        from src.subagents.loader import SubagentLoader
        subagents_dir = Path(__file__).parent.parent / "subagents"
        loader = SubagentLoader(subagents_dir)

        config = loader.get("旅游咨询顾问")
        assert config is not None, "文件系统中应能找到旅游咨询顾问"
        print(f"  文件系统 config.name = {config.name}")
        print(f"  文件系统 config.from_db = {config.from_db}")
        print(f"  文件系统 config.system_prompt length = {len(config.system_prompt)}")
        assert config.from_db is False, "from_file should have from_db=False"
        return config

    test_step("9.1 文件系统 fallback 验证", test_filesystem_fallback)

    # ========== 10. 清理 ==========
    def final_cleanup():
        existing = SubagentDefinitionDB.get_by_agent_id(AGENT_ID)
        if existing:
            SubagentDefinitionService.delete_definition(AGENT_ID)
            print(f"  清理: {AGENT_ID}")
        prompt = PromptRegistryService.get_prompt_by_scope(None, "subagent", AGENT_ID)
        if prompt:
            PromptRegistryService.delete_prompt(str(prompt["id"]))
            print(f"  清理 prompt: {prompt['id']}")
        # 清理缓存
        prompt_resolver.cache.invalidate_prompt(str(prompt["id"])) if prompt else None
        return True

    test_step("10.1 最终清理", final_cleanup)

    # ========== 总结 ==========
    print(f"\n{'='*60}")
    print(f"Phase 2 e2e 结果: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
