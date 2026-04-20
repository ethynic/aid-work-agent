@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

@REM echo Step 1/4: Stop backend Docker containers
@REM echo ----------------------------------------
@REM docker stop aid-agent-api 2>nul
@REM if %errorlevel% equ 0 (
@REM     echo [OK] Backend container stopped
@REM ) else (
@REM     echo [INFO] Backend container not running or stop failed, continuing
@REM )
@REM docker stop aid-agent-ui 2>nul
@REM echo.

@REM echo Step 2/4: Stop frontend service (if running)
@REM echo ----------------------------------------
@REM for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173"') do (
@REM     taskkill /F /PID %%a >nul 2>&1
@REM     if !errorlevel! equ 0 (
@REM         echo [OK] Frontend service stopped (PID: %%a)
@REM     )
@REM )
@REM echo [INFO] Frontend service check complete
@REM echo.

@REM echo Step 3/4: Start backend Docker container
@REM echo ----------------------------------------
@REM echo [INFO] Clearing backend log file...
@REM type nul > log\aid-work-agent.log
@REM echo [OK] Log file cleared
@REM docker start aid-agent-api
@REM if %errorlevel% equ 0 (
@REM     echo [OK] Backend container started successfully
@REM     echo.
@REM     echo ========================================
@REM     echo   All services started successfully!
@REM     echo ========================================
@REM     echo.
@REM     echo Frontend: http://localhost:5173
@REM     echo Backend:  http://localhost:8000
@REM     echo Health:   http://localhost:8000/health
@REM     echo.
@REM     echo Tips:
@REM     echo   - Frontend logs in new window
@REM     echo   - View backend logs: docker logs -f aid-agent-api
@REM     echo   - Stop all services: docker stop aid-agent-api
@REM ) else (
@REM     echo [ERROR] Backend container failed to start
@REM     echo [INFO] Container may not exist, run: docker compose up -d
@REM     exit /b 1
@REM )

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
