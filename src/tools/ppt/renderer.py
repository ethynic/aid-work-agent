"""Safe Python wrapper around the Node/PptxGenJS renderer."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from src.tools.ppt.spec import SlideDeckSpec


class PptRendererError(RuntimeError):
    """A sanitized renderer error suitable for orchestration handling."""


class NodePptRenderer:
    def __init__(self, renderer_dir: Path | None = None, timeout: int = 60):
        self.renderer_dir = renderer_dir or Path(__file__).parent / "renderer-node"
        self.timeout = timeout

    def render(self, spec: SlideDeckSpec, output_path: str | Path) -> dict:
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        renderer_dir = self.renderer_dir.resolve()
        entrypoint = renderer_dir / "dist" / "render.js"
        if not entrypoint.is_file():
            raise PptRendererError("Node PPT renderer is not built")

        with tempfile.TemporaryDirectory(prefix="ppt-render-") as temp_dir:
            spec_path = Path(temp_dir) / "spec.json"
            qa_path = Path(temp_dir) / "layout.json"
            rendered_path = Path(temp_dir) / "output.pptx"
            payload = spec.model_dump(mode="json")
            for slide in payload["slides"]:
                for node in slide["nodes"]:
                    if node["type"] in {"image", "raster"}:
                        node["path"] = str(Path(node["path"]).resolve())
            spec_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            command = [
                "node", str(entrypoint), "--spec", str(spec_path),
                "--out", str(rendered_path), "--qa-json", str(qa_path),
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=renderer_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                raise PptRendererError("Node PPT renderer unavailable or timed out") from exc
            if completed.returncode != 0:
                raise PptRendererError("Node PPT renderer failed")
            if not rendered_path.is_file() or rendered_path.stat().st_size == 0:
                raise PptRendererError("Node PPT renderer produced no output")
            if not qa_path.is_file():
                raise PptRendererError("Node PPT renderer produced no QA output")
            try:
                qa = json.loads(qa_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PptRendererError("Node PPT renderer QA output is invalid") from exc
            self._validate_qa(qa, spec)
            os.replace(rendered_path, output)
        return {"file_path": str(output), "qa": qa}

    @staticmethod
    def _validate_qa(qa: object, spec: SlideDeckSpec) -> None:
        expected_nodes = sum(len(slide.nodes) for slide in spec.slides)
        if (
            not isinstance(qa, dict)
            or qa.get("version") != "1.0"
            or qa.get("slide_count") != len(spec.slides)
            or qa.get("node_count") != expected_nodes
            or not isinstance(qa.get("slides"), list)
            or len(qa["slides"]) != len(spec.slides)
        ):
            raise PptRendererError("Node PPT renderer QA output is invalid")
