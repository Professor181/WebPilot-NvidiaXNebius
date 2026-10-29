"use client";

import { useState } from "react";
import { X, Terminal, RefreshCw, Wrench, Radio, ChevronDown, ChevronRight, Activity, FolderOpen } from "lucide-react";
import McpPrimitivesExplorer from "./McpPrimitivesExplorer";
import type { ClientFrame, McpLogFrame, McpServerInfo, McpTool } from "@/types/webpilot";
import { MCP_ENDPOINT } from "@/lib/mcpClient";
import { cx, prettyJson, shortId, timeOf } from "@/lib/format";

type Tab = "frames" | "headers" | "tools" | "primitives";

function statusClass(code: number): string {
  if (code === 403 || code >= 500) return "bg-rose-500/20 text-rose-200 ring-rose-500/50";
  if (code === 202) return "bg-amber-500/15 text-amber-200 ring-amber-400/50";
  if (code >= 400) return "bg-orange-500/15 text-orange-200 ring-orange-400/50";
  return "bg-emerald-500/15 text-emerald-200 ring-emerald-500/40";
}

function frameFlags(frame: McpLogFrame) {
  const resp = frame.response as { result?: { isError?: boolean; structuredContent?: { error_code?: string } }; error?: { code: number } } | null;
  return {
    toolError: Boolean(resp?.result?.isError),
    errorCode: resp?.result?.structuredContent?.error_code,
    rpcError: resp?.error?.code,
  };
}

function FrameRow({ frame }: { frame: McpLogFrame }) {
  const [open, setOpen] = useState(false);
  const { toolError, errorCode, rpcError } = frameFlags(frame);
  return (
    <li className="rounded-xl bg-obsidian-950/80 ring-1 ring-slate-800">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        {open ? <ChevronDown className="h-3 w-3 text-slate-500" /> : <ChevronRight className="h-3 w-3 text-slate-500" />}
        <span className={cx("rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ring-1", statusClass(frame.status_code))}>{frame.status_code}</span>
        <span className="truncate font-mono text-[11px] text-slate-100">
          {frame.method}
          {frame.tool_name && <span className="text-cyan-300"> · {frame.tool_name}</span>}
        </span>
        {toolError && (
          <span className="shrink-0 rounded bg-rose-500/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-rose-300 ring-1 ring-rose-500/40" title="Tool execution error returned as result.isError (SEP-1303)">
            SEP-1303 isError{errorCode ? ` · ${errorCode}` : ""}
          </span>
        )}
        {rpcError !== undefined && (
          <span className="shrink-0 rounded bg-orange-500/15 px-1.5 py-0.5 font-mono text-[9px] font-bold text-orange-300 ring-1 ring-orange-500/40">
            JSON-RPC {rpcError}
          </span>
        )}
        <span className="ml-auto shrink-0 font-mono text-[10px] text-slate-500">{frame.latency_ms.toFixed(1)} ms</span>
      </button>
      {open && (
        <div className="space-y-2 border-t border-slate-800 px-3 py-2">
          <p className="font-mono text-[10px] text-slate-500">
            #{frame.log_id} · {timeOf(frame.timestamp)} · Mcp-Session-Id: <span className="text-cyan-300">{frame.session_id ?? "—"}</span>
          </p>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">→ request</p>
          <pre className="max-h-56 overflow-auto rounded-lg bg-black/40 p-2 font-mono text-[10px] leading-relaxed text-sky-200">{prettyJson(frame.request)}</pre>
          <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">← response</p>
          <pre className="max-h-72 overflow-auto rounded-lg bg-black/40 p-2 font-mono text-[10px] leading-relaxed text-emerald-200">
            {frame.response === null ? "(no body — HTTP 202 Accepted / 204 No Content)" : prettyJson(frame.response)}
          </pre>
        </div>
      )}
    </li>
  );
}

interface Props {
  open: boolean;
  onClose: () => void;
  logs: McpLogFrame[];
  clientFrames: ClientFrame[];
  tools: McpTool[];
  sessionId: string | null;
  protocolVersion: string | null;
  serverInfo: McpServerInfo | null;
  autoRefresh: boolean;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
}

