"""PPT 图片资产（image_assets）单元测试：校验 + 大纲回收 + spec 路径规范化。

设计：docs/tools/ppt/ppt-image-assets-design.md
"""

import os

import pytest

pytestmark = pytest.mark.tools



def _make_image(root, name="chart.png"):
    from PIL import Image

    path = root / name
    Image.new("RGB", (40, 30), color=(30, 90, 200)).save(path)
    return str(path)


# ==================== validate_images ====================


def test_validate_images_accepts_in_tenant_root(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    path = _make_image(tmp_path)
    validated, errors = validate_images(
        [{"path": path, "title": "趋势图", "caption": "环比上升"}], tenant_root=str(tmp_path)
    )
    assert not errors
    assert len(validated) == 1
    assert validated[0].path == os.path.realpath(path)
    assert validated[0].title == "趋势图"


def test_validate_images_rejects_outside_root(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    root = tmp_path / "tenant"
    root.mkdir()
    other = tmp_path / "outside"
    other.mkdir()
    path = _make_image(other, "secret.png")

    validated, errors = validate_images(
        [{"path": path, "title": "越界", "caption": "c"}], tenant_root=str(root)
    )
    assert not validated
    assert any("不在当前租户存储目录内" in e for e in errors)


def test_validate_images_rejects_traversal(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    inside = _make_image(tmp_path)
    # 指向根内文件但经 ../ 逃逸再回来的路径：realpath 归一后仍在根内，应放行
    tricky = os.path.join(str(tmp_path), "sub", "..", os.path.basename(inside))
    validated, errors = validate_images(
        [{"path": tricky, "title": "t", "caption": "c"}], tenant_root=str(tmp_path)
    )
    assert not errors and len(validated) == 1

    # 指向根外的 ../ 逃逸路径：必须拒绝
    outside = os.path.join(str(tmp_path), "..", os.path.basename(inside))
    validated, errors = validate_images(
        [{"path": outside, "title": "t", "caption": "c"}], tenant_root=str(tmp_path)
    )
    assert not validated
    assert any("不在当前租户存储目录内" in e for e in errors)


def test_validate_images_rejects_bad_extension_missing_file_and_blank_caption(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    path = _make_image(tmp_path)

    validated, errors = validate_images(
        [{"path": path + ".gif", "title": "t", "caption": "c"}], tenant_root=str(tmp_path)
    )
    assert not validated and any("格式不支持" in e for e in errors)

    validated, errors = validate_images(
        [{"path": str(tmp_path / "nope.png"), "title": "t", "caption": "c"}],
        tenant_root=str(tmp_path),
    )
    assert not validated and any("文件不存在" in e for e in errors)

    validated, errors = validate_images(
        [{"path": path, "title": "t", "caption": "  "}], tenant_root=str(tmp_path)
    )
    assert not validated and any("caption" in e for e in errors)


def test_validate_images_enforces_count_and_size_limits(tmp_path, monkeypatch):
    from src.tools.ppt import image_assets

    path = _make_image(tmp_path)
    many = [
        {"path": path, "title": f"t{i}", "caption": "c"} for i in range(21)
    ]
    validated, errors = image_assets.validate_images(many, tenant_root=str(tmp_path))
    assert not validated and any("超过上限" in e for e in errors)

    monkeypatch.setattr(image_assets, "MAX_IMAGE_SIZE_BYTES", 4)
    validated, errors = image_assets.validate_images(
        [{"path": path, "title": "t", "caption": "c"}], tenant_root=str(tmp_path)
    )
    assert not validated and any("20MB" in e or "大小限制" in e for e in errors)


def test_validate_images_rejects_empty_file(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    path = tmp_path / "empty.png"
    path.write_bytes(b"")

    validated, errors = validate_images(
        [{"path": str(path), "title": "空图", "caption": "c"}], tenant_root=str(tmp_path)
    )
    assert not validated
    assert any("文件为空" in e for e in errors)


def test_validate_images_outside_root_reports_root_error_first(tmp_path):
    from src.tools.ppt.image_assets import validate_images

    root = tmp_path / "tenant"
    root.mkdir()
    other = tmp_path / "outside"
    other.mkdir()

    validated, errors = validate_images(
        [{"path": str(other / "x.gif"), "title": "越界且格式错", "caption": "c"}],
        tenant_root=str(root),
    )
    assert not validated
    # 根域检查先于扩展名：根外文件报越界而非格式
    assert any("不在当前租户存储目录内" in e for e in errors)


# ==================== resolve_tenant_root ====================


def test_resolve_tenant_root_prefers_tool_context_and_strips_prefix():
    from src.tools.context import ToolExecutionContext, tool_execution_scope
    from src.tools.ppt.image_assets import resolve_tenant_root

    with tool_execution_scope(
        ToolExecutionContext(tenant_id="tenant_abc123", user_id="u1")
    ):
        root = resolve_tenant_root()
    assert os.path.normcase(root) == os.path.normcase(
        os.path.join("storage", "tenants", "abc123")
    )


def test_resolve_tenant_root_defaults_to_anonymous():
    from src.tools.ppt.image_assets import resolve_tenant_root

    root = resolve_tenant_root()
    assert os.path.normcase(root) == os.path.normcase(
        os.path.join("storage", "tenants", "_anonymous")
    )


# ==================== reconcile_image_slides ====================


def _asset(path, title="图", caption="说明"):
    from src.tools.ppt.image_assets import ImageAsset

    return ImageAsset(path=os.path.realpath(path), title=title, caption=caption)


def test_reconcile_appends_missing_images_before_summary(tmp_path):
    from src.tools.ppt.image_assets import reconcile_image_slides

    path = _make_image(tmp_path)
    plan = {
        "title": "d",
        "slides": [
            {"layout": "cover", "title": "d"},
            {"layout": "summary", "title": "总结"},
        ],
    }
    result, warnings = reconcile_image_slides(plan, [_asset(path, "趋势", "上升")])
    layouts = [s["layout"] for s in result["slides"]]
    assert layouts == ["cover", "image", "summary"]
    assert result["slides"][1]["image_path"] == os.path.realpath(path)
    assert any("补图片页" in w for w in warnings)


def test_reconcile_appends_at_end_without_summary(tmp_path):
    from src.tools.ppt.image_assets import reconcile_image_slides

    path = _make_image(tmp_path)
    plan = {"title": "d", "slides": [{"layout": "cover", "title": "d"}]}
    result, _ = reconcile_image_slides(plan, [_asset(path)])
    assert [s["layout"] for s in result["slides"]] == ["cover", "image"]


def test_reconcile_heals_mangled_path_and_drops_unknown(tmp_path):
    from src.tools.ppt.image_assets import reconcile_image_slides

    path = _make_image(tmp_path, "月度销售额趋势_20260923_120000.png")
    asset = _asset(path, "趋势", "上升")
    plan = {
        "title": "d",
        "slides": [
            # LLM 转抄时丢了目录只剩文件名：basename 唯一命中，回写为校验后路径
            {"layout": "image", "title": "趋势", "image_path": os.path.basename(path)},
            # 编造的路径：丢弃
            {"layout": "image", "title": "幻觉图", "image_path": "C:/not/exists.png"},
            {"layout": "summary", "title": "总结"},
        ],
    }
    result, warnings = reconcile_image_slides(plan, [asset])
    image_slides = [s for s in result["slides"] if s.get("layout") == "image"]
    assert len(image_slides) == 1
    assert image_slides[0]["image_path"] == os.path.realpath(path)
    assert any("未匹配" in w for w in warnings)
    assert "warnings" in result


def test_reconcile_noop_without_images(tmp_path):
    from src.tools.ppt.image_assets import reconcile_image_slides

    plan = {"title": "d", "slides": [{"layout": "cover", "title": "d"}]}
    result, warnings = reconcile_image_slides(plan, [])
    assert result is plan and not warnings


def test_reconcile_dedupes_duplicate_references_to_same_asset(tmp_path):
    from src.tools.ppt.image_assets import reconcile_image_slides

    path = _make_image(tmp_path)
    asset = _asset(path, "趋势", "上升")
    plan = {
        "title": "d",
        "slides": [
            {"layout": "image", "title": "趋势", "image_path": path},
            {"layout": "image", "title": "趋势（重复）", "image_path": path},
            {"layout": "summary", "title": "总结"},
        ],
    }
    result, warnings = reconcile_image_slides(plan, [asset])
    image_slides = [s for s in result["slides"] if s.get("layout") == "image"]
    assert len(image_slides) == 1
    assert any("重复引用" in w for w in warnings)


def test_reconcile_ambiguous_basename_only_matches_full_path(tmp_path):
    """basename 重名时只认全路径命中，避免错配。"""
    from src.tools.ppt.image_assets import reconcile_image_slides

    report = tmp_path / "report"
    report.mkdir()
    sub = tmp_path / "temp"
    sub.mkdir()
    p1 = _make_image(report, "same.png")
    p2 = _make_image(sub, "same.png")
    assets = [_asset(p1, "a", "ca"), _asset(p2, "b", "cb")]
    plan = {
        "title": "d",
        "slides": [
            {"layout": "image", "title": "b", "image_path": p2},
            {"layout": "summary", "title": "总结"},
        ],
    }
    result, warnings = reconcile_image_slides(plan, assets)
    image_slides = [s for s in result["slides"] if s.get("layout") == "image"]
    # p2 全路径命中；p1 basename 歧义不会被误配，但漏图兜底仍会补上
    assert len(image_slides) == 2
    assert image_slides[0]["image_path"] == os.path.realpath(p2)
    assert any("补图片页" in w for w in warnings)


# ==================== normalize_spec_image_paths ====================


def _spec_with_image(path):
    from src.tools.ppt.spec import SlideDeckSpec

    return SlideDeckSpec.model_validate(
        {
            "title": "spec",
            "slides": [
                {
                    "id": "s1",
                    "nodes": [
                        {"type": "text", "x": 0, "y": 0, "w": 5, "h": 1, "text": "t"},
                        {"type": "image", "x": 1, "y": 1, "w": 4, "h": 3, "path": path},
                    ],
                }
            ],
        }
    )


def test_normalize_spec_image_paths_ok_and_rejects_outside(tmp_path):
    from src.tools.ppt.image_assets import normalize_spec_image_paths

    root = tmp_path / "tenant"
    root.mkdir()
    inside = _make_image(root)
    other = tmp_path / "outside"
    other.mkdir()
    outside = _make_image(other, "secret.png")

    spec = _spec_with_image(inside)
    errors = normalize_spec_image_paths(spec, tenant_root=str(root))
    assert not errors
    assert spec.slides[0].nodes[1].path == os.path.realpath(inside)
    # 文本节点不受影响
    assert spec.slides[0].nodes[0].type == "text"

    spec = _spec_with_image(outside)
    errors = normalize_spec_image_paths(spec, tenant_root=str(root))
    assert errors and "不合法或不可读" in errors[0]
