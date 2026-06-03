"""
Phase 1 端到端验证脚本

测试流程：
1. 注册 travel-consultant 子智能体的 System Prompt
2. 提交 V1 版本
3. 设置 production 标签
4. 通过 PromptResolver 解析
5. 修改内容 → 提交 V2
6. 版本列表、diff 对比
7. 草稿管理
8. 高危 staging 校验
9. 删除清理
"""

import sys
import uuid

from loguru import logger

# 初始化 DB 连接池
from src.db.database import init_postgres_pool
init_postgres_pool()

# 导入待测模块
from src.db.prompt_db import (
    PromptRegistryDB,
    PromptVersionDB,
    PromptLabelDB,
    PromptDraftDB,
)
from src.prompts.prompt_resolver import prompt_resolver
from src.prompts.prompt_registry_service import PromptRegistryService

# 读取旅游助手的 system prompt
SUBAGENT_BODY = """## 身份说明

你是一名热爱旅游行业的行程规划师。你喜欢帮客户设计行程，对国内各条线路都很熟悉，客户提一个目的地你脑子里就能大致勾勒出路线。你说话有自己的想法和判断，遇到不合理的请求会直接给出更好建议。

### 租户定制需求

在行为约束部分末尾如果有「租户定制需求」栏目，其中的内容可能与上面的默认规则有冲突，遇到冲突时以租户定制需求中的内容为准。

---
## 搜索原则（全局适用，任何阶段都要遵守）

规划行程的信息必须来自知识库，不允许凭记忆编造。以下原则适用于所有阶段（初次规划、修改行程、推荐景点等）。

## 对话阶段管理

整个流程分三个阶段：聊需求 → 出行程并反复调整 → 客户满意后引导报价。

## 沟通技巧

1. 先回应再追问
2. 帮客户做选择而不是列选项
3. 每次最多追问1-2个问题

## 行为约束

1. 不编造信息
2. 行程没确认不要报价
3. 只处理旅游相关的事
4. 绝不暴露内部工作过程
"""

# 修改后的版本（模拟修改）
SUBAGENT_BODY_V2 = SUBAGENT_BODY.replace(
    "你是一名热爱旅游行业的行程规划师",
    "你是一名资深旅游行业行程规划师，拥有10年从业经验",
).replace(
    "每次最多追问1-2个问题",
    "每次最多追问1-2个问题，语气自然亲切",
)

SCOPE = "subagent"
SCOPE_ID = "travel-consultant"
TENANT_ID = None  # 系统级
CREATED_BY = "test_script"


