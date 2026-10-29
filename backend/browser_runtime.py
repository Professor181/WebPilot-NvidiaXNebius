"""Browser runtime: Playwright semantic DOM distiller with an HTTP/BeautifulSoup fallback.

* Live URLs are rendered in headless Chromium (Playwright) when it is available, giving a
  JS-rendered DOM and a real PNG screenshot.
* Local `/sandbox/*` URLs are served by invoking the FastAPI app directly over ASGI (no network
  hop, works with or without a running uvicorn server); Chromium then renders the returned HTML
  for the screenshot.
* If Chromium is not installed or cannot launch (restricted shells, CI, wrong event loop), the
  runtime falls back to httpx and generates a high-resolution SVG evidence snapshot instead, so an
  evidence image always exists.

Every fetched document passes through the Untrusted Content Firewall before the result is
returned.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx

from backend import config
from backend.firewall import sanitize_html_and_extract
from backend.models import utc_now_iso

logger = logging.getLogger("webpilot.browser")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36 WebPilot/1.0 (+Alexa+ hackathon agent)"
)
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
PLAYWRIGHT_RETRY_AFTER_SECONDS = 300


class BrowserFetchError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def artifact_url(filename: str) -> str:
    return f"/artifacts/{filename}"


class BrowserRuntime:
    def __init__(self) -> None:
        self._asgi_app = None
        self._playwright = None
        self._browser = None
        self._browser_loop: Optional[asyncio.AbstractEventLoop] = None
        self._locks: Dict[int, asyncio.Lock] = {}
        self._pw_disabled_until = 0.0
        self.playwright_last_error: Optional[str] = None

    # ------------------------------------------------------------------ wiring

    def set_asgi_app(self, app: Any) -> None:
        self._asgi_app = app

    def status(self) -> Dict[str, Any]:
        return {
            "playwright_enabled": config.PLAYWRIGHT_ENABLED,
            "playwright_browser_running": bool(self._browser and self._browser.is_connected()),
            "playwright_last_error": self.playwright_last_error,
            "fallback": "httpx + BeautifulSoup with SVG evidence snapshots",
            "headless": config.PLAYWRIGHT_HEADLESS,
        }

    # ------------------------------------------------------------- URL policy

    @staticmethod
    def normalize_url(url: str) -> str:
        url = (url or "").strip()
        if url.startswith("/"):
            return config.WEBPILOT_BASE_URL + url
        if not urlparse(url).scheme:
            return "https://" + url
        return url

    @staticmethod
    def is_sandbox_url(url: str) -> bool:
        p = urlparse(url)
        if not p.path.startswith("/sandbox"):
            return False
        base = urlparse(config.WEBPILOT_BASE_URL)
        local_hosts = {
            base.netloc,
            f"localhost:{config.WEBPILOT_PORT}",
            f"127.0.0.1:{config.WEBPILOT_PORT}",
        }
        return p.netloc in local_hosts

    @staticmethod
    async def _assert_public_destination(url: str) -> None:
        """SSRF guard: live fetches may only target public internet hosts."""
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            raise BrowserFetchError("UNSUPPORTED_SCHEME", f"Only http(s) URLs can be inspected, got '{p.scheme}'.")
        host = p.hostname
        if not host:
            raise BrowserFetchError("INVALID_URL", f"URL has no host: {url}")
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80))
        except socket.gaierror as exc:
            raise BrowserFetchError("DNS_RESOLUTION_FAILED", f"Could not resolve host '{host}': {exc}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise BrowserFetchError(
                    "BLOCKED_PRIVATE_NETWORK",
                    f"Refusing to fetch '{host}' ({ip}): private or local network addresses are not inspectable. "
                    "Use the built-in /sandbox/* sites for local demos.",
                )

    # -------------------------------------------------------------- Playwright

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(id(loop))
        if lock is None:
            lock = asyncio.Lock()
            self._locks[id(loop)] = lock
        return lock

    async def _get_browser(self):
        if not config.PLAYWRIGHT_ENABLED or time.monotonic() < self._pw_disabled_until:
            return None
        loop = asyncio.get_running_loop()
        if self._browser is not None and self._browser_loop is loop and self._browser.is_connected():
            return self._browser
        async with self._lock():
            if self._browser is not None and self._browser_loop is loop and self._browser.is_connected():
                return self._browser
            try:
                from playwright.async_api import async_playwright

                async def _launch():
                    pw = await async_playwright().start()
                    try:
                        browser = await pw.chromium.launch(headless=config.PLAYWRIGHT_HEADLESS)
                    except Exception:
                        await pw.stop()
                        raise
                    return pw, browser

                self._playwright, self._browser = await asyncio.wait_for(
                    _launch(), timeout=config.PLAYWRIGHT_LAUNCH_TIMEOUT_SECONDS
                )
                self._browser_loop = loop
                self.playwright_last_error = None
                logger.info("Playwright Chromium launched")
                return self._browser
            except Exception as exc:  # NotImplementedError on selector loops, missing binary, timeouts...
                self._browser = None
                self._playwright = None
                self.playwright_last_error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                self._pw_disabled_until = time.monotonic() + PLAYWRIGHT_RETRY_AFTER_SECONDS
                logger.warning("Playwright unavailable, using HTTP/BS4 fallback: %s", self.playwright_last_error)
                return None

    def _fallback_reason(self) -> str:
        if not config.PLAYWRIGHT_ENABLED:
            return "Playwright disabled (WEBPILOT_ENABLE_PLAYWRIGHT=false)"
        return self.playwright_last_error or "Playwright Chromium unavailable"

    async def close(self) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:  # pragma: no cover - best effort shutdown
            pass
        finally:
            self._browser = None
            self._playwright = None

    async def _playwright_screenshot_html(self, html_doc: str, path: Path) -> bool:
        browser = await self._get_browser()
        if browser is None:
            return False
        context = None
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 860}, java_script_enabled=False)
            page = await context.new_page()
            await page.set_content(html_doc, wait_until="load", timeout=15000)
            await page.screenshot(path=str(path), full_page=True)
            return True
        except Exception as exc:
            self.playwright_last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Playwright screenshot failed: %s", exc)
            return False
        finally:
            if context is not None:
                await context.close()

    async def _playwright_fetch(self, url: str, path: Path) -> Optional[Dict[str, Any]]:
        browser = await self._get_browser()
        if browser is None:
            return None
        context = None
        try:
            context = await browser.new_context(viewport={"width": 1280, "height": 860}, user_agent=USER_AGENT)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=int(config.HTTP_TIMEOUT_SECONDS * 1000))
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            html_doc = await page.content()
            title = await page.title()
            await page.screenshot(path=str(path), full_page=False)
            return {
                "final_url": page.url,
                "status_code": response.status if response else 200,
                "html": html_doc,
                "title": title,
            }
        except Exception as exc:
            self.playwright_last_error = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            logger.warning("Playwright navigation failed for %s: %s", url, exc)
            return None
        finally:
            if context is not None:
                await context.close()

    # ------------------------------------------------------------------- HTTP

    async def _asgi_request(self, url: str, method: str, form_data: Optional[Dict[str, Any]]) -> httpx.Response:
        if self._asgi_app is None:
            raise BrowserFetchError("SANDBOX_UNAVAILABLE", "The sandbox ASGI app has not been registered.")
        transport = httpx.ASGITransport(app=self._asgi_app)
        async with httpx.AsyncClient(
            transport=transport, base_url=config.WEBPILOT_BASE_URL, follow_redirects=True,
            headers={"User-Agent": USER_AGENT}, timeout=config.HTTP_TIMEOUT_SECONDS,
        ) as client:
            if method == "POST":
                return await client.post(url, data={k: str(v) for k, v in (form_data or {}).items()})
            return await client.get(url)

    async def _http_get(self, url: str) -> httpx.Response:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=config.HTTP_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                     "Accept-Language": "en-IN,en;q=0.9"},
        ) as client:
            response = await client.get(url)
            if len(response.content) > MAX_DOCUMENT_BYTES:
                raise BrowserFetchError("DOCUMENT_TOO_LARGE", f"Document exceeds {MAX_DOCUMENT_BYTES} bytes.")
            return response

    # ---------------------------------------------------------------- public

    async def inspect(
        self,
        url: str,
        task_id: Optional[str] = None,
        step_id: Optional[str] = None,
        *,
        method: str = "GET",
        form_data: Optional[Dict[str, Any]] = None,
        stamp: str = "EVIDENCE CAPTURED",
    ) -> Dict[str, Any]:
        """Navigate to `url`, sanitize + distill the DOM and capture a screenshot / SVG snapshot."""
        url = self.normalize_url(url)
        method = method.upper()
        task_id = task_id or "adhoc"
        step_id = step_id or uuid.uuid4().hex[:8]
        config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        png_path = config.ARTIFACTS_DIR / f"{task_id}_{step_id}.png"
        started = time.perf_counter()

        sandbox = self.is_sandbox_url(url)
        html_doc: Optional[str] = None
        final_url, status_code, rendered_by = url, 0, None
        screenshot_ok = False
        fallback_reason: Optional[str] = None

        if sandbox:
            response = await self._asgi_request(url, method, form_data)
            html_doc, status_code, final_url = response.text, response.status_code, str(response.url)
            transport_method = "ASGI_SANDBOX_DIRECT"
            screenshot_ok = await self._playwright_screenshot_html(html_doc, png_path)
            rendered_by = "PLAYWRIGHT_CHROMIUM" if screenshot_ok else "HTTP_BS4_FALLBACK"
            if not screenshot_ok:
                fallback_reason = self._fallback_reason()
        else:
            if method != "GET":
                raise BrowserFetchError(
                    "LIVE_WEB_MUTATION_REFUSED",
                    "WebPilot never submits forms or transactions on live third-party websites; "
                    "consequential actions are limited to the controlled sandbox.",
                )
            await self._assert_public_destination(url)
            pw_result = await self._playwright_fetch(url, png_path)
            if pw_result is not None:
                html_doc, status_code, final_url = pw_result["html"], pw_result["status_code"], pw_result["final_url"]
                screenshot_ok = True
                transport_method = rendered_by = "PLAYWRIGHT_CHROMIUM"
            else:
                fallback_reason = self._fallback_reason()
                try:
                    response = await self._http_get(url)
                except httpx.HTTPError as exc:
                    raise BrowserFetchError("NETWORK_ERROR", f"Could not fetch {url}: {type(exc).__name__}: {exc}") from exc
                html_doc, status_code, final_url = response.text, response.status_code, str(response.url)
                transport_method = rendered_by = "HTTP_BS4_FALLBACK"

        distilled = sanitize_html_and_extract(html_doc or "", final_url)
        capability_method = f"TIER_2_SEMANTIC_DOM:{rendered_by}"
        if screenshot_ok and png_path.exists():
            screenshot_path, screenshot_kind = png_path, "png"
        else:
            screenshot_path = self.render_evidence_svg(
                task_id, step_id, title=distilled["title"] or final_url, source_url=final_url,
                lines=summarize_distilled(distilled), stamp=stamp, capability_method=capability_method,
                alerts=len(distilled["firewall_alerts"]), tone=_tone_for(status_code, distilled),
            )
            screenshot_kind = "svg"

        return {
            "requested_url": url,
            "final_url": final_url,
            "status_code": status_code,
            "title": distilled["title"],
            "html": html_doc or "",
            "distilled": distilled,
            "is_sandbox": sandbox,
            "transport_method": transport_method,
            "capability_method": capability_method,
            "screenshot_path": str(screenshot_path),
            "screenshot_url": artifact_url(screenshot_path.name),
            "screenshot_kind": screenshot_kind,
            "render_mode": "live_viewport" if (screenshot_ok and not sandbox) else ("rendered_html" if screenshot_ok else "svg_snapshot"),
            "fallback_reason": fallback_reason,
            "fetched_at": utc_now_iso(),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    async def capture_html(
        self, html_doc: str, task_id: str, step_id: str, *, title: str, source_url: str,
        lines: List[str], stamp: str, tone: str = "info",
    ) -> Dict[str, str]:
        """Render an arbitrary HTML document (e.g. a filled-in form preview) to PNG, or SVG as fallback."""
        config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        png_path = config.ARTIFACTS_DIR / f"{task_id}_{step_id}.png"
        if await self._playwright_screenshot_html(html_doc, png_path) and png_path.exists():
            return {"screenshot_url": artifact_url(png_path.name), "screenshot_kind": "png",
                    "rendered_by": "PLAYWRIGHT_CHROMIUM"}
        path = self.render_evidence_svg(task_id, step_id, title=title, source_url=source_url, lines=lines,
                                        stamp=stamp, capability_method="TIER_2_SEMANTIC_DOM:HTTP_BS4_FALLBACK",
                                        alerts=0, tone=tone)
        return {"screenshot_url": artifact_url(path.name), "screenshot_kind": "svg", "rendered_by": "SVG_SNAPSHOT"}

    # --------------------------------------------------------------- evidence

    def render_evidence_svg(
        self,
        task_id: str,
        step_id: str,
        *,
        title: str,
        source_url: str,
        lines: List[str],
        stamp: str,
        capability_method: str,
        alerts: int = 0,
        tone: str = "info",
    ) -> Path:
        """Write a browser-like SVG evidence snapshot and return its path."""
        config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.ARTIFACTS_DIR / f"{task_id}_{step_id}.svg"
        colors = {
            "success": ("#15803d", "#dcfce7"),
            "warning": ("#b45309", "#fef3c7"),
            "danger": ("#b91c1c", "#fee2e2"),
            "info": ("#1d4ed8", "#dbeafe"),
        }
        stamp_fg, stamp_bg = colors.get(tone, colors["info"])
        lines = [ln for ln in lines if ln is not None][:30]
        width, line_h, body_top = 1280, 26, 196
        height = max(560, body_top + line_h * len(lines) + 90)

        def t(value: str, limit: int = 118) -> str:
            value = str(value)
            return escape(value if len(value) <= limit else value[: limit - 1] + "…")

        footer = (
            "WebPilot evidence snapshot · SVG fallback (Chromium not available)"
            if "FALLBACK" in capability_method
            else "WebPilot evidence card · rendered from structured, firewall-checked data"
        )
        stamp_text = t(stamp.upper(), 40)
        stamp_w = 24 + 9 * len(stamp.upper()[:40])
        body = []
        for i, line in enumerate(lines):
            y = body_top + i * line_h
            is_header = line.startswith("## ")
            text = line[3:] if is_header else line
            weight = "700" if is_header else "400"
            fill = "#0f172a" if is_header else "#334155"
            body.append(f'<text x="56" y="{y}" font-size="{17 if is_header else 15}" font-weight="{weight}" fill="{fill}">{t(text)}</text>')
        alert_badge = ""
        if alerts:
            alert_badge = (
                f'<rect x="56" y="{height - 64}" width="420" height="34" rx="8" fill="#fee2e2" stroke="#fca5a5"/>'
                f'<text x="72" y="{height - 41}" font-size="14" font-weight="700" fill="#991b1b">'
                f'CONTENT FIREWALL: {alerts} alert(s) recorded and neutralized</text>'
            )
        svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" font-family="Segoe UI, Roboto, Helvetica, Arial, sans-serif">
  <rect width="{width}" height="{height}" fill="#f8fafc"/>
  <rect x="0" y="0" width="{width}" height="56" fill="#e2e8f0"/>
  <circle cx="26" cy="28" r="7" fill="#ef4444"/><circle cx="48" cy="28" r="7" fill="#f59e0b"/><circle cx="70" cy="28" r="7" fill="#22c55e"/>
  <rect x="100" y="12" width="{width - 130}" height="32" rx="16" fill="#ffffff" stroke="#cbd5e1"/>
  <text x="122" y="33" font-size="14" fill="#334155">🔒 {t(source_url, 140)}</text>
  <rect x="24" y="76" width="{width - 48}" height="{height - 100}" rx="14" fill="#ffffff" stroke="#e2e8f0"/>
  <text x="56" y="120" font-size="24" font-weight="700" fill="#0f172a">{t(title, 70)}</text>
  <text x="56" y="150" font-size="13" fill="#64748b">Captured {escape(utc_now_iso())} · {escape(capability_method)} · task {escape(task_id)} · step {escape(step_id)}</text>
  <line x1="56" y1="168" x2="{width - 56}" y2="168" stroke="#e2e8f0"/>
  <g transform="translate({width - 56 - stamp_w}, 96)">
    <rect width="{stamp_w}" height="38" rx="8" fill="{stamp_bg}" stroke="{stamp_fg}" stroke-width="2"/>
    <text x="{stamp_w / 2}" y="25" font-size="15" font-weight="800" fill="{stamp_fg}" text-anchor="middle" letter-spacing="0.5">{stamp_text}</text>
  </g>
  {''.join(body)}
  {alert_badge}
  <text x="{width - 56}" y="{height - 40}" font-size="12" fill="#94a3b8" text-anchor="end">{escape(footer)}</text>
</svg>"""
        path.write_text(svg, encoding="utf-8")
        return path


