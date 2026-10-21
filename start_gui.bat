@echo off
title PebbleAgent GUI
cd /d "%~dp0"

echo ========================================
echo   PebbleAgent GUI - starting...
echo ========================================
echo.

:: Preflight. Fail fast with an actionable message instead of letting
:: vite/python emit "not recognized as an internal or external command".
echo [0/3] Checking environment...

where python >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] python was not found in PATH.
    echo   Install Python 3.11+ and make sure it is on PATH.
    echo.
    pause
    exit /b 1
)

python -c "import websockets, openai, httpx, yaml" >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Python dependencies are not installed.
    echo   Run from the project root:
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] npm was not found in PATH.
    echo   Install Node.js 18+ and make sure it is on PATH.
    echo.
    pause
    exit /b 1
)

if not exist "ui-gui-vue3\node_modules" (
    echo   [ERROR] Frontend dependencies are not installed.
    echo   Run:
    echo     cd ui-gui-vue3
    echo     npm install
    echo.
    pause
    exit /b 1
)

echo   OK - environment ready
echo.

:: Clean up any stale backend holding port 19876
echo [1/3] Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -ano ^| find "127.0.0.1:19876"') do (
    echo       found PID=%%a, terminating...
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Start the Python WebSocket backend in its own window
echo [2/3] Starting backend...
start "PebbleAgent-Backend" cmd /c "python run_backend.py ^& pause"

:: Wait for the backend port (max 60s)
set tries=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a tries+=1
netstat -an | find "127.0.0.1:19876" >nul 2>&1
if not errorlevel 1 goto backend_ready
if %tries% lss 60 goto wait_loop
echo       backend not ready after 60s, check the PebbleAgent-Backend window
goto start_frontend

:backend_ready
echo       backend ready (ws://127.0.0.1:19876)
echo.

:start_frontend
:: Start the Vite dev server in its own window
echo [3/3] Starting frontend dev server...
cd ui-gui-vue3
start "PebbleAgent-Frontend" cmd /c "npm run dev ^& pause"

echo.
echo ========================================
echo   Open: http://localhost:5174
echo   Backend log: PebbleAgent-Backend window
echo   Frontend log: PebbleAgent-Frontend window
echo   Stop: close both windows
echo ========================================
echo.
echo   This window closes in 3 seconds...
timeout /t 3 /nobreak >nul
exit /b
