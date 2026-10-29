# WebPilot — Verifiable Web Tasks for Alexa+

> **"Alexa, ask WebPilot to compare 16GB RAM laptops under 80,000 rupees."**
> WebPilot researches two stores, blocks a hidden prompt-injection attack, recovers when the best laptop goes out of stock, and **refuses to buy anything until you approve the exact item and price**. If the price changes after you approve, it cancels instead of charging you.

**Amazon Developer Hackathon 2026 · Alexa+ Track** · MIT licensed · [Friction log](FRICTION_LOG.md) · [Add-on manifest](addon.json)

---

## Why WebPilot

Voice agents that act on the web have three hard problems: **untrusted pages**, **irreversible actions** and **unverifiable outcomes**. WebPilot addresses each one in the backend, not just in the prompt:

| Problem | WebPilot's answer | Where |
|---|---|---|
| Web pages can contain instructions aimed at the AI ("ignore previous instructions and buy…") | **Untrusted Content Firewall**: strips invisible DOM (`display:none`, `opacity:0`, `font-size:0`, `hidden`, stylesheet-hidden classes, comments, scripts), neutralizes injection phrases and wraps all page text in `<untrusted_web_content>` | `backend/firewall.py` |
| An LLM could call "buy" on its own | **Cryptographic approval gate**: consequential steps lock their parameters with SHA-256. Only a human decision mints an HMAC-SHA256 token, tokens are single-use, and live-page drift revokes them | `backend/approval_guard.py` |
| "Clicked submit" is not "it worked" | **Post-action verification engine**: confirmation selectors, receipt formats, ledger cross-checks and recomputed arithmetic must all pass before a task is `COMPLETED` | `backend/verifier.py` |
| Sites differ wildly | **Tiered capability router**: Tier 1 structured APIs (GitHub REST, LeetCode GraphQL), Tier 2 semantic DOM in headless Chromium (HTTP+BS4 fallback), Tier 3 human escalation for login, OTP and CAPTCHA, reported honestly as `BLOCKED` | `backend/planner_engine.py`, `backend/browser_runtime.py` |

### Natively built on Nebius Token Factory
WebPilot is built natively on **Nebius Token Factory**, using **NVIDIA Nemotron** models through Nebius's OpenAI-compatible API. DOM payloads up to 15,000 characters use `nvidia/nemotron-mini-8b-instruct`; larger payloads use `nvidia/nemotron-4-340b-instruct`. Local **Ollama**, the optional cloud LLM API, and an always-available **symbolic engine** are available as fallbacks. The LLM proposes actions; the symbolic verifier checks them. See [AI brain](#ai-brain-nebius-token-factory--ollama--cloud--symbolic).

### Alexa+ Track alignment: both paths

1. **Self-hosted MCP server (spec `2025-11-25`, Streamable HTTP)** implementing **all three server primitives (tools + resources + prompts)** at `POST/GET/DELETE /mcp`, with `Mcp-Session-Id`, Origin validation (403), `202` for notifications, SEP-1303 tool errors, tool `annotations` and `icons` (SEP-973), and `serverInfo.description`. It ships with an Alexa+ MCP Toolkit add-on manifest ([`addon.json`](addon.json)).
2. **Simulated Alexa+ multimodal web client** (Next.js) that speaks real JSON-RPC 2.0 to the server: animated Alexa+ orb, mic input and voice readback (Web Speech API), a live plan DAG, the approval gate with live attack buttons, an evidence gallery, and a raw protocol inspector.

---

## Universal Live Web Autopilot (any website, zero site-specific code)

> **"Tell WebPilot what you want done on ANY website. It discovers the site, inspects the live DOM generically, decides what to type or click at each step, pauses at an HMAC approval gate before any consequential action, and verifies the result."**

`backend/live_autopilot.py` + `llm_brain.decide_next_browser_action` drive a **real Chromium session** one atomic action per step. The engine that decides what to type and click contains **no domain tables, URL maps or CSS selectors for any site** (a test asserts this by scanning the source); the only site names live in the start-URL directory.

| Loop stage | What happens (generic for every site) |
|---|---|
| **Discover** | An explicit URL or domain in the goal is opened directly. A well-known brand the goal points at ("on Flipkart", "open ChatGPT", "search wikipedia for…") starts at its official root URL from `backend/site_directory.py`; this table only picks the starting page and can be extended with `WEBPILOT_SITE_DIRECTORY_JSON`. Otherwise WebPilot types the site name into a web search engine (DuckDuckGo HTML by default, `WEBPILOT_AUTOPILOT_SEARCH_URL`) and clicks the top **organic** result. Ads and sponsored results are skipped. |
| **Sense** | A universal DOM indexer tags every visible interactive element with `data-webpilot-idx`. For each one it records the tag, type, ARIA role, accessible name (aria-label, labelledby, `<label>`, placeholder, title), text, href, landmark (dialog/search/nav/main/footer), its card's price, and its bounding box. Page text goes through the Untrusted Content Firewall. |
| **Think** | The brain gets the goal, history, element list and firewalled text, and returns one action: `FILL_AND_SUBMIT`, `FILL`, `CLICK`, `SCROLL_DOWN`, `ASK_HUMAN_APPROVAL`, `DONE` or `BLOCKED`. Local **Ollama** is used when it's running. Every LLM proposal is validated by a **site-agnostic semantic policy** (token overlap with IDF weighting, ARIA roles, labels, landmarks, button intent), which also acts as the fallback. Proposals pointing at elements that don't exist, repeated actions or a premature `DONE` are rejected. |
| **Act** | The target gets a cyan outline and a *"WebPilot Step #k"* badge, a PNG is saved to `artifacts/{task_id}_step_{k}.png`, and Playwright performs the action. Cookie banners always get the privacy-preserving choice (reject or close, never "accept all"). |
| **Verify** | Every step must visibly change the page (URL, title or DOM signature), otherwise it is recorded as failed. On `DONE`, the final page is firewalled and answered with DOM-cited items (`verify_grounded_answer`). |

