"""Generic, site-agnostic task intents for the live autopilot.

Everything here works from the user's words and from the generic DOM index (labels, roles, input types,
container text) — never from a site name or a site-specific selector:

* follow-up references to earlier results ("the first one", "option 2", "this one", "buy it")
* product variants ("is it available in size 10", "select colour black")
* budgets ("under 15k", "below ₹2,000", "3000 budget")
* booking details (check-in / check-out dates, adults / children / rooms) and the widgets that take them
  (date inputs, calendar day cells, guest steppers and selects)
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional, Tuple

# ----------------------------------------------------------------------------------------- references

_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3,
             "fifth": 4, "5th": 4, "sixth": 5, "6th": 5, "last": -1}
_REF_NOUN = r"(?:one|option|result|item|listing|product|villa|hotel|property|stay|link|choice|pick|pair|shoe|deal)"
_ORDINAL_REF_RE = re.compile(rf"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|1st|2nd|3rd|4th|5th|6th|last)\s+{_REF_NOUN}\b", re.I)
_NUMBER_REF_RE = re.compile(r"\b(?:option|result|item|number|no\.|#)\s*(\d{1,2})\b", re.I)
_CURRENT_REF_RE = re.compile(rf"\b(?:this|that)\s+{_REF_NOUN}\b|\b(?:buy|book|reserve|order|add|get|purchase)\s+(?:it|this|that)\b|"
                             r"\b(?:it|this|that)\s+(?:looks|seems)\s+(?:good|great|nice|perfect|fine)\b", re.I)


def parse_reference(goal: str) -> Optional[Dict[str, Any]]:
    """A reference to something shown earlier in the conversation."""
    g = goal or ""
    m = _ORDINAL_REF_RE.search(g)
    if m:
        return {"index": _ORDINALS[m.group(1).lower()], "text": m.group(0)}
    m = _NUMBER_REF_RE.search(g)
    if m and 1 <= int(m.group(1)) <= 20:
        return {"index": int(m.group(1)) - 1, "text": m.group(0)}
    m = _CURRENT_REF_RE.search(g)
    if m:
        return {"current": True, "text": m.group(0)}
    return None


# ---------------------------------------------------------------------------------------- chat prompts

_Q_WORDS = (r"(?:what|who|whom|how|why|when|where|which|whether|if|is|are|can|could|does|do|did|should|would|will|to|about|for|"
            r"that|write|plan|make|create|give|explain|summari[sz]e|list|find|suggest|draft|generate|compare|translate|help|recommend)")
_ASK_RE = re.compile(
    rf"\b(?:ask|tell(?!\s+(?:me|us)\b)|prompt|message|request)(?:\s*:\s*|\s+)(?:(?!{_Q_WORDS}\b)(?:it|him|her|them|the (?:ai|bot|assistant|chatbot)|[\w.]+(?:\s+ai)?)\s+)?"
    r"(to\s+|about\s+|for\s+|whether\s+|if\s+|:\s*|that\s+)?(.+)$", re.I)


def parse_ask(goal: str) -> Optional[str]:
    """The message to type into a chat-style site ("open ChatGPT and ask it to plan a Goa trip")."""
    m = _ASK_RE.search(goal or "")
    if not m:
        return None
    lead = (m.group(1) or "").strip().lower()
    text = m.group(2).strip().strip("\"'“”")
    if len(text) < 3:
        return None
    if lead == "about":
        text = "Tell me about " + text
    elif lead in ("whether", "if"):
        text = "Is it true that " + text if not text.endswith("?") else text
    return text[0].upper() + text[1:]


_COMPOSER_LABEL = re.compile(r"ask|message|chat|prompt|type|write|anything|question|send a|reply", re.I)


def find_composer(elements: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The main message box of a chat-style page: a textarea / contenteditable / textbox, preferring chat-like labels."""
    cands = [e for e in elements if not e.get("disabled") and (e.get("tag") == "textarea" or e.get("contenteditable")
             or e.get("role") == "textbox") and (e.get("input_type") or "") != "password"]
    if not cands:
        return None

    def score(e: Dict[str, Any]) -> float:
        box = e.get("bounding_box") or {}
        return (3 if _COMPOSER_LABEL.search(_label(e)) else 0) + (box.get("w", 0) * max(box.get("h", 0), 20)) / 40000.0
    return sorted(cands, key=score, reverse=True)[0]


