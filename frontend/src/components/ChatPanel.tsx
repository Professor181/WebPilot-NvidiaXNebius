"use client";

import { useEffect, useRef, useState } from "react";
import {
  Mic,
  MicOff,
  Send,
  Volume2,
  VolumeX,
  LoaderCircle,
  Globe,
  Eye,
  KeyRound,
  MessageSquarePlus,
  Settings,
  LogIn,
  UserPlus,
  CircleCheck,
  CircleX,
  MonitorSmartphone,
} from "lucide-react";
import { getSpeechRecognition, stopSpeaking, ttsSupported, type SpeechRecognitionLike } from "@/lib/voice";
import { cx } from "@/lib/format";
import type { LiveAutopilotData, Task } from "@/types/webpilot";

export interface LiveWebOptions {
  universal: boolean;
  watch: boolean;
  persistent: boolean;
}

export interface ChatImage {
  url: string;
  caption: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "note";
  text: string;
  taskId?: string;
  status?: string;
  images?: ChatImage[];
  at: string;
}

export type SignInChoice =
  | { choice: "self" }
  | { choice: "signup" }
  | { choice: "cancel" }
  | { choice: "provide"; username: string; password: string; remember: boolean };

interface Props {
  messages: ChatMessage[];
  liveTask: Task | null;
  working: boolean;
  workingLabel: string | null;
  disabled: boolean;
  liveWeb: LiveWebOptions;
  onLiveWebChange: (next: LiveWebOptions) => void;
  ttsEnabled: boolean;
  onToggleTts: (enabled: boolean) => void;
  onSend: (text: string) => void;
  onNewChat: () => void;
  onOpenSettings: () => void;
  onListeningChange: (listening: boolean) => void;
  onSignIn: (choice: SignInChoice) => Promise<void>;
  onApprove: () => void;
  onReject: () => void;
  approvalBusy: boolean;
}

const EXAMPLES = [
  "Go on amazon, search for the best running shoes under 3000 and show me",
  "Search villas in Mahabaleshwar under 15k for check-in 12 Dec and check-out 15 Dec for 4 adults",
  "Open ChatGPT and ask it to plan a 3-day Goa trip",
];

function Toggle({ on, onClick, icon, label, title }: { on: boolean; onClick: () => void; icon: React.ReactNode; label: string; title: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      aria-pressed={on}
      className={cx(
        "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold ring-1 transition",
        on ? "bg-cyan-500/15 text-cyan-200 ring-cyan-400/50" : "bg-slate-900 text-slate-400 ring-slate-700 hover:ring-slate-500",
      )}
    >
      {icon} {label}
    </button>
  );
}

