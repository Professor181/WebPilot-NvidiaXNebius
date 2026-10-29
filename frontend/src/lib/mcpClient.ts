/**
 * Real MCP 2025-11-25 Streamable HTTP client + REST helpers for the WebPilot backend.
 *
 * Protocol flow (no mocks):
 *   1. POST /mcp  initialize                -> read `Mcp-Session-Id` response header
 *   2. POST /mcp  notifications/initialized -> expect HTTP 202 Accepted (empty body)
 *   3. POST /mcp  tools/list                -> discover the 8 WebPilot tools
 *   4. POST /mcp  tools/call                -> every agent action, with `Mcp-Session-Id` + `MCP-Protocol-Version`
 *
 * The backend exposes `Mcp-Session-Id` via `Access-Control-Expose-Headers`, which is what makes a
 * browser-based client like this one possible (see FRICTION_LOG.md, FL-01).
 */
import type {
  ApprovalDecisionError,
  ApprovalDecisionResponse,
  ApprovalRequestResult,
  CallToolResult,
  ClientFrame,
  EvidenceList,
  BrainStatus,
  McpPrompt,
  McpPromptResult,
  McpResource,
  McpResourceContents,
  McpResourceTemplate,
  AttackPageResponse,
  AttackTechnique,
  InterruptResponse,
  OrdersResponse,
  SyntheticRunOutput,
  HealthResponse,
  InitializeResult,
  InspectResult,
  JsonRpcResponse,
  McpLogsResponse,
  McpServerInfo,
  McpTool,
  PriceChangeResponse,
  ResetDemoResponse,
  Scenario,
  TaskDetailResponse,
  TaskListResponse,
  TaskSnapshot,
  ToolCallOutcome,
  ToolErrorPayload,
  VerifyResult, BrowserStatus, SavedAccount, UserSettings
} from "@/types/webpilot";

