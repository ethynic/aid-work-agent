"""E0 入口；本模块不包含任何设备写入动作。"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


from .errors import ProbeError


class Device:
    def __init__(self, adb, serial):
        self.adb = shutil.which(adb)
        self.serial = serial
        if not self.adb:
            raise ProbeError("ADB_MISSING: 安装 Android SDK Platform Tools 或指定 --adb")
        if not serial or serial.startswith("-") or any(c.isspace() for c in serial):
            raise ProbeError("SERIAL_REQUIRED: 用 --serial 指定已授权设备")

    def run(self, *args):
        try:
            return subprocess.run([self.adb, *args], capture_output=True, check=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            raise ProbeError("ADB_COMMAND_FAILED: 检查连接与设备授权") from None

    def check(self):
        lines = self.run("devices").decode("utf-8", errors="replace").splitlines()
        states = [line.split()[1] for line in lines if len(line.split()) == 2 and line.split()[0] == self.serial]
        if states != ["device"]:
            raise ProbeError("DEVICE_NOT_READY: 确认 USB 调试授权，重新连接离线设备")

    def screenshot(self):
        return self.run("-s", self.serial, "exec-out", "screencap", "-p")


def main(argv=None):
    parser = argparse.ArgumentParser(description="E0 只读定位；不会点击或拨号")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "observe"):
        command = sub.add_parser(name)
        command.add_argument("--adb", default="adb")
        command.add_argument("--serial")
        if name == "observe":
            command.add_argument("--target", required=True)
            command.add_argument("--output", type=Path, required=True, help="仓库外新建证据目录")
            command.add_argument("--confirm-test-content", action="store_true", help="确认屏幕仅含无敏感测试数据，且允许发送给视觉 API")
    args = parser.parse_args(argv)
    try:
        if args.command == "observe" and not args.confirm_test_content:
            raise ProbeError("TEST_CONTENT_CONFIRMATION_REQUIRED")
        device = Device(args.adb, args.serial)
        device.check()
        if args.command == "doctor":
            result = {"status": "PASS", "scope": "device_readiness_only", "api_configured": bool(os.getenv("MCA_VISION_API_KEY") and os.getenv("MCA_VISION_ENDPOINT")), "unproven": ["E0_real_vision", "E1_click", "telephone_audio"]}
        else:
            try:
                from .observe import observe
            except ImportError:
                raise ProbeError("DEPENDENCIES_MISSING: 安装实验 requirements.lock") from None
            result = observe(device, args.target, args.output)
    except ProbeError as error:
        result = {"status": error.status, "reason": error.reason}
    print(json.dumps(result, ensure_ascii=False))
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
