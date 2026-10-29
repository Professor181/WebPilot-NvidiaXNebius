"""Upgrade 3 — Mid-task conversational interruptions & re-planning."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from backend import db
from backend.main import app
from backend.planner_engine import planner
from backend.tests.conftest import approve

LAPTOP_GOAL = "Compare 16GB RAM laptops under 80000 rupees"


def _interrupt(client, task_id, instruction, **extra):
    return client.post(f"/api/tasks/{task_id}/interrupt", json={"instruction": instruction, **extra},
                       headers={"Origin": "http://localhost:3000"})


def test_interrupt_at_approval_gate_replans_and_preserves_evidence(mcp, client):
    task = mcp.create_task(LAPTOP_GOAL)
    old_approval = task["pending_approval"]["approval_id"]
    old_evidence = {e["evidence_id"] for e in mcp.call("get_execution_evidence", {"task_id": task["task_id"]})["structuredContent"]["evidence"]}

    r = _interrupt(client, task["task_id"], "Skip ElectroHub and only check ByteBazaar")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "APPLIED" and body["changes"] == ["Skip ElectroHub India (only ByteBazaar India)"]
    assert body["interrupt"]["status"] == "APPLIED"
    new = body["task"]
    assert new["status"] == "AWAITING_APPROVAL"
    assert new["summary"].startswith("Updated plan (Skip ElectroHub India")
    assert "cancelled the previous approval" in new["summary"]

    # Old approval revoked, a brand-new one locked for the re-planned choice (no out-of-stock detour now).
    assert db.get_approval(old_approval)["status"] == "REVOKED"
    pending = new["pending_approval"]
    assert pending["approval_id"] != old_approval and pending["status"] == "PENDING"
    assert pending["parameters"]["item_name"] == "Lenovo IdeaPad Slim 5"
    keys = [s["key"] for s in new["plan_steps"]]
    assert keys.index("interrupt_1") < keys.index("rank") < keys.index("checkout_1") < keys.index("approval")
    assert "approval@v1" in keys and "checkout_1@v1" in keys
    superseded = next(s for s in new["plan_steps"] if s["key"] == "approval@v1")
    assert superseded["status"] == "CANCELLED"
    assert not any(s["status"] == "FAILED_RECOVERED" for s in new["plan_steps"] if "@v" not in s["key"])
    excluded = [c for c in new["extracted_data"]["candidates_evaluated"] if "store excluded by you" in c["rejection_reasons"]]
    assert {c["name"] for c in excluded} == {"ASUS Vivobook 16X", "UltraBook ProMax 99"}

    evidence = {e["evidence_id"] for e in mcp.call("get_execution_evidence", {"task_id": task["task_id"]})["structuredContent"]["evidence"]}
    assert old_evidence < evidence, "completed evidence must be preserved and extended"

    # The stale approval can never be used; the fresh one executes and verifies normally.
    stale_token = approve  # noqa: F841 (documenting intent)
    token = approve(client, pending["approval_id"])["approval_token"]
    done = mcp.call("execute_approved_step", {"task_id": task["task_id"], "approval_id": pending["approval_id"], "approval_token": token})
    assert done["isError"] is False
    assert done["structuredContent"]["task"]["verification_report"]["verdict"] == "SIMULATED_TRANSACTION_VERIFIED"
    old = mcp.call("execute_approved_step", {"task_id": task["task_id"], "approval_id": old_approval, "approval_token": token})
    assert old["structuredContent"]["error_code"] == "APPROVAL_REVOKED"


def test_budget_cut_at_approval_gate_cancels_purchase_honestly(mcp, client):
    task = mcp.create_task(LAPTOP_GOAL)
    old_approval = task["pending_approval"]["approval_id"]
    r = _interrupt(client, task["task_id"], "Wait, change the budget from 80000 to 70000")
    new = r.json()["task"]
    assert r.json()["changes"] == ["Budget ₹80,000 → ₹70,000"]
    assert new["status"] == "COMPLETED"
    assert new["pending_approval"] is None
    assert "No laptop with at least 16GB RAM is available under ₹70,000" in new["summary"]
    assert db.get_approval(old_approval)["status"] == "REVOKED"
    assert client.get("/sandbox/bytebazaar/orders").json()["count"] == 0


def test_user_driven_upgrade_selects_the_premium_laptop(mcp, client):
    """The ₹1,45,000 laptop the hidden injection pushed is only chosen when the *user* raises the constraints."""
    task = mcp.create_task(LAPTOP_GOAL)
    r = _interrupt(client, task["task_id"], "Actually I need 32GB RAM and my budget is 1.5 lakh")
    body = r.json()
    assert set(body["changes"]) == {"Budget ₹80,000 → ₹1,50,000", "RAM ≥16GB → ≥32GB"}
    params = body["task"]["pending_approval"]["parameters"]
    assert params["item_name"] == "UltraBook ProMax 99" and params["total_price_inr"] == 145000


@pytest.mark.asyncio
async def test_interrupt_while_executing_is_applied_at_the_next_checkpoint(client):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=60) as ac:
        create = asyncio.create_task(ac.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "create_web_task", "arguments": {"goal": LAPTOP_GOAL, "parameters": {"pace_ms": 400}}},
        }))
        task_id = None
        for _ in range(100):
            await asyncio.sleep(0.05)
            tasks = (await ac.get("/api/tasks?limit=1")).json()["tasks"]
            if tasks and tasks[0]["status"] == "EXECUTING":
                task_id = tasks[0]["task_id"]
                break
        assert task_id, "task never started executing"
        r = await ac.post(f"/api/tasks/{task_id}/interrupt", json={"instruction": "Skip ByteBazaar, budget 1.5 lakh and 32GB RAM"})
        assert r.status_code == 202 and r.json()["mode"] == "QUEUED"
        result = (await create).json()["result"]["structuredContent"]["task"]

    steps = {s["key"]: s for s in result["plan_steps"]}
    assert steps["inspect_store_1"]["status"] == "SKIPPED"
    assert "never visited" in steps["inspect_store_1"]["detail"]
    assert steps["interrupt_1"]["status"] == "SUCCEEDED"
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["pending_approval"]["parameters"]["item_name"] == "UltraBook ProMax 99"
    assert result["summary"].startswith("Updated plan (")
    assert "comes only from your own constraints" in result["summary"]
    interrupts = (client.get(f"/api/tasks/{task_id}/interrupts")).json()["interrupts"]
    assert [i["status"] for i in interrupts] == ["APPLIED"]
    # ByteBazaar was never fetched, so no ByteBazaar evidence exists.
    evidence = client.get(f"/api/tasks/{task_id}").json()["evidence"]
    assert not any("/sandbox/bytebazaar" in e["source_url"] for e in evidence)


@pytest.mark.asyncio
async def test_attendance_threshold_change_mid_run(client):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=60) as ac:
        create = asyncio.create_task(ac.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "create_web_task", "arguments": {"goal": "check my attendance below 75 percent",
                                                                  "parameters": {"pace_ms": 500}}},
        }))
        task_id = None
        for _ in range(100):
            await asyncio.sleep(0.05)
            tasks = (await ac.get("/api/tasks?limit=1")).json()["tasks"]
            if tasks and tasks[0]["status"] == "EXECUTING":
                task_id = tasks[0]["task_id"]
                break
        r = await ac.post(f"/api/tasks/{task_id}/interrupt", json={"instruction": "Use 85 percent as the threshold instead"})
        assert r.status_code == 202
        result = (await create).json()["result"]["structuredContent"]["task"]
    assert result["status"] == "COMPLETED"
    assert result["extracted_data"]["threshold_pct"] == 85.0
    assert {s["code"] for s in result["extracted_data"]["below_threshold"]} == {"CS501", "CS502", "CS505"}
    assert any(s["key"] == "interrupt_1" for s in result["plan_steps"])


def test_grant_form_field_change_at_approval(mcp, client):
    task = mcp.create_task("Fill the grant application form")
    old = task["pending_approval"]["approval_id"]
    r = _interrupt(client, task["task_id"], "Change the project title to WebPilot Voice Copilot")
    body = r.json()
    assert body["mode"] == "APPLIED"
    new = body["task"]
    assert new["status"] == "AWAITING_APPROVAL"
    assert new["pending_approval"]["parameters"]["fields"]["project_title"] == "WebPilot Voice Copilot"
    assert db.get_approval(old)["status"] == "REVOKED"
    assert any(s["key"] == "reinspect" and s["status"] == "SUCCEEDED" for s in new["plan_steps"])
    assert any(s["key"] == "preview@v1" for s in new["plan_steps"])


def test_interrupt_errors_and_mcp_tool(mcp, client):
    done = mcp.create_task("check my attendance")
    r = _interrupt(client, done["task_id"], "use 80 percent")
    assert r.status_code == 409 and r.json()["error_code"] == "TASK_NOT_INTERRUPTIBLE"
    assert _interrupt(client, "task_000000000000", "use 80 percent").status_code == 404

    task = mcp.create_task(LAPTOP_GOAL)
    r = _interrupt(client, task["task_id"], "make it blue please")
    assert r.status_code == 422 and r.json()["error_code"] == "INTERRUPT_NOT_UNDERSTOOD"
    assert "Change the budget" in r.json()["hint"]
    r = _interrupt(client, task["task_id"], "skip electrohub and bytebazaar")
    assert r.status_code == 422 and r.json()["error_code"] == "INVALID_CONSTRAINT"

    out = mcp.call("interrupt_web_task", {"task_id": task["task_id"], "instruction": "budget 75000",
                                          "constraints": {"min_ram_gb": 16}})
    assert out["isError"] is False
    assert out["content"][0]["text"].startswith("Interruption APPLIED (Budget ₹80,000 → ₹75,000)")
    assert out["structuredContent"]["task"]["pending_approval"]["parameters"]["item_name"] == "Lenovo IdeaPad Slim 5"
    assert len(out["structuredContent"]["interruptions"]) == 1
    bad = mcp.call("interrupt_web_task", {"task_id": task["task_id"], "instruction": "hmm"})
    assert bad["isError"] is True


@pytest.mark.asyncio
async def test_late_interrupts_are_rejected_honestly(mcp):
    done = mcp.create_task("check my attendance")
    db.insert_interrupt("int_late00000001", done["task_id"], "use 80 percent", {"threshold_pct": 80.0}, ["Threshold"])
    await planner._drain_late_interrupts(done["task_id"])
    itr = db.get_interrupt("int_late00000001")
    assert itr["status"] == "REJECTED" and "after the task finished" in itr["outcome"]
