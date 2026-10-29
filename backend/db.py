"""SQLite persistence (WAL mode) for tasks, approvals, evidence, MCP sessions and MCP logs."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from backend import config
from backend.models import utc_now_iso

_lock = threading.RLock()
_initialized_path: Optional[str] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    goal TEXT NOT NULL,
    task_category TEXT NOT NULL CHECK (task_category IN ('READ_ANALYZE','RESEARCH_COMPARE','ACT_WITH_APPROVAL')),
    status TEXT NOT NULL CHECK (status IN ('PLANNED','EXECUTING','AWAITING_APPROVAL','AWAITING_USER_INPUT',
        'BLOCKED','COMPLETED','UNVERIFIED_OUTCOME','FAILED','CANCELLED')),
    environment_mode TEXT NOT NULL CHECK (environment_mode IN ('LIVE_WEB','CONTROLLED_SANDBOX','HYBRID')),
    interpreted_goal_json TEXT NOT NULL,
    plan_steps_json TEXT NOT NULL,
    extracted_data_json TEXT,
    pending_approval_json TEXT,
    verification_report_json TEXT,
    summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('PURCHASE_CHECKOUT','FORM_SUBMISSION','ACCOUNT_MUTATION')),
    action_summary TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING','USER_APPROVED','USER_REJECTED','CONSUMED','REVOKED','EXPIRED')),
    approval_token TEXT,
    preview_screenshot_url TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    page_title TEXT,
    capability_method TEXT NOT NULL,
    extracted_json TEXT,
    dom_citation_snippet TEXT,
    screenshot_url TEXT,
    firewall_alerts_json TEXT,
    verification_passed INTEGER NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_logs (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    method TEXT NOT NULL,
    tool_name TEXT,
    request_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp_sessions (
    session_id TEXT PRIMARY KEY,
    client_info_json TEXT,
    protocol_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS synthetic_tools (
    tool_name TEXT PRIMARY KEY,
    session_id TEXT,
    task_id TEXT,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_interrupts (
    interrupt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    instruction TEXT NOT NULL,
    delta_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','APPLIED','REJECTED')),
    outcome TEXT,
    created_at TEXT NOT NULL,
    applied_at TEXT
);

-- Accounts WebPilot may sign in with. Passwords are NOT stored here: they live in the OS credential store
-- (Windows Credential Manager / macOS Keychain / Secret Service) via `keyring`.
CREATE TABLE IF NOT EXISTS site_accounts (
    site TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('user','created')),
    verified INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS user_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_interrupts_task ON task_interrupts(task_id);
CREATE INDEX IF NOT EXISTS idx_synthetic_tools_session ON synthetic_tools(session_id);
CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
CREATE INDEX IF NOT EXISTS idx_evidence_task ON evidence_records(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
"""


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def init_db() -> None:
    """Create the schema (idempotent) and switch the database to WAL mode."""
    global _initialized_path
    with _lock:
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _initialized_path = str(config.DB_PATH)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    if _initialized_path != str(config.DB_PATH):
        init_db()
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Optional[str]) -> Any:
    if value is None or value == "":
        return None
    return json.loads(value)


# --------------------------------------------------------------------------- tasks

def _task_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "task_id": row["task_id"],
        "idempotency_key": row["idempotency_key"],
        "goal": row["goal"],
        "task_category": row["task_category"],
        "status": row["status"],
        "environment_mode": row["environment_mode"],
        "interpreted_goal": _loads(row["interpreted_goal_json"]) or {},
        "plan_steps": _loads(row["plan_steps_json"]) or [],
        "extracted_data": _loads(row["extracted_data_json"]),
        "pending_approval": _loads(row["pending_approval_json"]),
        "verification_report": _loads(row["verification_report_json"]),
        "summary": row["summary"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def insert_task(task: Dict[str, Any]) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tasks (task_id, idempotency_key, goal, task_category, status, environment_mode,
                   interpreted_goal_json, plan_steps_json, extracted_data_json, pending_approval_json,
                   verification_report_json, summary, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task["task_id"],
                task.get("idempotency_key"),
                task["goal"],
                task["task_category"],
                task["status"],
                task["environment_mode"],
                _dumps(task.get("interpreted_goal") or {}),
                _dumps(task.get("plan_steps") or []),
                _dumps(task.get("extracted_data")),
                _dumps(task.get("pending_approval")),
                _dumps(task.get("verification_report")),
                task.get("summary"),
                task.get("created_at") or now,
                now,
            ),
        )
    return get_task(task["task_id"])  # type: ignore[return-value]


