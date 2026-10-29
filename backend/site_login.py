"""Generic sign-in / sign-up / one-time-code page detection from the DOM index (no site-specific selectors)."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_IDENT_RE = re.compile(r"e-?mail|user ?name|user ?id|login|phone|mobile|account|identifier|\bid\b|sign.?in", re.I)
_NAME_RE = re.compile(r"^(?!.*user)(?:your |full |first |last |given |family )?name\b|first name|last name|full name", re.I)
_SUBMIT_RE = re.compile(r"^(sign ?in|log ?in|login|continue|next|submit|verify|create (?:an |your |my )?account|sign ?up|"
                        r"register|join|get started|proceed|done)\b", re.I)
_SIGNUP_LINK_RE = re.compile(r"create (?:an |your |a new )?(?:\w+ )?account|^sign ?up\b|^register\b|new (?:customer|user|here|to)|"
                             r"^join\b|don'?t have an account|get started", re.I)
_LOGIN_LINK_RE = re.compile(r"^(?:hello,? sign in|sign ?in|log ?in|login|sign in ?/ ?(?:register|sign up))\b", re.I)
_SIGNUP_TEXT_RE = re.compile(r"create (?:an |your )?account|sign ?up|register|confirm password|re-?enter password|new password", re.I)
_AUTH_URL_RE = re.compile(r"(?:^|[/._-])(log-?in|sign-?in|signin|sign-?up|signup|register|auth|accounts?/(?:login|signin)|ap/signin|sso)"
                          r"(?:$|[/._?#-])", re.I)
_OTP_RE = re.compile(r"\botp\b|one[- ]time|verification code|security code|enter (?:the )?code|6-digit|passcode", re.I)
_TERMS_RE = re.compile(r"terms|privacy|agree|conditions|policy", re.I)


def _label(el: Dict[str, Any]) -> str:
    return " ".join(filter(None, [el.get("accessible_name"), el.get("placeholder"), el.get("name_attr")])).strip()


def classify_auth_page(elements: List[Dict[str, Any]], url: str, title: str, text: str) -> Optional[Dict[str, Any]]:
    """Describe the sign-in / sign-up form on the page, or None when the page is not an auth page."""
    from backend.llm_brain import element_label

    els = [e for e in elements if not e.get("disabled")]
    inputs = [e for e in els if e.get("tag") == "input"]
    pw = [e for e in inputs if (e.get("input_type") or "") == "password"]
    otp = [e for e in inputs if (e.get("autocomplete") or "") == "one-time-code" or _OTP_RE.search(_label(e))]
    ident = [e for e in inputs if (e.get("input_type") or "text") in ("", "text", "email", "tel")
             and _IDENT_RE.search(_label(e) + " " + (e.get("autocomplete") or "")) and not re.search(r"search", _label(e), re.I)
             and e not in otp]
    names = [e for e in inputs if (e.get("input_type") or "text") in ("", "text") and _NAME_RE.search(_label(e)) and e not in ident]
    buttons = [e for e in els if e.get("tag") in ("button", "a") or e.get("role") == "button" or e.get("input_type") in ("submit", "button")]
    submit = next((b for b in buttons if b.get("tag") != "a" and _SUBMIT_RE.search(element_label(b))), None)
    signup_link = next((b for b in buttons if _SIGNUP_LINK_RE.search(element_label(b))), None)
    login_link = next((b for b in buttons if _LOGIN_LINK_RE.search(element_label(b))), None)
    terms = [e for e in inputs if (e.get("input_type") or "") == "checkbox" and _TERMS_RE.search(_label(e) + " " + (e.get("container_text") or "")[:120])]
    page_words = f"{title} {(text or '')[:1500]}"
    auth_url = bool(_AUTH_URL_RE.search(url or ""))

    if otp and len(inputs) <= 8:
        kind = "otp"
    elif len(pw) >= 2 or (pw and (_SIGNUP_TEXT_RE.search(element_label(submit) if submit else "") or names)):
        kind = "signup"
    elif pw:
        kind = "login"
    elif ident and (auth_url or re.search(r"\b(sign ?in|log ?in)\b", title or "", re.I)) and len(inputs) <= 6:
        kind = "login_identifier"  # multi-step: email/phone first, password on the next screen
    else:
        return None
    # A sign-in box on an ordinary content page (sidebar, footer) is not a wall we must pass.
    focused = auth_url or len(els) <= 40 or kind == "otp" or bool(re.search(r"\b(sign ?in|log ?in|create account|sign ?up)\b", title or "", re.I))
    missing = [_label(e) or e.get("input_type") for e in inputs
               if e.get("required") and e not in pw + ident + names + otp + terms and (e.get("input_type") or "") not in ("hidden", "submit")]
    return {
        "kind": kind, "focused": focused, "auth_url": auth_url,
        "identifier_idx": ident[0]["idx"] if ident else None,
        "identifier_type": (ident[0].get("input_type") or "text") if ident else None,
        "password_idxs": [e["idx"] for e in pw],
        "name_idxs": [e["idx"] for e in names],
        "otp_idx": otp[0]["idx"] if otp else None,
        "submit_idx": submit["idx"] if submit else None,
        "submit_label": element_label(submit)[:40] if submit else None,
        "signup_link_idx": signup_link["idx"] if signup_link else None,
        "login_link_idx": login_link["idx"] if login_link else None,
        "terms_idxs": [e["idx"] for e in terms],
        "missing_required": [m for m in missing if m][:6],
        "captcha": bool(re.search(r"captcha|i'?m not a robot|verify you are (?:a )?human", page_words, re.I)),
    }


def is_blocking(auth: Optional[Dict[str, Any]]) -> bool:
    return bool(auth) and bool(auth.get("focused"))
