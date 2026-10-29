"""Natural-language mid-task interruption parser.

Turns utterances such as "Wait, change the budget from 80000 to 70000", "Skip ElectroHub and only check
ByteBazaar", "make it 32GB RAM", "use 80 percent instead" or "change the project title to Voice Copilot"
into a typed constraint delta that the planner can apply to a running or paused task.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from backend.sandbox_routes import format_inr

_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?\s*|inr\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|l)?\b(?!\s*(?:gb|%|percent|per cent))",
    re.I,
)
_BUDGET_WORDS = re.compile(r"budget|price|under|below|max|cost|spend|afford|cheaper|₹|rupees|\brs\b|\binr\b|lakh|\d\s*k\b", re.I)
_RAM_RE = re.compile(r"(\d{1,3})\s*gb", re.I)
_THRESHOLD_RE = re.compile(r"(\d{2}(?:\.\d+)?)\s*(?:%|percent|per cent)", re.I)
_EXCLUDE_WORDS = re.compile(r"\b(skip|exclude|ignore|remove|drop|without|don'?t|do not|not|avoid|except|forget)\b", re.I)
_ONLY_WORDS = re.compile(r"\b(only|just|exclusively|stick to)\b", re.I)

FIELD_ALIASES: List[Tuple[str, str]] = [
    (r"applicant(?:'s)? name|my name|name", "applicant_name"),
    (r"project title|project name|title", "project_title"),
    (r"track(?: name)?", "track_name"),
    (r"repo(?:sitory)?(?: url| link)?|github (?:url|link)", "repo_url"),
    (r"abstract(?: summary)?|summary|description", "abstract_summary"),
]


class InterruptParseError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _amount(number: str, unit: Optional[str]) -> int:
    value = float(number.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("k", "thousand"):
        value *= 1000
    elif unit in ("lakh", "lakhs", "l"):
        value *= 100000
    return int(round(value))


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _store_intents(text: str, stores: List[Dict[str, str]]) -> List[Tuple[str, str]]:
    """Attribute each store mention to the nearest preceding cue in the same sentence.

    "skip A and B" -> both excluded; "skip A and only check B" -> A excluded, B only; "check A" -> no-op.
    """
    lower = text.lower()
    cues = sorted([(m.start(), "exclude") for m in _EXCLUDE_WORDS.finditer(lower)]
                  + [(m.start(), "only") for m in _ONLY_WORDS.finditer(lower)])
    boundaries = [m.start() for m in re.finditer(r"[.;!?]", lower)]
    intents: List[Tuple[str, str]] = []
    for s in stores:
        variants = {_compact(s["key"]), _compact(s["label"].split(" ")[0])}
        for variant in filter(None, variants):
            pattern = r"\s*".join(map(re.escape, variant))
            for m in re.finditer(pattern, lower):
                sentence_start = max([b for b in boundaries if b < m.start()] or [-1])
                prior = [c for c in cues if sentence_start < c[0] < m.start()]
                if prior:
                    intents.append((s["key"], prior[-1][1]))
    return intents


def parse_instruction(
    text: str,
    scenario: str,
    context: Dict[str, Any],
    structured: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return (delta, human-readable changes). Raises InterruptParseError if nothing applicable was found."""
    structured = dict(structured or {})
    delta: Dict[str, Any] = {}
    changes: List[str] = []
    lower = text.lower()

    if scenario == "laptop_shopping":
        current = context.get("constraints", {})
        stores: List[Dict[str, str]] = context.get("stores", [])
        # Budget
        new_price = structured.get("max_price_inr")
        if new_price is None and _BUDGET_WORDS.search(text):
            span = re.search(r"from\s+(.+?)\s+to\s+(.+)", text, re.I)
            source = span.group(2) if span else text
            amounts = [_amount(n, u) for n, u in _AMOUNT_RE.findall(source)]
            amounts = [a for a in amounts if a >= 1000]
            if amounts:
                new_price = amounts[0] if span else amounts[-1]
        if new_price is not None:
            new_price = int(new_price)
            if not 1000 <= new_price <= 10_000_000:
                raise InterruptParseError("INVALID_CONSTRAINT", "Budget must be between ₹1,000 and ₹1,00,00,000.")
            if new_price != current.get("max_price_inr"):
                delta["max_price_inr"] = new_price
                changes.append(f"Budget {format_inr(current.get('max_price_inr', 0))} → {format_inr(new_price)}")
        # RAM
        new_ram = structured.get("min_ram_gb")
        if new_ram is None:
            m = _RAM_RE.search(text)
            new_ram = int(m.group(1)) if m else None
        if new_ram is not None:
            new_ram = int(new_ram)
            if not 1 <= new_ram <= 512:
                raise InterruptParseError("INVALID_CONSTRAINT", "RAM must be between 1 and 512 GB.")
            if new_ram != current.get("min_ram_gb"):
                delta["min_ram_gb"] = new_ram
                changes.append(f"RAM ≥{current.get('min_ram_gb')}GB → ≥{new_ram}GB")
        # Stores
        exclude = {s.lower() for s in structured.get("exclude_stores", [])}
        only = {s.lower() for s in structured.get("include_only_stores", [])}
        for store_key, kind in _store_intents(text, stores):
            (only if kind == "only" else exclude).add(store_key)
        known = {s["key"] for s in stores}
        labels = {s["key"]: s["label"] for s in stores}
        exclude = {s for s in exclude if s in known}
        only = {s for s in only if s in known}
        if only:
            exclude |= known - only
        exclude -= set(current.get("excluded_stores", []))
        if exclude:
            if exclude >= known:
                raise InterruptParseError("INVALID_CONSTRAINT", "That would exclude every store; at least one store must remain.")
            delta["exclude_stores"] = sorted(exclude)
            changes.append("Skip " + ", ".join(labels[s] for s in sorted(exclude))
                           + (f" (only {', '.join(labels[s] for s in sorted(only))})" if only else ""))

    elif scenario == "attendance_audit":
        new_t = structured.get("threshold_pct")
        if new_t is None:
            m = _THRESHOLD_RE.search(text)
            new_t = float(m.group(1)) if m else None
        if new_t is not None:
            new_t = float(new_t)
            if not 1 <= new_t <= 100:
                raise InterruptParseError("INVALID_CONSTRAINT", "Threshold must be between 1 and 100 percent.")
            delta["threshold_pct"] = new_t
            changes.append(f"Attendance threshold {context.get('threshold_pct', 75):g}% → {new_t:g}%")

    elif scenario == "grant_form":
        form_data: Dict[str, str] = {k: str(v) for k, v in (structured.get("form_data") or {}).items()}
        for alias, field in FIELD_ALIASES:
            m = re.search(rf"\b(?:change|set|update|use|make|rename)\s+(?:the\s+|my\s+)?(?:{alias})\s+(?:to|as|=|into)\s+(.+)$",
                          text, re.I)
            if m and field not in form_data:
                value = m.group(1).strip().strip("\"'“”‘’").rstrip(".")
                form_data[field] = value
                break
        allowed = set(context.get("fields", []))
        form_data = {k: v for k, v in form_data.items() if not allowed or k in allowed}
        if form_data:
            delta["form_data"] = form_data
            changes += [f"{k} → “{v[:60]}”" for k, v in form_data.items()]
    else:
        raise InterruptParseError("INTERRUPT_NOT_APPLICABLE", f"Mid-task changes are not supported for scenario {scenario!r}.")

    if not delta:
        raise InterruptParseError(
            "INTERRUPT_NOT_UNDERSTOOD",
            f"I couldn't find a change I can apply to this {scenario.replace('_', ' ')} task in: {text!r}",
        )
    return delta, changes


def suggestions_for(scenario: str) -> List[str]:
    return {
        "laptop_shopping": ["Change the budget from 80000 to 70000", "Skip ElectroHub and only check ByteBazaar",
                            "Actually I need 32GB RAM and my budget is 1.5 lakh"],
        "attendance_audit": ["Use 80 percent as the threshold instead"],
        "grant_form": ["Change the project title to WebPilot Voice Copilot", "Set the track to Generative AI Track"],
    }.get(scenario, [])
