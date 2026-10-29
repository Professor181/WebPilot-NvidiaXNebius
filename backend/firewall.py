"""Untrusted Content Firewall.

Everything WebPilot reads from a web page is untrusted. Before page content reaches the planner
this module:

1. removes invisible DOM nodes (inline styles, `hidden` attribute, simple stylesheet rules,
   off-screen positioning) and HTML comments, recording any text they carried as
   `HIDDEN_DOM_INJECTION_BLOCKED` alerts;
2. removes active / opaque content (`<script>`, `<style>`, `<noscript>`, `<iframe>`, `<svg>`, ...);
3. scans the remaining visible text and attribute values for indirect prompt-injection
   patterns, neutralizing every match in place and recording a
   `PROMPT_INJECTION_PATTERN_NEUTRALIZED` alert;
4. distills the page into structured data (tables, forms, interactive elements with ARIA roles,
   product cards, headings, links) and detects access barriers (login, OTP / 2FA, CAPTCHA);
5. wraps the sanitized text in `<untrusted_web_content source="...">` delimiters.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from backend.models import FirewallAlert

STRIP_TAGS = ("script", "style", "noscript", "iframe", "svg", "template", "object", "embed", "canvas")
MAX_TEXT_CHARS = 15000

INJECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("IGNORE_PREVIOUS_INSTRUCTIONS", re.compile(
        r"ignore\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|preceding)\s+"
        r"(?:instructions?|prompts?|rules|directions|guidelines)", re.I)),
    ("DISREGARD_INSTRUCTIONS", re.compile(
        r"disregard\s+(?:all\s+|any\s+)?(?:the\s+|your\s+)?(?:previous|prior|above|earlier|system)?\s*"
        r"(?:instructions?|rules|guidelines)", re.I)),
    ("SYSTEM_OVERRIDE", re.compile(r"system\s+override", re.I)),
    ("SYSTEM_PROMPT_MARKER", re.compile(r"system\s+prompt\s*:", re.I)),
    ("WITHOUT_USER_APPROVAL", re.compile(
        r"without\s+(?:asking\s+(?:for\s+)?|requesting\s+|waiting\s+for\s+)?(?:the\s+)?user(?:'s)?\s+"
        r"(?:approval|consent|confirmation|permission)", re.I)),
    ("BYPASS_APPROVAL", re.compile(
        r"(?:bypass|skip|circumvent|disable)\s+(?:the\s+)?(?:user\s+)?"
        r"(?:approval|confirmation|verification|safety)(?:\s+(?:gate|step|check))?", re.I)),
    ("BUY_MOST_EXPENSIVE", re.compile(r"buy\s+the\s+most\s+expensive", re.I)),
    ("NEW_INSTRUCTIONS_MARKER", re.compile(r"\b(?:new|updated|revised)\s+instructions\s*:", re.I)),
    ("CREDENTIAL_EXFILTRATION", re.compile(
        r"(?:reveal|print|send|leak|exfiltrate)\s+(?:your|the)\s+(?:system\s+prompt|api\s+keys?|credentials|"
        r"password|approval\s+token)", re.I)),
]

_HIDDEN_STYLE_RULES: List[Tuple[str, re.Pattern]] = [
    ("display:none", re.compile(r"display\s*:\s*none", re.I)),
    ("visibility:hidden", re.compile(r"visibility\s*:\s*(?:hidden|collapse)", re.I)),
    ("opacity:0", re.compile(r"(?<![\w-])opacity\s*:\s*0*(?:\.0+)?%?\s*(?:;|$|!)", re.I)),
    ("font-size:0", re.compile(r"font-size\s*:\s*0+(?:\.0+)?(?:px|em|rem|pt|%|vw|vh)?\s*(?:;|$|!)", re.I)),
    ("offscreen-position", re.compile(r"(?:left|top|text-indent)\s*:\s*-\d{4,}(?:\.\d+)?px", re.I)),
    ("zero-size-clip", re.compile(r"(?:max-)?height\s*:\s*0(?:px)?\s*;.*overflow\s*:\s*hidden", re.I | re.S)),
]

_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR_RE = re.compile(r"^(?:[a-zA-Z][\w-]*)?(?:[#.][\w-]+)?$")

IMPLICIT_ROLES = {
    "button": "button",
    "a": "link",
    "select": "combobox",
    "textarea": "textbox",
    "form": "form",
    "table": "table",
    "nav": "navigation",
    "main": "main",
    "header": "banner",
    "footer": "contentinfo",
}
INPUT_ROLES = {
    "checkbox": "checkbox",
    "radio": "radio",
    "submit": "button",
    "button": "button",
    "reset": "button",
    "image": "button",
    "range": "slider",
    "number": "spinbutton",
    "search": "searchbox",
}


def _new_alert_id() -> str:
    return f"fw_{uuid.uuid4().hex[:12]}"


def css_selector_for(el: Tag) -> str:
    if el.get("id"):
        return f"{el.name}#{el.get('id')}"
    if el.get("name"):
        return f"{el.name}[name=\"{el.get('name')}\"]"
    classes = el.get("class") or []
    if classes:
        return f"{el.name}." + ".".join(classes[:2])
    parent = el.parent
    if isinstance(parent, Tag) and parent.name not in (None, "[document]"):
        siblings = [s for s in parent.find_all(el.name, recursive=False)]
        idx = siblings.index(el) + 1 if el in siblings else 1
        return f"{css_selector_for(parent)} > {el.name}:nth-of-type({idx})"
    return el.name or "node"


def scan_for_injection(text: str) -> List[str]:
    return [pid for pid, rx in INJECTION_PATTERNS if rx.search(text or "")]


def neutralize_text(text: str) -> Tuple[str, List[str]]:
    matched: List[str] = []
    for pid, rx in INJECTION_PATTERNS:
        if rx.search(text):
            matched.append(pid)
            text = rx.sub(f"[NEUTRALIZED_INJECTION:{pid}]", text)
    return text, matched


def _stylesheet_hidden_selectors(soup: BeautifulSoup) -> Dict[str, str]:
    """Collect simple selectors (tag, .class, #id, tag.class) that stylesheets hide."""
    hidden: Dict[str, str] = {}
    for style in soup.find_all("style"):
        css = re.sub(r"/\*.*?\*/", "", style.get_text() or "", flags=re.S)
        for selectors, decls in _CSS_RULE_RE.findall(css):
            technique = next((name for name, rx in _HIDDEN_STYLE_RULES if rx.search(decls)), None)
            if not technique:
                continue
            for sel in selectors.split(","):
                sel = sel.strip()
                if sel and _SIMPLE_SELECTOR_RE.match(sel) and not sel.startswith("@"):
                    hidden[sel] = f"stylesheet:{technique}"
    return hidden


def _matches_simple_selector(el: Tag, selector: str) -> bool:
    m = re.match(r"^([a-zA-Z][\w-]*)?(?:([#.])([\w-]+))?$", selector)
    if not m:
        return False
    tag, kind, name = m.groups()
    if tag and el.name != tag.lower():
        return False
    if kind == "#":
        return el.get("id") == name
    if kind == ".":
        return name in (el.get("class") or [])
    return bool(tag)


def _hiding_technique(el: Tag, sheet_rules: Dict[str, str]) -> Optional[str]:
    if el.name in ("html", "head", "body", "title", "meta", "link"):
        return None
    if el.has_attr("hidden"):
        return "hidden-attribute"
    style = el.get("style") or ""
    if style:
        for name, rx in _HIDDEN_STYLE_RULES:
            if rx.search(style):
                return name
    for sel, technique in sheet_rules.items():
        if _matches_simple_selector(el, sel):
            return technique
    return None


def _coerce(value: str) -> Any:
    v = value.strip()
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except ValueError:
            return v
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---------------------------------------------------------------- access barriers

_OTP_NAME_RE = re.compile(r"(otp|2fa|totp|mfa|one[-_]?time|verification[-_]?code|auth[-_]?code|security[-_]?code)", re.I)
_CAPTCHA_RE = re.compile(r"(captcha|recaptcha|hcaptcha|cf-turnstile|turnstile|challenge-form|arkose|funcaptcha)", re.I)


def detect_access_barriers(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    barriers: List[Dict[str, Any]] = []
    seen: set = set()

    def add(kind: str, selector: str, evidence: str) -> None:
        if kind in seen:
            return
        seen.add(kind)
        barriers.append({
            "barrier_type": kind,
            "selector": selector,
            "evidence": evidence[:240],
            "requires_human": True,
        })

    for el in soup.find_all(True):
        ident = " ".join(filter(None, [el.get("id") or "", " ".join(el.get("class") or []), el.get("name") or ""]))
        if el.name == "iframe" and _CAPTCHA_RE.search(el.get("src") or ""):
            add("CAPTCHA_CHALLENGE", css_selector_for(el), f"CAPTCHA iframe: {el.get('src')}")
        elif ident and _CAPTCHA_RE.search(ident) and el.name != "input":
            add("CAPTCHA_CHALLENGE", css_selector_for(el), _clean_text(el.get_text(" "))[:200] or ident)
        if el.name == "input":
            itype = (el.get("type") or "text").lower()
            if itype == "password":
                add("LOGIN_REQUIRED", css_selector_for(el), "Password field present")
            if (el.get("autocomplete") or "").lower() == "one-time-code" or _OTP_NAME_RE.search(
                f"{el.get('name') or ''} {el.get('id') or ''}"
            ):
                label = el.get("aria-label") or el.get("name") or el.get("id") or "one-time code"
                add("OTP_2FA_REQUIRED", css_selector_for(el), f"One-time passcode input: {label}")

    text = _clean_text(soup.get_text(" ")).lower()
    if re.search(r"verify you are (a )?human|i'?m not a robot|are you a robot|(?:type|enter) the characters you see|"
                 r"robot check|unusual traffic from your|automated access|press (?:&|and) hold", text):
        add("CAPTCHA_CHALLENGE", "body", "Page asks to verify the visitor is human")
    if re.search(r"two-factor|2-step verification|multi-factor|hardware (otp|token|security key)", text):
        add("OTP_2FA_REQUIRED", "body", "Page requires two-factor authentication")
    title = _clean_text(soup.title.get_text()) if soup.title else ""
    if re.search(r"just a moment|attention required|access denied|checking your browser", title, re.I):
        add("BOT_CHALLENGE", "title", f"Bot-protection interstitial: {title}")
    return barriers


# ------------------------------------------------------------------- extraction

def _label_map(soup: BeautifulSoup) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for label in soup.find_all("label"):
        target = label.get("for")
        if target:
            labels[target] = _clean_text(label.get_text(" "))
    return labels


def _role_for(el: Tag) -> Optional[str]:
    if el.get("role"):
        return el.get("role")
    if el.name == "input":
        itype = (el.get("type") or "text").lower()
        return INPUT_ROLES.get(itype, "textbox")
    if el.name == "a" and not el.get("href"):
        return None
    return IMPLICIT_ROLES.get(el.name)


def _extract_tables(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    tables = []
    for t_index, table in enumerate(soup.find_all("table")):
        all_rows = table.find_all("tr")
        headers: List[str] = []
        body_rows = all_rows
        thead = table.find("thead")
        if thead:
            headers = [_clean_text(c.get_text(" ")) for c in thead.find_all(["th", "td"])]
            body_rows = [r for r in all_rows if r.find_parent("thead") is None]
        elif all_rows and all(c.name == "th" for c in all_rows[0].find_all(["th", "td"])):
            headers = [_clean_text(c.get_text(" ")) for c in all_rows[0].find_all(["th", "td"])]
            body_rows = all_rows[1:]
        rows, records, row_attrs = [], [], []
        for r in body_rows:
            cells = [_clean_text(c.get_text(" ")) for c in r.find_all(["th", "td"])]
            if not any(cells):
                continue
            rows.append(cells)
            if headers and len(headers) == len(cells):
                records.append(dict(zip(headers, cells)))
            attrs = {k[5:].replace("-", "_"): _coerce(v) for k, v in r.attrs.items() if k.startswith("data-")}
            row_attrs.append(attrs)
        caption = table.find("caption")
        tables.append({
            "table_id": table.get("id") or f"table-{t_index + 1}",
            "aria_label": table.get("aria-label"),
            "caption": _clean_text(caption.get_text(" ")) if caption else None,
            "headers": headers,
            "rows": rows,
            "records": records,
            "row_attributes": row_attrs if any(row_attrs) else [],
            "row_count": len(rows),
        })
    return tables


def _extract_interactive(soup: BeautifulSoup, source_url: str, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    elements = []
    candidates = soup.find_all(["button", "a", "input", "select", "textarea"])
    candidates += [e for e in soup.find_all(attrs={"role": True}) if e.name not in ("button", "a", "input", "select", "textarea")
                   and e.get("role") in ("button", "link", "checkbox", "tab", "menuitem", "switch", "textbox")]
    for el in candidates:
        if el.name == "input" and (el.get("type") or "").lower() == "hidden":
            continue
        if el.name == "a" and not el.get("href") and not el.get("role"):
            continue
        text = _clean_text(el.get_text(" "))
        if el.name == "input":
            text = el.get("value") if (el.get("type") or "").lower() in ("submit", "button") else ""
        item = {
            "tag": el.name,
            "role": _role_for(el),
            "id": el.get("id"),
            "name": el.get("name"),
            "type": el.get("type"),
            "text": (text or "")[:160],
            "label": labels.get(el.get("id") or "") or el.get("aria-label") or el.get("placeholder"),
            "aria_label": el.get("aria-label"),
            "required": el.has_attr("required"),
            "disabled": el.has_attr("disabled"),
            "selector": css_selector_for(el),
        }
        if el.name == "a" and el.get("href"):
            item["href"] = urljoin(source_url, el.get("href"))
        if el.name == "select":
            item["options"] = [
                {"value": o.get("value", _clean_text(o.get_text())), "text": _clean_text(o.get_text())}
                for o in el.find_all("option")
            ]
        elements.append(item)
        if len(elements) >= 150:
            break
    return elements


def _extract_forms(soup: BeautifulSoup, source_url: str, labels: Dict[str, str]) -> List[Dict[str, Any]]:
    forms = []
    for f_index, form in enumerate(soup.find_all("form")):
        fields = []
        for el in form.find_all(["input", "select", "textarea"]):
            itype = (el.get("type") or ("textarea" if el.name == "textarea" else el.name)).lower()
            field = {
                "name": el.get("name"),
                "id": el.get("id"),
                "tag": el.name,
                "type": itype,
                "label": labels.get(el.get("id") or "") or el.get("aria-label") or el.get("placeholder"),
                "required": el.has_attr("required"),
                "maxlength": int(el["maxlength"]) if (el.get("maxlength") or "").isdigit() else None,
                "selector": css_selector_for(el),
            }
            if itype == "hidden":
                field["value"] = el.get("value", "")
            if el.name == "select":
                field["options"] = [o.get("value", _clean_text(o.get_text())) for o in el.find_all("option")
                                    if o.get("value", _clean_text(o.get_text()))]
            fields.append(field)
        submit = form.find(["button", "input"], attrs={"type": "submit"}) or form.find("button")
        forms.append({
            "form_id": form.get("id") or f"form-{f_index + 1}",
            "action": urljoin(source_url, form.get("action") or source_url),
            "method": (form.get("method") or "get").upper(),
            "fields": fields,
            "submit_selector": css_selector_for(submit) if submit else None,
            "submit_text": _clean_text(submit.get_text(" ")) if submit else None,
        })
    return forms


def _extract_product_cards(soup: BeautifulSoup, source_url: str) -> List[Dict[str, Any]]:
    cards = []
    seen = set()
    for el in soup.find_all(True):
        classes = el.get("class") or []
        if not (el.has_attr("data-product-id") or "product-card" in classes):
            continue
        if id(el) in seen:
            continue
        seen.add(id(el))
        data = {k[5:].replace("-", "_"): _coerce(v) for k, v in el.attrs.items() if k.startswith("data-")}
        name_el = el.find(class_="name") or el.find(["h2", "h3", "h4"])
        price_el = el.find(class_="price")
        specs_el = el.find(class_="specs")
        stock_el = el.find(class_="stock-note")
        link = el.find("a", href=True)
        cards.append({
            **data,
            "title": _clean_text(name_el.get_text(" ")) if name_el else data.get("name"),
            "display_price": _clean_text(price_el.get_text(" ")) if price_el else None,
            "specs": _clean_text(specs_el.get_text(" ")) if specs_el else None,
            "stock_note": _clean_text(stock_el.get_text(" ")) if stock_el else None,
            "action_url": urljoin(source_url, link["href"]) if link else None,
            "selector": css_selector_for(el),
        })
    return cards


_PRICE_RE = re.compile(
    r"(?P<cur>₹|Rs\.?|INR|US\$|\$|USD|€|EUR|£|GBP)\s?(?P<amt>\d{1,3}(?:[,\d]{0,12})(?:\.\d{1,2})?)"
    r"(?:\s?(?P<suffix>k|K|M|lakh|crore|/mo|/month|/yr|/year|per month|per year))?"
)
_CURRENCY_CODES = {"₹": "INR", "Rs": "INR", "Rs.": "INR", "INR": "INR", "$": "USD", "US$": "USD", "USD": "USD",
                   "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP"}


def _extract_structured_data(soup: BeautifulSoup, source_url: str, alerts: List[FirewallAlert]) -> Dict[str, Any]:
    """JSON-LD + OpenGraph metadata. Runs before <script> stripping; every string is neutralized."""
    import json

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, str):
            new, matched = neutralize_text(value)
            if matched:
                alerts.append(FirewallAlert(
                    alert_id=_new_alert_id(), alert_type="PROMPT_INJECTION_PATTERN_NEUTRALIZED", severity="HIGH",
                    source_url=source_url, element_selector=f'script[type="application/ld+json"] {path}',
                    hiding_technique="json-ld-metadata", matched_patterns=matched, snippet=value[:300],
                    action_taken="Injection text inside structured data neutralized",
                ))
            return new[:1000]
        if isinstance(value, list):
            return [clean(v, f"{path}[{i}]") for i, v in enumerate(value[:50])]
        if isinstance(value, dict):
            return {str(k)[:80]: clean(v, f"{path}.{k}") for k, v in list(value.items())[:60]}
        return value

    json_ld: List[Any] = []
    for i, script in enumerate(soup.find_all("script", attrs={"type": "application/ld+json"})[:10]):
        try:
            json_ld.append(clean(json.loads(script.string or script.get_text() or ""), f"[{i}]"))
        except (ValueError, TypeError):
            continue
    open_graph = {}
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop.startswith(("og:", "twitter:", "article:", "product:")) and meta.get("content"):
            open_graph[prop] = clean(meta["content"], prop)
    return {"json_ld": json_ld, "open_graph": open_graph}


def _extract_articles(soup: BeautifulSoup, source_url: str) -> List[Dict[str, Any]]:
    """Headline/article links: <article> blocks, title-classed links (e.g. Hacker News) and h2/h3 links."""
    seen: set = set()
    out: List[Dict[str, Any]] = []
    order = {id(el): i for i, el in enumerate(soup.find_all(True))}

    def add(title: str, href: Optional[str], selector: str, meta: Optional[Dict[str, Any]] = None,
            el: Optional[Tag] = None) -> None:
        title = _clean_text(title)
        if len(title) < 12 or len(out) >= 60:
            return
        url = urljoin(source_url, href) if href else None
        key = (title.lower(), url)
        if key in seen:
            return
        seen.add(key)
        item: Dict[str, Any] = {"title": title[:240], "url": url, "selector": selector,
                                "_pos": order.get(id(el), 10**9) if el is not None else 10**9}
        if meta:
            item.update(meta)
        out.append(item)

    for art in soup.find_all("article"):
        head = art.find(["h1", "h2", "h3", "h4"])
        link = (head.find("a", href=True) if head else None) or art.find("a", href=True)
        title = head.get_text(" ") if head else (link.get_text(" ") if link else "")
        add(title, link["href"] if link else None, css_selector_for(art), el=art)
    for a in soup.find_all("a", href=True):
        parent = a.parent if isinstance(a.parent, Tag) else None
        classes = " ".join((parent.get("class") or []) + (a.get("class") or [])) if parent else " ".join(a.get("class") or [])
        in_heading = a.find_parent(["h1", "h2", "h3"]) is not None
        if not (re.search(r"title|headline|story|storylink", classes, re.I) or in_heading):
            continue
        meta: Dict[str, Any] = {}
        row = a.find_parent("tr", id=True)
        if row is not None:
            score = soup.find(id=f"score_{row['id']}")
            if score is not None:
                digits = re.sub(r"[^\d]", "", score.get_text())
                meta["points"] = int(digits) if digits else None
            rank = row.find(class_="rank")
            if rank is not None:
                meta["rank"] = _clean_text(rank.get_text()).rstrip(".")
        add(a.get_text(" "), a["href"], css_selector_for(a), meta, el=a)
    out.sort(key=lambda item: item.pop("_pos"))  # document order: a front page reads top-down
    return out


def _extract_prices(text: str) -> List[Dict[str, Any]]:
    prices: List[Dict[str, Any]] = []
    for line in text.splitlines():
        for m in _PRICE_RE.finditer(line):
            raw_amt = m.group("amt").replace(",", "")
            try:
                amount = float(raw_amt)
            except ValueError:
                continue
            suffix = (m.group("suffix") or "").lower()
            if suffix == "k":
                amount *= 1_000
            elif suffix == "m":
                amount *= 1_000_000
            elif suffix == "lakh":
                amount *= 100_000
            elif suffix == "crore":
                amount *= 10_000_000
            prices.append({
                "currency": _CURRENCY_CODES.get(m.group("cur"), m.group("cur")),
                "amount": round(amount, 2),
                "raw": m.group(0).strip(),
                "period": suffix if suffix.startswith(("/", "per")) else None,
                "context": line.strip()[:160],
            })
            if len(prices) >= 60:
                return prices
    return prices


# --------------------------------------------------------------------- main entry

def wrap_untrusted(text: str, source_url: str) -> str:
    safe_src = source_url.replace('"', "%22")
    safe_text = re.sub(r"</?\s*untrusted_web_content", "[delimiter-removed]", text, flags=re.I)
    return f'<untrusted_web_content source="{safe_src}">\n{safe_text}\n</untrusted_web_content>'


def sanitize_html_and_extract(html: str, source_url: str) -> Dict[str, Any]:
    """Sanitize an untrusted HTML document and distill it into planner-safe structured data."""
    soup = BeautifulSoup(html or "", "html.parser")
    alerts: List[FirewallAlert] = []
    stats = {"hidden_nodes_removed": 0, "comments_removed": 0, "active_tags_removed": 0, "injections_neutralized": 0}

    barriers = detect_access_barriers(soup)
    sheet_rules = _stylesheet_hidden_selectors(soup)
    structured = _extract_structured_data(soup, source_url, alerts)

    # 1. HTML comments can smuggle instructions into scrapers.
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        text = _clean_text(str(comment))
        patterns = scan_for_injection(text)
        if patterns:
            alerts.append(FirewallAlert(
                alert_id=_new_alert_id(), alert_type="HIDDEN_DOM_INJECTION_BLOCKED", severity="HIGH",
                source_url=source_url, element_selector="<!-- comment -->", hiding_technique="html-comment",
                matched_patterns=patterns, snippet=text[:300],
                action_taken="Comment removed before content reached the planner",
            ))
        comment.extract()
        stats["comments_removed"] += 1

    # 2. Invisible DOM nodes (detected before <style> is stripped so stylesheet rules apply).
    hidden_nodes: List[Tuple[Tag, str]] = []
    hidden_ids: set = set()
    for el in soup.find_all(True):
        if el.name in STRIP_TAGS:
            continue
        if any(id(p) in hidden_ids for p in el.parents):
            continue
        technique = _hiding_technique(el, sheet_rules)
        if technique:
            hidden_nodes.append((el, technique))
            hidden_ids.add(id(el))
    for el, technique in hidden_nodes:
        text = _clean_text(el.get_text(" "))
        selector = css_selector_for(el)
        if text:
            patterns = scan_for_injection(text)
            alerts.append(FirewallAlert(
                alert_id=_new_alert_id(), alert_type="HIDDEN_DOM_INJECTION_BLOCKED",
                severity="CRITICAL" if patterns else "LOW",
                source_url=source_url, element_selector=selector, hiding_technique=technique,
                matched_patterns=patterns, snippet=text[:300],
                action_taken="Invisible node removed; its text was never shown to the planner",
            ))
        el.decompose()
        stats["hidden_nodes_removed"] += 1

    # 3. Active / opaque content.
    for el in soup.find_all(list(STRIP_TAGS)):
        el.decompose()
        stats["active_tags_removed"] += 1

    # 4. Visible prompt-injection text and attribute values: neutralize in place.
    for node in list(soup.find_all(string=True)):
        if not isinstance(node, NavigableString) or not node.strip():
            continue
        new_text, matched = neutralize_text(str(node))
        if matched:
            parent = node.parent
            alerts.append(FirewallAlert(
                alert_id=_new_alert_id(), alert_type="PROMPT_INJECTION_PATTERN_NEUTRALIZED", severity="HIGH",
                source_url=source_url, element_selector=css_selector_for(parent) if isinstance(parent, Tag) else None,
                matched_patterns=matched, snippet=_clean_text(str(node))[:300],
                action_taken="Visible injection text replaced with a neutralization marker",
            ))
            node.replace_with(new_text)
            stats["injections_neutralized"] += len(matched)
    for el in soup.find_all(True):
        for attr, value in list(el.attrs.items()):
            if not isinstance(value, str) or len(value) < 12:
                continue
            new_value, matched = neutralize_text(value)
            if matched:
                alerts.append(FirewallAlert(
                    alert_id=_new_alert_id(), alert_type="PROMPT_INJECTION_PATTERN_NEUTRALIZED", severity="HIGH",
                    source_url=source_url, element_selector=f"{css_selector_for(el)}[{attr}]",
                    matched_patterns=matched, snippet=value[:300],
                    action_taken=f"Injection text in attribute '{attr}' neutralized",
                ))
                el[attr] = new_value
                stats["injections_neutralized"] += len(matched)

    # 5. Distill.
    labels = _label_map(soup)
    title = _clean_text(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    root = soup.body or soup
    text = "\n".join(line for line in (l.strip() for l in root.get_text("\n").splitlines()) if line)

    # Injections split across several elements only become visible in the joined text.
    text, residual = neutralize_text(text)
    if residual:
        alerts.append(FirewallAlert(
            alert_id=_new_alert_id(), alert_type="PROMPT_INJECTION_PATTERN_NEUTRALIZED", severity="HIGH",
            source_url=source_url, element_selector="body (cross-element text)", matched_patterns=residual,
            snippet="Injection phrase spanning multiple elements", action_taken="Neutralized in distilled text",
        ))
        stats["injections_neutralized"] += len(residual)
    truncated = len(text) > MAX_TEXT_CHARS
    text = text[:MAX_TEXT_CHARS]

    headings = []
    for h in soup.find_all(lambda t: t.name in ("h1", "h2", "h3", "h4") or (t.get("role") == "heading")):
        heading_text = _clean_text(h.get_text(" "))
        if not heading_text:
            continue
        level = int(h.name[1]) if h.name in ("h1", "h2", "h3", "h4") else int(h.get("aria-level") or 2) \
            if str(h.get("aria-level") or "2").isdigit() else 2
        headings.append({"level": level, "text": heading_text})
        if len(headings) >= 40:
            break
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(source_url, a["href"])
        if href.startswith(("http://", "https://")):
            links.append({"text": _clean_text(a.get_text(" "))[:120], "href": href})
        if len(links) >= 60:
            break

    return {
        "source_url": source_url,
        "title": title,
        "meta_description": meta.get("content") if meta else None,
        "headings": headings,
        "links": links,
        "tables": _extract_tables(soup),
        "forms": _extract_forms(soup, source_url, labels),
        "interactive_elements": _extract_interactive(soup, source_url, labels),
        "product_cards": _extract_product_cards(soup, source_url),
        "articles": _extract_articles(soup, source_url),
        "prices": _extract_prices(text),
        "structured_data": structured,
        "barriers": barriers,
        "firewall_alerts": [a.model_dump() for a in alerts],
        "sanitized_text": text,
        "wrapped_text": wrap_untrusted(text, source_url),
        "text_truncated": truncated,
        "stats": stats,
    }


def summarize_alerts(alerts: Iterable[Dict[str, Any]]) -> str:
    alerts = list(alerts)
    if not alerts:
        return "No firewall alerts."
    hidden = sum(1 for a in alerts if a["alert_type"] == "HIDDEN_DOM_INJECTION_BLOCKED" and a["matched_patterns"])
    visible = sum(1 for a in alerts if a["alert_type"] == "PROMPT_INJECTION_PATTERN_NEUTRALIZED")
    parts = []
    if hidden:
        parts.append(f"{hidden} hidden prompt-injection payload(s) blocked")
    if visible:
        parts.append(f"{visible} visible injection pattern(s) neutralized")
    benign = len(alerts) - hidden - visible
    if benign:
        parts.append(f"{benign} invisible text node(s) removed")
    return "; ".join(parts) + "."
