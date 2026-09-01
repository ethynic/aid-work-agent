@echo off
rem 日志采集入口：调用同目录 collect_logs.ps1（文件保持 GBK 编码 + CRLF）
if not exist "%~dp0collect_logs.ps1" (
  echo [错误] 找不到 collect_logs.ps1：请不要在压缩包里直接双击运行。
  echo 请先把整个压缩包解压到任意文件夹，再双击解压后的 collect_logs.bat。
  echo 当前运行目录：%~dp0
  pause
  exit /b 1
)
echo 正在采集诊断日志（含 doctor 全链路检查，约 1 分钟）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect_logs.ps1"
pause
