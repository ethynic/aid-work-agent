"""协会采集证据日志单测。

验证意图：每个协会的所有链路原文（文心问句/回答、搜一搜查询词/列表/详情文本）
必须落到「协会名_日期.log」独立文件，且日志失败绝不影响采集主流程——
这是排障"采集依据"的唯一完整记录（app.log 会滚动且脱敏）。
"""

import re

from src.services.association_evidence_log import evidence_log


def test_writes_to_association_dated_file(tmp_path, monkeypatch):
    """不同协会写到各自的 协会名_日期.log 文件，按协会隔离。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    evidence_log("中国造纸协会", "文心·问句", "给出中国造纸协会的以下信息...")
    evidence_log("中国造纸协会", "文心·回答", "地址：北京市...")
    evidence_log("福建省针灸学会", "文心·问句", "给出福建省针灸学会的以下信息...")

    files = sorted(p.name for p in (tmp_path / "AidWorkAgent/association-client/logs/evidence").iterdir())
    assert len(files) == 2  # 两个协会两个文件
    paper = next(f for f in files if "中国造纸协会" in f)
    assert re.fullmatch(r"中国造纸协会_\d{4}-\d{2}-\d{2}\.log", paper)

    content = (tmp_path / "AidWorkAgent/association-client/logs/evidence" / paper).read_text(encoding="utf-8")
    assert "===== " in content and "文心·问句" in content and "文心·回答" in content
    assert "给出中国造纸协会的以下信息" in content
    assert "北京市" in content
    # 协会隔离：针灸学会的内容不落进造纸协会文件
    assert "针灸" not in content


def test_sanitizes_illegal_filename_characters(tmp_path, monkeypatch):
    """协会名含 Windows 非法字符时不崩，替换为下划线。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    evidence_log('协会: "测试/上级*单位"', "文心·问句", "问句内容")
    evidence_log("协会  带空白\t换行", "文心·回答", "回答内容")

    files = [p.name for p in (tmp_path / "AidWorkAgent/association-client/logs/evidence").iterdir()]
    assert len(files) == 2
    for name in files:
        assert not re.search(r'[\\/:*?"<>|]', name)
    # 空白被压缩移除
    assert any("带空白" in n and " " not in n for n in files)


def test_none_and_failure_never_raise(tmp_path, monkeypatch):
    """text=None 记 <空>；目录不可写等任何异常静默吞掉（不影响采集）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    evidence_log("测试协会", "文心·回答", None)  # 不崩，记 <空>
    content = list((tmp_path / "AidWorkAgent/association-client/logs/evidence").iterdir())[0]
    assert "<空>" in content.read_text(encoding="utf-8")

    # LOCALAPPDATA 指向文件（非目录）→ mkdir 失败 → 必须吞异常不抛
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(blocker))
    evidence_log("测试协会", "文心·问句", "不应抛异常")


def test_multiple_kinds_append_in_order(tmp_path, monkeypatch):
    """同一协会多次调用按顺序追加（问句→回答→查询词→列表→详情）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    kinds = ["文心·问句", "文心·回答", "微信搜姓名·第1次·查询词", "微信搜姓名·第1次·列表文本（秘书长）"]
    for kind in kinds:
        evidence_log("测试协会", kind, "x")

    files = list((tmp_path / "AidWorkAgent/association-client/logs/evidence").iterdir())
    content = files[0].read_text(encoding="utf-8")
    positions = [content.index(k) for k in kinds]
    assert positions == sorted(positions)  # 按写入顺序追加