# ------------------------------------------------------------------------------------------- variants

_VARIANT_ATTRS = r"size|colou?r|storage|capacity|variant|width|ram|memory|pack|flavou?r|style|fit"
_VARIANT_RE = re.compile(
    rf"\b({_VARIANT_ATTRS})\s*(?:of\s+|:|=|#|-)?\s*((?:uk|us|eu|in)\s*\d{{1,3}}(?:\.5)?|\d{{1,4}}(?:\.5)?\s*(?:gb|tb|ml|l|kg|g)?|"
    r"x{0,3}[sml]|xx?l|[a-z]{3,12})\b", re.I)
_VARIANT_STOP = {"is", "it", "the", "a", "an", "and", "or", "of", "in", "for", "available", "please", "that", "this", "options",
                 "option", "chart", "guide", "selected", "too", "also", "then", "one", "ones", "you", "me", "my", "your"}
_AVAIL_WORDS_RE = re.compile(r"\b(available|availability|in stock|stock|have it|do they have|is there)\b", re.I)
_SELECT_WORDS_RE = re.compile(r"\b(select|choose|pick|set|get|buy|order|add|purchase|book)\b", re.I)


_VALUE_FIRST_RE = re.compile(
    rf"\b(\d{{1,4}}(?:\.5)?\s*(?:gb|tb|ml|l|kg|g)|(?:uk|us|eu)\s*\d{{1,2}}(?:\.5)?|x{{0,3}}[sml]|xx?l|[a-z]{{3,12}})\s+({_VARIANT_ATTRS})\b", re.I)


_VALUE_FIRST_STOP = _VARIANT_STOP | {"which", "what", "any", "same", "right", "correct", "select", "choose", "pick", "want",
                                     "need", "get", "buy", "add", "order", "check", "with", "different", "other", "another"}


def parse_variant(goal: str) -> Optional[Dict[str, Any]]:
    def found(attribute: str, value: str) -> Dict[str, Any]:
        return {"attribute": attribute.lower().replace("colour", "color"), "value": value,
                "check": bool(_AVAIL_WORDS_RE.search(goal)), "select": bool(_SELECT_WORDS_RE.search(goal))}

    for m in _VARIANT_RE.finditer(goal or ""):  # "size 10", "colour black"
        value = re.sub(r"\s+", " ", m.group(2)).strip()
        if value.lower() not in _VARIANT_STOP:
            return found(m.group(1), value)
    for m in _VALUE_FIRST_RE.finditer(goal or ""):  # "256 GB storage", "black colour", "UK 9 size"
        value = re.sub(r"\s+", " ", m.group(1)).strip()
        if value.lower() not in _VALUE_FIRST_STOP:
            return found(m.group(2), value)
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _value_matches(label: str, attribute: str, value: str, context: str) -> bool:
    lab, val = _norm(label), _norm(value)
    if not lab or not val:
        return False
    bare = re.sub(r"^(?:uk|us|eu|in)\s*", "", val)
    if lab == val or lab == bare:
        return attribute == "" or bool(re.search(attribute.replace("color", "colou?r"), context, re.I)) or len(lab) <= 6
    attr_rx = attribute.replace("color", "colou?r")
    return bool(re.fullmatch(rf"(?:{attr_rx})?\s*[:\-]?\s*(?:uk|us|eu|in)?\s*{re.escape(bare)}(?:\s*\(.*\))?", lab, re.I))