_TASK_JSON_FIELDS = {
    "interpreted_goal": "interpreted_goal_json",
    "plan_steps": "plan_steps_json",
    "extracted_data": "extracted_data_json",
    "pending_approval": "pending_approval_json",
    "verification_report": "verification_report_json",
}
_TASK_PLAIN_FIELDS = {"status", "summary", "task_category", "environment_mode", "goal"}


def update_task(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    assignments: List[str] = []
    values: List[Any] = []
    for key, value in fields.items():
        if key in _TASK_JSON_FIELDS:
            assignments.append(f"{_TASK_JSON_FIELDS[key]} = ?")
            values.append(_dumps(value))
        elif key in _TASK_PLAIN_FIELDS:
            assignments.append(f"{key} = ?")
            values.append(value)
        else:
            raise ValueError(f"Unknown task field: {key}")
    assignments.append("updated_at = ?")
    values.append(utc_now_iso())
    values.append(task_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?", values)
    return get_task(task_id)


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    return _task_row_to_dict(row) if row else None


def get_task_by_idempotency_key(key: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE idempotency_key = ?", (key,)).fetchone()
    return _task_row_to_dict(row) if row else None


def list_tasks(limit: int = 100) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_task_row_to_dict(r) for r in rows]


# ----------------------------------------------------------------------- approvals

def _approval_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "approval_id": row["approval_id"],
        "task_id": row["task_id"],
        "step_id": row["step_id"],
        "action_type": row["action_type"],
        "action_summary": row["action_summary"],
        "parameters": _loads(row["parameters_json"]) or {},
        "parameter_hash": row["parameter_hash"],
        "status": row["status"],
        "approval_token": row["approval_token"],
        "preview_screenshot_url": row["preview_screenshot_url"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "consumed_at": row["consumed_at"],
    }


def insert_approval(approval: Dict[str, Any]) -> Dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO approvals (approval_id, task_id, step_id, action_type, action_summary,
                   parameters_json, parameter_hash, status, approval_token, preview_screenshot_url,
                   created_at, decided_at, consumed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                approval["approval_id"],
                approval["task_id"],
                approval["step_id"],
                approval["action_type"],
                approval["action_summary"],
                _dumps(approval["parameters"]),
                approval["parameter_hash"],
                approval.get("status", "PENDING"),
                approval.get("approval_token"),
                approval.get("preview_screenshot_url"),
                approval.get("created_at") or utc_now_iso(),
                approval.get("decided_at"),
                approval.get("consumed_at"),
            ),
        )
    return get_approval(approval["approval_id"])  # type: ignore[return-value]


def get_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
    return _approval_row_to_dict(row) if row else None


def list_approvals_for_task(task_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC", (task_id,)
        ).fetchall()
    return [_approval_row_to_dict(r) for r in rows]


def update_approval(approval_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {"status", "approval_token", "preview_screenshot_url", "decided_at", "consumed_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown approval fields: {sorted(unknown)}")
    if not fields:
        return get_approval(approval_id)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(f"UPDATE approvals SET {sets} WHERE approval_id = ?", (*fields.values(), approval_id))
    return get_approval(approval_id)


def transition_approval_status(
    approval_id: str, from_status: str, to_status: str, **fields: Any
) -> bool:
    """Atomic compare-and-set on approval status. Returns True if this call performed the transition."""
    allowed = {"approval_token", "decided_at", "consumed_at"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown approval fields: {sorted(unknown)}")
    sets = ["status = ?"] + [f"{k} = ?" for k in fields]
    values: List[Any] = [to_status, *fields.values(), approval_id, from_status]
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE approvals SET {', '.join(sets)} WHERE approval_id = ? AND status = ?", values
        )
        return cur.rowcount == 1


# ------------------------------------------------------------------------ evidence

def _evidence_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "evidence_id": row["evidence_id"],
        "task_id": row["task_id"],
        "step_id": row["step_id"],
        "source_url": row["source_url"],
        "page_title": row["page_title"],
        "capability_method": row["capability_method"],
        "extracted": _loads(row["extracted_json"]),
        "dom_citation_snippet": row["dom_citation_snippet"],
        "screenshot_url": row["screenshot_url"],
        "firewall_alerts": _loads(row["firewall_alerts_json"]) or [],
        "verification_passed": bool(row["verification_passed"]),
        "captured_at": row["captured_at"],
    }


def insert_evidence(evidence: Dict[str, Any]) -> Dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO evidence_records (evidence_id, task_id, step_id, source_url, page_title,
                   capability_method, extracted_json, dom_citation_snippet, screenshot_url,
                   firewall_alerts_json, verification_passed, captured_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                evidence["evidence_id"],
                evidence["task_id"],
                evidence["step_id"],
                evidence["source_url"],
                evidence.get("page_title"),
                evidence["capability_method"],
                _dumps(evidence.get("extracted")),
                evidence.get("dom_citation_snippet"),
                evidence.get("screenshot_url"),
                _dumps(evidence.get("firewall_alerts") or []),
                1 if evidence.get("verification_passed") else 0,
                evidence.get("captured_at") or utc_now_iso(),
            ),
        )
    return get_evidence(evidence["evidence_id"])  # type: ignore[return-value]