**Safety is decided server-side, not by the model.**
- Typing into search boxes and adding to a cart are **safe** and execute automatically.
- **Consequential** controls (checkout, buy now, submit application, send, delete, book…) are always turned into `AWAITING_APPROVAL`. Their page state (heading, button text, every visible price) is locked behind the SHA-256 + HMAC gate.
- The live browser session waits for your decision. On approval WebPilot re-indexes the live page and re-checks the locked state, so **price drift revokes the approval**. It then clicks, classifies the landing page, and reports `LIVE_CHECKOUT_HANDOFF_VERIFIED` when it reaches the payment step.
- **Final payment controls ("Pay now", "Place order") are never pressed**: the last step is always yours.
- Login, OTP and CAPTCHA walls end in `BLOCKED` with no bypass attempt. With 👁️ *Watch Live Browser* and 🔐 *Persistent Profile* both on, WebPilot waits in the visible window while **you** sign in, and the session is saved in `backend/browser_profile/`.
- **🔐 Persistent Profile attaches to your own Chrome.** WebPilot first connects to a Chrome you started with remote debugging (`connect_over_cdp`, `WEBPILOT_CHROME_CDP_URL`, default `http://localhost:9222`, loopback addresses only). It reads your open tabs (`browser.contexts[0].pages`). If one is already on the goal's site, it **continues in that tab** from where you are, in your logged-in session, without navigating away. Otherwise it opens a **new tab** in the same profile; unrelated tabs are never taken over, so nothing you're doing in them is lost. Only the tab WebPilot drives is routed through its filters. When the task ends, a reused tab gets its hooks removed and stays open, a tab WebPilot opened is closed, and WebPilot disconnects; your browser stays running. If nothing answers on the CDP port (refused or timed out), the task **never blocks**: WebPilot logs `CDP port 9222 not active; falling back to persistent local profile` and opens a visible `launch_persistent_context` (`headless=False`) with its own `backend/browser_profile/`. The launch step in the UI says which browser was used, so you can tell when your own Chrome wasn't connected. Run `start_chrome_debug.bat` to start Chrome correctly. Chrome 136+ ignores `--remote-debugging-port` on its default profile folder (a Google security change), so the script uses a dedicated profile under `%LOCALAPPDATA%\WebPilot\ChromeProfile`: sign in to your sites there once. The approval gate and the never-press-payment rule apply exactly as before, and matter more here because these are your real accounts.
- Private-network targets are refused, both at start and after every navigation.

**How to use it:** in the web client, 🌐 *Universal Live Web* is ON by default for typed and spoken goals; the demo chips stay on the deterministic sandbox. Over MCP, call the `run_live_web_autopilot` tool with `goal`, optional `start_url`, `max_steps` (default 5), `watch_live_browser` and `use_persistent_profile`. `create_web_task` also routes there automatically for multi-step goals that name a site or "the web". Each step appears in the **Live Browser Action Filmstrip**, which shows the highlighted screenshot, URL, verification tick and the decision (Ollama or semantic policy, plus any rejected LLM proposal).

The tests run the same engine on three fixture sites it has never been told about, each with deliberately different markup:
- **Paperlane:** a JS-only search with no form or name attributes, an aria-labelled input, an arrow button, `<section>` cards, a "Put in my tote" control and a "Checkout securely" link.
- **ShopMart:** a classic form layout with a cookie banner and a sponsored first result.
- **Findr:** a web search engine with an ad on top, used for site discovery.

A live multi-step run on wikipedia.org is also tested.

## Conversations on any website (logged in, follow-ups, checkout up to payment)

Talk to WebPilot the way you'd talk to a person sitting at your browser:

> **You:** go on amazon, search for the best running shoes under 3000 and show me
> **Alexa+:** Here are the best matches under 3,000: 1. … — ₹2,499 2. … — ₹999 *(with a screenshot of each option)*
> **You:** is the first one available in size 10?
> **Alexa+:** No — size 10 isn't available for “…” (“UK 10” is shown but disabled).
> **You:** what about the second one in size 8? → **Yes** · **You:** buy it → *approval card* · **You:** yes
> **Alexa+:** Done — I stopped at the payment step (total ₹1,048 incl. delivery). Take over in the WebPilot browser window to pay.

