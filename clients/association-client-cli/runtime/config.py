"""
客户端配置管理 —— 激活凭证存储 / machine_id 采集。

配置文件位置：%APPDATA%\\association-client\\cli-config.json
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "association-client"
CONFIG_FILE = CONFIG_DIR / "cli-config.json"


def get_machine_id() -> str:
    """采集本机唯一标识：sha256(主板序列号 + CPU ID + 磁盘序列号) 取前32位hex。

    Windows 用 PowerShell 采集硬件信息；非 Windows 退化用 uuid.getnode()（MAC 地址）。
    """
    if platform.system() == "Windows":
        try:
            # 用 PowerShell 采集主板/CPU/磁盘序列号
            ps_script = """
            $mb = (Get-WmiObject Win32_BaseBoard).SerialNumber
            $cpu = (Get-WmiObject Win32_Processor).ProcessorId
            $disk = (Get-WmiObject Win32_DiskDrive | Select-Object -First 1).SerialNumber
            "$mb|$cpu|$disk"
            """
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, timeout=10,
            )
            raw = result.stdout.strip() if result.returncode == 0 else ""
            if raw:
                return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        except Exception:
            pass
    # 退化方案：MAC 地址
    return hashlib.sha256(str(uuid.getnode()).encode("utf-8")).hexdigest()[:32]


def load_config() -> Optional[dict]:
    """读取配置文件，返回 None 表示未激活。"""
    if not CONFIG_FILE.exists():
        return None
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_config(config: dict) -> None:
    """保存配置文件（创建目录）。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_config() -> None:
    """删除配置文件（注销）。"""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()


def get_server_url(cli_arg: Optional[str] = None) -> str:
    """获取服务端地址：命令行参数 > 配置文件 > 默认值。"""
    if cli_arg:
        return cli_arg.rstrip("/")
    config = load_config()
    if config and config.get("server_url"):
        return config["server_url"].rstrip("/")
    return "https://agent.aidingyi.cn"


def get_access_token() -> Optional[str]:
    """从配置文件读取 access_token。"""
    config = load_config()
    return config.get("access_token") if config else None