def find_variant_elements(variant: Dict[str, Any], elements: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """(element, option) pairs offering the variant — select options and button/radio/label swatches."""
    from backend.llm_brain import element_label

    attr, value = variant["attribute"], variant["value"]
    hits: List[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]] = []
    for el in elements:
        if el.get("tag") == "select":
            for opt in el.get("options") or []:
                ctx = " ".join([el.get("accessible_name") or "", el.get("name_attr") or "", el.get("container_text") or ""])
                if _value_matches(opt.get("text", ""), attr, value, ctx + " " + attr):
                    hits.append((el, opt))
            continue
        if el.get("tag") in ("button", "label", "li", "a", "span", "div", "input") or el.get("role") in ("button", "radio", "option", "checkbox"):
            ctx = " ".join([el.get("container_text") or "", el.get("name_attr") or "", el.get("group_label") or ""])
            # The visible text can differ from the accessible name (e.g. title="Sold out" on a disabled "UK 10").
            if any(_value_matches(lbl, attr, value, ctx) for lbl in {element_label(el), (el.get("text") or "").strip()} if lbl):
                hits.append((el, None))
    return hits


_UNAVAILABLE_RE = re.compile(r"out of stock|sold out|unavailable|not available|notify me|no longer available", re.I)


def _is_selected(el: Dict[str, Any], opt: Optional[Dict[str, Any]]) -> bool:
    return bool(opt.get("selected")) if opt is not None else bool(el.get("checked") or el.get("selected_state"))


def variant_available(el: Dict[str, Any], opt: Optional[Dict[str, Any]]) -> bool:
    if opt is not None:
        return not opt.get("disabled") and not _UNAVAILABLE_RE.search(opt.get("text", ""))
    hint = " ".join([el.get("state_hint") or "", el.get("accessible_name") or "", el.get("title") or ""])
    return not el.get("disabled") and not _UNAVAILABLE_RE.search(hint) and not re.search(r"\b(disabled|unavailable|oos|soldout|sold-out|out-of-stock|strike)", el.get("state_hint") or "", re.I)


def variant_step(intents: Dict[str, Any], elements: List[Dict[str, Any]], phases: List[str]) -> Optional[Dict[str, Any]]:
    """Select the requested variant when it is offered and enabled (a safe, reversible action)."""
    from backend.llm_brain import element_label

    variant = intents.get("variant")
    if not variant or "variant" in phases:
        return None
    hits = [(e, o) for e, o in find_variant_elements(variant, elements) if not e.get("is_destructive_action")]
    usable = [(e, o) for e, o in hits if variant_available(e, o)]
    if not usable:
        return None
    el, opt = usable[0]
    if _is_selected(el, opt):
        return None
    if opt is not None:
        return {"action": "SELECT", "phase": "variant", "element_idx": el["idx"], "text": opt.get("text"),
                "reason": f"Select {variant['attribute']} “{opt.get('text')}”."}
    return {"action": "CLICK", "phase": "variant", "element_idx": el["idx"],
            "reason": f"Select {variant['attribute']} “{element_label(el)[:30]}”.", "is_consequential": False}


def variant_report(variant: Dict[str, Any], elements: List[Dict[str, Any]], page_text: str = "") -> Dict[str, Any]:
    """Is the requested variant offered and available on this page?"""
    from backend.llm_brain import element_label

    hits = find_variant_elements(variant, elements)
    out = {"attribute": variant["attribute"], "value": variant["value"], "found": bool(hits), "available": None, "evidence": ""}
    if not hits:
        out["evidence"] = f"No {variant['attribute']} “{variant['value']}” option is shown on this page."
        return out
    el, opt = hits[0]
    label = opt.get("text") if opt is not None else ((el.get("text") or "").strip() or element_label(el))
    if any(variant_available(e, o) for e, o in hits):
        selected = any(_is_selected(e, o) for e, o in hits)
        out.update(available=True, evidence=f"“{label}” is offered and selectable" + (" (selected)" if selected else ""))
        if selected and _UNAVAILABLE_RE.search(page_text[:4000] or "") and re.search(
                rf"{re.escape(variant['value'])}[^.\n]{{0,40}}(out of stock|sold out|unavailable)", page_text, re.I):
            out.update(available=False, evidence=f"The page says {variant['attribute']} {variant['value']} is out of stock")
    else:
        why = "disabled" if (opt.get("disabled") if opt else el.get("disabled")) else "marked unavailable"
        out.update(available=False, evidence=f"“{label}” is shown but {why}")
    return out