| Piece | How it works |
|---|---|
| **Your logged-in browser** | The **WebPilot Chrome** (`backend/user_chrome.py`) is a real Google Chrome window with its own long-lived profile (`%LOCALAPPDATA%\WebPilot\ChromeProfile`). WebPilot starts it automatically (DevTools port bound to 127.0.0.1), keeps it open between tasks, and attaches over CDP. Sign in to a site there once and every later task is already logged in, like a person's browser. Chrome 136+ blocks automation on your *everyday* profile (a Google anti-cookie-theft rule), which is why the profile is a dedicated one. |
| **Conversations** | The UI sends a `conversation_id`. The conversation keeps **one tab** and a memory of the options it showed, so follow-ups such as “the first one”, “option 2”, “this one looks good”, “buy it” and “book it” work on the same page. New chat, cancel, reset or 30 idle minutes closes the tab. |
| **Generic understanding** | `backend/task_intents.py`: references to earlier results; variants (“size 10”, “colour black”, “256 GB storage”), checked against disabled / sold-out swatches, `<select>` options and stock text; budgets (“under 15k”, “below ₹2,000”); booking details (check-in/out dates, adults/children/rooms). The same module handles calendars (day cells, month navigation), date inputs, guest steppers and selects. None of it is site-specific. |
| **Sign-in** | `backend/site_login.py` recognises sign-in, multi-step identifier→password, sign-up and one-time-code pages from the DOM. With a **saved login** WebPilot signs in by itself. Without one it **asks you** in the chat: *I'll sign in myself* (it waits in the window), *use a login* (optionally remembered), or *create an account with my email*. Account creation uses a generated 20-character password and is only offered when you've set your email in Settings. CAPTCHAs and one-time codes are always yours. |
| **Credential vault** | `backend/credential_vault.py` stores passwords **only in Windows Credential Manager** (via `keyring`). Nothing goes in SQLite, logs, task records, screenshots, LLM prompts or MCP. The model can't fill password fields: the server rejects it. The vault is reachable only from the local UI's Origin-guarded REST endpoints, never as an MCP tool. |
| **Checkout up to payment** | After you approve “Buy now”, “Reserve” or “Checkout”, WebPilot continues **within that approval** through sign-in, “Deliver to this address”, “Continue to payment” and review screens. It stops on the payment page and hands over to you. It never fills in personal details (address, phone) for you; it asks you to. Final pay buttons are never pressed. |
| **Screenshots** | Each step, each option card and the handoff are captured and shown in the chat. They are **deleted automatically** when the conversation ends (New chat, idle timeout, reset). Screenshots of stand-alone tasks are deleted 30 minutes after they finish (`WEBPILOT_SCREENSHOT_RETENTION_SECONDS`). |
| **Chat-style sites** | “Open ChatGPT and ask it to plan a 3-day Goa trip” → WebPilot finds the composer (textarea / rich-text box), types and sends your message, waits until the streamed reply stops changing, and reads back only the new reply text (firewall-neutralized). |
| **Chat UI** | A large chat panel with a scrolling thread, result screenshots, a multi-line input, voice input and readback. Approvals and sign-in choices are inline. Say “yes” or “no” to answer an approval. Settings & logins holds your email for sign-ups, saved logins and a button to open the WebPilot Chrome. |

## Architecture

```mermaid
flowchart LR
    subgraph Client["Alexa+ (real device / Simulated Multimodal Web Client :3000)"]
        V["Voice: SpeechRecognition + TTS"] --> UI["Plan DAG · Multimodal cards · Approval Gate · Evidence · MCP Inspector"]
    end

    UI -- "JSON-RPC 2.0 over Streamable HTTP\nPOST /mcp + Mcp-Session-Id" --> MCP
    UI -- "Human decision only\nPOST /api/approvals/{id}/decide" --> REST

    subgraph Backend["FastAPI backend :8000"]
        MCP["mcp_server.py\nMCP 2025-11-25 · 10 tools + session-synthesized tools\nOrigin 403 · 202 · SEP-1303 · tools/list_changed"] --> PL
        REST["main.py REST\n/health /api/tasks /api/mcp-logs /api/reset-demo"] --> AG
        PL["planner_engine.py\nGoal compiler + dynamic planner\n6 scenario workflows"]
        PL --> T1["Tier 1 adapters\nGitHub REST · LeetCode GraphQL"]
        PL --> BR["browser_runtime.py\nPlaywright Chromium → PNG\nHTTP/BS4 fallback → SVG"]
        BR --> FW["firewall.py\nUntrusted Content Firewall"]
        PL --> AG["approval_guard.py\nSHA-256 lock · HMAC token\ndrift check · single-use"]
        PL --> VF["verifier.py\nPost-action verification"]
        PL --> DB[("SQLite WAL\ntasks · approvals · evidence\nmcp_sessions · mcp_logs")]
        MCP --> DB
    end

    BR -- "ASGI direct" --> SB["/sandbox/* demo sites\nEduPortal · ElectroHub · ByteBazaar\nGrant Portal · ERP login wall · CodeArena"]
    BR -- "SSRF-guarded HTTPS" --> WEB["Public web"]
    T1 --> GH["api.github.com"]
```

### The approval gate, step by step