def get_evidence(evidence_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM evidence_records WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return _evidence_row_to_dict(row) if row else None


def set_evidence_verified(evidence_id: str, passed: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE evidence_records SET verification_passed = ? WHERE evidence_id = ?",
            (1 if passed else 0, evidence_id),
        )


def list_evidence_for_task(task_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_records WHERE task_id = ? ORDER BY captured_at ASC, rowid ASC", (task_id,)
        ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


# -------------------------------------------------------------------- MCP sessions

def create_session(session_id: str, client_info: Optional[Dict[str, Any]], protocol_version: str) -> None:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mcp_sessions (session_id, client_info_json, protocol_version, created_at, last_active_at)
               VALUES (?,?,?,?,?)""",
            (session_id, _dumps(client_info), protocol_version, now, now),
        )


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM mcp_sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return None
    return {
        "session_id": row["session_id"],
        "client_info": _loads(row["client_info_json"]),
        "protocol_version": row["protocol_version"],
        "created_at": row["created_at"],
        "last_active_at": row["last_active_at"],
    }


def touch_session(session_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE mcp_sessions SET last_active_at = ? WHERE session_id = ?", (utc_now_iso(), session_id)
        )


def delete_session(session_id: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM mcp_sessions WHERE session_id = ?", (session_id,))
        return cur.rowcount == 1


def count_sessions() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM mcp_sessions").fetchone()[0])


# ------------------------------------------------------------------------ MCP logs

def insert_mcp_log(
    session_id: Optional[str],
    method: str,
    tool_name: Optional[str],
    request: Any,
    response: Any,
    status_code: int,
    latency_ms: float,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mcp_logs (session_id, method, tool_name, request_json, response_json,
                   status_code, latency_ms, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                session_id,
                method,
                tool_name,
                _dumps(request) or "null",
                _dumps(response) or "null",
                status_code,
                round(latency_ms, 3),
                utc_now_iso(),
            ),
        )


def list_mcp_logs(limit: int = 50) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM mcp_logs ORDER BY log_id DESC LIMIT ?", (limit,)).fetchall()
    return [
        {
            "log_id": r["log_id"],
            "session_id": r["session_id"],
            "method": r["method"],
            "tool_name": r["tool_name"],
            "request": _loads(r["request_json"]),
            "response": _loads(r["response_json"]),
            "status_code": r["status_code"],
            "latency_ms": r["latency_ms"],
            "timestamp": r["timestamp"],
        }
        for r in rows
    ]


# ------------------------------------------------------------- security reporting

def list_firewall_alert_records(limit: int = 2000) -> List[Dict[str, Any]]:
    """Evidence rows that carry firewall alerts (for the MCP security report resource)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT task_id, step_id, source_url, captured_at, firewall_alerts_json FROM evidence_records
               WHERE firewall_alerts_json IS NOT NULL AND firewall_alerts_json != '[]'
               ORDER BY captured_at DESC LIMIT ?""", (limit,),
        ).fetchall()
    return [{"task_id": r["task_id"], "step_id": r["step_id"], "source_url": r["source_url"],
             "captured_at": r["captured_at"], "alerts": _loads(r["firewall_alerts_json"]) or []} for r in rows]


def approval_status_counts() -> Dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM approvals GROUP BY status").fetchall()
    return {r["status"]: int(r["n"]) for r in rows}


def tool_error_counts(tool_name: str, limit: int = 1000) -> Dict[str, int]:
    """Count tool-execution error codes (SEP-1303 isError results) for one tool from the MCP log."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT response_json FROM mcp_logs WHERE tool_name = ? ORDER BY log_id DESC LIMIT ?", (tool_name, limit),
        ).fetchall()
    counts: Dict[str, int] = {}
    for r in rows:
        resp = _loads(r["response_json"]) or {}
        result = (resp.get("result") or {}) if isinstance(resp, dict) else {}
        if result.get("isError"):
            code = (result.get("structuredContent") or {}).get("error_code", "UNKNOWN")
            counts[code] = counts.get(code, 0) + 1
    return counts


# ----------------------------------------------------------------- synthetic tools

def upsert_synthetic_tool(tool_name: str, session_id: Optional[str], task_id: Optional[str],
                          spec: Dict[str, Any], expires_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO synthetic_tools (tool_name, session_id, task_id, spec_json, created_at, expires_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(tool_name) DO UPDATE SET session_id=excluded.session_id, task_id=excluded.task_id,
                   spec_json=excluded.spec_json, expires_at=excluded.expires_at""",
            (tool_name, session_id, task_id, _dumps(spec), utc_now_iso(), expires_at),
        )


def _synthetic_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "tool_name": row["tool_name"],
        "session_id": row["session_id"],
        "task_id": row["task_id"],
        "spec": _loads(row["spec_json"]),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def get_synthetic_tool(tool_name: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM synthetic_tools WHERE tool_name = ?", (tool_name,)).fetchone()
    return _synthetic_row(row) if row else None


def list_synthetic_tools(session_id: Optional[str]) -> List[Dict[str, Any]]:
    """Unexpired tools registered for one MCP session (ISO timestamps compare lexicographically)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM synthetic_tools WHERE session_id IS ? AND expires_at > ? ORDER BY created_at ASC",
            (session_id, utc_now_iso()),
        ).fetchall()
    return [_synthetic_row(r) for r in rows]


# ------------------------------------------------------------------ task interrupts

def _interrupt_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "interrupt_id": row["interrupt_id"],
        "task_id": row["task_id"],
        "instruction": row["instruction"],
        "delta": _loads(row["delta_json"]) or {},
        "changes": _loads(row["changes_json"]) or [],
        "status": row["status"],
        "outcome": row["outcome"],
        "created_at": row["created_at"],
        "applied_at": row["applied_at"],
    }


def insert_interrupt(interrupt_id: str, task_id: str, instruction: str, delta: Dict[str, Any],
                     changes: List[str]) -> Dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO task_interrupts (interrupt_id, task_id, instruction, delta_json, changes_json, status, created_at)
               VALUES (?,?,?,?,?,'QUEUED',?)""",
            (interrupt_id, task_id, instruction, _dumps(delta), _dumps(changes), utc_now_iso()),
        )
    return get_interrupt(interrupt_id)  # type: ignore[return-value]


def get_interrupt(interrupt_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM task_interrupts WHERE interrupt_id = ?", (interrupt_id,)).fetchone()
    return _interrupt_row(row) if row else None


def list_interrupts(task_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM task_interrupts WHERE task_id = ?"
    args: List[Any] = [task_id]
    if status:
        query += " AND status = ?"
        args.append(status)
    with get_conn() as conn:
        rows = conn.execute(query + " ORDER BY created_at ASC, rowid ASC", args).fetchall()
    return [_interrupt_row(r) for r in rows]


def resolve_interrupt(interrupt_id: str, status: str, outcome: str) -> bool:
    """QUEUED -> APPLIED/REJECTED, exactly once."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE task_interrupts SET status = ?, outcome = ?, applied_at = ? WHERE interrupt_id = ? AND status = 'QUEUED'",
            (status, outcome, utc_now_iso(), interrupt_id),
        )
        return cur.rowcount == 1


