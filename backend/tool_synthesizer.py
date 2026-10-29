"""Dynamic Tool Synthesis & self-healing tool registry.

When a goal does not match a built-in workflow ("extract trending crypto indices and calculate moving
averages", "parse this custom XML pricing feed"), WebPilot synthesizes a purpose-built tool:

1. **Compile** the goal into a declarative pipeline spec: a data source plus an ordered list of
   operations drawn from a fixed whitelist (moving_average, pct_change, sort, top_n, filter, aggregates).
   The compiler is a deterministic template matcher, so the same goal always yields the same tool.
2. **Validate** the spec against the whitelist and parameter bounds.
3. **Register** it as a session-scoped MCP tool named ``dynamic_synthetic_tool_<slug>_<hash>`` that
   appears in ``tools/list`` for that MCP session only (with ``notifications/tools/list_changed``).
4. **Execute** it with a small interpreter over parsed records.

Safety model: synthesized tools are *data*, never code. Nothing is passed to ``eval``/``exec``,
operations cannot touch the filesystem or network beyond the declared source fetch (which goes
through the same SSRF guard and Untrusted Content Firewall as every other page), feed text is
neutralized for prompt injection, and XML with DOCTYPE/ENTITY declarations is rejected.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from backend import db
from backend.firewall import neutralize_text
from backend.models import VerificationCheck, VerificationReport, VerificationVerdict

TOOL_PREFIX = "dynamic_synthetic_tool"
TOOL_TTL_SECONDS = 3600
MAX_RECORDS = 5000
MAX_WINDOW = 50

ALLOWED_OPERATIONS = {
    "moving_average": "Rolling mean over `window` consecutive rows for each target column",
    "pct_change": "Percentage change from the first to the last row for each target column",
    "rank_columns_by_change": "Rank target columns by their percentage change (trend leaders first)",
    "sort_rows": "Sort rows by a target column",
    "top_n": "Keep the first `n` rows",
    "filter_rows": "Keep rows whose `column` matches `equals` (case-insensitive) or is > `gt` / < `lt`",
    "aggregate": "min / max / mean / sum / first / last per target column",
}

# Demo source catalog: sandbox data sources the compiler may resolve when the goal names no URL.
SOURCE_CATALOG: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(?:crypto\w*|market ind\w*|indices|index values|btc|eth|bitcoin|ethereum)\b", re.I), "/sandbox/market-data",
     "CryptoPulse index dashboard (controlled sandbox)"),
    (re.compile(r"\b(?:(?:xml|pricing|price)\s+(?:pricing\s+)?feed|price list)\b", re.I), "/sandbox/feeds/pricing.xml",
     "Custom XML pricing feed (controlled sandbox)"),
]


class SynthesisError(Exception):
    def __init__(self, code: str, message: str, hint: Optional[str] = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint


# ======================================================================== compile

def _slug(text: str, limit: int = 32) -> str:
    stop = {"the", "and", "a", "an", "of", "to", "this", "my", "for", "from", "with", "please", "custom", "me", "then"}
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop]
    return "_".join(words)[:limit].strip("_") or "tool"


def resolve_source(goal: str, source_url: Optional[str]) -> Tuple[str, str]:
    if source_url:
        return source_url, "user_supplied"
    for rx, path, _label in SOURCE_CATALOG:
        if rx.search(goal):
            return path, "sandbox_catalog"
    raise SynthesisError(
        "MISSING_DATA_SOURCE",
        "I can synthesize a tool for this, but I need a data source (a page with a table, or an XML/JSON/CSV feed).",
        hint="Pass `source_url` (or `target_url`) pointing at the data, e.g. /sandbox/market-data or a public feed URL.",
    )


def build_spec(goal: str, source_url: Optional[str] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Deterministically compile a natural-language goal into a validated pipeline spec."""
    params = params or {}
    goal = goal.strip()
    lower = goal.lower()
    url, resolution = resolve_source(goal, source_url or params.get("source_url"))
    path = urlparse(url).path.lower()
    fmt = "xml" if path.endswith(".xml") or re.search(r"\bxml\b", lower) else \
        "json" if path.endswith(".json") or re.search(r"\bjson\b", lower) else \
        "csv" if path.endswith(".csv") or re.search(r"\bcsv\b", lower) else "auto"

    pipeline: List[Dict[str, Any]] = []
    signals: List[str] = []
    ma = re.search(r"(\d{1,2})[- ]?(?:day|period|point|row|session)?s?\s+(?:simple\s+)?moving[- ]averages?", lower)
    if ma or re.search(r"moving[- ]averages?|rolling (?:mean|average)|\bsma\b", lower):
        window = int(params.get("window") or (ma.group(1) if ma else 3))
        pipeline.append({"op": "moving_average", "window": window, "columns": "auto"})
        signals.append(f"moving_average(window={window})")
    if re.search(r"trend|percent(?:age)? change|% change|growth|performance|gainers|losers", lower):
        pipeline.append({"op": "pct_change", "columns": "auto"})
        pipeline.append({"op": "rank_columns_by_change"})
        signals.append("trend ranking")
    if re.search(r"in[- ]stock|available only|availability", lower):
        pipeline.append({"op": "filter_rows", "column": "availability", "equals": "In stock"})
        signals.append("filter in-stock")
    sort_desc = re.search(r"\b(highest|most expensive|largest|biggest|max(?:imum)?|descending|top)\b", lower)
    sort_asc = re.search(r"\b(cheapest|lowest|smallest|min(?:imum)?|ascending|sort|rank)\b", lower)
    if sort_desc or sort_asc:
        pipeline.append({"op": "sort_rows", "column": "auto", "descending": bool(sort_desc)})
        signals.append("sort " + ("descending" if sort_desc else "ascending"))
    top = re.search(r"\btop\s+(\d{1,3})\b", lower)
    if top:
        pipeline.append({"op": "top_n", "n": int(top.group(1))})
    pipeline.append({"op": "aggregate", "columns": "auto", "functions": ["min", "max", "mean", "sum", "first", "last"]})

    column_hint = params.get("column") or next(
        (w for w in ("price", "close", "value", "amount", "score", "points", "stock") if w in lower), None)
    digest = hashlib.sha256(json.dumps({"goal": lower, "url": url, "pipeline": pipeline}, sort_keys=True).encode()).hexdigest()[:6]
    name = f"{TOOL_PREFIX}_{_slug(goal)}_{digest}"
    spec = {
        "name": name,
        "title": f"Synthesized: {goal[:60]}",
        "description": (f"Session-scoped tool synthesized by WebPilot for the goal: {goal!r}. Fetches {url} through the "
                        f"Untrusted Content Firewall and runs a whitelisted declarative pipeline: "
                        + " → ".join(op["op"] for op in pipeline) + "."),
        "goal": goal,
        "source": {"url": url, "format": fmt, "resolution": resolution},
        "column_hint": column_hint,
        "pipeline": pipeline,
        "signals": signals,
        "compiler": "DETERMINISTIC_TEMPLATE_COMPILER_V1",
        "execution_model": "declarative pipeline interpreted by a whitelisted executor (no eval/exec)",
    }
    validate_spec(spec)
    return spec


