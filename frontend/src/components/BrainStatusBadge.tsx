"use client";

import { useEffect, useRef, useState } from "react";
import { Brain, Cloud, Cpu, ServerCog, Sigma, CircleCheck, Circle, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { BrainProviderId, BrainStatus } from "@/types/webpilot";
import { fetchBrainStatus } from "@/lib/mcpClient";
import { cx } from "@/lib/format";

const ICONS: Record<BrainProviderId, LucideIcon> = {
  aws_bedrock: Cloud,
  ollama_local: Cpu,
  cloud_llm: ServerCog,
  deterministic_symbolic: Sigma,
};

const BADGE_TEXT: Record<BrainProviderId, string> = {
  aws_bedrock: "☁️ AWS Bedrock Converse + Symbolic Verifier",
  ollama_local: "🧠 Ollama Local LLM + Symbolic Verifier",
  cloud_llm: "🧠 Cloud LLM + Symbolic Verifier",
  deterministic_symbolic: "⚡ Symbolic Engine",
};

export default function BrainStatusBadge() {
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const s = await fetchBrainStatus();
        if (alive) {
          setStatus(s);
          setError(false);
        }
      } catch {
        if (alive) setError(true);
      }
    };
    load();
    const id = setInterval(load, 20000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const active = status?.active_provider ?? "deterministic_symbolic";
  const Icon = ICONS[active];
  const llm = active !== "deterministic_symbolic";

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        title="Active AI brain — click for provider wiring"
        className={cx(
          "flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold ring-1 transition",
          error ? "bg-slate-900 text-slate-500 ring-slate-700" : llm ? "bg-indigo-500/15 text-indigo-100 ring-indigo-400/50" : "bg-slate-900/80 text-slate-200 ring-slate-700 hover:ring-indigo-400/50",
        )}
      >
        <Brain className="h-3.5 w-3.5" />
        <span className="hidden lg:inline">{error ? "Brain offline" : BADGE_TEXT[active]}</span>
        <Icon className="h-3.5 w-3.5 lg:hidden" />
      </button>
      {open && status && (
        <div className="absolute right-0 top-11 z-50 w-[380px] rounded-2xl bg-obsidian-900/98 p-4 shadow-2xl ring-1 ring-slate-700 backdrop-blur-xl animate-fade-up">
          <div className="flex items-start gap-2">
            <Brain className="mt-0.5 h-4 w-4 text-indigo-300" />
            <div className="flex-1">
              <p className="text-sm font-bold text-white">Active AI brain: {status.active_label}</p>
              <p className="text-[11px] text-slate-400">{status.active_model} · mode {status.mode}</p>
            </div>
            <button onClick={() => setOpen(false)} className="text-slate-500 hover:text-white" aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </div>
          <ul className="mt-3 space-y-1.5">
            {status.providers.map((p) => {
              const PIcon = ICONS[p.id];
              return (
                <li key={p.id} className={cx("flex items-start gap-2 rounded-xl p-2 ring-1", p.id === active ? "bg-indigo-500/10 ring-indigo-400/40" : "bg-slate-950/60 ring-slate-800")}>
                  <PIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-300" />
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-slate-100">
                      {p.label}
                      {p.id === active && <span className="rounded bg-indigo-500/30 px-1 text-[9px] text-indigo-100">ACTIVE</span>}
                    </p>
                    <p className="truncate text-[10px] text-slate-400" title={p.detail}>
                      {p.detail}
                    </p>
                  </div>
                  {p.available ? <CircleCheck className="h-3.5 w-3.5 text-emerald-400" /> : <Circle className="h-3.5 w-3.5 text-slate-600" />}
                </li>
              );
            })}
          </ul>
          <div className="mt-3 space-y-1 rounded-xl bg-slate-950/70 p-3 text-[10px] leading-relaxed text-slate-400 ring-1 ring-slate-800">
            <p>
              <span className="font-semibold text-slate-200">Auto-detect order:</span> AWS Bedrock (boto3 <code>bedrock-runtime</code> Converse, when
              AWS credentials or <code>WEBPILOT_USE_BEDROCK=true</code>) → local Ollama (<code>{status.ollama.host}</code>) → cloud LLM
              (<code>LLM_API_KEY</code>) → symbolic engine.
            </p>
            <p>
              <span className="font-semibold text-slate-200">Bedrock:</span> {status.bedrock.model_id} in {status.bedrock.region} ·
              boto3 {status.bedrock.boto3_installed ? "installed" : "missing"} · {status.bedrock.configured ? "configured" : "not configured"}
            </p>
            <p>
              <span className="font-semibold text-slate-200">Verifier:</span> {status.verifier}
            </p>
            {status.last_error && <p className="text-amber-300">Last provider error: {status.last_error}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
