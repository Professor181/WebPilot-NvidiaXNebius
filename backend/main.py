"""FastAPI entrypoint: MCP server (/mcp), REST APIs for the Alexa+ web client, sandbox sites and evidence artifacts.

Run with:  uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import approval_guard, config, db
from backend.browser_runtime import browser_runtime
from backend.mcp_server import TOOLS, is_origin_allowed
from backend.mcp_server import router as mcp_router
from backend.models import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalStatus,
    InterruptRequest,
    ResetDemoRequest,
    TaskStatus,
)
from backend.planner_engine import ToolError, planner
from backend.sandbox_routes import reset_sandbox_state
from backend.sandbox_routes import router as sandbox_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("webpilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    browser_runtime.set_asgi_app(app)
    logger.info("WebPilot backend ready on %s (MCP %s at /mcp)", config.WEBPILOT_BASE_URL, config.MCP_PROTOCOL_VERSION)
    janitor = asyncio.create_task(_janitor())
    yield
    janitor.cancel()
    from backend import live_autopilot

    await live_autopilot.close_all_sessions()
    await browser_runtime.close()


async def _janitor() -> None:
    """Close idle conversations (and delete their screenshots) and expire screenshots of finished tasks."""
    from backend import live_autopilot

    while True:
        await asyncio.sleep(60)
        try:
            await live_autopilot.purge_expired_sessions()
            await live_autopilot.purge_old_screenshots()
        except Exception as exc:  # never let housekeeping kill the server
            logger.warning("Cleanup pass failed: %s", exc)


app = FastAPI(
    title="WebPilot Alexa+ Backend",
    version=config.APP_VERSION,
    description=config.APP_DESCRIPTION,
    lifespan=lifespan,
)
browser_runtime.set_asgi_app(app)

config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """Reject browser calls to the REST API from untrusted origins (CSRF / DNS-rebinding defence).

    /mcp performs the same check itself so rejected frames are also written to the MCP log.
    """
    path = request.url.path
    guarded = path.startswith("/api/") or (path.startswith("/sandbox/") and request.method not in ("GET", "HEAD"))
    if guarded and request.method != "OPTIONS":
        origin = request.headers.get("origin")
        if not is_origin_allowed(origin):
            return JSONResponse({"detail": f"Forbidden: Origin '{origin}' is not allowed"}, status_code=403)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=config.CORS_ORIGIN_REGEX,
    allow_origins=list(config.EXTRA_ALLOWED_ORIGINS),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "Mcp-Session-Id", "MCP-Protocol-Version", "Last-Event-ID"],
    expose_headers=["Mcp-Session-Id", "MCP-Protocol-Version"],
    max_age=600,
)

app.include_router(mcp_router)
app.include_router(sandbox_router)
app.mount("/artifacts", StaticFiles(directory=str(config.ARTIFACTS_DIR), check_dir=False), name="artifacts")


def _tool_error_response(exc: ToolError, status_code: int = 404) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=status_code)


@app.get("/", tags=["Meta"])
async def root() -> Dict[str, Any]:
    return {
        "service": config.APP_TITLE,
        "mcp_endpoint": f"{config.WEBPILOT_BASE_URL}/mcp",
        "health": f"{config.WEBPILOT_BASE_URL}/health",
        "sandbox": f"{config.WEBPILOT_BASE_URL}/sandbox",
        "docs": f"{config.WEBPILOT_BASE_URL}/docs",
    }


@app.get("/health", tags=["Meta"])
async def health() -> Dict[str, Any]:
    db_ok = True
    try:
        db.count_sessions()
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "service": config.APP_NAME,
        "version": config.APP_VERSION,
        "mcp": {
            "protocolVersion": config.MCP_PROTOCOL_VERSION,
            "supportedProtocolVersions": list(config.MCP_SUPPORTED_PROTOCOL_VERSIONS),
            "transport": "streamable-http",
            "endpoint": f"{config.WEBPILOT_BASE_URL}/mcp",
            "tools": [t["name"] for t in TOOLS],
        },
        "database": {"ok": db_ok, "path": str(config.DB_PATH), "journal_mode": "WAL"},
        "browser_runtime": browser_runtime.status(),
        "github_token_configured": bool(config.GITHUB_TOKEN),
        "mcp_primitives": ["tools", "resources", "prompts"],
    }


@app.get("/api/brain-status", tags=["Meta"])
async def brain_status() -> Dict[str, Any]:
    """Active AI brain provider plus availability of Nebius Token Factory, local Ollama and cloud LLMs."""
    from backend.llm_brain import brain

    return await brain.status()


@app.get("/api/tasks", tags=["Tasks"])
async def list_tasks(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    tasks = db.list_tasks(limit)
    return {"count": len(tasks), "tasks": tasks}


@app.get("/api/tasks/{task_id}", tags=["Tasks"])
async def get_task(task_id: str):
    try:
        snapshot = planner.task_snapshot(task_id)
    except ToolError as exc:
        return _tool_error_response(exc)
    snapshot["evidence"] = db.list_evidence_for_task(task_id)
    return snapshot


_INTERRUPT_HTTP_STATUS = {
    "TASK_NOT_FOUND": 404,
    "TASK_NOT_INTERRUPTIBLE": 409,
    "INTERRUPT_NOT_UNDERSTOOD": 422,
    "INTERRUPT_NOT_APPLICABLE": 422,
    "INVALID_CONSTRAINT": 422,
}


@app.post("/api/tasks/{task_id}/interrupt", tags=["Tasks"])
async def interrupt_task(task_id: str, body: InterruptRequest):
    """Mid-task change of mind. QUEUED for a running task (applied at the next checkpoint) or APPLIED
    immediately with re-planning when the task is waiting at the approval gate."""
    try:
        result = await planner.interrupt_task(task_id, body.instruction, body.constraints)
    except ToolError as exc:
        return _tool_error_response(exc, _INTERRUPT_HTTP_STATUS.get(exc.code, 400))
    return JSONResponse(result, status_code=202 if result["mode"] == "QUEUED" else 200)


@app.get("/api/tasks/{task_id}/interrupts", tags=["Tasks"])
async def list_task_interrupts(task_id: str):
    if db.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    items = db.list_interrupts(task_id)
    return {"task_id": task_id, "count": len(items), "interrupts": items}


@app.get("/api/approvals/{approval_id}", tags=["Approvals"])
async def get_approval(approval_id: str):
    approval = db.get_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id} not found")
    return approval_guard.public_view(approval)


@app.post("/api/approvals/{approval_id}/decide", tags=["Approvals"], response_model=ApprovalDecisionResponse)
async def decide_approval(approval_id: str, body: ApprovalDecisionRequest):
    """Human-in-the-loop decision. Approving mints the HMAC approval_token; rejecting is final."""
    try:
        approval = approval_guard.decide_approval(approval_id, approve=body.decision == "APPROVE")
    except approval_guard.ApprovalDecisionError as exc:
        return JSONResponse({"error_code": exc.code, "message": exc.message}, status_code=exc.http_status)

    task = db.get_task(approval["task_id"])
    if task is not None:
        fields: Dict[str, Any] = {"pending_approval": approval_guard.public_view(approval)}
        if approval["status"] == ApprovalStatus.USER_REJECTED.value:
            steps = task["plan_steps"]
            for s in steps:
                if s["status"] in ("PENDING", "AWAITING_APPROVAL"):
                    s["status"] = "CANCELLED"
            fields.update(status=TaskStatus.CANCELLED.value, plan_steps=steps,
                          summary="You declined the action, so I did not execute it. The task is cancelled.")
        db.update_task(task["task_id"], **fields)

    approved = approval["status"] == ApprovalStatus.USER_APPROVED.value
    return ApprovalDecisionResponse(
        approval_id=approval["approval_id"],
        task_id=approval["task_id"],
        status=approval["status"],
        approval_token=approval["approval_token"] if approved else None,
        parameter_hash=approval["parameter_hash"],
        decided_at=approval["decided_at"],
        message=("Approved. The agent may now execute this exact action once." if approved
                 else "Rejected. This action will never be executed."),
    )


@app.get("/api/mcp-logs", tags=["MCP Inspector"])
async def mcp_logs(limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    logs = db.list_mcp_logs(limit)
    return {"count": len(logs), "logs": logs}


@app.post("/api/reset-demo", tags=["Meta"])
async def reset_demo(body: Optional[ResetDemoRequest] = None) -> Dict[str, Any]:
    body = body or ResetDemoRequest()
    deleted = db.reset_demo_data(include_mcp_logs=body.include_mcp_logs, include_sessions=body.include_sessions)
    reset_sandbox_state()
    from backend import live_autopilot

    await live_autopilot.close_all_sessions()
    removed_artifacts = 0
    if config.ARTIFACTS_DIR.exists():
        for item in config.ARTIFACTS_DIR.iterdir():
            if item.is_file() and item.suffix in (".png", ".svg"):
                item.unlink(missing_ok=True)
                removed_artifacts += 1
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
    return {"reset": True, "deleted": deleted, "artifacts_removed": removed_artifacts, "sandbox_state": "reset"}


# ============================================================== conversations, sign-in, saved logins, settings
# These endpoints are for the local web UI only (Origin-guarded like the rest of /api). None of them is an MCP
# tool, so the language model can never read a saved password or answer a sign-in prompt on the user's behalf.

class SignInChoice(BaseModel):
    choice: str = Field(pattern="^(self|provide|signup|cancel)$")
    username: Optional[str] = Field(default=None, max_length=200)
    password: Optional[str] = Field(default=None, max_length=500)
    remember: bool = True


class CredentialIn(BaseModel):
    site: str = Field(min_length=3, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


class SettingsIn(BaseModel):
    email: Optional[str] = Field(default=None, max_length=200)
    full_name: Optional[str] = Field(default=None, max_length=120)


@app.post("/api/tasks/{task_id}/sign-in", tags=["Tasks"])
async def sign_in_choice(task_id: str, body: SignInChoice) -> Dict[str, Any]:
    try:
        return planner.submit_login_choice(task_id, body.model_dump())
    except ToolError as exc:
        return _tool_error_response(exc, 409 if exc.code in ("NO_SIGN_IN_PENDING", "EMAIL_NOT_SET") else 422)


@app.post("/api/conversations/{conversation_id}/end", tags=["Tasks"])
async def end_conversation(conversation_id: str) -> Dict[str, Any]:
    """Close the conversation's browser tab and delete every screenshot taken during it."""
    from backend import live_autopilot

    session = live_autopilot.SESSIONS.get(conversation_id)
    task_ids = list(session.task_ids) if session else []
    await live_autopilot.close_session(conversation_id)
    return {"ended": True, "conversation_id": conversation_id, "tasks": task_ids, "tab_closed": session is not None}


