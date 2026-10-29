"use client";

import { useState } from "react";
import { Zap, Mic, Send, LoaderCircle, CircleCheck, Clock, Ban } from "lucide-react";
import type { InterruptResponse, Task } from "@/types/webpilot";
import { interruptTask, BackendError } from "@/lib/mcpClient";
import { useSpeechCapture } from "@/lib/useSpeechCapture";
import { cx, timeOf } from "@/lib/format";

const SUGGESTIONS: Record<string, string[]> = {
  laptop_shopping: [
    "Wait, change the budget from 80000 to 70000",
    "Skip ElectroHub and only check ByteBazaar",
    "Actually I need 32GB RAM and my budget is 1.5 lakh",
  ],
  attendance_audit: ["Use 85 percent as the threshold instead"],
  grant_form: ["Change the project title to WebPilot Voice Copilot", "Set the track to Generative AI Track"],
};

interface LogEntry {
  id: string;
  at: string;
  instruction: string;
  mode: InterruptResponse["mode"] | "ERROR";
  detail: string;
}

interface Props {
  task: Task;
  live: boolean;
  onApplied: (response: InterruptResponse) => void;
}

export default function InterruptionBar({ task, live, onApplied }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [log, setLog] = useState<LogEntry[]>([]);
  const scenario = task.interpreted_goal?.scenario;
  const suggestions = SUGGESTIONS[scenario] ?? [];

  const send = async (instruction: string) => {
    const value = instruction.trim();
    if (!value || sending) return;
    setSending(true);
    try {
      const res = await interruptTask(task.task_id, value);
      setLog((l) => [
        {
          id: res.interrupt.interrupt_id,
          at: new Date().toISOString(),
          instruction: value,
          mode: res.mode,
          detail:
            res.mode === "QUEUED"
              ? `Queued: ${res.changes.join("; ")} — applied at the next step checkpoint`
              : res.mode === "APPLIED"
                ? `Re-planned: ${res.changes.join("; ")}`
                : res.interrupt.outcome ?? "Rejected",
        },
        ...l,
      ].slice(0, 5));
      if (res.mode !== "QUEUED") onApplied(res);
      setText("");
    } catch (err) {
      const e = err as BackendError;
      setLog((l) => [{ id: `${Date.now()}`, at: new Date().toISOString(), instruction: value, mode: "ERROR" as const, detail: e.message }, ...l].slice(0, 5));
    } finally {
      setSending(false);
    }
  };

  const speech = useSpeechCapture((spoken) => {
    setText(spoken);
    void send(spoken);
  });

  if (!suggestions.length) return null;

  return (
    <section className="rounded-2xl bg-gradient-to-r from-amber-500/10 via-obsidian-850/80 to-indigo-500/10 p-4 ring-1 ring-amber-400/40 animate-fade-up">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Zap className="h-4 w-4 text-amber-300" />
        <h2 className="text-sm font-bold text-amber-100">Change your mind mid-task</h2>
        <span className="text-[11px] text-slate-400">
          {live
            ? "Task is running — your change is queued and applied at the next step checkpoint."
            : "Task is paused at the approval gate — your change revokes the pending approval and re-plans now."}
        </span>
      </div>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send(text);
        }}
      >
        <button
          type="button"
          onClick={speech.toggle}
          aria-label="Speak an interruption"
          className={cx(
            "relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full ring-1",
            speech.listening ? "bg-amber-400 text-obsidian-950 ring-amber-200" : "bg-slate-900 text-amber-200 ring-amber-500/40",
          )}
        >
          {speech.listening && <span className="absolute inset-0 animate-ping rounded-full bg-amber-400/30" />}
          <Mic className="relative h-4 w-4" />
        </button>
        <input
          value={speech.listening && speech.interim ? speech.interim : text}
          onChange={(e) => setText(e.target.value)}
          placeholder='"Wait — skip ElectroHub and only check ByteBazaar"'
          className="min-w-0 flex-1 rounded-xl bg-slate-950/80 px-3 py-2.5 text-sm text-slate-100 ring-1 ring-amber-500/30 placeholder:text-slate-500 focus:outline-none focus:ring-amber-400/70"
        />
        <button
          type="submit"
          disabled={sending || !text.trim()}
          className="flex h-10 items-center gap-1.5 rounded-xl bg-amber-400 px-4 text-sm font-bold text-obsidian-950 transition hover:brightness-110 disabled:opacity-40"
        >
          {sending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />} Interrupt
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {suggestions.map((s) => (
          <button
            key={s}
            onClick={() => void send(s)}
            disabled={sending}
            className="rounded-lg bg-slate-950/70 px-2 py-1 text-[11px] text-amber-100/90 ring-1 ring-amber-500/30 hover:bg-amber-500/10 disabled:opacity-40"
          >
            “{s}”
          </button>
        ))}
      </div>
      {speech.error && <p className="mt-1 text-[11px] text-amber-300">{speech.error}</p>}
      {log.length > 0 && (
        <ul className="mt-3 space-y-1">
          {log.map((e) => (
            <li key={e.id} className="flex items-start gap-2 text-[11px]">
              {e.mode === "APPLIED" ? (
                <CircleCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
              ) : e.mode === "QUEUED" ? (
                <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" />
              ) : (
                <Ban className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
              )}
              <span className="font-mono text-slate-500">{timeOf(e.at)}</span>
              <span className="text-slate-300">“{e.instruction}”</span>
              <span className={cx(e.mode === "ERROR" || e.mode === "REJECTED" ? "text-rose-300" : "text-amber-200")}>→ {e.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
