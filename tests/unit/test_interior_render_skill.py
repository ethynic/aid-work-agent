"""interior-render 技能移植验证单测

覆盖：
- SKILL.md 被 SkillRegistry 加载（frontmatter 解析、requires_tools 自动补全声明）
- 上游移植完整性：关键纪律与方法论章节保留（防后续合并丢失）
- 工具映射改写：上游 qwenwork_* 已替换为我方 image_read / image_render / skill_execute
- sample_color.py 纯色 PNG 冒烟（Pillow BOX 平均取色）
- SUBAGENT.md frontmatter 可解析、工具/技能白名单正确、system_prompt 在 body
"""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "interior-render-1.0.0"
SKILL_MD = SKILL_DIR / "SKILL.md"
SAMPLE_COLOR = SKILL_DIR / "scripts" / "sample_color.py"
SUBAGENT_MD = PROJECT_ROOT / "subagents" / "interior-render" / "SUBAGENT.md"


@pytest.fixture(scope="module")
def skill_md_text():
    return SKILL_MD.read_text(encoding="utf-8")


class TestSkillLoading:
    def test_skill_registry_loads_interior_render(self):
        from src.core.skill_registry import SkillRegistry

        reg = SkillRegistry(skills_dir=SKILL_DIR.parent)
        reg.load_from_directory(SKILL_DIR.parent)
        skill = reg.get("interior-render")
        assert skill is not None
        assert skill.version == "1.0.0"
        assert skill.requires_tools == ["image_render", "image_read"]

    def test_registry_content_nonempty(self):
        from src.core.skill_registry import SkillRegistry

        reg = SkillRegistry(skills_dir=SKILL_DIR.parent)
        reg.load_from_directory(SKILL_DIR.parent)
        content = reg.get_content("interior-render")
        assert content and len(content) > 500


class TestUpstreamPorting:
    """上游方法论移植完整性（qwen-interior-render v1.2.0 -> 我方 1.0.0）"""

    def test_discipline_sections_preserved(self, skill_md_text):
        for keyword in [
            "禁止用像素阈值",
            "清晰度闸门",
            "不是墙",
            "整张一起喂",
            "视觉近似",
        ]:
            assert keyword in skill_md_text, f"关键纪律丢失: {keyword}"

    def test_workflow_phases_preserved(self, skill_md_text):
        for phase in ["Phase 0", "Phase 1", "Phase 2", "Phase 2.5", "Phase 3", "Phase 4", "Phase 5"]:
            assert phase in skill_md_text, f"工作流阶段丢失: {phase}"

    def test_templates_preserved(self, skill_md_text):
        for marker in ["表面材质类", "单体产品类", "负向词 A", "负向词 B", "把家具当地墙"]:
            assert marker in skill_md_text, f"模板/负向词丢失: {marker}"

    def test_tool_mapping_rewritten(self, skill_md_text):
        """上游专有工具名不得残留，须映射为我方工具"""
        for legacy in ["qwenwork_media_read", "qwenwork_router", "qwenwork_image_generate",
                       "qwenwork_file_present_files", "qwen-image-3.0-pro",
                       "alibaba/qwen-image-3.0-pro"]:
            assert legacy not in skill_md_text, f"上游专有工具未改写: {legacy}"
        for ours in ["image_read", "image_render", "skill_execute", "sample_color.py"]:
            assert ours in skill_md_text, f"我方工具映射缺失: {ours}"

    def test_file_id_bridge_documented(self, skill_md_text):
        """file_id 不能直接喂脚本、须经 image_read 的 file_path 桥接（我方关键适配点）"""
        assert "file_id" in skill_md_text
        assert "file_path" in skill_md_text


class TestSampleColorScript:
    def test_script_exists_and_runs(self):
        assert SAMPLE_COLOR.exists()

    def test_pure_color_png_smoke(self, tmp_path):
        """纯色 PNG 取色：中心 40% 区域平均色应精确命中源色"""
        import struct
        import zlib

        def make_png(color):
            def chunk(tag, data):
                c = struct.pack(">I", len(data)) + tag + data
                return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
            ihdr = struct.pack(">IIBBBBB", 100, 100, 8, 2, 0, 0, 0)
            raw = b"".join(b"\x00" + bytes(color) * 100 for _ in range(100))
            return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                    + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

        import subprocess
        import sys

        png = tmp_path / "solid.png"
        png.write_bytes(make_png((222, 218, 212)))  # #DEDAD4（上游示例色）
        out = subprocess.run(
            [sys.executable, str(SAMPLE_COLOR), "--image", str(png), "--label", "floor"],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode == 0, out.stderr
        label, hexv, rgb = out.stdout.strip().split("\t")
        assert label == "floor"
        assert hexv == "#DEDAD4"

    def test_bad_crop_rejected(self, tmp_path):
        import subprocess
        import sys

        png = tmp_path / "a.png"
        png.write_bytes(_tiny_png())
        out = subprocess.run(
            [sys.executable, str(SAMPLE_COLOR), "--image", str(png), "--crop", "1,2,3"],
            capture_output=True, text=True, timeout=30,
        )
        assert out.returncode != 0
        assert "4 个整数" in out.stderr


def _tiny_png() -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw = b"\x00\xff\xff\xff\x00\xff\xff\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class TestSubagentDefinition:
    def test_frontmatter_parses(self):
        import yaml

        text = SUBAGENT_MD.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        assert m, "SUBAGENT.md frontmatter 分割失败（--- 须顶格闭合）"
        fm = yaml.safe_load(m.group(1))
        assert fm["name"] == "产品效果图助手"
        assert set(fm["tools"]["allowed"]) == {"image_render", "image_read", "skill_execute", "cp"}
        assert fm["skills"]["allowed"] == ["interior-render"]

    def test_system_prompt_in_body_not_frontmatter(self):
        """system_prompt 必须放 body（frontmatter 内 system_prompt 有 --- 闭合陷阱）"""
        import yaml

        text = SUBAGENT_MD.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        fm = yaml.safe_load(m.group(1))
        assert "system_prompt" not in fm
        body = m.group(2).strip()
        assert "Phase 0-5" in body and len(body) > 300

    def test_triggers_cover_wecom_kf_scenarios(self):
        import yaml

        text = SUBAGENT_MD.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
        fm = yaml.safe_load(m.group(1))
        keywords = fm["triggers"]["keywords"]
        for kw in ["效果图", "户型图"]:
            assert kw in keywords