```mermaid
sequenceDiagram
    participant LLM as Alexa+ / LLM
    participant MCP as WebPilot MCP
    participant User as User (device screen)
    participant Store as Live page
    LLM->>MCP: create_web_task("buy best 16GB laptop < ₹80k")
    MCP->>Store: inspect, rank, prepare checkout (no purchase)
    MCP-->>LLM: AWAITING_APPROVAL · parameter_hash = SHA-256(item, seller, ₹74,990, url)
    LLM->>MCP: execute_approved_step(forged token)
    MCP-->>LLM: isError · PERMISSION_DENIED
    User->>MCP: POST /api/approvals/{id}/decide APPROVE
    MCP-->>User: approval_token = HMAC(secret, id + hash)
    LLM->>MCP: execute_approved_step(token)
    MCP->>Store: re-read live page → recompute hash
    alt price drifted (₹86,990)
        MCP-->>LLM: isError · DOM_STATE_DRIFT_DETECTED · approval REVOKED
    else unchanged
        MCP->>Store: place order once (approval → CONSUMED)
        MCP->>Store: verify receipt SIM-ORD-2026-8841
        MCP-->>LLM: COMPLETED · SIMULATED_TRANSACTION_VERIFIED
    end
```

---

## Quickstart (Windows, one click)

Prerequisites: **Python 3.11+** and **Node.js 18.18+** on `PATH`.

```bat
start_all.bat
```

This opens two windows and then launches your browser at **http://localhost:3000**:

| Window | What it does |
|---|---|
| `WebPilot Backend :8000` | `start_backend.bat`: creates `.venv`, installs `backend/requirements.txt`, installs Playwright Chromium once, then runs `uvicorn backend.main:app --port 8000` (**without** `--reload`, so the Windows Proactor event loop can launch headless Chromium) |
| `WebPilot Frontend :3000` | `start_frontend.bat`: `npm install` on first run, then `npm run dev` |

### Manual setup (any OS)

WebPilot uses Nebius Token Factory with NVIDIA Nemotron models. Add your Nebius API key to a `.env` file in the project root:

```env
NEBIUS_API_KEY=your_nebius_api_key_here
```

The key enables Nebius-backed LLM routing; the symbolic engine remains available without it.

```bash
# Backend
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt
python -m playwright install chromium     # optional: real PNG screenshots (SVG fallback otherwise)
uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # or: npm run build && npm start
```

| URL | Purpose |
|---|---|
| http://localhost:3000 | Simulated Alexa+ multimodal client |
| http://localhost:8000/mcp | MCP 2025-11-25 Streamable HTTP endpoint |
| http://localhost:8000/health | Health, MCP version, browser-runtime status |
| http://localhost:8000/sandbox | The built-in demo websites |
| http://localhost:8000/docs | OpenAPI docs for the REST helpers |

Configuration is optional. Copy `.env.example` to `.env` to set `GITHUB_TOKEN` (raises the GitHub API limit), `WEBPILOT_APPROVAL_SECRET` (**change it for any shared deployment**) or `WEBPILOT_BASE_URL` (your HTTPS tunnel when exposing `/mcp` to Alexa+). The frontend reads `NEXT_PUBLIC_BACKEND_URL` (default `http://localhost:8000`).

---

## MCP 2025-11-25 conformance proof

```bash
# 1. initialize → protocolVersion + Mcp-Session-Id header
curl -si http://localhost:8000/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'

# 2. notification → HTTP 202, empty body
curl -si http://localhost:8000/mcp -H "Content-Type: application/json" -H "Mcp-Session-Id: <id>" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. untrusted Origin → HTTP 403
curl -si http://localhost:8000/mcp -H "Origin: http://evil.attacker.com" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"ping"}'
```

| Requirement | Implementation |
|---|---|
| `protocolVersion: "2025-11-25"`, `serverInfo` with `name`, `title`, `version`, `description`, `icons` | `mcp_server._handle_message` / `server_info()` |
| `Mcp-Session-Id` issued on `initialize`, echoed on all responses, 404 for unknown sessions, `DELETE /mcp` terminates | `mcp_server.mcp_post`, `mcp_delete` |
| `Access-Control-Expose-Headers: Mcp-Session-Id` | `main.py` CORS middleware |
| Origin validation (403); allows no-Origin, `localhost`, `127.0.0.1`, `*.amazon.com`, `*.alexa.com` | `mcp_server.is_origin_allowed` |
| Notifications and client responses → `202 Accepted`, `content-length: 0` | `mcp_server.mcp_post` |
| Tool input validation → `result.isError: true` (SEP-1303); unknown tool → JSON-RPC `-32602` | `mcp_server.call_tool` |
| JSON Schema 2020-12 `inputSchema`, `annotations`, `icons` (SEP-973) on all tools | `mcp_server.TOOLS` |
| `capabilities.tools.listChanged: true`; `notifications/tools/list_changed` pushed on the GET SSE stream when a tool is synthesized | `mcp_server.publish_to_session` |
| `MCP-Protocol-Version` header validation, batch rejection, SSE response for SSE-only clients, `GET /mcp` SSE stream with priming event | `mcp_server.py` |
| Every frame logged with latency for the live inspector | `db.insert_mcp_log` → `GET /api/mcp-logs` |

### The 10 tools (+ session-synthesized tools)

