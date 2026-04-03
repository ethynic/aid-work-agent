@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo Step 1/4: Stop backend Docker containers
echo ----------------------------------------
docker stop aid-agent-api 2>nul
if %errorlevel% equ 0 (
    echo [OK] Backend container stopped
) else (
    echo [INFO] Backend container not running or stop failed, continuing
)
docker stop aid-agent-ui 2>nul
echo.

echo Step 2/4: Stop frontend service (if running)
echo ----------------------------------------
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173"') do (
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK] Frontend service stopped (PID: %%a)
    )
)
echo [INFO] Frontend service check complete
echo.

echo Step 3/4: Start backend Docker container
echo ----------------------------------------
@REM echo [INFO] Clearing backend log file...
@REM type nul > log\aid-work-agent.log
@REM echo [OK] Log file cleared
docker start aid-agent-api
if %errorlevel% equ 0 (
    echo [OK] Backend container started successfully
    echo.
    echo ========================================
    echo   All services started successfully!
    echo ========================================
    echo.
    echo Frontend: http://localhost:5173
    echo Backend:  http://localhost:8000
    echo Health:   http://localhost:8000/health
    echo.
    echo Tips:
    echo   - Frontend logs in new window
    echo   - View backend logs: docker logs -f aid-agent-api
    echo   - Stop all services: docker stop aid-agent-api
) else (
    echo [ERROR] Backend container failed to start
    echo [INFO] Container may not exist, run: docker compose up -d
    exit /b 1
)

echo Step 4/4: Start frontend service
echo ----------------------------------------
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo [INFO] Dependencies not found, installing...
    call npm install
)
echo [OK] Starting frontend dev server...
start "AID Frontend" cmd /k "npm run dev -- --port 5173"
timeout /t 3 /nobreak >nul
start http://localhost:5173
cd /d "%~dp0"
echo [OK] Frontend starting at http://localhost:5173
echo.

endlocal
