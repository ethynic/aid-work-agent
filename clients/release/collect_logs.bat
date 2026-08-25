@echo off
rem 日志采集入口：调用同目录 collect_logs.ps1（文件保持 GBK 编码 + CRLF）
echo 正在采集诊断日志（含 doctor 全链路检查，约 1 分钟）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect_logs.ps1"
pause
