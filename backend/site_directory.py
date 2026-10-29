"""Well-known brand / service names → their official root URL, used only to choose where a live task STARTS.

This is deliberately the one place WebPilot knows any site by name. Everything the autopilot does once the
page is open (finding the search box, choosing what to click, classifying consequential controls) stays
site-agnostic in `live_autopilot.py` / `llm_brain.semantic_decide`. A domain typed in the goal always wins
over this table, and unknown names still go through web-search discovery.

Override or extend with WEBPILOT_SITE_DIRECTORY_JSON='{"myshop": "https://shop.example"}'.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger("webpilot.site_directory")

# name (lowercase; spaces allowed) → official root URL
BUILTIN_SITES: Dict[str, str] = {
    "chatgpt": "https://chatgpt.com",
    "openai": "https://openai.com",
    "claude": "https://claude.ai",
    "gemini": "https://gemini.google.com",
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "stack overflow": "https://stackoverflow.com",
    "stackoverflow": "https://stackoverflow.com",
    "leetcode": "https://leetcode.com",
    "hacker news": "https://news.ycombinator.com",
    "wikipedia": "https://www.wikipedia.org",
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "x.com": "https://x.com",
    "twitter": "https://x.com",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
    "myntra": "https://www.myntra.com",
    "ebay": "https://www.ebay.com",
    "netflix": "https://www.netflix.com",
    "spotify": "https://open.spotify.com",
    "booking.com": "https://www.booking.com",
    "airbnb": "https://www.airbnb.com",
    "zomato": "https://www.zomato.com",
    "swiggy": "https://www.swiggy.com",
    "irctc": "https://www.irctc.co.in",
    "makemytrip": "https://www.makemytrip.com",
    "gmail": "https://mail.google.com",
    "google maps": "https://maps.google.com",
    "imdb": "https://www.imdb.com",
}

# A brand only counts as the *destination* when the goal points at it — "on Flipkart", "open ChatGPT",
# "ask chatgpt", "go to github", "Flipkart: …" — not when it is merely a topic ("Amazon rainforest facts").
_CUE = r"(?:on|at|from|in|into|via|using|use|open|opening|visit|launch|ask|search|check|browse|go to|goto|navigate to|head to|over to|onto)"


def directory() -> Dict[str, str]:
    sites = dict(BUILTIN_SITES)
    raw = os.getenv("WEBPILOT_SITE_DIRECTORY_JSON", "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            sites.update({str(k).lower().strip(): str(v).strip() for k, v in extra.items()
                          if str(v).strip().startswith(("https://", "http://"))})
        except (ValueError, AttributeError):
            logger.warning("Ignoring invalid WEBPILOT_SITE_DIRECTORY_JSON")
    return sites


def _name_re(name: str) -> str:
    return r"(?<![\w.])" + r"\s+".join(re.escape(part) for part in name.split()) + r"(?![\w])(?!\.\w)"


def lookup(name: Optional[str]) -> Optional[Tuple[str, str]]:
    """Exact lookup of a site name (e.g. the parsed site hint). Returns (name, url)."""
    key = re.sub(r"\s+", " ", (name or "").strip().lower()).strip(" .,!?:;'\"")
    if key.startswith("the "):
        key = key[4:]
    url = directory().get(key)
    return (key, url) if url else None


def infer_site(goal: str) -> Optional[Tuple[str, str]]:
    """Find a known brand the goal points at. Returns (name, official root URL) or None."""
    text = goal or ""
    best: Optional[Tuple[int, str, str]] = None
    for name, url in directory().items():
        pattern = _name_re(name)
        cued = re.search(rf"\b{_CUE}\s+(?:the\s+|my\s+|our\s+)?{pattern}", text, re.I)
        leading = re.match(rf"\s*(?:alexa[,\s]+)?{pattern}\s*[:,\-–]", text, re.I)
        hit = cued or leading
        if hit and (best is None or hit.start() < best[0] or (hit.start() == best[0] and len(name) > len(best[1]))):
            best = (hit.start(), name, url)
    return (best[1], best[2]) if best else None
