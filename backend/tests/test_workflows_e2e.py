"""End-to-end scenario workflows driven through the MCP endpoint and REST API."""
from __future__ import annotations

import re

import httpx
import pytest

from backend import planner_engine
from backend.browser_runtime import BrowserRuntime, browser_runtime
from backend.planner_engine import compile_goal
from backend.tests.conftest import approve, artifact_path


def _steps_by_status(task, status):
    return [s for s in task["plan_steps"] if s["status"] == status]


def _assert_evidence_files_exist(mcp, task_id, minimum):
    ev = mcp.call("get_execution_evidence", {"task_id": task_id})["structuredContent"]
    assert ev["count"] >= minimum
    for record in ev["evidence"]:
        if record["screenshot_url"]:
            path = artifact_path(record["screenshot_url"])
            assert path.exists() and path.stat().st_size > 500, record["screenshot_url"]
    return ev["evidence"]


# ------------------------------------------------------------ goal compiler

def test_goal_compiler_extracts_typed_constraints():
    g = compile_goal("find a laptop with 32 GB RAM under 1.2 lakh")
    assert g["scenario"] == "laptop_shopping"
    assert g["constraints"]["max_price_inr"] == 120000 and g["constraints"]["min_ram_gb"] == 32
    assert compile_goal("budget of ₹65k for a 16gb ram laptop")["constraints"]["max_price_inr"] == 65000
    assert compile_goal("check my attendance below 80%")["constraints"]["threshold_pct"] == 80.0
    gh = compile_goal("analyze https://github.com/fastapi/fastapi please")
    assert gh["scenario"] == "github_repo_analysis" and gh["constraints"] == {"owner": "fastapi", "repo": "fastapi"}
    assert compile_goal("analyze the Model Context Protocol GitHub repository")["constraints"] == {
        "owner": "modelcontextprotocol", "repo": "servers"}
    # The six one-click goals used by the Simulated Alexa+ web client.
    chips = {
        "Open my college portal, check my attendance, and tell me which subjects are below 75%.":
            ("attendance_audit", "READ_ANALYZE"),
        "Find a laptop under ₹80,000 with 16GB RAM across stores, compare options, and buy the best one after I approve.":
            ("laptop_shopping", "ACT_WITH_APPROVAL"),
        "Open the National Innovation Grant portal, complete the application form using my project details, "
        "and show it to me before submitting.": ("grant_form", "ACT_WITH_APPROVAL"),
        "Analyze https://github.com/modelcontextprotocol/servers, explain its architecture, and summarize recent open issues.":
            ("github_repo_analysis", "READ_ANALYZE"),
        "Check how many coding questions I solved this month and compare my progress with my friend's public profile.":
            ("profile_comparison", "RESEARCH_COMPARE"),
        "Open the restricted corporate ERP portal and submit my timesheet.": ("url_inspection", "ACT_WITH_APPROVAL"),
    }
    for goal, (scenario, category) in chips.items():
        compiled = compile_goal(goal)
        assert (compiled["scenario"], compiled["task_category"]) == (scenario, category), goal
    assert compile_goal("Find a laptop under ₹80,000 with 16GB RAM")["constraints"]["max_price_inr"] == 80000
    assert compile_goal("Open the restricted corporate ERP portal and submit my timesheet.")["target_url"].endswith("/sandbox/login-wall")
    with pytest.raises(planner_engine.ToolError) as exc:
        compile_goal("sing me a song")
    assert exc.value.code == "GOAL_NOT_UNDERSTOOD"


# ------------------------------------------------- Workflow 1: attendance

