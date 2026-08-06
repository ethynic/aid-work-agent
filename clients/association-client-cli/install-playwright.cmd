@echo off
chcp 65001 >nul
echo ========================================
echo   正在安装 Playwright Chromium 浏览器
echo   （协会官网采集需要，约 150MB 下载）
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11+
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/2] 安装 playwright 包...
pip install playwright
if errorlevel 1 (
    echo [错误] playwright 包安装失败
    pause
    exit /b 1
)

echo.
echo [2/2] 安装 Chromium 浏览器...
playwright install chromium
if errorlevel 1 (
    echo [错误] Chromium 安装失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   安装完成！现在可以运行协会信息收集了。
echo ========================================
pause
