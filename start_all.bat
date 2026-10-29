@echo off
REM WebPilot - one-click full stack launcher (Windows)
REM   Window 1: FastAPI MCP backend on :8000 (no --reload, so the Proactor loop can launch headless Chromium)
REM   Window 2: Next.js Simulated Alexa+ web client on :3000
cd /d "%~dp0"

echo [WebPilot] Starting FastAPI MCP backend (port 8000)...
start "WebPilot Backend :8000" cmd /k call "%~dp0start_backend.bat"

echo [WebPilot] Starting Next.js Alexa+ web client (port 3000)...
start "WebPilot Frontend :3000" cmd /k call "%~dp0start_frontend.bat"

echo [WebPilot] Waiting for the backend health check...
set /a tries=0
:wait_backend
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://localhost:8000/health -TimeoutSec 2) | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto :wait_frontend
set /a tries+=1
if %tries% geq 180 goto :timeout
timeout /t 2 /nobreak >nul
goto :wait_backend

:wait_frontend
echo [WebPilot] Backend is up. Waiting for the web client...
set /a tries=0
:wait_frontend_loop
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing http://localhost:3000 -TimeoutSec 5) | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 goto :open
set /a tries+=1
if %tries% geq 180 goto :timeout
timeout /t 2 /nobreak >nul
goto :wait_frontend_loop

:open
echo.
echo [WebPilot] Ready!
echo    Alexa+ web client : http://localhost:3000
echo    MCP endpoint      : http://localhost:8000/mcp
echo    Sandbox sites     : http://localhost:8000/sandbox
echo    API docs          : http://localhost:8000/docs
start "" http://localhost:3000
goto :eof

:timeout
echo [WebPilot] Timed out waiting for the servers. Check the two server windows for errors.
pause
exit /b 1
