@echo off
title PebbleAgent GUI
cd /d "%~dp0"

echo ========================================
echo   PebbleAgent GUI - 启动中...
echo ========================================
echo.

:: 清理残留的后端进程（端口 19876）
echo [1/3] 清理残留进程...
for /f "tokens=5" %%a in ('netstat -ano ^| find "127.0.0.1:19876"') do (
    echo       发现残留进程 PID=%%a，正在终止...
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: 启动 Python WebSocket 后端（新窗口）
echo [2/3] 启动后端服务...
start "PebbleAgent-Backend" cmd /c "python run_backend.py ^& pause"

:: 等待后端就绪（检测端口 19876）
:wait_loop
timeout /t 1 /nobreak >nul
netstat -an | find "127.0.0.1:19876" >nul 2>&1
if errorlevel 1 goto wait_loop

echo      后端已就绪 (ws://127.0.0.1:19876)
echo.

:: 启动前端 dev server
echo [3/3] 启动前端开发服务器...
cd ui-gui-vue3
start "PebbleAgent-Frontend" cmd /c "npm run dev ^& pause"

echo.
echo ========================================
echo   在浏览器打开 http://localhost:5174
echo   关闭此窗口即可停止所有服务
echo ========================================
echo.

:: 等待用户按任意键退出
pause

:: 关闭所有子进程
echo 正在关闭服务...
taskkill /f /fi "WINDOWTITLE eq PebbleAgent-Backend" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq PebbleAgent-Frontend" >nul 2>&1
echo 已停止所有服务。
