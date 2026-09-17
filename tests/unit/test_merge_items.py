# -*- coding: utf-8 -*-
"""merge_items.py 分片合并测试：拼接 / 参数覆盖 / 编号冲突 / project 合并"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "src/skills/spec-to-quotation-list-1.0.0/scripts/merge_items.py"


def _file_obj(filename, codes, prefix="HR"):
    return {
        "filename": filename,
        "sheet": filename,
        "scopes": ["scope"],
        "groups": [
            {"title": "01 G / 分组", "items": [{"code": c, "cn": c, "en": c, "uom": "Each",
                                                "basis": "b", "vendor": "TO BID",
                                                "spec": ["s"], "ref": "1.1 (P.1)"} for c in codes]}
        ],
    }


def _part(filename, codes, parameters=None, code_prefix=None):
    data = {"files": [_file_obj(filename, codes)]}
    if parameters:
        data["parameters"] = parameters
    if code_prefix:
        data["files"][0]["code_prefix"] = code_prefix
    return data


def _write_parts(tmp_path, parts: dict):
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir(exist_ok=True)
    for name, data in parts.items():
        (parts_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return parts_dir


def _run(tmp_path, parts_dir, out="items.json"):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--parts-dir", str(parts_dir), "--out", str(tmp_path / out)],
        capture_output=True, text=True)


class TestMergeItems:
    def test_files_concatenated_in_order(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "b.json": _part("02_公共.xlsx", ["PA-101"]),
            "a.json": _part("01_客房.xlsx", ["HR-101", "HR-102"]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0, r.stderr
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert [f["filename"] for f in merged["files"]] == ["01_客房.xlsx", "02_公共.xlsx"]
        assert merged["files"][0]["groups"][0]["items"][0]["code"] == "HR-101"

    def test_parameters_later_overrides_with_remark(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "a.json": _part("01_客房.xlsx", ["HR-101"], {"客房总数": {"value": 500, "remark": "初始"}}),
            "b.json": _part("02_公共.xlsx", ["PA-101"], {"客房总数": {"value": 600, "remark": "修正"},
                                                          "大堂面积": {"value": 200, "remark": "手册 P.30"}}),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert merged["parameters"]["客房总数"]["value"] == 600
        assert "合并自" in merged["parameters"]["客房总数"]["remark"]
        assert merged["parameters"]["大堂面积"]["value"] == 200

    def test_duplicate_code_across_parts_fails(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "a.json": _part("01_客房.xlsx", ["HR-101"]),
            "b.json": _part("02_公共.xlsx", ["HR-101"]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0
        assert "HR-101" in r.stderr or "HR-101" in r.stdout

    def test_project_takes_first_non_empty(self, tmp_path):
        p1 = _part("01_客房.xlsx", ["HR-101"])
        p1["project"] = {"project": "HOTEL101", "issue_date": "16 SEP 2026"}
        p2 = _part("02_公共.xlsx", ["PA-101"])
        p2["project"] = {"project": "其他名", "project_no": "P-2"}
        parts_dir = _write_parts(tmp_path, {"a.json": p1, "b.json": p2})
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert merged["project"]["project"] == "HOTEL101"
        assert merged["project"]["project_no"] == "P-2"
        assert merged["project"]["issue_date"] == "16 SEP 2026"

    def test_precise_images_and_renderings_merged(self, tmp_path):
        p1 = _part("01_客房.xlsx", ["HR-101"])
        p1["precise_images"] = {"HR-101": ["x", 11, 41]}
        p1["page_renderings"] = {"11": 39}
        p2 = _part("02_公共.xlsx", ["PA-101"])
        p2["precise_images"] = {"PA-101": ["x", 30, 100]}
        parts_dir = _write_parts(tmp_path, {"a.json": p1, "b.json": p2})
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert merged["precise_images"] == {"HR-101": ["x", 11, 41], "PA-101": ["x", 30, 100]}
        assert merged["page_renderings"] == {"11": 39}

    def test_empty_parts_dir_fails(self, tmp_path):
        r = _run(tmp_path, tmp_path / "nonexistent")
        assert r.returncode != 0

    def test_same_code_within_one_part_not_flagged_across_files(self, tmp_path):
        # 同分片内不允许跨文件重复 code（冲突校验覆盖所有 files）
        part = _part("01_客房.xlsx", ["HR-101"])
        part["files"].append(_file_obj("02_客房B.xlsx", ["HR-101"]))
        parts_dir = _write_parts(tmp_path, {"a.json": part})
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0


class TestParamRefCheck:
    def _part_with_qty_rule(self, rules, parameters):
        data = _part("01_客房.xlsx", ["HR-101"])
        item = data["files"][0]["groups"][0]["items"][0]
        item["qty_rule"] = rules
        data["parameters"] = parameters
        return data

    def test_missing_param_ref_fails(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "a.json": self._part_with_qty_rule(
                {"mode": "expr", "expr": "{客房总数} * 2"}, {"大堂面积": {"value": 200}}),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0
        assert "参数" in r.stderr

    def test_area_param_missing_fails_and_existing_passes(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "a.json": self._part_with_qty_rule(
                {"mode": "area", "param": "客房面积", "waste": 0.05}, {"客房总数": {"value": 500}}),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0

        parts_dir = _write_parts(tmp_path, {
            "a.json": self._part_with_qty_rule(
                {"mode": "area", "param": "客房面积", "waste": 0.05},
                {"客房面积": {"value": 21}}),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0, r.stderr
