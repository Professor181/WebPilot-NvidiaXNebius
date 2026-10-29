"use client";

import { useMemo, useState } from "react";
import LiveFilmstrip from "@/components/LiveFilmstrip";
import {
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Receipt,
  GraduationCap,
  Star,
  GitFork,
  Bug,
  FolderTree,
  ExternalLink,
  Trophy,
  Lock,
  Ban,
  TriangleAlert,
  CircleCheck,
  CircleX,
  Globe,
  Sparkles,
  Cpu,
  Package,
  FilePen,
  Wand,
  Newspaper,
  Tag,
  Camera,
  Brain,
  Quote,
} from "lucide-react";
import type {
  ActionOutcome,
  AttendanceData,
  FirewallAlert,
  GithubData,
  GrantFormData,
  InspectResult,
  LaptopCandidate,
  LaptopData,
  ProfileData,
  RecoveryLogEntry,
  SynthesizedData,
  ArticleItem,
  PricePoint,
  GroundedAnswer,
  LiveAutopilotData,
  Task,
  UrlInspectionData,
  VerificationReport,
} from "@/types/webpilot";
import { artifactUrl } from "@/lib/mcpClient";
import { cx, formatInr, humanize, TASK_STATUS_STYLE, verdictStyle } from "@/lib/format";
import { AlexaOrb } from "./AlexaHeader";

// ------------------------------------------------------------------- shared pieces

const SANDBOX_SITE_NAMES: Record<string, string> = {
  electrohub: "ElectroHub",
  bytebazaar: "ByteBazaar",
  attendance: "EduPortal",
  "application-form": "the Grant Portal",
  "login-wall": "the Enterprise Portal",
};

function siteLabel(url: string | undefined): string {
  if (!url) return "this page";
  try {
    const u = new URL(url);
    const segment = u.pathname.startsWith("/sandbox/") ? u.pathname.split("/")[2] : "";
    return SANDBOX_SITE_NAMES[segment] ?? u.hostname;
  } catch {
    return url;
  }
}