# --------------------------------------------------------------------------- reset

def reset_demo_data(include_mcp_logs: bool = True, include_sessions: bool = False) -> Dict[str, int]:
    with get_conn() as conn:
        counts = {
            "tasks": int(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]),
            "approvals": int(conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]),
            "evidence_records": int(conn.execute("SELECT COUNT(*) FROM evidence_records").fetchone()[0]),
            "mcp_logs": 0,
            "mcp_sessions": 0,
        }
        counts["synthetic_tools"] = int(conn.execute("SELECT COUNT(*) FROM synthetic_tools").fetchone()[0])
        counts["task_interrupts"] = int(conn.execute("SELECT COUNT(*) FROM task_interrupts").fetchone()[0])
        conn.execute("DELETE FROM task_interrupts")
        conn.execute("DELETE FROM synthetic_tools")
        conn.execute("DELETE FROM evidence_records")
        conn.execute("DELETE FROM approvals")
        conn.execute("DELETE FROM tasks")
        if include_mcp_logs:
            counts["mcp_logs"] = int(conn.execute("SELECT COUNT(*) FROM mcp_logs").fetchone()[0])
            conn.execute("DELETE FROM mcp_logs")
        if include_sessions:
            counts["mcp_sessions"] = int(conn.execute("SELECT COUNT(*) FROM mcp_sessions").fetchone()[0])
            conn.execute("DELETE FROM mcp_sessions")
    return counts


