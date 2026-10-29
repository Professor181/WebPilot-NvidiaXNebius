# WebPilot — Product Feedback, Feature Requests & Friction Log

**Project:** WebPilot Task Agent (Amazon Developer Hackathon 2026 · Alexa+ Track)
**Surface used:** Self-hosted MCP server, specification `2025-11-25`, Streamable HTTP transport, plus an Alexa+ MCP Toolkit add-on manifest (`addon.json`)
**Author:** Yash Kalaskar

This log records where building an Alexa+ add-on on MCP was smooth, where it was not, and what we would change. Each friction entry says what happened, how we worked around it, and the request. We kept it honest: where we could not validate something against the live Alexa+ service, we say so.

---

## 1. What worked well

| Area | Experience |
|---|---|
| **MCP as the integration contract** | Exposing a small set of task-oriented tools (`create_web_task`, `execute_approved_step`, …) instead of dozens of low-level browser actions kept the model's job simple and made the safety boundary easy to reason about. |
| **SEP-1303 (tool errors as `isError` results)** | Returning validation and permission failures as tool execution errors rather than JSON-RPC errors was the single most useful 2025-11-25 change for us. A rejected `approval_token` comes back as `PERMISSION_DENIED` with a `hint`, so the model can explain it to the user instead of crashing the turn. |
| **Tool annotations** | `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` map naturally onto our tools. Marking `execute_approved_step` as `destructiveHint: true, idempotentHint: true` documents exactly how it behaves (single-use approval, cached replay). |
| **Icons (SEP-973) and `serverInfo.description`** | Cheap to add; they make the tool list self-describing in our inspector UI and should do the same in a device UI. |
| **Streamable HTTP** | A single `/mcp` endpoint with plain JSON responses was straightforward to implement in FastAPI and to test with `pytest` and `curl`. `202 Accepted` for notifications is simple and unambiguous. |

---

## 2. Feature requests

### FR-01 — Native `requiresConfirmation` tool annotation for Alexa+

**Problem.** `destructiveHint` tells a client a tool *may* have side effects. It does not tell Alexa+ that the tool must **never run without an explicit, device-rendered user confirmation**. Today every MCP server that handles purchases, form submissions or account changes has to invent its own approval protocol. We built a full one: a SHA-256 parameter lock, an HMAC token minted only by a human decision endpoint, drift detection and single-use consumption.

**Request.** An annotation Alexa+ enforces natively, for example:

```json
"annotations": {
  "destructiveHint": true,
  "requiresConfirmation": {
    "mode": "device_visual_and_voice",
    "summaryField": "action_summary",
    "lockedParametersField": "parameters",
    "ttlSeconds": 900
  }
}
```

Alexa+ would render the locked parameters on screen devices, read the summary aloud on voice-only devices, and only forward the call after the user confirms. Ideally it would also attach a platform-signed confirmation assertion the server can verify. Servers would then get a consistent, trusted confirmation UX for free, and users would see the same gate across every add-on.

### FR-02 — First-class long-running task support in the Alexa+ MCP client

See FL-02. Concretely: support for MCP progress notifications and resumable task handles, so a tool can return "working on it" immediately and Alexa+ can proactively deliver the result later.

### FR-03 — A hosted add-on validator and simulator

See FL-03. A `addon.json` linter plus a cloud simulator that can reach a tunnelled or regional endpoint, so builders outside the launch region can test the full voice path.

### FR-04 — Honour `tools/list_changed` and give voice clients a "barge-in" channel for running tools

**What we built.** WebPilot synthesizes session-scoped tools at runtime: it advertises `capabilities.tools.listChanged: true` and pushes `notifications/tools/list_changed` over the GET SSE stream. It also accepts mid-task changes of mind through `interrupt_web_task`, applied at step checkpoints.

**What we don't know.** We could not confirm whether the Alexa+ MCP client opens the GET stream, re-fetches `tools/list` on `list_changed`, or lets a user's spoken "wait, skip ElectroHub" reach a server while a previous `tools/call` is still running.

**Request.** Document all three behaviours. Ideally, route a user barge-in during a running tool call to the server, either as an MCP cancellation or as a client-initiated follow-up call. That way, interruption-aware servers like this one can re-plan instead of being cancelled outright.

### FR-05 — Tell builders which MCP primitives Alexa+ consumes, and offer a Bedrock-native reasoning hook

**What we built.** WebPilot now implements all three MCP server primitives: tools, resources (task evidence, plans, a security audit report) and prompts (four voice-first templates). Its AI brain calls Amazon Bedrock through the Converse API.