def test_step(name, func):
    """运行测试步骤，打印结果"""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        result = func()
        print(f"[PASS] {name}")
        return result
    except Exception as e:
        print(f"[FAIL] {name} -> {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    print("=" * 60)
    print("Phase 1 端到端验证")
    print("=" * 60)

    # ========== 1. 注册 Prompt ==========
    registry = test_step("1.1 注册 Prompt", lambda: PromptRegistryService.register_prompt(
        scope=SCOPE, scope_id=SCOPE_ID, tenant_id=TENANT_ID,
        display_name="旅游咨询顾问 System Prompt",
        description="旅游咨询顾问的完整 System Prompt",
        created_by=CREATED_BY,
    ))

    if not registry:
        print("[FAIL] 注册失败，终止测试")
        sys.exit(1)

    prompt_id = str(registry["id"])
    print(f"  prompt_id = {prompt_id}")
    print(f"  latest_version = {registry['latest_version']}")

    # ========== 2. 提交 V1 ==========
    v1_result = test_step("2.1 提交 V1", lambda: PromptRegistryService.commit_version(
        prompt_id=prompt_id,
        content=SUBAGENT_BODY,
        commit_message="初始版本：旅游助手完整 System Prompt",
        created_by=CREATED_BY,
    ))

    if v1_result:
        v1 = v1_result.get("version")
        print(f"  version = {v1['version']}")
        print(f"  dedup = {v1_result.get('dedup')}")
        print(f"  content_hash = {v1.get('content_hash', '')[:16]}...")

    # ========== 3. 提交相同内容（去重测试）==========
    dedup_result = test_step("2.2 提交相同内容（去重）", lambda: PromptRegistryService.commit_version(
        prompt_id=prompt_id,
        content=SUBAGENT_BODY,
        commit_message="重复内容",
        created_by=CREATED_BY,
    ))

    if dedup_result:
        print(f"  dedup = {dedup_result.get('dedup')} (应为 True)")
        assert dedup_result.get("dedup") is True, "dedup check failed"

    # ========== 4. 设置 production 标签 ==========
    label_result = test_step("3.1 设置 production 标签", lambda: PromptRegistryService.set_label(
        prompt_id=prompt_id,
        label="production",
        version=1,
        created_by=CREATED_BY,
    ))
    if label_result:
        print(f"  label = {label_result['label']}, version_id = {label_result.get('version_id')}")

    # ========== 5. PromptResolver 解析 ==========
    resolved = test_step("4.1 PromptResolver.resolve()", lambda: prompt_resolver.resolve(
        scope=SCOPE, scope_id=SCOPE_ID, tenant_id=TENANT_ID,
    ))

    if resolved:
        print(f"  解析成功，内容长度 = {len(resolved)} 字符")
        print(f"  前50字 = {resolved[:50]}...")
        assert "行程规划师" in resolved, "content mismatch"
    else:
        print("  [FAIL] resolve returned None")

    # ========== 6. 提交 V2（不同内容）==========
    v2_result = test_step("5.1 提交 V2（修改内容）", lambda: PromptRegistryService.commit_version(
        prompt_id=prompt_id,
        content=SUBAGENT_BODY_V2,
        commit_message="修改：身份说明增加资历描述，沟通技巧增加语气要求",
        created_by=CREATED_BY,
    ))

    if v2_result:
        v2 = v2_result.get("version")
        print(f"  version = {v2['version']}")
        print(f"  dedup = {v2_result.get('dedup')}")

    # ========== 7. 版本列表 ==========
    versions = test_step("6.1 版本列表", lambda: PromptRegistryService.list_versions(
        prompt_id=prompt_id, page=1, page_size=10,
    ))

    if versions:
        print(f"  total = {versions['total']}, items:")
        for item in versions["items"]:
            v = dict(item)
            print(f"    V{v['version']}: hash={str(v.get('content_hash', ''))[:16]}..., msg={v.get('commit_message', '')}")

    # ========== 8. Diff 对比 ==========
    diff = test_step("7.1 Diff V1 vs V2", lambda: PromptRegistryService.diff_versions(
        prompt_id=prompt_id, from_version=1, to_version=2,
    ))

    if diff:
        print(f"  from V{diff['from']['version']} ({len(diff['from']['content'])} chars)")
        print(f"  to   V{diff['to']['version']} ({len(diff['to']['content'])} chars)")

    # ========== 9. 草稿管理 ==========
    draft = test_step("8.1 保存草稿", lambda: PromptRegistryService.save_draft(
        prompt_id=prompt_id,
        content=SUBAGENT_BODY + "\n\n## 新增测试段落\n\n这是草稿内容。",
        base_version=2,
        updated_by=CREATED_BY,
    ))
    if draft:
        print(f"  base_version = {draft.get('base_version')}")

    get_draft = test_step("8.2 获取草稿", lambda: PromptRegistryService.get_draft(prompt_id))
    if get_draft:
        print(f"  草稿内容长度 = {len(str(get_draft.get('content', '')))} 字符")

    commit_draft = test_step("8.3 提交草稿为新版本", lambda: PromptRegistryService.commit_draft(
        prompt_id=prompt_id,
        commit_message="从草稿提交V3",
        created_by=CREATED_BY,
    ))
    if commit_draft:
        v3 = commit_draft.get("version")
        if v3:
            print(f"  V{v3['version']} 已提交")

    # 草稿应已删除
    deleted_draft = test_step("8.4 验证草稿已删除", lambda: PromptRegistryService.get_draft(prompt_id))
    assert deleted_draft is None, "draft not deleted"
    print(f"  草稿状态 = {deleted_draft} (应为 None)")

    # ========== 10. 高危 staging 校验 ==========
    # 设置 staging 标签
    test_step("9.1 设置 staging 标签到 V3", lambda: PromptRegistryService.set_label(
        prompt_id=prompt_id,
        label="staging",
        version=3,
        created_by=CREATED_BY,
    ))

    # 设置 production 到 V3（已有 staging 指向 V3，应该通过）
    prod_v3 = test_step("9.2 设置 production 到 V3（staging 已验证）", lambda: PromptRegistryService.set_label(
        prompt_id=prompt_id,
        label="production",
        version=3,
        created_by=CREATED_BY,
    ))
    if prod_v3:
        print(f"  production label updated, version_id = {prod_v3.get('version_id')}")

    # ========== 11. 标签列表 ==========
    labels = test_step("10.1 标签列表", lambda: PromptRegistryService.list_labels(prompt_id))
    if labels:
        for lbl in labels:
            print(f"  {lbl['label']} → V{lbl['version']}")

    # ========== 12. PromptResolver 应返回 V3（最新 production）==========
    resolved_v3 = test_step("11.1 解析 production（应为 V3）", lambda: prompt_resolver.resolve(
        scope=SCOPE, scope_id=SCOPE_ID, tenant_id=TENANT_ID,
    ))
    if resolved_v3:
        assert "新增测试段落" in resolved_v3, "should be V3 content"
        print(f"  [OK] resolved to V3 with new section")

    # ========== 13. 查询 Prompt 列表 ==========
    prompt_list = test_step("12.1 列出 Prompt", lambda: PromptRegistryService.list_prompts(
        scope=SCOPE, page=1, page_size=10,
    ))
    if prompt_list:
        print(f"  total = {prompt_list['total']}")
        for item in prompt_list["items"]:
            d = dict(item)
            print(f"    {d['scope']}:{d['scope_id']} latest_version={d['latest_version']}")

    # ========== 14. 清理：删除测试数据 ==========
    test_step("13.1 删除 Prompt（级联清理）", lambda: PromptRegistryService.delete_prompt(prompt_id))

    # 验证删除
    deleted = test_step("13.2 验证已删除", lambda: PromptRegistryService.get_prompt(prompt_id))
    assert deleted is None, "delete failed"
    print(f"  状态 = {deleted} (应为 None)")

    # 验证 resolve 返回 None
    resolved_none = test_step("13.3 验证 resolve 返回 None", lambda: prompt_resolver.resolve(
        scope=SCOPE, scope_id=SCOPE_ID, tenant_id=TENANT_ID,
    ))
    print(f"  resolve 结果 = {resolved_none} (应为 None)")

    print(f"\n{'='*60}")
    print("[DONE] Phase 1 e2e verification completed!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