# --------------------------------------------------------------------------------------------- budget

_BUDGET_RE = re.compile(
    r"\b(?:under|below|less than|within|up ?to|upto|max(?:imum)?(?: of)?|budget(?: of| is)?|cheaper than|no more than|not more than)"
    r"\s*(?:rs\.?|inr|₹|\$|usd|€|£)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?|l|m|million)?\b", re.I)
_BUDGET_SUFFIX_RE = re.compile(r"(?:rs\.?|inr|₹|\$)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|lakhs?|lacs?)?\s+budget\b", re.I)
_MULT = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5, "m": 1e6, "million": 1e6}


def parse_max_price(text: str) -> Optional[float]:
    m = _BUDGET_RE.search(text or "") or _BUDGET_SUFFIX_RE.search(text or "")
    if not m:
        return None
    try:
        amount = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    unit = (m.group(2) or "").lower()
    return amount * _MULT.get(unit, 1.0)


# --------------------------------------------------------------------------------------------- dates

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December"]
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_TOKEN = (rf"(\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}(?:[/.-]\d{{2,4}})?|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON}(?:,?\s*\d{{4}})?|"
              rf"{_MON}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s*\d{{4}})?|today|tomorrow)")
_CHECKIN_RE = re.compile(rf"check[\s-]?in(?:\s+date)?(?:\s+(?:on|of|is|for))?\s*[:=\-]?\s*{DATE_TOKEN}", re.I)
_CHECKOUT_RE = re.compile(rf"check[\s-]?out(?:\s+date)?(?:\s+(?:on|of|is|for))?\s*[:=\-]?\s*{DATE_TOKEN}", re.I)
_RANGE_RE = re.compile(rf"\bfrom\s+{DATE_TOKEN}\s+(?:to|till|until|-|–)\s+{DATE_TOKEN}", re.I)
_ON_DATE_RE = re.compile(rf"\b(?:on|for)\s+{DATE_TOKEN}", re.I)


def to_iso(token: str, today: Optional[_dt.date] = None) -> Optional[str]:
    today = today or _dt.date.today()
    t = token.strip().lower().rstrip(".,")
    if t == "today":
        return today.isoformat()
    if t == "tomorrow":
        return (today + _dt.timedelta(days=1)).isoformat()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            return _dt.date.fromisoformat(t).isoformat()
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
        if m:  # day-first (as used in India and most of the world)
            day, month = int(m.group(1)), int(m.group(2))
            year = int(m.group(3)) if m.group(3) else None
        else:
            m = re.fullmatch(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MON})(?:,?\s*(\d{{4}}))?", t) or None
            if m:
                day, month, year = int(m.group(1)), _MONTHS[m.group(2)[:3]], int(m.group(3)) if m.group(3) else None
            else:
                m = re.fullmatch(rf"({_MON})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(\d{{4}}))?", t)
                if not m:
                    return None
                month, day, year = _MONTHS[m.group(1)[:3]], int(m.group(2)), int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        if year is None:
            candidate = _dt.date(today.year, month, day)
            year = today.year + (1 if candidate < today else 0)
        return _dt.date(year, month, day).isoformat()
    except (ValueError, KeyError):
        return None


