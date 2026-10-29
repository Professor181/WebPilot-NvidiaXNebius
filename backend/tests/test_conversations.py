"""Conversational live tasks: follow-ups in the same tab, variants, budgets, booking widgets, sign-in handling,
the credential vault, the WebPilot Chrome launcher and automatic screenshot cleanup."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time

import httpx
import pytest

from backend import config, credential_vault, live_autopilot, llm_brain, site_login, task_intents, user_chrome
from backend.llm_brain import OllamaProvider, decide_next_browser_action, parse_goal_intents
from backend.tests.conftest import approve, artifact_path

UI = {"Origin": "http://localhost:3000"}


def _run(mcp, goal, conv, start_url=None, **extra):
    args = {"goal": goal, "conversation_id": conv, "max_steps": 8, **extra}
    if start_url:
        args["start_url"] = start_url
    result = mcp.call("run_live_web_autopilot", args)
    assert result["isError"] is False, result
    return result["structuredContent"]["task"]


def _execute(mcp, client, task):
    approval = task["pending_approval"]
    token = approve(client, approval["approval_id"])["approval_token"]
    out = mcp.call("execute_approved_step", {"task_id": task["task_id"], "approval_id": approval["approval_id"], "approval_token": token})
    assert out["isError"] is False, out
    return out["structuredContent"]["task"]


def _execute_answering_sign_in(mcp, client, task, choice):
    """Approve + execute in a thread; answer the sign-in prompt from the 'UI' when it appears."""
    approval = task["pending_approval"]
    token = approve(client, approval["approval_id"])["approval_token"]
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("out", mcp.call("execute_approved_step", {
        "task_id": task["task_id"], "approval_id": approval["approval_id"], "approval_token": token})))
    th.start()
    request = None
    for _ in range(240):
        current = client.get(f"/api/tasks/{task['task_id']}").json()
        current = current.get("task", current)
        request = (current.get("extracted_data") or {}).get("login_request")
        if request:
            assert current["status"] == "AWAITING_USER_INPUT"
            r = client.post(f"/api/tasks/{task['task_id']}/sign-in", json=choice, headers=UI)
            assert r.status_code == 200, r.text
            break
        time.sleep(0.25)
    th.join(180)
    assert request, "the run never asked how to sign in"
    return request, box["out"]["structuredContent"]["task"]


# ====================================================================================== pure intents

def test_follow_up_references_variants_budgets_and_booking_details():
    assert task_intents.parse_reference("check if the first one that you send me is available in size 10?")["index"] == 0
    assert task_intents.parse_reference("book the third one")["index"] == 2
    assert task_intents.parse_reference("what about option 2")["index"] == 1
    assert task_intents.parse_reference("buy it")["current"] is True
    assert task_intents.parse_reference("this one looks good")["current"] is True
    assert task_intents.parse_variant("is it available in size 10?") == {"attribute": "size", "value": "10", "check": True, "select": False}
    assert task_intents.parse_variant("select colour black and add to cart")["value"] == "black"
    assert task_intents.parse_variant("add the 256 GB storage one")["value"] in ("256 GB", "256 gb")
    for text, amount in (("under 15k", 15000), ("below ₹2,000", 2000), ("3000 budget", 3000), ("within 1.5 lakh", 150000)):
        assert task_intents.parse_max_price(f"best shoes {text}") == amount
    today = dt.date(2026, 10, 22)
    assert task_intents.parse_dates("checkin date 12 Dec and check out date 15 Dec", today) == {"check_in": "2026-12-12", "check_out": "2026-12-15"}
    assert task_intents.parse_dates("from 3 Jan to 5 Jan", today) == {"check_in": "2027-01-03", "check_out": "2027-01-05"}
    assert task_intents.parse_dates("check-in 02/11/2026 check-out 04/11/2026", today)["check_in"] == "2026-11-02"  # day first
    assert task_intents.parse_guests("for 4 adults and 2 children, 2 rooms") == {"adults": 4, "children": 2, "rooms": 2}
    villa = parse_goal_intents("search best villas under 15k in mahabaleshwar for checkin date 12 Dec and check out date 15 Dec for 4 adults")
    assert villa["search_query"] == "villas in mahabaleshwar" and villa["booking"] and villa["max_price"] == 15000
    assert villa["wants_checkout"] is False  # a check-out *date* is not "check out"
    shoes = parse_goal_intents("go on my amazon website search for some best shoes under 3000 and show me")
    assert shoes["search_query"] == "shoes" and shoes["wants_open_result"] is False and shoes["max_price"] == 3000


def test_sign_in_and_sign_up_pages_are_recognised_generically():
    email_step = [{"idx": 1, "tag": "input", "input_type": "email", "accessible_name": "Email or mobile number"},
                  {"idx": 2, "tag": "button", "accessible_name": "Continue"},
                  {"idx": 3, "tag": "a", "accessible_name": "Create your Strideway account"}]
    auth = site_login.classify_auth_page(email_step, "https://shop.test/signin?next=/x", "Sign in", "")
    assert auth["kind"] == "login_identifier" and auth["identifier_idx"] == 1 and auth["submit_idx"] == 2 and auth["signup_link_idx"] == 3
    pw_step = [{"idx": 1, "tag": "input", "input_type": "password", "accessible_name": "Password"},
               {"idx": 2, "tag": "button", "accessible_name": "Sign in"}]
    assert site_login.classify_auth_page(pw_step, "https://shop.test/ap/signin", "Sign in", "")["kind"] == "login"
    signup = [{"idx": 1, "tag": "input", "accessible_name": "Your name", "required": True},
              {"idx": 2, "tag": "input", "input_type": "email", "accessible_name": "Email", "required": True},
              {"idx": 3, "tag": "input", "input_type": "password", "accessible_name": "Password"},
              {"idx": 4, "tag": "input", "input_type": "password", "accessible_name": "Re-enter password"},
              {"idx": 5, "tag": "button", "accessible_name": "Create account"}]
    s = site_login.classify_auth_page(signup, "https://shop.test/register", "Create account", "")
    assert s["kind"] == "signup" and s["name_idxs"] == [1] and s["password_idxs"] == [3, 4] and s["missing_required"] == []
    otp = [{"idx": 1, "tag": "input", "autocomplete": "one-time-code", "accessible_name": "Enter code"}]
    assert site_login.classify_auth_page(otp, "https://shop.test/verify", "Verify", "")["kind"] == "otp"
    # A newsletter box or a sidebar login on a busy content page is not a wall.
    busy = [{"idx": i, "tag": "a", "accessible_name": f"Article {i}", "href": f"https://news.test/{i}"} for i in range(60)]
    side = site_login.classify_auth_page(busy + [{"idx": 99, "tag": "input", "input_type": "password", "accessible_name": "Password"}],
                                         "https://news.test/", "News", "")
    assert side["kind"] == "login" and site_login.is_blocking(side) is False


# =========================================================================================== vault

def test_vault_keeps_passwords_only_in_the_os_store(client, tmp_path):
    rec = credential_vault.save("https://accounts.example.co.in/login", "me@example.com", "S3cret-Value!42")
    assert rec["site"] == "example.co.in" and "password" not in rec
    assert credential_vault.get("www.example.co.in")["password"] == "S3cret-Value!42"
    listing = client.get("/api/credentials", headers=UI).json()
    assert listing["vault_available"] is True and "S3cret-Value!42" not in json.dumps(listing)
    with sqlite3.connect(config.DB_PATH) as conn:  # nothing secret in the database
        dump = "\n".join(conn.iterdump())
    assert "S3cret-Value!42" not in dump
    tools = {t["name"] for t in client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                            headers={"Mcp-Session-Id": "x"}).json().get("result", {}).get("tools", [])}
    assert not any("credential" in t or "password" in t for t in tools)
    assert client.post("/api/credentials", json={"site": "shop.test", "username": "u", "password": "p"}, headers=UI).json()["saved"]
    assert client.delete("/api/credentials/shop.test", headers=UI).json()["deleted"] is True
    assert credential_vault.get("shop.test") is None
    pw = credential_vault.generate_password()
    assert len(pw) == 20 and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
    assert client.post("/api/credentials", json={"site": "shop.test", "username": "u", "password": "p"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    credential_vault.delete("example.co.in")


def test_settings_validate_the_email(client):
    assert client.post("/api/settings", json={"email": "not-an-email"}, headers=UI).status_code == 422
    out = client.post("/api/settings", json={"email": "me@example.com", "full_name": "Me Person"}, headers=UI).json()
    assert out == {"email": "me@example.com", "full_name": "Me Person"}
    client.post("/api/settings", json={"email": "", "full_name": ""}, headers=UI)


# ============================================================================ shoes: a whole conversation

def test_shoe_conversation_follow_ups_variants_saved_login_and_payment_handoff(mcp, client):
    credential_vault.save("http://localhost:8000", "asha@example.com", "Correct-Horse-9")
    conv = "conv-shoes"
    t1 = _run(mcp, "search for some best shoes under 3000 and show me", conv, "/sandbox/strideway")
    assert t1["status"] == "COMPLETED", t1["summary"]
    options = t1["extracted_data"]["options"]
    assert [o["title"] for o in options] == ["Trailrun Mesh Running Shoes", "Breeze Canvas Slip-on Shoes"]  # ₹3,999 pair excluded
    assert "under 3,000" in t1["summary"] and "1. Trailrun" in t1["summary"]
    cited = t1["extracted_data"]["answer"]["citations"]
    assert all(artifact_path(c["screenshot_url"]).exists() for c in cited[:2])  # one card screenshot per option
    assert conv in live_autopilot.SESSIONS  # the tab stays open for the next turn

    t2 = _run(mcp, "check if the first one that you send me is available in size 10?", conv)
    assert t2["status"] == "COMPLETED"
    assert t2["extracted_data"]["autopilot"]["filmstrip"][0]["description"].startswith("Opened the first one")
    assert t2["extracted_data"]["variant"]["available"] is False
    assert t2["summary"].startswith("No — size 10 isn't available for “Trailrun Mesh Running Shoes")

    t3 = _run(mcp, "is the second one available in size 8?", conv)
    assert t3["extracted_data"]["variant"]["available"] is True and t3["summary"].startswith("Yes — size 8 is available")
    assert any(f["action"] == "CLICK" and "UK 8" in f["description"] for f in t3["extracted_data"]["autopilot"]["filmstrip"])

    t4 = _run(mcp, "buy it", conv)
    assert t4["status"] == "AWAITING_APPROVAL"
    params = t4["pending_approval"]["parameters"]
    assert params["button_text"] == "Buy now" and params["item_heading"] == "Breeze Canvas Slip-on Shoes" and params["visible_prices"] == [999.0]

    final = _execute(mcp, client, t4)
    film = final["extracted_data"]["autopilot"]["filmstrip"]
    actions = [f["action"] for f in film]
    assert actions[-4:] == ["APPROVED_CLICK", "LOGIN", "CLICK", "HANDOFF"]  # sign-in wall handled with the saved login
    assert "asha@example.com" in film[-3]["description"] and "Deliver to this address" in film[-2]["description"]
    assert final["verification_report"]["verdict"] == "LIVE_CHECKOUT_HANDOFF_VERIFIED"
    assert "/sandbox/strideway/pay?item=sw-4&size=8" in final["extracted_data"]["outcome"]["handoff_url"]
    assert "1,048" in final["summary"]  # the real total incl. delivery, read from the payment page
    evidence = mcp.call("get_execution_evidence", {"task_id": t4["task_id"]})["structuredContent"]["evidence"]
    assert "Correct-Horse-9" not in json.dumps(evidence) and "Correct-Horse-9" not in json.dumps(final)

    # Ending the conversation closes the tab and deletes every screenshot taken during it.
    shots = [artifact_path(f["screenshot_url"]) for f in film if f.get("screenshot_url")]
    assert shots and all(p.exists() for p in shots)
    ended = client.post(f"/api/conversations/{conv}/end", headers=UI).json()
    assert ended["tab_closed"] is True and len(ended["tasks"]) == 4
    assert not any(p.exists() for p in shots)
    after = client.get(f"/api/tasks/{t4['task_id']}").json()
    after = after.get("task", after)
    assert after["extracted_data"]["screenshots_deleted"] is True
    assert all(f["screenshot_url"] is None for f in after["extracted_data"]["autopilot"]["filmstrip"])
    assert all(e["screenshot_url"] is None for e in mcp.call("get_execution_evidence", {"task_id": t1["task_id"]})["structuredContent"]["evidence"])
    assert conv not in live_autopilot.SESSIONS
    credential_vault.delete("localhost")


def test_unknown_reference_is_answered_honestly(mcp, client):
    _run(mcp, "search for some best shoes under 3000 and show me", "conv-ref", "/sandbox/strideway")
    t = _run(mcp, "book the fifth one", "conv-ref")
    assert t["status"] == "AWAITING_USER_INPUT" and "I showed 2 option(s)" in t["summary"]
    client.post("/api/conversations/conv-ref/end", headers=UI)


# ================================================================================== villa booking

def test_booking_form_calendar_stepper_budget_and_reserve_to_payment(mcp, client):
    cin = dt.date.today() + dt.timedelta(days=50)
    cout = cin + dt.timedelta(days=3)
    conv = "conv-villas"
    goal = (f"search best villas under 15k in mahabaleshwar for checkin date {cin.strftime('%d %b %Y')} and check out date "
            f"{cout.strftime('%d %b %Y')} for 4 adults")
    t1 = _run(mcp, goal, conv, "/sandbox/staynest", max_steps=6)
    assert t1["status"] == "COMPLETED", t1["summary"]
    url = t1["extracted_data"]["url"]
    assert f"in={cin.isoformat()}" in url and f"out={cout.isoformat()}" in url and "adults=4" in url
    film = t1["extracted_data"]["autopilot"]["filmstrip"]
    descs = " | ".join(f["description"] for f in film)
    assert "Check-in" in descs and cin.strftime("%B") in descs and descs.count("Increase adults") == 3
    titles = [o["title"] for o in t1["extracted_data"]["options"]]
    assert titles == ["Cliffside Strawberry Villa", "Venna Lake Cottage Villa", "Hilltop Heritage Bungalow Villa"]  # ₹18,900 excluded
    assert "Luxury resort deals" not in t1["summary"]  # sponsored result never offered

    t2 = _run(mcp, "the third one looks good", conv)
    assert t2["summary"].startswith("I've opened “Hilltop Heritage Bungalow Villa” — ₹14,200") and "Shall I book it?" in t2["summary"]
    t3 = _run(mcp, "book it", conv)
    assert t3["status"] == "AWAITING_APPROVAL" and t3["pending_approval"]["parameters"]["button_text"] == "Reserve"
    final = _execute(mcp, client, t3)
    actions = [f["action"] for f in final["extracted_data"]["autopilot"]["filmstrip"]]
    assert actions[-3:] == ["APPROVED_CLICK", "CLICK", "HANDOFF"]  # "Continue to payment" inside the approval's scope
    assert final["verification_report"]["verdict"] == "LIVE_CHECKOUT_HANDOFF_VERIFIED"
    assert "/sandbox/staynest/pay" in final["extracted_data"]["outcome"]["handoff_url"]
    client.post(f"/api/conversations/{conv}/end", headers=UI)


# ======================================================================================= sign-in choices

def test_new_site_sign_up_with_the_users_email_after_permission(mcp, client):
    client.post("/api/settings", json={"email": "new.user@example.com", "full_name": "New User"}, headers=UI)
    conv = "conv-signup"
    _run(mcp, "open the Cloudstep Everyday Sneakers", conv, "/sandbox/strideway/p/sw-2")
    t = _run(mcp, "buy it", conv)
    request, final = _execute_answering_sign_in(mcp, client, t, {"choice": "signup"})
    assert request["options"] == ["self", "provide", "signup"] and request["signup_email"] == "new.user@example.com"
    assert any(f["action"] == "SIGNUP" and "new.user@example.com" in f["description"] for f in final["extracted_data"]["autopilot"]["filmstrip"])
    account = credential_vault.get("localhost")
    assert account["username"] == "new.user@example.com" and len(account["password"]) == 20
    assert client.get("/sandbox/strideway/users.json").json()["new.user@example.com"]["has_address"] is False
    # A new account has no delivery address: WebPilot never types personal details and says so.
    assert final["status"] == "AWAITING_USER_INPUT" and "delivery / guest details" in final["summary"]
    assert account["password"] not in json.dumps(final)
    client.post(f"/api/conversations/{conv}/end", headers=UI)
    credential_vault.delete("localhost")
    client.post("/api/settings", json={"email": "", "full_name": ""}, headers=UI)


def test_user_supplied_login_is_used_and_remembered(mcp, client):
    conv = "conv-provide"
    _run(mcp, "open the Breeze Canvas Slip-on Shoes", conv, "/sandbox/strideway/p/sw-4")
    t = _run(mcp, "buy it", conv)
    request, final = _execute_answering_sign_in(mcp, client, t, {"choice": "provide", "username": "asha@example.com",
                                                                "password": "Correct-Horse-9", "remember": True})
    assert request["has_saved_login"] is False and request["options"] == ["self", "provide"]  # no email set → no sign-up offer
    assert final["verification_report"]["verdict"] == "LIVE_CHECKOUT_HANDOFF_VERIFIED"
    assert credential_vault.get("localhost")["username"] == "asha@example.com"
    assert "Correct-Horse-9" not in json.dumps(final)
    client.post(f"/api/conversations/{conv}/end", headers=UI)
    credential_vault.delete("localhost")


def test_sign_in_endpoint_rejects_unexpected_or_impossible_choices(client):
    assert client.post("/api/tasks/task_nope/sign-in", json={"choice": "self"}, headers=UI).status_code == 409
    assert client.post("/api/tasks/task_nope/sign-in", json={"choice": "hack"}, headers=UI).status_code == 422
    assert client.post("/api/tasks/task_nope/sign-in", json={"choice": "self"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_without_a_conversation_a_sign_in_wall_is_still_blocked_honestly(mcp):
    result = mcp.call("run_live_web_autopilot", {"goal": "Go to the portal and search for my payslip", "start_url": "/sandbox/login-wall",
                                                 "max_steps": 4})
    task = result["structuredContent"]["task"]
    assert task["status"] == "BLOCKED" and task["extracted_data"]["blocker"]["bypass_attempted"] is False


# ================================================================================ LLM safety rules

def _ollama(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})
    return httpx.MockTransport(handler)


FORM = [
    {"idx": 1, "tag": "input", "input_type": "password", "accessible_name": "Password"},
    {"idx": 2, "tag": "select", "accessible_name": "Size", "options": [{"text": "UK 9", "disabled": False}, {"text": "UK 10", "disabled": True}]},
    {"idx": 3, "tag": "button", "accessible_name": "UK 11", "disabled": True},
    {"idx": 4, "tag": "input", "input_type": "date", "accessible_name": "Check-in"},
]


@pytest.mark.parametrize("proposal,accepted", [
    ({"action": "FILL", "element_idx": 1, "text": "hunter2", "reason": "type password"}, False),
    ({"action": "SELECT", "element_idx": 2, "text": "UK 10", "reason": "pick size"}, False),
    ({"action": "SELECT", "element_idx": 2, "text": "UK 9", "reason": "pick size"}, True),
    ({"action": "CLICK", "element_idx": 3, "text": None, "reason": "pick size"}, False),
    ({"action": "FILL", "element_idx": 4, "text": "2026-12-12", "reason": "date"}, True),
])
@pytest.mark.asyncio
async def test_model_proposals_obey_server_side_rules(monkeypatch, proposal, accepted):
    monkeypatch.setattr(llm_brain, "http_transport", _ollama(proposal))
    monkeypatch.setattr(llm_brain.brain, "ollama", OllamaProvider())
    monkeypatch.setenv("WEBPILOT_BRAIN_PROVIDER", "ollama")
    out = await decide_next_browser_action("pick size 9 for check-in 12 Dec", "https://shop.test/p/1", "Shoe", [], FORM, "",
                                           memory={"results": [{"title": "Shoe A", "price": "₹999"}]})
    assert out["decided_by"].startswith("ollama_local") is accepted, out


# ================================================================= WebPilot Chrome + screenshot retention

@pytest.mark.asyncio
async def test_webpilot_chrome_is_started_with_its_own_profile_on_loopback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CHROME_AUTOLAUNCH", False)
    monkeypatch.setattr(user_chrome, "endpoint_alive", _alive_after(99))
    assert (await user_chrome.ensure_running())["running"] is False  # auto-launch off: never starts Chrome

    calls = []
    monkeypatch.setattr(config, "CHROME_AUTOLAUNCH", True)
    monkeypatch.setattr(config, "CHROME_PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(user_chrome, "chrome_path", lambda: "C:/fake/chrome.exe")
    monkeypatch.setattr(user_chrome.subprocess, "Popen", lambda args, **kw: calls.append(args) or _FakeProc())
    monkeypatch.setattr(user_chrome, "endpoint_alive", _alive_after(2))
    out = await user_chrome.ensure_running()
    assert out == {"running": True, "launched": True, "detail": "Started the WebPilot Chrome window."}
    args = calls[0]
    assert "--remote-debugging-address=127.0.0.1" in args and f"--user-data-dir={tmp_path / 'profile'}" in args
    assert "--remote-debugging-port=9222" in args


class _FakeProc:
    def poll(self):
        return None


def _alive_after(n):
    state = {"calls": 0}

    async def alive(timeout: float = 1.5) -> bool:
        state["calls"] += 1
        return state["calls"] > n
    return alive


@pytest.mark.asyncio
async def test_finished_standalone_tasks_lose_their_screenshots_after_the_retention_window(mcp, monkeypatch):
    result = mcp.call("run_live_web_autopilot", {"goal": "search for some best shoes under 3000 and show me",
                                                 "start_url": "/sandbox/strideway", "max_steps": 4})
    task = result["structuredContent"]["task"]
    shots = [artifact_path(f["screenshot_url"]) for f in task["extracted_data"]["autopilot"]["filmstrip"] if f["screenshot_url"]]
    assert shots and all(p.exists() for p in shots)
    monkeypatch.setattr(config, "SCREENSHOT_RETENTION_SECONDS", 3600)
    assert await live_autopilot.purge_old_screenshots() == 0 or all(p.exists() for p in shots)
    monkeypatch.setattr(config, "SCREENSHOT_RETENTION_SECONDS", -1)
    assert await live_autopilot.purge_old_screenshots() >= len(shots)
    assert not any(p.exists() for p in shots)


# ===================================================================================== chat-style sites

def test_ask_a_chat_site_types_the_message_waits_for_the_streamed_reply_and_reads_it(mcp, client):
    assert task_intents.parse_ask("Open ChatGPT and ask it to plan a 3-day Goa trip") == "Plan a 3-day Goa trip"
    assert task_intents.parse_ask("go to chatgpt and ask what is MCP") == "What is MCP"
    assert task_intents.parse_ask("Search for books on Paperlane and tell me the cheapest one") is None
    t = _run(mcp, "Open Chatterbox and ask it to plan a 3-day Goa trip", "conv-chat", "/sandbox/chatterbox")
    assert t["status"] == "COMPLETED", t["summary"]
    film = t["extracted_data"]["autopilot"]["filmstrip"]
    assert film[1]["action"] == "FILL_AND_SUBMIT" and "Plan a 3-day Goa trip" in film[1]["description"]
    assert t["summary"].startswith("localhost replied:")
    assert "Day 3: a sunset cruise before you fly home." in t["summary"]  # waited for the whole streamed reply
    assert "Hi! What can I help with?" not in t["summary"]  # only the new reply, not the old page text
    client.post("/api/conversations/conv-chat/end", headers=UI)


# ============================================================== never delete things the user didn't ask to delete

AMAZON_LIKE_CART = [
    {"idx": 1, "tag": "input", "input_type": "submit", "value": "Delete", "accessible_name": "Delete Nike Revolution 7 Running Shoes",
     "bounding_box": {"y": 300}},
    {"idx": 2, "tag": "input", "input_type": "submit", "value": "Save for later", "accessible_name": "Save for later", "bounding_box": {"y": 300}},
    {"idx": 3, "tag": "button", "accessible_name": "Remove from cart", "bounding_box": {"y": 320}},
    {"idx": 4, "tag": "a", "accessible_name": "Sign Out", "landmark": "header", "bounding_box": {"y": 10}},
    {"idx": 5, "tag": "input", "input_type": "submit", "value": "Proceed to Buy", "accessible_name": "", "bounding_box": {"y": 120}},
]
AMAZON_LIKE_RESULTS = [
    {"idx": 1, "tag": "a", "accessible_name": "Nike Revolution 7 Running Shoes", "href": "https://shop.test/dp/1", "price_text": "₹2,999",
     "container_text": "Nike Revolution 7 Running Shoes ₹2,999", "landmark": "main", "bounding_box": {"y": 300}},
    {"idx": 2, "tag": "button", "accessible_name": "Add to cart", "container_text": "Nike Revolution 7 ₹2,999", "bounding_box": {"y": 340}},
    {"idx": 3, "tag": "a", "accessible_name": "Nike Downshifter 12 Running Shoes", "href": "https://shop.test/dp/2", "price_text": "₹2,499",
     "container_text": "Nike Downshifter 12 Running Shoes ₹2,499", "landmark": "main", "bounding_box": {"y": 500}},
    {"idx": 4, "tag": "button", "accessible_name": "Add to cart", "container_text": "Nike Downshifter ₹2,499", "bounding_box": {"y": 540}},
]


def test_shortlist_requests_never_cart_or_buy_and_destructive_controls_are_never_targets():
    goal = "check for nike running shoes on amazon and pick top 3 for me and ask me which one to buy"
    intents = parse_goal_intents(goal)
    assert intents["search_query"] == "nike running shoes" and intents["defer_choice"] is True
    assert intents["wants_checkout"] is False and intents["wants_cart"] is False
    on_results = llm_brain.semantic_decide(goal, "https://shop.test/s?k=nike", "Results",
                                           [{"action": "FILL_AND_SUBMIT", "phase": "search", "url_after": "https://shop.test/s?k=nike"}],
                                           AMAZON_LIKE_RESULTS, "")
    assert on_results["action"] == "DONE"  # list the options and ask — no "Add to cart"
    # "Buy it" on a cart page: approval for Proceed to Buy (read from the input's value), never Delete / Remove.
    buy = llm_brain.semantic_decide("buy the nike running shoes", "https://shop.test/cart", "Cart", [], AMAZON_LIKE_CART, "")
    assert buy["action"] == "ASK_HUMAN_APPROVAL" and buy["element_idx"] == 5
    assert llm_brain.semantic_decide("add nike running shoes to my cart", "https://shop.test/cart", "Cart", [], AMAZON_LIKE_CART,
                                     "").get("element_idx") not in (1, 3, 4)
    annotated = {e["idx"]: e for e in llm_brain.annotate_elements(AMAZON_LIKE_CART)}
    assert annotated[3]["is_cart_action"] is False and annotated[3]["is_destructive_action"] is True  # "Remove from cart"
    assert llm_brain.destructive_allowed("remove the nike shoes from my cart") is True
    assert llm_brain.destructive_allowed(goal) is False


@pytest.mark.asyncio
async def test_the_model_cannot_click_delete_for_a_buying_goal(monkeypatch):
    monkeypatch.setattr(llm_brain, "http_transport", _ollama({"action": "CLICK", "element_idx": 1, "text": None, "reason": "matches the shoes"}))
    monkeypatch.setattr(llm_brain.brain, "ollama", OllamaProvider())
    monkeypatch.setenv("WEBPILOT_BRAIN_PROVIDER", "ollama")
    out = await decide_next_browser_action("buy the nike running shoes", "https://shop.test/cart", "Cart", [], AMAZON_LIKE_CART, "")
    assert out["element_idx"] == 5 and "destructive action ignored" in out["llm_note"]


def test_shortlist_conversation_on_a_store_lists_options_and_asks_which_to_buy(mcp, client):
    t = _run(mcp, "check for shoes on this store and pick top 3 for me and ask me which one to buy", "conv-shortlist",
             "/sandbox/strideway")
    assert t["status"] == "COMPLETED", t["summary"]
    assert "Which one would you like me to buy?" in t["summary"]
    assert t["pending_approval"] is None
    assert client.get("/sandbox/strideway/bag.json").json()["count"] == 0  # nothing added, nothing removed
    client.post("/api/conversations/conv-shortlist/end", headers=UI)
