# -*- coding: utf-8 -*-
"""merge_items.py 分片合并测试：items 拼接 / 参数覆盖 / 编号冲突 / 维度校验 / 参数死引用 / 矩阵摘要"""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "src/skills/spec-to-quotation-list-2.0.0/scripts/merge_items.py"


def _item(code, space="客房", theme="家具软装", **extra):
    item = {"code": code, "cn": code, "en": code, "space": space, "theme": theme,
            "category": "家具", "ref": "1.1 (P.1)"}
    item.update(extra)
    return item


def _part(items, parameters=None, project=None):
    data = {"items": items}
    if parameters:
        data["parameters"] = parameters
    if project:
        data["project"] = project
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
    def test_items_concatenated_in_order(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "b.json": _part([_item("PA-101", space="公共区")]),
            "a.json": _part([_item("HR-101"), _item("HR-102")]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0, r.stderr
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert [it["code"] for it in merged["items"]] == ["HR-101", "HR-102", "PA-101"]

    def test_parameters_later_overrides_with_remark(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {
            "a.json": _part([_item("HR-101")], {"客房总数": {"value": 500, "remark": "初始"}}),
            "b.json": _part([_item("PA-101", space="公共区")], {"客房总数": {"value": 600, "remark": "修正"},
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
            "a.json": _part([_item("HR-101")]),
            "b.json": _part([_item("HR-101", space="公共区")]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0
        assert "HR-101" in r.stderr or "HR-101" in r.stdout

    def test_missing_code_fails(self, tmp_path):
        parts_dir = _write_parts(tmp_path, {"a.json": _part([_item("")])})
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0

    def test_missing_dimension_fails(self, tmp_path):
        """缺 space/theme/category 任一维度即报错（生成器会静默丢弃，提前拦截）"""
        item = _item("HR-101")
        del item["theme"]
        parts_dir = _write_parts(tmp_path, {"a.json": _part([item])})
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0
        assert "theme" in r.stderr

    def test_project_takes_first_non_empty(self, tmp_path):
        p1 = _part([_item("HR-101")], project={"project": "HOTEL101", "issue_date": "16 SEP 2026"})
        p2 = _part([_item("PA-101", space="公共区")], project={"project": "其他名", "project_no": "P-2"})
        parts_dir = _write_parts(tmp_path, {"a.json": p1, "b.json": p2})
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0
        merged = json.loads((tmp_path / "items.json").read_text(encoding="utf-8"))
        assert merged["project"]["project"] == "HOTEL101"
        assert merged["project"]["project_no"] == "P-2"
        assert merged["project"]["issue_date"] == "16 SEP 2026"

    def test_precise_images_and_renderings_merged(self, tmp_path):
        p1 = _part([_item("HR-101")])
        p1["precise_images"] = {"HR-101": ["x", 11, 41]}
        p1["page_renderings"] = {"11": 39}
        p2 = _part([_item("PA-101", space="公共区")])
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


class TestParamRefCheck:
    def _part_with_qty_rule(self, rules, parameters):
        item = _item("HR-101")
        item["qty_rule"] = rules
        return _part([item], parameters=parameters)

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


class TestMatrixSummary:
    def test_matrix_output_with_tbc_count(self, tmp_path):
        """合并成功后打印 (space,theme) 矩阵摘要，TBC 条目单独计数"""
        parts_dir = _write_parts(tmp_path, {
            "a.json": _part([
                _item("HR-101"), _item("HR-102"),
                _item("HR-201", theme="硬装材料", tbc=True),
                _item("PA-101", space="公共区"),
            ]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode == 0, r.stderr
        assert "矩阵" in r.stdout
        assert "客房 × 家具软装：2 条" in r.stdout
        assert "客房 × 硬装材料：1 条，TBC 1" in r.stdout
        assert "公共区 × 家具软装：1 条" in r.stdout
        assert "合计组合 3 个、条目 4 条" in r.stdout

    def test_failed_merge_prints_no_matrix(self, tmp_path):
        """合并失败（编号冲突）不打印矩阵"""
        parts_dir = _write_parts(tmp_path, {
            "a.json": _part([_item("HR-101")]),
            "b.json": _part([_item("HR-101", space="公共区")]),
        })
        r = _run(tmp_path, parts_dir)
        assert r.returncode != 0
        assert "矩阵" not in r.stdout
