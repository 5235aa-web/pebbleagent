@echo off
chcp 65001 >nul
title PebbleAgent 后端服务
cd /d "%~dp0"

echo ========================================
echo   PebbleAgent 后端服务 - 启动中...
echo ========================================
echo.

:: 清理残留的后端进程（端口 19876）
echo [1/3] 清理残留进程...
for /f "tokens=5" %%a in ('netstat -ano ^| find "127.0.0.1:19876"') do (
    echo       发现残留进程 PID=%%a，正在终止...
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: 启动 Python WebSocket 后端
echo [2/3] 启动后端服务...
start "PebbleAgent-Backend" cmd /c "python run_backend.py ^& pause"

:: 等待后端就绪（检测端口 19876）
echo [3/3] 等待后端就绪...
:wait_loop
timeout /t 1 /nobreak >nul
netstat -an | find "127.0.0.1:19876" >nul 2>&1
if errorlevel 1 goto wait_loop

echo.
echo ========================================
echo   ✅ 后端服务已就绪
echo   地址: ws://127.0.0.1:19876
echo.
echo   按任意键停止后端服务
echo ========================================
echo.

pause >nul

:: 关闭后端进程
echo 正在关闭后端服务...
taskkill /f /fi "WINDOWTITLE eq PebbleAgent-Backend" >nul 2>&1
echo 后端服务已停止。
timeout /t 1 /nobreak >nul
