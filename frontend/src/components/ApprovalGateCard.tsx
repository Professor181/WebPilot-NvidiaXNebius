"use client";

import { useEffect, useState } from "react";
import {
  KeyRound,
  ShieldCheck,
  ShieldX,
  CircleCheck,
  CircleX,
  Clock,
  Copy,
  Check,
  FlaskConical,
  TrendingUp,
  LoaderCircle,
  Lock,
  RotateCcw,
  ExternalLink,
} from "lucide-react";
import type { ApprovalView, SecurityProofEvent, Task } from "@/types/webpilot";
import { artifactUrl } from "@/lib/mcpClient";
import { cx, formatInr, timeOf } from "@/lib/format";

function useCountdown(expiresAt: string | null | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  if (Number.isNaN(ms)) return null;
  const total = Math.max(0, Math.floor(ms / 1000));
  return { total, label: `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}` };
}

function ParamRow({ label, value, strong }: { label: string; value: React.ReactNode; strong?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-amber-500/10 py-1.5 last:border-0">
      <span className="text-xs text-amber-100/60">{label}</span>
      <span className={cx("text-right text-sm", strong ? "font-black text-white" : "font-medium text-amber-50")}>{value}</span>
    </div>
  );
}

function LockedParameters({ approval }: { approval: ApprovalView }) {
  const p = approval.parameters;
  if (p.action === "LIVE_CONSEQUENTIAL_CLICK") {
    const prices = (p.visible_prices as number[] | undefined) ?? [];
    const total = typeof p.display_total === "number" ? p.display_total : null;
    return (
      <div>
        <ParamRow label="Control to click" value={<span className="font-mono">“{String(p.button_text)}”</span>} strong />
        <ParamRow label="Website" value={String(p.domain)} />
        {p.item_heading ? <ParamRow label="Item / page" value={String(p.item_heading)} /> : null}
        {total !== null && (
          <ParamRow
            label="Total shown"
            value={<span className="font-mono text-lg">{`${String(p.currency ?? "")} ${total.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}</span>}
            strong
          />
        )}
        <ParamRow label="Visible prices (locked)" value={<span className="font-mono text-xs">{prices.length ? prices.join(", ") : "none"}</span>} />
        <ParamRow label="Policy" value="Payment is never pressed — you finish it" />
        <ParamRow
          label="Live page"
          value={
            <a href={String(p.page_url)} target="_blank" rel="noreferrer" className="break-all font-mono text-[11px] text-cyan-300 hover:underline">
              {String(p.page_url)}
            </a>
          }
        />
      </div>
    );
  }
  if (approval.action_type === "PURCHASE_CHECKOUT") {
    return (
      <div>
        <ParamRow label="Item" value={String(p.item_name)} strong />
        <ParamRow label="Seller" value={String(p.seller)} />
        <ParamRow label="Total price" value={<span className="font-mono text-lg">{formatInr(Number(p.total_price_inr))}</span>} strong />
        <ParamRow label="Quantity" value={String(p.quantity ?? 1)} />
        <ParamRow label="SKU" value={<span className="font-mono text-xs">{String(p.sku)}</span>} />
        <ParamRow
          label="Target URL"
          value={
            <a href={String(p.target_url)} target="_blank" rel="noreferrer" className="break-all font-mono text-[11px] text-cyan-300 hover:underline">
              {String(p.target_url)}
            </a>
          }
        />
      </div>
    );
  }
  const fields = (p.fields ?? {}) as Record<string, string>;
  return (
    <div>
      {Object.entries(fields).map(([k, v]) => (
        <ParamRow key={k} label={k} value={<span className="line-clamp-2 max-w-[340px] text-xs">{v}</span>} />
      ))}
      <ParamRow label="Submit to" value={<span className="break-all font-mono text-[11px] text-cyan-300">{String(p.method)} {String(p.form_action)}</span>} />
    </div>
  );
}

interface Props {
  task: Task;
  approval: ApprovalView;
  pendingAction: string | null;
  proofEvents: SecurityProofEvent[];
  onApprove: () => void;
  onReject: () => void;
  onForgedToken: () => void;
  onPriceDrift: () => void;
  onRerun: () => void;
}

export default function ApprovalGateCard({ task, approval, pendingAction, proofEvents, onApprove, onReject, onForgedToken, onPriceDrift, onRerun }: Props) {
  const [copied, setCopied] = useState(false);
  const countdown = useCountdown(approval.status === "PENDING" ? approval.expires_at : null);
  const busy = pendingAction !== null;
  const live = approval.status === "PENDING" && task.status === "AWAITING_APPROVAL";
  const preview = artifactUrl(approval.preview_screenshot_url);
  const isPurchase = approval.action_type === "PURCHASE_CHECKOUT" && approval.parameters.action !== "LIVE_CONSEQUENTIAL_CLICK";

  if (approval.status === "CONSUMED") {
    return (
      <section className="flex flex-wrap items-center gap-3 rounded-2xl bg-emerald-500/10 p-4 ring-1 ring-emerald-500/40">
        <ShieldCheck className="h-5 w-5 text-emerald-300" />
        <div className="flex-1 text-sm text-emerald-100">
          Approval <span className="font-mono">{approval.approval_id}</span> was <strong>CONSUMED</strong> at {timeOf(approval.consumed_at)} — single-use, replays
          return the cached verified outcome.
        </div>
        <span className="font-mono text-[10px] text-emerald-300/80">sha256 {approval.parameter_hash.slice(0, 16)}…</span>
      </section>
    );
  }

  const copyHash = async () => {
    await navigator.clipboard?.writeText(approval.parameter_hash).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <section
      className={cx(
        "overflow-hidden rounded-3xl ring-2 animate-fade-up",
        live ? "bg-gradient-to-br from-amber-500/20 via-amber-500/[0.07] to-obsidian-900 ring-amber-400/70 shadow-amber" : "bg-slate-900/80 ring-slate-700",
      )}
    >
      <div className={cx("flex flex-wrap items-center gap-3 px-5 py-3", live ? "bg-amber-500/15" : "bg-slate-800/50")}>
        <span className={cx("rounded-xl p-2", live ? "bg-amber-400 text-obsidian-950" : "bg-slate-700 text-slate-300")}>
          <KeyRound className="h-5 w-5" />
        </span>
        <div className="flex-1">
          <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-amber-300">Cryptographic approval gate · {approval.action_type}</p>
          <p className="text-base font-bold text-white">{approval.action_summary}</p>
        </div>
        <span
          className={cx(
            "status-pill",
            approval.status === "PENDING" ? "bg-amber-400/20 text-amber-100 ring-amber-300/70" : "bg-rose-500/20 text-rose-100 ring-rose-400/60",
          )}
        >
          {approval.status}
        </span>
        {countdown && (
          <span className={cx("flex items-center gap-1 font-mono text-xs", countdown.total < 60 ? "text-rose-300" : "text-amber-200")}>
            <Clock className="h-3.5 w-3.5" /> expires in {countdown.label}
          </span>
        )}
      </div>

      <div className="grid gap-5 p-5 lg:grid-cols-[1.25fr_1fr]">
        <div className="space-y-4">
          <div className="rounded-2xl bg-obsidian-950/70 p-4 ring-1 ring-amber-500/20">
            <p className="mb-1 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-amber-300/90">
              <Lock className="h-3.5 w-3.5" /> Locked parameters
            </p>
            <LockedParameters approval={approval} />
          </div>
          <div className="rounded-2xl bg-obsidian-950/70 p-3 ring-1 ring-slate-800">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">SHA-256 parameter_hash</span>
              <button onClick={copyHash} className="text-slate-400 hover:text-white" aria-label="Copy hash">
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
            <p className="mt-1 break-all font-mono text-[12px] leading-relaxed text-amber-200">{approval.parameter_hash}</p>
            <p className="mt-1 text-[10px] text-slate-500">
              The HMAC-SHA256 approval_token is bound to <span className="font-mono">approval_id + parameter_hash</span>. Any change to the live page invalidates it.
            </p>
          </div>

          {live ? (
            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                onClick={onApprove}
                disabled={busy}
                className="flex flex-1 items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-emerald-500 to-emerald-400 px-5 py-3.5 text-[15px] font-bold text-obsidian-950 shadow-emerald transition hover:brightness-110 disabled:opacity-50"
              >
                {pendingAction === "approve" ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <CircleCheck className="h-5 w-5" />}
                Approve &amp; Execute (Sign HMAC Token)
              </button>
              <button
                onClick={onReject}
                disabled={busy}
                className="flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-5 py-3.5 text-[15px] font-semibold text-rose-200 ring-1 ring-rose-500/50 transition hover:bg-rose-500/10 disabled:opacity-50"
              >
                {pendingAction === "reject" ? <LoaderCircle className="h-5 w-5 animate-spin" /> : <CircleX className="h-5 w-5" />}
                Reject &amp; Cancel
              </button>
            </div>
          ) : (
            <div className="rounded-2xl bg-rose-500/10 p-4 ring-1 ring-rose-500/40">
              <p className="flex items-center gap-2 text-sm font-bold text-rose-100">
                <ShieldX className="h-4 w-4" />
                {approval.status === "REVOKED" && task.status === "CANCELLED"
                  ? "Task cancelled — the pending approval was revoked. Nothing was executed."
                  : approval.status === "REVOKED"
                  ? "Approval revoked — live page drifted after approval. Nothing was executed or charged."
                  : approval.status === "USER_REJECTED"
                    ? "You rejected this action. It can never be executed."
                    : approval.status === "EXPIRED"
                      ? "Approval expired before execution. Nothing was executed."
                      : `Approval is ${approval.status}.`}
              </p>
              <button onClick={onRerun} disabled={busy} className="mt-3 flex items-center gap-1.5 rounded-xl bg-slate-900 px-3 py-2 text-xs font-semibold text-slate-100 ring-1 ring-slate-700 hover:bg-slate-800">
                <RotateCcw className="h-3.5 w-3.5" /> Re-run this scenario for a fresh approval
              </button>
            </div>
          )}
        </div>

        <div className="space-y-4">
          {preview && (
            <a href={preview} target="_blank" rel="noreferrer" className="group block overflow-hidden rounded-2xl ring-1 ring-amber-500/30">
              <div className="flex items-center justify-between bg-slate-900 px-3 py-1.5 text-[10px] text-slate-400">
                <span>{isPurchase ? "Pre-checkout evidence" : "Filled form preview (not submitted)"}</span>
                <ExternalLink className="h-3 w-3 opacity-60 group-hover:opacity-100" />
              </div>
              <img src={preview} alt="Approval preview" className="max-h-56 w-full bg-white object-cover object-top" />
            </a>
          )}

          <div className="rounded-2xl bg-obsidian-950/80 p-4 ring-1 ring-indigo-500/40">
            <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-indigo-300">
              <FlaskConical className="h-3.5 w-3.5" /> Judge security proof
            </p>
            <p className="mt-1 text-[11px] text-slate-400">Attack the gate live — each button sends a real <code>execute_approved_step</code> over MCP.</p>
            <div className="mt-3 grid gap-2">
              <button
                onClick={onForgedToken}
                disabled={busy || !live}
                className="flex items-center gap-2 rounded-xl bg-rose-500/10 px-3 py-2.5 text-left text-xs font-semibold text-rose-100 ring-1 ring-rose-500/40 transition hover:bg-rose-500/20 disabled:opacity-40"
              >
                {pendingAction === "forged" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <span>🧪</span>}
                Test Forged Token (LLM Bypass Attempt)
              </button>
              <button
                onClick={onPriceDrift}
                disabled={busy || !live || !isPurchase}
                title={isPurchase ? undefined : "Price drift applies to purchase approvals"}
                className="flex items-center gap-2 rounded-xl bg-amber-500/10 px-3 py-2.5 text-left text-xs font-semibold text-amber-100 ring-1 ring-amber-500/40 transition hover:bg-amber-500/20 disabled:opacity-40"
              >
                {pendingAction === "drift" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <TrendingUp className="h-4 w-4" />}
                📈 Test Price Drift Tampering (+₹12,000)
              </button>
            </div>
            {proofEvents.length > 0 && (
              <ul className="mt-3 space-y-2">
                {proofEvents.map((e) => (
                  <li
                    key={e.id}
                    className={cx(
                      "rounded-xl p-2.5 text-[11px] ring-1 animate-fade-up",
                      e.blocked ? "bg-rose-500/15 text-rose-100 ring-rose-500/50" : "bg-orange-500/15 text-orange-100 ring-orange-500/50",
                    )}
                  >
                    <p className="flex items-center gap-1.5 font-bold">
                      {e.blocked ? <ShieldX className="h-3.5 w-3.5" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                      {e.kind === "FORGED_TOKEN" ? "Forged token" : "Price drift"} · {e.blocked ? "Backend Blocked Execution" : "NOT blocked"}
                      <span className="ml-auto font-mono font-normal opacity-60">{timeOf(e.at)}</span>
                    </p>
                    <p className="mt-1 font-mono leading-relaxed">{e.message}</p>
                    {e.details && "drift" in e.details && e.details.drift ? (
                      <p className="mt-1 font-mono text-amber-200">
                        {Object.entries(e.details.drift as Record<string, { approved: unknown; live: unknown }>)
                          .map(([k, v]) => `${k}: ${k.includes("price") ? formatInr(Number(v.approved)) : String(v.approved)} → ${k.includes("price") ? formatInr(Number(v.live)) : String(v.live)}`)
                          .join(" · ")}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