def parse_dates(goal: str, today: Optional[_dt.date] = None) -> Dict[str, Optional[str]]:
    g = goal or ""
    out: Dict[str, Optional[str]] = {"check_in": None, "check_out": None}
    m = _RANGE_RE.search(g)
    if m:
        out["check_in"], out["check_out"] = to_iso(m.group(1), today), to_iso(m.group(2), today)
    m = _CHECKIN_RE.search(g)
    if m:
        out["check_in"] = to_iso(m.group(1), today)
    m = _CHECKOUT_RE.search(g)
    if m:
        out["check_out"] = to_iso(m.group(1), today)
    if not out["check_in"] and re.search(r"\b(book|reserve|stay|hotel|villa|room|ticket|flight|table)\b", g, re.I):
        m = _ON_DATE_RE.search(g)
        if m:
            out["check_in"] = to_iso(m.group(1), today)
    if out["check_in"] and out["check_out"] and out["check_out"] <= out["check_in"]:
        out["check_out"] = None
    return out


def parse_guests(goal: str) -> Dict[str, Optional[int]]:
    g = goal or ""
    adults = re.search(r"\b(\d{1,2})\s*(?:adults?|guests?|people|persons|pax|travell?ers)\b", g, re.I)
    kids = re.search(r"\b(\d{1,2})\s*(?:children|child|kids?)\b", g, re.I)
    rooms = re.search(r"\b(\d{1,2})\s*rooms?\b", g, re.I)
    return {"adults": int(adults.group(1)) if adults else None, "children": int(kids.group(1)) if kids else None,
            "rooms": int(rooms.group(1)) if rooms else None}


def clean_query(query: Optional[str]) -> Optional[str]:
    """Drop budget / date / guest clauses and filler adjectives from an extracted search query."""
    if not query:
        return query
    q = _BUDGET_RE.sub(" ", query)
    q = _BUDGET_SUFFIX_RE.sub(" ", q)
    q = re.sub(rf"\b(?:for\s+|with\s+)?check[\s-]?(?:in|out)\b.*$", " ", q, flags=re.I)
    q = re.sub(rf"\bfrom\s+{DATE_TOKEN}.*$", " ", q, flags=re.I)
    q = re.sub(r"\b(?:for|with)\s+\d{1,2}\s*(?:adults?|guests?|people|persons|pax|travell?ers|children|kids)\b.*$", " ", q, flags=re.I)
    q = re.sub(r"\s+and\s+show\s+me\b.*$|\s+(?:and\s+)?show\s+(?:me|them)\b.*$", " ", q, flags=re.I)
    q = re.sub(r"^\s*(?:the\s+)?(?:top|best|first)\s+\d{1,2}\s+", "", q, flags=re.I)  # "top 5 headphones"
    stripped = re.sub(r"^\s*(?:some|a few|the|good|nice|great|top|best|cheap)\s+(?:(?:some|good|nice|great|top|best|cheap)\s+)*", "", q, flags=re.I)
    q = stripped if stripped.strip() else q
    q = re.sub(r"\s+", " ", q).strip(" ,.-")
    return q or None


# ------------------------------------------------------------------------------------ booking widgets

_CHECKIN_LABEL = re.compile(r"check[\s-]?in|arrival|start date|from date|depart(?:ure)?\s*date|^from$|^dates?$|when", re.I)
_CHECKOUT_LABEL = re.compile(r"check[\s-]?out|^departure$|end date|to date|return(?:\s*date)?|^to$", re.I)
_DEST_LABEL = re.compile(r"where|destination|location|city|going|place|property|area|search", re.I)
_GUEST_LABEL = re.compile(r"adults?|guests?|travell?ers|people|persons", re.I)
_SUBMIT_LABEL = re.compile(r"^(search|find|check availability|show|go|update|apply|explore|see (?:stays|results|availability|prices))\b", re.I)
_NEXT_MONTH = re.compile(r"next month|^(next|›|»|→|>|❯)$", re.I)