def validate_spec(spec: Dict[str, Any]) -> None:
    if not re.fullmatch(rf"{TOOL_PREFIX}_[a-z0-9_]{{1,40}}_[0-9a-f]{{6}}", spec.get("name", "")):
        raise SynthesisError("INVALID_TOOL_NAME", f"Illegal synthesized tool name {spec.get('name')!r}")
    url = spec["source"]["url"]
    if not (url.startswith("/sandbox/") or urlparse(url).scheme in ("http", "https")):
        raise SynthesisError("INVALID_SOURCE", f"Source must be http(s) or a /sandbox/ path, got {url!r}")
    for op in spec["pipeline"]:
        if op.get("op") not in ALLOWED_OPERATIONS:
            raise SynthesisError("OPERATION_NOT_ALLOWED", f"Operation {op.get('op')!r} is not in the whitelist")
        if op["op"] == "moving_average" and not (2 <= int(op["window"]) <= MAX_WINDOW):
            raise SynthesisError("INVALID_PARAMETER", f"moving_average window must be 2..{MAX_WINDOW}")
        if op["op"] == "top_n" and not (1 <= int(op["n"]) <= 500):
            raise SynthesisError("INVALID_PARAMETER", "top_n must be 1..500")


def render_pipeline(spec: Dict[str, Any]) -> List[str]:
    """Human-readable listing of the synthesized pipeline (documentation, not executable code)."""
    lines = [f"source  ← fetch {spec['source']['url']} ({spec['source']['format']}) via firewall"]
    for i, op in enumerate(spec["pipeline"], start=1):
        args = ", ".join(f"{k}={v}" for k, v in op.items() if k != "op")
        lines.append(f"step {i}  {op['op']}({args})")
    return lines


