@echo off
chcp 65001 >nul
rem AidWork runtime 后台自愈启动器（任务计划程序登录时调用，无窗口运行）
rem 优先用 npm 全局安装的 aid-runtime；开发机回退仓库 dist 直接 node 运行
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
echo [%date% %time%] 自愈循环启动，命令: %RT_CMD% >> "%LOG%"
:loop
%RT_CMD% start >> "%LOG%" 2>&1
echo [%date% %time%] runtime 退出，10 秒后自动拉起 >> "%LOG%"
timeout /t 10 /nobreak >nul
goto loop
