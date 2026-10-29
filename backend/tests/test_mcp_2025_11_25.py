"""MCP 2025-11-25 Streamable HTTP protocol conformance tests."""
from __future__ import annotations

import pytest

from backend.tests.conftest import MCP_HEADERS, McpClient

EXPECTED_TOOLS = {
    "create_web_task", "get_task_status", "inspect_website", "request_user_approval",
    "execute_approved_step", "verify_task_result", "get_execution_evidence", "cancel_web_task",
    "interrupt_web_task", "synthesize_custom_tool", "run_live_web_autopilot",
}


def test_initialize_returns_protocol_version_server_info_and_session_id(client):
    m = McpClient(client)
    r = m.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                             "clientInfo": {"name": "judge", "version": "1"}},
              headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    result = body["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"]["tools"] == {"listChanged": True}  # synthesized tools change the list
    info = result["serverInfo"]
    assert info["name"] == "webpilot-alexa-plus"
    assert info["version"] and info["title"]
    assert "approval" in info["description"].lower()
    assert info["icons"][0]["mimeType"] == "image/svg+xml"
    session_id = r.headers.get("mcp-session-id")
    assert session_id and len(session_id) >= 16 and session_id.isascii()
    # CORS must expose the session header so the browser-based Alexa+ web client can read it.
    assert "mcp-session-id" in r.headers.get("access-control-expose-headers", "").lower()
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # The session id is echoed on subsequent responses.
    m.session_id = session_id
    ping = m.rpc("ping")
    assert ping.status_code == 200
    assert ping.json()["result"] == {}
    assert ping.headers["mcp-session-id"] == session_id


def test_cors_preflight_allows_session_headers(client):
    r = client.options("/mcp", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,mcp-session-id,mcp-protocol-version",
    })
    assert r.status_code == 200
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "mcp-session-id" in allowed and "mcp-protocol-version" in allowed


def test_notification_returns_202_with_empty_body(mcp):
    r = mcp.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                      headers={**MCP_HEADERS, "Mcp-Session-Id": mcp.session_id})
    assert r.status_code == 202
    assert r.content == b""
    assert r.headers.get("content-length") == "0"

    # Other notifications (no id) behave the same way.
    r2 = mcp.http.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/cancelled",
                                     "params": {"requestId": 99, "reason": "user"}},
                       headers={**MCP_HEADERS, "Mcp-Session-Id": mcp.session_id})
    assert r2.status_code == 202 and r2.content == b""


@pytest.mark.parametrize("origin", ["http://evil.attacker.com", "https://amazon.com.evil.io", "null", "file://"])
def test_untrusted_origin_is_rejected_with_403(client, origin):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                             "clientInfo": {"name": "x", "version": "1"}}},
                    headers={**MCP_HEADERS, "Origin": origin})
    assert r.status_code == 403
    assert "mcp-session-id" not in r.headers
    assert r.json()["error"]["message"].startswith("Forbidden")


@pytest.mark.parametrize("origin", [None, "http://localhost:3000", "http://127.0.0.1:8000",
                                    "https://alexa.amazon.com", "https://developer.amazon.com",
                                    "https://pitangui.alexa.com"])
def test_trusted_origins_are_accepted(client, origin):
    headers = dict(MCP_HEADERS)
    if origin:
        headers["Origin"] = origin
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "ping"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["result"] == {}


def test_tools_list_returns_all_tools_with_schemas_annotations_and_icons(mcp):
    r = mcp.rpc("tools/list", {})
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert {t["name"] for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 11
    for t in tools:
        schema = t["inputSchema"]
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert t["description"] and t["title"]
        ann = t["annotations"]
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert isinstance(ann[hint], bool), (t["name"], hint)
        assert t["icons"][0]["src"].startswith("data:image/svg+xml;base64,")
    by_name = {t["name"]: t for t in tools}
    assert by_name["execute_approved_step"]["annotations"]["destructiveHint"] is True
    assert by_name["get_task_status"]["annotations"]["readOnlyHint"] is True
    assert by_name["execute_approved_step"]["inputSchema"]["required"] == ["task_id", "approval_id", "approval_token"]


def test_missing_required_arguments_return_tool_execution_error_sep_1303(mcp):
    r = mcp.rpc("tools/call", {"name": "create_web_task", "arguments": {}})
    assert r.status_code == 200
    body = r.json()
    assert "error" not in body, "validation failures must not be JSON-RPC protocol errors"
    result = body["result"]
    assert result["isError"] is True
    assert result["content"][0]["type"] == "text"
    assert "goal is required" in result["content"][0]["text"]
    assert result["structuredContent"]["error_code"] == "INVALID_ARGUMENTS"

    bad_types = mcp.call("execute_approved_step", {"task_id": 42, "approval_id": "nope", "extra": True})
    assert bad_types["isError"] is True
    errors = bad_types["structuredContent"]["details"]["validation_errors"]
    assert any("task_id must be of type string" in e for e in errors)
    assert any("approval_token is required" in e for e in errors)
    assert any("extra is not an allowed property" in e for e in errors)


def test_unknown_tool_and_method_are_protocol_errors(mcp):
    r = mcp.rpc("tools/call", {"name": "buy_everything", "arguments": {}})
    assert r.json()["error"]["code"] == -32602
    r2 = mcp.rpc("completion/complete", {})
    assert r2.json()["error"]["code"] == -32601


def test_malformed_messages_and_batches_are_rejected(client):
    parse = client.post("/mcp", content=b"{not json", headers=MCP_HEADERS)
    assert parse.status_code == 400 and parse.json()["error"]["code"] == -32700
    batch = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=MCP_HEADERS)
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600
    bad_version = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                              headers={**MCP_HEADERS, "MCP-Protocol-Version": "1999-01-01"})
    assert bad_version.status_code == 400


def test_session_lifecycle_unknown_session_404_and_delete(mcp):
    sid = mcp.session_id
    unknown = mcp.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                            headers={**MCP_HEADERS, "Mcp-Session-Id": "deadbeef" * 4})
    assert unknown.status_code == 404

    status = mcp.http.get("/mcp", headers={"Mcp-Session-Id": sid, "Accept": "application/json"})
    assert status.status_code == 200
    assert status.json()["protocolVersion"] == "2025-11-25"
    assert len(status.json()["tools"]) == 11

    deleted = mcp.http.delete("/mcp", headers={"Mcp-Session-Id": sid})
    assert deleted.status_code == 204
    after = mcp.rpc("ping")
    assert after.status_code == 404
    assert mcp.http.delete("/mcp", headers={"Mcp-Session-Id": sid}).status_code == 404


def test_sse_only_clients_receive_event_stream_response(mcp):
    r = mcp.rpc("ping", headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert '"result": {}' in r.text and r.text.startswith("id: ")


def test_every_frame_is_logged_for_the_live_inspector(mcp):
    mcp.rpc("tools/list", {})
    mcp.call("get_task_status", {"task_id": "task_000000000000"})
    logs = mcp.http.get("/api/mcp-logs").json()["logs"]
    methods = [entry["method"] for entry in logs]
    assert "initialize" in methods and "notifications/initialized" in methods and "tools/list" in methods
    call = next(entry for entry in logs if entry["method"] == "tools/call")
    assert call["tool_name"] == "get_task_status"
    assert call["response"]["result"]["isError"] is True
    assert call["response"]["result"]["structuredContent"]["error_code"] == "TASK_NOT_FOUND"
    assert all(entry["latency_ms"] >= 0 for entry in logs)
    assert logs[0]["log_id"] > logs[-1]["log_id"], "newest first"