function SignInCard({ task, onSignIn }: { task: Task; onSignIn: Props["onSignIn"] }) {
  const req = (task.extracted_data as LiveAutopilotData | null)?.login_request;
  const [mode, setMode] = useState<"choose" | "provide">("choose");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [sending, setSending] = useState(false);
  if (!req) return null;
  const send = async (choice: SignInChoice) => {
    setSending(true);
    try {
      await onSignIn(choice);
    } finally {
      setSending(false);
      setPassword("");
    }
  };
  return (
    <div className="rounded-2xl bg-amber-500/10 p-3 ring-1 ring-amber-400/40">
      <p className="flex items-center gap-1.5 text-sm font-semibold text-amber-100">
        <LogIn className="h-4 w-4" /> {req.site} needs you to be signed in
        {req.has_saved_login && <span className="text-xs font-normal text-amber-200/80">(your saved login didn&apos;t work)</span>}
      </p>
      {mode === "choose" ? (
        <div className="mt-2 flex flex-wrap gap-2">
          <button disabled={sending} onClick={() => send({ choice: "self" })} className="chip-btn">
            <MonitorSmartphone className="h-3.5 w-3.5" /> I&apos;ll sign in myself in the browser window
          </button>
          <button disabled={sending} onClick={() => setMode("provide")} className="chip-btn">
            <KeyRound className="h-3.5 w-3.5" /> Use a login (saved securely)
          </button>
          {req.options.includes("signup") && (
            <button disabled={sending} onClick={() => send({ choice: "signup" })} className="chip-btn">
              <UserPlus className="h-3.5 w-3.5" /> Create an account with {req.signup_email}
            </button>
          )}
          <button disabled={sending} onClick={() => send({ choice: "cancel" })} className="chip-btn text-rose-200">
            <CircleX className="h-3.5 w-3.5" /> Cancel
          </button>
          {!req.options.includes("signup") && (
            <p className="w-full text-[11px] text-amber-200/70">Add your email in Settings to let me create accounts on new sites (I always ask first).</p>
          )}
        </div>
      ) : (
        <form
          className="mt-2 grid gap-2 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (username && password) send({ choice: "provide", username, password, remember });
          }}
        >
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="Email / username" autoComplete="username" className="chat-input-sm" />
          <input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" type="password" autoComplete="current-password" className="chat-input-sm" />
          <label className="flex items-center gap-2 text-xs text-amber-100/80">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Remember in Windows Credential Manager
          </label>
          <div className="flex gap-2 sm:justify-end">
            <button type="button" onClick={() => setMode("choose")} className="chip-btn">Back</button>
            <button type="submit" disabled={sending || !username || !password} className="chip-btn bg-amber-500/20">
              {sending ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <LogIn className="h-3.5 w-3.5" />} Sign in for me
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  if (m.role === "note") {
    return <p className="mx-auto max-w-[90%] text-center text-[11px] text-slate-500">{m.text}</p>;
  }
  const mine = m.role === "user";
  return (
    <div className={cx("flex", mine ? "justify-end" : "justify-start")}>
      <div
        className={cx(
          "max-w-[88%] rounded-2xl px-4 py-2.5 text-[14px] leading-relaxed ring-1",
          mine ? "rounded-br-sm bg-gradient-to-br from-[#00CAFF]/25 to-[#6366F1]/25 text-white ring-cyan-400/30" : "rounded-bl-sm bg-slate-800/80 text-slate-100 ring-slate-700/70",
        )}
      >
        <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{m.text}</p>
        {m.images && m.images.length > 0 && (
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {m.images.map((img, i) => (
              <a key={`${img.url}-${i}`} href={img.url} target="_blank" rel="noreferrer" className="group overflow-hidden rounded-lg bg-slate-950 ring-1 ring-slate-700 hover:ring-cyan-400/60">
                <img src={img.url} alt={img.caption} className="h-24 w-full object-cover object-top" />
                <p className="truncate px-1.5 py-1 text-[10px] text-slate-300">{img.caption}</p>
              </a>
            ))}
          </div>
        )}
        {m.status && !mine && <p className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">{m.status.replace(/_/g, " ")}</p>}
      </div>
    </div>
  );
}

export default function ChatPanel(props: Props) {
  const { messages, liveTask, working, workingLabel, disabled, liveWeb, onLiveWebChange, ttsEnabled, onToggleTts, onSend, onNewChat, onOpenSettings,
    onListeningChange, onSignIn, onApprove, onReject, approvalBusy } = props;
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [micError, setMicError] = useState<string | null>(null);
  const [micSupported, setMicSupported] = useState(true);
  const [ttsAvailable, setTtsAvailable] = useState(true);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    setMicSupported(Boolean(getSpeechRecognition()));
    setTtsAvailable(ttsSupported());
    return () => recognitionRef.current?.abort();
  }, []);
  useEffect(() => onListeningChange(listening), [listening, onListeningChange]);
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, working, liveTask?.status, liveTask?.updated_at]);
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text, interim]);

  const submit = (value: string) => {
    const goal = value.trim();
    if (!goal || disabled) return;
    onSend(goal);
    setText("");
    setInterim("");
  };

  const toggleMic = () => {
    setMicError(null);
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      setMicError("Voice capture isn't supported in this browser — use Chrome or Edge, or type your request.");
      return;
    }
    stopSpeaking();
    const rec = new Ctor();
    rec.lang = "en-IN";
    rec.interimResults = true;
    rec.continuous = false;
    rec.maxAlternatives = 1;
    let finalText = "";
    rec.onresult = (event) => {
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      setInterim(interimText);
      if (finalText) setText(finalText.trim());
    };
    rec.onerror = (event) => {
      const messagesMap: Record<string, string> = {
        "not-allowed": "Microphone permission was denied. Allow mic access for localhost and try again.",
        "no-speech": "I didn't hear anything. Tap the mic and try again.",
        "audio-capture": "No microphone was found.",
        network: "Speech recognition needs a network connection in this browser.",
      };
      setMicError(messagesMap[event.error] ?? `Speech recognition error: ${event.error}`);
    };
    rec.onend = () => {
      setListening(false);
      setInterim("");
      const spoken = finalText.replace(/^alexa[,\s]+/i, "").trim();
      if (spoken) submit(spoken);
    };
    recognitionRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch (err) {
      setMicError((err as Error).message);
    }
  };

  const data = (liveTask?.extracted_data ?? null) as LiveAutopilotData | null;
  const lastStep = liveTask?.plan_steps?.filter((s) => s.status === "RUNNING" || s.status === "SUCCEEDED").slice(-1)[0];
  const approvalPending = liveTask?.status === "AWAITING_APPROVAL" && liveTask.pending_approval?.status === "PENDING";

  return (
    <section className="glass-card flex flex-col p-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800/80 px-4 py-3">
        <h2 className="section-title mr-auto">Chat with Alexa+ · WebPilot</h2>
        <button onClick={onOpenSettings} className="chip-btn" title="Your email for sign-ups, saved logins and the WebPilot Chrome">
          <Settings className="h-3.5 w-3.5" /> Settings &amp; logins
        </button>
        <button onClick={onNewChat} disabled={working} className="chip-btn" title="End this conversation: closes its browser tab and deletes its screenshots">
          <MessageSquarePlus className="h-3.5 w-3.5" /> New chat
        </button>
        <button
          onClick={() => {
            if (ttsEnabled) stopSpeaking();
            onToggleTts(!ttsEnabled);
          }}
          disabled={!ttsAvailable}
          className={cx("chip-btn", ttsEnabled && "bg-cyan-500/15 text-cyan-200 ring-cyan-400/50")}
          title="Speak replies aloud"
        >
          {ttsEnabled ? <Volume2 className="h-3.5 w-3.5" /> : <VolumeX className="h-3.5 w-3.5" />} Voice
        </button>
      </div>

      <div ref={threadRef} className="flex h-[min(58vh,620px)] min-h-[320px] flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !working && (
          <div className="m-auto max-w-md text-center">
            <p className="text-base font-semibold text-slate-100">Tell me what to do on any website.</p>
            <p className="mt-1 text-sm text-slate-400">
              I use the WebPilot Chrome, where your logins are saved. You can follow up as you would with a person: &ldquo;is the first one available in size 10?&rdquo;, &ldquo;book it&rdquo;. I always stop at the payment page for you.
            </p>
            <div className="mt-3 flex flex-col gap-1.5">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => setText(ex)} className="rounded-lg bg-slate-900/80 px-3 py-2 text-left text-xs text-slate-300 ring-1 ring-slate-800 hover:ring-cyan-500/50">
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <Bubble key={m.id} m={m} />
        ))}
        {working && (
          <div className="flex justify-start">
            <div className="max-w-[88%] rounded-2xl rounded-bl-sm bg-slate-800/60 px-4 py-2.5 text-sm text-slate-300 ring-1 ring-slate-700/70">
              <p className="flex items-center gap-2">
                <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-cyan-300" />
                <span className="[overflow-wrap:anywhere]">{lastStep?.title ?? workingLabel ?? "Working on it…"}</span>
              </p>
              {data?.human_needed && <p className="mt-2 rounded-lg bg-violet-500/15 p-2 text-xs text-violet-100 ring-1 ring-violet-400/40">👋 {data.human_needed.message}</p>}
            </div>
          </div>
        )}
        {liveTask && data?.login_request && <SignInCard key={data.login_request.requested_at} task={liveTask} onSignIn={onSignIn} />}
        {approvalPending && !working && (
          <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-amber-500/10 p-3 ring-1 ring-amber-400/40">
            <p className="mr-auto text-sm text-amber-100">Approve “{String(liveTask?.pending_approval?.parameters?.button_text ?? "this step")}”? I&apos;ll stop at the payment page.</p>
            <button onClick={onApprove} disabled={approvalBusy} className="chip-btn bg-emerald-500/20 text-emerald-100 ring-emerald-400/50">
              {approvalBusy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <CircleCheck className="h-3.5 w-3.5" />} Yes, go ahead
            </button>
            <button onClick={onReject} disabled={approvalBusy} className="chip-btn text-rose-200">
              <CircleX className="h-3.5 w-3.5" /> No
            </button>
          </div>
        )}
      </div>

      <div className="border-t border-slate-800/80 px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <Toggle
            on={liveWeb.universal}
            onClick={() => onLiveWebChange({ ...liveWeb, universal: !liveWeb.universal })}
            icon={<Globe className="h-3.5 w-3.5" />}
            label="Any website (live)"
            title="Run typed or spoken goals on the real web through the site-agnostic browser autopilot"
          />
          <Toggle
            on={liveWeb.persistent}
            onClick={() => onLiveWebChange({ ...liveWeb, persistent: !liveWeb.persistent })}
            icon={<KeyRound className="h-3.5 w-3.5" />}
            label="Use my logged-in browser"
            title="Use the WebPilot Chrome (started automatically) where your sign-ins are kept, so sites recognise you"
          />
          <Toggle
            on={liveWeb.watch}
            onClick={() => onLiveWebChange({ ...liveWeb, watch: !liveWeb.watch })}
            icon={<Eye className="h-3.5 w-3.5" />}
            label="Show the browser"
            title="Keep the browser window visible (slow motion) so you can watch and take over at payment"
          />
        </div>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit(text);
          }}
        >
          <button
            type="button"
            onClick={toggleMic}
            disabled={disabled}
            aria-label={listening ? "Stop listening" : "Start voice command"}
            className={cx(
              "relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full ring-1 transition",
              listening ? "bg-gradient-to-br from-[#00CAFF] to-[#6366F1] text-white ring-cyan-300 shadow-glow" : "bg-slate-900 text-slate-200 ring-slate-700 hover:ring-cyan-400/60",
              disabled && "opacity-40",
            )}
          >
            {listening && <span className="absolute inset-0 animate-ping rounded-full bg-cyan-400/30" />}
            {micSupported ? <Mic className="relative h-5 w-5" /> : <MicOff className="h-5 w-5" />}
          </button>
          <textarea
            ref={inputRef}
            rows={3}
            value={listening && interim ? interim : text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(text);
              }
            }}
            placeholder={
              listening
                ? "Listening…"
                : approvalPending
                  ? "Say “yes” to go ahead, “no” to stop, or ask something else…"
                  : "Message Alexa+ — e.g. “search the best running shoes under 3000 on amazon and show me” (Shift+Enter for a new line)"
            }
            className="min-h-[84px] min-w-0 flex-1 resize-none rounded-2xl bg-slate-950/70 px-4 py-3 text-[15px] leading-relaxed text-slate-100 ring-1 ring-slate-800 placeholder:text-slate-500 focus:outline-none focus:ring-cyan-500/60"
          />
          <button
            type="submit"
            disabled={disabled || !text.trim()}
            className="flex h-12 items-center gap-1.5 rounded-2xl bg-gradient-to-r from-[#00CAFF] to-[#6366F1] px-5 text-sm font-semibold text-white shadow-glow transition hover:brightness-110 disabled:opacity-40 disabled:shadow-none"
          >
            {working ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            Send
          </button>
        </form>
        {micError && <p className="mt-1.5 text-xs text-amber-300">{micError}</p>}
      </div>
    </section>
  );
}
