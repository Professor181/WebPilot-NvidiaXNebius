"""Universal, site-agnostic live browser autopilot: intents, decisions, safety and real-Chromium runs on
fixture sites the engine has never seen (Paperlane, ShopMart, Findr) plus a live public website."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import httpx
import pytest

from backend import config, live_autopilot, llm_brain
from backend.llm_brain import OllamaProvider, decide_next_browser_action, parse_goal_intents, semantic_decide
from backend.planner_engine import compile_goal
from backend.tests.conftest import approve, artifact_path

PAPERLANE_GOAL = ("Go to Paperlane, find the book Designing Data-Intensive Applications, put it in my tote and check out "
                  "after my approval")
SHOPMART_GOAL = ("Search for US Polo sneakers on ShopMart, open the first result, add it to cart and proceed to checkout "
                 "after my approval")


def _autopilot(mcp, goal, start_url=None, max_steps=8, **extra):
    args = {"goal": goal, "max_steps": max_steps, **extra}
    if start_url:
        args["start_url"] = start_url
    result = mcp.call("run_live_web_autopilot", args)
    assert result["isError"] is False, result
    return result["structuredContent"]["task"]


def _film(task):
    return task["extracted_data"]["autopilot"]["filmstrip"]


# =================================================================== zero hardcoding

def test_engine_source_contains_no_site_specific_rules():
    sources = inspect.getsource(live_autopilot) + inspect.getsource(llm_brain.semantic_decide) + \
        inspect.getsource(llm_brain.parse_goal_intents) + inspect.getsource(llm_brain.annotate_elements)
    for site in ("amazon", "flipkart", "wikipedia", "github", "leetcode", "myntra", "twotabsearchtextbox", "field-keywords",
                 "paperlane", "shopmart", "qx-field"):
        assert site not in sources.lower(), f"site-specific rule found: {site}"


def test_goal_intents_are_extracted_generically():
    amazon = parse_goal_intents("Go to amazon.in, search for US Polo sneakers for men, open the first result, and add to cart")
    assert amazon["explicit_url"] == "https://amazon.in" and amazon["search_query"] == "US Polo sneakers for men"
    assert amazon["wants_cart"] and not amazon["needs_discovery"]
    flip = parse_goal_intents("Search for mechanical keyboards on Flipkart")
    assert flip["explicit_url"] is None and flip["site_hint"] == "Flipkart" and flip["needs_discovery"]
    assert flip["discovery_query"] == "Flipkart" and flip["search_query"] == "mechanical keyboards"
    web = parse_goal_intents("Find the creator of FastAPI on the web")
    assert web["site_hint"] is None and web["search_query"] == "creator of FastAPI" and web["needs_discovery"]
    follow = parse_goal_intents("Go to github.com, search for modelcontextprotocol servers, open the repository, then open its Issues tab")
    assert follow["follow_ups"] == ["Issues"] and follow["explicit_url"] == "https://github.com"
    pl = parse_goal_intents(PAPERLANE_GOAL)
    assert pl["search_query"] == "Designing Data-Intensive Applications" and pl["wants_cart"] and pl["wants_checkout"]


def test_live_goals_route_to_the_autopilot_without_site_tables():
    g = compile_goal("Search for mechanical keyboards on Trekmart")  # unknown site → web-search discovery
    assert g["scenario"] == "live_autopilot" and g["constraints"]["discovery"] is True
    assert g["target_url"] == config.AUTOPILOT_SEARCH_URL
    a = compile_goal("Go to amazon.in, search for US Polo sneakers for men and add the first one to my cart")
    assert a["scenario"] == "live_autopilot" and a["target_url"] == "https://amazon.in"
    assert a["task_category"] == "ACT_WITH_APPROVAL"
    assert compile_goal("look up neural networks on the web")["scenario"] == "live_autopilot"
    # Built-in sandbox scenarios and single-page inspections keep their deterministic workflows.
    assert compile_goal("Compare 16GB RAM laptops under 80000 rupees and buy the best one")["scenario"] == "laptop_shopping"
    assert compile_goal("Inspect https://example.com")["scenario"] == "url_inspection"
    assert compile_goal("anything at all", parameters={"live_autopilot": True})["scenario"] == "live_autopilot"


# ================================================================ semantic decisions

UNSEEN_PAGE = [
    {"idx": 1, "tag": "a", "role": "", "accessible_name": "Home", "href": "https://x.test/", "landmark": "nav", "bounding_box": {"y": 10, "w": 40}},
    {"idx": 2, "tag": "input", "input_type": "email", "accessible_name": "Your email for deals", "landmark": "footer", "bounding_box": {"y": 900, "w": 200}},
    {"idx": 3, "tag": "div", "role": "combobox", "contenteditable": True, "accessible_name": "What are you looking for?",
     "landmark": "header", "bounding_box": {"y": 40, "w": 420}},
    {"idx": 4, "tag": "a", "accessible_name": "Sponsored: Cheap hiking gear", "href": "https://x.test/p/ad",
     "container_text": "Sponsored Cheap hiking gear ₹99", "landmark": "main", "bounding_box": {"y": 300, "w": 300}},
    {"idx": 5, "tag": "a", "accessible_name": "Trailblazer 40L Hiking Backpack", "href": "https://x.test/p/40",
     "container_text": "Trailblazer 40L Hiking Backpack ₹2,499", "price_text": "₹2,499", "landmark": "main", "bounding_box": {"y": 380, "w": 300}},
    {"idx": 6, "tag": "button", "accessible_name": "Stash in basket", "landmark": "main", "bounding_box": {"y": 500, "w": 120}},
    {"idx": 7, "tag": "button", "accessible_name": "Proceed to checkout", "landmark": "main", "bounding_box": {"y": 560, "w": 160}},
    {"idx": 8, "tag": "button", "accessible_name": "Pay ₹2,499 now", "landmark": "main", "bounding_box": {"y": 620, "w": 160}},
]
HIKE_GOAL = "Search for hiking backpack on Trekmart, open the first result, put it in my basket and check out after my approval"


def test_semantic_policy_walks_an_unseen_site():
    d1 = semantic_decide(HIKE_GOAL, "https://x.test/", "Trekmart", [], UNSEEN_PAGE, "")
    assert d1["action"] == "FILL_AND_SUBMIT" and d1["element_idx"] == 3 and d1["text"] == "hiking backpack"
    h = [{"action": "FILL_AND_SUBMIT", "phase": "search", "url_after": "https://x.test/s"}]
    d2 = semantic_decide(HIKE_GOAL, "https://x.test/s", "Results", h, UNSEEN_PAGE, "")
    assert d2["action"] == "CLICK" and d2["element_idx"] == 5  # sponsored #4 and nav #1 skipped
    h.append({"action": "CLICK", "phase": "open_result", "url_after": "https://x.test/p/40"})
    d3 = semantic_decide(HIKE_GOAL, "https://x.test/p/40", "Backpack", h, UNSEEN_PAGE, "")
    assert d3["action"] == "CLICK" and d3["element_idx"] == 6 and d3["phase"] == "cart"
    h.append({"action": "CLICK", "phase": "cart", "url_after": "https://x.test/p/40"})
    d4 = semantic_decide(HIKE_GOAL, "https://x.test/p/40", "Backpack", h, UNSEEN_PAGE, "")
    assert d4["action"] == "ASK_HUMAN_APPROVAL" and d4["element_idx"] == 7  # commit tier preferred over final payment


def test_ecommerce_actions_outrank_destructive_and_unrelated_controls():
    controls = [
        {"idx": 1, "tag": "button", "accessible_name": "Delete", "bounding_box": {"y": 10}},
        {"idx": 2, "tag": "button", "text": "Remove", "bounding_box": {"y": 20}},
        {"idx": 3, "tag": "button", "aria_label": "Sign Out", "bounding_box": {"y": 30}},
        {"idx": 4, "tag": "button", "accessible_name": "Clear", "bounding_box": {"y": 40}},
        {"idx": 5, "tag": "button", "accessible_name": "Cancel", "bounding_box": {"y": 50}},
        {"idx": 6, "tag": "button", "accessible_name": "Put in my tote", "bounding_box": {"y": 100}},
        {"idx": 7, "tag": "input", "input_type": "submit", "value": "Add to Cart",
         "accessible_name": "Product option", "bounding_box": {"y": 400}},
    ]
    cart = semantic_decide("Add this product to my cart", "https://shop.test/product", "Product", [], controls, "")
    assert cart["action"] == "CLICK" and cart["element_idx"] == 7

    checkout = semantic_decide(
        "Buy this item now", "https://shop.test/checkout", "Checkout", [],
        [{"idx": 1, "tag": "button", "accessible_name": "Submit"},
         {"idx": 2, "tag": "button", "accessible_name": "Order control", "aria_label": "Place Order"}], "")
    assert checkout["action"] == "ASK_HUMAN_APPROVAL" and checkout["element_idx"] == 2

    search = semantic_decide(
        "Search for hiking backpack", "https://shop.test/search", "Results",
        [{"action": "FILL_AND_SUBMIT", "phase": "search", "url_after": "https://shop.test/search"}],
        [{"idx": 1, "tag": "a", "accessible_name": "Hiking Backpack", "aria_label": "Delete"},
         {"idx": 2, "tag": "a", "accessible_name": "Hiking Backpack product"}], "")
    assert search["action"] == "CLICK" and search["element_idx"] == 2


def test_live_dom_indexer_reads_submit_button_value_attribute():
    assert "el.getAttribute('value')" in live_autopilot.INDEXER_JS
    assert "aria_label:" in live_autopilot.INDEXER_JS


def test_consequence_classifier_and_barriers():
    assert llm_brain.classify_consequence("Place your order") == "final_payment"
    assert llm_brain.classify_consequence("Pay ₹2,499 now") == "final_payment"
    assert llm_brain.classify_consequence("Proceed to checkout") == "commit"
    assert llm_brain.classify_consequence("Submit application") == "commit"
    assert llm_brain.classify_consequence("Add to Cart") is None
    assert llm_brain.classify_consequence("Put in my tote") is None
    blocked = semantic_decide("search for x on Foo", "https://f.test/", "Check", [], UNSEEN_PAGE[:2],
                              "Please verify you are human to continue")
    assert blocked["action"] == "BLOCKED"


def _ollama(payload, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("llm,expected_action,expected_by", [
    ({"action": "CLICK", "element_idx": 7, "text": None, "reason": "go to checkout"}, "ASK_HUMAN_APPROVAL", "ollama_local"),
    ({"action": "CLICK", "element_idx": 99, "text": None, "reason": "hallucinated"}, "FILL_AND_SUBMIT", "semantic_fallback"),
    ({"action": "DONE", "element_idx": None, "text": None, "reason": "too early"}, "FILL_AND_SUBMIT", "semantic_fallback"),
    ({"action": "FILL_AND_SUBMIT", "element_idx": 3, "text": "hiking backpack 40L", "reason": "search"}, "FILL_AND_SUBMIT", "ollama_local"),
])
@pytest.mark.asyncio
async def test_llm_decisions_are_verified_by_the_semantic_policy(monkeypatch, llm, expected_action, expected_by):
    calls = []
    monkeypatch.setattr(llm_brain, "http_transport", _ollama(llm, calls))
    monkeypatch.setattr(llm_brain.brain, "ollama", OllamaProvider())
    monkeypatch.setenv("WEBPILOT_BRAIN_PROVIDER", "ollama")
    out = await decide_next_browser_action(HIKE_GOAL, "https://x.test/", "Trekmart", [], UNSEEN_PAGE, "")
    assert out["action"] == expected_action
    assert out["decided_by"].startswith(expected_by)
    assert "<untrusted_web_content" in calls[0]["messages"][1]["content"]
    if expected_by == "semantic_fallback":
        assert "rejected" in out["llm_note"]


@pytest.mark.asyncio
async def test_llm_cannot_select_destructive_control_for_cart_goal(monkeypatch):
    calls = []
    proposal = {"action": "CLICK", "element_idx": 1, "text": None, "reason": "remove item"}
    monkeypatch.setattr(llm_brain, "http_transport", _ollama(proposal, calls))
    monkeypatch.setattr(llm_brain.brain, "ollama", OllamaProvider())
    monkeypatch.setenv("WEBPILOT_BRAIN_PROVIDER", "ollama")
    elements = [
        {"idx": 1, "tag": "button", "accessible_name": "Delete"},
        {"idx": 2, "tag": "button", "accessible_name": "Other action", "text": "Add to Cart"},
    ]
    out = await decide_next_browser_action("Add this product to my cart", "https://shop.test/product", "Product",
                                           [], elements, "")
    prompt = calls[0]["messages"][0]["content"]
    assert "never select a control labeled Delete" in prompt
    assert out["action"] == "CLICK" and out["element_idx"] == 2
    assert out["decided_by"] == "semantic_fallback" and "destructive action ignored" in out["llm_note"]


# ============================================================= real Chromium runs

def test_unseen_bookshop_search_click_tote_and_approval_gate(mcp, client):
    task = _autopilot(mcp, PAPERLANE_GOAL, "/sandbox/paperlane")
    assert task["status"] == "AWAITING_APPROVAL", task["summary"]
    film = _film(task)
    assert [f["action"] for f in film] == ["NAVIGATE", "FILL_AND_SUBMIT", "CLICK", "CLICK", "ASK_HUMAN_APPROVAL"]
    assert "Designing Data-Intensive Applications" in film[1]["description"]
    assert "Put in my tote" in film[3]["description"]
    for f in film:
        png = artifact_path(f["screenshot_url"])
        assert png.exists() and png.read_bytes()[:4] == b"\x89PNG"
    approval = task["pending_approval"]
    params = approval["parameters"]
    assert params["button_text"] == "Checkout securely" and params["consequence_tier"] == "commit"
    assert params["item_heading"] == "Designing Data-Intensive Applications" and params["visible_prices"] == [1299.0]
    assert approval["action_type"] == "PURCHASE_CHECKOUT"
    assert client.get("/sandbox/paperlane/tote.json").json()["count"] == 1  # the safe cart step really happened
    evidence = mcp.call("get_execution_evidence", {"task_id": task["task_id"]})["structuredContent"]["evidence"]
    assert any(a.get("matched_patterns") for e in evidence for a in e["firewall_alerts"])  # hidden trap on results page
    assert task["task_id"] in live_autopilot.SESSIONS  # the live session waits for the approval

    token = approve(client, approval["approval_id"])["approval_token"]
    done = mcp.call("execute_approved_step", {"task_id": task["task_id"], "approval_id": approval["approval_id"], "approval_token": token})
    assert done["isError"] is False, done
    final = done["structuredContent"]["task"]
    assert final["status"] == "COMPLETED"
    report = final["verification_report"]
    assert report["verdict"] == "LIVE_CHECKOUT_HANDOFF_VERIFIED"
    landing = final["extracted_data"]["outcome"]["landing"]
    assert landing["payment_step"] is True and any("Pay" in b for b in landing["final_payment_controls"])
    assert final["extracted_data"]["outcome"]["handoff_url"].endswith("/sandbox/paperlane/checkout")
    assert "Finish the payment yourself" in final["summary"]
    assert task["task_id"] not in live_autopilot.SESSIONS  # headless session closed after the handoff


def test_price_drift_on_a_live_page_revokes_the_approval(mcp, client):
    task = _autopilot(mcp, PAPERLANE_GOAL, "/sandbox/paperlane")
    approval_id = task["pending_approval"]["approval_id"]
    token = approve(client, approval_id)["approval_token"]
    client.post("/sandbox/paperlane/simulate-price-change?enabled=true", headers={"Origin": "http://localhost:3000"})
    # The live page shows the old price until it is re-rendered; reload it the way a real user's tab would update.
    session = live_autopilot.SESSIONS[task["task_id"]]
    client.portal.call(session.page.reload)
    out = mcp.call("execute_approved_step", {"task_id": task["task_id"], "approval_id": approval_id, "approval_token": token})
    assert out["isError"] is True and out["structuredContent"]["error_code"] == "DOM_STATE_DRIFT_DETECTED"
    assert out["structuredContent"]["details"]["drift"]["visible_prices"] == {"approved": [1299.0], "live": [1599.0]}
    state = mcp.call("get_task_status", {"task_id": task["task_id"]})["structuredContent"]["task"]
    assert state["status"] == "AWAITING_USER_INPUT" and "did not click anything" in state["summary"]


def test_different_store_layout_cookie_banner_sponsored_and_cart(mcp, client):
    task = _autopilot(mcp, SHOPMART_GOAL, "/sandbox/shopmart")
    assert task["status"] == "AWAITING_APPROVAL", task["summary"]
    film = _film(task)
    assert "Reject non-essential" in film[0]["description"]  # privacy-preserving consent choice
    opened = next(f for f in film if f["action"] == "CLICK")
    assert "Clarkin" in opened["description"] and "Sparx" not in opened["description"]  # sponsored result skipped
    assert any("Add to Cart" in f["description"] for f in film)
    assert task["pending_approval"]["parameters"]["button_text"] == "Proceed to Buy"
    assert client.get("/sandbox/shopmart/cart.json").json()["count"] == 1


def test_site_discovery_through_web_search_then_listing_answer(mcp, monkeypatch):
    monkeypatch.setattr(config, "AUTOPILOT_SEARCH_URL", "/sandbox/findr")
    task = _autopilot(mcp, "Search for distributed systems books on Paperlane and tell me the cheapest one", max_steps=6)
    assert task["status"] == "COMPLETED", task["summary"]
    film = _film(task)
    assert film[0]["url_after"].endswith("/sandbox/findr")
    assert "Paperlane" in film[1]["description"]  # typed the site name into the web search engine
    assert film[2]["url_after"].endswith("/sandbox/paperlane") and "Ad" not in film[2]["description"]
    assert "distributed systems books" in film[3]["description"]
    answer = task["extracted_data"]["answer"]
    assert answer["plan"]["rank_by"] == "price_asc"
    assert answer["citations"][0]["title"] == "Understanding Distributed Systems"
    assert "Gardening" not in answer["answer"]
    assert task["verification_report"]["verdict"] == "VERIFIED_SUCCESS"


def test_login_wall_ends_blocked_without_bypass(mcp):
    task = _autopilot(mcp, "Go to the portal and search for my payslip", "/sandbox/login-wall", max_steps=4)
    assert task["status"] == "BLOCKED"
    assert task["extracted_data"]["blocker"]["bypass_attempted"] is False
    assert _film(task)[-1]["action"] == "BLOCKED"
    assert task["task_id"] not in live_autopilot.SESSIONS


def test_private_network_start_urls_are_refused(mcp):
    task = _autopilot(mcp, "Go to the router admin page and search for settings", "http://10.0.0.5/admin", max_steps=3)
    assert task["status"] == "FAILED" and "private or local network" in task["summary"]


def test_cancel_closes_the_waiting_browser_session(mcp):
    task = _autopilot(mcp, PAPERLANE_GOAL, "/sandbox/paperlane")
    assert task["task_id"] in live_autopilot.SESSIONS
    out = mcp.call("cancel_web_task", {"task_id": task["task_id"]})
    assert out["structuredContent"]["task"]["status"] == "CANCELLED"
    mcp.call("get_task_status", {"task_id": task["task_id"]})  # lets the scheduled close run
    assert task["task_id"] not in live_autopilot.SESSIONS


@pytest.mark.network
def test_live_public_site_multi_step_search_and_summary(mcp):
    try:
        httpx.get("https://www.wikipedia.org/", timeout=8)
    except Exception:
        pytest.skip("No internet access to wikipedia.org")
    task = _autopilot(mcp, "Go to wikipedia.org, search for Model Context Protocol and summarize the article", max_steps=6)
    assert task["status"] == "COMPLETED", task["summary"]
    film = _film(task)
    assert film[1]["action"] == "FILL_AND_SUBMIT" and "Model Context Protocol" in film[1]["description"]
    assert "/wiki/" in task["extracted_data"]["url"]
    assert task["extracted_data"]["answer"]["citations"]
    assert all(artifact_path(f["screenshot_url"]).exists() for f in film if f["screenshot_url"])
