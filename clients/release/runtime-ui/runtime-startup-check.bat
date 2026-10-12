@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0runtime-startup-check.ps1"
if errorlevel 1 echo Startup check could not complete. No Runtime data was changed.
pause