export const BACKEND_URL = (process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000").replace(/\/$/, "");
export const MCP_ENDPOINT = `${BACKEND_URL}/mcp`;
export const MCP_PROTOCOL_VERSION = "2025-11-25";
export const CLIENT_INFO = { name: "alexa-plus-web-simulator", version: "1.0.0" } as const;
export const FORGED_TOKEN = "forged-hmac-0000000000000000";

export class McpTransportError extends Error {
  constructor(
    message: string,
    public readonly httpStatus: number,
    public readonly rpcCode?: number,
  ) {
    super(message);
    this.name = "McpTransportError";
  }
}

export class BackendError extends Error {
  constructor(
    message: string,
    public readonly httpStatus: number,
    public readonly errorCode?: string,
  ) {
    super(message);
    this.name = "BackendError";
  }
}

type FrameListener = (frame: ClientFrame) => void;

function headersToObject(headers: Headers): Record<string, string> {
  const out: Record<string, string> = {};
  headers.forEach((value, key) => {
    out[key] = value;
  });
  return out;
}

function tryParseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export class McpClient {
  sessionId: string | null = null;
  protocolVersion: string | null = null;
  serverInfo: McpServerInfo | null = null;
  instructions: string | null = null;
  tools: McpTool[] = [];

  private nextId = 1;
  private frameSeq = 1;
  private initPromise: Promise<void> | null = null;
  private listeners = new Set<FrameListener>();

  onFrame(listener: FrameListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit(frame: Omit<ClientFrame, "id">) {
    const full = { ...frame, id: this.frameSeq++ };
    this.listeners.forEach((l) => l(full));
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      ...extra,
    };
    if (this.sessionId) {
      h["Mcp-Session-Id"] = this.sessionId;
      h["MCP-Protocol-Version"] = this.protocolVersion ?? MCP_PROTOCOL_VERSION;
    }
    return h;
  }

  /** POST one JSON-RPC message; returns the HTTP response and parsed body (null for 202). */
  private async post(
    message: Record<string, unknown>,
    tool?: string,
  ): Promise<{ res: Response; body: JsonRpcResponse | null; latencyMs: number }> {
    const requestHeaders = this.headers();
    const started = performance.now();
    let res: Response;
    try {
      res = await fetch(MCP_ENDPOINT, { method: "POST", headers: requestHeaders, body: JSON.stringify(message) });
    } catch (err) {
      throw new McpTransportError(
        `Cannot reach the WebPilot MCP server at ${MCP_ENDPOINT}. Is the backend running? (${(err as Error).message})`,
        0,
      );
    }
    const latencyMs = Math.round(performance.now() - started);
    let body: JsonRpcResponse | null = null;
    if (res.status !== 202) {
      const text = await res.text();
      const contentType = res.headers.get("content-type") ?? "";
      if (contentType.includes("text/event-stream")) {
        const dataLine = text.split("\n").find((l) => l.startsWith("data: "));
        body = dataLine ? (tryParseJson(dataLine.slice(6)) as JsonRpcResponse) : null;
      } else {
        body = (tryParseJson(text) as JsonRpcResponse) ?? null;
      }
    }
    this.emit({
      at: new Date().toISOString(),
      method: String(message.method),
      tool,
      requestHeaders,
      responseHeaders: headersToObject(res.headers),
      status: res.status,
      latencyMs,
    });
    return { res, body, latencyMs };
  }

  /** initialize -> notifications/initialized (202) -> tools/list */
  initialize(force = false): Promise<void> {
    if (force) {
      this.initPromise = null;
      this.sessionId = null;
    }
    if (!this.initPromise) {
      this.initPromise = this.doInitialize().catch((err) => {
        this.initPromise = null;
        throw err;
      });
    }
    return this.initPromise;
  }

  private async doInitialize(): Promise<void> {
    this.sessionId = null;
    const { res, body } = await this.post({
      jsonrpc: "2.0",
      id: this.nextId++,
      method: "initialize",
      params: { protocolVersion: MCP_PROTOCOL_VERSION, capabilities: {}, clientInfo: CLIENT_INFO },
    });
    if (!res.ok || !body?.result) {
      throw new McpTransportError(body?.error?.message ?? `initialize failed (HTTP ${res.status})`, res.status, body?.error?.code);
    }
    const result = body.result as InitializeResult;
    const sid = res.headers.get("Mcp-Session-Id");
    if (!sid) {
      throw new McpTransportError(
        "initialize succeeded but the Mcp-Session-Id header is not readable (missing Access-Control-Expose-Headers?)",
        res.status,
      );
    }
    this.sessionId = sid;
    this.protocolVersion = result.protocolVersion;
    this.serverInfo = result.serverInfo;
    this.instructions = result.instructions ?? null;

    const notif = await this.post({ jsonrpc: "2.0", method: "notifications/initialized" });
    if (notif.res.status !== 202) {
      throw new McpTransportError(`notifications/initialized expected HTTP 202, got ${notif.res.status}`, notif.res.status);
    }
    const list = await this.post({ jsonrpc: "2.0", id: this.nextId++, method: "tools/list", params: {} });
    const listed = list.body?.result as { tools: McpTool[] } | undefined;
    this.tools = listed?.tools ?? [];
  }

  async ensureSession(): Promise<void> {
    if (!this.sessionId) await this.initialize();
  }

  /** Raw JSON-RPC request with automatic re-initialization when the session was terminated (HTTP 404). */
  async request<T>(method: string, params: Record<string, unknown>, tool?: string): Promise<{ result: T; latencyMs: number; httpStatus: number }> {
    await this.ensureSession();
    let attempt = await this.post({ jsonrpc: "2.0", id: this.nextId++, method, params }, tool);
    if (attempt.res.status === 404) {
      await this.initialize(true);
      attempt = await this.post({ jsonrpc: "2.0", id: this.nextId++, method, params }, tool);
    }
    const { res, body, latencyMs } = attempt;
    if (body?.error) throw new McpTransportError(body.error.message, res.status, body.error.code);
    if (!res.ok || !body) throw new McpTransportError(`MCP request ${method} failed (HTTP ${res.status})`, res.status);
    return { result: body.result as T, latencyMs, httpStatus: res.status };
  }

  /** Re-fetch tools/list (session-scoped synthesized tools appear here after notifications/tools/list_changed). */
  async refreshTools(): Promise<McpTool[]> {
    const { result } = await this.request<{ tools: McpTool[] }>("tools/list", {});
    this.tools = result.tools ?? [];
    return this.tools;
  }

  async ping(): Promise<number> {
    const { latencyMs } = await this.request<Record<string, never>>("ping", {});
    return latencyMs;
  }

  /**
   * tools/call. Parses result.isError (SEP-1303 tool execution errors) and the JSON payload. The
   * backend returns a human headline in content[0].text, the serialized JSON in content[1].text and
   * the same object as structuredContent; we accept any of them.
   */
  async callTool<T>(name: string, args: Record<string, unknown>): Promise<ToolCallOutcome<T>> {
    const { result, latencyMs, httpStatus } = await this.request<CallToolResult>("tools/call", { name, arguments: args }, name);
    let payload: unknown = result.structuredContent;
    if (payload === undefined) {
      for (const block of result.content ?? []) {
        const parsed = block.type === "text" ? tryParseJson(block.text) : undefined;
        if (parsed && typeof parsed === "object") {
          payload = parsed;
          break;
        }
      }
    }
    const headline = result.content?.[0]?.text ?? "";
    return {
      tool: name,
      isError: Boolean(result.isError),
      data: result.isError ? null : ((payload as T) ?? null),
      error: result.isError ? ((payload as ToolErrorPayload) ?? { error_code: "UNKNOWN", message: headline }) : null,
      headline,
      raw: result,
      latencyMs,
      httpStatus,
    };
  }

  async terminate(): Promise<void> {
    if (!this.sessionId) return;
    try {
      await fetch(MCP_ENDPOINT, { method: "DELETE", headers: { "Mcp-Session-Id": this.sessionId } });
    } finally {
      this.sessionId = null;
      this.initPromise = null;
    }
  }
}

/** Single shared client for the whole app. */
export const mcp = new McpClient();

// ------------------------------------------------------------------------- tool wrappers

export function createWebTask(goal: string, opts: { scenario?: Scenario; target_url?: string; parameters?: Record<string, unknown> } = {}) {
  const args: Record<string, unknown> = { goal };
  if (opts.scenario && opts.scenario !== "auto") args.scenario = opts.scenario;
  if (opts.target_url) args.target_url = opts.target_url;
  if (opts.parameters) args.parameters = opts.parameters;
  return mcp.callTool<TaskSnapshot>("create_web_task", args);
}

export const getTaskStatus = (taskId: string) => mcp.callTool<TaskSnapshot>("get_task_status", { task_id: taskId });
export const inspectWebsite = (url: string, taskId?: string) =>
  mcp.callTool<InspectResult>("inspect_website", taskId ? { url, task_id: taskId } : { url });
export const requestUserApproval = (taskId: string, approvalId?: string) =>
  mcp.callTool<ApprovalRequestResult>("request_user_approval", approvalId ? { task_id: taskId, approval_id: approvalId } : { task_id: taskId });
export const executeApprovedStep = (taskId: string, approvalId: string, approvalToken: string) =>
  mcp.callTool<TaskSnapshot>("execute_approved_step", { task_id: taskId, approval_id: approvalId, approval_token: approvalToken });
export const verifyTaskResult = (taskId: string) => mcp.callTool<VerifyResult>("verify_task_result", { task_id: taskId });
export const getExecutionEvidence = (taskId: string) => mcp.callTool<EvidenceList>("get_execution_evidence", { task_id: taskId });
export const cancelWebTask = (taskId: string, reason?: string) =>
  mcp.callTool<TaskSnapshot>("cancel_web_task", reason ? { task_id: taskId, reason } : { task_id: taskId });
export const interruptViaMcp = (taskId: string, instruction: string) =>
  mcp.callTool<InterruptResponse>("interrupt_web_task", { task_id: taskId, instruction });
export const synthesizeCustomTool = (goal: string, sourceUrl?: string, window?: number) => {
  const args: Record<string, unknown> = { goal };
  if (sourceUrl) args.source_url = sourceUrl;
  if (window) args.window = window;
  return mcp.callTool<{ tool: McpTool; dry_run: SyntheticRunOutput; pipeline_listing: string[] }>("synthesize_custom_tool", args);
};
export const callSyntheticTool = (name: string, args: Record<string, unknown>) => mcp.callTool<SyntheticRunOutput>(name, args);

// --------------------------------------------------------------------------- REST helpers

async function rest<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    throw new BackendError(`Cannot reach the WebPilot backend at ${BACKEND_URL} (${(err as Error).message})`, 0);
  }
  const body = (await res.json().catch(() => null)) as (T & Partial<ApprovalDecisionError>) | null;
  if (!res.ok) {
    const detail = body as unknown as { detail?: string; message?: string; error_code?: string } | null;
    throw new BackendError(detail?.message ?? detail?.detail ?? `HTTP ${res.status} on ${path}`, res.status, detail?.error_code);
  }
  return body as T;
}

