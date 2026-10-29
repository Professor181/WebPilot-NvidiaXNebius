"""Persistent mode attaches to the user's own running Chrome over CDP, and falls back to the WebPilot profile."""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from backend import config, live_autopilot


class FakePage:
    def __init__(self, name, url="about:blank"):
        self.name, self.url, self.closed, self.routes, self.front = name, url, False, [], False

    async def bring_to_front(self):
        self.front = True

    async def unroute(self, pattern, handler=None):
        self.routes.remove(pattern)

    def set_default_timeout(self, ms):
        pass

    async def route(self, pattern, handler):
        self.routes.append(pattern)

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, pages):
        self.pages, self.closed, self.routes = list(pages), False, []

    async def new_page(self):
        page = FakePage(f"tab{len(self.pages)}")
        self.pages.append(page)
        return page

    def set_default_timeout(self, ms):
        pass

    async def route(self, pattern, handler):
        self.routes.append(pattern)

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.contexts, self.closed = [context], False

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, cdp_ok=True):
        self.cdp_ok = cdp_ok
        self.user_context = FakeContext([FakePage("user-gmail", "https://mail.google.com/mail/u/0/#inbox"),
                                         FakePage("user-github", "https://github.com/notifications")])
        self.cdp_calls, self.persistent_calls = [], []

    async def connect_over_cdp(self, url, **kw):
        self.cdp_calls.append((url, kw))
        if not self.cdp_ok:
            raise ConnectionRefusedError("connect ECONNREFUSED 127.0.0.1:9222")
        return FakeBrowser(self.user_context)

    async def launch_persistent_context(self, user_data_dir, **kw):
        self.persistent_calls.append((user_data_dir, kw))
        return FakeContext([FakePage("profile-tab")])


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium, self.stopped = chromium, False

    async def start(self):
        return self

    async def stop(self):
        self.stopped = True


@pytest.fixture
def fake_pw(monkeypatch):
    holder = {}

    def install(cdp_ok=True):
        pw = FakePlaywright(FakeChromium(cdp_ok))
        holder["pw"] = pw
        import playwright.async_api as api

        monkeypatch.setattr(api, "async_playwright", lambda: pw)
        return pw
    yield install
    live_autopilot._profile_lock = None


@pytest.mark.asyncio
async def test_persistent_mode_attaches_to_running_chrome_in_a_new_tab(fake_pw):
    pw = fake_pw(cdp_ok=True)
    session = await live_autopilot.open_session("t-cdp", watch=False, persistent=True)
    assert pw.chromium.cdp_calls[0][0] == "http://localhost:9222"
    assert session.connection == "cdp" and session.headed and session.persistent
    ctx = pw.chromium.user_context
    assert session.context is ctx and session.page is ctx.pages[-1] and len(ctx.pages) == 3  # new tab, user tabs kept
    assert session.page.routes == ["**/*"] and ctx.routes == []  # only our tab is intercepted
    assert not pw.chromium.persistent_calls
    assert any("Attached to your running Chrome" in n and "opened a new tab" in n for n in session.notes)
    assert session.reused_tab is False and [t["site"] for t in session.user_tabs] == ["mail.google.com", "github.com"]

    live_autopilot.SESSIONS["t-cdp"] = session
    await live_autopilot.close_session("t-cdp")
    assert session.page.closed is True
    assert [p.closed for p in ctx.pages[:2]] == [False, False]  # the user's own tabs survive
    assert ctx.closed is False and session.browser.closed is False and pw.stopped is True


@pytest.mark.asyncio
async def test_cdp_failure_falls_back_to_headed_persistent_profile(fake_pw, caplog):
    pw = fake_pw(cdp_ok=False)
    with caplog.at_level("WARNING", logger="webpilot.autopilot"):
        session = await live_autopilot.open_session("t-fb", watch=False, persistent=True)
    assert any("remote-debugging-port=9222" in r.getMessage() for r in caplog.records)
    user_data_dir, kw = pw.chromium.persistent_calls[0]
    assert user_data_dir == str(config.BROWSER_PROFILE_DIR) and kw["headless"] is False
    assert session.connection == "persistent_profile" and session.headed and session.owns_lock
    assert live_autopilot._profile_lock.locked()
    live_autopilot.SESSIONS["t-fb"] = session
    await live_autopilot.close_session("t-fb")
    assert session.context.closed is True and not live_autopilot._profile_lock.locked()