**What we don't know.** We could not determine whether the Alexa+ MCP client reads `resources/*` or offers `prompts/*` to users. We also couldn't tell whether an add-on can borrow the Alexa+ model for its own reasoning instead of provisioning Bedrock separately. MCP `sampling` would be the natural fit, and we deliberately did not advertise it without client support.

**Request.**
1. Publish an Alexa+ MCP client capability matrix covering tools, resources, prompts, sampling, elicitation and list-changed notifications.
2. Document whether resource contents can be shown on Echo Show screens.
3. Consider MCP `sampling` support so add-ons don't need their own model credentials.

**Related friction while building it.**
- Bedrock model access is per-account and per-region, and some models are reachable only through inference-profile ids (`us.<model-id>`). A misconfigured id surfaces as a generic `ValidationException`. We made the brain degrade to the symbolic engine and print the provider error in `/api/brain-status` and the reasoning trace.
- Newer Claude models reject forced tool choice, so our Bedrock path asks for JSON through a tool with `toolChoice: auto` and also accepts plain-text JSON.

---

## 3. Friction log

### FL-01 · Browser-based simulators need `Access-Control-Expose-Headers: Mcp-Session-Id`

| | |
|---|---|
| **Severity** | High for anyone building a browser test client; invisible for server-to-server clients |
| **Where** | Streamable HTTP session management in a browser (`fetch`) |

**What happened.** Our Simulated Alexa+ web client (Next.js on `localhost:3000`) calls the MCP server on `localhost:8000`. The server issues `Mcp-Session-Id` on the `initialize` response, but browsers hide non-safelisted response headers from cross-origin JavaScript. `res.headers.get("Mcp-Session-Id")` returns `null` unless the server explicitly exposes the header. That breaks every follow-up request with a confusing session error, and the spec text on sessions does not mention CORS at all.

**Workaround.** CORS configuration on the server:

```
Access-Control-Expose-Headers: Mcp-Session-Id, MCP-Protocol-Version
Access-Control-Allow-Headers: Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID
```

We combined that with strict `Origin` validation: untrusted origins get HTTP 403 (DNS-rebinding protection), while `localhost`, `*.amazon.com` and `*.alexa.com` are allowed. Our client fails loudly with an actionable message if the header is unreadable. There is a regression test for this (`test_initialize_returns_protocol_version_server_info_and_session_id` asserts the expose header).

**Request.** Add a short "browser clients and CORS" note to the Streamable HTTP transport docs and to the Alexa+ MCP QuickStart, listing the headers to expose and allow.

### FL-02 · Multi-step web navigation vs. conversational turn timeouts

| | |
|---|---|
| **Severity** | High for agentic and browser-automation add-ons |
| **Where** | `tools/call` latency budget inside a voice conversation |

**What happened.** A WebPilot task is many steps: compile the goal, inspect two stores through the firewall, rank, attempt a checkout, recover from an out-of-stock failure, capture evidence and lock an approval. In our runs against the built-in sandbox, `create_web_task` took about 0.3–0.5 s for the attendance and form tasks and about 2.3 s for the laptop task with headless Chromium screenshots. The live GitHub analysis took about 1–2 s, depending on the network. Real-world sites (slow pages, JS rendering, retries) can easily take 10–30 s. We could not find a documented per-tool-call timeout for the Alexa+ MCP client, so we had to assume the conservative case.

**Workaround.** We made the server-side state machine explicit and durable instead of relying on one long call:

- Every task is persisted (SQLite, WAL) with statuses `PLANNED → EXECUTING → AWAITING_APPROVAL / BLOCKED / COMPLETED …`. Every plan step is persisted as it starts and finishes.
- Consequential steps always pause (`AWAITING_APPROVAL`), which naturally splits the work into separate conversational turns.
- `get_task_status` returns a voice-ready `voice_summary` and a `next_action` hint, so the model can poll and resume a task in a later turn.
- `idempotency_key` on `create_web_task` makes retries after a timeout safe.

Our web client shows this state machine live: it polls the task table while the synchronous tool call is running, and the plan DAG updates step by step.

**Request.** Document the Alexa+ tool-call timeout. Support MCP progress notifications (`notifications/progress`) and, ideally, an async job pattern (return a task handle now, then push the result as a proactive notification when it completes). That pattern is necessary for any add-on that does real web work.

### FL-03 · Regional / preview availability makes end-to-end voice testing hard; `addon.json` cannot be validated offline

| | |
|---|---|
| **Severity** | Medium |
| **Where** | Add-on manifest authoring and end-to-end testing |