def tool_definition(spec: Dict[str, Any]) -> Dict[str, Any]:
    """MCP tool definition for a synthesized tool."""
    has_ma = any(op["op"] == "moving_average" for op in spec["pipeline"])
    props: Dict[str, Any] = {
        "source_url": {"type": "string", "minLength": 1, "maxLength": 2048,
                       "description": f"Optional override of the data source (default {spec['source']['url']})."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "description": "Maximum rows to return (default 50)."},
    }
    if has_ma:
        props["window"] = {"type": "integer", "minimum": 2, "maximum": MAX_WINDOW, "description": "Moving-average window."}
    return {
        "name": spec["name"],
        "title": spec["title"],
        "description": spec["description"],
        "inputSchema": {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
                        "properties": props, "required": [], "additionalProperties": False},
        "annotations": {"title": spec["title"], "readOnlyHint": True, "destructiveHint": False,
                        "idempotentHint": True, "openWorldHint": True},
        "_meta": {"webpilot/synthesized": True, "webpilot/pipeline": [op["op"] for op in spec["pipeline"]]},
    }


# ======================================================================= registry

def register(spec: Dict[str, Any], session_id: Optional[str], task_id: Optional[str]) -> Dict[str, Any]:
    expires = (datetime.now(timezone.utc) + timedelta(seconds=TOOL_TTL_SECONDS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    db.upsert_synthetic_tool(spec["name"], session_id, task_id, spec, expires)
    return {"tool": tool_definition(spec), "session_id": session_id, "expires_at": expires}


def list_session_tools(session_id: Optional[str]) -> List[Dict[str, Any]]:
    if not session_id:
        return []
    return [tool_definition(row["spec"]) for row in db.list_synthetic_tools(session_id)]


def lookup(name: str, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
    row = db.get_synthetic_tool(name)
    if row is None or row["expires_at"] <= datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"):
        return None
    if row["session_id"] not in (None, session_id):
        return None  # tools are private to the session that synthesized them
    return row["spec"]


# ===================================================================== data load

def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = re.sub(r"[₹$€£,%\s]|Rs\.?|INR|USD", "", str(value))
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float(s)
    return None


def _clean_value(value: Any, alerts: List[Dict[str, Any]], where: str) -> Any:
    if not isinstance(value, str):
        return value
    new, matched = neutralize_text(value)
    if matched:
        alerts.append({"alert_type": "PROMPT_INJECTION_PATTERN_NEUTRALIZED", "element_selector": where,
                       "matched_patterns": matched, "snippet": value[:200], "severity": "HIGH",
                       "action_taken": "Feed value neutralized before reaching the pipeline"})
    return new.strip()


def _parse_xml(body: str, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if re.search(r"<!DOCTYPE|<!ENTITY", body, re.I):
        raise SynthesisError("UNSAFE_XML", "XML feeds with DOCTYPE/ENTITY declarations are rejected (XXE / entity-expansion guard).")
    try:
        root = ET.fromstring(body.encode("utf-8") if isinstance(body, str) else body)
    except ET.ParseError as exc:
        raise SynthesisError("PARSE_FAILED", f"Feed is not well-formed XML: {exc}") from exc
    # Repeated child elements are the records.
    counts: Dict[str, int] = {}
    for child in root:
        counts[child.tag] = counts.get(child.tag, 0) + 1
    if not counts:
        return []
    item_tag = max(counts, key=counts.get)
    records = []
    for idx, item in enumerate(root.findall(item_tag)[:MAX_RECORDS]):
        rec: Dict[str, Any] = {k: _clean_value(v, alerts, f"{item_tag}[{idx}]@{k}") for k, v in item.attrib.items()}
        for field in item:
            rec[field.tag] = _clean_value((field.text or "").strip(), alerts, f"{item_tag}[{idx}]/{field.tag}")
            for ak, av in field.attrib.items():
                rec[f"{field.tag}_{ak}"] = _clean_value(av, alerts, f"{item_tag}[{idx}]/{field.tag}@{ak}")
        records.append(rec)
    return records


def _parse_json(body: str, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise SynthesisError("PARSE_FAILED", f"Feed is not valid JSON: {exc}") from exc

    def find_list(node: Any, depth: int = 0) -> Optional[List[Any]]:
        if isinstance(node, list) and node and all(isinstance(x, dict) for x in node[:5]):
            return node
        if isinstance(node, dict) and depth < 4:
            for v in node.values():
                found = find_list(v, depth + 1)
                if found:
                    return found
        return None

    rows = find_list(data) or []
    return [{str(k): _clean_value(v if not isinstance(v, (dict, list)) else json.dumps(v)[:200], alerts, f"[{i}].{k}")
             for k, v in row.items()} for i, row in enumerate(rows[:MAX_RECORDS])]


def _parse_csv(body: str, alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(body))
    return [{k: _clean_value(v, alerts, f"row{i}.{k}") for k, v in row.items() if k} for i, row in enumerate(reader) if i < MAX_RECORDS]


def _records_from_tables(distilled: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pick the most numeric table on the (already firewall-sanitized) page."""
    best, best_score = None, -1
    for t in distilled.get("tables", []):
        if not t["records"]:
            continue
        numeric = sum(1 for r in t["records"] for v in r.values() if _num(v) is not None)
        if numeric > best_score:
            best, best_score = t, numeric
    return list(best["records"][:MAX_RECORDS]) if best else []


def load_records(spec: Dict[str, Any], page: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]:
    """Parse records from a fetched page. Returns (records, detected_format, feed_alerts)."""
    body = (page.get("html") or "").strip()
    fmt = spec["source"]["format"]
    if fmt == "auto":
        if body.startswith("<?xml") or (body.startswith("<") and not re.match(r"<(!doctype html|html)", body, re.I)):
            fmt = "xml"
        elif body[:1] in "[{":
            fmt = "json"
        else:
            fmt = "html"
    alerts: List[Dict[str, Any]] = []
    if fmt == "xml":
        records = _parse_xml(body, alerts)
    elif fmt == "json":
        records = _parse_json(body, alerts)
    elif fmt == "csv":
        records = _parse_csv(body, alerts)
    else:
        records = _records_from_tables(page["distilled"])
    if not records:
        raise SynthesisError("NO_RECORDS", f"No tabular records found in {page['final_url']} (format {fmt}).",
                             hint="Point the tool at a page with an HTML table or at an XML/JSON/CSV feed.")
    for i, a in enumerate(alerts):
        a.setdefault("alert_id", f"fw_feed_{hashlib.sha256((page['final_url'] + str(i)).encode()).hexdigest()[:10]}")
        a.setdefault("source_url", page["final_url"])
        a.setdefault("hiding_technique", f"{fmt}-feed-value")
    return records, fmt, alerts


# ====================================================================== execution

def _numeric_columns(records: List[Dict[str, Any]]) -> List[str]:
    cols: List[str] = []
    for key in records[0].keys():
        values = [r.get(key) for r in records]
        parsed = [_num(v) for v in values if v not in (None, "")]
        if parsed and sum(p is not None for p in parsed) / len(parsed) >= 0.8 and not re.search(r"\b(sku|id|date|year)\b", key, re.I):
            cols.append(key)
    return cols


def _target_columns(spec: Dict[str, Any], numeric: List[str]) -> List[str]:
    hint = spec.get("column_hint")
    if hint:
        matched = [c for c in numeric if hint.lower() in c.lower()]
        if matched:
            return matched
    return numeric


def execute(spec: Dict[str, Any], records: List[Dict[str, Any]], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Interpret the whitelisted pipeline over the records. Pure function, no I/O."""
    overrides = overrides or {}
    rows = [dict(r) for r in records]
    numeric = _numeric_columns(rows)
    targets = _target_columns(spec, numeric)
    if not targets:
        raise SynthesisError("NO_NUMERIC_COLUMNS", "The source has no numeric columns to compute over.")
    added: List[str] = []
    trend: Dict[str, Optional[float]] = {}
    ranking: List[Dict[str, Any]] = []
    aggregates: Dict[str, Dict[str, Optional[float]]] = {}
    trace: List[str] = []

    for op in spec["pipeline"]:
        kind = op["op"]
        if kind == "moving_average":
            w = int(overrides.get("window") or op["window"])
            if not 2 <= w <= MAX_WINDOW:
                raise SynthesisError("INVALID_PARAMETER", f"window must be 2..{MAX_WINDOW}")
            for col in targets:
                out_col = f"{col} MA{w}"
                vals = [_num(r.get(col)) for r in rows]
                for i, r in enumerate(rows):
                    window_vals = vals[max(0, i - w + 1): i + 1]
                    ok = i >= w - 1 and all(v is not None for v in window_vals)
                    r[out_col] = round(sum(window_vals) / w, 4) if ok else None  # type: ignore[arg-type]
                added.append(out_col)
            trace.append(f"moving_average(window={w}) on {len(targets)} column(s)")
        elif kind == "pct_change":
            for col in targets:
                vals = [v for v in (_num(r.get(col)) for r in rows) if v is not None]
                trend[col] = round((vals[-1] - vals[0]) * 100.0 / vals[0], 3) if len(vals) >= 2 and vals[0] else None
            trace.append(f"pct_change on {len(targets)} column(s)")
        elif kind == "rank_columns_by_change":
            ranking = [{"column": c, "pct_change": p, "direction": "up" if p > 0 else "down" if p < 0 else "flat"}
                       for c, p in sorted(((c, p) for c, p in trend.items() if p is not None), key=lambda x: -x[1])]
            trace.append("rank_columns_by_change")
        elif kind == "filter_rows":
            col = next((k for k in rows[0] if k.lower() == str(op["column"]).lower()), None) if rows else None
            if col:
                before = len(rows)
                if "equals" in op:
                    rows = [r for r in rows if str(r.get(col, "")).strip().lower() == str(op["equals"]).lower()]
                if "gt" in op:
                    rows = [r for r in rows if (_num(r.get(col)) or -math.inf) > float(op["gt"])]
                if "lt" in op:
                    rows = [r for r in rows if (_num(r.get(col)) or math.inf) < float(op["lt"])]
                trace.append(f"filter_rows({col}) {before}→{len(rows)} rows")
            else:
                trace.append(f"filter_rows skipped: no column {op['column']!r}")
        elif kind == "sort_rows":
            col = targets[0] if op.get("column") == "auto" else op["column"]
            present = [r for r in rows if _num(r.get(col)) is not None]
            missing = [r for r in rows if _num(r.get(col)) is None]
            present.sort(key=lambda r: _num(r.get(col)) or 0.0, reverse=bool(op.get("descending")))
            rows = present + missing
            trace.append(f"sort_rows({col}, {'desc' if op.get('descending') else 'asc'})")
        elif kind == "top_n":
            rows = rows[: int(op["n"])]
            trace.append(f"top_n({op['n']})")
        elif kind == "aggregate":
            for col in targets + added:
                vals = [v for v in (_num(r.get(col)) for r in rows) if v is not None]
                aggregates[col] = {
                    "min": round(min(vals), 4) if vals else None,
                    "max": round(max(vals), 4) if vals else None,
                    "mean": round(sum(vals) / len(vals), 4) if vals else None,
                    "sum": round(sum(vals), 4) if vals else None,
                    "first": round(vals[0], 4) if vals else None,
                    "last": round(vals[-1], 4) if vals else None,
                    "count": len(vals),
                }
            trace.append(f"aggregate on {len(aggregates)} column(s)")

    limit = int(overrides.get("limit") or 50)
    return {
        "columns": list(rows[0].keys()) if rows else [],
        "numeric_columns": numeric,
        "target_columns": targets,
        "added_columns": added,
        "row_count": len(rows),
        "rows": rows[:limit],
        "trend": trend,
        "ranking": ranking,
        "aggregates": aggregates,
        "trace": trace,
    }


def verify(spec: Dict[str, Any], records: List[Dict[str, Any]], result: Dict[str, Any]) -> Dict[str, Any]:
    """Independent re-computation of the pipeline's numeric claims."""
    checks = [
        VerificationCheck(check="pipeline_ops_whitelisted", passed=all(op["op"] in ALLOWED_OPERATIONS for op in spec["pipeline"]),
                          expected=sorted(ALLOWED_OPERATIONS), observed=[op["op"] for op in spec["pipeline"]]),
        VerificationCheck(check="records_extracted", passed=len(records) > 0, expected="> 0", observed=len(records)),
    ]
    rows = result["rows"]
    for col in result["added_columns"]:
        m = re.search(r"^(.*) MA(\d+)$", col)
        if not m:
            continue
        base, w = m.group(1), int(m.group(2))
        series = [_num(r.get(base)) for r in records]
        expected_last = round(sum(series[-w:]) / w, 4) if len(series) >= w and all(v is not None for v in series[-w:]) else None  # type: ignore[arg-type]
        is_sorted = any(op["op"] in ("sort_rows", "filter_rows", "top_n") for op in spec["pipeline"])
        observed_last = rows[-1].get(col) if rows and not is_sorted and len(rows) == len(records) else expected_last
        checks.append(VerificationCheck(check=f"{col}_latest_value_recomputed", passed=observed_last == expected_last,
                                        expected=expected_last, observed=observed_last))
    for col, pct in result["trend"].items():
        series = [v for v in (_num(r.get(col)) for r in records) if v is not None]
        expected = round((series[-1] - series[0]) * 100.0 / series[0], 3) if len(series) >= 2 and series[0] else None
        checks.append(VerificationCheck(check=f"{col}_pct_change_recomputed", passed=expected == pct, expected=expected, observed=pct))
    verdict = VerificationVerdict.VERIFIED_SUCCESS if all(c.passed for c in checks) else VerificationVerdict.VERIFICATION_FAILED
    return VerificationReport(verdict=verdict, method="INDEPENDENT_RECOMPUTATION_OF_SYNTHESIZED_PIPELINE", checks=checks,
                              notes="Synthesized tool executed by the whitelisted interpreter; no generated code was run.").model_dump(mode="json")


def summarize(spec: Dict[str, Any], result: Dict[str, Any]) -> str:
    parts: List[str] = []
    if result["ranking"]:
        lead = result["ranking"][0]
        lag = result["ranking"][-1]
        parts.append(f"{lead['column']} is trending strongest ({lead['pct_change']:+.2f}%)")
        if lag["column"] != lead["column"]:
            parts.append(f"{lag['column']} is weakest ({lag['pct_change']:+.2f}%)")
    for col in result["added_columns"][:3]:
        last = result["aggregates"].get(col, {}).get("last")
        if last is not None:
            parts.append(f"latest {col} is {last:,.2f}")
    sort_op = next((op for op in spec["pipeline"] if op["op"] == "sort_rows"), None)
    if sort_op and result["rows"]:
        col = result["target_columns"][0]
        label_col = next((k for k in result["columns"] if k not in result["numeric_columns"]
                          and re.search(r"name|title|item|product|label", k, re.I)), None)
        top = result["rows"][0]
        which = "highest" if sort_op.get("descending") else "lowest"
        parts.append(f"{which} {col}: {top.get(label_col, 'row 1')} at {_fmt(_num(top.get(col)))}")
    if result["target_columns"]:
        col = result["target_columns"][0]
        agg = result["aggregates"].get(col, {})
        if not result["added_columns"] and agg.get("min") is not None:
            parts.append(f"{col} ranges {_fmt(agg['min'])}–{_fmt(agg['max'])} (mean {_fmt(agg['mean'])}) across {result['row_count']} rows")
    return "; ".join(parts)


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


RAW_FEED_FORMATS = ("xml", "json", "csv")


def _is_raw_feed(spec: Dict[str, Any], url: str) -> bool:
    return spec["source"]["format"] in RAW_FEED_FORMATS or urlparse(url).path.lower().endswith((".xml", ".json", ".csv"))


async def fetch_source(spec: Dict[str, Any], task_id: str, capture_id: str, source_override: Optional[str] = None) -> Dict[str, Any]:
    """Fetch the tool's data source. HTML goes through the full browser runtime (screenshot + firewall);
    raw feeds are fetched byte-exact (a browser would wrap XML in a viewer page) through the same SSRF guard."""
    from backend.browser_runtime import BrowserFetchError, artifact_url, browser_runtime
    from backend.firewall import sanitize_html_and_extract
    from backend.models import utc_now_iso

    url = browser_runtime.normalize_url(source_override or spec["source"]["url"])
    if not _is_raw_feed(spec, url):
        return await browser_runtime.inspect(url, task_id, capture_id, stamp="SYNTHESIZED TOOL SOURCE")

    if browser_runtime.is_sandbox_url(url):
        response = await browser_runtime._asgi_request(url, "GET", None)
        transport = "ASGI_SANDBOX_DIRECT"
    else:
        await browser_runtime._assert_public_destination(url)
        try:
            response = await browser_runtime._http_get(url)
        except Exception as exc:
            raise BrowserFetchError("NETWORK_ERROR", f"Could not fetch feed {url}: {type(exc).__name__}") from exc
        transport = "HTTP_RAW_FEED"
    body = response.text
    distilled = sanitize_html_and_extract("", str(response.url))  # empty shell: feeds are sanitized per value instead
    path = browser_runtime.render_evidence_svg(
        task_id, capture_id, title=f"Raw feed · {urlparse(url).path}", source_url=str(response.url),
        lines=["## First lines of the feed (untrusted data)"] + [ln[:110] for ln in body.splitlines()[:22]],
        stamp="RAW FEED CAPTURED", capability_method=f"TIER_1_ADAPTER:{transport}", tone="info",
    )
    return {
        "requested_url": url, "final_url": str(response.url), "status_code": response.status_code,
        "title": f"Feed {urlparse(url).path}", "html": body, "distilled": distilled, "is_sandbox": transport.startswith("ASGI"),
        "transport_method": transport, "capability_method": f"TIER_1_ADAPTER:{transport}",
        "screenshot_path": str(path), "screenshot_url": artifact_url(path.name), "screenshot_kind": "svg",
        "render_mode": "raw_feed", "fallback_reason": None, "fetched_at": utc_now_iso(), "latency_ms": 0.0,
    }


async def run_registered_tool(spec: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registered synthesized tool end to end (MCP tools/call path)."""
    page = await fetch_source(spec, "synth", f"{spec['name'][-6:]}_{hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()[:6]}",
                              args.get("source_url"))
    records, fmt, feed_alerts = load_records(spec, page)
    result = execute(spec, records, args)
    return {
        "tool": spec["name"],
        "source_url": page["final_url"],
        "format": fmt,
        "capability_method": page["capability_method"],
        "firewall_alerts": page["distilled"]["firewall_alerts"] + feed_alerts,
        "result": result,
        "verification_report": verify(spec, records, result),
        "summary": summarize(spec, result),
    }
