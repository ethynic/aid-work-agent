"""Python Node renderer wrapper tests."""

import json
import subprocess
from pathlib import Path

import pytest

from src.tools.ppt.renderer import NodePptRenderer, PptRendererError
from src.tools.ppt.spec import SlideDeckSpec, SlideSpec, TextNode

pytestmark = pytest.mark.tools


def _spec():
    return SlideDeckSpec(
        title="渲染测试",
        slides=[SlideSpec(id="s1", nodes=[TextNode(x=1, y=1, w=3, h=1, text="内容")])],
    )


def test_renderer_writes_temp_spec_checks_output_and_reads_qa(tmp_path, monkeypatch):
    renderer_dir = tmp_path / "renderer-node"
    entrypoint = renderer_dir / "dist" / "render.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// test", encoding="utf-8")

    def fake_run(command, **kwargs):
        spec_path = Path(command[command.index("--spec") + 1])
        output = Path(command[command.index("--out") + 1])
        qa = Path(command[command.index("--qa-json") + 1])
        assert json.loads(spec_path.read_text(encoding="utf-8"))["title"] == "渲染测试"
        assert output.parent != tmp_path
        output.write_bytes(b"pptx")
        qa.write_text(
            '{"version":"1.0","slide_count":1,"node_count":1,"slides":[{"id":"s1"}]}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = NodePptRenderer(renderer_dir=renderer_dir).render(_spec(), tmp_path / "out.pptx")

    assert result["qa"]["node_count"] == 1
    assert Path(result["file_path"]).read_bytes() == b"pptx"
    assert json.loads(
        (tmp_path / "out.layout.json").read_text(encoding="utf-8")
    )["slide_count"] == 1


def test_renderer_failure_does_not_overwrite_existing_output(tmp_path, monkeypatch):
    renderer_dir = tmp_path / "renderer-node"
    entrypoint = renderer_dir / "dist" / "render.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// test", encoding="utf-8")
    output = tmp_path / "out.pptx"
    output.write_bytes(b"existing")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "private"),
    )

    with pytest.raises(PptRendererError):
        NodePptRenderer(renderer_dir=renderer_dir).render(_spec(), output)

    assert output.read_bytes() == b"existing"


def test_renderer_rejects_inconsistent_qa(tmp_path, monkeypatch):
    renderer_dir = tmp_path / "renderer-node"
    entrypoint = renderer_dir / "dist" / "render.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// test", encoding="utf-8")

    def fake_run(command, **kwargs):
        Path(command[command.index("--out") + 1]).write_bytes(b"pptx")
        Path(command[command.index("--qa-json") + 1]).write_text(
            '{"version":"1.0","slide_count":2,"node_count":1,"slides":[]}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(PptRendererError, match="QA output is invalid"):
        NodePptRenderer(renderer_dir=renderer_dir).render(_spec(), tmp_path / "out.pptx")


def test_renderer_sanitizes_subprocess_failure(tmp_path, monkeypatch):
    renderer_dir = tmp_path / "renderer-node"
    entrypoint = renderer_dir / "dist" / "render.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// test", encoding="utf-8")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="api_key=secret C:\\tenant\\private.pptx"
        ),
    )

    with pytest.raises(PptRendererError, match="renderer failed") as error:
        NodePptRenderer(renderer_dir=renderer_dir).render(_spec(), tmp_path / "out.pptx")

    assert "secret" not in str(error.value)


def test_renderer_converts_timeout_to_safe_error(tmp_path, monkeypatch):
    renderer_dir = tmp_path / "renderer-node"
    entrypoint = renderer_dir / "dist" / "render.js"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("// test", encoding="utf-8")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("node secret", 1, stderr="private")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(PptRendererError, match="unavailable or timed out"):
        NodePptRenderer(renderer_dir=renderer_dir, timeout=1).render(
            _spec(), tmp_path / "out.pptx"
        )
