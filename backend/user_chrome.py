"""The WebPilot Chrome: a real Google Chrome window with its own long-lived profile, started on demand.

Chrome 136+ refuses remote debugging on the user's *default* profile (a Google security measure against
cookie theft), so WebPilot cannot drive the everyday Chrome profile directly. Instead it keeps one dedicated
profile (default: %LOCALAPPDATA%\\WebPilot\\ChromeProfile). The user signs in to a site there once — by hand or
through WebPilot's saved logins — and every later task reuses those sessions, exactly like a person's browser.

The window is started as a normal Chrome process (not a Playwright-launched automation browser) with the
DevTools port bound to 127.0.0.1, and it stays open between tasks. WebPilot attaches over CDP per task.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from backend import config

logger = logging.getLogger("webpilot.chrome")

_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser",
]
_process: Optional[subprocess.Popen] = None
_launch_lock: Optional[asyncio.Lock] = None


def chrome_path() -> Optional[str]:
    explicit = os.getenv("WEBPILOT_CHROME_PATH")
    if explicit:
        return explicit if Path(explicit).exists() else None
    return next((c for c in _CANDIDATES if c and Path(c).exists()), None)


def profile_dir() -> Path:
    return config.CHROME_PROFILE_DIR


def cdp_port() -> int:
    return urlparse(config.CHROME_CDP_URL).port or 9222


async def endpoint_alive(timeout: float = 1.5) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.get(f"http://127.0.0.1:{cdp_port()}/json/version")
            return r.status_code == 200
    except Exception:
        return False


async def status() -> Dict[str, Any]:
    alive = await endpoint_alive()
    return {
        "cdp_url": config.CHROME_CDP_URL, "running": alive, "chrome_path": chrome_path(),
        "profile_dir": str(profile_dir()), "autolaunch": config.CHROME_AUTOLAUNCH,
        "launched_by_webpilot": _process is not None and _process.poll() is None,
    }


async def ensure_running() -> Dict[str, Any]:
    """Make sure the WebPilot Chrome is listening on the CDP port, starting it if allowed."""
    global _process, _launch_lock
    if await endpoint_alive():
        return {"running": True, "launched": False, "detail": "WebPilot Chrome is already running."}
    if not config.CHROME_AUTOLAUNCH:
        return {"running": False, "launched": False, "detail": "Auto-launch is off (WEBPILOT_CHROME_AUTOLAUNCH=false)."}
    exe = chrome_path()
    if exe is None:
        return {"running": False, "launched": False, "detail": "Google Chrome was not found (set WEBPILOT_CHROME_PATH)."}
    if _launch_lock is None:
        _launch_lock = asyncio.Lock()
    async with _launch_lock:
        if await endpoint_alive():
            return {"running": True, "launched": False, "detail": "WebPilot Chrome is already running."}
        profile_dir().mkdir(parents=True, exist_ok=True)
        args = [exe, f"--remote-debugging-port={cdp_port()}", "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile_dir()}", "--no-first-run", "--no-default-browser-check",
                "--restore-last-session"]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # survives a backend restart
        try:
            _process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                        creationflags=flags, close_fds=True)
        except OSError as exc:
            return {"running": False, "launched": False, "detail": f"Chrome could not start: {exc}"}
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if await endpoint_alive(timeout=1.0):
                logger.info("Started WebPilot Chrome (profile %s, CDP 127.0.0.1:%s).", profile_dir(), cdp_port())
                return {"running": True, "launched": True, "detail": "Started the WebPilot Chrome window."}
            await asyncio.sleep(0.4)
        return {"running": False, "launched": True,
                "detail": ("Chrome started but didn't open the DevTools port. If a WebPilot Chrome window is already open "
                           "without it (e.g. started by hand), close that window and try again.")}
