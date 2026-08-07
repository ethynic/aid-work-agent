# PyInstaller 打包配置 —— 协会信息收集客户端 CLI
# 打包命令：pyinstaller build.spec
# 产出：dist/association-cli.exe

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = []

# Playwright Python 包（不含浏览器二进制，浏览器由客户预装）
pw_datas, pw_binaries, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_binaries
hiddenimports += pw_hidden

# 必须带入的脚本文件
scripts_dir = "scripts"
datas += [
    ("scripts/wechat-souyisou.ps1", "scripts"),
    ("scripts/wechat-souyisou-lib.ps1", "scripts"),
    ("scripts/extract-mobile.ps1", "scripts"),
    ("scripts/read-artifact.ps1", "scripts"),
    ("scripts/llm_judge.py", "scripts"),
    ("scripts/ocr_adapter.py", "scripts"),
    ("scripts/wenxin_collect.py", "scripts"),
]

# 必须带入的 runtime 模块
datas += [
    ("runtime", "runtime"),
]

# hidden imports —— 协会收集核心模块 + runtime
hiddenimports += [
    "runtime",
    "runtime.proxy_gateway",
    "runtime.config",
    "runtime.progress_reporter",
    "runtime.powershell_runner",
    "runtime.playwright_check",
    "runtime.wenxin_browser",
    "src.services.association_batch_enrichment",
    "src.services.association_enrichment_providers",
    "src.services.association_profile_extractor",
    "src.services.official_site_browser_collector",
    "src.services.official_site_page_collector",
    "src.services.billing",
    "src.llm.gateway",
    "src.tools.ocr.ocr_tool",
]

a = Analysis(
    ["main.py"],
    pathex=["..", "../.."],  # clients/ 目录 + 项目根目录（让 src.* 可 import）
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "numpy",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="association-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    onefile=True,
)
