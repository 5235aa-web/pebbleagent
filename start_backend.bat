@echo off
title PebbleAgent Backend
cd /d "%~dp0"

echo ========================================
echo   PebbleAgent Backend - starting...
echo ========================================
echo.

:: Preflight. Fail fast with an actionable message instead of letting
:: python emit a traceback from a missing dependency.
echo [1/3] Checking environment...

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

echo   OK - environment ready
echo.

:: Clean up any stale backend holding port 19876
echo [2/3] Cleaning up stale processes...
for /f "tokens=5" %%a in ('netstat -ano ^| find "127.0.0.1:19876"') do (
    echo       found PID=%%a, terminating...
    taskkill /f /pid %%a >nul 2>&1
)
timeout /t 1 /nobreak >nul

:: Start the backend
echo [3/3] Starting backend...
start "PebbleAgent-Backend" cmd /c "python run_backend.py ^& pause"

echo       waiting for ws://127.0.0.1:19876 ...
:wait_loop
timeout /t 1 /nobreak >nul
netstat -an | find "127.0.0.1:19876" >nul 2>&1
if errorlevel 1 goto wait_loop

echo.
echo ========================================
echo   Backend is ready
echo   ws://127.0.0.1:19876
echo.
echo   Press any key to stop the backend
echo ========================================
echo.

pause >nul

echo Stopping backend...
taskkill /f /fi "WINDOWTITLE eq PebbleAgent-Backend" >nul 2>&1
echo Backend stopped.
timeout /t 1 /nobreak >nul
