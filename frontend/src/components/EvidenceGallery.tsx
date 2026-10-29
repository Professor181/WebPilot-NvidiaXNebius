"use client";

import { useState } from "react";
import { Image as ImageIcon, ShieldCheck, ShieldAlert, CircleCheck, CircleX, RefreshCw, ExternalLink, X, ListChecks, LoaderCircle, ChevronDown, ChevronRight } from "lucide-react";
import type { EvidenceRecord, VerificationReport } from "@/types/webpilot";
import { artifactUrl } from "@/lib/mcpClient";
import { TIER_STYLE, cx, prettyJson, tierOfMethod, timeOf, verdictStyle } from "@/lib/format";

function short(value: unknown): string {
  if (value === null || value === undefined) return "—";
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > 90 ? `${s.slice(0, 89)}…` : s;
}

function VerificationPanel({ report, onReverify, reverifying }: { report: VerificationReport | null; onReverify: () => void; reverifying: boolean }) {
  const [open, setOpen] = useState(true);
  if (!report) return null;
  const passed = report.checks.filter((c) => c.passed).length;
  return (
    <div className="rounded-2xl bg-slate-950/60 p-4 ring-1 ring-slate-800">
      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 text-sm font-bold text-slate-100">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <ListChecks className="h-4 w-4 text-emerald-300" /> Verification report
        </button>
        <span className={cx("status-pill", verdictStyle(report.verdict))}>{report.verdict}</span>
        {report.reverified && <span className="chip text-emerald-300">re-verified</span>}
        <span className="font-mono text-[10px] text-slate-500">
          {report.method} · {passed}/{report.checks.length} checks
        </span>
        <button
          onClick={onReverify}
          disabled={reverifying}
          className="ml-auto flex items-center gap-1.5 rounded-lg bg-slate-900 px-2.5 py-1 text-[11px] font-semibold text-slate-200 ring-1 ring-slate-700 hover:bg-slate-800 disabled:opacity-50"
        >
          {reverifying ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} verify_task_result
        </button>
      </div>
      {report.notes && <p className="mt-2 text-xs text-slate-400">{report.notes}</p>}
      {open && report.checks.length > 0 && (
        <ul className="mt-3 grid gap-1.5 md:grid-cols-2">
          {report.checks.map((c) => (
            <li key={c.check} className="flex items-start gap-2 rounded-lg bg-obsidian-950/80 px-2.5 py-1.5 text-[11px] ring-1 ring-slate-800">
              {c.passed ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" /> : <CircleX className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />}
              <div className="min-w-0">
                <p className="font-mono text-slate-200">{c.check}</p>
                <p className="truncate text-slate-500" title={`expected ${short(c.expected)} · observed ${short(c.observed)}`}>
                  observed {short(c.observed)}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface Props {
  evidence: EvidenceRecord[];
  report: VerificationReport | null;
  onReverify: () => void;
  reverifying: boolean;
}

export default function EvidenceGallery({ evidence, report, onReverify, reverifying }: Props) {
  const [lightbox, setLightbox] = useState<EvidenceRecord | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!evidence.length && !report) return null;

  return (
    <section className="glass-card space-y-4 p-5">
      <div className="flex items-center justify-between">
        <h2 className="section-title flex items-center gap-2">
          <ImageIcon className="h-4 w-4 text-cyan-300" /> Evidence &amp; verification
        </h2>
        <span className="font-mono text-[11px] text-slate-500">{evidence.length} records via get_execution_evidence</span>
      </div>

      <VerificationPanel report={report} onReverify={onReverify} reverifying={reverifying} />

      <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {evidence.map((e) => {
          const tier = TIER_STYLE[tierOfMethod(e.capability_method)];
          const shot = artifactUrl(e.screenshot_url);
          const hasFirewall = e.firewall_alerts.length > 0;
          return (
            <article key={e.evidence_id} className="flex flex-col overflow-hidden rounded-2xl bg-slate-950/60 ring-1 ring-slate-800 animate-fade-up">
              {shot ? (
                <button onClick={() => setLightbox(e)} className="group relative block h-40 overflow-hidden bg-white">
                  <img src={shot} alt={e.page_title ?? e.source_url} loading="lazy" className="h-full w-full object-cover object-top transition group-hover:scale-[1.02]" />
                  <span className="absolute bottom-1.5 right-1.5 rounded bg-obsidian-950/80 px-1.5 py-0.5 font-mono text-[9px] text-slate-300">
                    {shot.endsWith(".png") ? "PNG · Chromium" : "SVG snapshot"}
                  </span>
                </button>
              ) : (
                <div className="flex h-20 items-center justify-center bg-slate-900 text-[11px] text-slate-500">no screenshot</div>
              )}
              <div className="flex flex-1 flex-col gap-1.5 p-3">
                <div className="flex flex-wrap items-center gap-1">
                  <span className={cx("rounded-md px-1.5 py-0.5 text-[9px] font-bold ring-1", tier.cls)}>{tier.label}</span>
                  {hasFirewall && (
                    <span className="flex items-center gap-0.5 rounded-md bg-rose-500/10 px-1.5 py-0.5 text-[9px] font-bold text-rose-300 ring-1 ring-rose-500/40">
                      <ShieldAlert className="h-2.5 w-2.5" /> FIREWALL_GUARD · {e.firewall_alerts.length}
                    </span>
                  )}
                  {e.verification_passed ? (
                    <span className="flex items-center gap-0.5 rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold text-emerald-300 ring-1 ring-emerald-500/40">
                      <ShieldCheck className="h-2.5 w-2.5" /> VERIFIED
                    </span>
                  ) : null}
                </div>
                <p className="truncate text-xs font-semibold text-slate-100" title={e.page_title ?? ""}>
                  {e.page_title ?? "Evidence"}
                </p>
                <a href={e.source_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 truncate font-mono text-[10px] text-slate-500 hover:text-cyan-300">
                  <span className="truncate">{e.source_url}</span> <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                </a>
                <p className="font-mono text-[10px] text-slate-500">
                  {e.capability_method} · {timeOf(e.captured_at)}
                </p>
                {e.dom_citation_snippet && (
                  <button
                    onClick={() => setExpanded(expanded === e.evidence_id ? null : e.evidence_id)}
                    className={cx(
                      "mt-auto rounded-lg bg-obsidian-950 px-2 py-1.5 text-left font-mono text-[10px] leading-relaxed text-slate-400 ring-1 ring-slate-800",
                      expanded === e.evidence_id ? "" : "line-clamp-3",
                    )}
                  >
                    {e.dom_citation_snippet}
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {lightbox && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 backdrop-blur" onClick={() => setLightbox(null)}>
          <div className="relative max-h-full w-full max-w-5xl overflow-auto rounded-2xl bg-obsidian-900 ring-1 ring-slate-700" onClick={(ev) => ev.stopPropagation()}>
            <div className="sticky top-0 z-10 flex items-center gap-2 bg-obsidian-900/95 px-4 py-2 text-xs text-slate-300">
              <span className="truncate font-mono">{lightbox.source_url}</span>
              <button onClick={() => setLightbox(null)} className="ml-auto rounded p-1 hover:bg-slate-800" aria-label="Close">
                <X className="h-4 w-4" />
              </button>
            </div>
            <img src={artifactUrl(lightbox.screenshot_url) ?? ""} alt={lightbox.page_title ?? ""} className="w-full bg-white" />
            <pre className="max-h-64 overflow-auto p-4 font-mono text-[11px] text-slate-400">{prettyJson(lightbox.extracted)}</pre>
          </div>
        </div>
      )}
    </section>
  );
}