def _label(el: Dict[str, Any]) -> str:
    return " ".join(filter(None, [el.get("accessible_name"), el.get("placeholder"), el.get("name_attr")])).strip()


def fillable(el: Dict[str, Any]) -> bool:
    """Text-like inputs the agent may type into (never passwords or file pickers)."""
    if el.get("disabled"):
        return False
    itype = (el.get("input_type") or "text").lower()
    if el.get("tag") == "textarea" or el.get("contenteditable") or el.get("role") in ("textbox", "searchbox", "combobox"):
        return itype != "password"
    return el.get("tag") == "input" and itype in ("", "text", "search", "email", "tel", "number", "date", "url")


def day_cell_matches(el: Dict[str, Any], iso: str) -> bool:
    d = _dt.date.fromisoformat(iso)
    data_date = (el.get("data_date") or "").strip()
    if data_date:
        return data_date.startswith(iso)
    label = el.get("accessible_name") or ""
    month_full, month_short = _MONTH_NAMES[d.month], _MONTH_NAMES[d.month][:3]
    if not re.search(rf"\b(?:{month_full}|{month_short})\b", label, re.I):
        return False
    if not re.search(rf"(?<!\d){d.day}(?:st|nd|rd|th)?(?!\d)", label):
        return False
    year = re.search(r"\b(20\d\d)\b", label)
    return year is None or int(year.group(1)) == d.year


def find_day_cell(elements: List[Dict[str, Any]], iso: str) -> Optional[Dict[str, Any]]:
    cells = [e for e in elements if not e.get("disabled") and day_cell_matches(e, iso)
             and (e.get("tag") in ("button", "td", "div", "span", "a") or e.get("role") in ("button", "gridcell", "option"))]
    return cells[0] if cells else None


def _date_field(elements: List[Dict[str, Any]], rx: re.Pattern) -> Optional[Dict[str, Any]]:
    for el in elements:
        if el.get("disabled") or el.get("input_type") == "password":
            continue
        if rx.search(_label(el)) and (el.get("tag") in ("input", "button") or el.get("role") in ("button", "combobox", "textbox")):
            return el
    return None


def _count(phases: List[str], name: str) -> int:
    return sum(1 for p in phases if p == name)


def _guest_count(el: Dict[str, Any]) -> Optional[int]:
    ctx = el.get("container_text") or ""
    m = re.search(r"[−\-–]\s*(\d{1,2})\s*[+＋]", ctx) or re.search(r"\b(\d{1,2})\s+adults?\b", ctx, re.I)
    return int(m.group(1)) if m else None


