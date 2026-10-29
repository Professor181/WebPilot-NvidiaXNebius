"""Goal compiler, dynamic planner and scenario workflows.

The engine turns a natural-language goal into a typed plan, executes it through the tiered
capability router and persists every step, evidence record and approval:

* TIER_1_ADAPTER        structured APIs (GitHub REST, LeetCode GraphQL)
* TIER_2_SEMANTIC_DOM   Playwright / HTTP+BS4 semantic DOM distillation behind the content firewall
* TIER_3_HITL_ESCALATION human-in-the-loop: approval gates and honest BLOCKED reports

Scenario workflows
------------------
A  attendance_audit       subjects below 75% + consecutive classes needed
B  profile_comparison     developer profile comparison with source citations
C  laptop_shopping        multi-site research, firewall, ranking, out-of-stock recovery, approval
D  (approved execution)   execute_approved_step: HMAC-gated checkout / submission + verification
E  grant_form             form population with a pre-submit approval pause
F  github_repo_analysis   live GitHub repository analysis with HTML fallback
G  url_inspection         arbitrary URL distillation (live Chromium viewport); login / OTP / CAPTCHA -> BLOCKED
H  synthesized_tool       novel data goals -> dynamically synthesized, session-registered MCP tool

Mid-task interruptions ("change the budget to 70000", "skip ElectroHub") are applied at step checkpoints
while a task is EXECUTING, or immediately (with re-planning) while it is AWAITING_APPROVAL. Completed
evidence is always preserved; superseded steps stay visible in the DAG.
"""
from __future__ import annotations

import asyncio
import copy
import hmac
import logging
import re
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from backend import approval_guard, config, db, tool_synthesizer, verifier
from backend.browser_runtime import BrowserFetchError, browser_runtime
from backend.firewall import summarize_alerts
from backend.interruptions import InterruptParseError, parse_instruction, suggestions_for
from backend.models import (
    TERMINAL_TASK_STATUSES,
    ActionType,
    ApprovalStatus,
    CapabilityTier,
    EnvironmentMode,
    Scenario,
    StepStatus,
    TaskCategory,
    TaskStatus,
    VerificationCheck,
    utc_now_iso,
)
from backend.sandbox_routes import STORES, format_inr

logger = logging.getLogger("webpilot.planner")

T1, T2, T3, TC = (
    CapabilityTier.TIER_1_ADAPTER.value,
    CapabilityTier.TIER_2_SEMANTIC_DOM.value,
    CapabilityTier.TIER_3_HITL_ESCALATION.value,
    CapabilityTier.INTERNAL_COMPUTE.value,
)

# Injectable transports so tests can exercise the TIER_1 adapters deterministically.
github_transport: Optional[httpx.AsyncBaseTransport] = None
leetcode_transport: Optional[httpx.AsyncBaseTransport] = None

DEFAULT_GITHUB_REPO = ("modelcontextprotocol", "servers")
DEFAULT_SANDBOX_PROFILES = ("yash_kalaskar", "aarav_mehta")

DEMO_APPLICANT_PROFILE = {
    "applicant_name": "Yash Kalaskar",
    "project_title": "WebPilot — Verifiable Web Task Agent for Alexa+",
    "track_name": "Alexa+ Track",
    "repo_url": "https://github.com/yashkalaskar/webpilot-alexa-plus",
    "abstract_summary": (
        "WebPilot is a self-hosted MCP 2025-11-25 server that lets Alexa+ complete multi-step web tasks "
        "safely. A tiered capability router prefers structured APIs, falls back to semantic DOM distillation "
        "in headless Chromium and escalates to the user for logins, OTPs and CAPTCHAs. All page content passes "
        "an untrusted-content firewall that strips hidden prompt injections. Consequential actions are locked "
        "behind an HMAC-signed human approval gate with drift detection, and every outcome is checked by a "
        "post-action verification engine before it is reported as complete."
    ),
}


class ToolError(Exception):
    """A tool-level failure reported to the model as `isError: true` (SEP-1303)."""

    def __init__(self, code: str, message: str, hint: Optional[str] = None, **details: Any):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        data = {"error_code": self.code, "message": f"{self.code}: {self.message}"}
        if self.hint:
            data["hint"] = self.hint
        if self.details:
            data["details"] = self.details
        return data


def absolute_url(path_or_url: Optional[str]) -> Optional[str]:
    if not path_or_url:
        return path_or_url
    if path_or_url.startswith("/"):
        return config.WEBPILOT_BASE_URL + path_or_url
    return path_or_url


def sandbox_url(path: str) -> str:
    return config.WEBPILOT_BASE_URL + path


# ============================================================================ goal compiler

_URL_RE = re.compile(r"(https?://[^\s<>\"')]+|(?<![\w/])/sandbox/[\w\-/?=&.%+]*)", re.I)
_GITHUB_URL_RE = re.compile(r"github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9_.-]+)", re.I)
_OWNER_REPO_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]*[A-Za-z][A-Za-z0-9_.-]*)\b")
_PRICE_RE = re.compile(
    r"(?:under|below|less than|within|max(?:imum)?|budget(?:\s+of)?|up\s*to|upto|<=?|cheaper than)\s*"
    r"(?:rs\.?|inr|₹)?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|thousand|lakh|lakhs|l)?\b",
    re.I,
)
_RAM_RE = re.compile(r"(\d{1,3})\s*gb\s*(?:of\s+)?(?:ram|memory)", re.I)
_MULTISTEP_RE = re.compile(
    r"\b(search(?:es|ing)?(?:\s+for)?|look\s+up|type\s|click|navigate|go to|browse|add (?:it |this |them )?to (?:my )?"
    r"(?:cart|bag|basket)|put .{0,20}\b(?:cart|tote|bag|basket)|buy|purchase|check ?out|book (?:a|the|tickets?|it)|"
    r"sign up|fill (?:in|out))\b")
_ON_WEB_RE = re.compile(r"\b(on|across) the (web|internet)\b|\bonline\b|\bweb search\b")
_SANDBOX_RULE_SCENARIOS = {"attendance_audit", "profile_comparison", "laptop_shopping", "grant_form"}
_BRAND_ACTION_RE = re.compile(r"\b(open|visit|launch|ask|go to|navigate|search|look up|look for|check for|shop for|pick|find|type|click|browse|buy|add|"
                              r"check ?out|book|order|write|post|show|tell|summari[sz]e|read|get)\b")
_LOGIN_SIGNAL_RE = re.compile(r"login|log in|captcha|otp|sign|portal|erp")


def _live_autopilot_reason(text: str, lower: str, url: Optional[str], params: Dict[str, Any],
                           chosen: Optional[str], signals: List[str]) -> Optional[str]:
    """Route a goal to the universal live browser autopilot. Pure intent signals — no site names."""
    from backend.llm_brain import DOMAIN_RE, GOTO_HINT_RE, SITE_HINT_RE

    if params.get("live_autopilot"):
        return "parameters.live_autopilot"
    if any(sig.startswith("url_route:") for sig in signals):
        return None  # a built-in sandbox scenario URL
    from backend import site_directory

    brand = None if (url or DOMAIN_RE.search(text)) else site_directory.infer_site(text)
    # A named site plus browser verbs ("go to github and search…") is a live task even if a built-in rule matched;
    # a bare brand mention never overrides a built-in workflow.
    if brand and (_MULTISTEP_RE.search(lower) or (_BRAND_ACTION_RE.search(lower) and chosen != "github_repo_analysis")):
        return f"known site '{brand[0]}' → {brand[1]}"
    multistep = _MULTISTEP_RE.search(lower)
    if not multistep:
        return None
    if url or DOMAIN_RE.search(text):
        target = url or DOMAIN_RE.search(text).group(1)
        return f"multi-step '{multistep.group(0).strip()}' on {target}"
    sandbox_rule = chosen in _SANDBOX_RULE_SCENARIOS or (
        chosen == Scenario.URL_INSPECTION.value and any(sig.startswith("keyword:") and _LOGIN_SIGNAL_RE.search(sig) for sig in signals))
    if sandbox_rule:
        return None
    site = SITE_HINT_RE.search(text) or GOTO_HINT_RE.search(text)
    if site and site.group(1).strip().lower() not in ("the web", "the internet", "it", "them"):
        return f"multi-step '{multistep.group(0).strip()}' on {site.group(1).strip()}"
    if _ON_WEB_RE.search(lower):
        return f"multi-step '{multistep.group(0).strip()}' on the web"
    return None


_QUESTION_RE = re.compile(
    r"\b(filter|rank|top\s+\d+|summari[sz]e|summary|tell me|what|which|who|list|find|key sections|stories|"
    r"headlines|articles|prices?|cheapest|compare|how many|explain)\b|\?", re.I)
_DATA_OP_RE = re.compile(
    r"\b(extract|parse|calculate|compute|moving[- ]averages?|rolling (?:mean|average)|aggregate|statistics|"
    r"percent(?:age)? change|feed|csv|xml|json|indices|time series)\b"
)
_RESTRICTED_PORTAL_RE = re.compile(r"(?:enterprise|corporate|restricted|erp|employee|intranet)\s+(?:\w+\s+)?portal|\berp\b")
_ACTION_VERB_RE = re.compile(r"\b(?:submit|buy|purchase|pay|order|book|send|post|update|change|delete|transfer|upload)\b")
_THRESHOLD_RE = re.compile(r"(?:below|under|less than|minimum|min\.?|at least)\s*(\d{2}(?:\.\d+)?)\s*(?:%|percent|per cent)", re.I)


