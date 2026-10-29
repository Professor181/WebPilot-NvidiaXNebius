"use client";

import { useState } from "react";
import { Film, MousePointerClick, Keyboard, ArrowDownToLine, ShieldAlert, Lock, Flag, Navigation, CircleCheck, CircleX, Eye, Bot } from "lucide-react";
import type { AutopilotFrame, LiveAutopilotData } from "@/types/webpilot";
import { cx } from "@/lib/format";

const ACTION_STYLE: Record<string, { label: string; tone: string; Icon: typeof Film }> = {
  NAVIGATE: { label: "Navigate", tone: "text-sky-200 ring-sky-400/40 bg-sky-500/10", Icon: Navigation },
  FILL_AND_SUBMIT: { label: "Type + submit", tone: "text-cyan-200 ring-cyan-400/40 bg-cyan-500/10", Icon: Keyboard },
  FILL: { label: "Type", tone: "text-cyan-200 ring-cyan-400/40 bg-cyan-500/10", Icon: Keyboard },
  CLICK: { label: "Click", tone: "text-indigo-200 ring-indigo-400/40 bg-indigo-500/10", Icon: MousePointerClick },
  SCROLL_DOWN: { label: "Scroll", tone: "text-slate-200 ring-slate-500/40 bg-slate-700/30", Icon: ArrowDownToLine },
  ASK_HUMAN_APPROVAL: { label: "Approval gate", tone: "text-amber-200 ring-amber-400/50 bg-amber-500/10", Icon: Lock },
  APPROVED_CLICK: { label: "Approved click", tone: "text-emerald-200 ring-emerald-400/50 bg-emerald-500/10", Icon: MousePointerClick },
  HANDOFF: { label: "Payment handoff", tone: "text-amber-200 ring-amber-400/50 bg-amber-500/10", Icon: Lock },
  WAIT_FOR_HUMAN: { label: "You signed in", tone: "text-violet-200 ring-violet-400/40 bg-violet-500/10", Icon: Eye },
  BLOCKED: { label: "Blocked", tone: "text-rose-200 ring-rose-400/50 bg-rose-500/10", Icon: ShieldAlert },
  DONE: { label: "Extract + answer", tone: "text-emerald-200 ring-emerald-400/50 bg-emerald-500/10", Icon: Flag },
};

function host(url: string): string {
  try {
    const u = new URL(url);
    return u.host + (u.pathname.length > 1 ? u.pathname : "");
  } catch {
    return url;
  }
}

function Frame({ frame, active, onSelect }: { frame: AutopilotFrame; active: boolean; onSelect: () => void }) {
  const style = ACTION_STYLE[frame.action] ?? ACTION_STYLE.CLICK;
  return (
    <button
      onClick={onSelect}
      className={cx(
        "group flex w-44 shrink-0 flex-col overflow-hidden rounded-xl bg-obsidian-950/70 text-left ring-1 transition",
        active ? "ring-cyan-400/70 shadow-glow" : "ring-slate-800 hover:ring-slate-600",
      )}
    >
      <div className="relative h-24 w-full bg-slate-900">
        {frame.screenshot_url ? (
          <img src={frame.screenshot_url} alt={`Step ${frame.step}`} className="h-full w-full object-cover object-top" />
        ) : (
          <div className="flex h-full items-center justify-center text-[10px] text-slate-500">no screenshot</div>
        )}
        <span className="absolute left-1.5 top-1.5 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-[10px] font-bold text-cyan-200">#{frame.step}</span>
        <span className="absolute right-1.5 top-1.5">
          {frame.verified ? <CircleCheck className="h-4 w-4 text-emerald-400" /> : <CircleX className="h-4 w-4 text-rose-400" />}
        </span>
      </div>
      <div className="space-y-1 p-2">
        <span className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-bold ring-1", style.tone)}>
          <style.Icon className="h-3 w-3" /> {style.label}
        </span>
        <p className="line-clamp-2 text-[11px] leading-snug text-slate-200 [overflow-wrap:anywhere]">{frame.description}</p>
        <p className="truncate font-mono text-[10px] text-slate-500">{host(frame.url_after)}</p>
      </div>
    </button>
  );
}

export default function LiveFilmstrip({ data }: { data: LiveAutopilotData }) {
  const frames = data.autopilot?.filmstrip ?? [];
  const [selected, setSelected] = useState<number | null>(null);
  if (!frames.length) return null;
  const current = frames[selected ?? frames.length - 1] ?? frames[frames.length - 1];
  const decision = data.autopilot.decisions?.find((d) => d.step === current.step);
  return (
    <div className="space-y-3 rounded-2xl bg-slate-950/60 p-4 ring-1 ring-cyan-500/20">
      <div className="flex flex-wrap items-center gap-2">
        <Film className="h-4 w-4 text-cyan-300" />
        <h3 className="text-sm font-bold text-slate-100">Live Browser Action Filmstrip</h3>
        <span className="rounded-md bg-slate-900 px-2 py-0.5 text-[10px] font-semibold text-slate-400 ring-1 ring-slate-800">
          {data.autopilot.mode?.connection === "cdp"
            ? `your running Chrome (CDP, ${data.autopilot.mode?.reused_tab ? "your open tab" : "new tab"})`
            : `${data.autopilot.mode?.headed ? "visible window" : "headless"} · ${data.autopilot.mode?.persistent ? "persistent profile" : "private profile"}`}
          {data.autopilot.discovery ? " · site discovered by web search" : ""}
        </span>
        <span className="ml-auto text-[10px] text-slate-500">{frames.length} steps · no site-specific selectors</span>
      </div>
      <div className="flex gap-2 overflow-x-auto pb-1">
        {frames.map((f, i) => (
          <Frame key={`${f.step}-${f.action}-${i}`} frame={f} active={current === f} onSelect={() => setSelected(i)} />
        ))}
      </div>
      <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
        <div className="overflow-hidden rounded-xl bg-slate-900 ring-1 ring-slate-800">
          {current.screenshot_url ? (
            <a href={current.screenshot_url} target="_blank" rel="noreferrer">
              <img src={current.screenshot_url} alt={`WebPilot step ${current.step}`} className="max-h-[340px] w-full object-cover object-top" />
            </a>
          ) : (
            <div className="flex h-40 items-center justify-center text-xs text-slate-500">No screenshot for this step</div>
          )}
        </div>
        <div className="space-y-2 text-xs">
          <p className="font-semibold text-slate-100 [overflow-wrap:anywhere]">
            Step #{current.step}: {current.description}
          </p>
          <p className="text-slate-400">
            <span className="text-slate-500">Page:</span> {current.page_title || "—"}
          </p>
          <p className="break-all font-mono text-[10px] text-cyan-300/80">{current.url_after}</p>
          {decision && (
            <div className="rounded-lg bg-obsidian-950/70 p-2 ring-1 ring-slate-800">
              <p className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-wider text-slate-400">
                <Bot className="h-3 w-3" /> Decision · {decision.decided_by}
              </p>
              <p className="mt-1 text-slate-300 [overflow-wrap:anywhere]">{decision.reason}</p>
              {decision.llm_note && <p className="mt-1 text-[10px] text-amber-300/80 [overflow-wrap:anywhere]">LLM proposal: {decision.llm_note}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