def booking_step(intents: Dict[str, Any], elements: List[Dict[str, Any]], phases: List[str],
                 best_input: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fill a booking/search form generically: destination → check-in → check-out → guests → submit."""
    from backend.llm_brain import element_label

    dates, guests = intents.get("dates") or {}, intents.get("guests") or {}
    if not (dates.get("check_in") or guests.get("adults")) or "search" in phases:
        return None
    els = [e for e in elements if not e.get("disabled")]

    def act(action: str, phase: str, el: Dict[str, Any], reason: str, text: Optional[str] = None) -> Dict[str, Any]:
        out = {"action": action, "phase": phase, "element_idx": el["idx"], "reason": reason, "is_consequential": False}
        if text is not None:
            out["text"] = text
        return out

    # destination / what to search for
    if intents.get("search_query") and "destination" not in phases:
        dest = next((e for e in els if fillable(e) and (e.get("input_type") or "text") not in ("date", "number", "email", "tel")
                     and _DEST_LABEL.search(_label(e))), None) or best_input
        if dest is not None:
            return act("FILL", "destination", dest, f"Type the destination into “{element_label(dest)[:50]}”.",
                       intents["search_query"])

    for key, rx, phase in (("check_in", _CHECKIN_LABEL, "date_in"), ("check_out", _CHECKOUT_LABEL, "date_out")):
        iso = dates.get(key)
        if not iso or phase in phases or _count(phases, f"open:{phase}") + _count(phases, f"month:{phase}") > 14:
            continue
        cell = find_day_cell(els, iso)
        if cell is not None and (f"open:{phase}" in phases or phase == "date_out" and "date_in" in phases):
            return act("CLICK", phase, cell, f"Pick {iso} in the calendar.")
        field = _date_field(els, rx)
        if field is not None and field.get("input_type") == "date":
            return act("FILL", phase, field, f"Set {key.replace('_', '-')} to {iso}.", iso)
        if cell is not None:
            return act("CLICK", phase, cell, f"Pick {iso} in the calendar.")
        if field is not None and f"open:{phase}" not in phases:
            return act("CLICK", f"open:{phase}", field, f"Open the {key.replace('_', '-')} date picker.")
        if f"open:{phase}" in phases:
            nxt = next((e for e in els if _NEXT_MONTH.search(element_label(e)) and e.get("tag") in ("button", "a", "span", "div")
                        or e.get("role") == "button" and _NEXT_MONTH.search(element_label(e))), None)
            if nxt is not None:
                return act("CLICK", f"month:{phase}", nxt, f"Move the calendar forward to reach {iso}.")
            if field is not None and fillable(field) and not field.get("readonly"):
                d = _dt.date.fromisoformat(iso)
                return act("FILL", phase, field, f"Type {key.replace('_', '-')} {iso}.", d.strftime("%d/%m/%Y"))

    adults = guests.get("adults")
    if adults and "guests" not in phases and _count(phases, "guests_inc") < 12:
        sel = next((e for e in els if e.get("tag") == "select" and _GUEST_LABEL.search(_label(e) + " " + (e.get("container_text") or "")[:60])), None)
        if sel is not None:
            opt = next((o for o in sel.get("options") or [] if re.match(rf"\D*{adults}\b", o.get("text", "")) and not o.get("disabled")), None)
            if opt is not None:
                return {"action": "SELECT", "phase": "guests", "element_idx": sel["idx"], "text": opt["text"],
                        "reason": f"Choose {opt['text']}.", "is_consequential": False}
        num = next((e for e in els if e.get("input_type") == "number" and _GUEST_LABEL.search(_label(e))), None)
        if num is not None:
            return act("FILL", "guests", num, f"Set adults to {adults}.", str(adults))
        plus = [e for e in els if (e.get("tag") == "button" or e.get("role") == "button") and (
            re.search(r"(increase|add|plus|more|\+|＋).{0,25}adult|adult.{0,25}(increase|add|plus|\+|＋)", element_label(e), re.I)
            or element_label(e).strip() in ("+", "＋") and re.search(r"adult", e.get("container_text") or "", re.I))]
        if plus:
            current = _guest_count(plus[0])
            if current is None:
                current = 1 + _count(phases, "guests_inc")
            if current < adults:
                return act("CLICK", "guests_inc", plus[0], f"Increase adults to {adults} (now {current}).")
        elif "open:guests" not in phases:
            trig = next((e for e in els if (e.get("tag") == "button" or e.get("role") in ("button", "combobox"))
                         and _GUEST_LABEL.search(element_label(e))), None)
            if trig is not None:
                return act("CLICK", "open:guests", trig, "Open the guests picker.")

    submit = next((e for e in els if (e.get("tag") in ("button", "a") or e.get("role") == "button" or
                                      e.get("input_type") == "submit") and _SUBMIT_LABEL.search(element_label(e))
                   and e.get("landmark") not in ("footer",)), None)
    if submit is not None:
        return act("CLICK", "search", submit, f"Run the search with the filled details (“{element_label(submit)[:30]}”).")
    if best_input is not None and intents.get("search_query"):
        return {"action": "FILL_AND_SUBMIT", "phase": "search", "element_idx": best_input["idx"],
                "text": intents["search_query"], "reason": "Submit the search."}
    return None
