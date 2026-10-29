/**
 * TypeScript contracts mirroring the WebPilot FastAPI backend exactly.
 *
 * Sources of truth:
 *  - backend/models.py          enums (TaskStatus, StepStatus, ApprovalStatus, ...)
 *  - backend/db.py              row -> dict shapes (_task_row_to_dict, _approval_row_to_dict, _evidence_row_to_dict)
 *  - backend/approval_guard.py  public_view() (adds approval_url, decision_endpoint, ttl_seconds, expires_at)
 *  - backend/planner_engine.py  extracted_data per scenario + task_snapshot()
 *  - backend/mcp_server.py      JSON-RPC envelopes, tool definitions, tool results
 *  - backend/main.py            REST payloads
 */

// ----------------------------------------------------------------------------- enums

export type TaskCategory = "READ_ANALYZE" | "RESEARCH_COMPARE" | "ACT_WITH_APPROVAL";

export type TaskStatus =
  | "PLANNED"
  | "EXECUTING"
  | "AWAITING_APPROVAL"
  | "AWAITING_USER_INPUT"
  | "BLOCKED"
  | "COMPLETED"
  | "UNVERIFIED_OUTCOME"
  | "FAILED"
  | "CANCELLED";

export type EnvironmentMode = "LIVE_WEB" | "CONTROLLED_SANDBOX" | "HYBRID";
export type ActionType = "PURCHASE_CHECKOUT" | "FORM_SUBMISSION" | "ACCOUNT_MUTATION";
export type ApprovalStatus = "PENDING" | "USER_APPROVED" | "USER_REJECTED" | "CONSUMED" | "REVOKED" | "EXPIRED";
export type CapabilityTier = "TIER_1_ADAPTER" | "TIER_2_SEMANTIC_DOM" | "TIER_3_HITL_ESCALATION" | "INTERNAL_COMPUTE";

export type StepStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "FAILED_RECOVERED"
  | "SKIPPED"
  | "BLOCKED"
  | "AWAITING_APPROVAL"
  | "CANCELLED";

export type VerificationVerdict =
  | "VERIFIED_SUCCESS"
  | "SIMULATED_TRANSACTION_VERIFIED"
  | "VERIFICATION_FAILED"
  | "LIVE_CHECKOUT_HANDOFF_VERIFIED"
  | "UNVERIFIED";

export type Scenario =
  | "auto"
  | "attendance_audit"
  | "profile_comparison"
  | "laptop_shopping"
  | "grant_form"
  | "github_repo_analysis"
  | "url_inspection"
  | "synthesized_tool"
  | "live_autopilot";

// ------------------------------------------------------------------------ core records

export interface PlanStep {
  step_id: string;
  key: string;
  index: number;
  title: string;
  capability_tier: CapabilityTier;
  status: StepStatus;
  target_url: string | null;
  detail: string | null;
  evidence_ids: string[];
  started_at: string | null;
  finished_at: string | null;
}

export interface FirewallAlert {
  alert_id: string;
  alert_type: "HIDDEN_DOM_INJECTION_BLOCKED" | "PROMPT_INJECTION_PATTERN_NEUTRALIZED";
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  source_url: string;
  element_selector: string | null;
  hiding_technique: string | null;
  matched_patterns: string[];
  snippet: string;
  action_taken: string;
}

export interface VerificationCheck {
  check: string;
  passed: boolean;
  expected: unknown;
  observed: unknown;
}

export interface VerificationReport {
  verdict: VerificationVerdict;
  method: string;
  checks: VerificationCheck[];
  notes: string | null;
  verified_at: string;
  confirmation_id?: string | null;
  confirmation_url?: string;
  reverified?: boolean;
}

/** Approval as exposed by approval_guard.public_view() — the token is only present once a human approved. */
export interface ApprovalView {
  approval_id: string;
  task_id: string;
  step_id: string;
  action_type: ActionType;
  action_summary: string;
  parameters: Record<string, unknown>;
  parameter_hash: string;
  status: ApprovalStatus;
  preview_screenshot_url: string | null;
  created_at: string;
  decided_at: string | null;
  consumed_at: string | null;
  approval_url: string;
  decision_endpoint: string;
  ttl_seconds: number;
  expires_at: string | null;
  approval_token?: string | null;
}

