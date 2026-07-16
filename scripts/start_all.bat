@echo off
cd /d "%~dp0.."

echo Pulling latest code from Git...
git pull
if %errorlevel% neq 0 (
    echo Warning: Git pull failed
    echo.
)

powershell -Command "Start-Process -FilePath 'D:\Program Files\CodeBuddy CN\CodeBuddy CN.exe' -ArgumentList '%CD%' -WindowStyle Maximized" >nul 2>&1
timeout /t 3 >nul

start "" "C:\Users\Ric\AppData\Local\SourceTree\SourceTree.exe"
timeout /t 1 >nul

start "" explorer.exe "D:\workbase\projects\aid-work-agent"
timeout /t 1 >nul

exit