| Tool | Annotations | Purpose |
|---|---|---|
| `create_web_task` | open-world | Compile a goal, plan, run until completion or a human gate |
| `get_task_status` | read-only, idempotent | Status, steps, data, approvals, `voice_summary` |
| `inspect_website` | read-only, open-world | Firewall-sanitized DOM distillation and barrier detection for any URL |
| `request_user_approval` | read-only | Locked parameters and hash; returns the token only after a human approves |
| `execute_approved_step` | **destructive**, idempotent | HMAC + drift-checked, single-use execution |
| `verify_task_result` | read-only, idempotent | Re-run post-action verification |
| `get_execution_evidence` | read-only | Screenshots, DOM citations, firewall alerts |
| `cancel_web_task` | destructive | Cancel and revoke pending approvals |
| `interrupt_web_task` | open-world | Mid-task change of mind: queued at the next step checkpoint, or immediate re-plan at the approval gate |
| `synthesize_custom_tool` | open-world, idempotent | Compile a novel data goal into a whitelisted declarative tool and register it for this MCP session |
| `dynamic_synthetic_tool_*` | read-only, idempotent | Tools synthesized at runtime; listed only in the session that created them (1 h TTL) |

---

## AI brain: Nebius Token Factory · Ollama · Cloud · Symbolic

| Provider id | When it's used | How |
|---|---|---|
| `nebius_token_factory` | `NEBIUS_API_KEY` set | OpenAI Python client at `https://api.tokenfactory.nebius.com/v1/`; DOM payloads over 15,000 characters use `nvidia/nemotron-4-340b-instruct`, otherwise `nvidia/nemotron-mini-8b-instruct` |
| `ollama_local` | an Ollama server answers `GET /api/tags` within 0.5 s (probe cached 30 s) | `POST /api/chat` with `format: "json"` using `OLLAMA_MODEL` or the first of llama3.2 / qwen2.5 / mistral |
| `cloud_llm` | `LLM_API_KEY` set | Raw REST via `httpx`: Anthropic Messages (`claude-opus-5-5`, `output_config.format` json_schema), OpenAI Chat Completions (`json_object`) or Gemini `generateContent` (`application/json`) |
| `deterministic_symbolic` | always (tests force it) | rule-based goal compiler + structured DOM ranker |

Set `NEBIUS_API_KEY` in the backend environment to enable Nebius. `WEBPILOT_BRAIN_PROVIDER=auto|nebius|ollama|cloud|deterministic` selects the provider; auto mode prefers Nebius, then Ollama, then the optional cloud provider. `GET /api/brain-status` reports what is configured and reachable, and the header badge shows it live.

- **`compile_goal_with_ai`:** the symbolic compiler always runs. An LLM proposal is schema-checked: known scenario, bounded numbers, http(s)/sandbox URLs only. It can rescue a goal no rule recognizes ("help me figure out if I'm at risk in my classes" → attendance audit), but it never overrides a rule match. Every task stores `brain_provider_used` and an `ai_reasoning_trace` covering classification, constraints, risk level, capability-tier routing and the MCP primitives used.
- **`answer_live_web_question`:** firewall-sanitized DOM items (headlines with points, headings, table rows, prices) are numbered and sent wrapped in `<untrusted_web_content>`. Citations to items that don't exist are dropped. The verifier then re-checks every cited item against the DOM snapshot: identical text, identical points, ranking order, and that each item matches a filter term.
- **Any provider error is non-fatal.** It is written into the trace and the symbolic path takes over.

**What has been verified:** the Nebius client call and model routing are covered with mocked responses, and the Ollama and cloud paths are tested with mocked HTTP. **What has not:** no live call to Nebius, Ollama or a cloud LLM has been made from this repository's test environment.

## MCP 2025-11-25 trifecta: tools + resources + prompts

`initialize` advertises `tools {listChanged: true}`, `resources {subscribe: false, listChanged: true}` and `prompts {listChanged: false}`.

| Primitive | Methods | What WebPilot exposes |
|---|---|---|
| Tools | `tools/list`, `tools/call`, `notifications/tools/list_changed` | 10 tools + session-synthesized `dynamic_synthetic_tool_*` |
| Resources | `resources/list`, `resources/templates/list`, `resources/read`, `notifications/resources/list_changed` | `webpilot://tasks/recent`, `webpilot://security/firewall-report` (blocked injections by technique/pattern, approvals by status, denied execute attempts), `webpilot://tasks/{task_id}/evidence`, `webpilot://tasks/{task_id}/plan`. Unknown URIs → `-32002` |
| Prompts | `prompts/list`, `prompts/get` | `alexa_check_attendance_threshold`, `alexa_compare_and_buy_with_approval`, `alexa_audit_live_webpage`, `alexa_analyze_github_repository`. Missing or invalid args → `-32602` |

The MCP Inspector's **Resources & Prompts** tab sends these requests live.

## Elite upgrades

### 1 · Live web intelligence and universal capture
Any public URL is opened in **headless Chromium** with a live viewport PNG. The page passes through the firewall, which distills **articles/headlines** (with Hacker News-style rank and points), **price points** (₹, $, €, £, including `/mo` pricing), **JSON-LD and OpenGraph** metadata (neutralized too), tables, forms and ARIA-role interactive elements. If Chromium times out or can't launch, WebPilot falls back to `httpx` + BeautifulSoup with an SVG snapshot, and records the `fallback_reason` instead of crashing. Private and local network targets remain blocked.

