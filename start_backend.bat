@echo off
setlocal
REM WebPilot backend - one-click Windows launcher
REM Creates .venv, installs dependencies + Playwright Chromium on first run, then serves on port 8000.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [WebPilot] Creating virtual environment...
    python -m venv .venv || goto :error
)

echo [WebPilot] Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r backend\requirements.txt || goto :error

if not exist ".venv\.chromium-installed" (
    echo [WebPilot] Installing Playwright Chromium - one time download...
    ".venv\Scripts\python.exe" -m playwright install chromium && type nul > ".venv\.chromium-installed"
    if errorlevel 1 echo [WebPilot] Chromium install failed - continuing with the HTTP/BS4 + SVG evidence fallback.
)

if "%WEBPILOT_PORT%"=="" set WEBPILOT_PORT=8000
if "%WEBPILOT_HOST%"=="" set WEBPILOT_HOST=127.0.0.1

echo.
echo [WebPilot] MCP endpoint : http://localhost:%WEBPILOT_PORT%/mcp
echo [WebPilot] Health       : http://localhost:%WEBPILOT_PORT%/health
echo [WebPilot] Sandbox      : http://localhost:%WEBPILOT_PORT%/sandbox
echo [WebPilot] API docs     : http://localhost:%WEBPILOT_PORT%/docs
echo.

REM No --reload: reload mode forces the selector event loop on Windows, which cannot launch Chromium.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host %WEBPILOT_HOST% --port %WEBPILOT_PORT%
goto :eof

:error
echo [WebPilot] Startup failed. See the messages above.
pause
exit /b 1
