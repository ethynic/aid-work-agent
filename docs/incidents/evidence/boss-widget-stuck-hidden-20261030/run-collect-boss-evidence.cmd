@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect-boss-widget-evidence.ps1"
if errorlevel 1 echo Collection failed. Please send the error shown in this window.
pause
