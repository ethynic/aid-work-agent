@echo off
chcp 65001 >nul
rem AidWork runtime auto-restart launcher (called by Task Scheduler at logon, windowless)
rem Prefers globally installed aid-runtime; dev fallback: repo dist via node.
rem 2026-09-01: runtime now writes logs/runtime.log itself (with rotation), so this
rem script no longer redirects output (avoids double-writing). Only loop lines are
rem logged here. When run manually by double-click, output still shows in this window.
setlocal
set PATH=%PATH%;%APPDATA%\npm
set LOGDIR=%APPDATA%\aidwork-tool-runtime\logs
set LOG=%LOGDIR%\runtime.log
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
if exist "%LOG%.old" del /q "%LOG%.old"
if exist "%LOG%" for %%F in ("%LOG%") do if %%~zF GTR 5242880 ren "%LOG%" runtime.old.log
set RT_CMD=aid-runtime
where %RT_CMD% >nul 2>nul
if errorlevel 1 set RT_CMD=node C:\repos\aid-work-agent\clients\agent-tool-runtime\dist\src\cli.js
echo [%date% %time%] watcher loop start, cmd: %RT_CMD% >> "%LOG%"
:loop
%RT_CMD% start
echo [%date% %time%] runtime exited, restart in 10s >> "%LOG%"
timeout /t 10 /nobreak >nul
goto loop