### 2 · Dynamic tool synthesis and a self-healing registry
Goals no built-in workflow covers ("Extract trending crypto indices and calculate moving averages", "Parse this custom XML pricing feed") are compiled into a **declarative pipeline** of whitelisted operations: `moving_average`, `pct_change`, `rank_columns_by_change`, `sort_rows`, `top_n`, `filter_rows` and `aggregate`. The pipeline is registered as a **session-scoped MCP tool** (`dynamic_synthetic_tool_<slug>_<hash>`) and `notifications/tools/list_changed` is emitted. Results are independently recomputed before being reported.

The safety stance is deliberate:
- **No generated code runs.** No `eval`/`exec`; the interpreter only knows the whitelisted operations.
- **Feeds are untrusted too.** Every feed value passes the injection neutralizer.
- **XML entity attacks are blocked.** XML with `DOCTYPE`/`ENTITY` declarations is rejected.
- **Tools are private.** Each tool is visible only to the MCP session that created it.

The compiler is deterministic; this synthesized-tool workflow does not call an LLM.

### 3 · Mid-task conversational interruptions and re-planning
`POST /api/tasks/{id}/interrupt` and the MCP tool `interrupt_web_task` accept requests like "Wait, change the budget from 80000 to 70000", "Skip ElectroHub and only check ByteBazaar", "use 85 percent" or "change the project title to X".

- **While `EXECUTING`:** the change is queued (HTTP 202) and applied at the next **step checkpoint**. For example, a store that hasn't been visited yet is skipped (`SKIPPED`).
- **At `AWAITING_APPROVAL`:** the pending approval is **revoked**. The tail of the plan is superseded but kept visible with its evidence, and ranking, checkout and a new approval are recomputed immediately.

A ⚡ step marks each interruption in the DAG. Slow-motion mode (`parameters.pace_ms`) makes this easy to demo live. Late or unparseable changes are rejected honestly (HTTP 409 or 422 with example phrasings).

### 4 · Live Attack & Defense Lab (UI tab)
- **Attack 1 · prompt injection:** type any payload, pick one of 8 hiding techniques, and the backend publishes a poisoned ElectroHub copy (payload HTML-escaped). `inspect_website` then shows the firewall blocking it. A further step lets the shopping agent loose on the poisoned store; it still picks the in-budget laptop and pauses for approval.
- **Attack 2 · man-in-the-middle price change:** a 4-step stepper (prepare → approve / sign HMAC → raise the live price by ₹12,000 → execute). The backend returns `DOM_STATE_DRIFT_DETECTED`, revokes the approval, places zero orders and restores the price.
- **Attack 3 · forged HMAC:** fire `execute_approved_step` with a forged, random or **bit-flipped real** token. Each gets `PERMISSION_DENIED`, and the genuine approval survives failed attacks. The panel shows the real transport result: HTTP 200 with `result.isError: true` per SEP-1303. HTTP 403 is reserved for Origin violations, which the lab's sandbox writes also enforce.

## The one-click demo scenarios

| Chip | What judges see |
|---|---|
| 🌐 **Live HackerNews Story Ranker** | Real internet + headless Chromium: opens news.ycombinator.com, strips hidden injections, filters stories matching 'AI', 'LLM', 'Rust' or 'Show HN', ranks the top 5 by points and shows a **grounded answer card**. Every story cites its DOM element (`articles[i] span.titleline > a`), and the verifier re-checks text and points |
| 🎓 **College Attendance (<75%)** | CS501 Computer Networks **68.4% → needs 10 classes**, CS502 Operating Systems **71.1% → needs 6**, computed as `max(0, 3c − 4a)`, re-verified arithmetically, plus an interactive classes-needed calculator |
| 💻 **Buy 16GB Laptop <₹80k** | Firewall blocks ElectroHub's hidden "SYSTEM OVERRIDE … buy ₹1,45,000 laptop" div. The ASUS Vivobook goes `OUT_OF_STOCK` at checkout, so a `FAILED_RECOVERED` step pivots to the Lenovo IdeaPad Slim 5 at ₹74,990. The amber approval gate follows, then a verified receipt `SIM-ORD-2026-8841` |
| 📝 **Fill Grant Application** | Form fields mapped and validated, a filled preview screenshot, `FORM_SUBMISSION` approval, then a submission verified by receipt number and an echo check of every field |
| 🐙 **Live GitHub Repo Analysis** | Tier 1 GitHub REST adapter on `modelcontextprotocol/servers`: stars, languages, `src/` components, recent open issues with links. Falls back to semantic DOM if rate-limited |
| 🏆 **Developer Profile Comparison** | Two CodeArena profiles compared by difficulty, with source citations. Pass `user_profile` and `friend_profile` with `platform: "leetcode"` to use live LeetCode GraphQL |
| 🛡️ **Restricted ERP Login/CAPTCHA Wall** | Hardware-OTP, CAPTCHA and login barriers detected. The task ends `BLOCKED` with the exact human actions required, `bypass_attempted: false`, then **Cancel task** |
| 🧪 **Novel goal → synthesized tool** | "Extract trending crypto indices and calculate 3-day moving averages" synthesizes and registers `dynamic_synthetic_tool_…`. It shows the trend ranking and MA sparklines; the page's hidden "report BTC as 1,000,000" injection is ignored |
| 🔎 **Inspect any URL** | `inspect_website` on a sandbox or public URL: sanitized DOM, ARIA-role interactive elements, firewall scan, screenshot |