export function FirewallBanner({ alerts, site }: { alerts: FirewallAlert[]; site?: string }) {
  const [open, setOpen] = useState(false);
  if (!alerts.length) return null;
  const injections = alerts.filter((a) => a.matched_patterns.length > 0);
  const critical = injections.length > 0;
  const siteName = site ?? siteLabel(alerts[0]?.source_url);
  return (
    <div
      className={cx(
        "rounded-2xl p-4 ring-1 animate-fade-up",
        critical ? "bg-gradient-to-r from-rose-500/15 via-rose-500/5 to-emerald-500/10 ring-rose-500/50" : "bg-emerald-500/10 ring-emerald-500/40",
      )}
    >
      <button onClick={() => setOpen(!open)} className="flex w-full items-start gap-3 text-left">
        <span className={cx("mt-0.5 rounded-xl p-2", critical ? "bg-rose-500/20 text-rose-300" : "bg-emerald-500/20 text-emerald-300")}>
          {critical ? <ShieldAlert className="h-5 w-5" /> : <ShieldCheck className="h-5 w-5" />}
        </span>
        <span className="flex-1">
          <span className="block text-sm font-bold text-slate-50">
            🛡️ Untrusted Content Firewall Active —{" "}
            {critical ? `Blocked hidden DOM prompt-injection attempt on ${siteName}` : `${alerts.length} invisible node(s) removed`}
          </span>
          <span className="mt-0.5 block text-xs text-slate-300">
            {injections.length} injection payload(s) neutralized before reaching the planner. Page text is wrapped in{" "}
            <code className="text-cyan-300">&lt;untrusted_web_content&gt;</code> and never treated as instructions.
          </span>
        </span>
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          {alerts.map((a) => (
            <div key={a.alert_id} className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-slate-800">
              <div className="flex flex-wrap items-center gap-1.5 text-[10px] font-mono">
                <span className={cx("rounded px-1.5 py-0.5 ring-1", a.severity === "CRITICAL" ? "bg-rose-500/20 text-rose-200 ring-rose-500/50" : "bg-slate-800 text-slate-300 ring-slate-700")}>
                  {a.severity}
                </span>
                <span className="text-slate-300">{a.alert_type}</span>
                {a.hiding_technique && <span className="text-amber-300">via {a.hiding_technique}</span>}
                {a.element_selector && <span className="text-cyan-300">{a.element_selector}</span>}
              </div>
              <p className="mt-2 font-mono text-xs leading-relaxed text-rose-200/90 line-through decoration-rose-500/70">“{a.snippet}”</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {a.matched_patterns.map((p) => (
                  <span key={p} className="rounded bg-rose-500/10 px-1.5 py-0.5 font-mono text-[10px] text-rose-300 ring-1 ring-rose-500/30">
                    {p}
                  </span>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-emerald-300">✓ {a.action_taken}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RecoveryBanner({ log, selected }: { log: RecoveryLogEntry[]; selected?: LaptopCandidate }) {
  if (!log.length) return null;
  const first = log[0];
  return (
    <div className="flex gap-3 rounded-2xl bg-amber-500/10 p-4 ring-1 ring-amber-400/50 animate-fade-up">
      <span className="mt-0.5 rounded-xl bg-amber-500/20 p-2 text-amber-300">
        <RefreshCw className="h-5 w-5" />
      </span>
      <div className="text-sm">
        <p className="font-bold text-amber-100">Self-Healing Recovery — checkout failure handled automatically</p>
        <p className="mt-1 text-xs leading-relaxed text-amber-100/80">
          <strong>{first.failed_candidate}</strong> on {first.seller} returned <code className="text-amber-300">{first.error_code}</code> during
          checkout (listing said “{first.listing_checkout_state ?? "in stock"}”). WebPilot marked the step{" "}
          <code className="text-amber-300">FAILED_RECOVERED</code>, re-planned at runtime and pivoted to the #{selected?.rank ?? 2} ranked option
          {selected ? (
            <>
              {" "}
              <strong>{selected.name}</strong> ({formatInr(selected.price_inr)}) on {selected.seller}
            </>
          ) : null}
          .
        </p>
      </div>
    </div>
  );
}

function ReceiptCard({ outcome, report, kind }: { outcome: ActionOutcome; report: VerificationReport | null; kind: "order" | "submission" }) {
  const verified = report && ["SIMULATED_TRANSACTION_VERIFIED", "VERIFIED_SUCCESS"].includes(report.verdict);
  const passed = report?.checks.filter((c) => c.passed).length ?? 0;
  return (
    <div
      className={cx(
        "rounded-2xl p-5 ring-1 animate-fade-up",
        verified ? "bg-gradient-to-br from-emerald-500/20 to-emerald-500/5 ring-emerald-400/60 shadow-emerald" : "bg-orange-500/10 ring-orange-400/50",
      )}
    >
      <div className="flex items-start gap-3">
        <span className="rounded-xl bg-emerald-500/20 p-2.5 text-emerald-300">
          <Receipt className="h-6 w-6" />
        </span>
        <div className="flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-emerald-300">
            {kind === "order" ? "Verified order receipt" : "Verified submission receipt"}
          </p>
          <p className="mt-1 font-mono text-2xl font-black tracking-wider text-white">{outcome.confirmation_id ?? "—"}</p>
          <p className="mt-1 text-xs text-slate-300">
            {report?.verdict} · {passed}/{report?.checks.length ?? 0} post-action checks passed
          </p>
          {kind === "order" && (
            <span className="mt-2 inline-block rounded-md bg-slate-950/80 px-2 py-1 text-[10px] font-bold tracking-wider text-amber-200 ring-1 ring-amber-400/30">
              CONTROLLED SANDBOX SIMULATION — NO REAL MONEY CHARGED
            </span>
          )}
        </div>
        <a href={outcome.confirmation_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-emerald-300 hover:underline">
          Receipt page <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-slate-950/60 p-3 ring-1 ring-slate-800">
      <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-slate-500">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-lg font-bold text-slate-100">{value}</div>
    </div>
  );
}

// ----------------------------------------------------------------- 1. attendance

function AttendanceCard({ data }: { data: AttendanceData }) {
  const [extra, setExtra] = useState<Record<string, number>>({});
  const t = data.threshold_pct;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <GraduationCap className="h-6 w-6 text-cyan-300" />
        <div>
          <h3 className="text-base font-bold text-slate-50">Semester attendance · {data.student ?? "Student"}</h3>
          <p className="text-xs text-slate-400">
            Minimum required {t}% · formula <code className="text-cyan-300">{data.formula}</code>
          </p>
        </div>
      </div>
      <div className="grid gap-2">
        {data.subjects.map((s) => {
          const risk = !s.below_threshold ? "ok" : s.percentage < t - 5 ? "high" : "medium";
          const k = extra[s.code] ?? 0;
          const projected = s.conducted + k ? ((s.attended + k) * 100) / (s.conducted + k) : 0;
          return (
            <div
              key={s.code}
              className={cx(
                "rounded-xl p-3 ring-1",
                risk === "high" ? "bg-rose-500/10 ring-rose-500/40" : risk === "medium" ? "bg-amber-500/10 ring-amber-400/40" : "bg-slate-950/50 ring-slate-800",
              )}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-slate-400">{s.code}</span>
                <span className="text-sm font-semibold text-slate-100">{s.subject}</span>
                <span className="ml-auto font-mono text-sm font-bold text-slate-100">{s.percentage.toFixed(1)}%</span>
                {risk === "ok" ? (
                  <span className="status-pill bg-emerald-500/15 text-emerald-200 ring-emerald-400/40">Safe</span>
                ) : (
                  <span className={cx("status-pill", risk === "high" ? "bg-rose-500/20 text-rose-100 ring-rose-400/60" : "bg-amber-500/20 text-amber-100 ring-amber-400/60")}>
                    {risk === "high" ? "High risk" : "At risk"} · needs {s.classes_needed}
                  </span>
                )}
              </div>
              <div className="relative mt-2 h-2 rounded-full bg-slate-800">
                <div
                  className={cx("h-full rounded-full", risk === "ok" ? "bg-emerald-500" : risk === "high" ? "bg-rose-500" : "bg-amber-400")}
                  style={{ width: `${Math.min(100, s.percentage)}%` }}
                />
                <span className="absolute -top-1 h-4 w-0.5 bg-white/70" style={{ left: `${t}%` }} title={`${t}% threshold`} />
              </div>
              <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
                <span>
                  {s.attended}/{s.conducted} attended
                </span>
                {s.displayed_pct !== null && <span>portal shows {s.displayed_pct}% · recomputed ✓</span>}
              </div>
              {s.below_threshold && (
                <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-slate-950/60 px-2.5 py-2 text-xs">
                  <span className="text-slate-400">Classes-needed calculator:</span>
                  <input
                    type="range"
                    min={0}
                    max={Math.max(15, s.classes_needed + 5)}
                    value={k}
                    onChange={(e) => setExtra({ ...extra, [s.code]: Number(e.target.value) })}
                    className="accent-cyan-400"
                    aria-label={`Extra classes for ${s.subject}`}
                  />
                  <span className="font-mono text-slate-200">
                    attend next {k} → {projected.toFixed(1)}%
                  </span>
                  <span className={cx("font-semibold", projected >= t ? "text-emerald-300" : "text-amber-300")}>
                    {projected >= t ? "✓ reaches threshold" : `${Math.max(0, s.classes_needed - k)} more to go`}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- 2. laptops

function LaptopCard({ data, task }: { data: LaptopData; task: Task }) {
  const failedNames = new Set(data.recovery_log.map((r) => r.failed_candidate));
  const rows = [...data.candidates_evaluated].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || a.price_inr - b.price_inr);
  return (
    <div className="space-y-4">
      <FirewallBanner alerts={data.firewall_alerts} />
      <RecoveryBanner log={data.recovery_log} selected={data.selected} />
      {data.outcome && <ReceiptCard outcome={data.outcome} report={task.verification_report} kind="order" />}
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="flex items-center gap-2 text-sm font-bold text-slate-100">
            <Cpu className="h-4 w-4 text-cyan-300" /> Multi-store comparison matrix
          </h3>
          <span className="font-mono text-[11px] text-slate-400">
            ≥{data.constraints.min_ram_gb}GB RAM · ≤{formatInr(data.constraints.max_price_inr)}
          </span>
        </div>
        <div className="overflow-x-auto rounded-xl ring-1 ring-slate-800">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-900/80 text-[11px] uppercase tracking-wider text-slate-400">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">Laptop</th>
                <th className="px-3 py-2">Store</th>
                <th className="px-3 py-2 text-right">Price</th>
                <th className="px-3 py-2">RAM</th>
                <th className="px-3 py-2">Rating</th>
                <th className="px-3 py-2">Delivery</th>
                <th className="px-3 py-2">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const selected = data.selected?.sku === c.sku;
                const oos = failedNames.has(c.name);
                return (
                  <tr key={c.sku} className={cx("border-t border-slate-800/80", selected && "bg-emerald-500/10", oos && "bg-amber-500/5")}>
                    <td className="px-3 py-2 font-mono text-slate-400">{c.rank ? `#${c.rank}` : "—"}</td>
                    <td className="px-3 py-2">
                      <div className="font-semibold text-slate-100">{c.name}</div>
                      <div className="max-w-[260px] truncate text-[10px] text-slate-500" title={c.specs ?? ""}>
                        {c.specs}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-slate-300">{c.seller}</td>
                    <td className={cx("px-3 py-2 text-right font-mono font-bold", c.price_inr > data.constraints.max_price_inr ? "text-rose-300" : "text-slate-100")}>
                      {formatInr(c.price_inr)}
                    </td>
                    <td className={cx("px-3 py-2 font-mono", c.ram_gb < data.constraints.min_ram_gb ? "text-rose-300" : "text-slate-200")}>{c.ram_gb}GB</td>
                    <td className="px-3 py-2 text-amber-200">
                      <Star className="mr-0.5 inline h-3 w-3 fill-amber-300 text-amber-300" />
                      {c.rating ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-400">{c.delivery}</td>
                    <td className="px-3 py-2">
                      {selected ? (
                        <span className="status-pill bg-emerald-500/15 text-emerald-200 ring-emerald-400/50">Selected</span>
                      ) : oos ? (
                        <span className="status-pill bg-amber-500/15 text-amber-200 ring-amber-400/50">Out of stock at checkout</span>
                      ) : c.qualifies ? (
                        <span className="status-pill bg-cyan-500/10 text-cyan-200 ring-cyan-400/40">Qualifies</span>
                      ) : (
                        <span className="text-[11px] text-rose-300">{c.rejection_reasons.join("; ")}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-1.5 text-[11px] text-slate-500">Ranked by lowest total price, tie-broken by rating. The ₹1,45,000 laptop pushed by the hidden injection never qualified.</p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- 3. grant

function GrantCard({ data, task }: { data: GrantFormData; task: Task }) {
  const fieldDefs = new Map(data.form.fields.map((f) => [f.name, f]));
  return (
    <div className="space-y-4">
      {data.outcome && <ReceiptCard outcome={data.outcome} report={task.verification_report} kind="submission" />}
      {data.problems && data.problems.length > 0 && (
        <div className="rounded-2xl bg-amber-500/10 p-4 ring-1 ring-amber-400/50">
          <p className="flex items-center gap-2 text-sm font-bold text-amber-100">
            <TriangleAlert className="h-4 w-4" /> Field validation needs your input
          </p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-100/90">
            {data.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="rounded-2xl bg-slate-50 p-5 text-slate-800 shadow-xl ring-4 ring-amber-400/30">
        <div className="mb-3 flex items-center justify-between border-b border-slate-200 pb-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wider text-emerald-700">National Innovation Grant Portal</p>
            <p className="text-base font-bold">Application preview — form #{data.form.form_id}</p>
          </div>
          <span className={cx("rounded-md px-2 py-1 text-[10px] font-bold", data.outcome ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800")}>
            {data.outcome ? "SUBMITTED" : "NOT SUBMITTED"}
          </span>
        </div>
        <div className="space-y-3">
          {data.field_mapping.map((m) => {
            const def = fieldDefs.get(m.field);
            const len = (m.value ?? "").length;
            const ok = !!m.value && (!def?.maxlength || len <= def.maxlength);
            return (
              <div key={m.field}>
                <div className="flex items-center gap-2 text-xs font-semibold text-slate-600">
                  {m.label ?? m.field}
                  {m.required && <span className="text-rose-500">*</span>}
                  <span className={cx("ml-auto rounded px-1.5 py-0.5 text-[9px] font-bold", m.source === "user_parameters" ? "bg-indigo-100 text-indigo-700" : "bg-slate-200 text-slate-600")}>
                    {m.source === "user_parameters" ? "FROM YOU" : m.source === "demo_applicant_profile" ? "DEMO PROFILE" : "MISSING"}
                  </span>
                  {ok ? <CircleCheck className="h-3.5 w-3.5 text-emerald-600" /> : <CircleX className="h-3.5 w-3.5 text-rose-500" />}
                </div>
                <div className={cx("mt-1 rounded-lg border bg-white px-3 py-2 text-sm", ok ? "border-slate-300" : "border-rose-400")}>
                  {m.value || <span className="text-slate-400">—</span>}
                </div>
                {def?.maxlength ? (
                  <p className="mt-0.5 text-right font-mono text-[10px] text-slate-400">
                    {len}/{def.maxlength}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-[11px] text-slate-500">
          Submit target: <code>{data.form.method} {data.form.action}</code>
        </p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 4. github

function GithubCard({ data }: { data: GithubData }) {
  const langs = Object.entries(data.languages_pct ?? {});
  const palette = ["bg-cyan-400", "bg-indigo-400", "bg-emerald-400", "bg-amber-400", "bg-rose-400", "bg-slate-400"];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <a href={data.html_url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-base font-bold text-slate-50 hover:text-cyan-300">
            {data.full_name} <ExternalLink className="h-3.5 w-3.5" />
          </a>
          <p className="mt-0.5 text-xs text-slate-400">{data.description}</p>
        </div>
        <span
          className={cx(
            "status-pill",
            data.source === "GITHUB_REST_API" ? "bg-emerald-500/15 text-emerald-200 ring-emerald-400/50" : "bg-cyan-500/10 text-cyan-200 ring-cyan-400/40",
          )}
        >
          {data.source === "GITHUB_REST_API" ? "TIER_1_ADAPTER · GitHub REST API" : "TIER_2 · HTML fallback (rate-limited)"}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Stars" icon={<Star className="h-3 w-3" />} value={data.stars?.toLocaleString() ?? "—"} />
        <Stat label="Forks" icon={<GitFork className="h-3 w-3" />} value={data.forks?.toLocaleString() ?? "—"} />
        <Stat label="Open issues + PRs" icon={<Bug className="h-3 w-3" />} value={data.open_issues_and_prs ?? "—"} />
        <Stat label="Language" value={data.language ?? "—"} />
      </div>
      {langs.length > 0 && (
        <div>
          <div className="flex h-2 overflow-hidden rounded-full">
            {langs.map(([name, pct], i) => (
              <div key={name} className={palette[i % palette.length]} style={{ width: `${pct}%` }} title={`${name} ${pct}%`} />
            ))}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-slate-400">
            {langs.map(([name, pct], i) => (
              <span key={name} className="flex items-center gap-1">
                <span className={cx("h-2 w-2 rounded-full", palette[i % palette.length])} />
                {name} {pct}%
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="rounded-xl bg-slate-950/60 p-3 ring-1 ring-slate-800">
        <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">
          <FolderTree className="h-3.5 w-3.5" /> Architecture
        </p>
        <p className="mt-1.5 text-sm text-slate-200">{data.architecture_summary}</p>
        {data.architecture.src_components && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {data.architecture.src_components.map((c) => (
              <span key={c} className="rounded-md bg-indigo-500/10 px-2 py-0.5 font-mono text-[11px] text-indigo-200 ring-1 ring-indigo-500/30">
                src/{c}
              </span>
            ))}
          </div>
        )}
        <div className="mt-2 flex flex-wrap gap-1.5">
          {data.architecture.detected_stack.map((s) => (
            <span key={s} className="chip">
              {s}
            </span>
          ))}
          {data.license && <span className="chip">License {data.license}</span>}
        </div>
      </div>
      <div>
        <p className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Recent open issues</p>
        <ul className="space-y-1.5">
          {data.open_issues_sample.slice(0, 8).map((i) => (
            <li key={i.number}>
              <a
                href={i.html_url}
                target="_blank"
                rel="noreferrer"
                className="flex items-start gap-2 rounded-lg bg-slate-950/50 px-3 py-2 text-xs ring-1 ring-slate-800 hover:ring-cyan-500/40"
              >
                <span className="font-mono text-emerald-300">#{i.number}</span>
                <span className="flex-1 text-slate-200">{i.title}</span>
                {i.labels.slice(0, 2).map((l) => (
                  <span key={l} className="rounded bg-slate-800 px-1.5 text-[10px] text-slate-300">
                    {l}
                  </span>
                ))}
              </a>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------- 5. profiles

function ProfileCard({ data }: { data: ProfileData }) {
  const [me, friend] = data.profiles;
  if (!me || !friend) return null;
  const max = Math.max(me.total_solved, friend.total_solved, 1);
  const metrics: { key: "easy" | "medium" | "hard"; label: string; color: string }[] = [
    { key: "easy", label: "Easy", color: "bg-emerald-400" },
    { key: "medium", label: "Medium", color: "bg-amber-400" },
    { key: "hard", label: "Hard", color: "bg-rose-400" },
  ];
  const leader = data.comparison.total_solved.leader;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-2">
        {[me, friend].map((p, idx) => (
          <div key={p.username} className={cx("rounded-2xl p-4 ring-1", leader === p.username ? "bg-indigo-500/10 ring-indigo-400/50" : "bg-slate-950/60 ring-slate-800")}>
            <div className="flex items-center gap-2">
              <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[#00CAFF] to-[#6366F1] text-sm font-bold text-white">
                {p.display_name.slice(0, 1)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-bold text-slate-100">
                  {p.display_name} {idx === 0 && <span className="text-[10px] text-slate-400">(you)</span>}
                </p>
                <p className="truncate font-mono text-[10px] text-slate-500">{p.platform}</p>
              </div>
              {leader === p.username && <Trophy className="h-5 w-5 text-amber-300" />}
            </div>
            <p className="mt-3 text-3xl font-black text-white">{p.total_solved}</p>
            <p className="text-[11px] text-slate-400">problems solved</p>
            <div className="mt-2 h-1.5 rounded-full bg-slate-800">
              <div className="h-full rounded-full bg-gradient-to-r from-[#00CAFF] to-[#6366F1]" style={{ width: `${(p.total_solved / max) * 100}%` }} />
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
              <div>
                <p className="font-bold text-slate-100">{p.contest_rating ?? "—"}</p>
                <p className="text-[10px] text-slate-500">rating</p>
              </div>
              <div>
                <p className="font-bold text-slate-100">{p.global_ranking?.toLocaleString() ?? "—"}</p>
                <p className="text-[10px] text-slate-500">rank</p>
              </div>
              <div>
                <p className="font-bold text-slate-100">{p.streak_days ?? "—"}</p>
                <p className="text-[10px] text-slate-500">streak</p>
              </div>
            </div>
            <a href={p.source_url} target="_blank" rel="noreferrer" className="mt-3 flex items-center gap-1 truncate font-mono text-[10px] text-cyan-300 hover:underline">
              source: {p.source_url} <ExternalLink className="h-3 w-3 shrink-0" />
            </a>
          </div>
        ))}
      </div>
      <div className="space-y-2 rounded-xl bg-slate-950/60 p-3 ring-1 ring-slate-800">
        {metrics.map((m) => {
          const c = data.comparison[m.key];
          const top = Math.max(c.user ?? 0, c.friend ?? 0, 1);
          return (
            <div key={m.key} className="grid grid-cols-[60px_1fr_1fr] items-center gap-2 text-xs">
              <span className={cx("font-semibold", data.biggest_gap_difficulty === m.key ? "text-amber-300" : "text-slate-300")}>{m.label}</span>
              <div className="flex items-center justify-end gap-1.5">
                <span className="font-mono text-slate-300">{c.user}</span>
                <div className="h-2 rounded-l-full bg-slate-800" style={{ width: "100%" }}>
                  <div className={cx("ml-auto h-full rounded-l-full", m.color)} style={{ width: `${((c.user ?? 0) / top) * 100}%` }} />
                </div>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="h-2 rounded-r-full bg-slate-800" style={{ width: "100%" }}>
                  <div className={cx("h-full rounded-r-full opacity-70", m.color)} style={{ width: `${((c.friend ?? 0) / top) * 100}%` }} />
                </div>
                <span className="font-mono text-slate-300">{c.friend}</span>
              </div>
            </div>
          );
        })}
        <p className="pt-1 text-center text-[10px] text-slate-500">
          you ← · → {friend.display_name}
          {data.biggest_gap_difficulty ? ` · biggest gap: ${data.biggest_gap_difficulty}` : ""}
        </p>
      </div>
    </div>
  );
}

// -------------------------------------------------------- 6. blocked / generic URL

const BARRIER_LABEL: Record<string, string> = {
  OTP_2FA_REQUIRED: "🔑 One-time passcode / 2FA",
  CAPTCHA_CHALLENGE: "🧩 CAPTCHA challenge",
  LOGIN_REQUIRED: "👤 Login required",
  BOT_CHALLENGE: "🤖 Bot-protection check",
};

function BlockedCard({ data, onCancel, canCancel, busy }: { data: UrlInspectionData; onCancel: () => void; canCancel: boolean; busy: boolean }) {
  const blocker = data.blocker!;
  return (
    <div className="rounded-2xl bg-gradient-to-br from-rose-500/15 to-rose-500/5 p-5 ring-1 ring-rose-500/50 animate-fade-up">
      <div className="flex items-start gap-3">
        <span className="rounded-xl bg-rose-500/20 p-2.5 text-rose-300">
          <Lock className="h-6 w-6" />
        </span>
        <div className="flex-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-rose-300">Tier 3 · Human-in-the-loop escalation</p>
          <p className="mt-0.5 text-lg font-bold text-white">Blocked at “{data.title}”</p>
          <p className="mt-1 text-xs text-slate-300">
            WebPilot stopped honestly. It did <strong>not</strong> attempt to bypass these controls and has not claimed any result.
          </p>
        </div>
      </div>
      <div className="mt-4 grid gap-2 md:grid-cols-3">
        {blocker.barriers.map((b, i) => (
          <div key={b.barrier_type} className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-rose-500/30">
            <p className="text-sm font-bold text-rose-100">{BARRIER_LABEL[b.barrier_type] ?? humanize(b.barrier_type)}</p>
            <p className="mt-1 font-mono text-[10px] text-cyan-300">{b.selector}</p>
            <p className="mt-1 text-[11px] text-slate-400">{b.evidence}</p>
            <p className="mt-2 text-[11px] font-semibold text-amber-200">You: {blocker.required_human_actions[i]}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <span className="rounded-md bg-slate-950/70 px-2 py-1 font-mono text-[10px] text-emerald-300 ring-1 ring-emerald-500/30">
          bypass_attempted: {String(blocker.bypass_attempted)}
        </span>
        {canCancel && (
          <button
            onClick={onCancel}
            disabled={busy}
            className="ml-auto flex items-center gap-1.5 rounded-xl bg-rose-500/20 px-4 py-2 text-sm font-semibold text-rose-100 ring-1 ring-rose-400/60 hover:bg-rose-500/30 disabled:opacity-50"
          >
            <Ban className="h-4 w-4" /> Cancel task (cancel_web_task)
          </button>
        )}
      </div>
    </div>
  );
}

function LiveIntel({ articles, prices, renderMode, fallbackReason }: { articles?: ArticleItem[]; prices?: PricePoint[]; renderMode?: string; fallbackReason?: string | null }) {
  return (
    <div className="space-y-3">
      {renderMode && (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span
            className={cx(
              "flex items-center gap-1 rounded-md px-2 py-0.5 font-semibold ring-1",
              renderMode === "live_viewport" ? "bg-emerald-500/10 text-emerald-300 ring-emerald-500/40" : "bg-slate-800 text-slate-300 ring-slate-700",
            )}
          >
            <Camera className="h-3 w-3" />
            {renderMode === "live_viewport"
              ? "Live Chromium viewport capture"
              : renderMode === "rendered_html"
                ? "Chromium render"
                : renderMode === "raw_feed"
                  ? "Raw feed capture"
                  : "HTTP + BS4 fallback · SVG snapshot"}
          </span>
          {fallbackReason && (
            <span className="truncate text-slate-500" title={fallbackReason}>
              fallback: {fallbackReason}
            </span>
          )}
        </div>
      )}
      {articles && articles.length > 0 && (
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">
            <Newspaper className="h-3.5 w-3.5" /> Articles &amp; headlines ({articles.length})
          </p>
          <ol className="space-y-1">
            {articles.slice(0, 10).map((a, i) => (
              <li key={a.title + "-" + i} className="flex items-start gap-2 rounded-lg bg-slate-950/50 px-3 py-1.5 text-xs ring-1 ring-slate-800">
                <span className="w-5 shrink-0 text-right font-mono text-slate-500">{a.rank ?? i + 1}.</span>
                {a.url ? (
                  <a href={a.url} target="_blank" rel="noreferrer" className="flex-1 text-slate-100 hover:text-cyan-300">
                    {a.title}
                  </a>
                ) : (
                  <span className="flex-1 text-slate-100">{a.title}</span>
                )}
                {a.points != null && <span className="shrink-0 font-mono text-[10px] text-amber-300">▲ {a.points}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
      {prices && prices.length > 0 && (
        <div>
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-slate-400">
            <Tag className="h-3.5 w-3.5" /> Price points ({prices.length})
          </p>
          <div className="flex flex-wrap gap-1.5">
            {prices.slice(0, 12).map((p, i) => (
              <span key={p.raw + "-" + i} title={p.context} className="rounded-md bg-emerald-500/10 px-2 py-1 font-mono text-[11px] text-emerald-200 ring-1 ring-emerald-500/30">
                {p.raw}
                {p.period ? " " + p.period : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Sparkline({ values, ma, width = 220, height = 48 }: { values: (number | null)[]; ma?: (number | null)[]; width?: number; height?: number }) {
  const all = [...values, ...(ma ?? [])].filter((v): v is number => v !== null);
  if (all.length < 2) return null;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * (width - 4) + 2;
  const y = (v: number) => height - 2 - ((v - min) / (max - min || 1)) * (height - 4);
  const path = (series: (number | null)[]) => {
    const parts: string[] = [];
    series.forEach((v, i) => {
      if (v === null) return;
      parts.push((parts.length ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1));
    });
    return parts.join(" ");
  };
  return (
    <svg width={width} height={height} className="overflow-visible" role="img" aria-label="trend sparkline">
      <path d={path(values)} fill="none" stroke="#00CAFF" strokeWidth={1.8} />
      {ma && <path d={path(ma)} fill="none" stroke="#F59E0B" strokeWidth={1.6} strokeDasharray="4 3" />}
    </svg>
  );
}

function toNumber(value: string | number | null | undefined): number | null {
  if (typeof value === "number") return value;
  if (value === null || value === undefined) return null;
  const n = Number(String(value).replace(/[^\d.-]/g, ""));
  return Number.isFinite(n) && String(value).trim() !== "" ? n : null;
}

function SynthesizedToolCard({ data }: { data: SynthesizedData }) {
  const r = data.result;
  const maCols = r.added_columns;
  return (
    <div className="space-y-4">
      <div className="rounded-2xl bg-gradient-to-r from-teal-500/15 to-indigo-500/10 p-4 ring-1 ring-teal-400/40">
        <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-teal-300">
          <Wand className="h-4 w-4" /> Dynamically synthesized MCP tool
        </p>
        <p className="mt-1 break-all font-mono text-sm font-bold text-white">{data.tool.name}</p>
        <p className="mt-1 text-[11px] text-slate-400">
          {data.tool.registered_for_session
            ? "Registered in this MCP session's tools/list (notifications/tools/list_changed emitted)"
            : "Task-scoped (no MCP session)"}{" "}
          · expires {new Date(data.tool.expires_at).toLocaleTimeString()} · {data.tool.spec.execution_model}
        </p>
        <pre className="mt-3 overflow-x-auto rounded-xl bg-obsidian-950/80 p-3 font-mono text-[11px] leading-relaxed text-teal-100 ring-1 ring-slate-800">
          {data.tool.pipeline_listing.join("\n")}
        </pre>
      </div>
      <FirewallBanner alerts={data.firewall_alerts} />
      {r.ranking.length > 0 && (
        <div className="grid gap-2 md:grid-cols-3">
          {r.ranking.map((row) => {
            const col = row.column;
            const series = r.rows.map((x) => toNumber(x[col]));
            const maCol = maCols.find((m) => m.startsWith(col + " MA"));
            const maSeries = maCol ? r.rows.map((x) => toNumber(x[maCol])) : undefined;
            return (
              <div key={col} className="rounded-xl bg-slate-950/60 p-3 ring-1 ring-slate-800">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-bold text-slate-100">{col}</span>
                  <span className={cx("font-mono text-sm font-bold", row.pct_change >= 0 ? "text-emerald-300" : "text-rose-300")}>
                    {row.pct_change >= 0 ? "▲" : "▼"} {row.pct_change.toFixed(2)}%
                  </span>
                </div>
                <Sparkline values={series} ma={maSeries} />
                {maCol && (
                  <p className="font-mono text-[10px] text-amber-300">
                    {maCol}: {r.aggregates[maCol]?.last?.toLocaleString() ?? "—"}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
      <div className="overflow-x-auto rounded-xl ring-1 ring-slate-800">
        <table className="w-full text-left text-[11px]">
          <thead className="bg-slate-900/80 text-slate-400">
            <tr>
              {r.columns.slice(0, 9).map((c) => (
                <th key={c} className={cx("px-2 py-1.5", maCols.includes(c) && "text-amber-300")}>
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {r.rows.slice(0, 12).map((row, i) => (
              <tr key={i} className="border-t border-slate-800/80">
                {r.columns.slice(0, 9).map((c) => {
                  const v = row[c];
                  return (
                    <td key={c} className={cx("px-2 py-1 font-mono", maCols.includes(c) ? "text-amber-200" : "text-slate-300")}>
                      {v === null || v === undefined ? "—" : typeof v === "number" ? v.toLocaleString() : String(v).slice(0, 60)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="font-mono text-[10px] text-slate-500">
        {data.record_count} {data.format.toUpperCase()} records from {data.source_url} · trace: {r.trace.join(" · ")}
      </p>
    </div>
  );
}

const BRAIN_LABEL: Record<string, string> = {
  aws_bedrock: "AWS Bedrock Converse",
  ollama_local: "Ollama local LLM",
  cloud_llm: "Cloud LLM",
  deterministic_symbolic: "Symbolic engine",
};

export function GroundedAnswerCard({ answer }: { answer: GroundedAnswer }) {
  return (
    <div className="rounded-2xl bg-gradient-to-br from-indigo-500/15 via-obsidian-900 to-cyan-500/10 p-4 ring-1 ring-indigo-400/50 animate-fade-up">
      <div className="flex flex-wrap items-center gap-2">
        <Quote className="h-4 w-4 text-indigo-300" />
        <p className="text-xs font-bold uppercase tracking-wider text-indigo-200">Live web intelligence · grounded answer</p>
        <span className="ml-auto rounded-md bg-indigo-500/20 px-2 py-0.5 text-[10px] font-semibold text-indigo-100 ring-1 ring-indigo-400/40">
          {BRAIN_LABEL[answer.provider_used] ?? answer.provider_used} · {answer.method}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-slate-400">
        Q: “{answer.question}” · {answer.items_considered} DOM items considered
        {answer.plan.terms.length > 0 && <> · filters: {answer.plan.terms.slice(0, 6).join(", ")}</>} · ranked by {answer.plan.rank_by.replace("_", " ")}
      </p>
      {answer.citations.length > 0 ? (
        <ol className="mt-3 space-y-1.5">
          {answer.citations.map((c) => (
            <li key={c.item_id} className="flex items-start gap-2 rounded-xl bg-obsidian-950/80 px-3 py-2 ring-1 ring-slate-800">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-indigo-500/25 text-[11px] font-bold text-indigo-100">{c.rank}</span>
              <div className="min-w-0 flex-1">
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer" className="text-sm font-semibold text-slate-100 hover:text-cyan-300">
                    {c.title}
                  </a>
                ) : (
                  <p className="text-sm font-semibold text-slate-100">{c.title}</p>
                )}
                <p className="mt-0.5 flex flex-wrap items-center gap-2 font-mono text-[10px] text-slate-500">
                  <span className="text-cyan-300">[#{c.item_id}]</span>
                  <span title="DOM citation">{c.dom_ref}</span>
                  {c.matched_terms.length > 0 && <span className="text-emerald-300">matched: {c.matched_terms.join(", ")}</span>}
                </p>
              </div>
              {c.points !== null && <span className="shrink-0 font-mono text-xs font-bold text-amber-300">▲ {c.points}</span>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-3 rounded-xl bg-obsidian-950/70 p-3 text-sm text-slate-300 ring-1 ring-slate-800">{answer.answer}</p>
      )}
      {answer.notes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[10px] text-amber-300">
          {answer.notes.map((n) => (
            <li key={n}>⚠ {n}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[10px] text-slate-500">Every cited item is re-checked against the DOM snapshot by the verifier (text, points and ranking).</p>
    </div>
  );
}

function ReasoningTracePanel({ task }: { task: Task }) {
  const [open, setOpen] = useState(false);
  const ig = task.interpreted_goal;
  const trace = ig?.ai_reasoning_trace ?? ig?.brain?.reasoning_trace ?? [];
  if (!trace.length) return null;
  const answer = (task.extracted_data as { answer?: GroundedAnswer | null } | null)?.answer ?? null;
  const provider = ig?.brain_provider_used ?? "deterministic_symbolic";
  const risk = ig?.brain?.risk_level ?? (task.task_category === "ACT_WITH_APPROVAL" ? "consequential" : "read_only");
  return (
    <div className="rounded-2xl bg-slate-950/60 ring-1 ring-indigo-500/30">
      <button onClick={() => setOpen(!open)} className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left">
        {open ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
        <Brain className="h-4 w-4 text-indigo-300" />
        <span className="text-sm font-bold text-slate-100">🧠 AI Reasoning &amp; Capability Routing Trace</span>
        <span className="rounded-md bg-indigo-500/15 px-2 py-0.5 text-[10px] font-semibold text-indigo-200 ring-1 ring-indigo-400/40">
          {BRAIN_LABEL[provider] ?? provider}
        </span>
        <span
          className={cx(
            "rounded-md px-2 py-0.5 text-[10px] font-semibold ring-1",
            risk === "consequential" ? "bg-amber-500/15 text-amber-200 ring-amber-400/40" : "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30",
          )}
        >
          risk: {risk === "consequential" ? "HIGH · approval-gated" : "LOW · read-only"}
        </span>
        <span className="ml-auto text-[10px] text-slate-500">{trace.length} reasoning steps</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-slate-800 px-4 py-3">
          {ig?.extraction_query && (
            <p className="text-[11px] text-slate-400">
              Extraction query: <span className="text-slate-200">“{ig.extraction_query}”</span>
            </p>
          )}
          <ol className="space-y-1.5">
            {trace.map((line, i) => (
              <li key={i} className="flex gap-2 text-xs leading-relaxed">
                <span className="w-5 shrink-0 text-right font-mono text-slate-600">{i + 1}.</span>
                <span className={cx(line.startsWith("Brain:") || line.startsWith("Symbolic verifier") ? "text-indigo-200" : line.startsWith("Risk") ? "text-amber-200" : line.startsWith("MCP primitives") ? "text-cyan-200" : "text-slate-300")}>
                  {line}
                </span>
              </li>
            ))}
          </ol>
          {answer && answer.citations.length > 0 && (
            <div>
              <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">Source DOM citations backing the answer</p>
              <ul className="space-y-0.5 font-mono text-[10px] text-slate-400">
                {answer.citations.map((c) => (
                  <li key={c.item_id}>
                    <span className="text-cyan-300">[#{c.item_id}]</span> {c.dom_ref} → “{c.title.slice(0, 90)}”
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DistilledPageCard({ data }: { data: UrlInspectionData }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Globe className="h-5 w-5 text-cyan-300" />
        <div className="min-w-0">
          <p className="truncate text-base font-bold text-slate-50">{data.title || data.url}</p>
          <a href={data.url} target="_blank" rel="noreferrer" className="truncate font-mono text-[11px] text-slate-400 hover:text-cyan-300">
            {data.url}
          </a>
        </div>
      </div>
      {data.answer && <GroundedAnswerCard answer={data.answer} />}
      {data.firewall_alerts && <FirewallBanner alerts={data.firewall_alerts} />}
      {data.screenshot_url && data.render_mode === "live_viewport" && (
        <a href={artifactUrl(data.screenshot_url) ?? undefined} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl ring-1 ring-slate-800">
          <img src={artifactUrl(data.screenshot_url) ?? ""} alt={"Live capture of " + data.url} className="max-h-64 w-full object-cover object-top" />
        </a>
      )}
      <LiveIntel articles={data.articles} prices={data.prices} renderMode={data.render_mode} fallbackReason={data.fallback_reason} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Headings" value={data.headings?.length ?? 0} />
        <Stat label="Links" value={data.links?.length ?? 0} />
        <Stat label="Tables" value={data.tables?.length ?? 0} />
        <Stat label="Interactive" value={data.interactive_elements?.length ?? 0} />
      </div>
      {data.interactive_elements && data.interactive_elements.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.interactive_elements.slice(0, 16).map((e, i) => (
            <span key={`${e.selector}-${i}`} className="rounded-md bg-slate-950/70 px-2 py-1 font-mono text-[10px] text-slate-300 ring-1 ring-slate-800" title={e.selector}>
              <span className="text-indigo-300">{e.role ?? e.tag}</span> {e.text || e.label || e.name || e.id}
            </span>
          ))}
        </div>
      )}
      {data.untrusted_text_excerpt && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-xl bg-obsidian-950 p-3 font-mono text-[11px] text-slate-400 ring-1 ring-slate-800">
          {data.untrusted_text_excerpt}
        </pre>
      )}
    </div>
  );
}

export function InspectResultCard({ result, onClose }: { result: InspectResult; onClose: () => void }) {
  const shot = artifactUrl(result.screenshot_url);
  return (
    <div className="glass-card space-y-3 p-5 animate-fade-up">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-cyan-300">inspect_website · {result.capability_method}</p>
          <p className="truncate text-base font-bold text-white">{result.title || result.url}</p>
          <p className="truncate font-mono text-[11px] text-slate-400">
            HTTP {result.status_code} · {result.url}
          </p>
        </div>
        <button onClick={onClose} className="rounded-lg px-2 py-1 text-xs text-slate-400 ring-1 ring-slate-700 hover:text-white">
          Close
        </button>
      </div>
      {result.blocked && (
        <div className="rounded-xl bg-rose-500/10 p-3 text-sm text-rose-100 ring-1 ring-rose-500/40">
          🔒 Human action required: {result.human_action_required.map((b) => BARRIER_LABEL[b] ?? humanize(b)).join(" · ")}
        </div>
      )}
      <FirewallBanner alerts={result.firewall_alerts} />
      <LiveIntel articles={result.articles} prices={result.prices} renderMode={result.render_mode} fallbackReason={result.fallback_reason} />
      <div className="grid gap-3 lg:grid-cols-[1fr_1.1fr]">
        {shot && (
          <a href={shot} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-xl ring-1 ring-slate-800">
            <img src={shot} alt={`Snapshot of ${result.url}`} className="max-h-72 w-full object-cover object-top" />
          </a>
        )}
        <div className="space-y-2">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Headings" value={result.headings.length} />
            <Stat label="Tables" value={result.tables.length} />
            <Stat label="Interactive" value={result.interactive_elements.length} />
          </div>
          {result.product_cards.length > 0 && (
            <p className="flex items-center gap-1.5 text-xs text-slate-300">
              <Package className="h-3.5 w-3.5 text-cyan-300" /> {result.product_cards.length} product cards distilled
            </p>
          )}
          <div className="flex max-h-28 flex-wrap gap-1.5 overflow-auto">
            {result.interactive_elements.slice(0, 24).map((e, i) => (
              <span key={`${e.selector}-${i}`} className="rounded-md bg-slate-950/70 px-2 py-1 font-mono text-[10px] text-slate-300 ring-1 ring-slate-800" title={e.selector}>
                <span className="text-indigo-300">{e.role ?? e.tag}</span> {e.text || e.label || e.name || e.id}
              </span>
            ))}
          </div>
        </div>
      </div>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-xl bg-obsidian-950 p-3 font-mono text-[11px] text-slate-400 ring-1 ring-slate-800">
        {result.untrusted_content}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------------- stage

interface StageProps {
  task: Task | null;
  live: boolean;
  busy: boolean;
  onCancel: () => void;
}

export default function MultimodalStage({ task, live, busy, onCancel }: StageProps) {
  const body = useMemo(() => {
    if (!task || !task.extracted_data) return null;
    const d = task.extracted_data;
    switch (task.interpreted_goal?.scenario) {
      case "attendance_audit":
        return <AttendanceCard data={d as unknown as AttendanceData} />;
      case "laptop_shopping":
        return <LaptopCard data={d as unknown as LaptopData} task={task} />;
      case "grant_form":
        return <GrantCard data={d as unknown as GrantFormData} task={task} />;
      case "github_repo_analysis":
        return <GithubCard data={d as unknown as GithubData} />;
      case "profile_comparison":
        return <ProfileCard data={d as unknown as ProfileData} />;
      case "synthesized_tool":
        return <SynthesizedToolCard data={d as unknown as SynthesizedData} />;
      case "live_autopilot": {
        const a = d as unknown as LiveAutopilotData;
        if (!a.autopilot) return null;
        return (
          <div className="space-y-4">
            <LiveFilmstrip data={a} />
            {a.answer && <GroundedAnswerCard answer={a.answer} />}
          </div>
        );
      }
      case "url_inspection": {
        const u = d as unknown as UrlInspectionData;
        return u.blocker ? (
          <BlockedCard data={u} onCancel={onCancel} canCancel={task.status === "BLOCKED"} busy={busy} />
        ) : (
          <DistilledPageCard data={u} />
        );
      }
      default:
        return null;
    }
  }, [task, onCancel, busy]);

  if (!task) {
    return (
      <section className="glass-card flex min-h-[340px] flex-col items-center justify-center gap-4 p-8 text-center">
        <AlexaOrb state="idle" size={84} />
        <div>
          <p className="text-lg font-semibold text-slate-100">“Alexa, ask WebPilot to…”</p>
          <p className="mt-1 max-w-md text-sm text-slate-400">
            Pick a one-click demo or speak a goal. Every action is a real JSON-RPC 2.0 call to the MCP 2025-11-25 server — open the Inspector to watch the frames.
          </p>
        </div>
      </section>
    );
  }

  const icon =
    task.status === "BLOCKED" || task.status === "FAILED" ? (
      <ShieldAlert className="h-4 w-4 text-rose-300" />
    ) : task.status === "AWAITING_APPROVAL" ? (
      <FilePen className="h-4 w-4 text-amber-300" />
    ) : (
      <Sparkles className="h-4 w-4 text-cyan-300" />
    );

  return (
    <section className="glass-card space-y-4 p-5">
      <div className="flex items-start gap-3">
        <AlexaOrb state={live ? "thinking" : task.status === "BLOCKED" || task.status === "AWAITING_APPROVAL" ? "alert" : "idle"} size={36} />
        <div className="min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-gradient-to-r from-slate-800/80 to-slate-900/60 px-4 py-3 ring-1 ring-slate-700/70">
          <p className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            {icon} Alexa+ says
            <span className={cx("status-pill ml-auto", TASK_STATUS_STYLE[task.status])}>{task.status}</span>
          </p>
          <p className="text-[15px] leading-relaxed text-slate-100 [overflow-wrap:anywhere]">
            {live && !task.summary ? "Working on it — compiling the plan and routing each step…" : task.summary ?? "…"}
          </p>
          {task.verification_report && task.status !== "AWAITING_APPROVAL" && (
            <span className={cx("status-pill mt-2", verdictStyle(task.verification_report.verdict))}>{task.verification_report.verdict}</span>
          )}
        </div>
      </div>
      {body}
      <ReasoningTracePanel task={task} />
      {task.status === "FAILED" && !body && (
        <div className="rounded-xl bg-rose-500/10 p-4 text-sm text-rose-100 ring-1 ring-rose-500/40">
          The task failed honestly — no outcome is claimed. {task.verification_report?.notes}
        </div>
      )}
    </section>
  );
}
