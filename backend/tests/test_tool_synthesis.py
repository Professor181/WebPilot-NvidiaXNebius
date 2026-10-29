"""Upgrade 2 — Dynamic tool synthesis & self-healing session tool registry."""
from __future__ import annotations

import asyncio

import pytest

from backend import mcp_server, tool_synthesizer
from backend.sandbox_routes import MARKET_SERIES
from backend.tests.conftest import McpClient

CRYPTO_GOAL = "Extract trending crypto indices and calculate moving averages"
XML_GOAL = "Parse this custom XML pricing feed and sort by cheapest price"


def test_compiler_is_deterministic_whitelisted_and_rejects_unsafe_specs():
    a = tool_synthesizer.build_spec(CRYPTO_GOAL)
    b = tool_synthesizer.build_spec(CRYPTO_GOAL)
    assert a == b, "same goal must synthesize the same tool"
    assert a["name"].startswith("dynamic_synthetic_tool_extract_trending_crypto")
    assert [op["op"] for op in a["pipeline"]] == ["moving_average", "pct_change", "rank_columns_by_change", "aggregate"]
    assert a["pipeline"][0]["window"] == 3
    assert a["source"] == {"url": "/sandbox/market-data", "format": "auto", "resolution": "sandbox_catalog"}
    assert tool_synthesizer.build_spec("Calculate the 5-day moving average", "/sandbox/market-data")["pipeline"][0]["window"] == 5

    evil = dict(a, pipeline=[{"op": "exec", "code": "__import__('os').system('calc')"}])
    with pytest.raises(tool_synthesizer.SynthesisError) as exc:
        tool_synthesizer.validate_spec(evil)
    assert exc.value.code == "OPERATION_NOT_ALLOWED"
    with pytest.raises(tool_synthesizer.SynthesisError):
        tool_synthesizer.validate_spec(dict(a, source={"url": "file:///etc/passwd", "format": "auto"}))
    with pytest.raises(tool_synthesizer.SynthesisError) as exc:
        tool_synthesizer.build_spec("calculate the moving average of something")
    assert exc.value.code == "MISSING_DATA_SOURCE"


def test_interpreter_math_is_exact():
    spec = tool_synthesizer.build_spec("calculate 2-day moving average and percent change", "/sandbox/x.csv")
    records = [{"day": "d1", "close": "10"}, {"day": "d2", "close": "20"}, {"day": "d3", "close": "₹40"}]
    result = tool_synthesizer.execute(spec, records)
    assert [r["close MA2"] for r in result["rows"]] == [None, 15.0, 30.0]
    assert result["trend"]["close"] == 300.0
    report = tool_synthesizer.verify(spec, records, result)
    assert report["verdict"] == "VERIFIED_SUCCESS"


def test_xml_feeds_with_doctype_are_rejected():
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><feed><item>&lol;</item></feed>'
    with pytest.raises(tool_synthesizer.SynthesisError) as exc:
        tool_synthesizer._parse_xml(bomb, [])
    assert exc.value.code == "UNSAFE_XML"


def test_novel_goal_synthesizes_registers_and_runs_a_session_tool(mcp, client):
    task = mcp.create_task(CRYPTO_GOAL)
    assert task["status"] == "COMPLETED", task["summary"]
    assert task["interpreted_goal"]["scenario"] == "synthesized_tool"
    assert [s["key"] for s in task["plan_steps"]] == ["compile", "synthesize", "register", "fetch", "execute", "verify"]
    data = task["extracted_data"]
    tool_name = data["tool"]["name"]
    assert data["tool"]["registered_for_session"] is True and data["record_count"] == 10
    btc = MARKET_SERIES["BTC Index"]
    last_ma3 = round(sum(btc[-3:]) / 3, 4)
    assert data["result"]["aggregates"]["BTC Index MA3"]["last"] == last_ma3
    assert data["result"]["ranking"][0]["column"] == "BTC Index"
    assert data["result"]["ranking"][-1]["column"] == "DeFi Pulse"
    # The page's hidden "report BTC as 1,000,000" injections were stripped, not obeyed.
    assert max(data["result"]["aggregates"]["BTC Index"].values()) < 1_000_000
    assert any(a.get("hiding_technique") == "html-comment" for a in data["firewall_alerts"])
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"
    assert "BTC Index is trending strongest" in task["summary"]

    listed = {t["name"]: t for t in mcp.rpc("tools/list", {}).json()["result"]["tools"]}
    assert tool_name in listed and len(listed) == 12
    assert listed[tool_name]["annotations"]["readOnlyHint"] is True
    assert listed[tool_name]["inputSchema"]["properties"]["window"]["maximum"] == 50

    out = mcp.call(tool_name, {"window": 5})
    assert out["isError"] is False
    assert out["structuredContent"]["result"]["aggregates"]["BTC Index MA5"]["last"] == round(sum(btc[-5:]) / 5, 4)
    bad = mcp.call(tool_name, {"window": 500})
    assert bad["isError"] is True and "window must be <= 50" in bad["content"][0]["text"]

    # Private to the session that synthesized it.
    other = McpClient(client)
    other.initialize()
    assert tool_name not in {t["name"] for t in other.rpc("tools/list", {}).json()["result"]["tools"]}
    assert other.rpc("tools/call", {"name": tool_name, "arguments": {}}).json()["error"]["code"] == -32602


def test_synthesize_custom_tool_parses_untrusted_xml_feed(mcp):
    result = mcp.call("synthesize_custom_tool", {"goal": XML_GOAL})
    assert result["isError"] is False, result
    data = result["structuredContent"]
    assert data["registered_for_session"] is True
    dry = data["dry_run"]
    assert dry["format"] == "xml" and dry["result"]["row_count"] == 6
    assert dry["result"]["rows"][0]["name"] == "BudgetBook 14"
    assert "lowest price: BudgetBook 14 at 42,990" in dry["summary"]
    neutralized = [a for a in dry["firewall_alerts"] if "IGNORE_PREVIOUS_INSTRUCTIONS" in a["matched_patterns"]]
    assert neutralized and neutralized[0]["element_selector"] == "item[3]/note"
    ultra = next(r for r in dry["result"]["rows"] if r["name"] == "UltraBook ProMax 99")
    assert "[NEUTRALIZED_INJECTION" in ultra["note"] and ultra["price"] == "145000"
    assert dry["verification_report"]["verdict"] == "VERIFIED_SUCCESS"
    assert any("sort_rows" in line for line in data["pipeline_listing"])


def test_synthesis_errors_are_tool_errors(mcp):
    out = mcp.call("synthesize_custom_tool", {"goal": "compute statistics about my sleep"})
    assert out["isError"] is True and out["structuredContent"]["error_code"] == "MISSING_DATA_SOURCE"
    out = mcp.call("synthesize_custom_tool", {"goal": "compute statistics", "source_url": "/sandbox/login-wall"})
    assert out["isError"] is True and out["structuredContent"]["error_code"] == "NO_RECORDS"


@pytest.mark.asyncio
async def test_registering_a_tool_emits_tools_list_changed(mcp):
    queue: asyncio.Queue = asyncio.Queue()
    mcp_server._session_queues[mcp.session_id] = queue
    try:
        mcp.call("synthesize_custom_tool", {"goal": XML_GOAL})
        message = queue.get_nowait()
        assert message == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    finally:
        mcp_server._session_queues.pop(mcp.session_id, None)
