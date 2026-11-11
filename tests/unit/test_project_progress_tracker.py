"""project-progress-tracker 技能测试（workbuddy 移植）：
注册发现 + requires_tools 声明 + 薄壳子智能体一致性 + 端到端流水线 + 校验红线。

端到端直接调用 scripts/ 下各脚本（纯本地流水线，零必装依赖、零联网），
覆盖演示数据生成 -> 抽取 -> 研判包 -> 看板构建 -> 增量导入闭环与字段校验失败路径。
"""

import base64
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

pytestmark = __import__("pytest").mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "project-progress-tracker-1.2.0"
SCRIPTS = SKILL_DIR / "scripts"
SUBAGENT_MD = PROJECT_ROOT / "subagents" / "project-progress-assistant" / "SUBAGENT.md"
AS_OF = "2026-11-11"


def _run(args, cwd=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": "/tmp",
    }
    return subprocess.run(
        [sys.executable] + [str(a) for a in args],
        cwd=str(cwd or SKILL_DIR), env=env, capture_output=True, text=True, timeout=120,
    )


def _parse_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    assert m, f"{path} 缺少 frontmatter"
    # 仅取顶层简单键，避免引入完整 YAML 依赖差异
    fm = {}
    for line in m.group(1).splitlines():
        if line.startswith("  ") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip()
    return fm


class TestProjectProgressTrackerSkill(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("project-progress-tracker", registry.list_skills())
        skill = registry.get("project-progress-tracker")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.2.0")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("project-progress-tracker")
        for tool in ("read", "cp", "image_read", "knowledge_base_search", "knowledge_file_search"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))

    def test_subagent_shell_definition(self):
        self.assertTrue(SUBAGENT_MD.exists(), "薄壳子智能体 SUBAGENT.md 不存在")
        fm = _parse_frontmatter(SUBAGENT_MD)
        self.assertEqual(fm.get("name"), "项目进度管家")
        allowed = re.search(r"allowed:\s*\n\s*-\s*(\S+)", SUBAGENT_MD.read_text(encoding="utf-8"))
        self.assertIsNotNone(allowed, "SUBAGENT.md 缺少 skills.allowed")
        self.assertEqual(allowed.group(1), "project-progress-tracker")


