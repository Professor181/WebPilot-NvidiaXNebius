"""Backend-enforced cryptographic approval gate for consequential actions.

Flow
----
1. The planner reaches a consequential step (purchase, form submission, account mutation) and
   calls :func:`create_approval_request`. The exact parameters that will be executed are locked
   with a canonical SHA-256 ``parameter_hash`` and the task halts in ``AWAITING_APPROVAL``.
2. Only the human decision endpoint (``POST /api/approvals/{id}/decide``) can call
   :func:`decide_approval`, which mints an HMAC-SHA256 ``approval_token`` bound to
   ``approval_id`` + ``parameter_hash``. The agent (LLM) has no way to mint one.
3. ``execute_approved_step`` must present that token. :func:`verify_and_consume_approval` checks
   status, token, expiry and live-page drift, then atomically marks the record ``CONSUMED`` so the
   action executes at most once.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from backend import config, db
from backend.models import ActionType, ApprovalStatus, utc_now_iso

TOKEN_PREFIX = "wp1."


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        return value.strip()
    return value


def canonical_json(params: Dict[str, Any]) -> str:
    return json.dumps(_canonicalize(params), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_parameter_hash(params: Dict[str, Any]) -> str:
    """Canonical-JSON SHA-256 hash of the locked action parameters."""
    return hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()


def generate_approval_token(approval_id: str, parameter_hash: str) -> str:
    """HMAC-SHA256 token binding a human approval to one approval record and one parameter set."""
    message = f"{approval_id}.{parameter_hash}".encode("utf-8")
    digest = hmac.new(config.WEBPILOT_APPROVAL_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return TOKEN_PREFIX + digest


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_expired(reference_ts: Optional[str]) -> bool:
    ts = _parse_ts(reference_ts)
    if ts is None:
        return False
    return datetime.now(timezone.utc) - ts > timedelta(seconds=config.WEBPILOT_APPROVAL_TTL_SECONDS)


def _param_diff(locked: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    locked_c, live_c = _canonicalize(locked), _canonicalize(live)
    diff = {}
    for key in sorted(set(locked_c) | set(live_c)):
        if locked_c.get(key) != live_c.get(key):
            diff[key] = {"approved": locked_c.get(key), "live": live_c.get(key)}
    return diff


def public_view(approval: Dict[str, Any], include_token: bool = False) -> Dict[str, Any]:
    """Approval record as exposed to clients. Tokens only appear once a human has approved."""
    view = {k: v for k, v in approval.items() if k != "approval_token"}
    view["approval_url"] = f"{config.WEBPILOT_FRONTEND_URL}/approvals/{approval['approval_id']}"
    view["decision_endpoint"] = f"{config.WEBPILOT_BASE_URL}/api/approvals/{approval['approval_id']}/decide"
    # PENDING approvals expire relative to creation, approved-but-unused ones relative to the decision.
    status = approval["status"]
    reference = approval["created_at"] if status == ApprovalStatus.PENDING.value else (
        approval["decided_at"] if status == ApprovalStatus.USER_APPROVED.value else None)
    ref_ts = _parse_ts(reference)
    view["ttl_seconds"] = config.WEBPILOT_APPROVAL_TTL_SECONDS
    view["expires_at"] = (
        (ref_ts + timedelta(seconds=config.WEBPILOT_APPROVAL_TTL_SECONDS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        if ref_ts else None
    )
    if include_token and approval["status"] in (ApprovalStatus.USER_APPROVED.value, ApprovalStatus.CONSUMED.value):
        view["approval_token"] = approval.get("approval_token")
    return view


def create_approval_request(
    task_id: str,
    step_id: str,
    action_type: ActionType,
    action_summary: str,
    parameters: Dict[str, Any],
    preview_screenshot_url: Optional[str] = None,
) -> Dict[str, Any]:
    return db.insert_approval({
        "approval_id": f"apr_{uuid.uuid4().hex[:16]}",
        "task_id": task_id,
        "step_id": step_id,
        "action_type": ActionType(action_type).value,
        "action_summary": action_summary,
        "parameters": parameters,
        "parameter_hash": compute_parameter_hash(parameters),
        "status": ApprovalStatus.PENDING.value,
        "preview_screenshot_url": preview_screenshot_url,
        "created_at": utc_now_iso(),
    })


class ApprovalDecisionError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.http_status = http_status


def decide_approval(approval_id: str, approve: bool) -> Dict[str, Any]:
    """Record a human decision. Only this function issues approval tokens."""
    approval = db.get_approval(approval_id)
    if approval is None:
        raise ApprovalDecisionError("NOT_FOUND", f"Approval {approval_id} does not exist.", 404)
    if approval["status"] != ApprovalStatus.PENDING.value:
        raise ApprovalDecisionError(
            "ALREADY_DECIDED", f"Approval {approval_id} is already {approval['status']}; decisions are final.", 409
        )
    now = utc_now_iso()
    if _is_expired(approval["created_at"]):
        db.transition_approval_status(approval_id, ApprovalStatus.PENDING.value, ApprovalStatus.EXPIRED.value, decided_at=now)
        raise ApprovalDecisionError(
            "APPROVAL_EXPIRED",
            f"Approval {approval_id} expired after {config.WEBPILOT_APPROVAL_TTL_SECONDS}s; ask WebPilot to re-plan.",
            410,
        )
    if approve:
        token = generate_approval_token(approval_id, approval["parameter_hash"])
        changed = db.transition_approval_status(
            approval_id, ApprovalStatus.PENDING.value, ApprovalStatus.USER_APPROVED.value,
            approval_token=token, decided_at=now,
        )
    else:
        changed = db.transition_approval_status(
            approval_id, ApprovalStatus.PENDING.value, ApprovalStatus.USER_REJECTED.value, decided_at=now,
        )
    if not changed:
        raise ApprovalDecisionError("ALREADY_DECIDED", f"Approval {approval_id} was decided concurrently.", 409)
    return db.get_approval(approval_id)  # type: ignore[return-value]


def _reject(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"valid": False, "error_code": code, "message": f"{code}: {message}", **extra}


def verify_and_consume_approval(
    task_id: str,
    approval_id: str,
    provided_token: str,
    current_live_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate an approval token and atomically consume the approval.

    Returns ``{"valid": True, "idempotent_replay": bool, ...}`` on success or
    ``{"valid": False, "error_code": ..., "message": ...}`` on rejection.
    """
    approval = db.get_approval(approval_id)
    if approval is None or approval["task_id"] != task_id:
        return _reject("PERMISSION_DENIED", f"No approval {approval_id} exists for task {task_id}.")

    status = approval["status"]
    if status == ApprovalStatus.PENDING.value:
        return _reject("PERMISSION_DENIED", "User has not approved this action yet.", approval_status=status)
    if status == ApprovalStatus.USER_REJECTED.value:
        return _reject("PERMISSION_DENIED", "User rejected this action; it can never be executed.", approval_status=status)
    if status == ApprovalStatus.REVOKED.value:
        return _reject(
            "APPROVAL_REVOKED",
            "This approval was revoked (page state drifted or the task was cancelled); request a new approval.",
            approval_status=status,
        )
    if status == ApprovalStatus.EXPIRED.value:
        return _reject("APPROVAL_EXPIRED", "This approval expired; request a new approval.", approval_status=status)

    expected_token = generate_approval_token(approval_id, approval["parameter_hash"])
    if not isinstance(provided_token, str) or not hmac.compare_digest(provided_token, expected_token):
        return _reject("PERMISSION_DENIED", "Invalid HMAC approval_token.", approval_status=status)

    if status == ApprovalStatus.CONSUMED.value:
        return {
            "valid": True,
            "idempotent_replay": True,
            "approval": approval,
            "message": "Approval already consumed; returning cached verified outcome.",
        }

    if _is_expired(approval["decided_at"]):
        db.transition_approval_status(approval_id, ApprovalStatus.USER_APPROVED.value, ApprovalStatus.EXPIRED.value)
        return _reject("APPROVAL_EXPIRED", "The approval window elapsed before execution; request a new approval.")

    if current_live_params is not None:
        live_hash = compute_parameter_hash(current_live_params)
        if not hmac.compare_digest(live_hash, approval["parameter_hash"]):
            db.transition_approval_status(approval_id, ApprovalStatus.USER_APPROVED.value, ApprovalStatus.REVOKED.value)
            return _reject(
                "DOM_STATE_DRIFT_DETECTED",
                "Live page parameters changed since approval was requested.",
                approved_parameter_hash=approval["parameter_hash"],
                live_parameter_hash=live_hash,
                drift=_param_diff(approval["parameters"], current_live_params),
            )

    consumed_at = utc_now_iso()
    if not db.transition_approval_status(
        approval_id, ApprovalStatus.USER_APPROVED.value, ApprovalStatus.CONSUMED.value, consumed_at=consumed_at
    ):
        # Lost a race with a concurrent execution: treat as an idempotent replay.
        latest = db.get_approval(approval_id)
        if latest and latest["status"] == ApprovalStatus.CONSUMED.value:
            return {
                "valid": True,
                "idempotent_replay": True,
                "approval": latest,
                "message": "Approval already consumed; returning cached verified outcome.",
            }
        return _reject("PERMISSION_DENIED", f"Approval is no longer executable (status {latest and latest['status']}).")
    return {"valid": True, "idempotent_replay": False, "approval": db.get_approval(approval_id)}


def revoke_open_approvals(task_id: str) -> int:
    """Revoke every not-yet-consumed approval of a task (used on cancellation)."""
    revoked = 0
    for approval in db.list_approvals_for_task(task_id):
        if approval["status"] in (ApprovalStatus.PENDING.value, ApprovalStatus.USER_APPROVED.value):
            if db.transition_approval_status(approval["approval_id"], approval["status"], ApprovalStatus.REVOKED.value):
                revoked += 1
    return revoked