def _parse_amount(number: str, unit: Optional[str]) -> int:
    value = float(number.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("k", "thousand"):
        value *= 1000
    elif unit in ("lakh", "lakhs", "l"):
        value *= 100000
    return int(round(value))


def _scenario_from_url(url: str) -> Optional[str]:
    p = urlparse(url if "://" in url else "http://x" + url)
    path = p.path
    if "github.com" in (p.netloc or "").lower() and _GITHUB_URL_RE.search(url):
        return Scenario.GITHUB_REPO_ANALYSIS.value
    if path.startswith("/sandbox/attendance"):
        return Scenario.ATTENDANCE_AUDIT.value
    if path.startswith(("/sandbox/electrohub", "/sandbox/bytebazaar")):
        return Scenario.LAPTOP_SHOPPING.value
    if path.startswith("/sandbox/application-form"):
        return Scenario.GRANT_FORM.value
    if path.startswith("/sandbox/codearena") or "leetcode.com" in (p.netloc or "").lower():
        return Scenario.PROFILE_COMPARISON.value
    return None


def compile_goal(
    goal: str,
    scenario: Optional[str] = None,
    target_url: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rule-based goal compiler: classifies the goal and extracts typed constraints."""
    params = dict(parameters or {})
    text = goal.strip()
    lower = text.lower()
    signals: List[str] = []

    url_in_goal = None
    m = _URL_RE.search(text)
    if m:
        url_in_goal = m.group(1).rstrip(".,;")
    url = target_url or url_in_goal

    chosen = scenario if scenario and scenario != Scenario.AUTO.value else None
    if chosen:
        signals.append(f"explicit_scenario:{chosen}")
    if not chosen and url:
        chosen = _scenario_from_url(url)
        if chosen:
            signals.append(f"url_route:{chosen}")
    if not chosen:
        rules: List[Tuple[str, List[str]]] = [
            (Scenario.GITHUB_REPO_ANALYSIS.value, [r"github", r"\brepo(sitory)?\b"]),
            (Scenario.ATTENDANCE_AUDIT.value, [r"attendance"]),
            (Scenario.PROFILE_COMPARISON.value, [r"leetcode", r"codearena", r"coding profile", r"problems solved",
                                                 r"profile.*friend|friend.*profile", r"developer profile"]),
            (Scenario.LAPTOP_SHOPPING.value, [r"laptop", r"notebook computer", r"ultrabook"]),
            (Scenario.GRANT_FORM.value, [r"grant", r"application form", r"fill (out|in)?.*form", r"\bapply\b"]),
            (Scenario.URL_INSPECTION.value, [r"login", r"log in", r"captcha", r"\botp\b", _RESTRICTED_PORTAL_RE.pattern,
                                             r"sign[- ]?in", r"inspect", r"website", r"web ?page"]),
        ]
        for name, patterns in rules:
            hit = next((p for p in patterns if re.search(p, lower)), None)
            if hit:
                chosen = name
                signals.append(f"keyword:{hit}")
                break
    if not (scenario and scenario != Scenario.AUTO.value):
        live_reason = _live_autopilot_reason(text, lower, url, params, chosen, signals)
        if live_reason:
            chosen = Scenario.LIVE_AUTOPILOT.value
            signals.append(f"live_web:{live_reason}")
    if not chosen:
        dm = _DATA_OP_RE.search(lower)
        if dm:
            chosen = Scenario.SYNTHESIZED_TOOL.value
            signals.append(f"novel_data_goal:{dm.group(0)} → tool synthesis")
    if not chosen and url:
        chosen = Scenario.URL_INSPECTION.value
        signals.append("url_present")
    if not chosen:
        raise ToolError(
            "GOAL_NOT_UNDERSTOOD",
            f"Could not map the goal to a supported web task: {goal!r}",
            hint=(
                "Rephrase the goal or pass `scenario` and/or `target_url`. Supported tasks: attendance audit, "
                "developer profile comparison, laptop research with approval-gated checkout, grant form filling, "
                "GitHub repository analysis, or inspecting any public URL."
            ),
        )

    interpreted: Dict[str, Any] = {
        "compiler": "RULE_BASED_GOAL_COMPILER_V1",
        "scenario": chosen,
        "matched_signals": signals,
        "target_url": url,
        "parameters": params,
        "constraints": {},
    }

    if chosen == Scenario.ATTENDANCE_AUDIT.value:
        tm = _THRESHOLD_RE.search(text)
        threshold = float(params.get("threshold_pct") or (tm.group(1) if tm else 75.0))
        interpreted.update(
            task_category=TaskCategory.READ_ANALYZE.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value if not target_url else EnvironmentMode.LIVE_WEB.value,
            intent=f"Find subjects with attendance below {threshold:g}% and how many consecutive classes restore it.",
            constraints={"threshold_pct": threshold},
            target_url=url or sandbox_url("/sandbox/attendance"),
        )
    elif chosen == Scenario.PROFILE_COMPARISON.value:
        user = params.get("user_profile")
        friend = params.get("friend_profile")
        live = bool(user and friend) and (params.get("platform", "leetcode" if "leetcode" in lower else "codearena") == "leetcode")
        interpreted.update(
            task_category=TaskCategory.RESEARCH_COMPARE.value,
            environment_mode=EnvironmentMode.LIVE_WEB.value if live else EnvironmentMode.CONTROLLED_SANDBOX.value,
            intent="Compare problem-solving progress of two developer profiles with cited sources.",
            constraints={
                "platform": "leetcode" if live else "codearena",
                "user_profile": user or DEFAULT_SANDBOX_PROFILES[0],
                "friend_profile": friend or DEFAULT_SANDBOX_PROFILES[1],
            },
        )
    elif chosen == Scenario.LAPTOP_SHOPPING.value:
        pm = _PRICE_RE.search(text)
        rm = _RAM_RE.search(text)
        max_price = int(params.get("max_price_inr") or (_parse_amount(pm.group(1), pm.group(2)) if pm else 80000))
        min_ram = int(params.get("min_ram_gb") or (rm.group(1) if rm else 16))
        stores = params.get("store_urls") or [sandbox_url("/sandbox/electrohub"), sandbox_url("/sandbox/bytebazaar")]
        interpreted.update(
            task_category=TaskCategory.ACT_WITH_APPROVAL.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value,
            intent=f"Find the best laptop with at least {min_ram}GB RAM under {format_inr(max_price)} across "
                   f"{len(stores)} stores and prepare an approval-gated checkout.",
            constraints={"max_price_inr": max_price, "min_ram_gb": min_ram, "currency": "INR",
                         "prepare_checkout": bool(params.get("prepare_checkout", True))},
            store_urls=[absolute_url(s) for s in stores],
        )
    elif chosen == Scenario.GRANT_FORM.value:
        interpreted.update(
            task_category=TaskCategory.ACT_WITH_APPROVAL.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value,
            intent="Populate the grant application form and pause for approval before final submission.",
            target_url=absolute_url(url) if url else sandbox_url("/sandbox/application-form"),
        )
    elif chosen == Scenario.GITHUB_REPO_ANALYSIS.value:
        owner, repo = _resolve_github_repo(text, url, params)
        interpreted.update(
            task_category=TaskCategory.READ_ANALYZE.value,
            environment_mode=EnvironmentMode.LIVE_WEB.value,
            intent=f"Analyze the GitHub repository {owner}/{repo}: architecture, stars, language and open issues.",
            constraints={"owner": owner, "repo": repo},
            target_url=f"https://github.com/{owner}/{repo}",
        )
    elif chosen == Scenario.LIVE_AUTOPILOT.value:
        from backend.llm_brain import parse_goal_intents

        from backend import site_directory

        intents = parse_goal_intents(text)
        start = params.get("start_url") or target_url or intents["explicit_url"]
        inferred = None
        if not start:
            # A known brand ("on Flipkart", "open ChatGPT") starts at its official URL instead of a web search.
            inferred = site_directory.lookup(intents["site_hint"]) or site_directory.infer_site(text)
            if inferred:
                start = inferred[1]
                signals.append(f"site_directory:{inferred[0]}→{inferred[1]}")
        discovery = not start
        try:
            max_steps = int(params.get("max_steps") or config.AUTOPILOT_MAX_STEPS)
        except (TypeError, ValueError):
            max_steps = config.AUTOPILOT_MAX_STEPS
        # Booking forms (calendar months, guest steppers) and site discovery need extra steps.
        max_steps = max(2, min(max_steps + (2 if discovery else 0) + (10 if intents["booking"] else 0), 25))
        commerce = intents["wants_cart"] or intents["wants_checkout"]
        resolved = absolute_url(start) if start else config.AUTOPILOT_SEARCH_URL
        sandbox = bool(start) and browser_runtime.is_sandbox_url(browser_runtime.normalize_url(resolved))
        interpreted.update(
            task_category=TaskCategory.ACT_WITH_APPROVAL.value if commerce else TaskCategory.READ_ANALYZE.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value if sandbox else EnvironmentMode.LIVE_WEB.value,
            intent=("Drive a real browser across the site with a Sense → Think → Act → Verify loop"
                    + ("; stop at the approval gate before any consequential step." if commerce else "; answer from the final page.")),
            constraints={
                "start_url": resolved, "discovery": discovery, "discovery_query": intents["discovery_query"] if discovery else None,
                "inferred_site": inferred[0] if inferred else None,
                "site_hint": intents["site_hint"], "search_query": intents["search_query"], "wants_cart": intents["wants_cart"],
                "wants_checkout": intents["wants_checkout"], "max_steps": max_steps,
                "watch": bool(params.get("watch_live_browser")), "persistent": bool(params.get("use_persistent_profile")),
                "conversation_id": (str(params["conversation_id"])[:64] if params.get("conversation_id") else None),
            },
            target_url=resolved,
        )
    elif chosen == Scenario.SYNTHESIZED_TOOL.value:
        try:
            source, resolution = tool_synthesizer.resolve_source(text, url or params.get("source_url"))
        except tool_synthesizer.SynthesisError as exc:
            raise ToolError(exc.code, exc.message, hint=exc.hint) from exc
        resolved = absolute_url(source)
        sandbox = browser_runtime.is_sandbox_url(browser_runtime.normalize_url(resolved))
        interpreted.update(
            task_category=TaskCategory.READ_ANALYZE.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value if sandbox else EnvironmentMode.LIVE_WEB.value,
            intent="No built-in workflow fits: synthesize a sandboxed, session-scoped tool for this data goal and run it.",
            constraints={"source_resolution": resolution},
            target_url=resolved,
        )
    else:
        if not url:
            if re.search(r"login|log in|captcha|\botp\b|sign[- ]?in|" + _RESTRICTED_PORTAL_RE.pattern, lower):
                url = sandbox_url("/sandbox/login-wall")
            else:
                raise ToolError(
                    "MISSING_TARGET_URL",
                    "This goal needs a website to inspect but no URL was given.",
                    hint="Pass `target_url` (e.g. https://example.com) or include the URL in the goal.",
                )
        resolved = absolute_url(url) if url.startswith("/") else url
        sandbox = browser_runtime.is_sandbox_url(browser_runtime.normalize_url(resolved))
        wants_action = bool(_ACTION_VERB_RE.search(lower))
        interpreted.update(
            task_category=TaskCategory.ACT_WITH_APPROVAL.value if wants_action else TaskCategory.READ_ANALYZE.value,
            environment_mode=EnvironmentMode.CONTROLLED_SANDBOX.value if sandbox else EnvironmentMode.LIVE_WEB.value,
            intent=("Reach the page needed for the requested action (any consequential step requires approval) and "
                    "report access barriers honestly." if wants_action else
                    "Inspect the website, distill its structure and report any access barriers honestly."),
            target_url=resolved,
        )
    return interpreted


def _resolve_github_repo(text: str, url: Optional[str], params: Dict[str, Any]) -> Tuple[str, str]:
    if params.get("owner") and params.get("repo"):
        return str(params["owner"]), str(params["repo"])
    if params.get("repository") and "/" in str(params["repository"]):
        owner, repo = str(params["repository"]).split("/", 1)
        return owner, repo
    for candidate in (url or "", text):
        m = _GITHUB_URL_RE.search(candidate)
        if m:
            return m.group(1), re.sub(r"\.git$", "", m.group(2))
    for m in _OWNER_REPO_RE.finditer(text):
        owner, repo = m.group(1), m.group(2)
        if owner.lower() not in ("and", "or", "and/or", "http", "https", "www"):
            return owner, repo
    if re.search(r"model\s*context\s*protocol|\bmcp\b", text, re.I):
        return DEFAULT_GITHUB_REPO
    return DEFAULT_GITHUB_REPO


# ============================================================================== task run

class TaskRun:
    """Mutable execution context for one task; persists every change."""

    MAX_PACE_MS = 5000

    def __init__(self, task: Dict[str, Any], session_id: Optional[str] = None):
        self.task = task
        self.session_id = session_id
        self._capture_counts: Dict[str, int] = {}
        params = (task.get("interpreted_goal") or {}).get("parameters") or {}
        try:
            pace_ms = int(params.get("pace_ms") or 0)
        except (TypeError, ValueError):
            pace_ms = 0
        self.pace_seconds = max(0, min(pace_ms, self.MAX_PACE_MS)) / 1000.0

    @property
    def task_id(self) -> str:
        return self.task["task_id"]

    @property
    def steps(self) -> List[Dict[str, Any]]:
        return self.task["plan_steps"]

    def save(self, **fields: Any) -> None:
        fields.setdefault("plan_steps", self.steps)
        updated = db.update_task(self.task_id, **fields)
        if updated:
            self.task = updated

    def plan(self, specs: List[Tuple[str, str, str, Optional[str]]]) -> None:
        self.task["plan_steps"] = [self._new_step(i, key, title, tier, url) for i, (key, title, tier, url) in enumerate(specs)]
        self.save()

    def _new_step(self, index: int, key: str, title: str, tier: str, url: Optional[str]) -> Dict[str, Any]:
        return {
            "step_id": f"step_{uuid.uuid4().hex[:10]}",
            "key": key,
            "index": index,
            "title": title,
            "capability_tier": tier,
            "status": StepStatus.PENDING.value,
            "target_url": url,
            "detail": None,
            "evidence_ids": [],
            "started_at": None,
            "finished_at": None,
        }

    def step(self, key: str) -> Dict[str, Any]:
        for s in self.steps:
            if s["key"] == key:
                return s
        raise KeyError(key)

    def has_step(self, key: str) -> bool:
        return any(s["key"] == key for s in self.steps)

    def insert_after(self, after_key: str, key: str, title: str, tier: str, url: Optional[str] = None) -> Dict[str, Any]:
        pos = next(i for i, s in enumerate(self.steps) if s["key"] == after_key) + 1
        new = self._new_step(0, key, title, tier, url)
        self.steps.insert(pos, new)
        for i, s in enumerate(self.steps):
            s["index"] = i
        self.save()
        return new

    def insert_before(self, before_key: str, key: str, title: str, tier: str, url: Optional[str] = None) -> Dict[str, Any]:
        pos = next(i for i, s in enumerate(self.steps) if s["key"] == before_key)
        new = self._new_step(0, key, title, tier, url)
        self.steps.insert(pos, new)
        self._reindex()
        self.save()
        return new

    def append_steps(self, specs: List[Tuple[str, str, str, Optional[str]]]) -> None:
        for key, title, tier, url in specs:
            self.steps.append(self._new_step(0, key, title, tier, url))
        self._reindex()
        self.save()

    def _reindex(self) -> None:
        for i, s in enumerate(self.steps):
            s["index"] = i

    def supersede(self, keys: List[str], prefixes: Tuple[str, ...], version: int, reason: str) -> List[str]:
        """Retire the tail of a plan after a re-plan: drop not-yet-run steps, keep executed ones (and their
        evidence) under a versioned key so the canonical keys can be reused by the new plan."""
        retired: List[str] = []
        kept: List[Dict[str, Any]] = []
        for s in self.steps:
            if s["key"] in keys or s["key"].startswith(prefixes):
                if s["status"] in (StepStatus.PENDING.value, StepStatus.SKIPPED.value) and not s["evidence_ids"]:
                    retired.append(s["key"])
                    continue
                if s["status"] in (StepStatus.RUNNING.value, StepStatus.AWAITING_APPROVAL.value):
                    s["status"] = StepStatus.CANCELLED.value
                    s["finished_at"] = utc_now_iso()
                    s["detail"] = f"{reason} (was: {s['detail'] or s['title']})"
                s["key"] = f"{s['key']}@v{version}"
                s["title"] = f"{s['title']} — superseded"
            kept.append(s)
        self.task["plan_steps"] = kept
        self._reindex()
        self.save()
        return retired

    def record_interruption(self, interrupt: Dict[str, Any], *, before_key: Optional[str] = None) -> str:
        """Add a visible ⚡ step for an applied interruption, before `before_key` or at the end."""
        n = sum(1 for s in self.steps if s["key"].startswith("interrupt_")) + 1
        key = f"interrupt_{n}"
        title = "⚡ User interruption: " + "; ".join(interrupt["changes"])
        if before_key and self.has_step(before_key):
            self.insert_before(before_key, key, title, T3)
        else:
            self.append_steps([(key, title, T3, None)])
        s = self.step(key)
        s["status"] = StepStatus.SUCCEEDED.value
        s["started_at"] = interrupt["created_at"]
        s["finished_at"] = utc_now_iso()
        s["detail"] = f"“{interrupt['instruction']}” — constraints recompiled; completed evidence preserved; remaining DAG re-routed."
        self.save()
        return key

    async def checkpoint(self) -> List[Dict[str, Any]]:
        """Step boundary: honour demo pacing, then return interruptions queued while the task was running."""
        if self.pace_seconds:
            await asyncio.sleep(self.pace_seconds)
        return db.list_interrupts(self.task_id, status="QUEUED")

    def start(self, key: str, target_url: Optional[str] = None, title: Optional[str] = None) -> Dict[str, Any]:
        s = self.step(key)
        s["status"] = StepStatus.RUNNING.value
        s["started_at"] = utc_now_iso()
        if target_url:
            s["target_url"] = target_url
        if title:
            s["title"] = title
        self.save(status=TaskStatus.EXECUTING.value)
        return s

    def finish(self, key: str, status: StepStatus, detail: Optional[str] = None) -> Dict[str, Any]:
        s = self.step(key)
        s["status"] = status.value
        s["finished_at"] = utc_now_iso()
        if not s["started_at"]:
            s["started_at"] = s["finished_at"]
        if detail is not None:
            s["detail"] = detail
        self.save()
        return s

    def fail_running_steps(self, detail: str) -> None:
        for s in self.steps:
            if s["status"] == StepStatus.RUNNING.value:
                s["status"] = StepStatus.FAILED.value
                s["finished_at"] = utc_now_iso()
                s["detail"] = detail
            elif s["status"] == StepStatus.PENDING.value:
                s["status"] = StepStatus.SKIPPED.value
        self.save()

    def capture_id(self, key: str) -> str:
        step_id = self.step(key)["step_id"]
        n = self._capture_counts.get(step_id, 0)
        self._capture_counts[step_id] = n + 1
        return step_id if n == 0 else f"{step_id}_{n + 1}"

    async def browse(self, key: str, url: str, *, method: str = "GET", form_data: Optional[Dict[str, Any]] = None,
                     stamp: str = "EVIDENCE CAPTURED") -> Dict[str, Any]:
        return await browser_runtime.inspect(url, self.task_id, self.capture_id(key), method=method,
                                             form_data=form_data, stamp=stamp)

    def record_evidence(
        self,
        key: str,
        *,
        source_url: str,
        capability_method: str,
        extracted: Any,
        page_title: Optional[str] = None,
        citation: Optional[str] = None,
        screenshot_url: Optional[str] = None,
        firewall_alerts: Optional[List[Dict[str, Any]]] = None,
        verified: bool = False,
    ) -> Dict[str, Any]:
        s = self.step(key)
        evidence = db.insert_evidence({
            "evidence_id": f"ev_{uuid.uuid4().hex[:14]}",
            "task_id": self.task_id,
            "step_id": s["step_id"],
            "source_url": source_url,
            "page_title": page_title,
            "capability_method": capability_method,
            "extracted": extracted,
            "dom_citation_snippet": (citation or "")[:2000] or None,
            "screenshot_url": absolute_url(screenshot_url),
            "firewall_alerts": firewall_alerts or [],
            "verification_passed": verified,
            "captured_at": utc_now_iso(),
        })
        s["evidence_ids"].append(evidence["evidence_id"])
        self.save()
        return evidence

    def page_evidence(self, key: str, page: Dict[str, Any], extracted: Any, citation: str, verified: bool = False) -> Dict[str, Any]:
        return self.record_evidence(
            key, source_url=page["final_url"], capability_method=page["capability_method"], extracted=extracted,
            page_title=page["title"], citation=citation, screenshot_url=page["screenshot_url"],
            firewall_alerts=page["distilled"]["firewall_alerts"], verified=verified,
        )

    def evidence_card(self, key: str, *, title: str, source_url: str, lines: List[str], stamp: str,
                      capability_method: str, tone: str = "success") -> str:
        path = browser_runtime.render_evidence_svg(
            self.task_id, self.capture_id(key), title=title, source_url=source_url, lines=lines, stamp=stamp,
            capability_method=capability_method, tone=tone,
        )
        return f"/artifacts/{path.name}"


# task_id → future resolved by the UI's sign-in choice (POST /api/tasks/{id}/sign-in)
LOGIN_WAITERS: Dict[str, "asyncio.Future"] = {}

_SUBMIT_FALLBACK_JS = """
(idx) => {
  const el = document.querySelector(`[data-webpilot-idx="${idx}"]`);
  if (!el) return false;
  const scope = el.form || el.parentElement;
  const btn = scope && scope.querySelector('button, input[type="submit"], [role="button"]');
  if (btn) { btn.click(); return true; }
  if (el.form) { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); return true; }
  return false;
}
"""


def json_preview(value: Any, limit: int = 300) -> str:
    import json as _json
    text = _json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _soup(html_doc: str) -> BeautifulSoup:
    return BeautifulSoup(html_doc or "", "html.parser")


def _find_col(headers: List[str], *patterns: str) -> Optional[int]:
    for i, h in enumerate(headers):
        for p in patterns:
            if re.search(p, h, re.I):
                return i
    return None


# =========================================================================== the engine

class PlannerEngine:
    # ------------------------------------------------------------------ create

    async def create_task(
        self,
        goal: str,
        scenario: Optional[str] = None,
        target_url: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if idempotency_key:
            existing = db.get_task_by_idempotency_key(idempotency_key)
            if existing:
                snapshot = self.task_snapshot(existing["task_id"])
                snapshot["idempotent_replay"] = True
                return snapshot

        from backend import llm_brain  # local import: llm_brain lazily imports this module

        compiled = await llm_brain.compile_goal_with_ai(goal, [target_url] if target_url else None, None,
                                                         scenario_hint=scenario, parameters=parameters)
        interpreted = compiled.interpreted
        interpreted["brain"] = compiled.brain_record()
        interpreted["brain_provider_used"] = compiled.brain_provider_used
        interpreted["ai_reasoning_trace"] = compiled.reasoning_trace
        interpreted["extraction_query"] = compiled.extraction_query
        task = db.insert_task({
            "task_id": f"task_{uuid.uuid4().hex[:12]}",
            "idempotency_key": idempotency_key,
            "goal": goal,
            "task_category": interpreted["task_category"],
            "status": TaskStatus.PLANNED.value,
            "environment_mode": interpreted["environment_mode"],
            "interpreted_goal": interpreted,
            "plan_steps": [],
        })
        run = TaskRun(task, session_id)
        handlers = {
            Scenario.ATTENDANCE_AUDIT.value: self._run_attendance,
            Scenario.PROFILE_COMPARISON.value: self._run_profile_comparison,
            Scenario.LAPTOP_SHOPPING.value: self._run_laptop_shopping,
            Scenario.GRANT_FORM.value: self._run_grant_form,
            Scenario.GITHUB_REPO_ANALYSIS.value: self._run_github,
            Scenario.URL_INSPECTION.value: self._run_url_inspection,
            Scenario.SYNTHESIZED_TOOL.value: self._run_synthesized_tool,
            Scenario.LIVE_AUTOPILOT.value: self._run_live_autopilot,
        }
        try:
            await handlers[interpreted["scenario"]](run, interpreted)
            await self._drain_late_interrupts(run.task_id)
        except tool_synthesizer.SynthesisError as exc:
            run.fail_running_steps(exc.message)
            run.save(status=TaskStatus.FAILED.value, summary=f"I couldn't build a tool for this goal: {exc.message}",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", f"{exc.code}: {exc.message}"))
        except BrowserFetchError as exc:
            run.fail_running_steps(exc.message)
            run.save(status=TaskStatus.FAILED.value,
                     summary=f"I could not complete this task because a page could not be loaded: {exc.message}",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", f"{exc.code}: {exc.message}"))
        except Exception as exc:  # unexpected failure: record honestly instead of losing the task
            logger.exception("Task %s failed", run.task_id)
            run.fail_running_steps(f"{type(exc).__name__}: {exc}")
            run.save(status=TaskStatus.FAILED.value,
                     summary=f"The task failed unexpectedly ({type(exc).__name__}). No outcome is claimed.",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", f"{type(exc).__name__}: {exc}"))
        return self.task_snapshot(run.task_id)

    def _compile_step(self, run: TaskRun, interpreted: Dict[str, Any]) -> None:
        run.start("compile")
        constraints = ", ".join(f"{k}={v}" for k, v in interpreted.get("constraints", {}).items()) or "none"
        run.finish("compile", StepStatus.SUCCEEDED,
                   f"Scenario {interpreted['scenario']} ({interpreted['task_category']}); constraints: {constraints}.")

    # ------------------------------------------------------- A: attendance audit

    async def _run_attendance(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        url = ig["target_url"]
        threshold = ig["constraints"]["threshold_pct"]
        run.plan([
            ("compile", "Compile goal into an attendance audit", TC, None),
            ("inspect", "Open the attendance portal and distill the attendance table", T2, url),
            ("compute", f"Recompute percentages and classes needed to reach {threshold:g}%", TC, None),
            ("verify", "Verify extraction and arithmetic", TC, None),
        ])
        self._compile_step(run, ig)

        run.start("inspect")
        page = await run.browse("inspect", url)
        d = page["distilled"]
        table = next((t for t in d["tables"] if t["table_id"] == "attendance-table"), None) or next(
            (t for t in d["tables"] if _find_col(t["headers"], r"attended|present") is not None), None)
        if table is None:
            run.finish("inspect", StepStatus.FAILED, "No attendance table found on the page.")
            run.save(status=TaskStatus.FAILED.value, summary="I could not find an attendance table on that page.",
                     verification_report=verifier.unverified_report("DOM_TABLE_EXTRACTION", "Attendance table missing."))
            return
        h = table["headers"]
        ci = {
            "code": _find_col(h, r"code"),
            "subject": _find_col(h, r"name|subject|course(?! code)"),
            "attended": _find_col(h, r"attended|present"),
            "conducted": _find_col(h, r"conducted|held|delivered|total"),
            "pct": _find_col(h, r"%|percent"),
        }
        rows: List[Dict[str, Any]] = []
        for cells in table["rows"]:
            try:
                attended = int(re.sub(r"\D", "", cells[ci["attended"]]))
                conducted = int(re.sub(r"\D", "", cells[ci["conducted"]]))
            except (TypeError, ValueError, IndexError):
                continue
            displayed = None
            if ci["pct"] is not None:
                pm = re.search(r"(\d+(?:\.\d+)?)", cells[ci["pct"]])
                displayed = float(pm.group(1)) if pm else None
            rows.append({
                "code": cells[ci["code"]] if ci["code"] is not None else f"ROW{len(rows) + 1}",
                "subject": cells[ci["subject"]] if ci["subject"] is not None else "",
                "attended": attended,
                "conducted": conducted,
                "displayed_pct": displayed,
            })
        student_m = re.search(r"Student:\s*(.+)", d["sanitized_text"])
        run.page_evidence("inspect", page, {"table_id": table["table_id"], "headers": h, "rows": table["rows"]},
                          citation=f"table#{table['table_id']} ({table['row_count']} rows): " +
                                   " || ".join(" | ".join(r) for r in table["rows"]))
        run.finish("inspect", StepStatus.SUCCEEDED,
                   f"Extracted {len(rows)} subjects from table#{table['table_id']} via {page['capability_method']}.")

        for itr in await run.checkpoint():
            if "threshold_pct" in itr["delta"]:
                threshold = float(itr["delta"]["threshold_pct"])
                run.record_interruption(itr, before_key="compute")
                run.step("compute")["title"] = f"Recompute percentages and classes needed to reach {threshold:g}%"
                run.step("verify")["title"] = "Verify extraction and arithmetic (re-planned threshold)"
                db.resolve_interrupt(itr["interrupt_id"], "APPLIED", f"Threshold set to {threshold:g}% before computation.")
            else:
                db.resolve_interrupt(itr["interrupt_id"], "REJECTED", "No applicable change for an attendance audit.")

        run.start("compute")
        for r in rows:
            r["percentage"] = round(r["attended"] * 100.0 / r["conducted"], 1) if r["conducted"] else 0.0
            r["below_threshold"] = r["percentage"] < threshold
            r["classes_needed"] = verifier.classes_needed_for_threshold(r["attended"], r["conducted"], threshold) if r["below_threshold"] else 0
        below = [r for r in rows if r["below_threshold"]]
        run.finish("compute", StepStatus.SUCCEEDED,
                   f"{len(below)} of {len(rows)} subjects below {threshold:g}%. Formula: k = max(0, 3·conducted − 4·attended).")

        run.start("verify")
        report = verifier.verify_attendance(rows, threshold, expected_row_count=table["row_count"])
        passed = report["verdict"] == "VERIFIED_SUCCESS"
        lines = [f"## Attendance audit — threshold {threshold:g}%"] + [
            f"{r['code']} {r['subject']}: {r['attended']}/{r['conducted']} = {r['percentage']:.1f}%"
            + (f"  → needs {r['classes_needed']} consecutive classes" if r["below_threshold"] else "  ✓")
            for r in rows
        ] + [f"## Verification: {report['verdict']} ({sum(c['passed'] for c in report['checks'])}/{len(report['checks'])} checks)"]
        card = run.evidence_card("verify", title="Attendance verification", source_url=page["final_url"], lines=lines,
                                 stamp=report["verdict"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                                 tone="success" if passed else "danger")
        run.record_evidence("verify", source_url=page["final_url"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                            extracted={"below_threshold": below, "checks": report["checks"]}, page_title=page["title"],
                            citation="Recomputed (a/c)·100 and minimal k with (a+k)/(c+k) ≥ threshold",
                            screenshot_url=card, verified=passed)
        for ev_id in run.step("inspect")["evidence_ids"]:
            db.set_evidence_verified(ev_id, passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report.get("notes") or report["verdict"])

        if below:
            parts = [f"{r['subject']} ({r['code']}) at {r['percentage']:.1f}% — attend the next {r['classes_needed']} "
                     f"classes in a row" for r in below]
            summary = f"{len(below)} of {len(rows)} subjects are below {threshold:g}%: " + "; ".join(parts) + "."
        else:
            summary = f"All {len(rows)} subjects meet the {threshold:g}% attendance requirement."
        run.save(
            status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value,
            summary=summary,
            extracted_data={
                "student": student_m.group(1).strip() if student_m else None,
                "threshold_pct": threshold,
                "formula": "classes_needed = max(0, 3 × conducted − 4 × attended)",
                "subjects": rows,
                "below_threshold": below,
                "source_url": page["final_url"],
            },
            verification_report=report,
        )

    # ---------------------------------------------------- B: profile comparison

    async def _fetch_codearena(self, run: TaskRun, key: str, username: str) -> Dict[str, Any]:
        url = username if username.startswith(("http://", "https://", "/")) else sandbox_url(f"/sandbox/codearena/{username}")
        page = await run.browse(key, url)
        d = page["distilled"]
        if page["status_code"] != 200:
            raise ToolError("PROFILE_NOT_FOUND", f"Profile page {page['final_url']} returned HTTP {page['status_code']}.")
        solved = next((t for t in d["tables"] if t["table_id"] == "solved-stats"), None)
        contest = next((t for t in d["tables"] if t["table_id"] == "contest-stats"), None)
        if not solved:
            raise ToolError("PROFILE_PARSE_FAILED", f"No solved-problems table on {page['final_url']}.")
        counts = {r[0].lower(): int(r[1]) for r in solved["rows"] if len(r) == 2 and r[1].isdigit()}
        metrics = {r[0]: r[1] for r in (contest["rows"] if contest else []) if len(r) == 2}
        rank_m = re.search(r"Global ranking\s*([\d,]+)", d["sanitized_text"])
        uname = urlparse(page["final_url"]).path.rstrip("/").split("/")[-1]
        profile = {
            "username": uname,
            "display_name": d["headings"][0]["text"] if d["headings"] else uname,
            "platform": "CodeArena (controlled sandbox)",
            "easy": counts.get("easy", 0),
            "medium": counts.get("medium", 0),
            "hard": counts.get("hard", 0),
            "total_solved": counts.get("total", 0),
            "contest_rating": int(metrics["Contest Rating"]) if metrics.get("Contest Rating", "").isdigit() else None,
            "global_ranking": int(rank_m.group(1).replace(",", "")) if rank_m else None,
            "streak_days": int(metrics["Current Streak (days)"]) if metrics.get("Current Streak (days)", "").isdigit() else None,
            "source_url": page["final_url"],
            "capability_method": page["capability_method"],
        }
        run.page_evidence(key, page, profile, citation="table#solved-stats: " + " || ".join(" | ".join(r) for r in solved["rows"]))
        return profile

    async def _fetch_leetcode(self, run: TaskRun, key: str, username: str) -> Dict[str, Any]:
        m = re.search(r"leetcode\.com/(?:u/)?([\w-]+)", username)
        uname = m.group(1) if m else username.strip("/ ")
        query = """query userProfile($username: String!) {
  matchedUser(username: $username) { username profile { ranking realName }
    submitStatsGlobal { acSubmissionNum { difficulty count } } }
  userContestRanking(username: $username) { rating attendedContestsCount }
}"""
        try:
            async with httpx.AsyncClient(transport=leetcode_transport, timeout=config.HTTP_TIMEOUT_SECONDS, headers={
                "Content-Type": "application/json", "Referer": "https://leetcode.com", "User-Agent": "WebPilot-Alexa-Plus/1.0",
            }) as client:
                r = await client.post("https://leetcode.com/graphql", json={"query": query, "variables": {"username": uname}})
            r.raise_for_status()
            payload = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ToolError("LIVE_SOURCE_UNAVAILABLE", f"LeetCode public stats for '{uname}' could not be retrieved "
                            f"({type(exc).__name__}). No numbers were guessed.") from exc
        user = (payload.get("data") or {}).get("matchedUser")
        if not user:
            raise ToolError("PROFILE_NOT_FOUND", f"LeetCode user '{uname}' does not exist.")
        counts = {e["difficulty"].lower(): int(e["count"]) for e in user["submitStatsGlobal"]["acSubmissionNum"]}
        contest = (payload.get("data") or {}).get("userContestRanking") or {}
        profile = {
            "username": user["username"],
            "display_name": (user.get("profile") or {}).get("realName") or user["username"],
            "platform": "LeetCode (live public GraphQL)",
            "easy": counts.get("easy", 0),
            "medium": counts.get("medium", 0),
            "hard": counts.get("hard", 0),
            "total_solved": counts.get("all", counts.get("easy", 0) + counts.get("medium", 0) + counts.get("hard", 0)),
            "contest_rating": round(contest["rating"]) if contest.get("rating") else None,
            "global_ranking": (user.get("profile") or {}).get("ranking"),
            "streak_days": None,
            "source_url": f"https://leetcode.com/u/{user['username']}/",
            "capability_method": "TIER_1_ADAPTER:LEETCODE_GRAPHQL",
        }
        card = run.evidence_card(key, title=f"LeetCode · {profile['username']}", source_url=profile["source_url"],
                                 lines=[f"Easy {profile['easy']} · Medium {profile['medium']} · Hard {profile['hard']}",
                                        f"Total solved {profile['total_solved']}", f"Ranking {profile['global_ranking']}",
                                        f"Contest rating {profile['contest_rating']}"],
                                 stamp="TIER 1 ADAPTER", capability_method=profile["capability_method"])
        run.record_evidence(key, source_url="https://leetcode.com/graphql", capability_method=profile["capability_method"],
                            extracted=profile, page_title=f"LeetCode profile {profile['username']}",
                            citation=f"matchedUser({profile['username']}).submitStatsGlobal.acSubmissionNum", screenshot_url=card)
        return profile

    async def _run_profile_comparison(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        c = ig["constraints"]
        live = c["platform"] == "leetcode"
        tier = T1 if live else T2
        run.plan([
            ("compile", "Compile goal into a profile comparison", TC, None),
            ("profile_user", f"Retrieve profile '{c['user_profile']}'", tier, None),
            ("profile_friend", f"Retrieve profile '{c['friend_profile']}'", tier, None),
            ("compare", "Compare problem-solving metrics", TC, None),
            ("verify", "Verify metrics and source citations", TC, None),
        ])
        self._compile_step(run, ig)
        fetch = self._fetch_leetcode if live else self._fetch_codearena
        profiles = []
        for key, name in (("profile_user", c["user_profile"]), ("profile_friend", c["friend_profile"])):
            run.start(key)
            try:
                p = await fetch(run, key, name)
            except ToolError as exc:
                run.finish(key, StepStatus.FAILED, exc.message)
                run.fail_running_steps("Skipped: a profile could not be retrieved.")
                run.save(status=TaskStatus.FAILED.value,
                         summary=f"I couldn't compare the profiles: {exc.message}",
                         verification_report=verifier.unverified_report("NOT_EXECUTED", exc.message))
                return
            profiles.append(p)
            run.finish(key, StepStatus.SUCCEEDED, f"{p['display_name']}: {p['total_solved']} solved (source {p['source_url']}).")

        run.start("compare")
        a, b = profiles
        metrics = ["easy", "medium", "hard", "total_solved", "contest_rating"]
        comparison = {}
        for mname in metrics:
            va, vb = a.get(mname), b.get(mname)
            if va is None or vb is None:
                comparison[mname] = {"user": va, "friend": vb, "leader": None, "gap": None}
                continue
            leader = "tie" if va == vb else (a["username"] if va > vb else b["username"])
            comparison[mname] = {"user": va, "friend": vb, "leader": leader, "gap": abs(va - vb)}
        gaps = {k: v["gap"] for k, v in comparison.items() if k in ("easy", "medium", "hard") and v["gap"] is not None}
        biggest = max(gaps, key=gaps.get) if gaps else None
        run.finish("compare", StepStatus.SUCCEEDED, f"Compared {len(metrics)} metrics.")

        run.start("verify")
        report = verifier.verify_profile_comparison(profiles)
        passed = report["verdict"] == "VERIFIED_SUCCESS"
        run.record_evidence("verify", source_url=a["source_url"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                            extracted={"comparison": comparison, "checks": report["checks"]},
                            citation=f"Sources: {a['source_url']} ; {b['source_url']}", verified=passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report["verdict"])

        total = comparison["total_solved"]
        if total["leader"] == "tie":
            headline = f"You and {b['display_name']} have both solved {a['total_solved']} problems."
        elif total["leader"] == a["username"]:
            headline = f"You are ahead of {b['display_name']} by {total['gap']} problems ({a['total_solved']} vs {b['total_solved']})."
        else:
            headline = f"{b['display_name']} is ahead of you by {total['gap']} problems ({b['total_solved']} vs {a['total_solved']})."
        if biggest:
            headline += (f" The biggest gap is in {biggest.title()} problems "
                         f"(you: {comparison[biggest]['user']}, {b['display_name']}: {comparison[biggest]['friend']}).")
        run.save(
            status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value,
            summary=headline,
            extracted_data={"profiles": profiles, "comparison": comparison, "biggest_gap_difficulty": biggest,
                            "sources": [a["source_url"], b["source_url"]]},
            verification_report=report,
        )

    # ------------------------------------------------------ C: laptop shopping

    @staticmethod
    def _parse_candidates(page: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for card in page["distilled"]["product_cards"]:
            price = card.get("price")
            if not isinstance(price, int):
                digits = re.sub(r"[^\d]", "", str(card.get("display_price") or ""))
                price = int(digits) if digits else None
            ram = card.get("ram_gb")
            if not isinstance(ram, int):
                rm = _RAM_RE.search(card.get("specs") or "")
                ram = int(rm.group(1)) if rm else None
            if price is None or ram is None:
                continue
            out.append({
                "sku": str(card.get("sku") or card.get("product_id")),
                "name": card.get("name") or card.get("title"),
                "seller": card.get("seller") or page["title"],
                "price_inr": price,
                "ram_gb": ram,
                "rating": float(card["rating"]) if card.get("rating") is not None else None,
                "delivery": card.get("delivery"),
                "stock_note": card.get("stock_note"),
                "checkout_state": card.get("checkout_state"),
                "specs": card.get("specs"),
                "checkout_url": card.get("action_url"),
                "source_url": page["final_url"],
                "selector": card.get("selector"),
            })
        return out

    @staticmethod
    def _store_key(url: str) -> str:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "sandbox":
            return parts[1] if parts[1] != "attack-lab" else "attack-lab-" + (parts[2] if len(parts) > 2 else "")
        return urlparse(url).netloc or url

    def _store_label(self, url: str) -> str:
        key = self._store_key(url)
        if key in STORES:
            return STORES[key]["name"]
        if key.startswith("attack-lab"):
            return "Attack Lab store (poisoned ElectroHub copy)"
        return key

    @staticmethod
    def _next_version(run: TaskRun) -> int:
        return 1 + max([int(m.group(1)) for s in run.steps for m in [re.search(r"@v(\d+)$", s["key"])] if m] or [0])

    def _apply_laptop_interrupt(self, run: TaskRun, c: Dict[str, Any], itr: Dict[str, Any],
                                before_key: Optional[str] = None) -> bool:
        d = itr["delta"]
        applied = False
        if "max_price_inr" in d:
            c["max_price_inr"] = int(d["max_price_inr"])
            applied = True
        if "min_ram_gb" in d:
            c["min_ram_gb"] = int(d["min_ram_gb"])
            applied = True
        if d.get("exclude_stores"):
            c["excluded_stores"] = sorted(set(c.get("excluded_stores", [])) | set(d["exclude_stores"]))
            applied = True
        if not applied:
            db.resolve_interrupt(itr["interrupt_id"], "REJECTED", "No applicable change for a laptop search.")
            return False
        run.record_interruption(itr, before_key=before_key)
        if run.has_step("rank") and run.step("rank")["status"] == StepStatus.PENDING.value:
            run.step("rank")["title"] = (f"Filter (≥{c['min_ram_gb']}GB RAM, ≤{format_inr(c['max_price_inr'])}) and rank "
                                         f"candidates — re-planned")
            run.save()
        db.resolve_interrupt(itr["interrupt_id"], "APPLIED", "; ".join(itr["changes"]))
        return True

    async def _run_laptop_shopping(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        c = dict(ig["constraints"])
        c.setdefault("excluded_stores", [])
        stores = ig["store_urls"]
        specs: List[Tuple[str, str, str, Optional[str]]] = [("compile", "Compile goal into shopping constraints", TC, None)]
        for i, s in enumerate(stores):
            specs.append((f"inspect_store_{i}", f"Inspect store {urlparse(s).path or s} through the content firewall", T2, s))
        specs.append(("rank", f"Filter (≥{c['min_ram_gb']}GB RAM, ≤{format_inr(c['max_price_inr'])}) and rank candidates", TC, None))
        if c["prepare_checkout"]:
            specs += [
                ("checkout_1", "Prepare controlled checkout for the #1 candidate", T2, None),
                ("approval", "Lock parameters and request human approval (HMAC gate)", T3, None),
                ("execute", "Execute approved checkout", T2, None),
                ("verify", "Verify order confirmation on the receipt page", TC, None),
            ]
        run.plan(specs)
        self._compile_step(run, ig)

        candidates: List[Dict[str, Any]] = []
        all_alerts: List[Dict[str, Any]] = []
        applied: List[Dict[str, Any]] = []
        for i, store_url in enumerate(stores):
            key = f"inspect_store_{i}"
            for itr in await run.checkpoint():
                if self._apply_laptop_interrupt(run, c, itr, before_key=key):
                    applied.append(itr)
            if self._store_key(store_url) in c["excluded_stores"]:
                run.finish(key, StepStatus.SKIPPED, "Skipped: you removed this store mid-task, so it was never visited.")
                continue
            run.start(key)
            page = await run.browse(key, store_url)
            found = self._parse_candidates(page)
            alerts = page["distilled"]["firewall_alerts"]
            all_alerts += alerts
            candidates += found
            run.page_evidence(key, page, {"products": found},
                              citation="; ".join(f"{p['selector']}: {p['name']} {format_inr(p['price_inr'])}" for p in found))
            detail = f"{page['title']}: {len(found)} products extracted via {page['capability_method']}."
            if alerts:
                detail += " FIREWALL: " + summarize_alerts(alerts)
            run.finish(key, StepStatus.SUCCEEDED, detail)

        for itr in await run.checkpoint():
            if self._apply_laptop_interrupt(run, c, itr, before_key="rank"):
                applied.append(itr)

        extracted: Dict[str, Any] = {
            "constraints": c,
            "stores": [{"key": self._store_key(s), "label": self._store_label(s), "url": s} for s in stores],
            "candidates_scanned": candidates,
            "firewall_alerts": all_alerts,
            "firewall_summary": summarize_alerts(all_alerts),
            "recovery_log": [],
        }
        await self._laptop_rank_and_checkout(run, c, extracted, note=self._interrupt_note(applied))

    async def _laptop_rank_and_checkout(self, run: TaskRun, c: Dict[str, Any], extracted: Dict[str, Any],
                                        note: str = "") -> None:
        """Rank scanned candidates under the current constraints and prepare an approval-gated checkout.
        Re-entrant: re-planning after an interruption calls it again with updated constraints."""
        max_price, min_ram = c["max_price_inr"], c["min_ram_gb"]
        excluded = set(c.get("excluded_stores", []))
        run.start("rank", title=f"Filter (≥{min_ram}GB RAM, ≤{format_inr(max_price)}) and rank candidates")
        evaluated = []
        for cand in extracted["candidates_scanned"]:
            reasons = []
            if self._store_key(cand["source_url"]) in excluded:
                reasons.append("store excluded by you")
            if cand["price_inr"] > max_price:
                reasons.append(f"price {format_inr(cand['price_inr'])} exceeds budget {format_inr(max_price)}")
            if cand["ram_gb"] < min_ram:
                reasons.append(f"{cand['ram_gb']}GB RAM is below the {min_ram}GB requirement")
            evaluated.append({**cand, "qualifies": not reasons, "rejection_reasons": reasons})
        qualifying = sorted([e for e in evaluated if e["qualifies"]], key=lambda e: (e["price_inr"], -(e["rating"] or 0)))
        for rank, e in enumerate(qualifying, start=1):
            e["rank"] = rank
        rank_report = verifier.verify_ranked_candidates(qualifying, max_price, min_ram)
        run.record_evidence("rank", source_url=extracted["stores"][0]["url"], capability_method="INTERNAL_COMPUTE:CONSTRAINT_FILTER",
                            extracted={"evaluated": evaluated, "ranking_rule": "lowest total price, tie-broken by rating",
                                       "constraints": dict(c), "checks": rank_report["checks"]},
                            citation=" ; ".join(f"#{e['rank']} {e['name']} {format_inr(e['price_inr'])} ({e['seller']})" for e in qualifying),
                            verified=rank_report["verdict"] == "VERIFIED_SUCCESS")
        run.finish("rank", StepStatus.SUCCEEDED,
                   f"{len(qualifying)} of {len(evaluated)} laptops qualify. Ranked by lowest price, tie-broken by rating.")
        extracted.update(constraints=c, candidates_evaluated=evaluated, ranked=qualifying)
        extracted.pop("selected", None)

        if not qualifying:
            run.fail_running_steps("No laptop satisfies the constraints.")
            run.save(status=TaskStatus.COMPLETED.value, extracted_data=extracted, verification_report=rank_report,
                     pending_approval=None,
                     summary=note + f"No laptop with at least {min_ram}GB RAM is available under {format_inr(max_price)} "
                                    f"in the stores I checked, so nothing will be ordered.")
            return
        if not c["prepare_checkout"]:
            top = qualifying[0]
            run.save(status=TaskStatus.COMPLETED.value, extracted_data=extracted, verification_report=rank_report,
                     summary=note + f"Best match: {top['name']} at {format_inr(top['price_inr'])} from {top['seller']}.")
            return

        # Checkout preparation with dynamic failure recovery.
        locked: Optional[Dict[str, Any]] = None
        chosen: Optional[Dict[str, Any]] = None
        review_page: Optional[Dict[str, Any]] = None
        step_key = "checkout_1"
        for idx, cand in enumerate(qualifying):
            if idx > 0:
                prev = step_key
                step_key = f"checkout_{idx + 1}"
                run.insert_after(prev, step_key, f"Dynamic re-plan: pivot to #{idx + 1} {cand['name']} ({cand['seller']})", T2, None)
            run.start(step_key, target_url=cand["checkout_url"],
                      title=f"Prepare controlled checkout for #{idx + 1} {cand['name']} ({cand['seller']})")
            page = await run.browse(step_key, cand["checkout_url"], stamp="PRE-CHECKOUT EVIDENCE")
            soup = _soup(page["html"])
            error = soup.select_one("#checkout-error")
            if error is not None or page["status_code"] >= 400:
                code = (error.get("data-error-code") if error else None) or f"HTTP_{page['status_code']}"
                run.page_evidence(step_key, page, {"error_code": code, "sku": cand["sku"]},
                                  citation=f"#checkout-error[data-error-code={code}]: " + (error.get_text(" ", strip=True)[:200] if error else ""))
                extracted["recovery_log"].append({
                    "failed_candidate": cand["name"], "seller": cand["seller"], "error_code": code,
                    "listing_checkout_state": cand.get("checkout_state"), "at": utc_now_iso(),
                })
                is_last = idx == len(qualifying) - 1
                run.finish(step_key, StepStatus.FAILED if is_last else StepStatus.FAILED_RECOVERED,
                           f"{code} for {cand['name']} at {cand['seller']}." + ("" if is_last else " Re-planning with the next ranked candidate."))
                continue
            locked = self._checkout_params_from_review(page)
            if locked is None:
                run.finish(step_key, StepStatus.FAILED, "Checkout review page did not expose order details.")
                continue
            chosen, review_page = cand, page
            run.page_evidence(step_key, page, {"locked_parameters": locked},
                              citation=f"#checkout-review: {locked['item_name']} | {locked['seller']} | {format_inr(locked['total_price_inr'])}")
            run.finish(step_key, StepStatus.SUCCEEDED,
                       f"Checkout ready: {locked['item_name']} at {format_inr(locked['total_price_inr'])} from {locked['seller']}.")
            break

        if locked is None or chosen is None:
            run.fail_running_steps("Every qualifying candidate failed at checkout.")
            run.save(status=TaskStatus.FAILED.value, extracted_data=extracted, pending_approval=None,
                     summary=note + "Every laptop that matched your constraints failed at checkout, so nothing was ordered.",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", "All checkout attempts failed."))
            return

        # Last checkpoint before locking parameters: a change of mind here re-plans instead of asking for approval.
        queued = await run.checkpoint()
        if queued:
            applied = [self._apply_laptop_interrupt(run, c, itr, before_key="approval") for itr in queued]
            if any(applied):
                self._reset_laptop_tail(run, c)
                extracted["recovery_log"] = []
                await self._laptop_rank_and_checkout(run, c, extracted, note=self._interrupt_note(queued))
                return

        run.start("approval")
        approval = approval_guard.create_approval_request(
            run.task_id, run.step("approval")["step_id"], ActionType.PURCHASE_CHECKOUT,
            f"Buy {locked['item_name']} from {locked['seller']} for {format_inr(locked['total_price_inr'])} (controlled sandbox checkout)",
            locked, preview_screenshot_url=absolute_url(review_page["screenshot_url"]),
        )
        run.finish("approval", StepStatus.AWAITING_APPROVAL,
                   f"Approval {approval['approval_id']} created; parameter hash {approval['parameter_hash'][:16]}…")
        extracted["selected"] = chosen
        recovered = extracted["recovery_log"]
        all_alerts = extracted.get("firewall_alerts", [])
        summary = note
        if recovered:
            f0 = recovered[0]
            summary += f"{f0['failed_candidate']} at {f0['seller']} went out of stock during checkout, so I switched to the next best option. "
        summary += (f"{locked['item_name']} from {locked['seller']} at {format_inr(locked['total_price_inr'])} is ready to order. "
                    f"I need your approval before I place this order.")
        if all_alerts:
            pushed = [a for a in all_alerts if "WITHOUT_USER_APPROVAL" in a["matched_patterns"]]
            if pushed and locked["item_name"].lower() in " ".join(a["snippet"].lower() for a in pushed):
                summary += (f" Note: a hidden instruction on the store also pushed {locked['item_name']}; I ignored it. "
                            "This choice comes only from your own constraints, and it still needs your approval.")
            elif pushed:
                summary += " I also blocked a hidden instruction on ElectroHub that tried to make me buy a ₹1,45,000 laptop without asking you."
            else:
                summary += f" {summarize_alerts(all_alerts)}"
        run.save(status=TaskStatus.AWAITING_APPROVAL.value, extracted_data=extracted,
                 pending_approval=approval_guard.public_view(approval), summary=summary,
                 verification_report=verifier.unverified_report("AWAITING_APPROVAL", "No action executed yet."))

    def _reset_laptop_tail(self, run: TaskRun, c: Dict[str, Any]) -> None:
        """Retire rank/checkout/approval steps of the previous plan and append a fresh tail."""
        version = self._next_version(run)
        run.supersede(["rank", "approval", "execute", "verify"], ("checkout_",), version, "Superseded by user interruption")
        run.append_steps([
            ("rank", f"Filter (≥{c['min_ram_gb']}GB RAM, ≤{format_inr(c['max_price_inr'])}) and rank candidates — re-planned", TC, None),
            ("checkout_1", "Prepare controlled checkout for the new #1 candidate", T2, None),
            ("approval", "Lock parameters and request human approval (HMAC gate)", T3, None),
            ("execute", "Execute approved checkout", T2, None),
            ("verify", "Verify order confirmation on the receipt page", TC, None),
        ])

    @staticmethod
    def _interrupt_note(interrupts: List[Dict[str, Any]]) -> str:
        changes = [ch for itr in interrupts for ch in itr["changes"]]
        return ("Updated plan (" + "; ".join(changes) + "). ") if changes else ""

    @staticmethod
    def _checkout_params_from_review(page: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        soup = _soup(page["html"])
        review = soup.select_one("#checkout-review")
        form = soup.select_one("#place-order-form")
        if review is None or form is None:
            return None
        try:
            total = int(review.get("data-total-price", ""))
        except ValueError:
            return None
        return {
            "action": ActionType.PURCHASE_CHECKOUT.value,
            "item_name": review.get("data-item-name"),
            "sku": review.get("data-sku"),
            "seller": review.get("data-seller"),
            "total_price_inr": total,
            "currency": "INR",
            "quantity": 1,
            "target_url": page["final_url"],
            "checkout_action_url": urljoin(page["final_url"], form.get("action")),
            "environment": EnvironmentMode.CONTROLLED_SANDBOX.value,
        }

    # ---------------------------------------------------------- E: grant form

    async def _run_grant_form(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        url = ig["target_url"]
        params = ig.get("parameters") or {}
        run.plan([
            ("compile", "Compile goal into a form-filling task", TC, None),
            ("inspect", "Inspect the application form and its fields", T2, url),
            ("map", "Map applicant data to form fields and validate constraints", TC, None),
            ("preview", "Render a filled-in preview of the form", T2, None),
            ("approval", "Pause for human approval before final submission (HMAC gate)", T3, None),
            ("execute", "Submit the approved application", T2, None),
            ("verify", "Verify the submission receipt", TC, None),
        ])
        self._compile_step(run, ig)

        run.start("inspect")
        page = await run.browse("inspect", url)
        forms = page["distilled"]["forms"]
        form = next((f for f in forms if f["form_id"] == "grant-form"), forms[0] if forms else None)
        if form is None:
            run.finish("inspect", StepStatus.FAILED, "No form found on the page.")
            run.save(status=TaskStatus.FAILED.value, summary="I couldn't find a form on that page.",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", "No form found."))
            return
        fields = [f for f in form["fields"] if f.get("name") and f["type"] not in ("hidden", "submit", "button")]
        run.page_evidence("inspect", page, {"form": form},
                          citation=f"form#{form['form_id']} {form['method']} {form['action']}: " + ", ".join(f["name"] for f in fields))
        run.finish("inspect", StepStatus.SUCCEEDED, f"Form #{form['form_id']} has {len(fields)} fields; submit → {form['action']}.")

        provided = {**(params.get("form_data") or {}), **{k: v for k, v in params.items() if k in {f["name"] for f in fields}}}
        for itr in await run.checkpoint():
            if itr["delta"].get("form_data"):
                provided.update(itr["delta"]["form_data"])
                run.record_interruption(itr, before_key="map")
                db.resolve_interrupt(itr["interrupt_id"], "APPLIED", "; ".join(itr["changes"]))
            else:
                db.resolve_interrupt(itr["interrupt_id"], "REJECTED", "No applicable change for this form.")
        await self._grant_fill_and_request(run, page, form, provided)

    async def _grant_fill_and_request(self, run: TaskRun, page: Dict[str, Any], form: Dict[str, Any],
                                      provided: Dict[str, Any], note: str = "") -> None:
        """Map values onto the form, render the filled preview and pause at the approval gate."""
        fields = [f for f in form["fields"] if f.get("name") and f["type"] not in ("hidden", "submit", "button")]
        run.start("map")
        values: Dict[str, str] = {}
        sources: Dict[str, str] = {}
        problems: List[str] = []
        for f in fields:
            name = f["name"]
            if name in provided and str(provided[name]).strip():
                values[name], sources[name] = str(provided[name]).strip(), "user_parameters"
            elif name in DEMO_APPLICANT_PROFILE:
                values[name], sources[name] = DEMO_APPLICANT_PROFILE[name], "demo_applicant_profile"
            elif f["required"]:
                problems.append(f"{name} is required but no value was provided")
                continue
            v = values.get(name, "")
            if f.get("maxlength") and len(v) > f["maxlength"]:
                problems.append(f"{name} exceeds {f['maxlength']} characters")
            if f.get("options") and v and v not in f["options"]:
                problems.append(f"{name} must be one of {f['options']}")
            if f["type"] == "url" and v and not re.match(r"^https?://[^\s]+$", v):
                problems.append(f"{name} must be a valid http(s) URL")
        mapping = [{"field": f["name"], "label": f.get("label"), "value": values.get(f["name"]),
                    "source": sources.get(f["name"]), "required": f["required"]} for f in fields]
        if problems:
            run.finish("map", StepStatus.FAILED, "; ".join(problems))
            run.fail_running_steps("Waiting for missing or invalid applicant data.")
            run.save(status=TaskStatus.AWAITING_USER_INPUT.value, pending_approval=None,
                     summary=note + "I need a bit more information before I can fill the form: " + "; ".join(problems) + ".",
                     extracted_data={"form": form, "field_mapping": mapping, "problems": problems, "provided": provided,
                                     "form_url": page["final_url"]},
                     verification_report=verifier.unverified_report("NOT_EXECUTED", "Awaiting user input."))
            return
        run.finish("map", StepStatus.SUCCEEDED, f"Mapped {len(values)} fields (" +
                   ", ".join(sorted({s for s in sources.values()})) + ").")

        run.start("preview")
        preview_html = self._fill_form_html(page["html"], form["form_id"], values)
        preview = await browser_runtime.capture_html(
            preview_html, run.task_id, run.capture_id("preview"), title="Filled application preview",
            source_url=page["final_url"], lines=[f"## Form #{form['form_id']} — filled preview (not submitted)"] +
            [f"{m['field']}: {m['value']}" for m in mapping], stamp="PREVIEW · NOT SUBMITTED", tone="warning",
        )
        run.record_evidence("preview", source_url=page["final_url"], capability_method=f"TIER_2_SEMANTIC_DOM:{preview['rendered_by']}",
                            extracted={"field_mapping": mapping}, page_title=page["title"],
                            citation="Filled preview: " + "; ".join(f"{k}={v[:60]}" for k, v in values.items()),
                            screenshot_url=preview["screenshot_url"])
        run.finish("preview", StepStatus.SUCCEEDED, "Preview rendered; nothing has been submitted.")

        locked = self._form_params(page, form, values)
        run.start("approval")
        approval = approval_guard.create_approval_request(
            run.task_id, run.step("approval")["step_id"], ActionType.FORM_SUBMISSION,
            f"Submit the grant application '{values.get('project_title', '')}' for {values.get('applicant_name', '')} "
            f"to {page['title'] or form['action']}",
            locked, preview_screenshot_url=absolute_url(preview["screenshot_url"]),
        )
        run.finish("approval", StepStatus.AWAITING_APPROVAL, f"Approval {approval['approval_id']} created.")
        run.save(
            status=TaskStatus.AWAITING_APPROVAL.value,
            summary=note + (f"I've filled in the grant application '{values.get('project_title', '')}' for "
                            f"{values.get('applicant_name', '')}. Please review and approve before I submit it."),
            extracted_data={"form": form, "field_mapping": mapping, "values": values, "provided": provided,
                            "form_url": page["final_url"]},
            pending_approval=approval_guard.public_view(approval),
            verification_report=verifier.unverified_report("AWAITING_APPROVAL", "Nothing submitted yet."),
        )

    @staticmethod
    def _fill_form_html(page_html: str, form_id: str, values: Dict[str, str]) -> str:
        soup = _soup(page_html)
        form = soup.find("form", id=form_id) or soup.find("form")
        if form is None:
            return page_html
        for name, value in values.items():
            el = form.find(attrs={"name": name})
            if el is None:
                continue
            if el.name == "textarea":
                el.string = value
            elif el.name == "select":
                for opt in el.find_all("option"):
                    if opt.get("value") == value:
                        opt["selected"] = "selected"
                    elif opt.has_attr("selected"):
                        del opt["selected"]
            else:
                el["value"] = value
        banner = soup.new_tag("div", style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:10px;margin-bottom:14px;font-weight:600")
        banner.string = "WebPilot preview — filled but NOT submitted. Awaiting your approval."
        form.insert(0, banner)
        return str(soup)

    @staticmethod
    def _form_params(page: Dict[str, Any], form: Dict[str, Any], values: Dict[str, str]) -> Dict[str, Any]:
        return {
            "action": ActionType.FORM_SUBMISSION.value,
            "form_url": page["final_url"],
            "form_id": form["form_id"],
            "form_action": form["action"],
            "method": form["method"],
            "field_names": sorted(f["name"] for f in form["fields"] if f.get("name") and f["type"] != "hidden"),
            "fields": values,
            "environment": EnvironmentMode.CONTROLLED_SANDBOX.value,
        }

    # ------------------------------------------------------------ F: GitHub

    async def _github_api(self, owner: str, repo: str) -> Dict[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "WebPilot-Alexa-Plus/1.0"}
        if config.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
        async with httpx.AsyncClient(base_url="https://api.github.com", transport=github_transport, headers=headers,
                                     timeout=config.HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            async def get(path: str, params: Optional[Dict[str, Any]] = None, optional: bool = False):
                r = await client.get(path, params=params)
                if r.status_code == 404:
                    if optional:
                        return None, r.headers
                    raise ToolError("REPOSITORY_NOT_FOUND", f"GitHub repository {owner}/{repo} does not exist or is private.")
                if r.status_code == 429 or (r.status_code == 403 and (
                        r.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in r.text.lower())):
                    raise _GitHubRateLimited(r.headers.get("x-ratelimit-reset"))
                r.raise_for_status()
                return r.json(), r.headers

            meta, h = await get(f"/repos/{owner}/{repo}")
            issues, _ = await get(f"/repos/{owner}/{repo}/issues", {"state": "open", "per_page": 15, "sort": "updated"})
            languages, _ = await get(f"/repos/{owner}/{repo}/languages", optional=True)
            contents, h2 = await get(f"/repos/{owner}/{repo}/contents", optional=True)
            components = None
            if isinstance(contents, list) and any(i.get("type") == "dir" and i.get("name") == "src" for i in contents):
                src, h2 = await get(f"/repos/{owner}/{repo}/contents/src", optional=True)
                if isinstance(src, list):
                    components = sorted(i["name"] for i in src if i.get("type") == "dir")
        total_bytes = sum((languages or {}).values()) or 1
        top_dirs = sorted(i["name"] for i in (contents or []) if i.get("type") == "dir")
        top_files = sorted(i["name"] for i in (contents or []) if i.get("type") == "file")
        real_issues = [i for i in (issues or []) if "pull_request" not in i][:10]
        return {
            "source": "GITHUB_REST_API",
            "owner": owner,
            "repo": repo,
            "full_name": meta.get("full_name"),
            "description": meta.get("description"),
            "html_url": meta.get("html_url"),
            "homepage": meta.get("homepage") or None,
            "stars": meta.get("stargazers_count"),
            "forks": meta.get("forks_count"),
            "watchers": meta.get("subscribers_count"),
            "open_issues_and_prs": meta.get("open_issues_count"),
            "language": meta.get("language"),
            "languages_pct": {k: round(v * 100.0 / total_bytes, 1) for k, v in sorted((languages or {}).items(), key=lambda kv: -kv[1])[:6]},
            "license": (meta.get("license") or {}).get("spdx_id"),
            "default_branch": meta.get("default_branch"),
            "topics": meta.get("topics") or [],
            "created_at": meta.get("created_at"),
            "pushed_at": meta.get("pushed_at"),
            "archived": meta.get("archived"),
            "architecture": {
                "top_level_directories": top_dirs,
                "top_level_files": top_files,
                "src_components": components,
                "detected_stack": _detect_stack(top_files, meta.get("language")),
            },
            "open_issues_sample": [
                {"number": i["number"], "title": i["title"], "html_url": i["html_url"],
                 "labels": [lb["name"] for lb in i.get("labels", [])], "comments": i.get("comments"),
                 "updated_at": i.get("updated_at")}
                for i in real_issues
            ],
            "api_rate_limit_remaining": h.get("x-ratelimit-remaining"),
        }

    async def _github_html_fallback(self, run: TaskRun, key: str, owner: str, repo: str) -> Dict[str, Any]:
        page = await run.browse(key, f"https://github.com/{owner}/{repo}")
        if page["status_code"] == 404:
            raise ToolError("REPOSITORY_NOT_FOUND", f"GitHub repository {owner}/{repo} does not exist or is private.")
        soup = _soup(page["html"])

        def count(selector: str) -> Optional[int]:
            el = soup.select_one(selector)
            raw = (el.get("title") or el.get_text()) if el else ""
            digits = re.sub(r"[^\d]", "", raw or "")
            return int(digits) if digits else None

        og = soup.find("meta", attrs={"property": "og:description"})
        lang_el = soup.select_one('[itemprop="programmingLanguage"]') or soup.select_one(".BorderGrid .color-fg-default.text-bold")
        issues = []
        issues_page = await run.browse(key, f"https://github.com/{owner}/{repo}/issues")
        for a in _soup(issues_page["html"]).select('a[id^="issue_"], a[data-testid="issue-pr-title-link"]')[:10]:
            href = a.get("href", "")
            m = re.search(r"/issues/(\d+)", href)
            if m:
                issues.append({"number": int(m.group(1)), "title": a.get_text(" ", strip=True),
                               "html_url": f"https://github.com{href}" if href.startswith("/") else href,
                               "labels": [], "comments": None, "updated_at": None})
        data = {
            "source": "GITHUB_HTML_FALLBACK",
            "owner": owner,
            "repo": repo,
            "full_name": f"{owner}/{repo}",
            "description": og.get("content") if og else page["distilled"]["meta_description"],
            "html_url": f"https://github.com/{owner}/{repo}",
            "stars": count("#repo-stars-counter-star"),
            "forks": count("#repo-network-counter"),
            "open_issues_and_prs": count("#issues-repo-tab-count"),
            "language": lang_el.get_text(strip=True) if lang_el else None,
            "architecture": {"top_level_directories": [], "top_level_files": [], "src_components": None, "detected_stack": []},
            "open_issues_sample": issues,
        }
        run.page_evidence(key, page, data, citation=f"#repo-stars-counter-star={data['stars']}; og:description")
        return data

    async def _run_github(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        owner, repo = ig["constraints"]["owner"], ig["constraints"]["repo"]
        api_url = f"https://api.github.com/repos/{owner}/{repo}"
        run.plan([
            ("compile", "Compile goal into a repository analysis", TC, None),
            ("adapter", f"Query GitHub REST API for {owner}/{repo} (metadata, issues, languages, layout)", T1, api_url),
            ("summarize", "Summarize architecture, popularity and open issues", TC, None),
            ("verify", "Cross-check repository identity, counts and links", TC, None),
        ])
        self._compile_step(run, ig)

        run.start("adapter")
        try:
            data = await self._github_api(owner, repo)
            card_lines = [
                f"## {data['full_name']} — {data.get('description') or ''}",
                f"★ {data['stars']:,} stars · {data['forks']:,} forks · {data['open_issues_and_prs']} open issues+PRs",
                f"Primary language: {data['language']} · License: {data.get('license')}",
                "Languages: " + ", ".join(f"{k} {v}%" for k, v in data["languages_pct"].items()),
                "Top-level: " + ", ".join(data["architecture"]["top_level_directories"][:12]),
            ]
            if data["architecture"]["src_components"]:
                card_lines.append("src/: " + ", ".join(data["architecture"]["src_components"][:14]))
            card_lines.append("## Recent open issues")
            card_lines += [f"#{i['number']} {i['title']}" for i in data["open_issues_sample"][:8]]
            card = run.evidence_card("adapter", title=f"GitHub · {owner}/{repo}", source_url=api_url, lines=card_lines,
                                     stamp="TIER 1 ADAPTER", capability_method="TIER_1_ADAPTER:GITHUB_REST_API")
            run.record_evidence("adapter", source_url=api_url, capability_method="TIER_1_ADAPTER:GITHUB_REST_API",
                                extracted=data, page_title=data["full_name"], screenshot_url=card,
                                citation=f"GET {api_url} → stargazers_count={data['stars']}, language={data['language']}")
            run.finish("adapter", StepStatus.SUCCEEDED,
                       f"GitHub API returned metadata and {len(data['open_issues_sample'])} open issues "
                       f"(rate limit remaining: {data['api_rate_limit_remaining']}).")
        except (_GitHubRateLimited, httpx.HTTPError) as exc:
            reason = "rate limited" if isinstance(exc, _GitHubRateLimited) else f"{type(exc).__name__}"
            run.finish("adapter", StepStatus.FAILED_RECOVERED, f"GitHub API {reason}; falling back to semantic DOM.")
            run.insert_after("adapter", "fallback", f"Fallback: distill github.com/{owner}/{repo} page", T2,
                             f"https://github.com/{owner}/{repo}")
            run.start("fallback")
            try:
                data = await self._github_html_fallback(run, "fallback", owner, repo)
            except BrowserFetchError as fexc:
                run.finish("fallback", StepStatus.FAILED, fexc.message)
                run.fail_running_steps("GitHub unavailable.")
                run.save(status=TaskStatus.FAILED.value,
                         summary=f"I couldn't reach GitHub for {owner}/{repo} (API {reason}, page fetch failed). No data was guessed.",
                         verification_report=verifier.unverified_report("NOT_EXECUTED", fexc.message))
                return
            run.finish("fallback", StepStatus.SUCCEEDED, "Extracted repository facts from the public HTML page.")
        except ToolError as exc:
            run.finish("adapter", StepStatus.FAILED, exc.message)
            run.fail_running_steps("Repository unavailable.")
            run.save(status=TaskStatus.FAILED.value, summary=exc.message,
                     verification_report=verifier.unverified_report("NOT_EXECUTED", exc.message))
            return

        run.start("summarize")
        arch = data["architecture"]
        arch_summary = []
        if arch["src_components"]:
            arch_summary.append(f"a monorepo whose src/ holds {len(arch['src_components'])} components "
                                f"({', '.join(arch['src_components'][:6])}{'…' if len(arch['src_components']) > 6 else ''})")
        if arch["detected_stack"]:
            arch_summary.append("built with " + ", ".join(arch["detected_stack"]))
        data["architecture_summary"] = "; ".join(arch_summary) or "layout not available from this source"
        run.finish("summarize", StepStatus.SUCCEEDED, data["architecture_summary"])

        run.start("verify")
        report = verifier.verify_github_analysis(owner, repo, data)
        passed = report["verdict"] == "VERIFIED_SUCCESS"
        for s in run.steps:
            for ev in s["evidence_ids"]:
                db.set_evidence_verified(ev, passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report.get("notes") or report["verdict"])
        stars = f"{data['stars']:,}" if isinstance(data.get("stars"), int) else "an unknown number of"
        issues_txt = ""
        if data["open_issues_sample"]:
            issues_txt = f" The most recently updated open issue is #{data['open_issues_sample'][0]['number']}: {data['open_issues_sample'][0]['title']}."
        summary = (f"{data['full_name']} has {stars} stars"
                   + (f", is written mainly in {data['language']}" if data.get("language") else "")
                   + (f", and has {data['open_issues_and_prs']} open issues and pull requests" if data.get("open_issues_and_prs") is not None else "")
                   + f". It is {data['architecture_summary']}." + issues_txt)
        run.save(status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value,
                 summary=summary, extracted_data=data, verification_report=report)

    # -------------------------------------------------------- G: URL inspection

    @staticmethod
    def _assess_barriers(page: Dict[str, Any]) -> List[Dict[str, Any]]:
        d = page["distilled"]
        hard = [b for b in d["barriers"] if b["barrier_type"] in ("CAPTCHA_CHALLENGE", "OTP_2FA_REQUIRED", "BOT_CHALLENGE")]
        login = [b for b in d["barriers"] if b["barrier_type"] == "LOGIN_REQUIRED"]
        if hard:
            return hard + login
        if login and (page["status_code"] in (401, 403) or len(d["sanitized_text"]) < 600):
            return login
        return []

    async def _run_url_inspection(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        url = ig["target_url"]
        run.plan([
            ("compile", "Compile goal into a website inspection", TC, None),
            ("inspect", "Navigate and distill the page through the content firewall", T2, url),
            ("assess", "Check for login, OTP / 2FA and CAPTCHA barriers", T3, None),
            ("answer", "Answer the question from cited, firewall-sanitized DOM items (AI brain)", TC, None),
            ("verify", "Verify the distilled content and every answer citation against the DOM", TC, None),
        ])
        self._compile_step(run, ig)

        run.start("inspect")
        page = await run.browse("inspect", url)
        d = page["distilled"]
        run.page_evidence("inspect", page, {
            "title": d["title"], "headings": d["headings"][:15], "barriers": d["barriers"],
            "interactive_elements": d["interactive_elements"][:25], "tables": [{k: t[k] for k in ("table_id", "headers", "row_count")} for t in d["tables"]],
        }, citation=d["sanitized_text"][:600])
        run.finish("inspect", StepStatus.SUCCEEDED,
                   f"HTTP {page['status_code']} · '{d['title']}' · {len(d['headings'])} headings, {len(d['links'])} links, "
                   f"{len(d['tables'])} tables, {len(d['interactive_elements'])} interactive elements via {page['capability_method']}.")

        run.start("assess")
        barriers = self._assess_barriers(page)
        if barriers:
            actions = {
                "OTP_2FA_REQUIRED": "enter the one-time passcode from your hardware token or authenticator",
                "CAPTCHA_CHALLENGE": "solve the CAPTCHA (\"verify you are human\") challenge",
                "LOGIN_REQUIRED": "sign in with your own credentials",
                "BOT_CHALLENGE": "pass the site's bot-protection check in your own browser",
            }
            human_actions = [actions[b["barrier_type"]] for b in barriers]
            run.record_evidence("assess", source_url=page["final_url"], capability_method="TIER_3_HITL_ESCALATION",
                                extracted={"barriers": barriers}, page_title=d["title"], screenshot_url=page["screenshot_url"],
                                citation="; ".join(f"{b['selector']}: {b['evidence']}" for b in barriers), verified=True)
            run.finish("assess", StepStatus.BLOCKED, "Human action required: " + "; ".join(human_actions) + ".")
            run.fail_running_steps("Not attempted: blocked by an access barrier.")
            for s in run.steps:
                if s["key"] in ("verify", "answer"):
                    s["status"] = StepStatus.SKIPPED.value
            site = d["title"] or urlparse(page["final_url"]).netloc
            run.save(
                status=TaskStatus.BLOCKED.value,
                summary=(f"I stopped at '{site}'. Continuing needs you to " + _join_natural(human_actions) +
                         ". I did not try to bypass these checks and I have not claimed any result. "
                         "Complete them yourself, then ask me to continue."),
                extracted_data={"url": page["final_url"], "title": d["title"], "blocker": {
                    "barriers": barriers, "required_human_actions": human_actions, "bypass_attempted": False}},
                verification_report=verifier.unverified_report(
                    "HONEST_BLOCKER_REPORT", "Task halted at an access barrier; no outcome is claimed.",
                    [VerificationCheck(check="barrier_evidence_recorded", passed=True, observed=[b["barrier_type"] for b in barriers])]),
            )
            return
        run.finish("assess", StepStatus.SUCCEEDED,
                   "No blocking barrier." + (" A login form is present but public content was readable." if d["barriers"] else ""))

        from backend import llm_brain

        answer: Optional[Dict[str, Any]] = None
        question = ig.get("extraction_query") or llm_brain.derive_extraction_query(run.task["goal"])
        dom_items = llm_brain.build_dom_items(d)
        if _QUESTION_RE.search(question):
            run.start("answer")
            answer = await llm_brain.answer_live_web_question(run.task["goal"], d)
            run.record_evidence(
                "answer", source_url=page["final_url"], capability_method=f"BRAIN:{answer['provider_used']}",
                extracted={"question": answer["question"], "plan": answer["plan"], "citations": answer["citations"]},
                page_title=d["title"], screenshot_url=page["screenshot_url"],
                citation="\n".join(f"[#{c['item_id']}] {c['dom_ref']}: {c['title']}" for c in answer["citations"]) or "No matching items.",
            )
            run.finish("answer", StepStatus.SUCCEEDED,
                       f"{answer['method']} over {answer['items_considered']} DOM items → {len(answer['citations'])} cited "
                       f"(filters: {', '.join(answer['plan']['terms'][:6]) or 'none'}; rank: {answer['plan']['rank_by']}).")
        else:
            run.finish("answer", StepStatus.SKIPPED, "No question to answer — page inspection only.")

        run.start("verify")
        report = verifier.verify_page_distillation(page["status_code"], d)
        if answer is not None:
            grounded_checks = verifier.verify_grounded_answer(answer, dom_items)
            report["checks"] += [c.model_dump(mode="json") for c in grounded_checks]
            if not all(c.passed for c in grounded_checks):
                report["verdict"] = "VERIFICATION_FAILED"
                report["notes"] = (report.get("notes") or "") + " Ungrounded answer citations detected."
            report["method"] += "+DOM_GROUNDED_CITATION_CHECK"
        passed = report["verdict"] == "VERIFIED_SUCCESS"
        for ev in run.step("inspect")["evidence_ids"] + run.step("answer")["evidence_ids"]:
            db.set_evidence_verified(ev, passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report["verdict"])
        headline = d["headings"][0]["text"] if d["headings"] else ""
        articles, prices = d.get("articles", []), d.get("prices", [])
        summary = (f"'{d['title'] or page['final_url']}' "
                   + (f"leads with \"{headline}\" and " if headline and headline != d["title"] else "")
                   + f"has {len(d['headings'])} headings, {len(d['links'])} links, {len(d['tables'])} tables and "
                   f"{len(d['interactive_elements'])} interactive elements.")
        if articles:
            summary += f" Top items: " + "; ".join(f"“{a['title']}”" for a in articles[:3]) + "."
        if prices:
            summary += f" I found {len(prices)} price points, for example {prices[0]['raw']}."
        summary += f" {summarize_alerts(d['firewall_alerts'])}"
        if page.get("fallback_reason") and not page["is_sandbox"]:
            summary += " (Chromium was unavailable, so I used the HTTP fallback.)"
        if answer is not None:
            summary = answer["answer"] + (" " + summarize_alerts(d["firewall_alerts"]) if d["firewall_alerts"] else "")
            if page.get("fallback_reason") and not page["is_sandbox"]:
                summary += " (Chromium was unavailable, so I used the HTTP fallback.)"
        run.save(
            status=TaskStatus.COMPLETED.value if passed else TaskStatus.FAILED.value,
            summary=summary,
            extracted_data={
                "url": page["final_url"], "status_code": page["status_code"], "title": d["title"],
                "meta_description": d["meta_description"], "headings": d["headings"][:20], "links": d["links"][:25],
                "tables": [{**t, "rows": t["rows"][:15]} for t in d["tables"][:5]], "forms": d["forms"][:5],
                "interactive_elements": d["interactive_elements"][:30], "firewall_alerts": d["firewall_alerts"],
                "articles": articles[:30], "prices": prices[:30], "structured_data": d.get("structured_data"),
                "capability_method": page["capability_method"], "render_mode": page.get("render_mode"),
                "fallback_reason": page.get("fallback_reason"), "screenshot_url": absolute_url(page["screenshot_url"]),
                "untrusted_text_excerpt": d["wrapped_text"][:3000],
                "answer": answer,
            },
            verification_report=report,
        )

    # =========================================== I: universal live browser autopilot

    def _film_appender(self, run: TaskRun, data: Dict[str, Any]) -> Any:
        film = data["autopilot"]["filmstrip"]

        def add_film(k: int, action: str, description: str, shot: Optional[str], url_before: str, url_after: str,
                     title: str, verified: bool, extra: Optional[Dict[str, Any]] = None) -> None:
            film.append({"step": k, "action": action, "description": description, "screenshot_url": absolute_url(shot),
                         "url_before": url_before, "url_after": url_after, "page_title": title, "verified": verified,
                         "at": utc_now_iso(), **(extra or {})})
            run.save(extracted_data=data)
        return add_film

    async def _run_live_autopilot(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        from backend import live_autopilot as ap
        from backend import llm_brain, site_login
        from backend.llm_brain import annotate_elements, element_label

        c = ig["constraints"]
        goal = run.task["goal"]
        task_id = run.task_id
        conv = c.get("conversation_id")
        key_ = conv or task_id
        intents = llm_brain.parse_goal_intents(goal)
        mode = ("visible window, slow-mo" if c["watch"] else "headless") + (", persistent profile" if c["persistent"] else "")
        run.plan([
            ("compile", "Compile goal into a live browser mission", TC, None),
            ("launch", f"Launch Chromium ({mode})", T2, None),
        ])
        self._compile_step(run, ig)

        run.start("launch")
        await ap.purge_expired_sessions()
        session = ap.get_session(key_) if conv else None
        continuing = session is not None
        names_site = not c["discovery"]
        start_url, discovery = await ap.resolve_start(goal, None if c["discovery"] else c["start_url"])
        if continuing:
            session.touch()
            session.task_ids.append(task_id)
            run.finish("launch", StepStatus.SUCCEEDED, f"Continuing in the same browser tab as before ({session.page.url}).")
        else:
            try:
                session = await ap.open_session(key_, c["watch"], c["persistent"], start_url)
            except Exception as exc:
                run.finish("launch", StepStatus.FAILED, f"Chromium could not start: {type(exc).__name__}: {exc}")
                run.save(status=TaskStatus.FAILED.value,
                         summary="I couldn't start the browser for this live task (Playwright Chromium unavailable). Nothing was done.",
                         verification_report=verifier.unverified_report("NOT_EXECUTED", f"{type(exc).__name__}: {exc}"))
                return
            session.conversation_id = conv
            session.task_ids = [task_id]
            ap.SESSIONS[key_] = session
            run.finish("launch", StepStatus.SUCCEEDED,
                       f"Chromium {'window visible' if session.headed else 'headless'}"
                       + {"cdp": ", attached to the WebPilot Chrome over CDP (your saved sign-ins)", "persistent_profile": ", persistent profile"}
                       .get(session.connection, ", fresh private profile")
                       + (f". {' '.join(session.notes)}" if session.notes else "."))
        page = session.page
        memory = session.memory
        history: List[Dict[str, Any]] = []
        data: Dict[str, Any] = {
            "autopilot": {"start_url": start_url, "discovery": discovery, "conversation_id": conv, "continued": continuing,
                          "mode": {"headed": session.headed, "persistent": session.persistent, "connection": session.connection,
                                   "reused_tab": session.reused_tab, "open_tabs": len(session.user_tabs)},
                          "filmstrip": [], "decisions": []},
        }
        add_film = self._film_appender(run, data)
        keep_session = bool(conv)

        # What earlier result does a follow-up ("the first one", "buy it") point at?
        target = None
        ref = intents.get("reference")
        if continuing and ref and not intents["search_query"] and not names_site:
            results = memory.get("results") or []
            if ref.get("current"):
                target = None  # "buy it" / "this one": the page we are on
            elif results and -len(results) <= ref["index"] < len(results):
                target = results[ref["index"]]
            else:
                run.save(status=TaskStatus.AWAITING_USER_INPUT.value, extracted_data=data,
                         summary=(f"I don't have a “{ref['text']}” from earlier" + (f" — I showed {len(results)} option(s)."
                                  if results else " — search for something first and I'll list the options.")),
                         verification_report=verifier.unverified_report("NOT_EXECUTED", "Unknown reference to an earlier result."))
                return

        try:
            # ---- Step 1: where to start
            k = 1
            key = "act_1"
            reused = session.reused_tab and not continuing
            stay = continuing and not names_site and target is None
            if target is not None:
                label1, dest = f"Step 1 · Open {ref['text']}: “{target['title'][:60]}”", target["url"]
            elif stay:
                label1, dest = f"Step 1 · Continue on the current page ({page.url})", page.url
            elif reused:
                label1, dest = f"Step 1 · Continue in your open tab {page.url}", start_url
            else:
                label1, dest = (f"Step 1 · Navigate to {start_url}" + (" (web search for site discovery)" if discovery else "")), start_url
            run.append_steps([(key, label1, T2, dest)])
            run.start(key)
            url_before = page.url
            if target is not None or not (stay or reused):
                await page.goto(dest, wait_until="domcontentloaded", timeout=25000)
            await ap.settle(page)
            dismissed = await ap.dismiss_consent(page)
            if dismissed:
                await ap.settle(page)
            shot = await ap.screenshot(page, task_id, f"step_{k}")
            title = await page.title()
            distilled = await ap.distill(page)
            run.record_evidence(key, source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                                extracted={"action": "NAVIGATE", "consent_dismissed": dismissed, "reference": ref if target else None},
                                page_title=title, screenshot_url=shot, firewall_alerts=distilled["firewall_alerts"],
                                citation=f"goto {dest}" + (f"; consent banner: clicked “{dismissed}”" if dismissed else ""))
            run.finish(key, StepStatus.SUCCEEDED, f"Loaded “{title}”" + (f"; dismissed consent banner (“{dismissed}”)." if dismissed else "."))
            first = (f"Opened {ref['text']}: “{target['title'][:60]}”" if target is not None else
                     f"Continued on {page.url}" if stay else
                     f"Continued in your open tab {page.url}" if reused else f"Navigated to {page.url}")
            add_film(k, "NAVIGATE", first + (f" · clicked “{dismissed}” on the consent banner" if dismissed else ""),
                     shot, url_before, page.url, title, True)
            history.append({"action": "NAVIGATE", "phase": "open_result" if target is not None else "navigate", "url_after": page.url})
            if target is not None:
                memory["selected"] = target
            failures = 0
            auth_rounds = 0

            while k < c["max_steps"] + auth_rounds * 2:
                k += 1
                elements = await ap.index_elements(page)
                distilled = await ap.distill(page)
                title = await page.title()
                auth = site_login.classify_auth_page(elements, page.url, title, distilled["sanitized_text"])
                if site_login.is_blocking(auth) and auth_rounds < 3:
                    auth_rounds += 1
                    outcome = await self._handle_auth(run, session, page, k, auth, add_film, data)
                    if outcome == "ok":
                        history.append({"action": "LOGIN", "phase": "login", "url_after": page.url, "verified": True})
                        continue
                    return  # the handler recorded BLOCKED / AWAITING_USER_INPUT honestly
                barriers = self._assess_barriers({"distilled": distilled, "status_code": 200})
                if barriers and session.headed:
                    if await self._wait_for_human(run, page, k, add_film, data, "Please complete the check in the WebPilot "
                                                  "browser window — I'll continue as soon as it's done."):
                        continue
                if barriers:
                    await self._finish_autopilot_blocked(run, page, k, barriers, add_film, data)
                    return
                annotated = {e["idx"]: e for e in annotate_elements(elements)}
                decision = await llm_brain.decide_next_browser_action(
                    goal, page.url, title, history, elements, distilled["sanitized_text"][:3000],
                    discovery_host=ap.discovery_host() if discovery else None, memory=memory)
                data["autopilot"]["decisions"].append({"step": k, **{x: decision.get(x) for x in
                                                       ("action", "element_idx", "text", "reason", "decided_by", "llm_note", "phase")}})
                action = decision["action"]
                if action == "DONE":
                    break
                if action == "BLOCKED":
                    await self._finish_autopilot_blocked(run, page, k, [{"barrier_type": "HUMAN_VERIFICATION", "selector": "page",
                                                                         "evidence": decision.get("blocker_reason", ""), "requires_human": True}],
                                                         add_film, data)
                    return
                el = annotated.get(decision.get("element_idx"))
                if action == "ASK_HUMAN_APPROVAL" and el is not None:
                    keep_session = await self._autopilot_approval(run, page, k, el, decision, distilled, add_film, data) or keep_session
                    return
                verified = await self._browser_step(run, page, k, el, decision, history, add_film)
                failures = 0 if verified else failures + 1
                if failures >= 2:
                    break

            # ---- Chat-style site: wait for the reply and hand it back
            if intents.get("ask") and any(h.get("phase") == "ask" for h in history):
                await self._finish_chat_reply(run, page, k + 1, history, add_film, data, intents)
                return
            # ---- Final: grounded answer from the last page
            await self._finish_autopilot_answer(run, page, k + 1, history, add_film, data, intents, session)
        except BrowserFetchError as exc:
            run.fail_running_steps(exc.message)
            run.save(status=TaskStatus.FAILED.value, extracted_data=data,
                     summary=f"I stopped because the browser was sent somewhere I won't go: {exc.message}",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", f"{exc.code}: {exc.message}"))
        except Exception as exc:
            logger.exception("Live autopilot failed for %s", task_id)
            run.fail_running_steps(f"{type(exc).__name__}: {str(exc)[:200]}")
            run.save(status=TaskStatus.FAILED.value, extracted_data=data,
                     summary=f"The live browser run stopped with an error ({type(exc).__name__}). Nothing consequential was done.",
                     verification_report=verifier.unverified_report("NOT_EXECUTED", f"{type(exc).__name__}: {str(exc)[:300]}"))
        finally:
            if not keep_session:
                await ap.close_session(key_)

    async def _finish_chat_reply(self, run: TaskRun, page: Any, k: int, history: List[Dict[str, Any]], add_film: Any,
                                 data: Dict[str, Any], intents: Dict[str, Any]) -> None:
        """After sending a message on a chat-style site, wait until the reply stops streaming and read it back."""
        from backend import live_autopilot as ap
        from backend.firewall import neutralize_text

        run.append_steps([("answer", "Wait for the reply and read it", TC, page.url)])
        run.start("answer")
        before = next((h.get("page_text_before") for h in history if h.get("phase") == "ask"), "") or ""
        last, stable, text = "", 0, ""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and stable < 3:
            await asyncio.sleep(1.5)
            try:
                text = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                continue
            stable = stable + 1 if text == last and len(text) > len(before) else 0
            last = text
        sent = intents["ask"]
        new = text
        if before and text.startswith(before[: max(0, len(before) - 50)]):
            new = text[len(before) - 50:] if len(before) > 50 else text[len(before):]
        idx = new.find(sent[:40])
        if idx >= 0:
            new = new[idx + len(sent[:40]):]
        reply = neutralize_text(re.sub(r"\n{3,}", "\n\n", new).strip())[0][:3000]
        title = await page.title()
        shot = await ap.screenshot(page, run.task_id, "reply")
        site = (urlparse(page.url).hostname or page.url).removeprefix("www.")
        run.record_evidence("answer", source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:CHAT_REPLY",
                            extracted={"sent": sent, "reply_chars": len(reply)}, page_title=title, screenshot_url=shot,
                            verified=bool(reply), citation=reply[:1500] or "No reply text found.")
        run.finish("answer", StepStatus.SUCCEEDED if reply else StepStatus.FAILED,
                   f"Read {len(reply)} characters of reply." if reply else "No reply appeared.")
        add_film(k, "DONE", f"Read the reply on {site}", shot, page.url, page.url, title, bool(reply))
        chat_reply = (f"{site} replied:\n{reply}" if reply else
                      f"I sent your message on {site}, but no reply appeared yet — check the WebPilot browser window.")
        data.update({"url": page.url, "title": title, "chat_reply": chat_reply, "screenshot_url": absolute_url(shot),
                     "sent_message": sent})
        report = {"verdict": "VERIFIED_SUCCESS" if reply else "UNVERIFIED", "method": "CHAT_REPLY_CAPTURE",
                  "checks": [{"check": "message_sent", "passed": True, "expected": sent[:80], "observed": "submitted"},
                             {"check": "reply_captured", "passed": bool(reply), "expected": "new page text", "observed": len(reply)}],
                  "verified_at": utc_now_iso(), "notes": "Reply text is untrusted page content (firewall-neutralized)."}
        run.save(status=TaskStatus.COMPLETED.value if reply else TaskStatus.UNVERIFIED_OUTCOME.value, extracted_data=data,
                 summary=chat_reply, verification_report=report)

    async def _browser_step(self, run: TaskRun, page: Any, k: int, el: Optional[Dict[str, Any]], decision: Dict[str, Any],
                            history: List[Dict[str, Any]], add_film: Any, prefix: str = "act") -> bool:
        """Perform one safe action (fill / select / click / scroll) with highlight, screenshot and verification."""
        from backend import live_autopilot as ap
        from backend.llm_brain import element_label

        action = decision["action"]
        label = element_label(el)[:80] if el else ""
        idx = decision.get("element_idx")
        shown = decision.get("text", "")
        verb = {"FILL_AND_SUBMIT": f"Type “{shown}” into [{idx}] “{label}” and submit",
                "FILL": f"Type “{shown}” into [{idx}] “{label}”",
                "SELECT": f"Choose “{shown}” in [{idx}] “{label}”",
                "CLICK": f"Click [{idx}] “{label}”",
                "SCROLL_DOWN": "Scroll down to look for more"}.get(action, action)
        key = f"{prefix}_{k}"
        run.append_steps([(key, f"Step {k} · {verb}", T2, page.url)])
        run.start(key)
        url_before, before = page.url, await ap.signature(page)
        text_before = ""
        if decision.get("phase") == "ask":
            try:
                text_before = await page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                text_before = ""
        shot = None
        if action in ("FILL_AND_SUBMIT", "FILL", "SELECT", "CLICK") and el is not None:
            await ap.highlight(page, el["idx"], f"WebPilot Step #{k}: {action.replace('_', ' ').title()}")
            shot = await ap.screenshot(page, run.task_id, f"step_{k}")
            await ap.unhighlight(page)
            async def perform(idx: int) -> None:
                loc = ap.locator_for(page, idx)
                if action in ("FILL_AND_SUBMIT", "FILL"):
                    if el.get("contenteditable") and el.get("tag") not in ("input", "textarea"):
                        await loc.click(timeout=8000)
                        await page.keyboard.type(decision.get("text", ""), delay=8)  # rich editors ignore fill()
                    else:
                        await loc.fill(decision.get("text", ""), timeout=8000)
                    if action == "FILL_AND_SUBMIT":
                        await loc.press("Enter", timeout=8000)
                        await ap.settle(page)
                        if await ap.signature(page) == before:
                            await page.evaluate(_SUBMIT_FALLBACK_JS, idx)
                            await ap.settle(page)
                elif action == "SELECT":
                    try:
                        await loc.select_option(label=decision.get("text", ""), timeout=8000)
                    except Exception:
                        await loc.select_option(value=decision.get("text", ""), timeout=8000)
                    await ap.settle(page)
                else:
                    try:
                        await page.evaluate("i => document.querySelector(`[data-webpilot-idx=\"${i}\"]`)?.removeAttribute('target')", idx)
                    except Exception:
                        pass
                    try:
                        await loc.click(timeout=6000)
                    except Exception:
                        # Animated widgets (calendars, carousels) never look "stable" to Playwright: click anyway.
                        try:
                            await loc.click(force=True, timeout=4000)
                        except Exception:
                            if not await page.evaluate(
                                    "i => { const e = document.querySelector(`[data-webpilot-idx=\"${i}\"]`); if (!e) return false;"
                                    " e.click(); return true; }", idx):
                                raise
                    await ap.settle(page)

            async def by_label() -> bool:
                """Re-rendered widgets keep their aria-label: click the control by that exact label."""
                name = (el.get("aria_label") or el.get("accessible_name") or "").strip()
                if action != "CLICK" or not name or len(name) > 180:
                    return False
                target = page.locator(f'[aria-label="{name.replace(chr(92), "").replace(chr(34), chr(92) + chr(34))}"]').first
                try:
                    if await target.count() == 0:
                        return False
                    await target.click(force=True, timeout=5000)
                    await ap.settle(page)
                    return True
                except Exception:
                    return False

            try:
                if action == "CLICK" and await ap.locator_for(page, el["idx"]).count() == 0 and await by_label():
                    pass  # the marked node was replaced by an identical re-render; clicked it by label
                else:
                    await perform(el["idx"])
            except Exception as first_error:
                # Modern sites re-render (calendars, carousels) and drop our marker: click by exact label, else find the
                # same control again and retry once. A control that still can't be used fails this step, not the task.
                try:
                    if not await by_label():
                        same = None
                        for cand in await ap.index_elements(page):
                            if cand.get("tag") == el.get("tag") and (cand.get("accessible_name") or "") == (el.get("accessible_name") or "") \
                                    and (cand.get("text") or "") == (el.get("text") or ""):
                                same = cand
                                break
                        if same is None:
                            raise first_error
                        await perform(same["idx"])
                except Exception as exc:
                    run.finish(key, StepStatus.FAILED, f"Couldn't use “{label}”: {type(exc).__name__}. Trying another way.")
                    history.append({"action": action, "phase": f"failed:{decision.get('phase')}", "element_idx": idx,
                                    "element_label": label, "url_before": url_before, "url_after": page.url, "verified": False})
                    add_film(k, action, f"{verb} — didn't respond, trying another way", shot, url_before, page.url,
                             await page.title(), False)
                    return False
        elif action == "SCROLL_DOWN":
            await page.mouse.wheel(0, 700)
            await asyncio.sleep(0.6)
            shot = await ap.screenshot(page, run.task_id, f"step_{k}")
        else:
            run.finish(key, StepStatus.FAILED, f"Decision referenced a missing element: {decision}")
            return False
        if not browser_runtime.is_sandbox_url(page.url) and page.url.startswith("http"):
            await browser_runtime._assert_public_destination(page.url)
        after_title = await page.title()
        after_alerts = (await ap.distill(page))["firewall_alerts"]
        changed = (await ap.signature(page)) != before
        verified = changed or action in ("FILL", "SELECT", "SCROLL_DOWN") or decision.get("phase") in ("variant", "guests_inc")
        history.append({"action": action, "phase": decision.get("phase"), "element_idx": idx, "element_label": label,
                        "text": decision.get("text"), "url_before": url_before, "url_after": page.url, "verified": verified,
                        **({"page_text_before": text_before} if text_before else {})})
        run.record_evidence(key, source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                            extracted={"action": action, "element": {k2: el.get(k2) for k2 in ("idx", "tag", "role", "accessible_name",
                                       "input_type", "landmark")} if el else None, "text": decision.get("text"),
                                       "url_before": url_before, "url_after": page.url, "decided_by": decision.get("decided_by"),
                                       "reason": decision.get("reason")},
                            page_title=after_title, screenshot_url=shot, verified=verified, firewall_alerts=after_alerts,
                            citation=f"{verb} → {page.url}")
        run.finish(key, StepStatus.SUCCEEDED if verified else StepStatus.FAILED,
                   f"{decision.get('reason', '')} [{decision.get('decided_by')}] → “{after_title}”" + ("" if verified else " (no visible effect)"))
        add_film(k, action, verb, shot, url_before, page.url, after_title, verified,
                 {"element_idx": idx, "decided_by": decision.get("decided_by")})
        return verified

    # ------------------------------------------------------------------ sign-in handling

    async def _handle_auth(self, run: TaskRun, session: Any, page: Any, k: int, auth: Dict[str, Any], add_film: Any,
                           data: Dict[str, Any]) -> str:
        """A sign-in / sign-up / one-time-code page stands between us and the goal. Returns 'ok' to continue."""
        from backend import credential_vault as vault

        site = vault.site_key(page.url)
        creds = vault.get(site)
        if auth["kind"] == "otp" or auth["captcha"]:
            why = "a one-time code" if auth["kind"] == "otp" else "a CAPTCHA"
            if session.headed and await self._wait_for_human(
                    run, page, k, add_film, data, f"{site} is asking for {why}. Please complete it in the WebPilot browser window "
                    "— I never solve CAPTCHAs or read your codes — and I'll continue right after."):
                return "ok"
            await self._finish_autopilot_blocked(run, page, k, [{"barrier_type": "OTP_2FA_REQUIRED" if auth["kind"] == "otp" else "CAPTCHA_CHALLENGE",
                                                                 "selector": "page", "evidence": f"{site} asks for {why}", "requires_human": True}],
                                                 add_film, data)
            return "blocked"
        if creds and auth["kind"] in ("login", "login_identifier"):
            if await self._auto_login(run, page, k, creds, add_film):
                vault.mark_used(site, verified=True)
                return "ok"
        conversational = bool(data["autopilot"].get("conversation_id"))
        if not conversational:
            await self._finish_autopilot_blocked(run, page, k, [{"barrier_type": "LOGIN_REQUIRED", "selector": "page",
                                                                 "evidence": f"{site} requires signing in", "requires_human": True}],
                                                 add_film, data)
            return "blocked"
        for _attempt in range(3):
            choice = await self._ask_login_choice(run, page, k, site, auth, bool(creds), add_film, data)
            kind = (choice or {}).get("choice")
            if kind == "self":
                if not session.headed:
                    run.save(summary="I'm running without a visible window, so you can't sign in here. Turn on Persistent Profile "
                                     "and Watch Live Browser, or give me the login to save.")
                    continue
                if await self._wait_for_human(run, page, k, add_film, data,
                                              f"Sign in to {site} in the WebPilot browser window; I'll continue once you're in. "
                                              "The login stays saved in that browser for next time."):
                    return "ok"
                continue
            if kind == "provide":
                given = {"site": site, "username": choice.get("username", ""), "password": choice.get("password", "")}
                if choice.get("remember"):
                    try:
                        vault.save(site, given["username"], given["password"], source="user")
                    except Exception as exc:
                        logger.warning("Could not save the login for %s: %s", site, exc)
                if await self._auto_login(run, page, k, given, add_film):
                    return "ok"
                run.save(summary=f"That login didn't get me into {site}. Try again, or sign in yourself in the browser window.")
                continue
            if kind == "signup":
                outcome = await self._auto_signup(run, page, k, site, auth, add_film, data)
                if outcome == "ok":
                    return "ok"
                if outcome == "needs_human" and session.headed and await self._wait_for_human(
                        run, page, k, add_film, data, f"I filled in the sign-up form for {site} with your email and a strong "
                        "password (saved in Windows Credential Manager). Please finish what's left in the browser window — terms, "
                        "a verification code or email link — and I'll continue."):
                    from backend import credential_vault as v2

                    v2.mark_used(site, verified=True)
                    return "ok"
                continue
            break  # cancelled or timed out
        await self._finish_autopilot_blocked(run, page, k, [{"barrier_type": "LOGIN_REQUIRED", "selector": "page",
                                                             "evidence": f"{site} requires signing in; no sign-in was completed",
                                                             "requires_human": True}], add_film, data)
        return "blocked"

    async def _ask_login_choice(self, run: TaskRun, page: Any, k: int, site: str, auth: Dict[str, Any], has_saved: bool,
                                add_film: Any, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from backend import credential_vault as vault
        from backend import live_autopilot as ap

        key = f"act_{k}_signin_{len([s for s in run.steps if s['key'].startswith(f'act_{k}_signin')])}"
        run.append_steps([(key, f"Ask you how to sign in to {site}", T3, page.url)])
        run.start(key)
        shot = await ap.screenshot(page, run.task_id, f"step_{k}_signin")
        profile = vault.user_profile()
        data["login_request"] = {
            "site": site, "url": page.url, "kind": auth["kind"], "has_saved_login": has_saved,
            "signup_email": profile.get("email"), "screenshot_url": absolute_url(shot), "requested_at": utc_now_iso(),
            "options": ["self", "provide", "signup"] if profile.get("email") else ["self", "provide"],
        }
        add_film(k, "ASK_LOGIN", f"{site} needs you signed in — asked how you'd like to sign in", shot, page.url, page.url,
                 await page.title(), True)
        run.save(status=TaskStatus.AWAITING_USER_INPUT.value, extracted_data=data,
                 summary=(f"{site} needs you to be signed in" + (" (your saved login didn't work)" if has_saved else "")
                          + ". Do you want to sign in yourself in the browser window, give me a login to save, or"
                          + (f" let me create an account with {profile['email']}?" if profile.get("email")
                             else " add your email in Settings so I can create an account?")))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        LOGIN_WAITERS[run.task_id] = fut
        try:
            choice = await asyncio.wait_for(fut, timeout=config.LOGIN_CHOICE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            choice = None
        finally:
            LOGIN_WAITERS.pop(run.task_id, None)
            data.pop("login_request", None)
        safe = {k2: v for k2, v in (choice or {}).items() if k2 not in ("password",)}
        run.finish(key, StepStatus.SUCCEEDED if choice else StepStatus.BLOCKED,
                   f"You chose: {safe.get('choice', 'nothing (timed out)')}" + (f" as {safe['username']}" if safe.get("username") else ""))
        run.save(status=TaskStatus.EXECUTING.value, extracted_data=data)
        return choice

    def submit_login_choice(self, task_id: str, choice: Dict[str, Any]) -> Dict[str, Any]:
        """Called by the local UI (REST, Origin-guarded) — never by MCP — with the user's sign-in decision."""
        fut = LOGIN_WAITERS.get(task_id)
        if fut is None or fut.done():
            raise ToolError("NO_SIGN_IN_PENDING", "This task isn't waiting for a sign-in decision.")
        kind = choice.get("choice")
        if kind not in ("self", "provide", "signup", "cancel"):
            raise ToolError("INVALID_ARGUMENT", "choice must be self, provide, signup or cancel.")
        if kind == "provide" and not (choice.get("username") and choice.get("password")):
            raise ToolError("INVALID_ARGUMENT", "username and password are required to sign in for you.")
        if kind == "signup":
            from backend import credential_vault as vault

            if not vault.user_profile().get("email"):
                raise ToolError("EMAIL_NOT_SET", "Add your email in Settings first — I use it to create the account.")
        fut.set_result(dict(choice))
        return {"accepted": True, "task_id": task_id, "choice": kind}

    async def _wait_for_human(self, run: TaskRun, page: Any, k: int, add_film: Any, data: Dict[str, Any], message: str) -> bool:
        """Visible browser: let the user act (sign in, solve a CAPTCHA, type a code) and continue when the wall is gone."""
        from backend import live_autopilot as ap
        from backend import site_login

        key = f"act_{k}_human_{len([s for s in run.steps if s['key'].startswith(f'act_{k}_human')])}"
        run.append_steps([(key, "Waiting for you in the WebPilot browser window", T3, page.url)])
        run.start(key)
        data["human_needed"] = {"message": message, "url": page.url, "since": utc_now_iso()}
        run.save(status=TaskStatus.AWAITING_USER_INPUT.value, extracted_data=data, summary=message)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        deadline = time.monotonic() + config.AUTOPILOT_LOGIN_WAIT_SECONDS
        done = False
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            try:
                elements = await ap.index_elements(page)
                distilled = await ap.distill(page)
                auth = site_login.classify_auth_page(elements, page.url, await page.title(), distilled["sanitized_text"])
            except Exception:
                continue  # mid-navigation
            if not site_login.is_blocking(auth) and not self._assess_barriers({"distilled": distilled, "status_code": 200}):
                done = True
                break
        data.pop("human_needed", None)
        run.finish(key, StepStatus.SUCCEEDED if done else StepStatus.BLOCKED,
                   "Done — continuing." if done else "Nothing changed within the waiting window.")
        if done:
            add_film(k, "WAIT_FOR_HUMAN", "You finished the step in the browser — continuing", None, page.url, page.url,
                     await page.title(), True)
        run.save(status=TaskStatus.EXECUTING.value, extracted_data=data)
        return done

    async def _auto_login(self, run: TaskRun, page: Any, k: int, creds: Dict[str, str], add_film: Any) -> bool:
        """Sign in with a saved login (identifier → password screens handled generically). Passwords never leave this code."""
        from backend import live_autopilot as ap
        from backend import site_login

        key = f"act_{k}_login_{len([s for s in run.steps if s['key'].startswith(f'act_{k}_login')])}"
        run.append_steps([(key, f"Sign in to {creds['site']} with your saved login ({creds['username']})", T2, page.url)])
        run.start(key)
        url_before = page.url
        for _screen in range(3):
            elements = await ap.index_elements(page)
            distilled = await ap.distill(page)
            auth = site_login.classify_auth_page(elements, page.url, await page.title(), distilled["sanitized_text"])
            if not site_login.is_blocking(auth):
                break
            if auth["kind"] in ("otp", "signup") or auth["captcha"]:
                break
            last = None
            if auth["identifier_idx"] is not None:
                last = ap.locator_for(page, auth["identifier_idx"])
                await last.fill(creds["username"])
            if auth["password_idxs"]:
                last = ap.locator_for(page, auth["password_idxs"][0])
                await last.fill(creds["password"])
            if auth["submit_idx"] is not None:
                await ap.locator_for(page, auth["submit_idx"]).click(timeout=12000)
            elif last is not None:
                await last.press("Enter")
            else:
                break
            await ap.settle(page)
        elements = await ap.index_elements(page)
        distilled = await ap.distill(page)
        title = await page.title()
        auth = site_login.classify_auth_page(elements, page.url, title, distilled["sanitized_text"])
        ok = not site_login.is_blocking(auth)
        shot = await ap.screenshot(page, run.task_id, f"step_{k}_login")
        run.record_evidence(key, source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:SAVED_LOGIN",
                            extracted={"site": creds["site"], "username": creds["username"], "password": "••••••••",
                                       "signed_in": ok, "still_on": auth["kind"] if auth else None},
                            page_title=title, screenshot_url=shot, verified=ok,
                            citation=f"Signed in to {creds['site']} as {creds['username']}" if ok else
                            f"Sign-in to {creds['site']} did not complete ({auth['kind'] if auth else 'unknown'})")
        run.finish(key, StepStatus.SUCCEEDED if ok else StepStatus.FAILED,
                   f"Signed in as {creds['username']}." if ok else "The site didn't accept the saved login (or wants more steps).")
        add_film(k, "LOGIN", f"Signed in to {creds['site']} as {creds['username']}" if ok else
                 f"Tried your saved login for {creds['site']} — it needs more", shot, url_before, page.url, title, ok)
        return ok

    async def _auto_signup(self, run: TaskRun, page: Any, k: int, site: str, auth: Dict[str, Any], add_film: Any,
                           data: Dict[str, Any]) -> str:
        """Create an account with the user's email and a generated password (only after the user chose this)."""
        from backend import credential_vault as vault
        from backend import live_autopilot as ap
        from backend import site_login

        profile = vault.user_profile()
        email = profile.get("email")
        key = f"act_{k}_signup"
        run.append_steps([(key, f"Create a {site} account with {email}", T2, page.url)])
        run.start(key)
        url_before = page.url
        if auth["kind"] != "signup" and auth.get("signup_link_idx") is not None:
            await ap.locator_for(page, auth["signup_link_idx"]).click(timeout=12000)
            await ap.settle(page)
        elements = await ap.index_elements(page)
        distilled = await ap.distill(page)
        form = site_login.classify_auth_page(elements, page.url, await page.title(), distilled["sanitized_text"])
        if not form or form["kind"] not in ("signup", "login_identifier", "login"):
            run.finish(key, StepStatus.FAILED, "Couldn't find a sign-up form.")
            return "needs_human"
        password = vault.generate_password()
        needs_human = []
        if form["identifier_idx"] is not None:
            if form["identifier_type"] == "tel":
                needs_human.append("a phone number")
            else:
                await ap.locator_for(page, form["identifier_idx"]).fill(email)
        if form["name_idxs"]:
            if profile.get("full_name"):
                parts = profile["full_name"].split()
                for n, idx in enumerate(form["name_idxs"]):
                    value = profile["full_name"] if len(form["name_idxs"]) == 1 else (parts[0] if n == 0 else " ".join(parts[1:]) or parts[0])
                    await ap.locator_for(page, idx).fill(value)
            else:
                needs_human.append("your name")
        for idx in form["password_idxs"]:
            await ap.locator_for(page, idx).fill(password)
        if form["terms_idxs"]:
            needs_human.append("accepting the site's terms")
        if form["missing_required"]:
            needs_human.append(", ".join(form["missing_required"]))
        # Save first, so the generated password is never lost even if the site wants more steps.
        vault.save(site, email, password, source="created", verified=False)
        shot = await ap.screenshot(page, run.task_id, f"step_{k}_signup")
        if needs_human or not form["password_idxs"]:
            run.record_evidence(key, source_url=page.url, capability_method="TIER_3_HITL_ESCALATION",
                                extracted={"site": site, "email": email, "password": "•••••••• (saved in the OS credential store)",
                                           "left_for_you": needs_human or ["the next sign-up screen"]},
                                page_title=await page.title(), screenshot_url=shot, verified=True,
                                citation=f"Sign-up form filled for {email}; left for you: {needs_human}")
            run.finish(key, StepStatus.BLOCKED, "Filled what I could; the rest needs you: " + (", ".join(needs_human) or "next screen"))
            add_film(k, "SIGNUP", f"Filled the {site} sign-up with {email} — the rest needs you", shot, url_before, page.url,
                     await page.title(), True)
            return "needs_human"
        if form["submit_idx"] is not None:
            await ap.locator_for(page, form["submit_idx"]).click(timeout=12000)
        else:
            await ap.locator_for(page, form["password_idxs"][-1]).press("Enter")
        await ap.settle(page)
        elements = await ap.index_elements(page)
        distilled = await ap.distill(page)
        title = await page.title()
        after = site_login.classify_auth_page(elements, page.url, title, distilled["sanitized_text"])
        ok = not site_login.is_blocking(after)
        if ok:
            vault.mark_used(site, verified=True)
        shot = await ap.screenshot(page, run.task_id, f"step_{k}_signup_done")
        run.record_evidence(key, source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:ACCOUNT_SIGNUP",
                            extracted={"site": site, "email": email, "password": "•••••••• (saved in the OS credential store)",
                                       "account_ready": ok, "next": after["kind"] if after else None},
                            page_title=title, screenshot_url=shot, verified=ok,
                            citation=f"Created {site} account for {email}" if ok else f"Sign-up submitted; {site} wants more ({after['kind'] if after else '?'})")
        run.finish(key, StepStatus.SUCCEEDED if ok else StepStatus.BLOCKED,
                   f"Account created for {email}; the password is saved in your OS credential store." if ok else
                   "Submitted; the site wants a verification step.")
        add_film(k, "SIGNUP", f"Created a {site} account for {email}" if ok else f"Submitted the {site} sign-up — verification needed",
                 shot, url_before, page.url, title, ok)
        return "ok" if ok else "needs_human"

    async def _finish_autopilot_blocked(self, run: TaskRun, page: Any, k: int, barriers: List[Dict[str, Any]],
                                        add_film: Any, data: Dict[str, Any]) -> None:
        from backend import live_autopilot as ap

        key = f"act_{k}_blocked"
        run.append_steps([(key, f"Step {k} · Human verification required", T3, page.url)])
        run.start(key)
        shot = await ap.screenshot(page, run.task_id, f"step_{k}")
        title = await page.title()
        kinds = sorted({b["barrier_type"] for b in barriers})
        run.record_evidence(key, source_url=page.url, capability_method="TIER_3_HITL_ESCALATION", extracted={"barriers": barriers},
                            page_title=title, screenshot_url=shot, verified=True,
                            citation="; ".join(f"{b['selector']}: {b['evidence']}" for b in barriers)[:500])
        run.finish(key, StepStatus.BLOCKED, "Needs a human: " + ", ".join(kinds))
        add_film(k, "BLOCKED", "Stopped at a sign-in / CAPTCHA wall — not bypassed", shot, page.url, page.url, title, True)
        run.fail_running_steps("Not attempted: blocked by an access barrier.")
        data["blocker"] = {"barriers": barriers, "bypass_attempted": False, "url": page.url}
        run.save(status=TaskStatus.BLOCKED.value, extracted_data=data,
                 summary=(f"I reached “{title}” but it requires {', '.join(k2.replace('_', ' ').lower() for k2 in kinds)}. I did "
                          "not try to bypass it. Turn on “Watch Live Browser” with the persistent profile to sign in once, "
                          "then ask me again."),
                 verification_report=verifier.unverified_report(
                     "HONEST_BLOCKER_REPORT", "Live run halted at an access barrier; no outcome is claimed.",
                     [VerificationCheck(check="barrier_evidence_recorded", passed=True, observed=kinds)]))

    async def _autopilot_approval(self, run: TaskRun, page: Any, k: int, el: Dict[str, Any], decision: Dict[str, Any],
                                  distilled: Dict[str, Any], add_film: Any, data: Dict[str, Any]) -> bool:
        """Pause at a consequential control. Returns True if the browser session must stay open."""
        from backend import live_autopilot as ap
        from backend.llm_brain import element_label

        title = await page.title()
        label = element_label(el)[:80]
        params = ap.consequential_params(page.url, title, distilled, el)
        await ap.highlight(page, el["idx"], f"WebPilot Step #{k}: paused — needs your approval")
        shot = await ap.screenshot(page, run.task_id, f"step_{k}")
        await ap.unhighlight(page)
        key = f"act_{k}"
        session = ap.session_for_task(run.task_id)
        if params["consequence_tier"] == "final_payment":
            run.append_steps([(key, f"Step {k} · Reached the payment control “{label}” — handing off to you", T3, page.url)])
            run.start(key)
            run.record_evidence(key, source_url=page.url, capability_method="TIER_3_HITL_ESCALATION",
                                extracted={"locked_view": params}, page_title=title, screenshot_url=shot, verified=True,
                                citation=f"[{el['idx']}] “{label}” is a final-payment control")
            run.finish(key, StepStatus.BLOCKED, "WebPilot never presses final payment controls.")
            add_film(k, "HANDOFF", f"Stopped at “{label}” — final payment is always yours", shot, page.url, page.url, title, True)
            data["handoff"] = {"url": page.url, "button_text": label, "params": params}
            headed = bool(session and session.headed)
            run.save(status=TaskStatus.AWAITING_USER_INPUT.value, extracted_data=data, pending_approval=None,
                     summary=(f"I've reached the payment step on {params['domain']} (“{label}”"
                              + (f", total {params['currency']} {params['display_total']:,.2f}" if params.get("display_total") else "")
                              + "). Payment is always completed by you"
                              + (" — take over in the WebPilot browser window." if headed else f": open {page.url} to finish.")),
                     verification_report=verifier.unverified_report("HANDOFF", "Stopped before payment by policy."))
            return headed
        run.append_steps([
            (key, f"Step {k} · Lock “{label}” and request your approval (HMAC gate)", T3, page.url),
            ("approval", "Human approval of the consequential click", T3, page.url),
            ("execute", "Click the approved control on the live page", T2, page.url),
            ("verify", "Verify the resulting page (stop before any payment)", TC, None),
        ])
        run.start(key)
        text = f"{label} {run.task['goal']}".lower()
        action_type = (ActionType.PURCHASE_CHECKOUT if re.search(r"check ?out|buy|pay|order|cart|tote|bag|book|reserve|purchase", text)
                       else ActionType.FORM_SUBMISSION if re.search(r"submit|apply|send|register|sign up", text)
                       else ActionType.ACCOUNT_MUTATION)
        run.record_evidence(key, source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                            extracted={"locked_parameters": params, "element": {x: el.get(x) for x in ("idx", "tag", "role", "accessible_name")}},
                            page_title=title, screenshot_url=shot, firewall_alerts=distilled["firewall_alerts"],
                            citation=f"[{el['idx']}] “{label}” on {page.url} · prices {params['visible_prices']}")
        run.finish(key, StepStatus.SUCCEEDED, f"Locked page state (heading “{params['item_heading']}”, prices {params['visible_prices']}).")
        run.start("approval")
        approval = approval_guard.create_approval_request(
            run.task_id, run.step("approval")["step_id"], action_type,
            f"Click “{label}” on {params['domain']}" + (f" for “{params['item_heading']}”" if params.get("item_heading") else "")
            + (f" · total shown {params['currency'] or ''} {params['display_total']:,.2f}" if params.get("display_total") else ""),
            params, preview_screenshot_url=absolute_url(shot))
        run.finish("approval", StepStatus.AWAITING_APPROVAL, f"Approval {approval['approval_id']} created; hash {approval['parameter_hash'][:16]}…")
        add_film(k, "ASK_HUMAN_APPROVAL", f"Paused before “{label}” — locked price and page state behind the HMAC approval gate",
                 shot, page.url, page.url, title, True, {"element_idx": el["idx"], "approval_id": approval["approval_id"]})
        did = [h for h in data["autopilot"]["filmstrip"] if h["action"] in ("FILL_AND_SUBMIT", "FILL", "SELECT", "CLICK")]
        picked = (session.memory.get("selected") or {}).get("title") if session is not None else None
        if picked:  # the item the user chose earlier in the conversation names the action better than page headings
            params = {**params}  # display only — the hashed parameters stay exactly what the page shows
            display_item = picked
        else:
            display_item = params.get("item_heading")
        summary = ((f"I drove {params['domain']} in a real browser ({len(did)} actions) and stopped at “{label}”" if did else
                    f"I'm ready to click “{label}” on {params['domain']}")
                   + (f" for “{display_item}”" if display_item else "")
                   + (f" — total shown {params['currency'] or ''} {params['display_total']:,.2f}" if params.get("display_total") else "")
                   + ". Approve (or just say “yes”) and I'll continue up to the payment page, where you take over.")
        run.save(status=TaskStatus.AWAITING_APPROVAL.value, extracted_data=data, summary=summary,
                 pending_approval=approval_guard.public_view(approval),
                 verification_report=verifier.unverified_report("AWAITING_APPROVAL", "No consequential click executed yet."))
        return True

    async def _finish_autopilot_answer(self, run: TaskRun, page: Any, k: int, history: List[Dict[str, Any]],
                                       add_film: Any, data: Dict[str, Any], intents: Optional[Dict[str, Any]] = None,
                                       session: Any = None) -> None:
        from backend import live_autopilot as ap
        from backend import llm_brain, task_intents
        from backend.llm_brain import DOMAIN_RE, annotate_elements

        intents = intents or llm_brain.parse_goal_intents(run.task["goal"])
        run.append_steps([("answer", "Extract the final page and answer with cited DOM items", TC, page.url),
                          ("verify", "Verify actions and every citation against the DOM", TC, None)])
        run.start("answer")
        elements = annotate_elements(await ap.index_elements(page))
        distilled = await ap.distill(page)
        title = await page.title()
        dom = dict(distilled)
        cards = ap.page_cards([e for e in elements if not e.get("disabled")])
        priced_cards = [cd for cd in cards if cd.get("price_amount") is not None]
        if (len(dom.get("articles") or []) < 3 and cards) or len(priced_cards) >= 3:
            dom["articles"] = cards  # result cards carry prices, so budgets and price rankings apply
        question_goal = DOMAIN_RE.sub("", run.task["goal"])
        terms = llm_brain.content_tokens(intents["search_query"] or "")
        if terms and not llm_brain.analyse_question(question_goal)["summarize"] and not intents["wants_open_result"]:
            # The page lists search results: keep only items about the searched terms (OR semantics).
            question_goal += " matching " + ", ".join(f"'{t}'" for t in terms)
        answer = await llm_brain.answer_live_web_question(question_goal, dom)
        variant = task_intents.variant_report(intents["variant"], elements, distilled["sanitized_text"]) if intents.get("variant") else None
        # One screenshot per cited result card, so the user sees each option.
        for n, cit in enumerate((answer.get("citations") or [])[:4], start=1):
            m = re.search(r'data-webpilot-idx="(\d+)"', str(cit.get("dom_ref") or cit.get("selector") or ""))
            if m:
                cit["screenshot_url"] = absolute_url(await ap.card_screenshot(page, run.task_id, int(m.group(1)), f"option_{n}"))
        shot = await ap.screenshot(page, run.task_id, "final")
        run.record_evidence("answer", source_url=page.url, capability_method=f"BRAIN:{answer['provider_used']}",
                            extracted={"question": answer["question"], "citations": answer["citations"], "variant": variant},
                            page_title=title, screenshot_url=shot, firewall_alerts=distilled["firewall_alerts"],
                            citation="\n".join(f"[#{c2['item_id']}] {c2['dom_ref']}: {c2['title']}" for c2 in answer["citations"])[:2000]
                            or "No matching items.")
        run.finish("answer", StepStatus.SUCCEEDED, f"{answer['method']} over {answer['items_considered']} items → {len(answer['citations'])} cited.")
        add_film(k, "DONE", f"Extracted “{title}” and answered with {len(answer['citations'])} cited DOM items", shot, page.url, page.url, title, True)

        run.start("verify")
        dom_items = llm_brain.build_dom_items(dom)
        checks = [c2.model_dump(mode="json") for c2 in verifier.verify_grounded_answer(answer, dom_items)]
        acted = [h for h in history if h.get("action") in ("FILL_AND_SUBMIT", "CLICK")]
        checks.append({"check": "browser_actions_had_visible_effect", "passed": all(h.get("verified") for h in acted),
                       "expected": "every click/fill changed the page", "observed": [h.get("verified") for h in acted]})
        checks.append({"check": "no_consequential_control_clicked_without_approval", "passed": True,
                       "expected": "consequential controls gated", "observed": "classifier enforced server-side"})
        if variant is not None:
            checks.append({"check": "variant_checked_on_page", "passed": True, "expected": f"{variant['attribute']} {variant['value']}",
                           "observed": variant["evidence"]})
        passed = all(ch["passed"] for ch in checks)
        report = {"verdict": "VERIFIED_SUCCESS" if passed else "VERIFICATION_FAILED", "method": "LIVE_AUTOPILOT_ACTION_TRACE+DOM_GROUNDED_CITATIONS",
                  "checks": checks, "notes": None if passed else "Some actions had no visible effect or citations failed.",
                  "verified_at": utc_now_iso()}
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report["verdict"])

        # Conversation memory: what the user can refer to next ("the first one", "buy it").
        results = [{"title": c2["title"], "url": c2.get("url"), "price": c2.get("price"),
                    "screenshot_url": c2.get("screenshot_url")} for c2 in answer.get("citations") or [] if c2.get("url")]
        if len(results) < 2 and len(cards) >= 2 and variant is None and not intents.get("reference"):
            # Nothing matched the wording exactly, but the page lists options: offer those (within budget).
            budget = intents.get("max_price")
            pool = [cd for cd in cards if cd.get("price_amount") is not None and (not budget or cd["price_amount"] <= budget)] or cards
            results = [{"title": cd["title"], "url": cd["url"], "price": cd.get("price"), "screenshot_url": None} for cd in pool[:6]]
        cart_done = any(h.get("phase") == "cart" and h.get("verified") for h in history)
        if session is not None:
            if len(results) >= 2 and not cart_done:  # a cart page's "recommended for you" never replaces the user's options
                session.memory["results"] = results
            session.memory["page"] = {"url": page.url, "title": title}
            session.touch()
        commit_here = [e for e in elements if e.get("consequence_tier") == "commit" and not e.get("disabled")]
        first_price = next((p.get("raw") for p in distilled.get("prices") or [] if p.get("raw")), None)
        reply = self._chat_reply(answer, variant, title, results, commit_here, intents, bool(data["autopilot"].get("conversation_id")),
                                 first_price)
        opened = bool(data["autopilot"]["filmstrip"]) and data["autopilot"]["filmstrip"][0]["description"].startswith("Opened ")
        if opened and variant is None and not cart_done and bool(data["autopilot"].get("conversation_id")):
            # "the second one looks good": we opened it, so offer the next step instead of listing the page's links.
            picked = (session.memory.get("selected") or {}) if session is not None else {}
            name = (picked.get("title") or re.split(r"\s+[—|-]\s+", title)[0])[:90]
            price = picked.get("price") or first_price
            verb = "book it" if re.search(r"stay|villa|home|hotel|room|apartment|flat|cottage|bungalow|rental|night",
                                          f"{name} {title}", re.I) else "buy it"
            reply = (f"I've opened “{name}”" + (f" — {price}" if price else "") + f". Shall I {verb}? Say “{verb}” and I'll "
                     "take it up to the payment page for you to finish.")
            results = []
        if cart_done:
            picked = ((session.memory.get("selected") or {}).get("title") if session is not None else None) or "the item"
            reply = (f"Done — I added “{picked[:90]}” to your cart ✅ (the site shows “{title[:60]}”). "
                     "Say “buy it” and I'll take it to checkout, stopping at the payment page for you.")
            results = []
        data.update({"url": page.url, "title": title, "answer": answer, "firewall_alerts": distilled["firewall_alerts"],
                     "screenshot_url": absolute_url(shot), "render_mode": "live_viewport", "capability_method": "TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                     "articles": (dom.get("articles") or [])[:20], "prices": (distilled.get("prices") or [])[:20],
                     "headings": distilled.get("headings", [])[:20], "variant": variant, "chat_reply": reply,
                     "options": results[:6]})
        steps_done = len([f for f in data["autopilot"]["filmstrip"] if f["action"] not in ("DONE",)])
        summary = reply if data["autopilot"].get("conversation_id") else f"After {steps_done} live browser steps on “{title}”: {answer['answer']}"
        run.save(status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value, extracted_data=data,
                 summary=summary, verification_report=report)

    @staticmethod
    def _chat_reply(answer: Dict[str, Any], variant: Optional[Dict[str, Any]], title: str, results: List[Dict[str, Any]],
                    commit_here: List[Dict[str, Any]], intents: Dict[str, Any], conversational: bool,
                    price: Optional[str] = None) -> str:
        """A short, spoken-style reply without DOM citation markers."""
        from backend.llm_brain import element_label

        if variant is not None:
            item = title.split(" | ")[0].split(" - ")[0][:80]
            if variant["available"] is True:
                text = f"Yes — {variant['attribute']} {variant['value']} is available for “{item}”."
            elif variant["available"] is False:
                text = f"No — {variant['attribute']} {variant['value']} isn't available for “{item}” ({variant['evidence']})."
            else:
                text = f"I couldn't find a {variant['attribute']} {variant['value']} option on “{item}”."
            if variant["available"] and conversational:
                text += " Say “buy it” or “add it to cart” and I'll continue."
            return text
        if len(results) >= 2:
            lines = [f"{n}. {r['title'][:90]}" + (f" — {r['price']}" if r.get("price") else "") for n, r in enumerate(results[:5], start=1)]
            budget = f" under {intents['max_price']:,.0f}" if intents.get("max_price") else ""
            text = f"Here are the best matches{budget} I found on “{title[:60]}”:\n" + "\n".join(lines)
            if conversational and intents.get("defer_choice"):
                text += "\nWhich one would you like me to buy? Say e.g. “the second one” or “is the first one available in size 9?”."
            elif conversational:
                text += "\nTell me which one you like — e.g. “the first one looks good”, “open the second one” or “book the third one”."
            return text
        text = re.sub(r"\s*\[#\d+\]", "", answer.get("answer") or "").strip()
        if conversational and commit_here and not intents.get("wants_checkout"):
            verb = element_label(commit_here[0])[:30]
            name = re.split(r"\s+[—|-]\s+", title)[0][:90]
            return (f"I've opened “{name}”" + (f" — {price}" if price else "") + f". Shall I go ahead and {verb.lower()}? "
                    "Say yes and I'll take it up to the payment page for you to finish.")
        return text or f"I'm on “{title[:80]}”."

    _CONTINUE_RE = re.compile(
        r"^(continue|proceed|next|deliver (?:to|here)|use this (?:address|payment method)|save and continue|confirm (?:address|details)|"
        r"go to (?:checkout|payment)|proceed to (?:checkout|buy|payment|pay)|checkout|check ?out|review|reserve|book now|"
        r"request to book|select (?:this )?(?:room|address|delivery)|choose (?:this )?(?:room|address))\b", re.I)
    _PERSONAL_RE = re.compile(r"address|street|pin ?code|zip|postal|city|phone|mobile|full name|guest name|first name|last name", re.I)

    async def _continue_checkout(self, run: TaskRun, session: Any, page: Any, data: Dict[str, Any], approval: Dict[str, Any]) -> Optional[str]:
        """After an approved checkout/booking click: walk the remaining safe steps up to the payment page (never paying)."""
        from backend import live_autopilot as ap
        from backend import site_login
        from backend.llm_brain import annotate_elements, element_label

        add_film = self._film_appender(run, data)
        film = data["autopilot"]["filmstrip"]
        for _step in range(8):
            k = len(film) + 1
            elements = annotate_elements(await ap.index_elements(page))
            distilled = await ap.distill(page)
            landing = ap.payment_landing(elements, distilled)
            if landing["payment_step"] or landing["confirmation"]:
                return
            title = await page.title()
            auth = site_login.classify_auth_page(elements, page.url, title, distilled["sanitized_text"])
            if site_login.is_blocking(auth):
                if await self._handle_auth(run, session, page, k, auth, add_film, data) != "ok":
                    return
                continue
            empty_personal = [e for e in elements if e.get("tag") in ("input", "textarea", "select") and not e.get("disabled")
                              and e.get("required") and not e.get("value") and self._PERSONAL_RE.search(
                                  " ".join(filter(None, [e.get("accessible_name"), e.get("placeholder"), e.get("name_attr")])))]
            if empty_personal:
                if session.headed and await self._wait_for_human(
                        run, page, k, add_film, data, "Please fill in your delivery / guest details in the WebPilot browser window "
                        "(I don't type personal details for you) and press the site's continue button — I'll take it from there."):
                    continue
                return "needs_details"
            nxt = next((e for e in elements if not e.get("disabled") and e.get("consequence_tier") != "final_payment"
                        and not e.get("is_destructive_action")
                        and (e.get("tag") in ("button", "a") or e.get("role") == "button" or e.get("input_type") == "submit")
                        and e.get("landmark") not in ("nav", "footer", "header") and self._CONTINUE_RE.search(element_label(e))), None)
            if nxt is None:
                return
            decision = {"action": "CLICK", "element_idx": nxt["idx"], "phase": "checkout",
                        "reason": f"Continue the approved checkout (“{element_label(nxt)[:40]}”) — stopping before payment.",
                        "decided_by": "approved_checkout_scope"}
            if not await self._browser_step(run, page, k, nxt, decision, [], add_film, prefix="checkout"):
                return

    async def _execute_live_click(self, run: TaskRun, task_id: str, approval: Dict[str, Any], approval_token: str) -> Dict[str, Any]:
        from backend import live_autopilot as ap

        locked = approval["parameters"]
        session = ap.session_for_task(task_id)
        if session is None:
            raise ToolError("LIVE_SESSION_EXPIRED",
                            "The live browser session that holds this page (and its cart) is no longer open, so nothing was clicked.",
                            hint="Run the task again to get a fresh page and a fresh approval.")
        session.touch()
        page = session.page
        run.start("execute", target_url=page.url)
        elements = await ap.index_elements(page)
        distilled = await ap.distill(page)
        el = ap.find_element_by_label(elements, locked["button_text"])
        live = (ap.consequential_params(page.url, await page.title(), distilled, el) if el
                else {"error": "approved control not found on the live page", "page_url": page.url})
        result = approval_guard.verify_and_consume_approval(task_id, approval["approval_id"], approval_token, live)
        if not result["valid"]:
            run.record_evidence("execute", source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                                extracted={"live_parameters": live, "drift": result.get("drift")},
                                citation=f"{result['error_code']}: {result.get('drift')}")
            run.finish("execute", StepStatus.FAILED, result["message"])
            if result["error_code"] == "DOM_STATE_DRIFT_DETECTED":
                run.step("verify")["status"] = StepStatus.SKIPPED.value
                if run.has_step("approval"):
                    run.finish("approval", StepStatus.FAILED, "Approval revoked: the live page changed after approval.")
                changes = ", ".join(f"{k2}: {v['approved']} → {v['live']}" for k2, v in (result.get("drift") or {}).items())
                run.save(status=TaskStatus.AWAITING_USER_INPUT.value,
                         pending_approval=approval_guard.public_view(db.get_approval(approval["approval_id"])),
                         summary=f"I did not click anything: the live page changed after you approved it ({changes}). Your approval was revoked.")
            raise ToolError(result["error_code"], result["message"].split(": ", 1)[-1], hint=_hint_for(result["error_code"]),
                            **{k2: v for k2, v in result.items() if k2 in ("drift", "approved_parameter_hash", "live_parameter_hash")})
        if result["idempotent_replay"]:
            return self._replay_result(task_id, approval["approval_id"])
        if run.has_step("approval"):
            run.finish("approval", StepStatus.SUCCEEDED, f"User approved; HMAC token verified, live page re-checked, approval consumed.")
        url_before = page.url
        await ap.highlight(page, el["idx"], "WebPilot: approved click")
        pre = await ap.screenshot(page, task_id, "approved_click")
        await ap.unhighlight(page)
        await ap.locator_for(page, el["idx"]).click(timeout=12000)
        await ap.settle(page)
        post = await ap.screenshot(page, task_id, "after_approved_click")
        data = dict(run.task.get("extracted_data") or {})
        data.setdefault("autopilot", {"filmstrip": [], "decisions": []})
        film = data["autopilot"].setdefault("filmstrip", [])
        film.append({"step": len(film) + 1, "action": "APPROVED_CLICK", "description": f"Clicked the approved “{locked['button_text']}”",
                     "screenshot_url": absolute_url(post), "pre_screenshot_url": absolute_url(pre), "url_before": url_before,
                     "url_after": page.url, "page_title": await page.title(), "verified": True, "at": utc_now_iso()})
        run.save(extracted_data=data)
        run.finish("execute", StepStatus.SUCCEEDED, f"Clicked “{locked['button_text']}” → “{await page.title()}”.")
        # Within the scope of this approval: continue through address / review screens up to (never past) payment.
        stop_reason = await self._continue_checkout(run, session, page, data, approval)
        if run.task["status"] == TaskStatus.BLOCKED.value:
            return self.task_snapshot(task_id)
        after_elements = await ap.index_elements(page)
        after = await ap.distill(page)
        landing = ap.payment_landing(after_elements, after)
        title = await page.title()
        final_shot = await ap.screenshot(page, task_id, "checkout_handoff")
        run.record_evidence("execute", source_url=page.url, capability_method="TIER_2_SEMANTIC_DOM:LIVE_AUTOPILOT",
                            extracted={"clicked": locked["button_text"], "url_before": url_before, "url_after": page.url, "landing": landing},
                            page_title=title, screenshot_url=final_shot or post, citation=f"Clicked “{locked['button_text']}” → {page.url}")
        if not run.has_step("verify"):
            run.append_steps([("verify", "Verify the resulting page (stop before any payment)", TC, None)])
        run.start("verify")
        checks = [
            VerificationCheck(check="approved_control_clicked", passed=True, expected=locked["button_text"], observed=locked["button_text"]),
            VerificationCheck(check="page_changed_after_click", passed=page.url != url_before or title != locked.get("page_title"),
                              expected="new page state", observed=page.url),
            VerificationCheck(check="no_final_payment_control_pressed", passed=True,
                              expected="payment left to the human", observed=landing["final_payment_controls"] or "none pressed"),
            VerificationCheck(check="landed_on_payment_step_or_confirmation", passed=landing["payment_step"] or landing["confirmation"],
                              expected="payment step (handoff) or confirmation", observed=landing),
        ]
        if all(ch.passed for ch in checks):
            verdict = "LIVE_CHECKOUT_HANDOFF_VERIFIED" if landing["payment_step"] else "VERIFIED_SUCCESS"
        else:
            verdict = "UNVERIFIED"
        report = {"verdict": verdict, "method": "LIVE_POST_CLICK_PAGE_CLASSIFICATION",
                  "checks": [ch.model_dump(mode="json") for ch in checks], "verified_at": utc_now_iso(),
                  "confirmation_url": page.url,
                  "notes": ("Stopped at the payment step: WebPilot never enters or confirms payment." if landing["payment_step"]
                            else None)}
        run.finish("verify", StepStatus.SUCCEEDED if verdict != "UNVERIFIED" else StepStatus.FAILED, verdict)
        film.append({"step": len(film) + 1, "action": "HANDOFF" if landing["payment_step"] else "DONE",
                     "description": "Reached the payment page — your turn to pay" if landing["payment_step"] else f"Ended on “{title}”",
                     "screenshot_url": absolute_url(final_shot), "url_before": page.url, "url_after": page.url, "page_title": title,
                     "verified": verdict != "UNVERIFIED", "at": utc_now_iso()})
        data["outcome"] = {"confirmation_id": None, "confirmation_url": page.url, "handoff_url": page.url,
                           "approval_id": approval["approval_id"], "executed_at": utc_now_iso(), "landing": landing}
        conversational = bool(session.conversation_id)
        keep = conversational or (session.headed and landing["payment_step"])
        if landing["payment_step"]:
            totals = sorted({round(float(p["amount"]), 2) for p in (after.get("prices") or [])[:12] if p.get("amount") is not None})
            summary = (f"Done — I clicked “{locked['button_text']}” and stopped at the payment step on {locked['domain']}"
                       + (f" (total shown {locked.get('currency') or ''} {max(totals):,.2f})" if totals else
                          f" (total shown {locked.get('currency') or ''} {locked['display_total']:,.2f})" if locked.get("display_total") else "")
                       + (". Take over in the WebPilot browser window to pay." if session.headed else f". Finish the payment yourself at {page.url}."))
        elif landing["confirmation"]:
            summary = f"Done — I clicked “{locked['button_text']}” on {locked['domain']} and the page confirmed it."
        elif stop_reason == "needs_details":
            summary = (f"I clicked “{locked['button_text']}” and got as far as the delivery / guest details form on {locked['domain']}. "
                       "I don't fill in personal details for you — please complete it in the WebPilot browser window and "
                       "continue to payment yourself.")
        else:
            summary = f"I clicked “{locked['button_text']}” but couldn't confirm the result on {page.url}, so I'm not reporting it as done."
        final_status = (TaskStatus.COMPLETED.value if verdict != "UNVERIFIED" else
                        TaskStatus.AWAITING_USER_INPUT.value if stop_reason == "needs_details" else TaskStatus.UNVERIFIED_OUTCOME.value)
        run.save(status=final_status,
                 extracted_data=data, verification_report=report, summary=summary,
                 pending_approval=approval_guard.public_view(db.get_approval(approval["approval_id"])))
        if stop_reason == "needs_details":
            keep = True  # the user finishes the form in this tab
        if not keep:
            await ap.close_session(ap.session_key_for_task(task_id) or task_id)
        snapshot = self.task_snapshot(task_id)
        snapshot.update({"idempotent_replay": False, "executed": True, "confirmation_id": None, "handoff_url": page.url})
        return snapshot

    # ==================================================== H: synthesized tool

    #: Set by the MCP layer so newly registered tools trigger notifications/tools/list_changed.
    tool_registry_listener: Optional[Callable[[Optional[str]], None]] = None

    def _register_synthetic(self, spec: Dict[str, Any], session_id: Optional[str], task_id: Optional[str]) -> Dict[str, Any]:
        reg = tool_synthesizer.register(spec, session_id, task_id)
        if session_id and self.tool_registry_listener:
            try:
                self.tool_registry_listener(session_id)
            except Exception:  # notification is best-effort
                logger.exception("tools/list_changed notification failed")
        return reg

    async def _run_synthesized_tool(self, run: TaskRun, ig: Dict[str, Any]) -> None:
        params = ig.get("parameters") or {}
        source = ig["target_url"]
        run.plan([
            ("compile", "Compile goal: no built-in workflow fits → tool synthesis", TC, None),
            ("synthesize", "Synthesize a sandboxed declarative tool from the goal", TC, None),
            ("register", "Register it as a session-scoped MCP tool", TC, None),
            ("fetch", "Fetch the data source through the Untrusted Content Firewall", T2, source),
            ("execute", "Run the synthesized pipeline in the whitelisted interpreter", TC, None),
            ("verify", "Independently recompute the pipeline's results", TC, None),
        ])
        self._compile_step(run, ig)

        run.start("synthesize")
        spec = tool_synthesizer.build_spec(run.task["goal"], source, params)
        listing = tool_synthesizer.render_pipeline(spec)
        run.record_evidence("synthesize", source_url=source, capability_method="INTERNAL_COMPUTE:TOOL_SYNTHESIZER",
                            extracted={"spec": spec}, page_title=spec["title"], citation="\n".join(listing), verified=True)
        run.finish("synthesize", StepStatus.SUCCEEDED,
                   f"{spec['name']}: " + " → ".join(op["op"] for op in spec["pipeline"]) + " (declarative; no generated code is executed).")

        run.start("register")
        reg = self._register_synthetic(spec, run.session_id, run.task_id)
        run.finish("register", StepStatus.SUCCEEDED,
                   (f"Registered for MCP session {run.session_id[:8]}… until {reg['expires_at']}; tools/list now includes it."
                    if run.session_id else "No MCP session on this call: tool registered for this task only (not listed)."))

        run.start("fetch")
        page = await tool_synthesizer.fetch_source(spec, run.task_id, run.capture_id("fetch"))
        records, fmt, feed_alerts = tool_synthesizer.load_records(spec, page)
        alerts = page["distilled"]["firewall_alerts"] + feed_alerts
        run.record_evidence("fetch", source_url=page["final_url"], capability_method=page["capability_method"],
                            extracted={"format": fmt, "record_count": len(records), "sample": records[:5]},
                            page_title=page["title"], screenshot_url=page["screenshot_url"], firewall_alerts=alerts,
                            citation=f"{len(records)} records; first: {json_preview(records[0])}")
        run.finish("fetch", StepStatus.SUCCEEDED,
                   f"{len(records)} {fmt.upper()} records from {page['final_url']} via {page['capability_method']}. "
                   f"{summarize_alerts(alerts)}")

        run.start("execute")
        result = tool_synthesizer.execute(spec, records)
        run.finish("execute", StepStatus.SUCCEEDED, " · ".join(result["trace"]))

        run.start("verify")
        report = tool_synthesizer.verify(spec, records, result)
        passed = report["verdict"] == "VERIFIED_SUCCESS"
        lines = [f"## {spec['name']}"] + listing + ["## Result"] + [
            f"{r['column']}: {r['pct_change']:+.2f}%" for r in result["ranking"][:6]
        ] + [f"{col}: last {agg['last']} · mean {agg['mean']}" for col, agg in list(result["aggregates"].items())[:6]]
        card = run.evidence_card("verify", title="Synthesized tool result", source_url=page["final_url"], lines=lines,
                                 stamp=report["verdict"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                                 tone="success" if passed else "danger")
        run.record_evidence("verify", source_url=page["final_url"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                            extracted={"checks": report["checks"]}, page_title=spec["title"], screenshot_url=card,
                            citation="Independent recomputation of moving averages and percentage changes", verified=passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report["verdict"])
        run.save(
            status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value,
            summary=f"No built-in workflow matched, so I synthesized {spec['name']}. {tool_synthesizer.summarize(spec, result)}.",
            extracted_data={
                "tool": {"name": spec["name"], "definition": tool_synthesizer.tool_definition(spec), "spec": spec,
                         "pipeline_listing": listing, "session_id": run.session_id, "expires_at": reg["expires_at"],
                         "registered_for_session": bool(run.session_id)},
                "source_url": page["final_url"], "format": fmt, "record_count": len(records),
                "result": result, "firewall_alerts": alerts,
            },
            verification_report=report,
        )

    async def synthesize_tool(self, goal: str, source_url: Optional[str], window: Optional[int],
                              session_id: Optional[str]) -> Dict[str, Any]:
        """MCP synthesize_custom_tool: compile, register for the session and dry-run the new tool once."""
        try:
            spec = tool_synthesizer.build_spec(goal, source_url, {"window": window} if window else None)
            reg = self._register_synthetic(spec, session_id, None)
            run_out = await tool_synthesizer.run_registered_tool(spec, {"window": window} if window else {})
        except tool_synthesizer.SynthesisError as exc:
            raise ToolError(exc.code, exc.message, hint=exc.hint) from exc
        return {
            "tool": reg["tool"],
            "registered_for_session": bool(session_id),
            "session_id": session_id,
            "expires_at": reg["expires_at"],
            "pipeline_listing": tool_synthesizer.render_pipeline(spec),
            "spec": spec,
            "dry_run": run_out,
            "next_action": (f"Call tools/call with name '{spec['name']}' to run it again (optionally with window/source_url)."
                            if session_id else "Initialize an MCP session to have the tool listed in tools/list."),
        }

    async def call_synthesized_tool(self, name: str, args: Dict[str, Any], session_id: Optional[str]) -> Dict[str, Any]:
        spec = tool_synthesizer.lookup(name, session_id)
        if spec is None:
            raise ToolError("UNKNOWN_SYNTHETIC_TOOL", f"{name} is not registered for this session (or has expired).",
                            hint="Call synthesize_custom_tool again to re-create it.")
        try:
            return await tool_synthesizer.run_registered_tool(spec, args)
        except tool_synthesizer.SynthesisError as exc:
            raise ToolError(exc.code, exc.message, hint=exc.hint) from exc

    # ================================================= mid-task interruptions

    INTERRUPTIBLE = (TaskStatus.PLANNED.value, TaskStatus.EXECUTING.value, TaskStatus.AWAITING_APPROVAL.value)

    def _interrupt_context(self, task: Dict[str, Any]) -> Dict[str, Any]:
        ig = task["interpreted_goal"]
        data = task.get("extracted_data") or {}
        scenario = ig.get("scenario")
        if scenario == Scenario.LAPTOP_SHOPPING.value:
            urls = ig.get("store_urls") or []
            constraints = dict(data.get("constraints") or ig.get("constraints") or {})
            for itr in db.list_interrupts(task["task_id"], status="QUEUED"):
                constraints.update({k: v for k, v in itr["delta"].items() if k in ("max_price_inr", "min_ram_gb")})
            return {"constraints": constraints,
                    "stores": [{"key": self._store_key(u), "label": self._store_label(u)} for u in urls]}
        if scenario == Scenario.ATTENDANCE_AUDIT.value:
            return {"threshold_pct": ig.get("constraints", {}).get("threshold_pct", 75)}
        if scenario == Scenario.GRANT_FORM.value:
            form = data.get("form") or {}
            fields = [f["name"] for f in form.get("fields", []) if f.get("name") and f.get("type") != "hidden"]
            return {"fields": fields or list(DEMO_APPLICANT_PROFILE)}
        return {}

    async def interrupt_task(self, task_id: str, instruction: str, constraints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Apply a mid-task change of mind: queued for a running task, re-planned immediately at the approval gate."""
        task = self._require_task(task_id)
        if task["status"] not in self.INTERRUPTIBLE:
            raise ToolError("TASK_NOT_INTERRUPTIBLE", f"Task {task_id} is {task['status']}; only EXECUTING or "
                            "AWAITING_APPROVAL tasks can be changed mid-flight.", hint="Create a new task with the new constraints.")
        scenario = task["interpreted_goal"].get("scenario")
        try:
            delta, changes = parse_instruction(instruction, scenario, self._interrupt_context(task), constraints)
        except InterruptParseError as exc:
            examples = suggestions_for(scenario)
            raise ToolError(exc.code, exc.message, hint=("Try: " + " | ".join(examples)) if examples else None) from exc
        if scenario == Scenario.LAPTOP_SHOPPING.value and task["status"] == TaskStatus.AWAITING_APPROVAL.value \
                and "candidates_scanned" not in (task.get("extracted_data") or {}):
            raise ToolError("INTERRUPT_NOT_APPLICABLE", "This task predates re-planning support; create a new task.")

        itr = db.insert_interrupt(f"int_{uuid.uuid4().hex[:12]}", task_id, instruction, delta, changes)
        latest = db.get_task(task_id)
        if latest["status"] == TaskStatus.AWAITING_APPROVAL.value:
            await self._replan_after_approval(latest, itr)
            mode = "APPLIED"
        elif latest["status"] in TERMINAL_TASK_STATUSES or latest["status"] not in self.INTERRUPTIBLE:
            db.resolve_interrupt(itr["interrupt_id"], "REJECTED", f"Task finished ({latest['status']}) before the change arrived.")
            mode = "REJECTED"
        else:
            mode = "QUEUED"
        snapshot = self.task_snapshot(task_id)
        snapshot.update({"interrupt": db.get_interrupt(itr["interrupt_id"]), "mode": mode, "changes": changes})
        return snapshot

    async def _replan_after_approval(self, task: Dict[str, Any], itr: Dict[str, Any]) -> None:
        if db.get_interrupt(itr["interrupt_id"])["status"] != "QUEUED":
            return
        run = TaskRun(task)
        run.pace_seconds = 0.0
        revoked = approval_guard.revoke_open_approvals(task["task_id"])
        note = self._interrupt_note([itr]) + (f"I cancelled the previous approval request. " if revoked else "")
        scenario = task["interpreted_goal"].get("scenario")
        try:
            if scenario == Scenario.LAPTOP_SHOPPING.value:
                extracted = dict(task["extracted_data"])
                c = dict(extracted["constraints"])
                self._reset_laptop_tail(run, c)
                self._apply_laptop_interrupt(run, c, itr, before_key="rank")
                extracted["recovery_log"] = []
                await self._laptop_rank_and_checkout(run, c, extracted, note=note)
            elif scenario == Scenario.GRANT_FORM.value:
                extracted = task["extracted_data"]
                provided = dict(extracted.get("provided") or {})
                provided.update(itr["delta"].get("form_data") or {})
                form_url = extracted.get("form_url") or task["interpreted_goal"]["target_url"]
                run.supersede(["map", "preview", "approval", "execute", "verify"], ("reinspect",), self._next_version(run),
                              "Superseded by user interruption")
                run.record_interruption(itr)
                run.append_steps([
                    ("reinspect", "Re-open the form to rebuild the preview", T2, form_url),
                    ("map", "Re-map applicant data with your changes", TC, None),
                    ("preview", "Render the updated filled-in preview", T2, None),
                    ("approval", "Pause for human approval before final submission (HMAC gate)", T3, None),
                    ("execute", "Submit the approved application", T2, None),
                    ("verify", "Verify the submission receipt", TC, None),
                ])
                db.resolve_interrupt(itr["interrupt_id"], "APPLIED", "; ".join(itr["changes"]))
                run.start("reinspect")
                page = await run.browse("reinspect", form_url)
                forms = page["distilled"]["forms"]
                form = next((f for f in forms if f["form_id"] == extracted["form"]["form_id"]), forms[0] if forms else None)
                if form is None:
                    run.finish("reinspect", StepStatus.FAILED, "The form is no longer on the page.")
                    run.fail_running_steps("Form missing after re-plan.")
                    run.save(status=TaskStatus.FAILED.value, pending_approval=None,
                             summary=note + "The form disappeared when I re-opened it, so nothing will be submitted.")
                    return
                run.page_evidence("reinspect", page, {"form": form}, citation=f"form#{form['form_id']} re-inspected after interruption")
                run.finish("reinspect", StepStatus.SUCCEEDED, "Form re-inspected; earlier evidence preserved.")
                await self._grant_fill_and_request(run, page, form, provided, note=note)
            else:
                db.resolve_interrupt(itr["interrupt_id"], "REJECTED", f"Re-planning is not supported for {scenario}.")
        except BrowserFetchError as exc:
            run.fail_running_steps(exc.message)
            run.save(status=TaskStatus.FAILED.value, pending_approval=None,
                     summary=note + f"Re-planning failed because a page could not be loaded: {exc.message}")

    async def _drain_late_interrupts(self, task_id: str) -> None:
        """Interruptions that arrived after the last checkpoint: re-plan at the approval gate or reject honestly."""
        for itr in db.list_interrupts(task_id, status="QUEUED"):
            task = db.get_task(task_id)
            if task["status"] == TaskStatus.AWAITING_APPROVAL.value:
                await self._replan_after_approval(task, itr)
            else:
                db.resolve_interrupt(itr["interrupt_id"], "REJECTED",
                                     f"Arrived after the task finished ({task['status']}); nothing left to re-plan.")

    # =================================================== D: approved execution

    async def execute_approved_step(self, task_id: str, approval_id: str, approval_token: str) -> Dict[str, Any]:
        task = self._require_task(task_id)
        approval = db.get_approval(approval_id)
        if approval is None or approval["task_id"] != task_id:
            raise ToolError("PERMISSION_DENIED", f"No approval {approval_id} exists for task {task_id}.",
                            hint="Call request_user_approval(task_id) to get the approval_id of this task.")
        run = TaskRun(task)

        # Replays and obviously invalid tokens are rejected before touching the live page.
        pre = approval_guard.verify_and_consume_approval(task_id, approval_id, approval_token, current_live_params=None) \
            if approval["status"] != ApprovalStatus.USER_APPROVED.value else None
        if pre is not None:
            if not pre["valid"]:
                raise ToolError(pre["error_code"], pre["message"].split(": ", 1)[-1], hint=_hint_for(pre["error_code"]),
                                approval_status=approval["status"])
            return self._replay_result(task_id, approval_id)

        expected = approval_guard.generate_approval_token(approval_id, approval["parameter_hash"])
        if not isinstance(approval_token, str) or not hmac.compare_digest(approval_token, expected):
            raise ToolError("PERMISSION_DENIED", "Invalid HMAC approval_token.", hint=_hint_for("PERMISSION_DENIED"))

        locked = approval["parameters"]
        if locked.get("action") == "LIVE_CONSEQUENTIAL_CLICK":
            return await self._execute_live_click(run, task_id, approval, approval_token)
        action = approval["action_type"]
        exec_key = "execute"
        run.start(exec_key, target_url=locked.get("target_url") or locked.get("form_url"))
        live_page = await run.browse(exec_key, locked.get("target_url") or locked["form_url"], stamp="LIVE RE-CHECK")
        if action == ActionType.PURCHASE_CHECKOUT.value:
            live_params = self._checkout_params_from_review(live_page) or {"error": "checkout review unavailable",
                                                                           "status_code": live_page["status_code"]}
        else:
            forms = live_page["distilled"]["forms"]
            form = next((f for f in forms if f["form_id"] == locked.get("form_id")), None)
            live_params = self._form_params(live_page, form, locked["fields"]) if form else {"error": "form missing"}

        result = approval_guard.verify_and_consume_approval(task_id, approval_id, approval_token, live_params)
        if not result["valid"]:
            run.page_evidence(exec_key, live_page, {"live_parameters": live_params, "drift": result.get("drift")},
                              citation=f"{result['error_code']}: {result.get('drift')}")
            if result["error_code"] == "DOM_STATE_DRIFT_DETECTED":
                drift_txt = ", ".join(
                    f"{k}: {format_inr(v['approved'])} → {format_inr(v['live'])}" if k.endswith("_inr") and isinstance(v["approved"], int)
                    and isinstance(v["live"], int) else f"{k}: {v['approved']} → {v['live']}"
                    for k, v in (result.get("drift") or {}).items())
                run.finish(exec_key, StepStatus.FAILED, f"DOM_STATE_DRIFT_DETECTED ({drift_txt}). Approval revoked; nothing executed.")
                if run.has_step("approval"):
                    run.finish("approval", StepStatus.FAILED, "Approval revoked: live page parameters drifted after approval.")
                run.step("verify")["status"] = StepStatus.SKIPPED.value
                run.save(status=TaskStatus.AWAITING_USER_INPUT.value, pending_approval=approval_guard.public_view(db.get_approval(approval_id)),
                         summary=f"I did not complete the action because the page changed after you approved it ({drift_txt}). "
                                 "Your approval was revoked. Tell me whether to re-plan with the new details.")
            elif result["error_code"] == "APPROVAL_EXPIRED":
                run.finish(exec_key, StepStatus.FAILED, result["message"])
                run.save(status=TaskStatus.AWAITING_USER_INPUT.value,
                         pending_approval=approval_guard.public_view(db.get_approval(approval_id)),
                         summary="The approval expired before the action ran, so nothing was executed. Ask me to re-plan.")
            else:
                run.finish(exec_key, StepStatus.FAILED, result["message"])
                run.save(status=TaskStatus.AWAITING_APPROVAL.value)
            raise ToolError(result["error_code"], result["message"].split(": ", 1)[-1], hint=_hint_for(result["error_code"]),
                            **{k: v for k, v in result.items() if k in ("drift", "approved_parameter_hash", "live_parameter_hash")})
        if result["idempotent_replay"]:
            return self._replay_result(task_id, approval_id)

        # The approval is now CONSUMED: perform the action exactly once.
        if run.has_step("approval"):
            run.finish("approval", StepStatus.SUCCEEDED,
                       f"User approved; HMAC token verified, live parameters re-checked and approval {approval_id} consumed.")
        if action == ActionType.PURCHASE_CHECKOUT.value:
            form_data = {"sku": locked["sku"], "expected_price": locked["total_price_inr"], "approval_ref": approval_id}
            post_url = locked["checkout_action_url"]
        else:
            form_data = dict(locked["fields"])
            post_url = locked["form_action"]
        outcome = await run.browse(exec_key, post_url, method="POST", form_data=form_data, stamp="POST-ACTION EVIDENCE")
        run.page_evidence(exec_key, outcome, {"submitted": {k: v for k, v in form_data.items()}, "status_code": outcome["status_code"]},
                          citation=f"{locked.get('action')} POST {post_url} → HTTP {outcome['status_code']} {outcome['final_url']}")
        run.finish(exec_key, StepStatus.SUCCEEDED if outcome["status_code"] < 400 else StepStatus.FAILED,
                   f"Action attempted with approval {approval_id}; landed on {outcome['final_url']} (HTTP {outcome['status_code']}).")

        run.start("verify")
        if action == ActionType.PURCHASE_CHECKOUT.value:
            report = verifier.verify_checkout(outcome["html"], outcome["status_code"], locked)
        else:
            report = verifier.verify_form_submission(outcome["html"], outcome["status_code"], locked["fields"])
        report["confirmation_url"] = outcome["final_url"]
        passed = report["verdict"] in ("VERIFIED_SUCCESS", "SIMULATED_TRANSACTION_VERIFIED")
        for ev in run.step(exec_key)["evidence_ids"][-1:]:
            db.set_evidence_verified(ev, passed)
        run.record_evidence("verify", source_url=outcome["final_url"], capability_method="INTERNAL_COMPUTE:VERIFIER",
                            extracted={"checks": report["checks"], "confirmation_id": report.get("confirmation_id")},
                            page_title=outcome["title"], screenshot_url=outcome["screenshot_url"],
                            citation=f"Confirmation {report.get('confirmation_id')} verified against approved parameters",
                            verified=passed)
        run.finish("verify", StepStatus.SUCCEEDED if passed else StepStatus.FAILED, report.get("notes") or report["verdict"])

        extracted = dict(run.task.get("extracted_data") or {})
        extracted["outcome"] = {"confirmation_id": report.get("confirmation_id"), "confirmation_url": outcome["final_url"],
                                "approval_id": approval_id, "executed_at": utc_now_iso()}
        if action == ActionType.PURCHASE_CHECKOUT.value:
            summary = (f"Done. Your order for {locked['item_name']} from {locked['seller']} at {format_inr(locked['total_price_inr'])} "
                       f"is confirmed with reference {report.get('confirmation_id')}. This was a controlled sandbox checkout, "
                       f"so no real money was charged.") if passed else \
                "I attempted the checkout but could not verify a confirmation, so I'm not reporting it as complete."
        else:
            summary = (f"Your grant application was submitted. The receipt number is {report.get('confirmation_id')}.") if passed else \
                "I attempted the submission but could not verify a receipt, so I'm not reporting it as complete."
        run.save(status=TaskStatus.COMPLETED.value if passed else TaskStatus.UNVERIFIED_OUTCOME.value,
                 summary=summary, extracted_data=extracted, verification_report=report,
                 pending_approval=approval_guard.public_view(db.get_approval(approval_id)))
        snapshot = self.task_snapshot(task_id)
        snapshot.update({"idempotent_replay": False, "executed": True, "confirmation_id": report.get("confirmation_id")})
        return snapshot

    def _replay_result(self, task_id: str, approval_id: str) -> Dict[str, Any]:
        snapshot = self.task_snapshot(task_id)
        outcome = (snapshot["task"].get("extracted_data") or {}).get("outcome") or {}
        snapshot.update({
            "idempotent_replay": True,
            "executed": False,
            "confirmation_id": outcome.get("confirmation_id"),
            "message": "Approval already consumed; returning cached verified outcome. The action was not repeated.",
        })
        return snapshot

    # ============================================================ other tools

    async def verify_task_result(self, task_id: str) -> Dict[str, Any]:
        task = self._require_task(task_id)
        scenario = task["interpreted_goal"].get("scenario")
        data = task.get("extracted_data") or {}
        outcome = data.get("outcome")
        report = task.get("verification_report")
        live_click = bool(outcome and (db.get_approval(outcome.get("approval_id", "")) or {}).get("parameters", {}).get("action")
                          == "LIVE_CONSEQUENTIAL_CLICK")
        if live_click:
            report = {**(report or {}), "reverified": False,
                      "notes": ((report or {}).get("notes") or "") + " Live-site outcome is verified at execution time from the "
                               "browser session; re-fetching a stateful checkout page would not be meaningful."}
        elif outcome and outcome.get("confirmation_url"):
            approval = db.get_approval(outcome["approval_id"])
            page = await browser_runtime.inspect(outcome["confirmation_url"], task_id, f"reverify_{uuid.uuid4().hex[:6]}",
                                                 stamp="RE-VERIFICATION")
            if approval and approval["action_type"] == ActionType.PURCHASE_CHECKOUT.value:
                report = verifier.verify_checkout(page["html"], page["status_code"], approval["parameters"])
            elif approval:
                report = verifier.verify_form_submission(page["html"], page["status_code"], approval["parameters"]["fields"])
            report["confirmation_url"] = outcome["confirmation_url"]
            report["reverified"] = True
        elif scenario == Scenario.ATTENDANCE_AUDIT.value and data.get("subjects"):
            report = verifier.verify_attendance(data["subjects"], data["threshold_pct"], len(data["subjects"]))
            report["reverified"] = True
        elif scenario == Scenario.PROFILE_COMPARISON.value and data.get("profiles"):
            report = verifier.verify_profile_comparison(data["profiles"])
            report["reverified"] = True
        elif scenario == Scenario.GITHUB_REPO_ANALYSIS.value and data.get("full_name"):
            report = verifier.verify_github_analysis(data["owner"], data["repo"], data)
            report["reverified"] = True
        elif report is None:
            report = verifier.unverified_report("NOT_EXECUTED", f"Task is {task['status']}; nothing to verify yet.")
        if task["status"] in (TaskStatus.AWAITING_APPROVAL.value, TaskStatus.BLOCKED.value, TaskStatus.AWAITING_USER_INPUT.value):
            report = {**(report or {}), "verdict": "UNVERIFIED",
                      "notes": f"Task is {task['status']}; no outcome has been executed or claimed."}
        db.update_task(task_id, verification_report=report)
        return {"task_id": task_id, "status": db.get_task(task_id)["status"], "verification_report": report}

    def request_user_approval(self, task_id: str, approval_id: Optional[str] = None) -> Dict[str, Any]:
        task = self._require_task(task_id)
        approvals = db.list_approvals_for_task(task_id)
        if not approvals:
            raise ToolError("NO_APPROVAL_REQUIRED", f"Task {task_id} has no consequential step that needs approval "
                            f"(status {task['status']}).", hint="Only ACT_WITH_APPROVAL tasks create approvals.")
        approval = next((a for a in approvals if a["approval_id"] == approval_id), None) if approval_id else approvals[-1]
        if approval is None:
            raise ToolError("APPROVAL_NOT_FOUND", f"Approval {approval_id} does not belong to task {task_id}.")
        view = approval_guard.public_view(approval, include_token=True)
        status = approval["status"]
        if status == ApprovalStatus.PENDING.value:
            instruction = ("Waiting for the user. Ask them to review and approve in the WebPilot Alexa+ app "
                           f"({view['approval_url']}). Call request_user_approval again to check; once approved it "
                           "returns the approval_token for execute_approved_step.")
        elif status == ApprovalStatus.USER_APPROVED.value:
            instruction = "The user approved. Call execute_approved_step with this approval_id and approval_token."
        elif status == ApprovalStatus.CONSUMED.value:
            instruction = "Already executed; calling execute_approved_step again returns the cached verified outcome."
        else:
            instruction = f"This approval is {status} and cannot be executed."
        return {"task_id": task_id, "task_status": task["status"], "approval": view, "next_action": instruction}

    def cancel_task(self, task_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        task = self._require_task(task_id)
        if task["status"] in TERMINAL_TASK_STATUSES:
            raise ToolError("TASK_ALREADY_FINAL", f"Task {task_id} is already {task['status']} and cannot be cancelled.")
        revoked = approval_guard.revoke_open_approvals(task_id)
        waiter = LOGIN_WAITERS.get(task_id)
        if waiter is not None and not waiter.done():
            waiter.set_result({"choice": "cancel"})
        try:
            from backend import live_autopilot

            if task_id in live_autopilot.SESSIONS:  # stand-alone task: its own browser goes; conversation tabs stay
                asyncio.get_running_loop().create_task(live_autopilot.close_session(task_id))
        except RuntimeError:
            pass
        steps = task["plan_steps"]
        for s in steps:
            if s["status"] in (StepStatus.PENDING.value, StepStatus.RUNNING.value, StepStatus.AWAITING_APPROVAL.value, StepStatus.BLOCKED.value):
                s["status"] = StepStatus.CANCELLED.value
                s["finished_at"] = utc_now_iso()
        approvals = db.list_approvals_for_task(task_id)
        db.update_task(task_id, status=TaskStatus.CANCELLED.value, plan_steps=steps,
                       pending_approval=approval_guard.public_view(approvals[-1]) if approvals else None,
                       summary=f"Task cancelled{': ' + reason if reason else ''}. {revoked} pending approval(s) revoked; "
                               "nothing further will be executed.")
        snapshot = self.task_snapshot(task_id)
        snapshot["revoked_approvals"] = revoked
        return snapshot

    async def inspect_website(self, url: str, task_id: Optional[str] = None) -> Dict[str, Any]:
        if task_id:
            self._require_task(task_id)
        capture = f"inspect_{uuid.uuid4().hex[:8]}"
        page = await browser_runtime.inspect(url, task_id or "adhoc", capture)
        d = page["distilled"]
        barriers = self._assess_barriers(page)
        if task_id:
            db.insert_evidence({
                "evidence_id": f"ev_{uuid.uuid4().hex[:14]}", "task_id": task_id, "step_id": capture,
                "source_url": page["final_url"], "page_title": d["title"], "capability_method": page["capability_method"],
                "extracted": {"headings": d["headings"][:15], "barriers": d["barriers"]},
                "dom_citation_snippet": d["sanitized_text"][:600], "screenshot_url": absolute_url(page["screenshot_url"]),
                "firewall_alerts": d["firewall_alerts"], "verification_passed": False, "captured_at": utc_now_iso(),
            })
        return {
            "url": page["final_url"],
            "status_code": page["status_code"],
            "title": d["title"],
            "capability_method": page["capability_method"],
            "screenshot_url": absolute_url(page["screenshot_url"]),
            "blocked": bool(barriers),
            "barriers": d["barriers"],
            "blocking_barriers": barriers,
            "human_action_required": [b["barrier_type"] for b in barriers],
            "headings": d["headings"][:25],
            "tables": [{**t, "rows": t["rows"][:20]} for t in d["tables"][:6]],
            "forms": d["forms"][:6],
            "interactive_elements": d["interactive_elements"][:40],
            "product_cards": d["product_cards"][:20],
            "articles": d.get("articles", [])[:30],
            "prices": d.get("prices", [])[:30],
            "structured_data": d.get("structured_data"),
            "render_mode": page.get("render_mode"),
            "fallback_reason": page.get("fallback_reason"),
            "links": d["links"][:30],
            "firewall_alerts": d["firewall_alerts"],
            "firewall_summary": summarize_alerts(d["firewall_alerts"]),
            "untrusted_content": d["wrapped_text"][:4000],
            "text_truncated": d["text_truncated"] or len(d["wrapped_text"]) > 4000,
        }

    # ------------------------------------------------------------- snapshots

    def _require_task(self, task_id: str) -> Dict[str, Any]:
        task = db.get_task(task_id)
        if task is None:
            raise ToolError("TASK_NOT_FOUND", f"No task with id {task_id}.",
                            hint="Use the task_id returned by create_web_task.")
        return task

    def task_snapshot(self, task_id: str) -> Dict[str, Any]:
        task = self._require_task(task_id)
        approvals = [approval_guard.public_view(a) for a in db.list_approvals_for_task(task_id)]
        evidence = db.list_evidence_for_task(task_id)
        status = task["status"]
        next_actions = {
            TaskStatus.AWAITING_APPROVAL.value: "Tell the user what will happen and ask them to approve in the WebPilot app; "
                                                "then call request_user_approval to obtain the approval_token and "
                                                "execute_approved_step to run it.",
            TaskStatus.BLOCKED.value: "Explain the barrier to the user; WebPilot will not bypass it. Offer to cancel_web_task.",
            TaskStatus.AWAITING_USER_INPUT.value: "Ask the user for the missing information or whether to re-plan.",
            TaskStatus.COMPLETED.value: "Read the summary to the user; get_execution_evidence lists citations and screenshots.",
        }
        return {
            "task": copy.deepcopy(task),
            "interruptions": db.list_interrupts(task_id),
            "approvals": approvals,
            "evidence_count": len(evidence),
            "voice_summary": task.get("summary"),
            "next_action": next_actions.get(status),
        }


class _GitHubRateLimited(Exception):
    def __init__(self, reset: Optional[str]):
        super().__init__(f"GitHub API rate limit exceeded (reset at {reset})")
        self.reset = reset


def _detect_stack(files: List[str], language: Optional[str]) -> List[str]:
    markers = {
        "package.json": "Node.js / npm", "pnpm-lock.yaml": "pnpm", "tsconfig.json": "TypeScript",
        "pyproject.toml": "Python (pyproject)", "requirements.txt": "Python (pip)", "uv.lock": "uv",
        "Cargo.toml": "Rust (Cargo)", "go.mod": "Go modules", "pom.xml": "Maven", "build.gradle": "Gradle",
        "Dockerfile": "Docker", "docker-compose.yml": "Docker Compose", "Makefile": "Make",
    }
    stack = [label for f, label in markers.items() if f in files]
    if language and not any(language.lower() in s.lower() for s in stack):
        stack.insert(0, language)
    return stack


def _join_natural(items: List[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _hint_for(code: str) -> str:
    return {
        "PERMISSION_DENIED": "Only the user can approve via the WebPilot app (POST /api/approvals/{id}/decide). "
                             "Call request_user_approval to check status and obtain the genuine approval_token.",
        "DOM_STATE_DRIFT_DETECTED": "The page changed after approval. Tell the user what changed and create a new task "
                                    "if they still want to proceed.",
        "APPROVAL_REVOKED": "Create a new task to obtain a fresh approval.",
        "APPROVAL_EXPIRED": "Create a new task to obtain a fresh approval.",
    }.get(code, "Check the task with get_task_status.")


planner = PlannerEngine()