@app.get("/api/browser/status", tags=["Meta"])
async def browser_status() -> Dict[str, Any]:
    from backend import live_autopilot, user_chrome

    status = await user_chrome.status()
    status["conversations_open"] = sum(1 for s in live_autopilot.SESSIONS.values() if s.conversation_id)
    return status


@app.post("/api/browser/launch", tags=["Meta"])
async def browser_launch() -> Dict[str, Any]:
    from backend import user_chrome

    return await user_chrome.ensure_running()


@app.get("/api/credentials", tags=["Saved logins"])
async def list_credentials() -> Dict[str, Any]:
    from backend import credential_vault as vault

    return {"vault_available": vault.available(), "accounts": vault.list_accounts()}  # never includes passwords


@app.post("/api/credentials", tags=["Saved logins"])
async def save_credential(body: CredentialIn) -> Dict[str, Any]:
    from backend import credential_vault as vault

    try:
        record = vault.save(body.site, body.username, body.password, source="user")
    except vault.VaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"saved": True, "account": record}


@app.delete("/api/credentials/{site}", tags=["Saved logins"])
async def delete_credential(site: str) -> Dict[str, Any]:
    from backend import credential_vault as vault

    return {"deleted": vault.delete(site), "site": vault.site_key(site)}


@app.get("/api/settings", tags=["Saved logins"])
async def get_settings() -> Dict[str, Any]:
    from backend import credential_vault as vault

    return vault.user_profile()


@app.post("/api/settings", tags=["Saved logins"])
async def save_settings(body: SettingsIn) -> Dict[str, Any]:
    from backend import credential_vault as vault

    try:
        return vault.set_user_profile(body.email, body.full_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
