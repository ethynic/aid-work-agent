#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官网客服「签名链接（ext 模式）」demo 登录后端（聊易私有云，agent.0635easy.com）

模拟租户官网自有登录体系：校验硬编码账号 -> 服务端 HMAC 签名 -> 返回聊天页签名链接。
纯标准库实现，零第三方依赖（Python 3.10，宿主机 systemd 常驻，监听 127.0.0.1:8790）。

签名协议（src/saas/api/web_c.py _verify_ext_signature）：
    sig = HMAC-SHA256(key=channel_secret, msg=f"{u}.{ts}.{name}") hex 小写
    ts  = unix 秒，服务端时效 24h；name 参与签名但可缺省（缺省时签名串用空串）

API（经 nginx /web_c_sig_demo/api/ 前缀代理，location 剥离前缀后落到这里）：
    POST /login   body {"username": "...", "password": "..."}  -> {"success": true, "url": "..."}
    GET  /health  -> {"success": true}

注意：本文件含 channel_secret，仅 demo 用途，勿用于真实租户生产环境。
"""

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

# ===== 渠道连接信息（聊易官网客服 web_c 渠道）=====
TENANT_ID = "tenant_569f4e5acf4d"
CONFIG_ID = "chan_82e934e3da46"
CHANNEL_SECRET = "556d92bae70fc895aa089cd2ffdf2203a66886d844c07a2f51e3082065d17c9b"
CHAT_BASE = f"https://agent.0635easy.com/c/{TENANT_ID}/{CONFIG_ID}"

# ===== 硬编码账号（demo 专用）：用户名 -> (密码, 官网userid, 显示昵称) =====
# userid 须匹配服务端正则 ^[A-Za-z0-9_\-@.]{1,128}$，中文用户名映射为拼音 userid；
# name（中文昵称）参与签名，同一 userid 跨设备/跨访问会话连续。
USERS = {
    "张三": ("123", "zhangsan", "张三"),
    "李四": ("123", "lisi", "李四"),
}

LISTEN_ADDR = ("127.0.0.1", 8790)

# 简单登录限速：单 IP 每 60 秒最多 10 次尝试（demo 防穷举，非生产级）
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60
_rate_bucket: dict = defaultdict(deque)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    bucket = _rate_bucket[ip]
    while bucket and now - bucket[0] > RATE_LIMIT_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_MAX:
        return True
    bucket.append(now)
    return False


def build_signed_url(username: str) -> str:
    """按 ext 签名协议生成聊天页链接（name 原始值参与签名，URL 传输时编码）。"""
    _pwd, userid, display_name = USERS[username]
    ts = str(int(time.time()))
    message = f"{userid}.{ts}.{display_name}"
    sig = hmac.new(CHANNEL_SECRET.encode("utf-8"), message.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return (f"{CHAT_BASE}?u={quote(userid)}&ts={ts}"
            f"&name={quote(display_name)}&sig={sig}")


class DemoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WebCSigDemo/1.0"

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802（http.server 命名约定）
        if self.path == "/health":
            self._send_json(200, {"success": True})
        else:
            self._send_json(404, {"success": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/login":
            self._send_json(404, {"success": False, "error": "not found"})
            return

        client_ip = self.client_address[0]
        if _rate_limited(client_ip):
            self._send_json(429, {"success": False, "error": "尝试过于频繁，请稍后再试"})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                self._send_json(400, {"success": False, "error": "请求体过大"})
                return
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            username = str(data.get("username") or "").strip()
            password = str(data.get("password") or "")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"success": False, "error": "请求格式错误"})
            return

        record = USERS.get(username)
        if not record or record[0] != password:
            print(f"[login] failed ip={client_ip} username={username!r}")
            self._send_json(400, {"success": False, "error": "用户名或密码错误"})
            return

        url = build_signed_url(username)
        print(f"[login] ok ip={client_ip} username={username} -> {url}")
        self._send_json(200, {"success": True, "url": url})

    def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志，登录事件已单独打印
        pass


def main() -> None:
    server = ThreadingHTTPServer(LISTEN_ADDR, DemoHandler)
    print(f"[web_c_sig_demo] listening on {LISTEN_ADDR[0]}:{LISTEN_ADDR[1]}")
    server.serve_forever()


if __name__ == "__main__":
    main()