def test_workflow_1_attendance_below_75_percent(mcp):
    task = mcp.create_task("Alexa, ask WebPilot to check my college attendance below 75 percent")
    assert task["status"] == "COMPLETED"
    assert task["task_category"] == "READ_ANALYZE"
    data = task["extracted_data"]
    assert data["student"] == "Yash Kalaskar"
    below = {r["code"]: r for r in data["below_threshold"]}
    assert set(below) == {"CS501", "CS502"}
    assert below["CS501"]["subject"] == "Computer Networks"
    assert below["CS501"]["percentage"] == 68.4 and below["CS501"]["classes_needed"] == 10
    assert below["CS502"]["subject"] == "Operating Systems"
    assert below["CS502"]["percentage"] == 71.1 and below["CS502"]["classes_needed"] == 6
    ok = {r["code"]: r["percentage"] for r in data["subjects"] if not r["below_threshold"]}
    assert ok == {"CS503": 92.1, "CS504": 94.4, "CS505": 83.3}
    report = task["verification_report"]
    assert report["verdict"] == "VERIFIED_SUCCESS" and all(c["passed"] for c in report["checks"])
    assert "10" in task["summary"] and "Computer Networks" in task["summary"]
    assert all(s["status"] == "SUCCEEDED" for s in task["plan_steps"])

    evidence = _assert_evidence_files_exist(mcp, task["task_id"], 2)
    page_ev = evidence[0]
    assert page_ev["source_url"].endswith("/sandbox/attendance")
    assert "CS501 | Computer Networks | 26 | 38 | 68.4%" in page_ev["dom_citation_snippet"]
    assert page_ev["verification_passed"] is True
    svg = artifact_path(page_ev["screenshot_url"]).read_text(encoding="utf-8")
    assert "<svg" in svg and "/sandbox/attendance" in svg

    reverify = mcp.call("verify_task_result", {"task_id": task["task_id"]})["structuredContent"]
    assert reverify["verification_report"]["verdict"] == "VERIFIED_SUCCESS"


# ----------------------- Workflow 2 & 3: laptops + recovery + approved checkout

def test_workflow_2_3_laptop_comparison_recovery_and_approved_checkout(mcp, client):
    task = mcp.create_task("Alexa, ask WebPilot to compare 16GB RAM laptops under 80000 rupees")
    tid = task["task_id"]
    assert task["status"] == "AWAITING_APPROVAL"
    assert task["task_category"] == "ACT_WITH_APPROVAL"
    data = task["extracted_data"]

    # Firewall caught the ElectroHub injection trap.
    assert any("WITHOUT_USER_APPROVAL" in a["matched_patterns"] for a in data["firewall_alerts"])
    # Constraint evaluation over all four laptops.
    evaluated = {e["name"]: e for e in data["candidates_evaluated"]}
    assert len(evaluated) == 4
    assert not evaluated["UltraBook ProMax 99"]["qualifies"]
    assert "exceeds budget" in evaluated["UltraBook ProMax 99"]["rejection_reasons"][0]
    assert not evaluated["BudgetBook 14"]["qualifies"]
    assert "8GB RAM" in evaluated["BudgetBook 14"]["rejection_reasons"][0]
    ranked = [(e["rank"], e["name"], e["price_inr"], e["seller"]) for e in data["ranked"]]
    assert ranked == [(1, "ASUS Vivobook 16X", 72990, "ElectroHub India"),
                      (2, "Lenovo IdeaPad Slim 5", 74990, "ByteBazaar India")]

    # Dynamic failure recovery: #1 out of stock at checkout -> pivot to #2.
    recovered = _steps_by_status(task, "FAILED_RECOVERED")
    assert len(recovered) == 1 and "ASUS Vivobook 16X" in recovered[0]["title"]
    assert "OUT_OF_STOCK" in recovered[0]["detail"]
    pivot = next(s for s in task["plan_steps"] if "Lenovo IdeaPad Slim 5" in s["title"])
    assert pivot["status"] == "SUCCEEDED" and pivot["index"] == recovered[0]["index"] + 1
    assert data["recovery_log"][0]["error_code"] == "OUT_OF_STOCK"

    pending = task["pending_approval"]
    assert pending["action_type"] == "PURCHASE_CHECKOUT" and pending["status"] == "PENDING"
    params = pending["parameters"]
    assert params["item_name"] == "Lenovo IdeaPad Slim 5" and params["seller"] == "ByteBazaar India"
    assert params["total_price_inr"] == 74990 and "/sandbox/bytebazaar/checkout" in params["target_url"]
    assert pending["ttl_seconds"] == 900 and pending["expires_at"].endswith("Z")
    assert re.fullmatch(r"[0-9a-f]{64}", pending["parameter_hash"])
    assert artifact_path(pending["preview_screenshot_url"]).exists()
    assert "approval_token" not in pending

    # Human approves in the Alexa+ UI; the agent fetches the token through MCP.
    decision = approve(client, pending["approval_id"])
    assert decision["status"] == "USER_APPROVED" and decision["approval_token"].startswith("wp1.")
    req = mcp.call("request_user_approval", {"task_id": tid})["structuredContent"]
    assert req["approval"]["approval_token"] == decision["approval_token"]

    result = mcp.call("execute_approved_step", {"task_id": tid, "approval_id": pending["approval_id"],
                                                "approval_token": req["approval"]["approval_token"]})
    assert result["isError"] is False, result
    done = result["structuredContent"]
    assert done["confirmation_id"] == "SIM-ORD-2026-8841"
    final = done["task"]
    assert final["status"] == "COMPLETED"
    assert final["verification_report"]["verdict"] == "SIMULATED_TRANSACTION_VERIFIED"
    assert all(c["passed"] for c in final["verification_report"]["checks"])
    assert "SIM-ORD-2026-8841" in final["summary"] and "no real money" in final["summary"].lower()
    assert final["pending_approval"]["status"] == "CONSUMED"
    assert all(s["status"] in ("SUCCEEDED", "FAILED_RECOVERED") for s in final["plan_steps"]), final["plan_steps"]

    receipt = client.get(final["extracted_data"]["outcome"]["confirmation_url"]).text
    assert 'id="order-confirmation-id"' in receipt and "SIM-ORD-2026-8841" in receipt
    assert "CONTROLLED SANDBOX SIMULATION — NO REAL MONEY CHARGED" in receipt

    reverify = mcp.call("verify_task_result", {"task_id": tid})["structuredContent"]["verification_report"]
    assert reverify["verdict"] == "SIMULATED_TRANSACTION_VERIFIED" and reverify["reverified"] is True
    _assert_evidence_files_exist(mcp, tid, 7)