export interface EvidenceRecord {
  evidence_id: string;
  task_id: string;
  step_id: string;
  source_url: string;
  page_title: string | null;
  capability_method: string;
  extracted: unknown;
  dom_citation_snippet: string | null;
  screenshot_url: string | null;
  firewall_alerts: FirewallAlert[];
  verification_passed: boolean;
  captured_at: string;
}

export interface InterpretedGoal {
  compiler: string;
  scenario: Exclude<Scenario, "auto">;
  matched_signals: string[];
  target_url: string | null;
  parameters: Record<string, unknown>;
  constraints: Record<string, unknown>;
  task_category: TaskCategory;
  environment_mode: EnvironmentMode;
  intent: string;
  store_urls?: string[];
  brain?: BrainRecord;
  brain_provider_used?: BrainProviderId;
  ai_reasoning_trace?: string[];
  extraction_query?: string;
}

export type BrainProviderId = "aws_bedrock" | "ollama_local" | "cloud_llm" | "deterministic_symbolic";

export interface BrainRecord {
  provider_used: BrainProviderId;
  provider_label: string;
  extraction_query: string;
  risk_level: "read_only" | "consequential";
  reasoning_trace: string[];
}

export interface Task {
  task_id: string;
  idempotency_key: string | null;
  goal: string;
  task_category: TaskCategory;
  status: TaskStatus;
  environment_mode: EnvironmentMode;
  interpreted_goal: InterpretedGoal;
  plan_steps: PlanStep[];
  extracted_data: Record<string, unknown> | null;
  pending_approval: ApprovalView | null;
  verification_report: VerificationReport | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

/** planner.task_snapshot() — returned by create_web_task, get_task_status, cancel_web_task, execute_approved_step. */
export interface InterruptionRecord {
  interrupt_id: string;
  task_id: string;
  instruction: string;
  delta: Record<string, unknown>;
  changes: string[];
  status: "QUEUED" | "APPLIED" | "REJECTED";
  outcome: string | null;
  created_at: string;
  applied_at: string | null;
}

export interface TaskSnapshot {
  task: Task;
  interruptions?: InterruptionRecord[];
  approvals: ApprovalView[];
  evidence_count: number;
  voice_summary: string | null;
  next_action: string | null;
  idempotent_replay?: boolean;
  executed?: boolean;
  confirmation_id?: string | null;
  message?: string;
  revoked_approvals?: number;
}

// ------------------------------------------------------------- scenario extracted_data

export interface AttendanceSubject {
  code: string;
  subject: string;
  attended: number;
  conducted: number;
  displayed_pct: number | null;
  percentage: number;
  below_threshold: boolean;
  classes_needed: number;
}

export interface AttendanceData {
  student: string | null;
  threshold_pct: number;
  formula: string;
  subjects: AttendanceSubject[];
  below_threshold: AttendanceSubject[];
  source_url: string;
}

export interface LaptopCandidate {
  sku: string;
  name: string;
  seller: string;
  price_inr: number;
  ram_gb: number;
  rating: number | null;
  delivery: string | null;
  stock_note: string | null;
  checkout_state: string | null;
  specs: string | null;
  checkout_url: string | null;
  source_url: string;
  selector: string | null;
  qualifies: boolean;
  rejection_reasons: string[];
  rank?: number;
}

export interface RecoveryLogEntry {
  failed_candidate: string;
  seller: string;
  error_code: string;
  listing_checkout_state: string | null;
  at: string;
}

export interface ActionOutcome {
  confirmation_id: string | null;
  confirmation_url: string;
  approval_id: string;
  executed_at: string;
}

export interface LaptopData {
  constraints: { max_price_inr: number; min_ram_gb: number; currency: string; prepare_checkout: boolean };
  candidates_evaluated: LaptopCandidate[];
  ranked: LaptopCandidate[];
  firewall_alerts: FirewallAlert[];
  firewall_summary: string;
  recovery_log: RecoveryLogEntry[];
  selected?: LaptopCandidate;
  outcome?: ActionOutcome;
}

export interface FormFieldDef {
  name: string | null;
  id: string | null;
  tag: string;
  type: string;
  label: string | null;
  required: boolean;
  maxlength: number | null;
  selector: string;
  value?: string;
  options?: string[];
}

export interface FieldMapping {
  field: string;
  label: string | null;
  value: string | null;
  source: "user_parameters" | "demo_applicant_profile" | null;
  required: boolean;
}

export interface GrantFormData {
  form: {
    form_id: string;
    action: string;
    method: string;
    fields: FormFieldDef[];
    submit_selector: string | null;
    submit_text: string | null;
  };
  field_mapping: FieldMapping[];
  values?: Record<string, string>;
  problems?: string[];
  outcome?: ActionOutcome;
}

export interface GithubIssue {
  number: number;
  title: string;
  html_url: string;
  labels: string[];
  comments: number | null;
  updated_at: string | null;
}

export interface GithubData {
  source: "GITHUB_REST_API" | "GITHUB_HTML_FALLBACK";
  owner: string;
  repo: string;
  full_name: string;
  description: string | null;
  html_url: string;
  homepage?: string | null;
  stars: number | null;
  forks: number | null;
  watchers?: number | null;
  open_issues_and_prs: number | null;
  language: string | null;
  languages_pct?: Record<string, number>;
  license?: string | null;
  default_branch?: string | null;
  topics?: string[];
  created_at?: string | null;
  pushed_at?: string | null;
  archived?: boolean | null;
  architecture: {
    top_level_directories: string[];
    top_level_files: string[];
    src_components: string[] | null;
    detected_stack: string[];
  };
  architecture_summary?: string;
  open_issues_sample: GithubIssue[];
  api_rate_limit_remaining?: string | null;
}

export interface DeveloperProfile {
  username: string;
  display_name: string;
  platform: string;
  easy: number;
  medium: number;
  hard: number;
  total_solved: number;
  contest_rating: number | null;
  global_ranking: number | null;
  streak_days: number | null;
  source_url: string;
  capability_method: string;
}

export interface MetricComparison {
  user: number | null;
  friend: number | null;
  leader: string | null;
  gap: number | null;
}

export interface ProfileData {
  profiles: DeveloperProfile[];
  comparison: Record<"easy" | "medium" | "hard" | "total_solved" | "contest_rating", MetricComparison>;
  biggest_gap_difficulty: string | null;
  sources: string[];
}

export interface Barrier {
  barrier_type: "LOGIN_REQUIRED" | "OTP_2FA_REQUIRED" | "CAPTCHA_CHALLENGE" | "BOT_CHALLENGE";
  selector: string;
  evidence: string;
  requires_human: boolean;
}

export interface Heading {
  level: number;
  text: string;
}

export interface LinkRef {
  text: string;
  href: string;
}

export interface DistilledTable {
  table_id: string;
  aria_label: string | null;
  caption: string | null;
  headers: string[];
  rows: string[][];
  records: Record<string, string>[];
  row_attributes: Record<string, unknown>[];
  row_count: number;
}

export interface InteractiveElement {
  tag: string;
  role: string | null;
  id: string | null;
  name: string | null;
  type: string | null;
  text: string;
  label: string | null;
  aria_label: string | null;
  required: boolean;
  disabled: boolean;
  selector: string;
  href?: string;
  options?: { value: string; text: string }[];
}

export interface ArticleItem {
  title: string;
  url: string | null;
  selector: string;
  points?: number | null;
  rank?: string;
}

export interface PricePoint {
  currency: string;
  amount: number;
  raw: string;
  period: string | null;
  context: string;
}

export interface StructuredData {
  json_ld: unknown[];
  open_graph: Record<string, string>;
}

export type RenderMode = "live_viewport" | "rendered_html" | "svg_snapshot" | "raw_feed";

// ------------------------------------------------------------- universal live autopilot

export type AutopilotAction =
  | "NAVIGATE"
  | "FILL_AND_SUBMIT"
  | "FILL"
  | "CLICK"
  | "SCROLL_DOWN"
  | "ASK_HUMAN_APPROVAL"
  | "APPROVED_CLICK"
  | "HANDOFF"
  | "WAIT_FOR_HUMAN"
  | "ASK_LOGIN"
  | "LOGIN"
  | "SIGNUP"
  | "SELECT"
  | "BLOCKED"
  | "DONE";

export interface AutopilotFrame {
  step: number;
  action: AutopilotAction;
  description: string;
  screenshot_url: string | null;
  pre_screenshot_url?: string | null;
  url_before: string;
  url_after: string;
  page_title: string;
  verified: boolean;
  at: string;
  element_idx?: number;
  decided_by?: string;
  approval_id?: string;
}

export interface AutopilotDecision {
  step: number;
  action: AutopilotAction;
  element_idx: number | null;
  text: string | null;
  reason: string;
  decided_by: string;
  llm_note: string | null;
  phase: string;
}

export interface LiveOption {
  title: string;
  url: string | null;
  price?: string | null;
  screenshot_url?: string | null;
}

export interface LoginRequest {
  site: string;
  url: string;
  kind: "login" | "login_identifier" | "signup" | "otp";
  has_saved_login: boolean;
  signup_email: string | null;
  screenshot_url: string | null;
  requested_at: string;
  options: ("self" | "provide" | "signup")[];
}

export interface VariantReport {
  attribute: string;
  value: string;
  found: boolean;
  available: boolean | null;
  evidence: string;
}

export interface LiveAutopilotData {
  chat_reply?: string;
  options?: LiveOption[];
  variant?: VariantReport | null;
  login_request?: LoginRequest;
  human_needed?: { message: string; url: string; since: string };
  screenshots_deleted?: boolean;
  autopilot: {
    start_url: string;
    discovery: boolean;
    conversation_id?: string | null;
    continued?: boolean;
    mode: { headed: boolean; persistent: boolean; connection?: "cdp" | "persistent_profile" | "ephemeral"; reused_tab?: boolean; open_tabs?: number };
    filmstrip: AutopilotFrame[];
    decisions: AutopilotDecision[];
  };
  answer?: GroundedAnswer | null;
  url?: string;
  title?: string;
  blocker?: { barriers: Barrier[]; bypass_attempted: boolean; url: string };
  handoff?: { url: string; button_text: string };
  outcome?: { handoff_url: string; landing: { payment_step: boolean; final_payment_controls: string[]; confirmation: boolean } };
}

export interface UrlInspectionData {
  url: string;
  title: string;
  articles?: ArticleItem[];
  prices?: PricePoint[];
  structured_data?: StructuredData | null;
  capability_method?: string;
  render_mode?: RenderMode;
  fallback_reason?: string | null;
  screenshot_url?: string | null;
  answer?: GroundedAnswer | null;
  status_code?: number;
  meta_description?: string | null;
  headings?: Heading[];
  links?: LinkRef[];
  tables?: DistilledTable[];
  forms?: GrantFormData["form"][];
  interactive_elements?: InteractiveElement[];
  firewall_alerts?: FirewallAlert[];
  untrusted_text_excerpt?: string;
  blocker?: {
    barriers: Barrier[];
    required_human_actions: string[];
    bypass_attempted: boolean;
  };
}

/** Result of the inspect_website MCP tool. */
export interface InspectResult {
  url: string;
  status_code: number;
  title: string;
  capability_method: string;
  screenshot_url: string | null;
  blocked: boolean;
  barriers: Barrier[];
  blocking_barriers: Barrier[];
  human_action_required: string[];
  headings: Heading[];
  tables: DistilledTable[];
  forms: GrantFormData["form"][];
  interactive_elements: InteractiveElement[];
  product_cards: Record<string, unknown>[];
  articles: ArticleItem[];
  prices: PricePoint[];
  structured_data: StructuredData | null;
  render_mode: RenderMode;
  fallback_reason: string | null;
  links: LinkRef[];
  firewall_alerts: FirewallAlert[];
  firewall_summary: string;
  untrusted_content: string;
  text_truncated: boolean;
}

export interface EvidenceList {
  task_id: string;
  count: number;
  evidence: EvidenceRecord[];
}

export interface VerifyResult {
  task_id: string;
  status: TaskStatus;
  verification_report: VerificationReport;
}

export interface ApprovalRequestResult {
  task_id: string;
  task_status: TaskStatus;
  approval: ApprovalView;
  next_action: string;
}

// ------------------------------------------------------------------------- MCP protocol

export interface JsonRpcError {
  code: number;
  message: string;
  data?: unknown;
}

export interface JsonRpcResponse<T = unknown> {
  jsonrpc: "2.0";
  id: number | string | null;
  result?: T;
  error?: JsonRpcError;
}

export interface McpToolAnnotations {
  title?: string;
  readOnlyHint: boolean;
  destructiveHint: boolean;
  idempotentHint: boolean;
  openWorldHint: boolean;
}

export interface McpIcon {
  src: string;
  mimeType: string;
  sizes: string[];
}

export interface McpTool {
  _meta?: { "webpilot/synthesized"?: boolean; "webpilot/pipeline"?: string[] };
  name: string;
  title: string;
  description: string;
  inputSchema: {
    $schema: string;
    type: "object";
    properties: Record<string, Record<string, unknown>>;
    required: string[];
    additionalProperties: boolean;
  };
  annotations: McpToolAnnotations;
  icons?: McpIcon[];
}

export interface McpServerInfo {
  name: string;
  title: string;
  version: string;
  description: string;
  websiteUrl?: string;
  icons?: McpIcon[];
}

export interface InitializeResult {
  protocolVersion: string;
  capabilities: { tools?: { listChanged: boolean } };
  serverInfo: McpServerInfo;
  instructions?: string;
}

export interface McpTextContent {
  type: "text";
  text: string;
}

export interface CallToolResult<T = unknown> {
  content: McpTextContent[];
  structuredContent?: T;
  isError: boolean;
}

/** structuredContent of a tool execution error (SEP-1303). */
export interface ToolErrorPayload {
  error_code: string;
  message: string;
  hint?: string;
  details?: Record<string, unknown>;
}

export interface ToolCallOutcome<T> {
  tool: string;
  isError: boolean;
  data: T | null;
  error: ToolErrorPayload | null;
  headline: string;
  raw: CallToolResult;
  latencyMs: number;
  httpStatus: number;
}

/** One row of GET /api/mcp-logs (backend/db.py list_mcp_logs). */
export interface McpLogFrame {
  log_id: number;
  session_id: string | null;
  method: string;
  tool_name: string | null;
  request: unknown;
  response: JsonRpcResponse<CallToolResult> | Record<string, unknown> | null;
  status_code: number;
  latency_ms: number;
  timestamp: string;
}

export interface McpLogsResponse {
  count: number;
  logs: McpLogFrame[];
}

// ------------------------------------------------------------------------------- REST

export interface ApprovalDecisionResponse {
  approval_id: string;
  task_id: string;
  status: ApprovalStatus;
  approval_token: string | null;
  parameter_hash: string;
  decided_at: string | null;
  message: string;
}

export interface ApprovalDecisionError {
  error_code: string;
  message: string;
}

export interface ResetDemoResponse {
  reset: boolean;
  deleted: Record<string, number>;
  artifacts_removed: number;
  sandbox_state: string;
}

export interface PriceChangeResponse {
  price_tampered: boolean;
  sku: string;
  current_price_inr: number;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  version: string;
  mcp: {
    protocolVersion: string;
    supportedProtocolVersions: string[];
    transport: string;
    endpoint: string;
    tools: string[];
  };
  database: { ok: boolean; path: string; journal_mode: string };
  browser_runtime: {
    playwright_enabled: boolean;
    playwright_browser_running: boolean;
    playwright_last_error: string | null;
    fallback: string;
    headless: boolean;
  };
  github_token_configured: boolean;
}

export interface TaskDetailResponse extends TaskSnapshot {
  evidence: EvidenceRecord[];
}

export interface TaskListResponse {
  count: number;
  tasks: Task[];
}

// --------------------------------------------------------------------- UI-only models

export type SecurityProofKind = "FORGED_TOKEN" | "PRICE_DRIFT";

export interface SecurityProofEvent {
  id: string;
  kind: SecurityProofKind;
  at: string;
  blocked: boolean;
  errorCode: string | null;
  message: string;
  details?: Record<string, unknown>;
}

export interface ClientFrame {
  id: number;
  at: string;
  method: string;
  tool?: string;
  requestHeaders: Record<string, string>;
  responseHeaders: Record<string, string>;
  status: number;
  latencyMs: number;
}

// ------------------------------------------------------------- synthesized tools

export interface PipelineOp {
  op: string;
  [key: string]: unknown;
}

export interface SyntheticToolSpec {
  name: string;
  title: string;
  description: string;
  goal: string;
  source: { url: string; format: string; resolution: string };
  column_hint: string | null;
  pipeline: PipelineOp[];
  signals: string[];
  compiler: string;
  execution_model: string;
}

export interface SyntheticResult {
  columns: string[];
  numeric_columns: string[];
  target_columns: string[];
  added_columns: string[];
  row_count: number;
  rows: Record<string, string | number | null>[];
  trend: Record<string, number | null>;
  ranking: { column: string; pct_change: number; direction: "up" | "down" | "flat" }[];
  aggregates: Record<string, { min: number | null; max: number | null; mean: number | null; sum: number | null; first: number | null; last: number | null; count: number }>;
  trace: string[];
}

export interface SynthesizedData {
  tool: {
    name: string;
    definition: McpTool;
    spec: SyntheticToolSpec;
    pipeline_listing: string[];
    session_id: string | null;
    expires_at: string;
    registered_for_session: boolean;
  };
  source_url: string;
  format: string;
  record_count: number;
  result: SyntheticResult;
  firewall_alerts: FirewallAlert[];
}

export interface SyntheticRunOutput {
  tool: string;
  source_url: string;
  format: string;
  capability_method: string;
  firewall_alerts: FirewallAlert[];
  result: SyntheticResult;
  verification_report: VerificationReport;
  summary: string;
}

// ------------------------------------------------------------- interruptions

export interface InterruptResponse extends TaskSnapshot {
  interrupt: InterruptionRecord;
  mode: "APPLIED" | "QUEUED" | "REJECTED";
  changes: string[];
}

// ------------------------------------------------------------- attack lab

export type AttackTechnique =
  | "display_none"
  | "opacity_zero"
  | "font_size_zero"
  | "hidden_attribute"
  | "offscreen"
  | "html_comment"
  | "aria_label"
  | "visible_text";

export interface AttackPageResponse {
  page_id: string;
  technique: AttackTechnique;
  path: string;
  payload_length: number;
}

export interface OrdersResponse {
  store: string;
  count: number;
  orders: { order_id: string; item: string; price_inr: number; approval_ref: string | null; placed_at: string }[];
}

// ------------------------------------------------------------- AI brain & grounded answers

export interface AnswerCitation {
  rank: number;
  item_id: number;
  kind: "article" | "heading" | "table_row" | "price";
  title: string;
  url: string | null;
  points: number | null;
  price?: string | null;
  screenshot_url?: string | null;
  matched_terms: string[];
  dom_ref: string;
}

export interface SavedAccount {
  site: string;
  username: string;
  source: "user" | "created";
  verified: boolean;
  created_at: string;
  last_used_at: string | null;
}

export interface UserSettings {
  email: string | null;
  full_name: string | null;
}

export interface BrowserStatus {
  cdp_url: string;
  running: boolean;
  chrome_path: string | null;
  profile_dir: string;
  autolaunch: boolean;
  launched_by_webpilot: boolean;
  conversations_open: number;
}

export interface GroundedAnswer {
  question: string;
  page_title: string;
  source_url: string | null;
  items_considered: number;
  plan: { terms: string[]; top_n: number; rank_by: "points" | "page_order" | "price_asc" | "price_desc"; summarize: boolean };
  provider_used: BrainProviderId;
  method: string;
  notes: string[];
  answer: string;
  citations: AnswerCitation[];
  matched_count: number;
  filters_applied?: string[];
}

export interface BrainProviderStatus {
  id: BrainProviderId;
  label: string;
  available: boolean;
  detail: string;
}

export interface BrainStatus {
  active_provider: BrainProviderId;
  active_label: string;
  active_model: string;
  mode: string;
  verifier: string;
  providers: BrainProviderStatus[];
  bedrock: { configured: boolean; boto3_installed: boolean; region: string; model_id: string; api: string };
  ollama: { reachable: boolean; models: string[]; selected_model: string | null; host: string };
  cloud: { configured: boolean; vendor: string | null; model: string | null };
  last_error: string | null;
}

// ------------------------------------------------------------- MCP resources & prompts

export interface McpResource {
  uri: string;
  name: string;
  title?: string;
  description?: string;
  mimeType?: string;
}

export interface McpResourceTemplate {
  uriTemplate: string;
  name: string;
  title?: string;
  description?: string;
  mimeType?: string;
}

export interface McpResourceContents {
  contents: { uri: string; mimeType: string; text: string }[];
}

export interface McpPromptArgument {
  name: string;
  description?: string;
  required?: boolean;
}

export interface McpPrompt {
  name: string;
  title?: string;
  description?: string;
  arguments?: McpPromptArgument[];
}

export interface McpPromptResult {
  description?: string;
  messages: { role: "user" | "assistant"; content: { type: "text"; text: string } }[];
}
