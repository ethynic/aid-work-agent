"""PromptRegistryService 一键回退上一版单元测试

验证（docs/plans/plan-prompt-direct-delivery-rollback.md Phase 2）：
- get_previous_version：version-1 优先，缺记录时回退 parent_version 再试一次
- rollback_to_previous：上一版内容走 commit_version 保存链（最新即生效不变式）
- 仅一版 / 无任何版本 / 上一版不存在 → None（调用方转「无可回退版本」）
- dedup 幂等透传（内容相同返回已有版本）
"""

import pytest
from unittest.mock import patch

from src.prompts.prompt_registry_service import PromptRegistryService

pytestmark = pytest.mark.unit

SVC = "src.prompts.prompt_registry_service"


class TestGetPreviousVersion:
    def test_returns_version_minus_one(self):
        latest = {"version": 3, "parent_version": 2}
        previous = {"version": 2, "content": "v2"}
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value=latest), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version", return_value=previous) as mock_get:
            result = PromptRegistryService.get_previous_version("p1")

        assert result == previous
        mock_get.assert_called_once_with("p1", 2)

    def test_single_version_returns_none(self):
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value={"version": 1, "parent_version": None}), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version") as mock_get:
            result = PromptRegistryService.get_previous_version("p1")

        assert result is None
        mock_get.assert_not_called()

    def test_no_versions_returns_none(self):
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value=None), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version") as mock_get:
            result = PromptRegistryService.get_previous_version("p1")

        assert result is None
        mock_get.assert_not_called()

    def test_falls_back_to_parent_version_when_record_missing(self):
        """version-1 记录可能被清理：按 latest.parent_version 再试一次"""
        latest = {"version": 5, "parent_version": 3}
        previous = {"version": 3, "content": "v3"}
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value=latest), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version",
                   side_effect=[None, previous]) as mock_get:
            result = PromptRegistryService.get_previous_version("p1")

        assert result == previous
        assert mock_get.call_args_list[0].args == ("p1", 4)
        assert mock_get.call_args_list[1].args == ("p1", 3)

    def test_no_fallback_without_parent_version(self):
        latest = {"version": 5, "parent_version": None}
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value=latest), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version", return_value=None) as mock_get:
            result = PromptRegistryService.get_previous_version("p1")

        assert result is None
        mock_get.assert_called_once_with("p1", 4)


class TestRollbackToPrevious:
    def test_rollback_commits_previous_content_via_save_chain(self):
        """上一版内容走同一条保存链存为新版本（可再撤销）"""
        previous = {"version": 2, "content": "上一版内容"}
        commit_result = {"version": {"version": 4, "content": "上一版内容"}, "dedup": False}
        with patch(f"{SVC}.PromptVersionDB.get_latest",
                   return_value={"version": 3, "parent_version": 2}), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version", return_value=previous), \
             patch(f"{SVC}.PromptRegistryService.commit_version", return_value=commit_result) as mock_commit:
            result = PromptRegistryService.rollback_to_previous("p1", created_by="admin1")

        assert result == commit_result
        mock_commit.assert_called_once_with(
            prompt_id="p1",
            content="上一版内容",
            commit_message="回退上一版",
            created_by="admin1",
        )

    def test_single_version_returns_none_without_commit(self):
        """仅一版 → None，不触发 commit_version"""
        with patch(f"{SVC}.PromptVersionDB.get_latest", return_value={"version": 1, "parent_version": None}), \
             patch(f"{SVC}.PromptRegistryService.commit_version") as mock_commit:
            result = PromptRegistryService.rollback_to_previous("p1")

        assert result is None
        mock_commit.assert_not_called()

    def test_dedup_passthrough_keeps_existing_version(self):
        """回退内容与最新版相同（如 v1→v2→回退回 v1 内容）：dedup 返回已有版本，不产生新版本"""
        previous = {"version": 1, "content": "相同内容"}
        commit_result = {"version": {"version": 2, "content": "相同内容"}, "dedup": True}
        with patch(f"{SVC}.PromptVersionDB.get_latest",
                   return_value={"version": 2, "parent_version": 1}), \
             patch(f"{SVC}.PromptVersionDB.get_by_prompt_and_version", return_value=previous), \
             patch(f"{SVC}.PromptRegistryService.commit_version", return_value=commit_result) as mock_commit:
            result = PromptRegistryService.rollback_to_previous("p1", created_by="admin1")

        assert result == commit_result
        assert result["dedup"] is True
        mock_commit.assert_called_once()
