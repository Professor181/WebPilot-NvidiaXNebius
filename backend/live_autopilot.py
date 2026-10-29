"""Universal, site-agnostic live browser autopilot (Sense → Think → Act → Verify).

WebPilot drives a real Playwright Chromium across multiple pages of ANY website:

* **Site discovery** — an explicit URL/domain in the goal is opened directly; a site named without a URL (or a
  plain web question) is discovered through the configured web search engine (``AUTOPILOT_SEARCH_URL``), the
  only built-in URL. There is no domain lookup table.
* **Universal DOM indexer** — every step injects one generic scanner that numbers visible, enabled interactive
  elements (``data-webpilot-idx``) and describes them by tag, input type, ARIA role, accessible name, input value,
  placeholder, landmark and surrounding card text. No site-specific selectors exist anywhere in this module.
* **Decisions** — :func:`backend.llm_brain.decide_next_browser_action` (LLM proposes, semantic policy and safety
  rules verify).
* **Visual evidence** — before each click/fill the target gets a glowing cyan outline and a floating
  "WebPilot Step #k" badge, then a real PNG screenshot is captured (``artifacts/{task_id}_step_{k}.png``).
* **Safety** — page text goes through the Untrusted Content Firewall every step; consequential controls are
  classified server-side; checkout/submit/send/book controls pause at the HMAC approval gate; final payment
  controls ("Pay now", "Place order") are never pressed — WebPilot hands off to the human. Private network
  targets are blocked; CAPTCHA/OTP walls end in an honest BLOCKED (or wait for the user to sign in when the
  browser window is visible with a persistent profile).
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from backend import config
from backend.browser_runtime import BrowserFetchError, artifact_url, browser_runtime
from backend.firewall import sanitize_html_and_extract
from backend.models import utc_now_iso

logger = logging.getLogger("webpilot.autopilot")

SETTLE_TIMEOUT_MS = 10000
SESSION_TTL_SECONDS = config.WEBPILOT_APPROVAL_TTL_SECONDS

INDEXER_JS = r"""
(() => {
  const SEL = 'input, textarea, select, button, a[href], [role="button"], [role="link"], [role="searchbox"], ' +
              '[role="combobox"], [role="textbox"], [contenteditable="true"], [contenteditable=""], [role="radio"], ' +
              '[role="option"], [role="gridcell"], [role="tab"], [role="checkbox"], [role="menuitem"], label[for], ' +
              '[data-date], [data-day], [aria-pressed]';
  document.querySelectorAll('[data-webpilot-idx]').forEach(e => e.removeAttribute('data-webpilot-idx'));
  const out = []; let i = 0;
  const vw = window.innerWidth;
  const priceRe = /(₹|Rs\.?|INR|US\$|\$|USD|€|EUR|£|GBP)\s?\d[\d,]*(\.\d{1,2})?/;
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 5 || r.height < 5) return null;
    if (r.right < 0 || r.bottom + window.scrollY < 0 || r.left > vw + 5) return null;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return null;
    }
    return r;
  };
  const accName = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(id => (document.getElementById(id)||{}).innerText||'').join(' ').trim(); if (t) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    if (el.labels && el.labels.length) return el.labels[0].innerText;
    if (el.getAttribute('title')) return el.getAttribute('title');
    const img = el.querySelector && el.querySelector('img[alt]');
    const txt = (el.innerText || '').trim();
    if (txt) return txt;
    if (img) return img.getAttribute('alt');
    return el.value || el.getAttribute('placeholder') || '';
  };
  const landmark = (el) => {
    if (el.closest('dialog, [role="dialog"], [aria-modal="true"]')) return 'dialog';
    if (el.closest('[role="search"], form[role="search"]')) return 'search';
    if (el.closest('nav, [role="navigation"]')) return 'nav';
    if (el.closest('footer, [role="contentinfo"]')) return 'footer';
    if (el.closest('header, [role="banner"]')) return 'header';
    if (el.closest('main, [role="main"]')) return 'main';
    return 'none';
  };
  const container = (el) => {
    // Nearest "card": the closest ancestor that carries a price, else the first mid-sized ancestor.
    let n = el, fallback = '';
    for (let k = 0; k < 6 && n.parentElement; k++) {
      n = n.parentElement;
      const t = (n.innerText || '').trim();
      if (t.length > 1600) break;  // big product cards (e.g. marketplace search results) still count
      if (priceRe.test(t)) return t;
      if (!fallback && t.length >= 40) fallback = t;
    }
    return fallback;
  };
  const groupLabel = (el) => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return lg.innerText.trim(); }
    const g = el.closest('[role="radiogroup"], [role="group"], [role="listbox"], [aria-label]');
    if (g && g !== el) return (g.getAttribute('aria-label') || '').trim();
    return '';
  };
  const seen = new Set();
  for (const el of document.querySelectorAll(SEL)) {
    if (seen.has(el)) continue; seen.add(el);
    if (el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'hidden') continue;
    const disabled = !!(el.disabled || el.getAttribute('aria-disabled') === 'true');
    const r = visible(el); if (!r) continue;
    i += 1; el.setAttribute('data-webpilot-idx', String(i));
    const form = el.form || el.closest('form');
    const ctext = container(el);
    const pm = ctext.match(priceRe);
    let inSearchForm = false;
    const scope = form || el.parentElement;
    if (scope) {
      const box = scope.querySelector('input[type="search"], [role="searchbox"], input:not([type]), input[type="text"]');
      const boxText = box ? [accName(box), box.getAttribute('placeholder'), box.getAttribute('name'), box.getAttribute('title')].join(' ') : '';
      const searchy = !!box && ((box.type || '') === 'search' || box.getAttribute('role') === 'searchbox' ||
                      !!box.closest('[role="search"], search') || /search|find|query|look ?up|keyword/i.test(boxText)) &&
                      !/^cc-|email|tel|password/i.test(box.getAttribute('autocomplete') || '');
      inSearchForm = searchy && box !== el && (el.tagName === 'BUTTON' || (el.type || '') === 'submit' || el.getAttribute('role') === 'button') &&
                     (scope.querySelectorAll('input:not([type="hidden"]), textarea').length <= 2);
    }
    out.push({
      idx: i, tag: el.tagName.toLowerCase(), input_type: (el.getAttribute('type') || '').toLowerCase(),
      role: (el.getAttribute('role') || '').toLowerCase(), accessible_name: (accName(el) || '').trim().slice(0, 200),
      aria_label: (el.getAttribute('aria-label') || '').trim().slice(0, 200),
      placeholder: (el.getAttribute('placeholder') || '').slice(0, 120), text: (el.innerText || '').trim().slice(0, 200),
      value: (el.tagName === 'INPUT' && el.type !== 'password')
        ? ((['submit', 'button'].includes((el.getAttribute('type') || '').toLowerCase())
            ? (el.getAttribute('value') || el.value) : el.value) || '').slice(0, 120) : '',
      name_attr: (el.getAttribute('name') || '').slice(0, 60), href: el.href || '',
      contenteditable: el.isContentEditable || false, landmark: landmark(el), in_search_form: inSearchForm,
      container_text: ctext.slice(0, 300), price_text: pm ? pm[0] : '',
      disabled: disabled, required: !!el.required, readonly: !!el.readOnly,
      autocomplete: (el.getAttribute('autocomplete') || '').toLowerCase().slice(0, 40),
      checked: !!(el.checked || el.getAttribute('aria-checked') === 'true'),
      selected_state: el.getAttribute('aria-pressed') === 'true' || el.getAttribute('aria-selected') === 'true' ||
                      el.getAttribute('aria-current') === 'true',
      state_hint: ((typeof el.className === 'string' ? el.className : '') + ' ' + (el.getAttribute('title') || '')).slice(0, 160),
      title: (el.getAttribute('title') || '').slice(0, 120),
      data_date: el.getAttribute('data-date') || el.getAttribute('data-day') || el.getAttribute('datetime') || '',
      group_label: groupLabel(el).slice(0, 80),
      options: el.tagName === 'SELECT' ? Array.from(el.options).slice(0, 60).map(o => ({text: (o.text || '').trim().slice(0, 60),
               value: (o.value || '').slice(0, 60), disabled: !!o.disabled, selected: !!o.selected})) : undefined,
      bounding_box: {x: Math.round(r.left), y: Math.round(r.top + window.scrollY), w: Math.round(r.width), h: Math.round(r.height)}
    });
    if (i >= 400) break;
  }
  return out;
})()
"""

HIGHLIGHT_JS = r"""
([idx, label]) => {
  const el = document.querySelector(`[data-webpilot-idx="${idx}"]`);
  if (!el) return false;
  { const b = el.getBoundingClientRect();  // only scroll when off-screen: scrolling closes popovers on some sites
    if (b.top < 0 || b.bottom > window.innerHeight || b.left < 0 || b.right > window.innerWidth) el.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
  el.setAttribute('data-webpilot-prev-style', el.getAttribute('style') || '');
  el.style.outline = '3px solid #00CAFF'; el.style.outlineOffset = '2px';
  el.style.boxShadow = '0 0 12px #00CAFF, 0 0 28px rgba(0,202,255,0.55)';
  el.style.background = 'rgba(0, 202, 255, 0.12)';
  const r = el.getBoundingClientRect();
  const badge = document.createElement('div');
  badge.id = 'webpilot-step-badge';
  badge.textContent = label;
  badge.style.cssText = 'position:fixed;z-index:2147483647;left:' + Math.max(4, r.left) + 'px;top:' + Math.max(4, r.top - 30) +
    'px;background:#00CAFF;color:#04111f;font:700 12px/1.2 Segoe UI,Arial,sans-serif;padding:6px 10px;border-radius:8px;' +
    'box-shadow:0 4px 18px rgba(0,202,255,.6);pointer-events:none';
  document.body.appendChild(badge);
  return true;
}
"""

UNHIGHLIGHT_JS = r"""
() => {
  document.getElementById('webpilot-step-badge')?.remove();
  document.querySelectorAll('[data-webpilot-prev-style]').forEach(el => {
    el.setAttribute('style', el.getAttribute('data-webpilot-prev-style')); el.removeAttribute('data-webpilot-prev-style');
  });
}
"""

# Consent banners: prefer the most privacy-preserving choice; never auto-accept.
DISMISS_JS = r"""
() => {
  const scopes = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog, [id*="cookie" i], ' +
    '[class*="cookie" i], [id*="consent" i], [class*="consent" i], [id*="gdpr" i], [class*="banner" i]'));
  const prefer = [/reject( all| non-essential)?|decline|only (necessary|essential)|necessary only|refuse/i,
                  /^(close|dismiss|not now|no,? thanks|maybe later|×|x)$/i];
  for (const re of prefer) {
    for (const scope of scopes) {
      const r = scope.getBoundingClientRect(); if (r.width < 5 || r.height < 5) continue;
      for (const b of scope.querySelectorAll('button, [role="button"], a')) {
        const t = (b.innerText || b.getAttribute('aria-label') || '').trim();
        if (t && re.test(t)) { b.click(); return t; }
      }
    }
  }
  return null;
}
"""

SIGNATURE_JS = "() => (document.body ? document.body.innerText.length + ':' + document.querySelectorAll('*').length : '0')"


# ============================================================================ sessions

@dataclass
class AutopilotSession:
    task_id: str
    playwright: Any
    browser: Any
    context: Any
    page: Any
    headed: bool
    persistent: bool
    loop: asyncio.AbstractEventLoop
    created_at: float = field(default_factory=time.monotonic)
    notes: List[str] = field(default_factory=list)
    # "cdp" = attached to the user's own running Chrome; "persistent_profile" = launched with backend/browser_profile;
    # "ephemeral" = fresh private context.
    connection: str = "ephemeral"
    owns_lock: bool = False
    reused_tab: bool = False  # CDP: driving one of the user's already-open tabs (never closed by WebPilot)
    user_tabs: List[Dict[str, str]] = field(default_factory=list)
    # A conversation keeps one tab across turns ("show me shoes" → "is the first one in size 10?" → "buy it").
    conversation_id: Optional[str] = None
    task_ids: List[str] = field(default_factory=list)
    memory: Dict[str, Any] = field(default_factory=dict)
    last_active: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        if self.conversation_id:
            return time.monotonic() - self.last_active > config.CONVERSATION_IDLE_SECONDS
        return time.monotonic() - self.created_at > SESSION_TTL_SECONDS

    def touch(self) -> None:
        self.last_active = time.monotonic()

    async def close(self) -> None:
        if self.connection == "cdp":
            # The browser and its default context belong to the user: close only the tab WebPilot opened, then
            # disconnect. Never context.close() / browser.close() here — that would close the user's own tabs.
            try:
                if self.reused_tab:
                    await self.page.unroute("**/*", _route_handler)  # hand the user's tab back untouched
                elif not self.page.is_closed():
                    await self.page.close()
            except Exception:
                pass
            try:
                await self.playwright.stop()
            except Exception:
                pass
            return
        try:
            await self.context.close()
        except Exception:
            pass
        try:
            if self.browser is not None:
                await self.browser.close()
        except Exception:
            pass
        try:
            await self.playwright.stop()
        except Exception:
            pass


SESSIONS: Dict[str, AutopilotSession] = {}
_profile_lock: Optional[asyncio.Lock] = None


def _host_is_private(host: str) -> bool:
    if host in ("localhost", "0.0.0.0", "::1") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


async def _route_handler(route: Any) -> None:
    """Serve /sandbox/* through the FastAPI app (no server needed); block private-network requests."""
    request = route.request
    url = request.url
    if browser_runtime.is_sandbox_url(url):
        if browser_runtime._asgi_app is None:
            await route.abort()
            return
        transport = httpx.ASGITransport(app=browser_runtime._asgi_app)
        try:
            raw_headers = await request.all_headers()  # includes the Cookie header (request.headers omits it)
        except Exception:
            raw_headers = request.headers
        headers = {k: v for k, v in raw_headers.items() if k.lower() in ("content-type", "accept", "cookie")}
        async with httpx.AsyncClient(transport=transport, base_url=config.WEBPILOT_BASE_URL, follow_redirects=False,
                                     timeout=config.HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.request(request.method, url, content=request.post_data_buffer, headers=headers)
        out_headers = {k: v for k, v in resp.headers.items()
                       if k.lower() not in ("content-length", "content-encoding", "transfer-encoding", "set-cookie")}
        cookies = resp.headers.get_list("set-cookie")
        if cookies:
            out_headers["set-cookie"] = "\n".join(cookies)  # Playwright applies one cookie per line
        if 300 <= resp.status_code < 400 and "location" in out_headers:
            # Chromium follows a fulfilled redirect outside the interceptor; re-navigate from the page instead so the
            # follow-up request is routed through the app as well.
            import json as _json
            from urllib.parse import urljoin

            target = urljoin(url, out_headers["location"])
            body = (f"<!doctype html><meta charset=utf-8><title>Redirecting…</title>"
                    f"<script>location.replace({_json.dumps(target)});</script>").encode("utf-8")
            await route.fulfill(status=200, headers={"content-type": "text/html; charset=utf-8",
                                                     **({"set-cookie": out_headers["set-cookie"]} if cookies else {})}, body=body)
            return
        await route.fulfill(status=resp.status_code, headers=out_headers, body=resp.content)
        return
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme in ("http", "https") and _host_is_private(host):
        await route.abort("blockedbyclient")
        return
    await route.continue_()


def _loopback_cdp_url(url: str) -> Optional[str]:
    """Only loopback DevTools endpoints are allowed: a remote one would hand a page's control to another machine."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https", "ws", "wss") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host == "localhost":
        return url
    try:
        return url if ipaddress.ip_address(host).is_loopback else None
    except ValueError:
        return None


def _site_key(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def pick_user_tab(pages: List[Any], start_url: Optional[str]) -> Optional[Any]:
    """An already-open tab on the goal's site (same host, ignoring www.), most recently opened first.

    Only matching tabs are reused: taking over an unrelated tab could throw away what the user is doing there."""
    target = _site_key(start_url or "")
    if not target or _host_is_private(target):
        return None
    for page in reversed(pages):
        try:
            if not page.is_closed() and _site_key(page.url) == target:
                return page
        except Exception:
            continue
    return None


async def _attach_to_user_chrome(pw: Any, slow_mo: int, notes: List[str],
                                 start_url: Optional[str] = None) -> Optional[Tuple[Any, Any, Any, bool, List[Dict[str, str]]]]:
    """Attach to the user's running Chrome (started with --remote-debugging-port).

    Returns (browser, context, page, reused_tab, user_tabs)."""
    url = _loopback_cdp_url(config.CHROME_CDP_URL)
    if url is None:
        logger.warning("Refusing non-loopback CDP endpoint %r; using the WebPilot profile instead.", config.CHROME_CDP_URL)
        notes.append("CDP endpoint is not on this machine (loopback only), so it was not used.")
        return None
    try:
        browser = await pw.chromium.connect_over_cdp(url, timeout=config.CHROME_CDP_TIMEOUT_MS, slow_mo=slow_mo)
    except Exception as exc:  # refused, timed out, or not a DevTools endpoint
        port = urlparse(url).port or 9222
        logger.warning("CDP port %s not active; falling back to persistent local profile (%s: %s). Start Chrome with "
                       "--remote-debugging-port=%s to use your own signed-in browser.", port, type(exc).__name__,
                       str(exc).splitlines()[0][:160] if str(exc) else "", port)
        notes.append(f"Chrome isn't listening for DevTools on port {port}, so WebPilot opened its own saved browser "
                     f"profile (backend/browser_profile). Run start_chrome_debug.bat to use your own Chrome instead.")
        return None
    # The default context is the user's signed-in profile (cookies for every site they're logged into).
    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    existing = list(context.pages)
    user_tabs = []
    for p in existing:
        try:
            user_tabs.append({"url": p.url[:300], "site": _site_key(p.url)})
        except Exception:
            continue
    page = pick_user_tab(existing, start_url)
    reused = page is not None
    if reused:
        try:
            await page.bring_to_front()
        except Exception:
            pass
        notes.append(f"Attached to your running Chrome over CDP ({url}); continuing in your open {_site_key(page.url)} tab "
                     "with your signed-in session.")
    else:
        page = await context.new_page()
        notes.append(f"Attached to your running Chrome over CDP ({url}); none of your {len(existing)} open tab(s) is on "
                     "this site, so WebPilot opened a new tab in your signed-in profile.")
    logger.info("Attached to user Chrome over CDP at %s: %d open tab(s), %s.", url, len(existing),
                "reusing a matching tab" if reused else "opened a new tab")
    return browser, context, page, reused, user_tabs


async def open_session(task_id: str, watch: bool, persistent: bool, start_url: Optional[str] = None) -> AutopilotSession:
    """Launch Chromium: headless by default, headed + slow_mo when watching.

    persistent=True first attaches to the user's real running Chrome over CDP (their tabs, cookies and logins).
    If nothing answers on the CDP port, it never blocks the task: it falls back to a visible (headless=False)
    launch_persistent_context from backend/browser_profile, whether or not watch is on."""
    global _profile_lock
    from playwright.async_api import async_playwright

    if _profile_lock is None:
        _profile_lock = asyncio.Lock()
    notes: List[str] = []
    if persistent:
        from backend import user_chrome

        launch = await user_chrome.ensure_running()  # start the WebPilot Chrome (its own long-lived profile) if needed
        if launch.get("launched") and launch.get("running"):
            notes.append("Started the WebPilot Chrome window (your saved logins live in its profile).")
        pw = await async_playwright().start()
        attached = None
        try:
            attached = await _attach_to_user_chrome(pw, config.AUTOPILOT_SLOW_MO_MS if watch else 0, notes, start_url)
        except Exception as exc:  # e.g. the browser disconnected while opening the tab
            logger.warning("CDP attach failed after connecting (%s); falling back.", type(exc).__name__)
            notes.append("Chrome connection dropped while opening a tab; using WebPilot's own persistent profile instead.")
        if attached is not None:
            browser, context, page, reused, user_tabs = attached
            page.set_default_timeout(15000)
            # Route only the tab WebPilot drives, so the user's other tabs are never intercepted.
            await page.route("**/*", _route_handler)
            return AutopilotSession(task_id, pw, browser, context, page, headed=True, persistent=True,
                                    loop=asyncio.get_running_loop(), notes=notes, connection="cdp",
                                    reused_tab=reused, user_tabs=user_tabs)
        await pw.stop()
        watch = True  # the fallback persistent profile is opened visibly (headless=False) so the user can sign in
    if persistent and _profile_lock.locked():
        notes.append("Persistent profile is in use by another task; using a fresh private profile instead.")
        persistent = False
    pw = await async_playwright().start()
    headless = not watch
    slow_mo = config.AUTOPILOT_SLOW_MO_MS if watch else 0
    browser = None
    try:
        try:
            if persistent:
                await _profile_lock.acquire()
                config.BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                context = await pw.chromium.launch_persistent_context(
                    str(config.BROWSER_PROFILE_DIR), headless=headless, slow_mo=slow_mo, viewport={"width": 1280, "height": 860})
            else:
                browser = await pw.chromium.launch(headless=headless, slow_mo=slow_mo)
                context = await browser.new_context(viewport={"width": 1280, "height": 860})
        except Exception as exc:
            if headless:
                raise
            notes.append(f"Visible browser window could not open ({type(exc).__name__}); continuing headless.")
            headless = True
            if persistent:
                context = await pw.chromium.launch_persistent_context(str(config.BROWSER_PROFILE_DIR), headless=True,
                                                                      viewport={"width": 1280, "height": 860})
            else:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 860})
    except Exception:
        if persistent and _profile_lock.locked():
            _profile_lock.release()
        await pw.stop()
        raise
    context.set_default_timeout(15000)
    await context.route("**/*", _route_handler)
    page = context.pages[0] if context.pages else await context.new_page()
    session = AutopilotSession(task_id, pw, browser, context, page, headed=not headless, persistent=persistent,
                               loop=asyncio.get_running_loop(), notes=notes,
                               connection="persistent_profile" if persistent else "ephemeral", owns_lock=persistent)
    return session