export const fetchHealth = () => rest<HealthResponse>("/health");
export const fetchTask = (taskId: string) => rest<TaskDetailResponse>(`/api/tasks/${encodeURIComponent(taskId)}`);
export const fetchTasks = (limit = 20) => rest<TaskListResponse>(`/api/tasks?limit=${limit}`);
export const fetchMcpLogs = (limit = 50) => rest<McpLogsResponse>(`/api/mcp-logs?limit=${limit}`);
export const fetchOrders = () => rest<OrdersResponse>("/sandbox/bytebazaar/orders");
export const fetchBrainStatus = () => rest<BrainStatus>("/api/brain-status");

// ------------------------------------------------- conversations, sign-in, saved logins (local UI only; never MCP)
export const sendSignInChoice = (
  taskId: string,
  body: { choice: "self" | "provide" | "signup" | "cancel"; username?: string; password?: string; remember?: boolean },
) => rest<{ accepted: boolean }>(`/api/tasks/${encodeURIComponent(taskId)}/sign-in`, { method: "POST", body: JSON.stringify(body) });
export const endConversation = (conversationId: string) =>
  rest<{ ended: boolean; tasks: string[]; tab_closed: boolean }>(`/api/conversations/${encodeURIComponent(conversationId)}/end`, { method: "POST" });
