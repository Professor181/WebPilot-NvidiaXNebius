"use client";

import {
  CircleCheck,
  CircleX,
  CircleDot,
  Circle,
  LoaderCircle,
  RefreshCw,
  Ban,
  KeyRound,
  ShieldAlert,
  Layers,
  ExternalLink,
} from "lucide-react";
import type { PlanStep, StepStatus, Task } from "@/types/webpilot";
import { STEP_STATUS_STYLE, TASK_STATUS_STYLE, TIER_STYLE, cx, humanize, timeOf } from "@/lib/format";

function StepIcon({ status }: { status: StepStatus }) {
  const cls = "h-4 w-4";
  switch (status) {
    case "SUCCEEDED":
      return <CircleCheck className={cx(cls, "text-emerald-400")} />;
    case "FAILED":
      return <CircleX className={cx(cls, "text-rose-400")} />;
    case "FAILED_RECOVERED":
      return <RefreshCw className={cx(cls, "text-amber-400")} />;
    case "RUNNING":
      return <LoaderCircle className={cx(cls, "animate-spin text-cyan-300")} />;
    case "AWAITING_APPROVAL":
      return <KeyRound className={cx(cls, "text-amber-300")} />;
    case "BLOCKED":
      return <ShieldAlert className={cx(cls, "text-rose-400")} />;
    case "SKIPPED":
    case "CANCELLED":
      return <Ban className={cx(cls, "text-slate-500")} />;
    case "PENDING":
      return <Circle className={cx(cls, "text-slate-600")} />;
    default:
      return <CircleDot className={cls} />;
  }
}

function duration(step: PlanStep): string | null {
  if (!step.started_at || !step.finished_at) return null;
  const ms = new Date(step.finished_at).getTime() - new Date(step.started_at).getTime();
  if (Number.isNaN(ms)) return null;
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

interface Props {
  task: Task | null;
  live: boolean;
}

export default function PlanDagStepper({ task, live }: Props) {
  if (!task) {
    return (
      <section className="glass-card p-4">
        <h2 className="section-title mb-3">Execution plan</h2>
        <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-slate-500">
          <Layers className="h-8 w-8 text-slate-700" />
          The goal compiler&apos;s plan appears here as a live DAG with capability tiers.
        </div>
      </section>
    );
  }

  const steps = [...task.plan_steps].sort((a, b) => a.index - b.index);
  const done = steps.filter((s) => ["SUCCEEDED", "FAILED_RECOVERED"].includes(s.status)).length;
  const pct = steps.length ? Math.round((done / steps.length) * 100) : 0;

  return (
    <section className="glass-card p-4">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="section-title">Execution plan</h2>
          <p className="mt-1 truncate text-xs text-slate-400" title={task.goal}>
            {task.interpreted_goal?.intent ?? task.goal}
          </p>
        </div>
        <span className={cx("status-pill", TASK_STATUS_STYLE[task.status])}>
          {live && <LoaderCircle className="h-3 w-3 animate-spin" />}
          {task.status}
        </span>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5 font-mono text-[10px]">
        <span className="chip">{task.task_category}</span>
        <span className="chip">{task.environment_mode}</span>
        <span className="chip">{task.interpreted_goal?.scenario}</span>
        <span className="chip text-slate-500">{task.task_id}</span>
      </div>

      <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-gradient-to-r from-[#00CAFF] to-[#6366F1] transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>

      <ol className="relative">
        {steps.map((step, i) => {
          const style = STEP_STATUS_STYLE[step.status];
          const tier = TIER_STYLE[step.capability_tier];
          const isPivot = i > 0 && steps[i - 1].status === "FAILED_RECOVERED";
          const isInterrupt = step.key.startsWith("interrupt_");
          const superseded = step.key.includes("@v");
          return (
            <li
              key={step.step_id}
              className={cx(
                "relative flex gap-3 pb-4 last:pb-0 animate-fade-up",
                superseded && "opacity-45",
                isInterrupt && "-mx-2 rounded-xl bg-amber-500/10 px-2 pt-2 ring-1 ring-amber-400/40",
              )}
            >
              {i < steps.length - 1 && (
                <span
                  className={cx(
                    "absolute left-[11px] top-6 h-[calc(100%-12px)] w-px",
                    step.status === "FAILED_RECOVERED" ? "border-l border-dashed border-amber-400/70" : "bg-slate-700/80",
                  )}
                />
              )}
              <span className={cx("relative z-10 mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-obsidian-950 ring-2", style.ring)}>
                <StepIcon status={step.status} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={cx(
                      "text-[13px] font-medium",
                      step.status === "PENDING" ? "text-slate-400" : isInterrupt ? "text-amber-100" : "text-slate-100",
                      superseded && "line-through decoration-slate-500",
                    )}
                  >
                    {step.title}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  <span className={cx("rounded-md px-1.5 py-0.5 text-[10px] font-semibold ring-1", tier.cls)}>{tier.label}</span>
                  <span className={cx("font-mono text-[10px]", style.text)}>{step.status}</span>
                  {duration(step) && <span className="font-mono text-[10px] text-slate-500">{duration(step)}</span>}
                  {step.evidence_ids.length > 0 && (
                    <span className="font-mono text-[10px] text-slate-500">· {step.evidence_ids.length} evidence</span>
                  )}
                  {step.target_url && (
                    <a href={step.target_url} target="_blank" rel="noreferrer" className="text-slate-500 hover:text-cyan-300" title={step.target_url}>
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
                {step.detail && <p className="mt-1 text-xs leading-relaxed text-slate-400">{step.detail}</p>}
                {step.status === "FAILED_RECOVERED" && (
                  <div className="mt-2 rounded-lg bg-amber-500/10 px-2.5 py-1.5 text-[11px] font-medium text-amber-200 ring-1 ring-amber-400/40">
                    ↻ Dynamic recovery: the planner re-routed instead of failing the task.
                  </div>
                )}
                {isPivot && (
                  <div className="mt-2 text-[11px] font-medium text-amber-300/90">Re-planned step inserted at runtime</div>
                )}
              </div>
              <span className="hidden shrink-0 font-mono text-[10px] text-slate-600 sm:block">{timeOf(step.finished_at ?? step.started_at)}</span>
            </li>
          );
        })}
      </ol>
      {task.interpreted_goal?.matched_signals?.length ? (
        <p className="mt-3 border-t border-slate-800 pt-2 font-mono text-[10px] text-slate-500">
          goal compiler: {task.interpreted_goal.compiler} · signals: {task.interpreted_goal.matched_signals.map(humanize).join(", ")}
        </p>
      ) : null}
    </section>
  );
}
