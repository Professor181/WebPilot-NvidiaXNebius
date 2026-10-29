"""Post-Action Verification Engine.

Clicking a button only *attempts* an action. A task is marked COMPLETED only after this engine
inspects independent post-action evidence (DOM confirmation selectors, receipt numbers, ledger
values, row counts, recomputed arithmetic) and every check passes.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from bs4 import BeautifulSoup

from backend.models import VerificationCheck, VerificationReport, VerificationVerdict


def _text(soup: BeautifulSoup, selector: str) -> Optional[str]:
    el = soup.select_one(selector)
    return re.sub(r"\s+", " ", el.get_text(" ")).strip() if el else None


def _attr(soup: BeautifulSoup, selector: str, attr: str) -> Optional[str]:
    el = soup.select_one(selector)
    return el.get(attr) if el else None


def _report(checks: List[VerificationCheck], method: str, success: VerificationVerdict, notes: str = "") -> Dict[str, Any]:
    verdict = success if checks and all(c.passed for c in checks) else VerificationVerdict.VERIFICATION_FAILED
    failed = [c.check for c in checks if not c.passed]
    if failed:
        notes = (notes + " " if notes else "") + "Failed checks: " + ", ".join(failed) + "."
    return VerificationReport(verdict=verdict, method=method, checks=checks, notes=notes or None).model_dump(mode="json")


def unverified_report(method: str, notes: str, checks: Optional[Iterable[VerificationCheck]] = None) -> Dict[str, Any]:
    return VerificationReport(
        verdict=VerificationVerdict.UNVERIFIED, method=method, checks=list(checks or []), notes=notes
    ).model_dump(mode="json")


# ------------------------------------------------------------------- attendance

def classes_needed_for_threshold(attended: int, conducted: int, threshold_pct: float = 75.0) -> int:
    """Consecutive classes that must be attended to reach the threshold.

    For 75%: (a + k) / (c + k) >= 3/4  <=>  k >= 3c - 4a, so k = max(0, 3c - 4a).
    """
    if abs(threshold_pct - 75.0) < 1e-9:
        return max(0, 3 * conducted - 4 * attended)
    p = threshold_pct / 100.0
    k = 0
    while conducted + k > 0 and (attended + k) / (conducted + k) < p - 1e-12:
        k += 1
    return k


def verify_attendance(rows: List[Dict[str, Any]], threshold_pct: float, expected_row_count: int) -> Dict[str, Any]:
    checks = [VerificationCheck(
        check="attendance_table_row_count", passed=len(rows) == expected_row_count and len(rows) > 0,
        expected=expected_row_count, observed=len(rows),
    )]
    p = threshold_pct / 100.0
    for r in rows:
        a, c, k = r["attended"], r["conducted"], r["classes_needed"]
        recomputed = round(a * 100.0 / c, 1) if c else 0.0
        checks.append(VerificationCheck(
            check=f"{r['code']}_percentage_matches_portal",
            passed=r.get("displayed_pct") is None or abs(recomputed - r["displayed_pct"]) <= 0.05,
            expected=recomputed, observed=r.get("displayed_pct"),
        ))
        reaches = c + k == 0 or (a + k) / (c + k) >= p - 1e-12
        minimal = k == 0 or (a + k - 1) / (c + k - 1) < p - 1e-12
        checks.append(VerificationCheck(
            check=f"{r['code']}_classes_needed_is_minimal_solution", passed=reaches and minimal,
            expected=f"(a+k)/(c+k) >= {threshold_pct}% with minimal k", observed={"k": k, "after_k_pct": round((a + k) * 100.0 / (c + k), 2) if c + k else None},
        ))
        checks.append(VerificationCheck(
            check=f"{r['code']}_below_threshold_flag", passed=r["below_threshold"] == (recomputed < threshold_pct),
            expected=recomputed < threshold_pct, observed=r["below_threshold"],
        ))
    return _report(checks, "DOM_TABLE_EXTRACTION_PLUS_ARITHMETIC_RECOMPUTATION", VerificationVerdict.VERIFIED_SUCCESS)


# ---------------------------------------------------------------------- checkout

ORDER_ID_RE = re.compile(r"^SIM-ORD-\d{4}-\d{3,}$")


def verify_checkout(confirm_html: str, status_code: int, locked: Dict[str, Any]) -> Dict[str, Any]:
    soup = BeautifulSoup(confirm_html or "", "html.parser")
    order_id = _text(soup, "#order-confirmation-id")
    item = _text(soup, "#confirmed-item")
    seller = _text(soup, "#confirmed-seller")
    amount = _attr(soup, "#confirmed-amount", "data-amount")
    status = _text(soup, "#confirmed-status")
    badge = _text(soup, "#simulation-badge") or ""
    checks = [
        VerificationCheck(check="http_status_ok", passed=status_code == 200, expected=200, observed=status_code),
        VerificationCheck(check="confirmation_selector_present", passed=order_id is not None,
                          expected="#order-confirmation-id", observed=order_id),
        VerificationCheck(check="order_id_format", passed=bool(order_id and ORDER_ID_RE.match(order_id)),
                          expected=ORDER_ID_RE.pattern, observed=order_id),
        VerificationCheck(check="item_matches_approved", passed=item == locked.get("item_name"),
                          expected=locked.get("item_name"), observed=item),
        VerificationCheck(check="seller_matches_approved", passed=seller == locked.get("seller"),
                          expected=locked.get("seller"), observed=seller),
        VerificationCheck(check="amount_matches_approved",
                          passed=amount is not None and amount.isdigit() and int(amount) == int(locked.get("total_price_inr", -1)),
                          expected=locked.get("total_price_inr"), observed=int(amount) if amount and amount.isdigit() else amount),
        VerificationCheck(check="order_status_confirmed", passed=status == "CONFIRMED", expected="CONFIRMED", observed=status),
        VerificationCheck(check="ledger_matches_link_parameters", passed=soup.select_one("#order-mismatch") is None,
                          expected="no #order-mismatch", observed="mismatch" if soup.select_one("#order-mismatch") else "consistent"),
        VerificationCheck(check="controlled_sandbox_badge_present", passed="NO REAL MONEY CHARGED" in badge,
                          expected="CONTROLLED SANDBOX SIMULATION — NO REAL MONEY CHARGED", observed=badge or None),
    ]
    report = _report(
        checks, "POST_ACTION_DOM_CONFIRMATION_SELECTORS", VerificationVerdict.SIMULATED_TRANSACTION_VERIFIED,
        notes="Controlled sandbox transaction; no real money was charged.",
    )
    report["confirmation_id"] = order_id
    return report


# ------------------------------------------------------------------ form submit

RECEIPT_RE = re.compile(r"^NIG-2026-[0-9A-F]{8}$")


def verify_form_submission(receipt_html: str, status_code: int, submitted: Dict[str, str]) -> Dict[str, Any]:
    soup = BeautifulSoup(receipt_html or "", "html.parser")
    receipt = _text(soup, "#submission-receipt-id")
    checks = [
        VerificationCheck(check="http_status_ok", passed=status_code == 200, expected=200, observed=status_code),
        VerificationCheck(check="no_form_errors", passed=soup.select_one("#form-errors") is None,
                          expected="no #form-errors", observed=_text(soup, "#form-errors")),
        VerificationCheck(check="receipt_selector_present", passed=receipt is not None,
                          expected="#submission-receipt-id", observed=receipt),
        VerificationCheck(check="receipt_id_format", passed=bool(receipt and RECEIPT_RE.match(receipt)),
                          expected=RECEIPT_RE.pattern, observed=receipt),
    ]
    for name, value in submitted.items():
        echoed = _text(soup, f'dd[data-field="{name}"]')
        checks.append(VerificationCheck(
            check=f"echoed_{name}_matches", passed=echoed == re.sub(r"\s+", " ", value).strip(),
            expected=value[:120], observed=(echoed or "")[:120] or None,
        ))
    report = _report(checks, "POST_SUBMIT_RECEIPT_AND_FIELD_ECHO", VerificationVerdict.VERIFIED_SUCCESS,
                     notes="Submitted to the controlled sandbox grant portal.")
    report["confirmation_id"] = receipt
    return report


# ------------------------------------------------------------------- research

def verify_github_analysis(owner: str, repo: str, data: Dict[str, Any]) -> Dict[str, Any]:
    full_name = (data.get("full_name") or "").lower()
    issues = data.get("open_issues_sample") or []
    checks = [
        VerificationCheck(check="repository_identity_matches", passed=full_name == f"{owner}/{repo}".lower(),
                          expected=f"{owner}/{repo}", observed=data.get("full_name")),
        VerificationCheck(check="html_url_is_github",
                          passed=str(data.get("html_url", "")).lower().startswith(f"https://github.com/{owner}/{repo}".lower()),
                          expected=f"https://github.com/{owner}/{repo}", observed=data.get("html_url")),
        VerificationCheck(check="stars_is_non_negative_integer",
                          passed=isinstance(data.get("stars"), int) and data["stars"] >= 0, expected=">= 0", observed=data.get("stars")),
        VerificationCheck(check="issue_links_are_direct_urls",
                          passed=all(str(i.get("html_url", "")).startswith("https://github.com/") for i in issues),
                          expected="https://github.com/...", observed=len(issues)),
    ]
    return _report(checks, f"{data.get('source', 'GITHUB')}_CROSS_FIELD_CONSISTENCY", VerificationVerdict.VERIFIED_SUCCESS)


def verify_profile_comparison(profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    checks = [VerificationCheck(check="two_profiles_extracted", passed=len(profiles) == 2, expected=2, observed=len(profiles))]
    for p in profiles:
        total = p.get("easy", 0) + p.get("medium", 0) + p.get("hard", 0)
        checks.append(VerificationCheck(
            check=f"{p['username']}_difficulty_sum_equals_total", passed=total == p.get("total_solved"),
            expected=total, observed=p.get("total_solved"),
        ))
        checks.append(VerificationCheck(
            check=f"{p['username']}_has_source_citation", passed=bool(p.get("source_url")),
            expected="source_url", observed=p.get("source_url"),
        ))
    return _report(checks, "EXTRACTED_METRICS_ARITHMETIC_CROSS_CHECK", VerificationVerdict.VERIFIED_SUCCESS)


def verify_page_distillation(status_code: int, distilled: Dict[str, Any]) -> Dict[str, Any]:
    checks = [
        VerificationCheck(check="http_status_ok", passed=200 <= status_code < 400, expected="2xx/3xx", observed=status_code),
        VerificationCheck(check="content_extracted", passed=bool(distilled.get("sanitized_text")),
                          expected="non-empty sanitized text", observed=len(distilled.get("sanitized_text", ""))),
        VerificationCheck(check="untrusted_content_wrapped",
                          passed=distilled.get("wrapped_text", "").startswith("<untrusted_web_content"),
                          expected="<untrusted_web_content ...>", observed=distilled.get("wrapped_text", "")[:40]),
    ]
    return _report(checks, "SEMANTIC_DOM_DISTILLATION_CHECKS", VerificationVerdict.VERIFIED_SUCCESS)


def verify_grounded_answer(answer: Dict[str, Any], dom_items: List[Dict[str, Any]]) -> List[VerificationCheck]:
    """Every citation must point at a real item of the sanitized DOM snapshot, with identical text and points."""
    by_id = {i["item_id"]: i for i in dom_items}
    citations = answer.get("citations") or []
    checks = [VerificationCheck(
        check="answer_citations_exist_in_dom_snapshot",
        passed=all(c["item_id"] in by_id for c in citations),
        expected="every cited item_id present", observed=[c["item_id"] for c in citations],
    )]
    checks.append(VerificationCheck(
        check="cited_text_matches_dom_verbatim",
        passed=all(c["item_id"] in by_id and by_id[c["item_id"]]["title"] == c["title"] for c in citations),
        expected="identical titles", observed=len(citations),
    ))
    checks.append(VerificationCheck(
        check="cited_points_match_dom",
        passed=all(c["item_id"] in by_id and by_id[c["item_id"]].get("points") == c.get("points") for c in citations),
        expected="identical points", observed=[c.get("points") for c in citations],
    ))
    plan = answer.get("plan") or {}
    if answer.get("provider_used") == "deterministic_symbolic":
        checks.append(VerificationCheck(
            check="citation_count_within_requested_top_n", passed=len(citations) <= int(plan.get("top_n", 5)),
            expected=f"<= {plan.get('top_n', 5)}", observed=len(citations),
        ))
        if plan.get("terms") and not plan.get("summarize"):
            checks.append(VerificationCheck(
                check="every_cited_item_matches_a_filter_term",
                passed=all(c.get("matched_terms") for c in citations), expected=plan["terms"][:8],
                observed=[c.get("matched_terms") for c in citations],
            ))
        pts = [c.get("points") for c in citations]
        if plan.get("rank_by") == "points" and all(isinstance(p, int) for p in pts) and pts:
            checks.append(VerificationCheck(check="ranked_by_points_descending", passed=pts == sorted(pts, reverse=True),
                                            expected="descending", observed=pts))
    referenced = {int(m) for m in re.findall(r"\[#(\d+)\]", answer.get("answer") or "")}
    checks.append(VerificationCheck(
        check="answer_text_references_only_cited_items",
        passed=referenced <= {c["item_id"] for c in citations}, expected=sorted(c["item_id"] for c in citations),
        observed=sorted(referenced),
    ))
    return checks


def verify_ranked_candidates(candidates: List[Dict[str, Any]], max_price: int, min_ram: int) -> Dict[str, Any]:
    checks = []
    for c in candidates:
        checks.append(VerificationCheck(
            check=f"{c['sku']}_satisfies_constraints",
            passed=c["price_inr"] <= max_price and c["ram_gb"] >= min_ram,
            expected={"max_price_inr": max_price, "min_ram_gb": min_ram},
            observed={"price_inr": c["price_inr"], "ram_gb": c["ram_gb"]},
        ))
    return _report(checks, "CONSTRAINT_RECHECK_ON_RANKED_CANDIDATES", VerificationVerdict.VERIFIED_SUCCESS)