export const fetchBrowserStatus = () => rest<BrowserStatus>("/api/browser/status");
export const launchWebPilotChrome = () => rest<{ running: boolean; launched: boolean; detail: string }>("/api/browser/launch", { method: "POST" });
export const fetchSavedLogins = () => rest<{ vault_available: boolean; accounts: SavedAccount[] }>("/api/credentials");
export const saveLogin = (site: string, username: string, password: string) =>
  rest<{ saved: boolean; account: SavedAccount }>("/api/credentials", { method: "POST", body: JSON.stringify({ site, username, password }) });
export const deleteSavedLogin = (site: string) => rest<{ deleted: boolean }>(`/api/credentials/${encodeURIComponent(site)}`, { method: "DELETE" });
export const fetchSettings = () => rest<UserSettings>("/api/settings");
export const saveSettings = (settings: { email?: string; full_name?: string }) =>
  rest<UserSettings>("/api/settings", { method: "POST", body: JSON.stringify(settings) });

// ---------------------------------------------------------------- MCP resources & prompts (JSON-RPC)
export const listResources = () => mcp.request<{ resources: McpResource[] }>("resources/list", {});
export const listResourceTemplates = () => mcp.request<{ resourceTemplates: McpResourceTemplate[] }>("resources/templates/list", {});
export const readResource = (uri: string) => mcp.request<McpResourceContents>("resources/read", { uri });
export const listPrompts = () => mcp.request<{ prompts: McpPrompt[] }>("prompts/list", {});
export const getPrompt = (name: string, args: Record<string, string>) =>
  mcp.request<McpPromptResult>("prompts/get", { name, arguments: args });

