"use client";

import { useState } from "react";
import { Copy, Check, PanelRightOpen, PanelRightClose, RotateCcw, Plug, Unplug, LoaderCircle } from "lucide-react";
import { cx, shortId } from "@/lib/format";
import BrainStatusBadge from "./BrainStatusBadge";

export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "alert";

export function AlexaOrb({ state, size = 44 }: { state: OrbState; size?: number }) {
  const spin = state === "thinking" ? "animate-orb-spin-fast" : "animate-orb-spin";
  const pulse = state === "listening" || state === "speaking" ? "animate-orb-pulse" : "";
  const ring =
    state === "alert"
      ? "from-amber-400 via-rose-500 to-amber-400"
      : "from-[#00CAFF] via-[#6366F1] to-[#00CAFF]";
  return (
    <div className={cx("relative shrink-0", pulse)} style={{ width: size, height: size }} aria-label={`Alexa+ is ${state}`}>
      <div className={cx("absolute inset-0 rounded-full bg-gradient-to-tr blur-md opacity-70", ring, spin)} />
      <div className={cx("absolute inset-0 rounded-full bg-[conic-gradient(var(--tw-gradient-stops))]", ring, spin)} />
      <div className="absolute inset-[3px] rounded-full bg-obsidian-950" />
      <div
        className={cx(
          "absolute inset-[7px] rounded-full bg-gradient-to-br opacity-90",
          state === "alert" ? "from-amber-300/40 to-rose-500/30" : "from-[#00CAFF]/40 to-[#6366F1]/40",
        )}
      />
    </div>
  );
}

interface Props {
  orbState: OrbState;
  sessionId: string | null;
  protocolVersion: string | null;
  serverName: string | null;
  connected: boolean;
  connecting: boolean;
  toolsCount: number;
  frameCount: number;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
  onReset: () => void;
  onReconnect: () => void;
  resetting: boolean;
}

export default function AlexaHeader(p: Props) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (!p.sessionId) return;
    await navigator.clipboard?.writeText(p.sessionId).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-obsidian-950/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1680px] items-center gap-4 px-5 py-3">
        <AlexaOrb state={p.orbState} />
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <h1 className="bg-gradient-to-r from-[#00CAFF] to-[#6366F1] bg-clip-text text-xl font-bold tracking-tight text-transparent">
              Alexa+
            </h1>
            <span className="text-lg font-semibold text-slate-100">WebPilot</span>
            <span className="hidden rounded-full bg-slate-800/70 px-2 py-0.5 text-[11px] font-medium text-slate-300 ring-1 ring-slate-700 sm:inline">
              Simulated Multimodal Device
            </span>
          </div>
          <p className="truncate text-xs text-slate-400">
            Verifiable web tasks · MCP {p.protocolVersion ?? "2025-11-25"} Streamable HTTP · Approval-gated actions
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
          <BrainStatusBadge />
          <div
            className={cx(
              "flex items-center gap-2 rounded-xl px-3 py-1.5 text-xs ring-1",
              p.connected ? "bg-emerald-500/10 text-emerald-200 ring-emerald-500/40" : "bg-rose-500/10 text-rose-200 ring-rose-500/40",
            )}
          >
            {p.connecting ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : p.connected ? (
              <Plug className="h-3.5 w-3.5" />
            ) : (
              <Unplug className="h-3.5 w-3.5" />
            )}
            <span className="hidden md:inline">{p.connected ? `${p.serverName ?? "MCP"} · ${p.toolsCount} tools` : "MCP offline"}</span>
            {!p.connected && !p.connecting && (
              <button onClick={p.onReconnect} className="font-semibold underline underline-offset-2">
                Retry
              </button>
            )}
          </div>

          <button
            onClick={copy}
            title={p.sessionId ?? "No MCP session"}
            className="group flex items-center gap-2 rounded-xl bg-slate-900/80 px-3 py-1.5 font-mono text-xs text-slate-300 ring-1 ring-slate-700 hover:ring-cyan-500/50"
          >
            <span className="text-slate-500">Mcp-Session-Id</span>
            <span className="text-cyan-300">{shortId(p.sessionId, 10)}</span>
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5 opacity-60 group-hover:opacity-100" />}
          </button>

          <button
            onClick={p.onReset}
            disabled={p.resetting}
            className="flex items-center gap-1.5 rounded-xl bg-slate-900/80 px-3 py-1.5 text-xs font-medium text-slate-200 ring-1 ring-slate-700 transition hover:bg-slate-800 disabled:opacity-50"
          >
            <RotateCcw className={cx("h-3.5 w-3.5", p.resetting && "animate-spin")} />
            Reset Demo
          </button>

          <button
            onClick={p.onToggleInspector}
            className={cx(
              "flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold ring-1 transition",
              p.inspectorOpen
                ? "bg-gradient-to-r from-[#00CAFF]/25 to-[#6366F1]/25 text-white ring-cyan-400/50"
                : "bg-slate-900/80 text-slate-200 ring-slate-700 hover:bg-slate-800",
            )}
          >
            {p.inspectorOpen ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
            MCP Inspector
            <span className="rounded-md bg-slate-950/70 px-1.5 font-mono text-[10px] text-cyan-300">{p.frameCount}</span>
          </button>
        </div>
      </div>
    </header>
  );
}
