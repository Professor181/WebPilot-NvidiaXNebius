"use client";

import { useState } from "react";
import { FolderOpen, FileJson, MessageSquareText, Play, LoaderCircle, LayoutTemplate } from "lucide-react";
import type { McpPrompt, McpResource } from "@/types/webpilot";
import { getPrompt, listPrompts, listResources, listResourceTemplates, readResource } from "@/lib/mcpClient";
import { cx, prettyJson } from "@/lib/format";

const PROMPT_DEFAULTS: Record<string, Record<string, string>> = {
  alexa_check_attendance_threshold: { portal_url: "/sandbox/attendance", min_percentage: "75" },
  alexa_compare_and_buy_with_approval: { product_query: "laptop", max_budget_inr: "80000", min_ram_gb: "16" },
  alexa_audit_live_webpage: { url: "https://news.ycombinator.com", question: "list the top 3 AI stories by points" },
  alexa_analyze_github_repository: { repo_url: "https://github.com/modelcontextprotocol/servers" },
};

interface Frame {
  method: string;
  params: Record<string, unknown>;
  latencyMs: number;
  ok: boolean;
  body: unknown;
}

export default function McpPrimitivesExplorer() {
  const [resources, setResources] = useState<McpResource[]>([]);
  const [prompts, setPrompts] = useState<McpPrompt[]>([]);
  const [selectedPrompt, setSelectedPrompt] = useState<string | null>(null);
  const [args, setArgs] = useState<Record<string, string>>({});
  const [frame, setFrame] = useState<Frame | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const run = async (label: string, method: string, params: Record<string, unknown>, fn: () => Promise<{ result: unknown; latencyMs: number }>) => {
    setBusy(label);
    try {
      const { result, latencyMs } = await fn();
      let body: unknown = result;
      const contents = (result as { contents?: { text: string; mimeType: string }[] }).contents;
      if (contents?.[0]?.mimeType === "application/json") {
        try {
          body = { ...(result as object), parsed_text: JSON.parse(contents[0].text) };
        } catch {
          /* keep raw */
        }
      }
      setFrame({ method, params, latencyMs, ok: true, body });
      return result;
    } catch (err) {
      setFrame({ method, params, latencyMs: 0, ok: false, body: { error: (err as Error).message, code: (err as { rpcCode?: number }).rpcCode } });
      return null;
    } finally {
      setBusy(null);
    }
  };

  const doListResources = async () => {
    const r = (await run("rl", "resources/list", {}, listResources)) as { resources: McpResource[] } | null;
    if (r) setResources(r.resources);
  };
  const doListPrompts = async () => {
    const r = (await run("pl", "prompts/list", {}, listPrompts)) as { prompts: McpPrompt[] } | null;
    if (r) setPrompts(r.prompts);
  };
  const choosePrompt = (name: string) => {
    setSelectedPrompt(name);
    setArgs(PROMPT_DEFAULTS[name] ?? {});
  };
  const prompt = prompts.find((p) => p.name === selectedPrompt);

  const btn = "flex items-center gap-1 rounded-lg bg-slate-900 px-2.5 py-1.5 text-[11px] font-semibold text-slate-200 ring-1 ring-slate-700 hover:ring-cyan-500/50 disabled:opacity-40";

  return (
    <div className="space-y-3">
      <p className="text-[11px] leading-relaxed text-slate-400">
        WebPilot implements all three MCP 2025-11-25 server primitives — <span className="text-cyan-300">tools</span>,{" "}
        <span className="text-cyan-300">resources</span> and <span className="text-cyan-300">prompts</span>. Every button sends a live JSON-RPC request over{" "}
        <code>/mcp</code> with this session&apos;s <code>Mcp-Session-Id</code>.
      </p>

      <section className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-slate-800">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-200">
          <FolderOpen className="h-3.5 w-3.5 text-cyan-300" /> Resources
        </p>
        <div className="flex flex-wrap gap-1.5">
          <button className={btn} onClick={doListResources} disabled={busy !== null}>
            {busy === "rl" ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} resources/list
          </button>
          <button className={btn} onClick={() => run("rt", "resources/templates/list", {}, listResourceTemplates)} disabled={busy !== null}>
            <LayoutTemplate className="h-3 w-3" /> resources/templates/list
          </button>
          <button className={btn} onClick={() => run("rf", "resources/read", { uri: "webpilot://security/firewall-report" }, () => readResource("webpilot://security/firewall-report"))} disabled={busy !== null}>
            <FileJson className="h-3 w-3" /> read firewall-report
          </button>
        </div>
        {resources.length > 0 && (
          <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto">
            {resources.map((r) => (
              <li key={r.uri}>
                <button
                  onClick={() => run("rr", "resources/read", { uri: r.uri }, () => readResource(r.uri))}
                  disabled={busy !== null}
                  className="w-full truncate rounded-md bg-slate-900/70 px-2 py-1 text-left font-mono text-[10px] text-slate-300 ring-1 ring-slate-800 hover:text-cyan-300"
                  title={r.description}
                >
                  resources/read → {r.uri}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-xl bg-obsidian-950/80 p-3 ring-1 ring-slate-800">
        <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-200">
          <MessageSquareText className="h-3.5 w-3.5 text-indigo-300" /> Prompts (Alexa+ voice templates)
        </p>
        <button className={btn} onClick={doListPrompts} disabled={busy !== null}>
          {busy === "pl" ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} prompts/list
        </button>
        {prompts.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {prompts.map((p) => (
              <button
                key={p.name}
                onClick={() => choosePrompt(p.name)}
                className={cx("rounded-md px-2 py-1 font-mono text-[10px] ring-1", selectedPrompt === p.name ? "bg-indigo-500/20 text-indigo-100 ring-indigo-400/60" : "bg-slate-900 text-slate-400 ring-slate-800")}
              >
                {p.name}
              </button>
            ))}
          </div>
        )}
        {prompt && (
          <form
            className="mt-2 space-y-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              void run("pg", "prompts/get", { name: prompt.name, arguments: args }, () => getPrompt(prompt.name, args));
            }}
          >
            {(prompt.arguments ?? []).map((a) => (
              <label key={a.name} className="flex items-center gap-2 text-[10px] text-slate-400">
                <span className="w-28 shrink-0 font-mono">
                  {a.name}
                  {a.required && <span className="text-rose-400">*</span>}
                </span>
                <input
                  value={args[a.name] ?? ""}
                  onChange={(e) => setArgs({ ...args, [a.name]: e.target.value })}
                  placeholder={a.description}
                  className="min-w-0 flex-1 rounded-md bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-100 ring-1 ring-slate-700 focus:outline-none focus:ring-indigo-400/60"
                />
              </label>
            ))}
            <button type="submit" className={btn} disabled={busy !== null}>
              {busy === "pg" ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} prompts/get
            </button>
          </form>
        )}
      </section>

      {frame && (
        <section className="rounded-xl bg-black/40 p-3 ring-1 ring-slate-800">
          <p className="mb-1 flex items-center gap-2 font-mono text-[10px]">
            <span className={cx("rounded px-1.5 py-0.5 font-bold ring-1", frame.ok ? "bg-emerald-500/15 text-emerald-200 ring-emerald-500/40" : "bg-rose-500/15 text-rose-200 ring-rose-500/40")}>
              {frame.ok ? "result" : "error"}
            </span>
            <span className="text-slate-200">{frame.method}</span>
            <span className="truncate text-slate-500">{JSON.stringify(frame.params)}</span>
            <span className="ml-auto text-slate-500">{frame.latencyMs} ms</span>
          </p>
          <pre className="max-h-[420px] overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-emerald-100">{prettyJson(frame.body)}</pre>
        </section>
      )}
    </div>
  );
}