# --------------------------------------------------------------------------- site accounts / settings

def upsert_site_account(site: str, username: str, source: str, verified: bool) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO site_accounts (site, username, source, verified, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(site) DO UPDATE SET username=excluded.username, source=excluded.source, verified=excluded.verified",
            (site, username, source, 1 if verified else 0, now))
    return get_site_account(site)  # type: ignore[return-value]


def get_site_account(site: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM site_accounts WHERE site = ?", (site,)).fetchone()
    return {**dict(row), "verified": bool(row["verified"])} if row else None


def list_site_accounts() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM site_accounts ORDER BY site").fetchall()
    return [{**dict(r), "verified": bool(r["verified"])} for r in rows]


def touch_site_account(site: str, verified: Optional[bool] = None) -> None:
    with get_conn() as conn:
        if verified is None:
            conn.execute("UPDATE site_accounts SET last_used_at = ? WHERE site = ?", (utc_now_iso(), site))
        else:
            conn.execute("UPDATE site_accounts SET last_used_at = ?, verified = ? WHERE site = ?",
                         (utc_now_iso(), 1 if verified else 0, site))


def delete_site_account(site: str) -> bool:
    with get_conn() as conn:
        return conn.execute("DELETE FROM site_accounts WHERE site = ?", (site,)).rowcount > 0


def get_setting(key: str) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM user_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: Optional[str]) -> None:
    with get_conn() as conn:
        if value is None or value == "":
            conn.execute("DELETE FROM user_settings WHERE key = ?", (key,))
        else:
            conn.execute("INSERT INTO user_settings (key, value, updated_at) VALUES (?,?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                         (key, value, utc_now_iso()))


def clear_evidence_screenshots(task_ids: List[str]) -> int:
    """Forget screenshot URLs once the files are deleted (the evidence text and citations are kept)."""
    if not task_ids:
        return 0
    with get_conn() as conn:
        marks = ",".join("?" for _ in task_ids)
        return conn.execute(f"UPDATE evidence_records SET screenshot_url = NULL WHERE task_id IN ({marks})", task_ids).rowcount
