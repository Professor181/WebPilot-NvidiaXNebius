import type { CapabilityTier, StepStatus, TaskStatus, VerificationVerdict } from "@/types/webpilot";

/** Indian digit grouping, identical to backend sandbox_routes.format_inr (145000 -> ₹1,45,000). */
export function formatInr(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return "—";
  return "₹" + Math.round(amount).toLocaleString("en-IN");
}

export function shortId(id: string | null | undefined, keep = 8): string {
  if (!id) return "—";
  return id.length <= keep + 2 ? id : `${id.slice(0, keep)}…`;
}

export function timeOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function humanize(value: string): string {
  return value
    .replace(/^TIER_\d_/, "")
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export const TASK_STATUS_STYLE: Record<TaskStatus, string> = {
  PLANNED: "bg-slate-700/40 text-slate-200 ring-slate-500/40",
  EXECUTING: "bg-cyan-500/15 text-cyan-200 ring-cyan-400/40",
  AWAITING_APPROVAL: "bg-amber-500/15 text-amber-200 ring-amber-400/50",
  AWAITING_USER_INPUT: "bg-amber-500/10 text-amber-200 ring-amber-400/40",
  BLOCKED: "bg-rose-500/15 text-rose-200 ring-rose-400/50",
  COMPLETED: "bg-emerald-500/15 text-emerald-200 ring-emerald-400/50",
  UNVERIFIED_OUTCOME: "bg-orange-500/15 text-orange-200 ring-orange-400/50",
  FAILED: "bg-rose-500/15 text-rose-200 ring-rose-400/50",
  CANCELLED: "bg-slate-600/30 text-slate-300 ring-slate-500/40",
};

export const STEP_STATUS_STYLE: Record<StepStatus, { dot: string; text: string; ring: string }> = {
  PENDING: { dot: "bg-slate-600", text: "text-slate-400", ring: "ring-slate-700" },
  RUNNING: { dot: "bg-cyan-400 animate-pulse", text: "text-cyan-200", ring: "ring-cyan-400/60" },
  SUCCEEDED: { dot: "bg-emerald-400", text: "text-emerald-200", ring: "ring-emerald-500/50" },
  FAILED: { dot: "bg-rose-500", text: "text-rose-200", ring: "ring-rose-500/60" },
  FAILED_RECOVERED: { dot: "bg-amber-400", text: "text-amber-200", ring: "ring-amber-400/60" },
  SKIPPED: { dot: "bg-slate-700", text: "text-slate-500", ring: "ring-slate-700" },
  BLOCKED: { dot: "bg-rose-500", text: "text-rose-200", ring: "ring-rose-500/60" },
  AWAITING_APPROVAL: { dot: "bg-amber-400 animate-pulse", text: "text-amber-200", ring: "ring-amber-400/70" },
  CANCELLED: { dot: "bg-slate-600", text: "text-slate-400", ring: "ring-slate-600" },
};

export const TIER_STYLE: Record<CapabilityTier, { label: string; cls: string }> = {
  TIER_1_ADAPTER: { label: "Tier 1 · API Adapter", cls: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/40" },
  TIER_2_SEMANTIC_DOM: { label: "Tier 2 · Semantic DOM", cls: "bg-cyan-500/10 text-cyan-300 ring-cyan-500/40" },
  TIER_3_HITL_ESCALATION: { label: "Tier 3 · Human-in-the-Loop", cls: "bg-amber-500/10 text-amber-300 ring-amber-500/40" },
  INTERNAL_COMPUTE: { label: "Compute · Verifier", cls: "bg-indigo-500/10 text-indigo-300 ring-indigo-500/40" },
};

export function verdictStyle(verdict: VerificationVerdict | undefined | null): string {
  switch (verdict) {
    case "VERIFIED_SUCCESS":
    case "SIMULATED_TRANSACTION_VERIFIED":
    case "LIVE_CHECKOUT_HANDOFF_VERIFIED":
      return "bg-emerald-500/15 text-emerald-200 ring-emerald-400/50";
    case "VERIFICATION_FAILED":
      return "bg-rose-500/15 text-rose-200 ring-rose-400/50";
    default:
      return "bg-slate-600/30 text-slate-300 ring-slate-500/40";
  }
}

/** Maps an evidence capability_method ("TIER_2_SEMANTIC_DOM:PLAYWRIGHT_CHROMIUM") to a tier. */
export function tierOfMethod(method: string): CapabilityTier {
  if (method.startsWith("TIER_1")) return "TIER_1_ADAPTER";
  if (method.startsWith("TIER_2")) return "TIER_2_SEMANTIC_DOM";
  if (method.startsWith("TIER_3")) return "TIER_3_HITL_ESCALATION";
  return "INTERNAL_COMPUTE";
}
