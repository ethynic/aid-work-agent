@echo off
cd /d "%~dp0.."

echo Pulling latest code from Git...
git pull
if %errorlevel% neq 0 (
    echo Warning: Git pull failed
    echo.
)

start "WSL Backend" cmd /k "cd /d "%CD%" && wsl -e bash -c "docker logs aid-agent-api -f""
timeout /t 2 >nul

powershell -Command "Start-Process -FilePath 'D:\Program Files\CodeBuddy CN\CodeBuddy CN.exe' -ArgumentList '%CD%' -WindowStyle Maximized" >nul 2>&1
timeout /t 3 >nul

start "" "C:\Users\Ric\AppData\Local\SourceTree\SourceTree.exe"
timeout /t 1 >nul

start "claude" wt.exe new-tab --title "claude" --startingDirectory "%CD%" -- claude
timeout /t 1 >nul

start "Services" cmd /c ""%~dp0..\start_services.bat""

exit