# ----------------------------------------------- Workflow E: grant form

def test_workflow_e_grant_form_population_approval_and_verified_submission(mcp, client):
    task = mcp.create_task("Fill out the National Innovation Grant application form for my WebPilot project",
                           parameters={"form_data": {"applicant_name": "Yash Kalaskar"}})
    tid = task["task_id"]
    assert task["status"] == "AWAITING_APPROVAL"
    mapping = {m["field"]: m for m in task["extracted_data"]["field_mapping"]}
    assert set(mapping) == {"applicant_name", "project_title", "track_name", "repo_url", "abstract_summary"}
    assert mapping["applicant_name"]["source"] == "user_parameters"
    assert mapping["track_name"]["value"] == "Alexa+ Track"
    pending = task["pending_approval"]
    assert pending["action_type"] == "FORM_SUBMISSION"
    assert pending["parameters"]["form_action"].endswith("/sandbox/application-form/submit")
    assert artifact_path(pending["preview_screenshot_url"]).exists()
    # Nothing has been submitted before approval.
    assert client.get("/sandbox/application-form/submissions").json()["count"] == 0

    token = approve(client, pending["approval_id"])["approval_token"]
    result = mcp.call("execute_approved_step", {"task_id": tid, "approval_id": pending["approval_id"], "approval_token": token})
    assert result["isError"] is False, result
    final = result["structuredContent"]["task"]
    assert final["status"] == "COMPLETED"
    report = final["verification_report"]
    assert report["verdict"] == "VERIFIED_SUCCESS"
    assert re.fullmatch(r"NIG-2026-[0-9A-F]{8}", report["confirmation_id"])
    assert any(c["check"] == "echoed_abstract_summary_matches" and c["passed"] for c in report["checks"])
    subs = client.get("/sandbox/application-form/submissions").json()
    assert subs["count"] == 1 and subs["submissions"][0]["receipt_id"] == report["confirmation_id"]
    assert subs["submissions"][0]["fields"]["applicant_name"] == "Yash Kalaskar"


