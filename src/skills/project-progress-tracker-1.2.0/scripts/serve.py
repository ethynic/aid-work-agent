#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地分析桥接服务：让看板「录入 → AI 研判 → 看板刷新」一键闭环

只监听 127.0.0.1，纯 Python 标准库。密钥仅由 analyze.py 在服务端从环境变量
PPT_LLM_API_KEY 读取，绝不传到浏览器、不写 localStorage、不落盘明文。

启动：
    python scripts/serve.py --root <技能目录> --port 8765
然后打开 http://127.0.0.1:8765 ，看板会自动解锁「✦ AI 一键重分析」。

接口：
    GET  /                -> dashboard/index.html
    GET  /api/status      -> 服务状态（不含任何凭据信息）
    GET  /api/data        -> 当前 data/projects.json（用于同步最新研判）
    POST /api/analyze     -> {items:[...], as_of} 落盘 + 抽取 + 研判 + 构建
                             返回 mode=api（含新数据）或 mode=packet（已生成证据包）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT: Path | None = None
SKILL_ROOT: Path | None = None
AS_OF = ""
PY = sys.executable


def run(cmd: list[str]) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as exc:
        return 1, "", str(exc)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    server_version = "PPTBridge/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[serve] " + (fmt % args), flush=True)

    def _local_only(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1")

    def _send(self, code: int, body, ctype: str) -> None:
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _guard(self) -> bool:
        if not self._local_only():
            self._json({"ok": False, "error": "仅允许本机访问"}, 403)
            return False
        return True

    def do_GET(self) -> None:
        if not self._guard():
            return
        p = urlparse(self.path).path
        if p in ("/", "/index.html"):
            f = ROOT / "dashboard" / "index.html"
            if not f.exists():
                return self._json({"ok": False, "error": "看板未构建，请先运行 build_dashboard.py"}, 404)
            return self._send(200, f.read_bytes(), "text/html; charset=utf-8")
        if p == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if p == "/api/status":
            pj = ROOT / "data" / "projects.json"
            count = len(read_json(pj).get("projects", [])) if pj.exists() else 0
            return self._json({"ok": True, "as_of": AS_OF, "projects": count,
                               "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
        if p == "/api/data":
            pj = ROOT / "data" / "projects.json"
            if not pj.exists():
                return self._json({"ok": False, "error": "缺少 data/projects.json"}, 404)
            return self._json({"ok": True, "data": read_json(pj)})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        if not self._guard():
            return
        p = urlparse(self.path).path
        if p != "/api/analyze":
            return self._json({"ok": False, "error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:
            return self._json({"ok": False, "error": "请求体不是合法 JSON：" + str(exc)}, 400)

        items = payload.get("items") or []
        as_of = payload.get("as_of") or AS_OF
        scripts = (SKILL_ROOT or ROOT) / "scripts"
        log: list[str] = []

        # 1) 新录入记录落盘（复用 ingest_inbox.py，不带 --chain）
        if items:
            (ROOT / "data" / "inbox.json").write_text(
                json.dumps({"generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                            "as_of": as_of, "source": "dashboard one-click", "items": items},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            rc, out, err = run([PY, str(scripts / "ingest_inbox.py"), "--root", str(ROOT), "--as-of", as_of])
            log.append(f"[ingest] {len(items)} 条新记录已落盘" if rc == 0 else f"[ingest] 失败：{err or out}")
            if rc != 0:
                return self._json({"ok": False, "error": "新记录落盘失败", "log": log}, 500)
        else:
            log.append("[ingest] 无新录入记录，基于现有资料重跑")

        # 2) 抽取
        rc, out, err = run([PY, str(scripts / "extract_sources.py"), "--root", str(ROOT), "--as-of", as_of])
        log.append(out or err or "[extract] 完成")
        if rc != 0:
            return self._json({"ok": False, "error": "资料抽取失败", "log": log}, 500)

        # 3) 研判：analyze.py 自行决定 api / packet 降级，服务端不探测凭据、不设阻断
        mark = time.time() - 2
        rc, out, err = run([PY, str(scripts / "analyze.py"), "--root", str(ROOT), "--as-of", as_of, "--mode", "api"])
        log.append(out or err or "[analyze] 完成")
        if rc != 0:
            return self._json({"ok": False, "error": "研判步骤失败", "log": log}, 500)

        analysis_file = ROOT / "data" / "analysis.json"
        api_done = analysis_file.exists() and analysis_file.stat().st_mtime >= mark
        if api_done:
            rc, out, err = run([PY, str(scripts / "build_dashboard.py"), "--root", str(ROOT), "--as-of", as_of])
            log.append(out or err or "[build] 完成")
            if rc != 0:
                return self._json({"ok": False, "error": "看板构建失败", "log": log}, 500)
            return self._json({"ok": True, "mode": "api", "data": read_json(ROOT / "data" / "projects.json"),
                               "message": "AI 研判完成，看板已刷新", "log": log})

        # packet 降级：证据包已生成，由对话中的智能体研判后点「同步最新研判」
        return self._json({
            "ok": True, "mode": "packet",
            "message": "新记录已并入资料并生成证据包。在对话里说「分析这批新记录」，完成后点「↻ 同步最新研判」即可刷新看板。",
            "log": log,
        })


def main() -> int:
    global ROOT, SKILL_ROOT, AS_OF
    ap = argparse.ArgumentParser(description="项目进度看板 · 本地 AI 分析桥接服务")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="数据目录（可写）：dashboard/、data/、logs/ 都在这里")
    ap.add_argument("--skill-root", default=str(Path(__file__).resolve().parent.parent),
                    help="技能只读资源目录（scripts/ 等），一般保持默认")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--as-of", default=dt.date.today().isoformat())
    args = ap.parse_args()
    ROOT = Path(args.root).resolve()
    SKILL_ROOT = Path(args.skill_root).resolve()
    AS_OF = args.as_of
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(parents=True, exist_ok=True)

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"[serve] 技能目录：{ROOT}", flush=True)
    print(f"[serve] 已启动：http://127.0.0.1:{args.port}  （仅本机可访问）", flush=True)
    print("[serve] 密钥只从服务端环境变量 PPT_LLM_API_KEY 读取，不会进入浏览器。", flush=True)
    print("[serve] Ctrl+C 停止。", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