async def close_session(task_id: str) -> None:
    session = SESSIONS.pop(task_id, None)
    if session is not None:
        await session.close()
        if session.owns_lock and _profile_lock is not None and _profile_lock.locked():
            _profile_lock.release()
        if session.conversation_id:  # the conversation is over: its screenshots go with it
            delete_task_screenshots(session.task_ids)


def session_for_task(task_id: str) -> Optional[AutopilotSession]:
    """The live session that ran this task (a conversation tab or the task's own browser)."""
    if task_id in SESSIONS:
        return get_session(task_id)
    for key, session in SESSIONS.items():
        if task_id in session.task_ids:
            return get_session(key)
    return None


def session_key_for_task(task_id: str) -> Optional[str]:
    if task_id in SESSIONS:
        return task_id
    return next((k for k, s in SESSIONS.items() if task_id in s.task_ids), None)


def active_task_ids() -> set:
    out = set(SESSIONS)
    for session in SESSIONS.values():
        out.update(session.task_ids)
    return out


def delete_task_screenshots(task_ids: List[str]) -> int:
    """Delete every screenshot file of these tasks and forget their URLs (evidence text and citations stay)."""
    removed = 0
    if not task_ids:
        return 0
    try:
        files = list(config.ARTIFACTS_DIR.glob("*"))
    except OSError:
        files = []
    prefixes = tuple(f"{t}_" for t in task_ids)
    for f in files:
        if f.is_file() and f.name.startswith(prefixes):
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    from backend import db

    db.clear_evidence_screenshots(list(task_ids))
    for tid in task_ids:
        task = db.get_task(tid)
        data = (task or {}).get("extracted_data") or {}
        if not data:
            continue
        changed = False
        for frame in ((data.get("autopilot") or {}).get("filmstrip") or []):
            for k in ("screenshot_url", "pre_screenshot_url"):
                if frame.get(k):
                    frame[k] = None
                    changed = True
        for c in ((data.get("answer") or {}).get("citations") or []):
            if c.get("screenshot_url"):
                c["screenshot_url"] = None
                changed = True
        if data.get("screenshot_url"):
            data["screenshot_url"] = None
            changed = True
        if changed or removed:
            data["screenshots_deleted"] = True
            db.update_task(tid, extracted_data=data)
    if removed:
        logger.info("Deleted %d screenshot(s) for %d finished task(s).", removed, len(task_ids))
    return removed


