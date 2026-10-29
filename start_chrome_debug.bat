@echo off
setlocal
REM Start your Google Chrome with remote debugging on 127.0.0.1:9222 so WebPilot's
REM "Persistent Profile" mode can attach to it (new tab, your signed-in sessions).
REM Chrome 136+ ignores --remote-debugging-port on the DEFAULT profile folder (a Google
REM security change against cookie theft), so this uses a dedicated profile folder.
REM Sign in to your sites once in this window; the logins persist across restarts.
REM Close every other Chrome window that uses this profile first.

set "PROFILE=%LOCALAPPDATA%\WebPilot\ChromeProfile"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" (
    echo [WebPilot] Google Chrome was not found. Install Chrome or edit this file with its path.
    exit /b 1
)
echo [WebPilot] Starting Chrome with remote debugging on 127.0.0.1:9222
echo [WebPilot] Profile: %PROFILE%
start "" "%CHROME%" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check