/** Mid-task change of mind (REST twin of interrupt_web_task). HTTP 202 = queued for a running task. */
export function interruptTask(taskId: string, instruction: string, constraints?: Record<string, unknown>) {
  return rest<InterruptResponse>(`/api/tasks/${encodeURIComponent(taskId)}/interrupt`, {
    method: "POST",
    body: JSON.stringify(constraints ? { instruction, constraints } : { instruction }),
  });
}

/** Attack lab: publish a poisoned ElectroHub copy carrying a judge-supplied payload. */
export function createAttackPage(payload: string, technique: AttackTechnique) {
  return rest<AttackPageResponse>("/sandbox/attack-lab/pages", { method: "POST", body: JSON.stringify({ payload, technique }) });
}

export function setPriceTamper(enabled: boolean) {
  return rest<PriceChangeResponse>(`/sandbox/bytebazaar/simulate-price-change?enabled=${enabled}`, { method: "POST" });
}

/** Human decision via the REST gate. This is the ONLY way an approval_token is ever minted. */
export function postApprovalDecision(approvalId: string, decision: "APPROVE" | "REJECT") {
  return rest<ApprovalDecisionResponse>(`/api/approvals/${encodeURIComponent(approvalId)}/decide`, {
    method: "POST",
    body: JSON.stringify({ decision, decided_by: "alexa-plus-web-simulator" }),
  });
}

export interface DecisionResult {
  decision: ApprovalDecisionResponse;
  execution: ToolCallOutcome<TaskSnapshot> | null;
}

/**
 * The user taps Approve/Reject on the Alexa+ screen. Approving obtains the HMAC approval_token from
 * the backend and immediately invokes execute_approved_step over MCP with that token.
 */
export async function decideApproval(
  taskId: string,
  approvalId: string,
  decision: "APPROVED" | "REJECTED" | "APPROVE" | "REJECT",
): Promise<DecisionResult> {
  const normalized = decision.startsWith("APPROVE") ? "APPROVE" : "REJECT";
  const decided = await postApprovalDecision(approvalId, normalized);
  if (normalized === "REJECT" || !decided.approval_token) return { decision: decided, execution: null };
  const execution = await executeApprovedStep(taskId, approvalId, decided.approval_token);
  return { decision: decided, execution };
}

/** Simulates a compromised / hallucinating LLM calling the destructive tool with a fabricated token. */
export function testForgedTokenAttack(taskId: string, approvalId: string) {
  return executeApprovedStep(taskId, approvalId, FORGED_TOKEN);
}

export interface PriceDriftResult {
  tamper: PriceChangeResponse;
  decision: ApprovalDecisionResponse | null;
  execution: ToolCallOutcome<TaskSnapshot> | null;
  restored: PriceChangeResponse | null;
}

/**
 * The merchant raises the price (₹74,990 -> ₹86,990) after the checkout was locked, the user approves
 * the ORIGINAL parameters, and the agent tries to execute. The backend re-reads the live page,
 * detects DOM_STATE_DRIFT_DETECTED and revokes the approval. The store price is restored afterwards.
 */
export async function testPriceDriftAttack(taskId: string, approvalId: string): Promise<PriceDriftResult> {
  const tamper = await setPriceTamper(true);
  let decision: ApprovalDecisionResponse | null = null;
  let execution: ToolCallOutcome<TaskSnapshot> | null = null;
  let restored: PriceChangeResponse | null = null;
  try {
    const result = await decideApproval(taskId, approvalId, "APPROVED");
    decision = result.decision;
    execution = result.execution;
  } finally {
    restored = await setPriceTamper(false).catch(() => null);
  }
  return { tamper, decision, execution, restored };
}

export async function resetDemoState(): Promise<ResetDemoResponse> {
  const result = await rest<ResetDemoResponse>("/api/reset-demo", {
    method: "POST",
    body: JSON.stringify({ include_mcp_logs: true, include_sessions: true }),
  });
  await mcp.initialize(true);
  return result;
}

/** Absolute URL for backend-served artifacts (screenshots are stored absolute, but be defensive). */
export function artifactUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.startsWith("/") ? `${BACKEND_URL}${url}` : url;
}