**What happened.** We authored `addon.json` (manifestVersion `1.0`, `apis.custom.mcpServer` with `transport: STREAMABLE_HTTP`, `protocolVersion: 2025-11-25`) from the QuickStart description. However, there is no offline schema or linter for the manifest, so we could not confirm that the file is valid before submission. Alexa+ preview availability also varies by region and device, and the manifest endpoint has to be reachable by the Alexa cloud (our local `http://localhost:8000/mcp` is not). As a result, **we were not able to run a full voice round-trip through the real Alexa+ service from our development environment.** All of our protocol verification used our own conformance tests and the simulated client.

**Workaround.**

- 47 automated `pytest` tests cover the transport (initialize, session header, 202 notifications, 403 Origin, 404 unknown session, DELETE termination, SSE-only clients, batch rejection), the tool schemas and annotations, SEP-1303 errors, and every workflow end to end.
- The Simulated Alexa+ Multimodal Web Client exercises the exact JSON-RPC flow a real client would: initialize, then `notifications/initialized` (202), then `tools/list`, then `tools/call`. It adds voice input (Web Speech API) and voice readback (SpeechSynthesis).
- `WEBPILOT_BASE_URL` and the manifest endpoint can point at an HTTPS tunnel when a reachable endpoint is needed.

**Request.**
1. Publish a JSON Schema for `addon.json`, or provide an `ask`-style CLI validator.
2. Provide a developer-console simulator that can call a tunnelled MCP endpoint and show the raw JSON-RPC frames, which is essentially what our MCP Inspector drawer does.
3. Make the simulator available in all developer regions, even where the consumer Alexa+ experience is still in preview.

### FL-04 · A universal browser agent needs a "consequential action" contract and long-lived sessions across turns

| | |
|---|---|
| **Severity** | High for open-web agents |
| **Where** | Approval turns that need the *same* live browser state; voice latency of multi-step navigation |

**What happened.** Our Universal Live Web Autopilot drives any site with a real Chromium session. A typical shopping goal is search, open result, add to cart, then pause before checkout, which takes 4–6 browser actions and 8–25 s on real sites. The approval itself arrives in a *later* conversational turn. The cart and session cookies only exist inside that browser context, so the server must keep the session alive between turns. We bound it to the approval TTL, close it on cancel, reset and shutdown, and return `LIVE_SESSION_EXPIRED` instead of guessing if it has gone. We also had to define "consequential" ourselves. Our server-side classifier lets search and add-to-cart run, gates checkout, submit, send, delete and book behind the HMAC approval, and never presses final payment, because nothing in the MCP annotations can say "this specific click needs the user".

**Request.** (1) A per-call `requiresConfirmation` / consent hint (see FR-01) that Alexa+ renders as a native confirmation card, bound to a server-provided parameter hash. (2) Session affinity guarantees for MCP servers, so a follow-up `tools/call` in a later turn reliably reaches the server instance that holds the browser. (3) `notifications/progress` support (FL-02), so the user hears "searching… opening the first result… added to cart" while the steps run.

### FL-05 · Agents need the user's signed-in browser, but browsers (rightly) won't hand it over

| | |
|---|---|
| **Severity** | High for any "do it on my account" agent |
| **Where** | Using the user's existing website sessions; sign-in and sign-up on the user's behalf |

**What happened.** Users expect "order it on my Amazon" to just work in their logged-in browser. Since Chrome 136, remote debugging is refused on the default profile (an anti-cookie-theft measure), so no agent can legitimately drive the everyday profile. We ship a dedicated **WebPilot Chrome** profile that the user signs in to once. Passwords for automatic sign-in live only in the OS credential store; account creation happens only after an explicit choice in the chat. CAPTCHAs and one-time codes always go back to the human.

**Request.** An Alexa+ account-linking / passkey hand-off pattern for MCP add-ons, so a skill can ask the *user's* device to complete a sign-in (or approve a passkey) and receive only a scoped session, never a password. A standard "needs human sign-in" MCP elicitation would let Alexa+ render the prompt natively instead of each add-on building its own.

---

## 4. Smaller notes

- **Protocol version negotiation.** We echo the client's requested version when supported (`2025-11-25`, `2025-06-18`, `2025-03-26`) and reject unknown `MCP-Protocol-Version` headers with HTTP 400. A short compatibility table in the Alexa+ docs (which spec versions the Alexa+ client sends) would remove guesswork.
- **Structured content duplication.** We return `structuredContent` plus a JSON text block for backwards compatibility, with a human headline as the first text block. Guidance on which block Alexa+ reads aloud would help servers optimize the spoken response.
- **Session optionality.** We accept requests without `Mcp-Session-Id` (stateless mode) but return 404 for unknown or terminated sessions. Clarifying whether Alexa+ always sends the session header would let servers be stricter.