export default function McpInspectorDrawer(p: Props) {
  const [tab, setTab] = useState<Tab>("frames");
  const sepErrors = p.logs.filter((f) => frameFlags(f).toolError).length;
  const avg = p.logs.length ? p.logs.reduce((s, f) => s + f.latency_ms, 0) / p.logs.length : 0;

  return (
    <aside
      className={cx(
        "fixed bottom-0 right-0 top-0 z-40 flex w-full max-w-[560px] flex-col border-l border-slate-800 bg-obsidian-950/95 shadow-2xl backdrop-blur-xl transition-transform duration-300",
        p.open ? "translate-x-0" : "translate-x-full",
      )}
      aria-hidden={!p.open}
    >
      <div className="border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-cyan-300" />
          <h2 className="text-sm font-bold text-white">Live MCP Protocol Inspector</h2>
          <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">{p.protocolVersion ?? "2025-11-25"}</span>
          <button onClick={p.onClose} className="ml-auto rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="Close inspector">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 font-mono text-[10px] text-slate-400">
          <span className="truncate">POST {MCP_ENDPOINT}</span>
          <span className="truncate">server: {p.serverInfo ? `${p.serverInfo.name}@${p.serverInfo.version}` : "—"}</span>
          <span className="truncate">
            Mcp-Session-Id: <span className="text-cyan-300">{shortId(p.sessionId, 16)}</span>
          </span>
          <span>
            {p.logs.length} frames · avg {avg.toFixed(1)} ms · <span className="text-rose-300">{sepErrors} SEP-1303</span>
          </span>
        </div>
        <div className="mt-3 flex items-center gap-1">
          {(
            [
              ["frames", "JSON-RPC frames", Activity],
              ["headers", "HTTP headers", Radio],
              ["tools", `Tools (${p.tools.length})`, Wrench],
              ["primitives", "Resources & Prompts", FolderOpen],
            ] as const
          ).map(([id, label, Icon]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cx(
                "flex items-center gap-1 rounded-lg px-2.5 py-1 text-[11px] font-semibold",
                tab === id ? "bg-slate-800 text-white ring-1 ring-cyan-500/40" : "text-slate-400 hover:text-slate-200",
              )}
            >
              <Icon className="h-3 w-3" /> {label}
            </button>
          ))}
          <label className="ml-auto flex items-center gap-1 text-[10px] text-slate-400">
            <input type="checkbox" checked={p.autoRefresh} onChange={p.onToggleAutoRefresh} className="accent-cyan-400" /> live
          </label>
          <button onClick={p.onRefresh} className="rounded-lg p-1 text-slate-400 hover:bg-slate-800 hover:text-white" aria-label="Refresh logs">
            <RefreshCw className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3">
        {tab === "frames" && (
          <ul className="space-y-1.5">
            {p.logs.length === 0 && <li className="p-6 text-center text-xs text-slate-500">No frames yet — run a scenario.</li>}
            {p.logs.map((f) => (
              <FrameRow key={f.log_id} frame={f} />
            ))}
          </ul>
        )}

        {tab === "headers" && (
          <ul className="space-y-1.5">
            {p.clientFrames.length === 0 && <li className="p-6 text-center text-xs text-slate-500">Browser-side HTTP exchanges appear here.</li>}
            {[...p.clientFrames].reverse().map((f) => (
              <li key={f.id} className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-slate-800">
                <div className="flex items-center gap-2">
                  <span className={cx("rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ring-1", statusClass(f.status))}>{f.status}</span>
                  <span className="font-mono text-[11px] text-slate-100">
                    {f.method}
                    {f.tool && <span className="text-cyan-300"> · {f.tool}</span>}
                  </span>
                  <span className="ml-auto font-mono text-[10px] text-slate-500">
                    {f.latencyMs} ms · {timeOf(f.at)}
                  </span>
                </div>
                <div className="mt-2 grid gap-1 font-mono text-[10px]">
                  {Object.entries(f.requestHeaders).map(([k, v]) => (
                    <span key={`rq-${k}`} className="truncate text-sky-300/90">
                      → {k}: {v}
                    </span>
                  ))}
                  {Object.entries(f.responseHeaders)
                    .filter(([k]) => /mcp|content-type|content-length|access-control/i.test(k))
                    .map(([k, v]) => (
                      <span key={`rs-${k}`} className={cx("truncate", /mcp-session-id/i.test(k) ? "text-cyan-300" : "text-emerald-300/90")}>
                        ← {k}: {v}
                      </span>
                    ))}
                </div>
              </li>
            ))}
          </ul>
        )}

        {tab === "primitives" && <McpPrimitivesExplorer />}

        {tab === "tools" && (
          <ul className="space-y-2">
            {p.tools.map((t) => (
              <li key={t.name} className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-slate-800">
                <div className="flex items-center gap-2">
                  {t.icons?.[0] && <img src={t.icons[0].src} alt="" className="h-6 w-6 rounded-md" />}
                  <span className="break-all font-mono text-[12px] font-bold text-white">{t.name}</span>
                  {t._meta?.["webpilot/synthesized"] ? (
                    <span className="shrink-0 rounded bg-teal-500/15 px-1.5 py-0.5 text-[9px] font-bold text-teal-300 ring-1 ring-teal-500/40">SESSION · SYNTHESIZED</span>
                  ) : (
                    <span className="text-[11px] text-slate-400">{t.title}</span>
                  )}
                </div>
                <p className="mt-1.5 line-clamp-3 text-[11px] text-slate-400">{t.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {(["readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"] as const).map((h) => (
                    <span
                      key={h}
                      className={cx(
                        "rounded px-1.5 py-0.5 font-mono text-[9px] ring-1",
                        t.annotations[h]
                          ? h === "destructiveHint"
                            ? "bg-rose-500/15 text-rose-200 ring-rose-500/40"
                            : "bg-emerald-500/10 text-emerald-200 ring-emerald-500/30"
                          : "bg-slate-800/60 text-slate-500 ring-slate-700",
                      )}
                    >
                      {h}: {String(t.annotations[h])}
                    </span>
                  ))}
                </div>
                <p className="mt-2 font-mono text-[10px] text-slate-500">
                  required: {t.inputSchema.required.join(", ") || "—"} · props: {Object.keys(t.inputSchema.properties).join(", ")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