class TestPipelineEndToEnd(unittest.TestCase):
    """在独立临时目录跑完整流水线；--root 与 --skill-root 分离是本次移植的关键补丁。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ppt_test_")
        self.root = Path(self.tmp) / "work"

    def tearDown(self):
        subprocess.run(["rm", "-rf", self.tmp], check=False)

    def _demo(self):
        proc = _run([SCRIPTS / "make_demo.py", "--root", self.root, "--as-of", AS_OF])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def test_demo_to_dashboard_pipeline(self):
        self._demo()
        proc = _run([SCRIPTS / "extract_sources.py", "--root", self.root, "--as-of", AS_OF])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        extracted = json.loads((self.root / "data" / "extracted.json").read_text(encoding="utf-8"))
        self.assertEqual(len(extracted["meta"]["projects"]), 8)
        self.assertGreaterEqual(extracted["meta"]["source_count"], 20)

        proc = _run([SCRIPTS / "analyze.py", "--root", self.root, "--skill-root", SKILL_DIR,
                     "--as-of", AS_OF, "--mode", "packet"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        packet = (self.root / "data" / "analysis_packet.md").read_text(encoding="utf-8")
        self.assertIn("三态", packet)
        self.assertIn("佛山基地3号宽体窑炉技改", packet)

        # build：演示 projects.json 直接可用，且 totals 重算回写真源
        proc = _run([SCRIPTS / "build_dashboard.py", "--root", self.root, "--skill-root", SKILL_DIR])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        dashboard = self.root / "dashboard" / "index.html"
        self.assertTrue(dashboard.exists())
        html = dashboard.read_text(encoding="utf-8")
        self.assertNotIn("__DASHBOARD_DATA__", html, "占位符未被替换")
        self.assertIn("演示数据", html)
        projects = json.loads((self.root / "data" / "projects.json").read_text(encoding="utf-8"))
        self.assertEqual(projects["meta"]["totals"]["total"], 8)
        self.assertEqual(projects["meta"]["totals"]["on_track"], 3)
        self.assertEqual(projects["meta"]["totals"]["overdue"], 2)
        for p in projects["projects"]:
            self.assertIn("days_remaining", p)

    def test_build_rejects_missing_required_fields(self):
        self._demo()
        pj = self.root / "data" / "projects.json"
        data = json.loads(pj.read_text(encoding="utf-8"))
        del data["projects"][0]["status"]
        data["projects"][1]["progress"] = 150
        pj.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        proc = _run([SCRIPTS / "build_dashboard.py", "--root", self.root, "--skill-root", SKILL_DIR])
        self.assertNotEqual(proc.returncode, 0, "缺必填字段/非法数值时必须校验失败")
        self.assertFalse((self.root / "dashboard" / "index.html").exists(), "校验失败不得生成残缺看板")
        # SystemExit 消息走 stderr
        self.assertIn("status", proc.stderr)

    def test_ingest_inbox_round_trip(self):
        self._demo()
        inbox = Path(self.tmp) / "inbox.json"
        inbox.write_text(json.dumps({
            "as_of": AS_OF,
            "items": [{"project": "佛山基地3号宽体窑炉技改", "type": "communication",
                       "date": AS_OF, "title": "周会沟通", "source": "微信群",
                       "content": "特种设备登记本周能办结吗？", "files": [], "binary_files": []}],
        }, ensure_ascii=False), encoding="utf-8")
        proc = _run([SCRIPTS / "ingest_inbox.py", "--root", self.root, "--skill-root", SKILL_DIR,
                     "--inbox", inbox, "--as-of", AS_OF, "--chain"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sources = self.root / "data" / "sources" / "佛山基地3号宽体窑炉技改"
        self.assertTrue(any(f.name.startswith("inbox_") for f in sources.glob("*.md")))
        self.assertFalse(inbox.exists(), "原待分析包应被归档")
        self.assertTrue(any(self.root.joinpath("data").glob("inbox_processed_*.json")))

    def test_stage_inputs_file_and_kb(self):
        self._demo()  # 既有项目目录名对齐依赖演示数据中的目录
        upload = Path(self.tmp) / "上传材料.txt"
        upload.write_text("项目A进展顺利，按计划推进", encoding="utf-8")
        kbtext = Path(self.tmp) / "kb正文.txt"
        kbtext.write_text("周报：3# 线持续停窑，批复仍未下发。", encoding="utf-8")

        proc = _run([SCRIPTS / "stage_inputs.py", "--root", self.root,
                     "--project", "新项目XYZ", "--file", upload])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = _run([SCRIPTS / "stage_inputs.py", "--root", self.root,
                     "--project", "佛山基地3号宽体窑炉技改", "--kb",
                     "--title", "窑炉技改沟通纪要", "--text-file", kbtext, "--date", AS_OF])
        self.assertEqual(proc.returncode, 0, proc.stderr)

        staged = self.root / "data" / "sources" / "新项目XYZ" / "上传材料.txt"
        self.assertTrue(staged.exists())
        kb_dir = self.root / "data" / "sources" / "佛山基地3号宽体窑炉技改"
        kb_files = list(kb_dir.glob("kb_*.md"))
        self.assertEqual(len(kb_files), 1)
        content = kb_files[0].read_text(encoding="utf-8")
        self.assertIn("source: knowledge_base", content)
        self.assertIn("doc_date: " + AS_OF, content)
        # 既有项目目录名对齐：不新建近似目录
        self.assertEqual(len(list((self.root / "data" / "sources").iterdir())), 1 + 8)

    def test_stage_inputs_missing_file_fails(self):
        proc = _run([SCRIPTS / "stage_inputs.py", "--root", self.root,
                     "--project", "P", "--file", "/nonexistent/file.txt"])
        self.assertNotEqual(proc.returncode, 0)

    def test_files_param_delivery_contract(self):
        """研判结果经 skill_execute files 参数落盘的契约：base64 解码 + cp 到 $PPT_ROOT。"""
        self._demo()
        projects = json.loads((self.root / "data" / "projects.json").read_text(encoding="utf-8"))
        payload = base64.b64encode(
            json.dumps(projects, ensure_ascii=False).encode("utf-8")).decode("ascii")
        self.assertEqual(json.loads(base64.b64decode(payload)).get("projects"), projects["projects"])


if __name__ == "__main__":
    unittest.main()
