@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ========================================
echo   AID Work Agent 服务启动脚本
echo ========================================
echo.

echo 步骤 1/4: 停止后端 Docker 容器
echo ----------------------------------------
docker stop aid-agent-api 2>nul
if %errorlevel% equ 0 (
    echo [OK] 后端容器已停止
) else (
    echo [INFO] 后端容器未运行或停止失败，继续下一步
)
docker stop aid-agent-ui 2>nul
echo.

echo 步骤 2/4: 停止前端服务（如有运行）
echo ----------------------------------------
:: 查找并停止可能运行的前端开发服务器
for /f "tokens=2" %%a in ('netstat -ano ^| findstr ":5173"') do (
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK] 已停止前端服务 (PID: %%a)
    )
)
echo [INFO] 前端服务检查完成
echo.

echo 步骤 3/4: 启动前端服务
echo ----------------------------------------
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo [INFO] 检测到未安装依赖，正在安装...
    call npm install
)
echo [OK] 正在启动前端开发服务器...
start "AID Frontend" cmd /k "npm run dev -- --port 5173"
cd /d "%~dp0"
echo [OK] 前端服务启动中 (http://localhost:5173)
echo.

echo 步骤 4/4: 启动后端 Docker 容器
echo ----------------------------------------
docker start aid-agent-api
if %errorlevel% equ 0 (
    echo [OK] 后端容器启动成功
    echo.
    echo ========================================
    echo   所有服务启动完成！
    echo ========================================
    echo.
    echo 前端地址: http://localhost:5173
    echo 后端地址: http://localhost:8000
    echo 健康检查: http://localhost:8000/health
    echo.
    echo 提示:
    echo   - 前端日志在新打开的窗口中
    echo   - 查看后端日志: docker logs -f aid-agent-api
    echo   - 停止所有服务: docker stop aid-agent-api
) else (
    echo [ERROR] 后端容器启动失败
    echo [INFO] 容器可能不存在，请先运行 docker compose up -d 创建容器
    exit /b 1
)

endlocal
