"""
SkillRegistry 白名单过滤测试

验证 SkillRegistry 在带 allowed 过滤时：
- list_skills() 返回过滤后列表（运行时使用）
- list_all_loaded_skills() 返回未过滤全集（管理后台技能选择器使用）
- get() 只能取到过滤后的 skill
- get_all_skill() 能取到被过滤掉的 skill

背景：管理后台技能选择器需要展示全部已加载 skill，不能仅显示主智能体白名单内的。
"""

import unittest
from pathlib import Path

from src.core.skill_registry import SkillRegistry


def _make_skill_md(skill_dir: Path, name: str, description: str = "") -> None:
    """在 skill_dir 下创建一个示例 skill 目录 + SKILL.md"""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description or name}\n"
        "version: 1.0.0\n"
        "---\n"
        f"# {name}\n"
        f"{name} 正文。\n",
        encoding="utf-8",
    )


class TestSkillRegistryAllowedFilter(unittest.TestCase):
    """load_from_directory 带 allowed 过滤"""

    def setUp(self):
        import tempfile
        self.temp_dir = Path(tempfile.mkdtemp())
        # 创建 3 个 skill
        _make_skill_md(self.temp_dir / "skill-a", "skill-a", "A 技能")
        _make_skill_md(self.temp_dir / "skill-b", "skill-b", "B 技能")
        _make_skill_md(self.temp_dir / "skill-c", "skill-c", "C 技能")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_skills_returns_filtered(self):
        """allowed 过滤后，list_skills() 只返回白名单内的 skill"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir, allowed=["skill-a", "skill-c"])

        skills = sorted(registry.list_skills())
        self.assertEqual(skills, ["skill-a", "skill-c"])

    def test_list_all_loaded_skills_returns_full_set(self):
        """list_all_loaded_skills() 返回未过滤全集"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir, allowed=["skill-a"])

        all_skills = sorted(registry.list_all_loaded_skills())
        self.assertEqual(all_skills, ["skill-a", "skill-b", "skill-c"])

    def test_get_returns_none_for_filtered_out(self):
        """get() 对被过滤掉的 skill 返回 None"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir, allowed=["skill-a"])

        self.assertIsNotNone(registry.get("skill-a"))
        self.assertIsNone(registry.get("skill-b"))
        self.assertIsNone(registry.get("skill-c"))

    def test_get_all_skill_returns_filtered_out(self):
        """get_all_skill() 能取到被过滤掉的 skill"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir, allowed=["skill-a"])

        self.assertIsNotNone(registry.get_all_skill("skill-a"))
        skill_b = registry.get_all_skill("skill-b")
        self.assertIsNotNone(skill_b)
        self.assertEqual(skill_b.name, "skill-b")
        self.assertEqual(skill_b.description, "B 技能")

    def test_no_allowed_filter_consistent(self):
        """无 allowed 时，list_skills() 与 list_all_loaded_skills() 一致"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir)

        self.assertEqual(
            sorted(registry.list_skills()),
            sorted(registry.list_all_loaded_skills()),
        )

    def test_get_all_skill_nonexistent_returns_none(self):
        """get_all_skill() 对不存在的 skill 返回 None"""
        registry = SkillRegistry()
        registry.load_from_directory(self.temp_dir, allowed=["skill-a"])

        self.assertIsNone(registry.get_all_skill("nonexistent-skill"))


class TestSkillRegistryMultiDirectory(unittest.TestCase):
    """load_from_directories 多目录 + 过滤"""

    def setUp(self):
        import tempfile
        self.temp_dir = Path(tempfile.mkdtemp())
        # 目录1：基础 skill
        self.base_dir = self.temp_dir / "base"
        _make_skill_md(self.base_dir / "skill-a", "skill-a", "A 基础")
        _make_skill_md(self.base_dir / "skill-b", "skill-b", "B 基础")
        # 目录2：企业 skill（覆盖 skill-a，新增 skill-d）
        self.enterprise_dir = self.temp_dir / "enterprise"
        _make_skill_md(self.enterprise_dir / "skill-a", "skill-a", "A 企业覆盖")
        _make_skill_md(self.enterprise_dir / "skill-d", "skill-d", "D 企业专属")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_multi_dir_filtered_list_skills(self):
        """多目录 + allowed 过滤，list_skills() 只返回白名单内的"""
        registry = SkillRegistry()
        registry.load_from_directories(
            [self.base_dir, self.enterprise_dir],
            allowed=["skill-a", "skill-b"],
        )

        skills = sorted(registry.list_skills())
        self.assertEqual(skills, ["skill-a", "skill-b"])

    def test_multi_dir_all_loaded_skills(self):
        """多目录 + allowed 过滤，list_all_loaded_skills() 返回合并后的全集"""
        registry = SkillRegistry()
        registry.load_from_directories(
            [self.base_dir, self.enterprise_dir],
            allowed=["skill-a"],
        )

        all_skills = sorted(registry.list_all_loaded_skills())
        # 合并后：skill-a, skill-b, skill-d（同名 skill-a 只算一个）
        self.assertEqual(all_skills, ["skill-a", "skill-b", "skill-d"])

    def test_multi_dir_get_all_skill_for_filtered_out(self):
        """多目录场景下，get_all_skill() 能取到被过滤掉的 skill"""
        registry = SkillRegistry()
        registry.load_from_directories(
            [self.base_dir, self.enterprise_dir],
            allowed=["skill-a"],
        )

        # skill-b 被过滤掉，但 get_all_skill 能取到
        skill_b = registry.get_all_skill("skill-b")
        self.assertIsNotNone(skill_b)
        self.assertEqual(skill_b.name, "skill-b")

        # skill-d 被过滤掉，但 get_all_skill 能取到
        skill_d = registry.get_all_skill("skill-d")
        self.assertIsNotNone(skill_d)
        self.assertEqual(skill_d.name, "skill-d")

    def test_multi_dir_high_priority_overrides_description(self):
        """高优先级目录覆盖同名 skill 的描述，全集反映覆盖后的版本"""
        registry = SkillRegistry()
        registry.load_from_directories(
            [self.base_dir, self.enterprise_dir],
            allowed=["skill-a"],
        )

        skill_a = registry.get_all_skill("skill-a")
        self.assertIsNotNone(skill_a)
        # 企业目录覆盖基础目录，描述应为 "A 企业覆盖"
        self.assertEqual(skill_a.description, "A 企业覆盖")

    def test_multi_dir_no_filter_consistent(self):
        """多目录无 allowed 时，list_skills 与 list_all_loaded_skills 一致"""
        registry = SkillRegistry()
        registry.load_from_directories([self.base_dir, self.enterprise_dir])

        self.assertEqual(
            sorted(registry.list_skills()),
            sorted(registry.list_all_loaded_skills()),
        )


if __name__ == "__main__":
    unittest.main()