def _tone_for(status_code: int, distilled: Dict[str, Any]) -> str:
    if status_code >= 400:
        return "danger"
    if distilled.get("barriers"):
        return "warning"
    if distilled.get("firewall_alerts"):
        return "warning"
    return "success"


def summarize_distilled(distilled: Dict[str, Any]) -> List[str]:
    """Human-readable summary lines of a distilled page, used for SVG snapshots."""
    lines: List[str] = []
    for h in distilled.get("headings", [])[:4]:
        lines.append(f"## {h['text']}")
    for table in distilled.get("tables", [])[:2]:
        lines.append(f"## Table {table['table_id']} ({table['row_count']} rows)")
        if table["headers"]:
            lines.append(" | ".join(table["headers"]))
        for row in table["rows"][:8]:
            lines.append(" | ".join(row))
    cards = distilled.get("product_cards", [])
    if cards:
        lines.append(f"## Product cards ({len(cards)})")
        for c in cards[:6]:
            lines.append(
                f"{c.get('title') or c.get('name')} — {c.get('display_price') or c.get('price')} — "
                f"rating {c.get('rating', '?')} — {c.get('stock_note') or ''}"
            )
    articles = distilled.get("articles", [])
    if articles:
        lines.append(f"## Articles / headlines ({len(articles)})")
        for a in articles[:8]:
            pts = f"  [{a['points']} pts]" if a.get("points") is not None else ""
            lines.append(f"• {a['title']}{pts}")
    prices = distilled.get("prices", [])
    if prices:
        lines.append(f"## Price points ({len(prices)})")
        for pr in prices[:4]:
            lines.append(f"{pr['raw']} — {pr['context'][:90]}")
    for form in distilled.get("forms", [])[:2]:
        names = [f["name"] for f in form["fields"] if f.get("name") and f["type"] != "hidden"]
        lines.append(f"## Form {form['form_id']} → {form['method']} {form['action']}")
        lines.append("Fields: " + ", ".join(names))
    for b in distilled.get("barriers", []):
        lines.append(f"BARRIER: {b['barrier_type']} — {b['evidence']}")
    for a in distilled.get("firewall_alerts", [])[:3]:
        lines.append(f"FIREWALL {a['alert_type']} [{a.get('hiding_technique') or ','.join(a['matched_patterns'])}]: {a['snippet'][:80]}")
    if len(lines) < 6:
        for text_line in distilled.get("sanitized_text", "").splitlines()[:12]:
            lines.append(text_line)
    return lines


browser_runtime = BrowserRuntime()
