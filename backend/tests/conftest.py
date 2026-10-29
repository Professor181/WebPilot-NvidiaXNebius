"""Shared fixtures: isolated SQLite DB + artifacts dir, a TestClient and a small MCP client helper."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_TMP = Path(tempfile.mkdtemp(prefix="webpilot-tests-"))
os.environ["WEBPILOT_DB_PATH"] = str(_TMP / "webpilot-test.db")
os.environ["WEBPILOT_ARTIFACTS_DIR"] = str(_TMP / "artifacts")
# Deterministic, fast tests: exercise the HTTP/BS4 + SVG evidence path. Playwright is covered when
# the server runs normally.
os.environ.setdefault("WEBPILOT_ENABLE_PLAYWRIGHT", "false")
# Deterministic brain even if Ollama / AWS / LLM keys exist on the machine; provider tests opt in explicitly.
os.environ["WEBPILOT_BRAIN_PROVIDER"] = "deterministic"
os.environ["OLLAMA_MODEL"] = ""  # a local .env may pin a model; provider tests pick one via /api/tags
os.environ["WEBPILOT_CHROME_AUTOLAUNCH"] = "false"  # tests never start the user's Chrome
os.environ.setdefault("WEBPILOT_CHROME_PROFILE_DIR", os.path.join(tempfile.gettempdir(), "webpilot-test-chrome-profile"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import config  # noqa: E402
from backend.main import app  # noqa: E402

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class McpClient:
    def __init__(self, client: TestClient):
        self.http = client
        self.session_id: Optional[str] = None
        self._next_id = 1

    def rpc(self, method: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            msg["params"] = params
        h = dict(MCP_HEADERS)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
            h["MCP-Protocol-Version"] = "2025-11-25"
        h.update(headers or {})
        return self.http.post("/mcp", json=msg, headers=h)

    def initialize(self):
        r = self.rpc("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "webpilot-pytest", "version": "1.0.0"},
        })
        assert r.status_code == 200, r.text
        self.session_id = r.headers["mcp-session-id"]
        n = self.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                           headers={**MCP_HEADERS, "Mcp-Session-Id": self.session_id})
        assert n.status_code == 202
        return r

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "result" in body, body
        return body["result"]

    def create_task(self, goal: str, **kwargs: Any) -> Dict[str, Any]:
        result = self.call("create_web_task", {"goal": goal, **kwargs})
        assert result["isError"] is False, result
        return result["structuredContent"]["task"]


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_demo_state(client):
    r = client.post("/api/reset-demo", json={"include_mcp_logs": True, "include_sessions": True})
    assert r.status_code == 200
    yield


@pytest.fixture()
def mcp(client) -> McpClient:
    m = McpClient(client)
    m.initialize()
    return m


def artifact_path(url: str) -> Path:
    return config.ARTIFACTS_DIR / url.rsplit("/", 1)[-1]


def approve(client: TestClient, approval_id: str) -> Dict[str, Any]:
    r = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "APPROVE", "decided_by": "pytest-user"},
                    headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- an in-memory OS credential store for tests
import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        self.store.pop((service, username), None)


keyring.set_keyring(MemoryKeyring())
