"""
SkillSession 数据类测试
"""

import pytest

pytestmark = pytest.mark.skills

from src.core.skill_session import SkillSession


class TestSkillSessionCreation:
    """SkillSession 创建和默认值"""

    def test_normal_creation(self):
        session = SkillSession(
            skill_name="test-skill",
            start_index=5,
            message_count_before=5,
        )
        assert session.skill_name == "test-skill"
        assert session.start_index == 5
        assert session.message_count_before == 5
        assert session.is_complete is False

    def test_default_is_complete(self):
        session = SkillSession(
            skill_name="x",
            start_index=0,
            message_count_before=0,
        )
        assert session.is_complete is False

    def test_explicit_is_complete(self):
        session = SkillSession(
            skill_name="x",
            start_index=0,
            message_count_before=0,
            is_complete=True,
        )
        assert session.is_complete is True
