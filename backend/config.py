"""Runtime configuration for the WebPilot backend.

Every setting has a sensible default so the backend runs with zero configuration.
Values can be overridden through environment variables or a `.env` file in the
project root.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Playwright launches Chromium as a subprocess, which on Windows requires the
# Proactor event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


APP_NAME = "webpilot-alexa-plus"
APP_TITLE = "WebPilot Task Agent"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = (
    "Autonomous web task planner for Alexa+ with a tiered capability router, an untrusted "
    "content firewall, a backend-enforced HMAC approval gate for consequential actions and "
    "post-action verification."
)

MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")

WEBPILOT_HOST = os.getenv("WEBPILOT_HOST", "127.0.0.1")
WEBPILOT_PORT = int(os.getenv("WEBPILOT_PORT", "8000"))
WEBPILOT_BASE_URL = os.getenv("WEBPILOT_BASE_URL", f"http://localhost:{WEBPILOT_PORT}").rstrip("/")
WEBPILOT_FRONTEND_URL = os.getenv("WEBPILOT_FRONTEND_URL", "http://localhost:3000").rstrip("/")
WEBPILOT_APPROVAL_SECRET = os.getenv("WEBPILOT_APPROVAL_SECRET", "webpilot-alexa-plus-2026-hmac-secret-key")
WEBPILOT_APPROVAL_TTL_SECONDS = int(os.getenv("WEBPILOT_APPROVAL_TTL_SECONDS", "900"))

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

# ---- AI brain (backend/llm_brain.py). Every provider is optional; the symbolic brain always works.
# WEBPILOT_BRAIN_PROVIDER: auto | nebius | ollama | cloud | deterministic
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")  # anthropic | openai | gemini (auto-detected from the key when empty)
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # optional override, e.g. an OpenAI-compatible gateway
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
LLM_TIMEOUT_SECONDS = float(os.getenv("WEBPILOT_LLM_TIMEOUT_SECONDS", "45"))

PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
PLAYWRIGHT_ENABLED = _env_bool("WEBPILOT_ENABLE_PLAYWRIGHT", "true")
PLAYWRIGHT_LAUNCH_TIMEOUT_SECONDS = float(os.getenv("PLAYWRIGHT_LAUNCH_TIMEOUT_SECONDS", "20"))
HTTP_TIMEOUT_SECONDS = float(os.getenv("WEBPILOT_HTTP_TIMEOUT_SECONDS", "15"))

# ---- Universal live browser autopilot (backend/live_autopilot.py)
# The only built-in URL: a generic web search engine used for site discovery when a goal names no URL.
AUTOPILOT_SEARCH_URL = os.getenv("WEBPILOT_AUTOPILOT_SEARCH_URL", "https://html.duckduckgo.com/html/")
AUTOPILOT_MAX_STEPS = int(os.getenv("WEBPILOT_AUTOPILOT_MAX_STEPS", "6"))
AUTOPILOT_SLOW_MO_MS = int(os.getenv("WEBPILOT_AUTOPILOT_SLOW_MO_MS", "400"))
AUTOPILOT_LOGIN_WAIT_SECONDS = int(os.getenv("WEBPILOT_AUTOPILOT_LOGIN_WAIT_SECONDS", "300"))
BROWSER_PROFILE_DIR = Path(os.getenv("WEBPILOT_BROWSER_PROFILE_DIR", str(BACKEND_DIR / "browser_profile")))
# Persistent mode first tries to attach to the user's own running Chrome over the DevTools protocol (loopback only).
CHROME_CDP_URL = os.getenv("WEBPILOT_CHROME_CDP_URL", "http://localhost:9222")
CHROME_CDP_TIMEOUT_MS = int(os.getenv("WEBPILOT_CHROME_CDP_TIMEOUT_MS", "15000"))
# The WebPilot Chrome: a real Chrome with its own long-lived profile, auto-started when a task needs it.
CHROME_PROFILE_DIR = Path(os.getenv("WEBPILOT_CHROME_PROFILE_DIR",
                                    str(Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "WebPilot" / "ChromeProfile")))
CHROME_AUTOLAUNCH = os.getenv("WEBPILOT_CHROME_AUTOLAUNCH", "true").lower() in ("1", "true", "yes", "on")
# Conversations keep their browser tab between turns; idle ones are closed and their screenshots deleted.
CONVERSATION_IDLE_SECONDS = int(os.getenv("WEBPILOT_CONVERSATION_IDLE_SECONDS", "1800"))
# Screenshots of finished stand-alone (non-conversation) live tasks are deleted after this long.
SCREENSHOT_RETENTION_SECONDS = int(os.getenv("WEBPILOT_SCREENSHOT_RETENTION_SECONDS", "1800"))
# How long a paused task waits for the user to choose how to sign in.
LOGIN_CHOICE_TIMEOUT_SECONDS = int(os.getenv("WEBPILOT_LOGIN_CHOICE_TIMEOUT_SECONDS", "600"))

DB_PATH = Path(os.getenv("WEBPILOT_DB_PATH", str(BACKEND_DIR / "webpilot.db")))
ARTIFACTS_DIR = Path(os.getenv("WEBPILOT_ARTIFACTS_DIR", str(BACKEND_DIR / "artifacts")))

# Origins that may call /mcp and /api from a browser. Requests without an Origin
# header (server-to-server, curl, Alexa cloud) are always allowed.
ALLOWED_ORIGIN_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
ALLOWED_ORIGIN_DOMAIN_SUFFIXES = ("amazon.com", "alexa.com")
EXTRA_ALLOWED_ORIGINS = tuple(
    o.strip().rstrip("/") for o in os.getenv("WEBPILOT_ALLOWED_ORIGINS", "").split(",") if o.strip()
)
# Regex mirror of the allow-list above, used by the CORS middleware.
CORS_ORIGIN_REGEX = (
    r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
    r"|^https?://([a-z0-9-]+\.)*(amazon|alexa)\.com(:\d+)?$"
)