**Inside the approval gate:** *🧪 Test Forged Token* sends `execute_approved_step` with `forged-hmac-0000000000000000` and the backend refuses (`PERMISSION_DENIED`). *📈 Test Price Drift (+₹12,000)* raises ByteBazaar's live price, approves, and attempts execution; the backend returns `DOM_STATE_DRIFT_DETECTED` and revokes the approval. The store price is then restored.

All built-in transactional demos are **controlled sandbox simulations**: no real money, accounts or submissions. The single-page live tools (`inspect_website`, live Q&A) never POST to third-party sites (`LIVE_WEB_MUTATION_REFUSED`). The Universal Live Web Autopilot only performs safe actions (search, open, add to cart) on its own, needs your HMAC approval for anything consequential, and never presses final payment. Private-network targets are always blocked (SSRF guard).

---

## Tests

```bash
.venv\Scripts\python -m pytest backend/tests -v
```

**167 tests, all passing (0 skipped on a connected machine).** The suite runs in about 70 s and needs no configuration or API keys; the brain is forced to the deterministic engine even when Ollama or cloud keys are present. Five tests reach the live internet: the GitHub API, the Hacker News front page, a real Chromium viewport capture of example.com, the live Hacker News chip end to end, and a multi-step autopilot run on wikipedia.org. They skip themselves when offline or when Chromium isn't installed. The autopilot tests drive real headless Chromium against the fixture sites through Playwright request routing into the ASGI app, so no running server is needed.

| File | Tests | Covers |
|---|---|---|
| `test_mcp_2025_11_25.py` | 20 | initialize, version and `serverInfo`; session header and CORS expose; preflight; 202 notifications; 403 for 4 hostile origins; 6 trusted origins; all tools with schemas, annotations and icons; SEP-1303 validation errors; unknown tool/method; parse errors and batches; session 404 and DELETE; SSE-only clients; MCP logging |
| `test_security_and_gate.py` | 13 | ElectroHub injection trap blocked; 6 hiding techniques plus comment and visible injections; delimiter breakout; `inspect_website` alerts; SSRF refusal; canonical hashing and token binding; forged token; execute-before-approval; price drift and revocation; guard unit drift; idempotent replay with exactly one order; rejected approvals are final; cross-site approval 403 |
| `test_live_web.py` | 6 | articles/points/prices/JSON-LD extraction with injection neutralization; Chromium timeout → HTTP fallback with `fallback_reason`; honest network failure; `inspect_website` intelligence fields; live Hacker News; real Chromium viewport PNG |
| `test_tool_synthesis.py` | 7 | deterministic, whitelisted compiler (rejects `exec`, `file://`, missing source); exact interpreter math; XXE/DOCTYPE rejection; novel goal → synthesized, session-listed, private tool with MA/trend verification; untrusted XML feed neutralization; synthesis errors as SEP-1303 errors; `tools/list_changed` notification |
| `test_interruption.py` | 8 | re-plan at the approval gate (old approval revoked, evidence preserved, new approval executes); budget cut → honest "nothing to order"; user-driven upgrade; **mid-execution** interrupt applied at a checkpoint (store skipped, never fetched); attendance threshold mid-run; grant field change; 404/409/422 errors and the MCP tool; late interrupts rejected |
| `test_security_attacks.py` | 11 | 6 hidden-injection techniques blocked; visible/aria payloads neutralized and benign text honestly passed as untrusted data; payload escaping and sandbox Origin guard; agent ignores a poisoned store; MITM price drift; forged and bit-flipped tokens denied without burning the approval |
| `test_championship_upgrades.py` | 30 | capabilities trifecta; `resources/list` / `templates/list` / `read` (recent, evidence, plan, firewall report counting blocked attacks, `-32002`); `resources/list_changed`; `prompts/list` + 4 rendered `prompts/get` + argument validation; symbolic compile with reasoning trace; **Nebius model cascade at the 15,000-character boundary and goal rescue**; **Ollama** rescue end to end over MCP; invalid LLM proposals discarded; Anthropic/OpenAI/Gemini request shapes; auto-mode provider order; `/api/brain-status`; grounded Q&A ranking; hallucinated-citation cleanup; fabricated-citation rejection; live-web Q&A workflow; live Hacker News chip |
| `test_universal_autopilot.py` | 17 | no site names/selectors in the engine source; generic goal-intent parsing (URL, site hint, query, cart/checkout, follow-ups); routing in `compile_goal`; the semantic policy walking an unseen site (contenteditable search, sponsored skip, cart, commit-over-payment); consequence classifier and barrier detection; **mocked Ollama** decisions (accepted, hallucinated element, premature DONE, consequential CLICK → approval); **real Chromium**: Paperlane search → result → tote → `AWAITING_APPROVAL` → approve → payment handoff (`LIVE_CHECKOUT_HANDOFF_VERIFIED`, Pay never pressed); live price drift revokes; ShopMart cookie reject + sponsored skip + cart; Findr site discovery + cheapest-price answer; login wall `BLOCKED`; private-network refusal; cancel closes the session; live wikipedia.org search + summary |
| `test_conversations.py` | 19 | follow-up references, variants, budgets, dates/guests and check-out-date parsing; sign-in / sign-up / OTP page recognition; vault keeps passwords only in the OS store (SQLite dump checked); settings validation; **whole shoe conversation in real Chromium** (budget list with card screenshots → first one size 10 sold out → second one size 8 → buy it → approval → saved-login sign-in → saved address → payment handoff; ending the chat deletes every screenshot); unknown reference; **villa booking** (calendar month navigation, date input, adults stepper, budget, sponsored skip, “the third one looks good”, Reserve → Continue to payment → handoff); sign-up with the user's email after the sign-in prompt; user-supplied login remembered; sign-in endpoint guards; LLM can't type passwords / pick disabled options; WebPilot Chrome launch args; screenshot retention |
| `test_site_directory.py` | 11 | brand → official root URL (Flipkart, ChatGPT, GitHub, Amazon, Wikipedia, LeetCode, YouTube); `search <site> for <query>` parsing; typed domains win; topic mentions ("Amazon rainforest") are not destinations; built-in workflows not hijacked; env-extensible directory rejecting non-http URLs |
| `test_cdp_attach.py` | 11 | persistent mode attaches over CDP; an open tab on the goal's site is reused (brought to front, hooks removed afterwards, never closed) while unrelated tabs are never chosen; otherwise a new tab is opened and only that tab is routed; closing never closes the user's tabs or browser; CDP failure → headed `launch_persistent_context` with a warning; non-loopback CDP endpoints refused; with Watch Live Browser on, a closed port or a timeout falls back to a headed `browser_profile` launch with the warning logged; **real** attach to a separately started Chromium with `--remote-debugging-port` running the autopilot to the approval gate, with the browser still alive afterwards; **real** reuse of an already-open example.com tab in installed Google Chrome (no extra tab, tab still open afterwards) |
| `test_workflows_e2e.py` | 14 | goal compiler (including all 6 UI chip goals); attendance; laptops with recovery, approval, execution and verification; grant form; invalid form data → `AWAITING_USER_INPUT`; GitHub via mocked API, rate-limit HTML fallback and live API; profile comparison; ERP/CAPTCHA `BLOCKED` then cancel; cancel revokes the gate; generic URL distillation; REST and idempotency key; SVG evidence fallback |