def test_workflow_e_invalid_user_data_pauses_for_input(mcp):
    task = mcp.create_task("Fill the grant application form",
                           parameters={"form_data": {"repo_url": "not a url", "track_name": "Cooking Track"}})
    assert task["status"] == "AWAITING_USER_INPUT"
    problems = " ".join(task["extracted_data"]["problems"])
    assert "repo_url must be a valid http(s) URL" in problems and "track_name must be one of" in problems
    assert task["pending_approval"] is None


# ---------------------------------------------- Workflow F: GitHub analysis

def _github_mock(rate_limited: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if rate_limited:
            return httpx.Response(403, json={"message": "API rate limit exceeded"},
                                  headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"})
        base = "/repos/modelcontextprotocol/servers"
        path = request.url.path
        if path == base:
            return httpx.Response(200, headers={"x-ratelimit-remaining": "4999"}, json={
                "full_name": "modelcontextprotocol/servers", "description": "Model Context Protocol Servers",
                "html_url": "https://github.com/modelcontextprotocol/servers", "stargazers_count": 91446,
                "forks_count": 11234, "subscribers_count": 512, "open_issues_count": 139, "language": "TypeScript",
                "license": {"spdx_id": "MIT"}, "default_branch": "main", "topics": ["mcp"], "archived": False,
            })
        if path == base + "/issues":
            return httpx.Response(200, json=[
                {"number": 5180, "title": "filesystem: expandHome() on Windows", "comments": 2, "labels": [{"name": "bug"}],
                 "html_url": "https://github.com/modelcontextprotocol/servers/issues/5180"},
                {"number": 5179, "title": "Add server", "pull_request": {"url": "x"},
                 "html_url": "https://github.com/modelcontextprotocol/servers/pull/5179"},
                {"number": 5170, "title": "git: support worktrees", "comments": 0, "labels": [],
                 "html_url": "https://github.com/modelcontextprotocol/servers/issues/5170"},
            ])
        if path == base + "/languages":
            return httpx.Response(200, json={"TypeScript": 800000, "Python": 150000, "JavaScript": 50000})
        if path == base + "/contents":
            return httpx.Response(200, json=[{"name": "src", "type": "dir"}, {"name": ".github", "type": "dir"},
                                             {"name": "package.json", "type": "file"}, {"name": "tsconfig.json", "type": "file"},
                                             {"name": "README.md", "type": "file"}])
        if path == base + "/contents/src":
            return httpx.Response(200, json=[{"name": n, "type": "dir"} for n in ("filesystem", "git", "memory", "fetch")])
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.MockTransport(handler)


def test_workflow_f_github_repo_analysis_tier1_adapter(mcp, monkeypatch):
    monkeypatch.setattr(planner_engine, "github_transport", _github_mock())
    task = mcp.create_task("Alexa, ask WebPilot to analyze the Model Context Protocol GitHub repository")
    assert task["status"] == "COMPLETED", task["summary"]
    assert task["environment_mode"] == "LIVE_WEB"
    data = task["extracted_data"]
    assert data["source"] == "GITHUB_REST_API" and data["stars"] == 91446 and data["language"] == "TypeScript"
    assert [i["number"] for i in data["open_issues_sample"]] == [5180, 5170]  # pull requests filtered out
    assert all(i["html_url"].startswith("https://github.com/modelcontextprotocol/servers/issues/") for i in data["open_issues_sample"])
    assert data["architecture"]["src_components"] == ["fetch", "filesystem", "git", "memory"]
    assert "TypeScript" in data["architecture"]["detected_stack"]
    assert data["languages_pct"]["TypeScript"] == 80.0
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"
    assert task["plan_steps"][1]["capability_tier"] == "TIER_1_ADAPTER"
    assert "91,446 stars" in task["summary"]
    evidence = _assert_evidence_files_exist(mcp, task["task_id"], 1)
    assert evidence[0]["capability_method"] == "TIER_1_ADAPTER:GITHUB_REST_API"


def test_workflow_f_github_rate_limit_falls_back_to_semantic_dom(mcp, monkeypatch):
    monkeypatch.setattr(planner_engine, "github_transport", _github_mock(rate_limited=True))
    repo_html = """<html><head><title>GitHub - modelcontextprotocol/servers</title>
      <meta property="og:description" content="Model Context Protocol Servers"></head><body>
      <span id="repo-stars-counter-star" title="91,446">91.4k</span><span id="repo-network-counter" title="11,234">11.2k</span>
      <span id="issues-repo-tab-count" title="139">139</span><span itemprop="programmingLanguage">TypeScript</span>
      <a id="issue_5180_link" href="/modelcontextprotocol/servers/issues/5180">filesystem: expandHome() on Windows</a>
      </body></html>"""

    async def fake_get(self, url):
        return httpx.Response(200, text=repo_html, request=httpx.Request("GET", url))

    async def allow_public(url):
        return None

    monkeypatch.setattr(BrowserRuntime, "_http_get", fake_get)
    monkeypatch.setattr(BrowserRuntime, "_assert_public_destination", staticmethod(allow_public))
    task = mcp.create_task("analyze github repo modelcontextprotocol/servers")
    assert task["status"] == "COMPLETED", task["summary"]
    adapter = task["plan_steps"][1]
    assert adapter["capability_tier"] == "TIER_1_ADAPTER" and adapter["status"] == "FAILED_RECOVERED"
    assert "rate limited" in adapter["detail"]
    fallback = task["plan_steps"][2]
    assert fallback["key"] == "fallback" and fallback["capability_tier"] == "TIER_2_SEMANTIC_DOM"
    data = task["extracted_data"]
    assert data["source"] == "GITHUB_HTML_FALLBACK" and data["stars"] == 91446 and data["open_issues_and_prs"] == 139
    assert data["open_issues_sample"][0]["number"] == 5180


@pytest.mark.network
def test_workflow_f_live_github_api(mcp):
    try:
        rl = httpx.get("https://api.github.com/rate_limit", timeout=6).json()
        if rl["resources"]["core"]["remaining"] < 6:
            pytest.skip("GitHub API rate limit nearly exhausted")
    except Exception:
        pytest.skip("No internet access to api.github.com")
    task = mcp.create_task("Analyze the GitHub repository https://github.com/modelcontextprotocol/servers")
    assert task["status"] == "COMPLETED", task["summary"]
    data = task["extracted_data"]
    assert data["full_name"].lower() == "modelcontextprotocol/servers"
    assert isinstance(data["stars"], int) and data["stars"] > 1000
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"


# ------------------------------------------- Workflow B: profile comparison

def test_workflow_b_developer_profile_comparison_with_citations(mcp):
    task = mcp.create_task("Compare my coding profile progress with my friend's profile")
    assert task["status"] == "COMPLETED"
    data = task["extracted_data"]
    p_user, p_friend = data["profiles"]
    assert (p_user["username"], p_user["total_solved"]) == ("yash_kalaskar", 381)
    assert (p_friend["username"], p_friend["total_solved"]) == ("aarav_mehta", 466)
    assert data["comparison"]["total_solved"] == {"user": 381, "friend": 466, "leader": "aarav_mehta", "gap": 85}
    assert all(src.endswith(("/codearena/yash_kalaskar", "/codearena/aarav_mehta")) for src in data["sources"])
    assert "85 problems" in task["summary"]
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"


# -------------------------------------------- Workflow G: blocker + cancel

def test_workflow_g_login_captcha_wall_blocked_then_cancelled(mcp):
    task = mcp.create_task("Log in to the restricted enterprise portal and download my payslip")
    tid = task["task_id"]
    assert task["status"] == "BLOCKED"
    blocker = task["extracted_data"]["blocker"]
    kinds = {b["barrier_type"] for b in blocker["barriers"]}
    assert {"OTP_2FA_REQUIRED", "CAPTCHA_CHALLENGE", "LOGIN_REQUIRED"} <= kinds
    assert blocker["bypass_attempted"] is False
    assert any(b["selector"] == "div#captcha-box" for b in blocker["barriers"])
    assert "did not try to bypass" in task["summary"]
    assert task["verification_report"]["verdict"] == "UNVERIFIED"
    tier3 = [s for s in task["plan_steps"] if s["capability_tier"] == "TIER_3_HITL_ESCALATION"]
    assert tier3[0]["status"] == "BLOCKED"
    with pytest.raises(planner_engine.ToolError):
        planner_engine.planner.request_user_approval(tid)

    inspected = mcp.call("inspect_website", {"url": "/sandbox/login-wall", "task_id": tid})["structuredContent"]
    assert inspected["blocked"] is True and "CAPTCHA_CHALLENGE" in inspected["human_action_required"]

    cancelled = mcp.call("cancel_web_task", {"task_id": tid, "reason": "User will sign in manually"})
    assert cancelled["isError"] is False
    assert cancelled["structuredContent"]["task"]["status"] == "CANCELLED"
    again = mcp.call("cancel_web_task", {"task_id": tid})
    assert again["isError"] is True and again["structuredContent"]["error_code"] == "TASK_ALREADY_FINAL"


def test_workflow_g_cancel_awaiting_approval_revokes_gate(mcp, client):
    task = mcp.create_task("Compare 16GB RAM laptops under 80000 rupees")
    approval_id = task["pending_approval"]["approval_id"]
    cancelled = mcp.call("cancel_web_task", {"task_id": task["task_id"]})["structuredContent"]
    assert cancelled["revoked_approvals"] == 1 and cancelled["task"]["status"] == "CANCELLED"
    r = client.post(f"/api/approvals/{approval_id}/decide", json={"decision": "APPROVE"})
    assert r.status_code == 409


def test_workflow_g_generic_url_inspection_distills_structure(mcp):
    task = mcp.create_task("Inspect this website and list what I can do there",
                           target_url="/sandbox/bytebazaar", scenario="url_inspection")
    assert task["status"] == "COMPLETED"
    data = task["extracted_data"]
    assert data["title"] == "ByteBazaar India — Laptops"
    assert any(e["id"] == "buy-now-btn" and e["role"] == "button" for e in data["interactive_elements"])
    assert data["untrusted_text_excerpt"].startswith("<untrusted_web_content")
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"


# ------------------------------------------------------ REST + runtime

def test_rest_endpoints_and_idempotency_key(mcp, client):
    first = mcp.call("create_web_task", {"goal": "check my attendance", "idempotency_key": "demo-run-1"})["structuredContent"]
    second = mcp.call("create_web_task", {"goal": "check my attendance", "idempotency_key": "demo-run-1"})["structuredContent"]
    assert first["task"]["task_id"] == second["task"]["task_id"] and second["idempotent_replay"] is True

    health = client.get("/health").json()
    assert health["status"] == "ok" and health["mcp"]["protocolVersion"] == "2025-11-25"
    assert len(health["mcp"]["tools"]) == 11 and health["database"]["journal_mode"] == "WAL"
    tasks = client.get("/api/tasks").json()
    assert tasks["count"] == 1
    detail = client.get(f"/api/tasks/{first['task']['task_id']}").json()
    assert detail["task"]["status"] == "COMPLETED" and len(detail["evidence"]) >= 2
    assert client.get("/api/tasks/task_ffffffffffff").status_code == 404

    reset = client.post("/api/reset-demo").json()
    assert reset["reset"] is True and reset["deleted"]["tasks"] == 1
    assert client.get("/api/tasks").json()["count"] == 0


@pytest.mark.asyncio
async def test_browser_runtime_svg_fallback_evidence_always_exists(client):
    page = await browser_runtime.inspect("/sandbox/attendance", "task_runtime01", "step_svg")
    assert page["status_code"] == 200 and page["is_sandbox"] is True
    assert page["capability_method"] == "TIER_2_SEMANTIC_DOM:HTTP_BS4_FALLBACK"
    assert page["screenshot_kind"] == "svg" and page["screenshot_url"] == "/artifacts/task_runtime01_step_svg.svg"
    svg = artifact_path(page["screenshot_url"]).read_text(encoding="utf-8")
    assert svg.startswith("<?xml") and "attendance-table" in svg and "CS501" in svg
    assert page["distilled"]["tables"][0]["table_id"] == "attendance-table"
