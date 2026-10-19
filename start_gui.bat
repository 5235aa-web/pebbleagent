@echo off
chcp 65001 >nul
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

:: 等待后端就绪（检测端口 19876，最多 60 秒）
set tries=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a tries+=1
netstat -an | find "127.0.0.1:19876" >nul 2>&1
if not errorlevel 1 goto backend_ready
if %tries% lss 60 goto wait_loop
echo       后端 60 秒内未就绪，请查看 PebbleAgent-Backend 窗口日志
goto start_frontend

:backend_ready
echo       后端已就绪 (ws://127.0.0.1:19876)
echo.

:start_frontend
:: 启动前端 dev server（新窗口）
echo [3/3] 启动前端开发服务器...
cd ui-gui-vue3
start "PebbleAgent-Frontend" cmd /c "npm run dev ^& pause"

echo.
echo ========================================
echo   在浏览器打开 http://localhost:5174
echo   后端日志: PebbleAgent-Backend 窗口
echo   前端日志: PebbleAgent-Frontend 窗口
echo   停止服务: 关闭上面两个窗口即可
echo ========================================
echo.
echo 本窗口 3 秒后自动关闭...
timeout /t 3 /nobreak >nul
exit /b