@pytest.mark.asyncio
async def test_non_loopback_cdp_endpoints_are_never_used(fake_pw, monkeypatch):
    monkeypatch.setattr(config, "CHROME_CDP_URL", "http://203.0.113.7:9222")
    pw = fake_pw(cdp_ok=True)
    session = await live_autopilot.open_session("t-remote", watch=False, persistent=True)
    assert pw.chromium.cdp_calls == [] and session.connection == "persistent_profile"
    assert live_autopilot._loopback_cdp_url("http://127.0.0.1:9222") and live_autopilot._loopback_cdp_url("ws://[::1]:9222/x")
    assert live_autopilot._loopback_cdp_url("http://localhost.evil.com:9222") is None
    await session.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_cdp_attach_runs_the_autopilot_in_the_users_browser(mcp, monkeypatch):
    """Start a separate Chromium with --remote-debugging-port (as a user would start Chrome) and attach to it."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
    except Exception:
        pytest.skip("Playwright Chromium not installed")
    port = _free_port()
    proc = subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={tempfile.mkdtemp()}",
                             "--headless=new", "--no-first-run", "--no-default-browser-check", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            pytest.skip("Chromium remote-debugging endpoint did not come up")
        monkeypatch.setattr(config, "CHROME_CDP_URL", f"http://127.0.0.1:{port}")
        result = mcp.call("run_live_web_autopilot", {
            "goal": "Go to Paperlane, find the book Designing Data-Intensive Applications, put it in my tote and check out "
                    "after my approval", "start_url": "/sandbox/paperlane", "max_steps": 8, "use_persistent_profile": True})
        task = result["structuredContent"]["task"]
        assert task["status"] == "AWAITING_APPROVAL", task["summary"]
        assert task["extracted_data"]["autopilot"]["mode"]["connection"] == "cdp"
        launch = next(s for s in task["plan_steps"] if s["key"] == "launch")
        assert "attached to the WebPilot Chrome over CDP" in launch["detail"]
        session = live_autopilot.SESSIONS[task["task_id"]]
        tabs = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=3).json()
        assert any("paperlane" in t.get("url", "") for t in tabs if t.get("type") == "page")
        assert any(t.get("url") == "about:blank" for t in tabs if t.get("type") == "page")  # user's tab untouched
        mcp.call("cancel_web_task", {"task_id": task["task_id"]})
        mcp.call("get_task_status", {"task_id": task["task_id"]})
        assert task["task_id"] not in live_autopilot.SESSIONS and session.connection == "cdp"
        # The user's browser is still alive after WebPilot disconnects.
        time.sleep(0.5)
        assert proc.poll() is None
        assert httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=3).status_code == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_an_open_tab_on_the_goal_site_is_reused_and_handed_back(fake_pw):
    pw = fake_pw(cdp_ok=True)
    ctx = pw.chromium.user_context
    session = await live_autopilot.open_session("t-reuse", watch=False, persistent=True,
                                                start_url="https://www.github.com/search?q=mcp")
    github_tab = ctx.pages[1]
    assert session.reused_tab is True and session.page is github_tab and github_tab.front is True
    assert len(ctx.pages) == 2 and github_tab.routes == ["**/*"] and ctx.pages[0].routes == []
    assert any("continuing in your open github.com tab" in n for n in session.notes)
    live_autopilot.SESSIONS["t-reuse"] = session
    await live_autopilot.close_session("t-reuse")
    assert github_tab.closed is False and github_tab.routes == []  # the user's tab stays open, our hooks removed
    assert ctx.closed is False and pw.stopped is True


def test_only_tabs_on_the_goal_site_are_candidates():
    tabs = [FakePage("a", "https://chatgpt.com/c/123"), FakePage("b", "https://www.amazon.in/gp/cart"),
            FakePage("c", "https://amazon.in/dp/B01")]
    assert live_autopilot.pick_user_tab(tabs, "https://amazon.in").name == "c"  # most recent matching tab
    assert live_autopilot.pick_user_tab(tabs, "https://leetcode.com/problems") is None
    assert live_autopilot.pick_user_tab(tabs, None) is None
    assert live_autopilot.pick_user_tab([FakePage("lan", "http://192.168.1.1/admin")], "http://192.168.1.1/") is None


_CHROME_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
    "/usr/bin/google-chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def test_real_cdp_attach_continues_in_the_users_open_tab(mcp, monkeypatch):
    # Uses the user's installed Google Chrome (a throwaway profile, headless). Playwright's bundled Chromium build
    # never finishes attaching to a tab that was already showing an https page before the connection, so it can't
    # stand in for a real browser here; installed Chrome attaches normally.
    exe = next((c for c in _CHROME_CANDIDATES if c and Path(c).exists()), None)
    if exe is None:
        pytest.skip("Google Chrome is not installed")
    try:
        httpx.get("https://example.com/", timeout=6)
    except Exception:
        pytest.skip("No internet access to example.com")
    port = _free_port()
    proc = subprocess.Popen([exe, f"--remote-debugging-port={port}", f"--user-data-dir={tempfile.mkdtemp()}",
                             "--headless=new", "--no-first-run", "--no-default-browser-check", "https://example.com/"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                tabs = httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=1).json()
                if any("example.com" in t.get("url", "") for t in tabs):
                    break
            except Exception:
                pass
            time.sleep(0.3)
        else:
            pytest.skip("Chromium did not open the example.com tab")
        monkeypatch.setattr(config, "CHROME_CDP_URL", f"http://127.0.0.1:{port}")
        task = mcp.call("run_live_web_autopilot", {"goal": "Go to example.com and summarize the page", "max_steps": 3,
                                                   "use_persistent_profile": True})["structuredContent"]["task"]
        mode = task["extracted_data"]["autopilot"]["mode"]
        assert mode["connection"] == "cdp" and mode["reused_tab"] is True and mode["open_tabs"] >= 1
        assert task["extracted_data"]["autopilot"]["filmstrip"][0]["description"].startswith("Continued in your open tab")
        assert task["status"] in ("COMPLETED", "UNVERIFIED_OUTCOME"), task["summary"]
        time.sleep(0.5)
        pages = [t for t in httpx.get(f"http://127.0.0.1:{port}/json/list", timeout=3).json() if t.get("type") == "page"]
        assert len(pages) == 1 and "example.com" in pages[0]["url"]  # same tab, still open, no extra tabs
        assert proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ------------------------------------------- Watch Live Browser + Persistent Profile: never blocks the user

@pytest.mark.asyncio
async def test_watch_plus_persistent_falls_back_to_the_local_profile_when_port_9222_is_closed(fake_pw, caplog):
    pw = fake_pw(cdp_ok=False)
    with caplog.at_level("WARNING", logger="webpilot.autopilot"):
        session = await live_autopilot.open_session("t-watch", watch=True, persistent=True)
    assert any("CDP port 9222 not active; falling back to persistent local profile" in r.getMessage() for r in caplog.records)
    assert pw.chromium.cdp_calls and pw.chromium.cdp_calls[0][0] == "http://localhost:9222"
    user_data_dir, kw = pw.chromium.persistent_calls[0]
    assert user_data_dir == str(config.BROWSER_PROFILE_DIR) and kw["headless"] is False
    assert kw["slow_mo"] == config.AUTOPILOT_SLOW_MO_MS  # watching keeps slow motion
    assert session.connection == "persistent_profile" and session.headed is True
    assert any("WebPilot opened its own saved browser profile" in n for n in session.notes)  # shown on the launch step
    live_autopilot.SESSIONS["t-watch"] = session
    await live_autopilot.close_session("t-watch")
    assert session.context.closed is True and not live_autopilot._profile_lock.locked()


@pytest.mark.asyncio
async def test_a_cdp_timeout_also_falls_back(fake_pw):
    pw = fake_pw(cdp_ok=True)

    async def slow(url, **kw):
        raise TimeoutError("BrowserType.connect_over_cdp: Timeout 15000ms exceeded.")
    pw.chromium.connect_over_cdp = slow
    session = await live_autopilot.open_session("t-timeout", watch=True, persistent=True)
    assert session.connection == "persistent_profile" and pw.chromium.persistent_calls[0][1]["headless"] is False
    await session.close()


@pytest.mark.asyncio
async def test_non_loopback_endpoint_with_watch_falls_back_without_connecting(fake_pw, monkeypatch):
    monkeypatch.setattr(config, "CHROME_CDP_URL", "http://203.0.113.7:9222")
    pw = fake_pw(cdp_ok=True)
    session = await live_autopilot.open_session("t-remote2", watch=True, persistent=True)
    assert pw.chromium.cdp_calls == [] and session.connection == "persistent_profile"
    await session.close()


@pytest.mark.asyncio
async def test_watch_plus_persistent_attaches_when_chrome_is_listening(fake_pw):
    pw = fake_pw(cdp_ok=True)
    session = await live_autopilot.open_session("t-watch-ok", watch=True, persistent=True)
    assert session.connection == "cdp" and pw.chromium.persistent_calls == []
    await session.close()