async def purge_old_screenshots() -> int:
    """Stand-alone tasks: delete screenshots once the task has been finished for SCREENSHOT_RETENTION_SECONDS."""
    from datetime import datetime, timezone

    from backend import db

    active = active_task_ids()
    stale = []
    now = datetime.now(timezone.utc)
    for task in db.list_tasks(500):
        if task["task_id"] in active or task["status"] in ("PLANNED", "EXECUTING", "AWAITING_APPROVAL", "AWAITING_USER_INPUT"):
            continue
        if (task.get("extracted_data") or {}).get("screenshots_deleted"):
            continue
        try:
            updated = datetime.fromisoformat(task["updated_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if (now - updated).total_seconds() > config.SCREENSHOT_RETENTION_SECONDS:
            stale.append(task["task_id"])
    return delete_task_screenshots(stale) if stale else 0


async def close_all_sessions() -> None:
    for task_id in list(SESSIONS):
        await close_session(task_id)


def get_session(task_id: str) -> Optional[AutopilotSession]:
    session = SESSIONS.get(task_id)
    if session is None:
        return None
    try:
        same_loop = asyncio.get_running_loop() is session.loop
    except RuntimeError:
        same_loop = False
    if session.expired or not same_loop or session.page.is_closed():
        return None
    return session


async def purge_expired_sessions() -> None:
    for task_id, session in list(SESSIONS.items()):
        try:
            gone = session.expired or session.page.is_closed()
        except Exception:
            gone = True
        if gone:
            await close_session(task_id)


# ========================================================================= page helpers

async def settle(page: Any) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=SETTLE_TIMEOUT_MS)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    await asyncio.sleep(0.6)  # let slide-in widgets (calendars, menus) finish animating before re-indexing


async def index_elements(page: Any) -> List[Dict[str, Any]]:
    try:
        return await page.evaluate(INDEXER_JS)
    except Exception as exc:
        logger.warning("DOM indexer failed: %s", exc)
        return []


async def dismiss_consent(page: Any) -> Optional[str]:
    try:
        return await page.evaluate(DISMISS_JS)
    except Exception:
        return None


async def signature(page: Any) -> str:
    try:
        return f"{page.url}|{await page.evaluate(SIGNATURE_JS)}"
    except Exception:
        return page.url


async def screenshot(page: Any, task_id: str, name: str) -> Optional[str]:
    config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.ARTIFACTS_DIR / f"{task_id}_{name}.png"
    try:
        await page.screenshot(path=str(path), full_page=False)
        return artifact_url(path.name)
    except Exception as exc:
        logger.warning("Screenshot failed: %s", exc)
        return None


async def highlight(page: Any, idx: int, label: str) -> bool:
    try:
        return bool(await page.evaluate(HIGHLIGHT_JS, [idx, label]))
    except Exception:
        return False


async def unhighlight(page: Any) -> None:
    try:
        await page.evaluate(UNHIGHLIGHT_JS)
    except Exception:
        pass


def locator_for(page: Any, idx: int) -> Any:
    return page.locator(f'[data-webpilot-idx="{idx}"]').first


CARD_JS = r"""
(idx) => {
  const el = document.querySelector(`[data-webpilot-idx="${idx}"]`);
  if (!el) return false;
  document.querySelectorAll('[data-webpilot-card]').forEach(e => e.removeAttribute('data-webpilot-card'));
  const priceRe = /(₹|Rs\.?|INR|US\$|\$|USD|€|EUR|£|GBP)\s?\d/;
  let n = el, best = el;
  for (let k = 0; k < 7 && n.parentElement; k++) {
    n = n.parentElement;
    const r = n.getBoundingClientRect();
    if (r.width > window.innerWidth * 0.95 || r.height > 900) break;
    best = n;
    if (priceRe.test(n.innerText || '') && r.height >= 80) break;
  }
  best.setAttribute('data-webpilot-card', '1');
  best.scrollIntoView({block: 'center'});
  return true;
}
"""


async def card_screenshot(page: Any, task_id: str, idx: int, name: str) -> Optional[str]:
    """Screenshot of the result card around element idx (so the user sees each option, not the whole page)."""
    config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.ARTIFACTS_DIR / f"{task_id}_{name}.png"
    try:
        if not await page.evaluate(CARD_JS, idx):
            return None
        await page.locator('[data-webpilot-card="1"]').first.screenshot(path=str(path), timeout=5000)
        return artifact_url(path.name)
    except Exception as exc:
        logger.debug("Card screenshot failed: %s", exc)
        return None


async def distill(page: Any) -> Dict[str, Any]:
    html = await page.content()
    return sanitize_html_and_extract(html, page.url)


def page_cards(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generic result cards: main-content links with a meaningful label, plus any nearby price."""
    from backend.firewall import _PRICE_RE

    from backend.llm_brain import AD_RE

    cards, seen = [], set()
    for el in elements:
        if not (el.get("tag") == "a" or el.get("role") == "link") or el.get("landmark") in ("nav", "header", "footer"):
            continue
        if AD_RE.search(el.get("accessible_name") or "") or AD_RE.search((el.get("container_text") or "")[:80]):
            continue  # sponsored / ad results are never offered as options
        label = (el.get("accessible_name") or el.get("text") or "").strip()
        href = el.get("href") or ""
        if len(label) < 8 or not href or href in seen:
            continue
        seen.add(href)
        amount = None
        m = _PRICE_RE.search(el.get("price_text") or "")
        if m:
            try:
                amount = float(m.group("amt").replace(",", ""))
            except ValueError:
                amount = None
        cards.append({"title": label[:240], "url": href, "points": None, "rank": None,
                      "selector": f'[data-webpilot-idx="{el["idx"]}"]', "price": el.get("price_text") or None,
                      "price_amount": amount})
    return cards[:60]


def page_heading(distilled: Dict[str, Any], page_title: str) -> str:
    for h in distilled.get("headings") or []:
        if h.get("level") == 1 and h.get("text"):
            return h["text"][:200]
    return (distilled.get("headings") or [{}])[0].get("text") or page_title


def consequential_params(page_url: str, page_title: str, distilled: Dict[str, Any], element: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical, re-computable description of a consequential click (hashed by the approval guard)."""
    from backend.llm_brain import element_label

    p = urlparse(page_url)
    prices = sorted({round(float(x["amount"]), 2) for x in (distilled.get("prices") or [])[:12] if x.get("amount") is not None})
    currency = next((x["currency"] for x in (distilled.get("prices") or []) if x.get("currency")), None)
    total = max(prices) if prices else None
    return {
        "action": "LIVE_CONSEQUENTIAL_CLICK",
        "domain": p.netloc,
        "page_url": f"{p.scheme}://{p.netloc}{p.path}" + (f"?{p.query}" if p.query else ""),
        "page_title": page_title[:200],
        "item_heading": page_heading(distilled, page_title),
        "button_text": element_label(element)[:120],
        "consequence_tier": element.get("consequence_tier") or "commit",
        "visible_prices": prices[:8],
        "currency": currency,
        "display_total": total,
        "total_price_inr": int(total) if (total is not None and currency == "INR") else None,
        "item_name": page_heading(distilled, page_title),
        "seller": p.netloc,
        "environment": "CONTROLLED_SANDBOX" if browser_runtime.is_sandbox_url(page_url) else "LIVE_WEB",
    }


def find_element_by_label(elements: List[Dict[str, Any]], label: str) -> Optional[Dict[str, Any]]:
    from backend.llm_brain import annotate_elements, element_label

    target = re.sub(r"\s+", " ", label).strip().lower()
    for el in annotate_elements(elements):
        if re.sub(r"\s+", " ", element_label(el)).strip().lower()[:120] == target:
            return el
    return None


async def resolve_start(goal: str, start_url: Optional[str]) -> Tuple[str, bool]:
    """Return (url, used_discovery). Explicit URLs win; otherwise the configured web search engine."""
    from backend.llm_brain import parse_goal_intents

    url = start_url or parse_goal_intents(goal)["explicit_url"]
    if url:
        url = browser_runtime.normalize_url(url)
        if not browser_runtime.is_sandbox_url(url):
            await browser_runtime._assert_public_destination(url)
        return url, False
    return browser_runtime.normalize_url(config.AUTOPILOT_SEARCH_URL), True


def discovery_host() -> str:
    return (urlparse(browser_runtime.normalize_url(config.AUTOPILOT_SEARCH_URL)).hostname or "").lower()


def payment_landing(elements: List[Dict[str, Any]], distilled: Dict[str, Any]) -> Dict[str, Any]:
    """Generic post-click classification: payment step reached? confirmation shown?"""
    from backend.llm_brain import annotate_elements, element_label

    ann = annotate_elements(elements)
    final_buttons = [element_label(e) for e in ann if e.get("consequence_tier") == "final_payment"]
    text = (distilled.get("sanitized_text") or "").lower()
    payment_inputs = any(re.search(r"card|cvv|upi|expiry|security code", (e.get("accessible_name") or "") + " " +
                                   (e.get("placeholder") or ""), re.I) for e in ann if e.get("tag") in ("input", "textarea"))
    return {
        # The word "payment" alone (e.g. a "Continue to payment" button) is not the payment step: it needs a
        # pay control, card/UPI fields or an explicit payment-method chooser.
        "payment_step": bool(final_buttons or payment_inputs or re.search(
            r"\b(?:select|choose) (?:a |your )?payment (?:method|option)|\bpayment options\b|\bpay with\b", text)),
        "final_payment_controls": final_buttons[:5],
        "confirmation": bool(re.search(r"\b(thank you|submitted|confirmation|confirmed|success(fully)?|received|booked)\b", text)),
    }
