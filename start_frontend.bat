@echo off
REM WebPilot Simulated Alexa+ web client - serves http://localhost:3000
cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo [WebPilot] Installing frontend dependencies - first run only...
    call npm install --no-audit --no-fund || goto :error
)

echo [WebPilot] Alexa+ web client: http://localhost:3000  (backend expected at http://localhost:8000)
npm run dev
goto :eof

:error
echo [WebPilot] npm install failed. Make sure Node.js 18.18+ is installed.
pause
exit /b 1
