"""PaddleOCR adapter for encrypted RPA evidence collection.

客户端 CLI 模式：检测到 ASSOCIATION_CLIENT_SERVER_URL 环境变量时，
走服务端 /api/client/v1/ocr/parse（服务端调 PaddleOCR，经计费记录）。
否则降级直连项目 paddleocr_doc_parsing（服务端开发调试用）。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
from typing import Any, Callable


def run_ocr(
    payload: dict[str, Any],
    ocr_func: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    paths = payload.get("image_paths")
    if not isinstance(paths, list) or not paths or len(paths) > 3:
        raise ValueError("image_paths must contain 1 to 3 paths")

    # 客户端 CLI 模式：走服务端 OCR 代理
    server_url = os.environ.get("ASSOCIATION_CLIENT_SERVER_URL")
    access_token = os.environ.get("ASSOCIATION_CLIENT_ACCESS_TOKEN")
    if server_url and access_token and ocr_func is None:
        return _run_ocr_via_proxy(paths, server_url, access_token)

    if ocr_func is None:
        project_root = pathlib.Path(__file__).resolve().parents[3]
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from src.tools.ocr.ocr_tool import paddleocr_doc_parsing

        ocr_func = paddleocr_doc_parsing
    texts: list[str] = []
    temporary_root = pathlib.Path(tempfile.gettempdir()).resolve()
    for raw_path in paths:
        path = pathlib.Path(str(raw_path)).resolve()
        if (
            not path.is_file()
            or path.parent != temporary_root
            or not path.name.startswith("wechat-ocr-")
            or path.suffix.lower() != ".png"
        ):
            raise ValueError("invalid image path")
        result = ocr_func(file_path=str(path), file_type=1)
        if not result.get("success"):
            raise RuntimeError("ocr provider failed")
        text = str(result.get("full_text", "")).strip()
        if text:
            texts.append(text)
    combined = "\n\n".join(texts)
    if not combined or len(combined) > 2_000_000:
        raise ValueError("invalid OCR text")
    return {"ok": True, "text": combined, "image_count": len(paths)}


def _run_ocr_via_proxy(paths: list, server_url: str, access_token: str) -> dict[str, Any]:
    """走服务端 /api/client/v1/ocr/parse（multipart 上传图片）。"""
    import httpx

    texts: list[str] = []
    for raw_path in paths:
        path = pathlib.Path(str(raw_path)).resolve()
        if not path.is_file():
            raise ValueError(f"invalid image path: {path}")
        with open(path, "rb") as f:
            resp = httpx.post(
                f"{server_url.rstrip('/')}/api/client/v1/ocr/parse",
                headers={"Authorization": f"Bearer {access_token}"},
                files={"image": (path.name, f, "image/png")},
                timeout=60,
            )
        resp.raise_for_status()
        text = resp.json().get("text", "").strip()
        if text:
            texts.append(text)
    combined = "\n\n".join(texts)
    if not combined:
        raise ValueError("OCR 返回空文本")
    return {"ok": True, "text": combined, "image_count": len(paths)}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.read())
        # PaddleOCR 及项目工具可能输出模型日志、路径或识别正文，隔离全部内部输出。
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run_ocr(payload)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
        return 0
    except Exception as exc:
        # 不输出 OCR 原文或临时文件路径。
        sys.stderr.write(f"ocr_failed:{type(exc).__name__}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
