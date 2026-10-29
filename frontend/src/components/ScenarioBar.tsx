"use client";

import { useState } from "react";
import { GraduationCap, Laptop, FilePen, GitBranch, Trophy, Lock, ScanSearch, Search, Wand, Timer, Globe } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { TaskCategory } from "@/types/webpilot";
import { cx } from "@/lib/format";

export interface ScenarioChip {
  id: string;
  emoji: string;
  label: string;
  goal: string;
  category: TaskCategory;
  icon: LucideIcon;
  accent: string;
}

export const SCENARIOS: ScenarioChip[] = [
  {
    id: "hn-live",
    emoji: "🌐",
    label: "Live HackerNews Story Ranker (Real Internet + Chromium)",
    goal: "Open https://news.ycombinator.com, filter stories matching 'AI', 'LLM', 'Rust', or 'Show HN', and rank the top 5 by points",
    category: "READ_ANALYZE",
    icon: Globe,
    accent: "hover:ring-indigo-400/60 ring-indigo-500/30 sm:col-span-2",
  },
  {
    id: "attendance",
    emoji: "🎓",
    label: "College Attendance (<75%)",
    goal: "Open my college portal, check my attendance, and tell me which subjects are below 75%.",
    category: "READ_ANALYZE",
    icon: GraduationCap,
    accent: "hover:ring-cyan-400/60",
  },
  {
    id: "laptop",
    emoji: "💻",
    label: "Buy 16GB Laptop <₹80k (Recovery + Approval)",
    goal: "Find a laptop under ₹80,000 with 16GB RAM across stores, compare options, and buy the best one after I approve.",
    category: "ACT_WITH_APPROVAL",
    icon: Laptop,
    accent: "hover:ring-amber-400/60",
  },
  {
    id: "grant",
    emoji: "📝",
    label: "Fill Grant Application Form (Approval)",
    goal: "Open the National Innovation Grant portal, complete the application form using my project details, and show it to me before submitting.",
    category: "ACT_WITH_APPROVAL",
    icon: FilePen,
    accent: "hover:ring-amber-400/60",
  },
  {
    id: "github",
    emoji: "🐙",
    label: "Live GitHub Repo Analysis",
    goal: "Analyze https://github.com/modelcontextprotocol/servers, explain its architecture, and summarize recent open issues.",
    category: "READ_ANALYZE",
    icon: GitBranch,
    accent: "hover:ring-emerald-400/60",
  },
  {
    id: "profile",
    emoji: "🏆",
    label: "Developer Profile Comparison",
    goal: "Check how many coding questions I solved this month and compare my progress with my friend's public profile.",
    category: "RESEARCH_COMPARE",
    icon: Trophy,
    accent: "hover:ring-indigo-400/60",
  },
  {
    id: "erp",
    emoji: "🛡️",
    label: "Restricted ERP Login/CAPTCHA Wall",
    goal: "Open the restricted corporate ERP portal and submit my timesheet.",
    category: "ACT_WITH_APPROVAL",
    icon: Lock,
    accent: "hover:ring-rose-400/60",
  },
  {
    id: "synth",
    emoji: "🧪",
    label: "Novel Goal → Synthesized Tool (crypto moving averages)",
    goal: "Extract trending crypto indices and calculate 3-day moving averages",
    category: "READ_ANALYZE",
    icon: Wand,
    accent: "hover:ring-teal-400/60",
  },
];

const CATEGORY_STYLE: Record<TaskCategory, string> = {
  READ_ANALYZE: "text-cyan-300",
  RESEARCH_COMPARE: "text-indigo-300",
  ACT_WITH_APPROVAL: "text-amber-300",
};

const URL_SUGGESTIONS = ["/sandbox/electrohub", "/sandbox/login-wall", "/sandbox/application-form", "https://example.com"];

interface Props {
  disabled: boolean;
  activeId: string | null;
  slowMotion: boolean;
  onToggleSlowMotion: () => void;
  onRun: (chip: ScenarioChip) => void;
  onInspect: (url: string) => void;
}

export default function ScenarioBar({ disabled, activeId, slowMotion, onToggleSlowMotion, onRun, onInspect }: Props) {
  const [url, setUrl] = useState("");

  const submit = (value: string) => {
    const v = value.trim();
    if (v) onInspect(v);
  };

  return (
    <section className="glass-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="section-title">One-click judge demos</h2>
        <button
          onClick={onToggleSlowMotion}
          title="Adds a 1.5 s pause at every step checkpoint (parameters.pace_ms) so you can interrupt the task while it runs"
          className={cx(
            "flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] font-bold ring-1",
            slowMotion ? "bg-amber-400/20 text-amber-200 ring-amber-400/60" : "bg-slate-900 text-slate-400 ring-slate-700",
          )}
        >
          <Timer className="h-3 w-3" /> Slow-motion {slowMotion ? "ON" : "OFF"}
        </button>
      </div>
      <p className="-mt-1 mb-2 text-[11px] text-slate-500">
        Each chip sends a real <code className="text-cyan-300">create_web_task</code> over MCP. Slow-motion lets you interrupt mid-run.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {SCENARIOS.map((chip) => {
          const Icon = chip.icon;
          return (
            <button
              key={chip.id}
              disabled={disabled}
              onClick={() => onRun(chip)}
              title={chip.goal}
              className={cx(
                "group flex items-start gap-3 rounded-xl bg-slate-900/70 p-3 text-left ring-1 ring-slate-800 transition",
                chip.accent,
                activeId === chip.id && "ring-2 ring-cyan-400/70 bg-slate-800/80",
                disabled && "cursor-not-allowed opacity-50",
              )}
            >
              <span className="mt-0.5 text-lg leading-none">{chip.emoji}</span>
              <span className="min-w-0 flex-1">
                <span className="flex items-start gap-1.5 text-[13px] font-semibold leading-snug text-slate-100">
                  <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400 group-hover:text-slate-200" />
                  <span>{chip.label}</span>
                </span>
                <span className={cx("mt-0.5 block font-mono text-[10px] tracking-wide", CATEGORY_STYLE[chip.category])}>
                  {chip.category}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <form
        className="mt-3 flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          submit(url);
        }}
      >
        <div className="relative flex-1">
          <ScanSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Inspect any URL — https://… or /sandbox/…"
            className="w-full rounded-xl bg-slate-950/70 py-2.5 pl-9 pr-3 text-sm text-slate-100 ring-1 ring-slate-800 placeholder:text-slate-500 focus:outline-none focus:ring-cyan-500/60"
          />
        </div>
        <button
          type="submit"
          disabled={disabled || !url.trim()}
          className="flex items-center justify-center gap-1.5 rounded-xl bg-slate-800 px-4 py-2.5 text-sm font-semibold text-slate-100 ring-1 ring-slate-700 transition hover:bg-slate-700 disabled:opacity-40"
        >
          <Search className="h-4 w-4" /> inspect_website
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {URL_SUGGESTIONS.map((s) => (
          <button
            key={s}
            disabled={disabled}
            onClick={() => {
              setUrl(s);
              submit(s);
            }}
            className="rounded-lg bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-400 ring-1 ring-slate-800 hover:text-cyan-300 disabled:opacity-40"
          >
            {s}
          </button>
        ))}
      </div>
    </section>
  );
}