Frontend checks: `cd frontend && npm run typecheck && npm run lint && npm run build`.

---

## Repository layout

```text
webpilot-alexa-plus/
├── addon.json               Alexa+ MCP Toolkit add-on manifest
├── start_all.bat            Backend + frontend in parallel windows
├── start_backend.bat        FastAPI :8000
├── start_frontend.bat       Next.js :3000
├── FRICTION_LOG.md          Product feedback, feature requests, friction log
├── backend/
│   ├── main.py              FastAPI app: /mcp, REST, /sandbox, /artifacts
│   ├── mcp_server.py        MCP 2025-11-25 Streamable HTTP + 10 tools + session tool registry
│   ├── llm_brain.py         Multi-provider AI brain: Nebius Token Factory · Ollama · cloud REST · symbolic
│   ├── mcp_resources.py     MCP resources (tasks, evidence, plan, firewall report) + 4 Alexa+ prompts
│   ├── tool_synthesizer.py  Declarative tool synthesis, whitelisted interpreter, session registry
│   ├── interruptions.py     Natural-language mid-task change parser
│   ├── planner_engine.py    Goal compiler, dynamic planner, scenario workflows
│   ├── browser_runtime.py   Playwright Chromium / HTTP+BS4 fallback, PNG/SVG evidence
│   ├── firewall.py          Untrusted Content Firewall
│   ├── approval_guard.py    SHA-256 lock, HMAC token, drift + single-use
│   ├── verifier.py          Post-action verification
│   ├── sandbox_routes.py    Built-in demo websites
│   ├── db.py · models.py · config.py
│   └── tests/               47 pytest tests
└── frontend/                Next.js 15 · React 19 · TypeScript · Tailwind · lucide-react
    └── src/
        ├── types/webpilot.ts       Contracts mirroring the backend payloads
        ├── lib/mcpClient.ts        Real MCP Streamable HTTP client + REST helpers
        ├── lib/voice.ts · format.ts
        ├── components/             AlexaHeader, ScenarioBar, VoiceCommandBar, PlanDagStepper, InterruptionBar,
        │                           MultimodalStage, ApprovalGateCard, EvidenceGallery, McpInspectorDrawer,
        │                           SecurityAttackSimulatorPanel, BrainStatusBadge, McpPrimitivesExplorer
        └── app/                    layout.tsx, page.tsx, globals.css
```

## Connecting a real Alexa+ add-on

`addon.json` points at `http://localhost:8000/mcp` for local development. To let the Alexa cloud reach it, expose port 8000 through an HTTPS tunnel, set `WEBPILOT_BASE_URL` to the tunnel URL, and update `apis.custom.mcpServer.endpoint`. Requests from the Alexa cloud carry no browser `Origin` and are accepted; browser calls must come from an allowed origin. See [FRICTION_LOG.md](FRICTION_LOG.md) (FL-03) for what we could and could not validate end to end.

## License

[MIT](LICENSE) © 2026 Yash Kalaskar
