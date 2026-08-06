"""
PowerShell 脚本运行器 —— 封装微信 RPA 的 PowerShell 子进程调用。

关键职责：
    - 设置环境变量（让 llm_judge.py / ocr_adapter.py 走服务端代理）
    - spawn powershell.exe 运行 wechat-souyisou.ps1
    - 解析 stdout JSON 结果
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional


def scripts_dir() -> Path:
    """scripts 目录（PyInstaller 打包后从 _MEIPASS 或源码目录解析）。"""
    if getattr(sys, "frozen", False):
        # PyInstaller 打包后
        base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
        return base / "scripts"
    return Path(__file__).resolve().parent.parent / "scripts"


def make_env(server_url: str, access_token: str) -> dict:
    """构造子进程环境变量（让 llm_judge.py / ocr_adapter.py 走代理）。

    llm_judge.py 和 ocr_adapter.py 改造后会读取这些环境变量，
    若存在则用 ProxyLLMGateway，否则降级用模块级 llm_gateway。
    """
    env = os.environ.copy()
    env["ASSOCIATION_CLIENT_SERVER_URL"] = server_url
    env["ASSOCIATION_CLIENT_ACCESS_TOKEN"] = access_token
    # Python 解释器路径（供 PowerShell spawn python llm_judge.py）
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


async def run_wechat_rpa(
    *,
    command: str,  # collect / search
    association_name: str,
    person_name: str = "",
    server_url: str = "",
    access_token: str = "",
    limit: int = 10,
    timeout: int = 600,
    extra_args: Optional[list[str]] = None,
) -> dict:
    """运行微信 RPA PowerShell 脚本，返回解析后的 JSON dict。

    Raises:
        RuntimeError: 含 error_code（超时/进程失败/JSON解析失败）
    """
    script = scripts_dir() / "wechat-souyisou.ps1"
    if not script.exists():
        raise RuntimeError(f"WECHAT_SCRIPT_NOT_FOUND: {script}")

    args = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(script),
        "-Command", command,
        "-AssociationName", association_name,
    ]
    if person_name:
        args += ["-PersonName", person_name]
    if command == "collect":
        args += ["-Execute", "-UseProjectLlm", "-Limit", str(limit)]
    elif command == "search":
        args += ["-Execute"]
    if extra_args:
        args += extra_args

    env = make_env(server_url, access_token)

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
        raise RuntimeError("WECHAT_RPA_TIMEOUT")

    # 解析 stdout 最后一行 JSON
    try:
        payload = json.loads(
            stdout.decode("utf-8-sig").strip().splitlines()[-1]
        )
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("WECHAT_RPA_RESPONSE_INVALID")

    return payload
