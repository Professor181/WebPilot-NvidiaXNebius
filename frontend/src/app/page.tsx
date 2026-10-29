"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CircleAlert, CircleCheck, ShieldX, X, LayoutDashboard, Bug } from "lucide-react";
import AlexaHeader, { type OrbState } from "@/components/AlexaHeader";
import ScenarioBar, { type ScenarioChip } from "@/components/ScenarioBar";
import ChatPanel, { type ChatImage, type ChatMessage, type LiveWebOptions, type SignInChoice } from "@/components/ChatPanel";
import SettingsDrawer from "@/components/SettingsDrawer";
import PlanDagStepper from "@/components/PlanDagStepper";
import MultimodalStage, { InspectResultCard } from "@/components/MultimodalStage";
import ApprovalGateCard from "@/components/ApprovalGateCard";
import EvidenceGallery from "@/components/EvidenceGallery";
import McpInspectorDrawer from "@/components/McpInspectorDrawer";
import InterruptionBar from "@/components/InterruptionBar";
import SecurityAttackSimulatorPanel from "@/components/SecurityAttackSimulatorPanel";
import {
  BackendError,
  McpTransportError,
  cancelWebTask,
  createWebTask,
  decideApproval,
  endConversation,
  fetchMcpLogs,
  fetchTask,
  fetchTasks,
  getExecutionEvidence,
  getTaskStatus,
  inspectWebsite,
  mcp,
  resetDemoState,
  sendSignInChoice,
  testForgedTokenAttack,
  testPriceDriftAttack,
  verifyTaskResult,
} from "@/lib/mcpClient";
import { speak, spokenSummary, stopSpeaking } from "@/lib/voice";
import { cx } from "@/lib/format";
import type {
  ClientFrame,
  EvidenceRecord,
  InspectResult,
  InterruptResponse,
  LiveAutopilotData,
  McpLogFrame,
  McpServerInfo,
  McpTool,
  SecurityProofEvent,
  Task,
  TaskSnapshot,
  ToolCallOutcome,
} from "@/types/webpilot";

interface Toast {
  id: number;
  tone: "success" | "error" | "info";
  title: string;
  body?: string;
}

type PendingAction = "approve" | "reject" | "forged" | "drift" | null;
type WorkspaceTab = "workspace" | "security";

const INTERRUPTIBLE_SCENARIOS = new Set(["laptop_shopping", "attendance_audit", "grant_form"]);
const SLOW_MOTION_PACE_MS = 1500;

const TTS_KEY = "webpilot.tts";

const YES_RE = /^(yes|yeah|yep|yup|ok|okay|sure|go ahead|do it|approve|confirm|proceed|please do|book it|buy it)\b/i;
const NO_RE = /^(no|nope|nah|cancel|stop|don'?t|reject|not now)\b/i;

function newConversationId(): string {
  try {
    return `conv_${crypto.randomUUID().replace(/-/g, "").slice(0, 20)}`;
  } catch {
    return `conv_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  }
}

/** Screenshots worth showing in the chat for a finished live task: each option card, else the last page. */
function chatImages(task: Task): ChatImage[] {
  const data = (task.extracted_data ?? null) as LiveAutopilotData | null;
  if (!data) return [];
  const cited = (data.answer?.citations ?? []).filter((c) => c.screenshot_url);
  if (cited.length) {
    return cited.slice(0, 6).map((c, i) => ({ url: c.screenshot_url as string, caption: `${i + 1}. ${c.title}${c.price ? ` — ${c.price}` : ""}` }));
  }
  const frames = (data.autopilot?.filmstrip ?? []).filter((f) => f.screenshot_url);
  const last = frames[frames.length - 1];
  return last ? [{ url: last.screenshot_url as string, caption: last.page_title || last.description }] : [];
}

function errorText(err: unknown): string {
  if (err instanceof McpTransportError || err instanceof BackendError) return err.message;
  return err instanceof Error ? err.message : String(err);
}

export default function Home() {
  // ------------------------------------------------------------------ MCP session
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [protocolVersion, setProtocolVersion] = useState<string | null>(null);
  const [serverInfo, setServerInfo] = useState<McpServerInfo | null>(null);
  const [tools, setTools] = useState<McpTool[]>([]);

  // -------------------------------------------------------------------- task state
  const [task, setTask] = useState<Task | null>(null);
  const [live, setLive] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceRecord[]>([]);
  const [inspectResult, setInspectResult] = useState<InspectResult | null>(null);
  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [proofEvents, setProofEvents] = useState<SecurityProofEvent[]>([]);
  const [activeChip, setActiveChip] = useState<string | null>(null);
  const [reverifying, setReverifying] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [tab, setTab] = useState<WorkspaceTab>("workspace");
  const [slowMotion, setSlowMotion] = useState(false);
  const [liveWeb, setLiveWeb] = useState<LiveWebOptions>({ universal: true, watch: true, persistent: true });
  const [conversationId, setConversationId] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const messageSeq = useRef(1);
  const watching = useRef<string | null>(null);
  const lastRun = useRef<{ goal: string; chipId: string | null; live: LiveWebOptions | null } | null>(null);

  // ------------------------------------------------------------- inspector + voice
  const [logs, setLogs] = useState<McpLogFrame[]>([]);
  const [clientFrames, setClientFrames] = useState<ClientFrame[]>([]);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const lastSpoken = useRef<string>("");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastSeq = useRef(1);

  const busy = busyLabel !== null || pendingAction !== null;

  useEffect(() => setConversationId(newConversationId()), []);

  const pushMessage = useCallback((m: Omit<ChatMessage, "id" | "at">) => {
    setMessages((list) => [...list, { ...m, id: `m${messageSeq.current++}`, at: new Date().toISOString() }]);
  }, []);

  const pushTaskReply = useCallback(
    (t: Task) => {
      const data = (t.extracted_data ?? null) as LiveAutopilotData | null;
      pushMessage({ role: "assistant", text: data?.chat_reply || t.summary || `Task ${t.status}`, taskId: t.task_id, status: t.status, images: chatImages(t) });
    },
    [pushMessage],
  );

  /** Keep `task` fresh while a long tool call runs (e.g. a sign-in prompt appears mid-checkout). */
  const watchTask = useCallback((taskId: string) => {
    watching.current = taskId;
    const loop = async () => {
      while (watching.current === taskId) {
        try {
          const detail = await fetchTask(taskId);
          if (watching.current === taskId) setTask(detail.task);
        } catch {
          /* ignore */
        }
        await new Promise((r) => setTimeout(r, 700));
      }
    };
    void loop();
    return () => {
      if (watching.current === taskId) watching.current = null;
    };
  }, []);

  const toast = useCallback((tone: Toast["tone"], title: string, body?: string) => {
    const id = toastSeq.current++;
    setToasts((t) => [...t.slice(-3), { id, tone, title, body }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6500);
  }, []);

  const syncSession = useCallback(() => {
    setSessionId(mcp.sessionId);
    setProtocolVersion(mcp.protocolVersion);
    setServerInfo(mcp.serverInfo);
    setTools(mcp.tools);
    setConnected(Boolean(mcp.sessionId));
  }, []);

  const refreshLogs = useCallback(async () => {
    try {
      const res = await fetchMcpLogs(60);
      setLogs(res.logs);
    } catch {
      /* backend offline; the header already shows it */
    }
  }, []);

  const connect = useCallback(async () => {
    setConnecting(true);
    try {
      await mcp.initialize();
      syncSession();
    } catch (err) {
      setConnected(false);
      toast("error", "MCP server unreachable", errorText(err));
    } finally {
      setConnecting(false);
      refreshLogs();
    }
  }, [syncSession, refreshLogs, toast]);

  useEffect(() => {
    const off = mcp.onFrame((f) => {
      setClientFrames((frames) => [...frames.slice(-59), f]);
      if (mcp.sessionId) setSessionId(mcp.sessionId);
    });
    connect();
    try {
      setTtsEnabled(localStorage.getItem(TTS_KEY) === "1");
    } catch {
      /* storage unavailable */
    }
    return off;
  }, [connect]);

  useEffect(() => {
    if (!inspectorOpen || !autoRefresh) return;
    const id = setInterval(refreshLogs, 2500);
    return () => clearInterval(id);
  }, [inspectorOpen, autoRefresh, refreshLogs]);

  // ---------------------------------------------------------------- voice readback
  useEffect(() => {
    if (!task || !ttsEnabled || live) return;
    const key = `${task.task_id}:${task.status}`;
    if (lastSpoken.current === key) return;
    const text = spokenSummary(task);
    if (!text) return;
    lastSpoken.current = key;
    setSpeaking(true);
    speak(text, { onEnd: () => setSpeaking(false) });
  }, [task, ttsEnabled, live]);

  const toggleTts = (enabled: boolean) => {
    setTtsEnabled(enabled);
    if (!enabled) {
      stopSpeaking();
      setSpeaking(false);
    }
    try {
      localStorage.setItem(TTS_KEY, enabled ? "1" : "0");
    } catch {
      /* ignore */
    }
  };

  // --------------------------------------------------------------------- helpers
  const loadEvidence = useCallback(async (taskId: string) => {
    try {
      const out = await getExecutionEvidence(taskId);
      if (out.data) setEvidence(out.data.evidence);
    } catch {
      /* non-fatal */
    }
  }, []);

  const applySnapshot = useCallback(
    (snapshot: TaskSnapshot | null) => {
      if (!snapshot) return;
      setTask(snapshot.task);
      loadEvidence(snapshot.task.task_id);
    },
    [loadEvidence],
  );

  const refreshTask = useCallback(
    async (taskId: string) => {
      const status = await getTaskStatus(taskId);
      if (status.data) applySnapshot(status.data);
    },
    [applySnapshot],
  );

  const reportToolError = useCallback(
    (outcome: ToolCallOutcome<unknown>) => {
      toast("error", outcome.error?.error_code ?? "Tool error", [outcome.error?.message, outcome.error?.hint].filter(Boolean).join(" — "));
    },
    [toast],
  );

  // --------------------------------------------------------------- run a scenario
  const runGoal = useCallback(
    async (goal: string, chipId: string | null = null, live: LiveWebOptions | null = null) => {
      if (busy) return;
      stopSpeaking();
      lastRun.current = { goal, chipId, live };
      setActiveChip(chipId);
      window.scrollTo({ top: 0, behavior: "smooth" });
      setBusyLabel(
        live?.universal
          ? "WebPilot is driving a live browser: sense → think → act → verify… (create_web_task · live_autopilot)"
          : "Alexa+ is planning and browsing… (create_web_task)",
      );
      setLive(true);
      setTask(null);
      setEvidence([]);
      setInspectResult(null);
      setProofEvents([]);

      // Watch the backend's task table so the DAG animates while the synchronous tool call runs.
      let knownTop: string | null = null;
      try {
        knownTop = (await fetchTasks(1)).tasks[0]?.task_id ?? null;
      } catch {
        knownTop = null;
      }
      let stopped = false;
      const poll = async () => {
        while (!stopped) {
          try {
            const newest = (await fetchTasks(1)).tasks[0];
            if (!stopped && newest && newest.task_id !== knownTop) setTask(newest);
          } catch {
            /* ignore */
          }
          await new Promise((r) => setTimeout(r, 450));
        }
      };
      void poll();

      try {
        const outcome = live?.universal
          ? await createWebTask(goal, {
              scenario: "live_autopilot",
              parameters: {
                live_autopilot: true,
                watch_live_browser: live.watch,
                use_persistent_profile: live.persistent,
                max_steps: 8,
                ...(conversationId ? { conversation_id: conversationId } : {}),
              },
            })
          : await createWebTask(goal, slowMotion ? { parameters: { pace_ms: SLOW_MOTION_PACE_MS } } : {});
        stopped = true;
        if (outcome.data?.task.interpreted_goal?.scenario === "synthesized_tool") {
          await mcp.refreshTools().catch(() => undefined);
        }
        syncSession();
        if (outcome.isError) {
          setTask(null);
          reportToolError(outcome);
          if (live?.universal) pushMessage({ role: "assistant", text: `Sorry — ${outcome.error?.message ?? "that failed"}`, status: "FAILED" });
        } else if (outcome.data) {
          applySnapshot(outcome.data);
          if (live?.universal) pushTaskReply(outcome.data.task);
          const s = outcome.data.task.status;
          toast(s === "COMPLETED" ? "success" : s === "AWAITING_APPROVAL" ? "info" : s === "BLOCKED" ? "error" : "info", `Task ${s}`, outcome.data.next_action ?? undefined);
        }
      } catch (err) {
        stopped = true;
        toast("error", "Request failed", errorText(err));
      } finally {
        stopped = true;
        setLive(false);
        setBusyLabel(null);
        refreshLogs();
      }
    },
    [busy, slowMotion, conversationId, applySnapshot, refreshLogs, reportToolError, syncSession, toast, pushMessage, pushTaskReply],
  );

  const onInterruptApplied = useCallback(
    (res: InterruptResponse) => {
      applySnapshot(res);
      setProofEvents([]);
      toast(res.mode === "APPLIED" ? "success" : "info", res.mode === "APPLIED" ? "Re-planned" : "Interruption " + res.mode, res.changes.join("; "));
      refreshLogs();
    },
    [applySnapshot, refreshLogs, toast],
  );

  const runInspect = useCallback(
    async (url: string) => {
      if (busy) return;
      setBusyLabel(`Inspecting ${url} through the content firewall… (inspect_website)`);
      try {
        const outcome = await inspectWebsite(url);
        if (outcome.isError) reportToolError(outcome);
        else if (outcome.data) {
          setInspectResult(outcome.data);
          toast(outcome.data.blocked ? "error" : "success", outcome.data.blocked ? "Barrier detected" : "Page distilled", outcome.headline);
        }
      } catch (err) {
        toast("error", "Inspection failed", errorText(err));
      } finally {
        setBusyLabel(null);
        refreshLogs();
      }
    },
    [busy, refreshLogs, reportToolError, toast],
  );

  // ------------------------------------------------------------------- approvals
  const approval = task?.pending_approval ?? null;

  const onApprove = async () => {
    if (!task || !approval) return;
    setPendingAction("approve");
    const unwatch = watchTask(task.task_id);
    try {
      const { decision, execution } = await decideApproval(task.task_id, approval.approval_id, "APPROVED");
      toast("info", "HMAC approval_token issued", `${decision.approval_token?.slice(0, 22)}… → execute_approved_step`);
      if (execution?.isError) {
        reportToolError(execution);
        await refreshTask(task.task_id);
      } else if (execution?.data) {
        applySnapshot(execution.data);
        toast("success", "Executed and verified", execution.data.confirmation_id ? `Confirmation ${execution.data.confirmation_id}` : undefined);
        if (task.interpreted_goal?.scenario === "live_autopilot") pushTaskReply(execution.data.task);
      }
    } catch (err) {
      toast("error", "Approval failed", errorText(err));
      await refreshTask(task.task_id).catch(() => undefined);
    } finally {
      unwatch();
      setPendingAction(null);
      refreshLogs();
    }
  };

  const onReject = async () => {
    if (!task || !approval) return;
    setPendingAction("reject");
    try {
      await decideApproval(task.task_id, approval.approval_id, "REJECTED");
      await refreshTask(task.task_id);
      toast("info", "Rejected", "The action will never be executed.");
      if (task.interpreted_goal?.scenario === "live_autopilot") pushMessage({ role: "assistant", text: "Okay — I won't do that. What next?" });
    } catch (err) {
      toast("error", "Reject failed", errorText(err));
    } finally {
      setPendingAction(null);
      refreshLogs();
    }
  };

  const pushProof = (e: Omit<SecurityProofEvent, "id" | "at">) =>
    setProofEvents((list) => [{ ...e, id: `${Date.now()}-${Math.random()}`, at: new Date().toISOString() }, ...list].slice(0, 4));

  const onForgedToken = async () => {
    if (!task || !approval) return;
    setPendingAction("forged");
    try {
      const outcome = await testForgedTokenAttack(task.task_id, approval.approval_id);
      pushProof({
        kind: "FORGED_TOKEN",
        blocked: outcome.isError,
        errorCode: outcome.error?.error_code ?? null,
        message: outcome.isError ? `${outcome.error?.message} — Backend Blocked Execution` : "Execution was NOT blocked",
        details: outcome.error?.details,
      });
      await refreshTask(task.task_id);
    } catch (err) {
      toast("error", "Forged-token test failed", errorText(err));
    } finally {
      setPendingAction(null);
      refreshLogs();
    }
  };

  const onPriceDrift = async () => {
    if (!task || !approval) return;
    setPendingAction("drift");
    try {
      const result = await testPriceDriftAttack(task.task_id, approval.approval_id);
      const exec = result.execution;
      pushProof({
        kind: "PRICE_DRIFT",
        blocked: Boolean(exec?.isError),
        errorCode: exec?.error?.error_code ?? null,
        message: exec?.isError
          ? `${exec.error?.message} Approval revoked; store price restored to ₹${result.restored?.current_price_inr?.toLocaleString("en-IN") ?? "74,990"}.`
          : "Execution was NOT blocked",
        details: exec?.error?.details,
      });
      await refreshTask(task.task_id);
    } catch (err) {
      toast("error", "Price-drift test failed", errorText(err));
    } finally {
      setPendingAction(null);
      refreshLogs();
    }
  };

  const onCancel = useCallback(async () => {
    if (!task) return;
    setBusyLabel("Cancelling task… (cancel_web_task)");
    try {
      const outcome = await cancelWebTask(task.task_id, "Cancelled from the Alexa+ screen");
      if (outcome.isError) reportToolError(outcome);
      else {
        applySnapshot(outcome.data);
        toast("info", "Task cancelled", `${outcome.data?.revoked_approvals ?? 0} approval(s) revoked`);
      }
    } catch (err) {
      toast("error", "Cancel failed", errorText(err));
    } finally {
      setBusyLabel(null);
      refreshLogs();
    }
  }, [task, applySnapshot, refreshLogs, reportToolError, toast]);

  const onReverify = async () => {
    if (!task) return;
    setReverifying(true);
    try {
      const outcome = await verifyTaskResult(task.task_id);
      if (outcome.isError) reportToolError(outcome);
      else {
        await refreshTask(task.task_id);
        toast("success", "Re-verified", outcome.data?.verification_report.verdict);
      }
    } catch (err) {
      toast("error", "Verification failed", errorText(err));
    } finally {
      setReverifying(false);
      refreshLogs();
    }
  };

  const onReset = async () => {
    setResetting(true);
    stopSpeaking();
    try {
      const res = await resetDemoState();
      syncSession();
      setTask(null);
      setEvidence([]);
      setInspectResult(null);
      setProofEvents([]);
      setActiveChip(null);
      setClientFrames([]);
      lastSpoken.current = "";
      toast("success", "Demo reset", `${res.deleted.tasks ?? 0} task(s) cleared · new MCP session ${mcp.sessionId?.slice(0, 8)}…`);
    } catch (err) {
      toast("error", "Reset failed", errorText(err));
    } finally {
      setResetting(false);
      refreshLogs();
    }
  };

  // ---------------------------------------------------------------------- chat
  const onSend = (text: string) => {
    pushMessage({ role: "user", text });
    const pending = task?.status === "AWAITING_APPROVAL" && task.pending_approval?.status === "PENDING";
    if (pending && YES_RE.test(text)) {
      void onApprove();
      return;
    }
    if (pending && NO_RE.test(text)) {
      void onReject();
      return;
    }
    void runGoal(text, null, liveWeb);
  };

  const onNewChat = async () => {
    stopSpeaking();
    const old = conversationId;
    setConversationId(newConversationId());
    setMessages([]);
    setTask(null);
    setEvidence([]);
    if (old) {
      try {
        const res = await endConversation(old);
        if (res.tasks.length) toast("info", "Conversation ended", `Closed its browser tab and deleted the screenshots of ${res.tasks.length} task(s).`);
      } catch {
        /* backend offline: its janitor cleans idle conversations anyway */
      }
    }
  };

  const onSignIn = async (choice: SignInChoice) => {
    if (!task) return;
    try {
      await sendSignInChoice(task.task_id, choice);
      const said =
        choice.choice === "self"
          ? "I'll sign in myself in the browser window."
          : choice.choice === "signup"
            ? "Create an account with my email."
            : choice.choice === "provide"
              ? `Sign in as ${choice.username}${choice.remember ? " (remember it)" : ""}.`
              : "Cancel.";
      pushMessage({ role: "user", text: said });
    } catch (err) {
      toast("error", "Sign-in choice failed", errorText(err));
    }
  };

  const onRerun = () => {
    if (lastRun.current) runGoal(lastRun.current.goal, lastRun.current.chipId, lastRun.current.live);
  };

  const orbState: OrbState = listening
    ? "listening"
    : busy
      ? "thinking"
      : speaking
        ? "speaking"
        : task?.status === "AWAITING_APPROVAL" || task?.status === "BLOCKED"
          ? "alert"
          : "idle";

  return (
    <div className="min-h-screen">
      <AlexaHeader
        orbState={orbState}
        sessionId={sessionId}
        protocolVersion={protocolVersion}
        serverName={serverInfo?.name ?? null}
        connected={connected}
        connecting={connecting}
        toolsCount={tools.length}
        frameCount={logs.length}
        inspectorOpen={inspectorOpen}
        onToggleInspector={() => {
          setInspectorOpen(!inspectorOpen);
          refreshLogs();
        }}
        onReset={onReset}
        onReconnect={connect}
        resetting={resetting}
      />

      <nav className={cx("mx-auto flex max-w-[1680px] gap-2 px-5 pt-4", inspectorOpen && "2xl:pr-[580px]")} aria-label="Workspace">
        {(
          [
            ["workspace", "Agent Workspace", LayoutDashboard],
            ["security", "Security Attack Lab", Bug],
          ] as const
        ).map(([id, label, Icon]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={cx(
              "flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold ring-1 transition",
              tab === id
                ? id === "security"
                  ? "bg-rose-500/15 text-rose-100 ring-rose-400/60"
                  : "bg-gradient-to-r from-[#00CAFF]/20 to-[#6366F1]/20 text-white ring-cyan-400/50"
                : "bg-slate-900/70 text-slate-400 ring-slate-800 hover:text-slate-200",
            )}
          >
            <Icon className="h-4 w-4" /> {label}
            {id === "security" && <span className="rounded bg-rose-500/20 px-1.5 text-[10px] text-rose-200">LIVE</span>}
          </button>
        ))}
      </nav>

      {tab === "security" ? (
        <main className={cx("mx-auto max-w-[1680px] px-5 py-5", inspectorOpen && "2xl:pr-[580px]")}>
          <SecurityAttackSimulatorPanel
            connected={connected}
            onTaskUpdate={(t) => {
              setTask(t);
              loadEvidence(t.task_id);
              refreshLogs();
            }}
          />
        </main>
      ) : (
      <main className={cx("mx-auto grid max-w-[1680px] gap-5 px-5 py-5 transition-[padding] xl:grid-cols-[minmax(500px,640px)_1fr]", inspectorOpen && "2xl:pr-[580px]")}>
        <div className="space-y-5">
          <ChatPanel
            messages={messages}
            liveTask={task && task.interpreted_goal?.scenario === "live_autopilot" ? task : null}
            working={live || pendingAction === "approve"}
            workingLabel={busyLabel}
            disabled={busy || !connected}
            liveWeb={liveWeb}
            onLiveWebChange={setLiveWeb}
            ttsEnabled={ttsEnabled}
            onToggleTts={toggleTts}
            onSend={onSend}
            onNewChat={onNewChat}
            onOpenSettings={() => setSettingsOpen(true)}
            onListeningChange={setListening}
            onSignIn={onSignIn}
            onApprove={onApprove}
            onReject={onReject}
            approvalBusy={pendingAction !== null}
          />
          <ScenarioBar
            disabled={busy || !connected}
            activeId={activeChip}
            slowMotion={slowMotion}
            onToggleSlowMotion={() => setSlowMotion((v) => !v)}
            onRun={(chip: ScenarioChip) => runGoal(chip.goal, chip.id)}
            onInspect={runInspect}
          />
          <PlanDagStepper task={task} live={live} />
        </div>

        <div className="min-w-0 space-y-5">
          {task &&
            INTERRUPTIBLE_SCENARIOS.has(task.interpreted_goal?.scenario) &&
            (task.status === "EXECUTING" || task.status === "PLANNED" || task.status === "AWAITING_APPROVAL") && (
              <InterruptionBar key={task.task_id} task={task} live={live || task.status !== "AWAITING_APPROVAL"} onApplied={onInterruptApplied} />
            )}
          {task && approval && (
            <ApprovalGateCard
              task={task}
              approval={approval}
              pendingAction={pendingAction}
              proofEvents={proofEvents}
              onApprove={onApprove}
              onReject={onReject}
              onForgedToken={onForgedToken}
              onPriceDrift={onPriceDrift}
              onRerun={onRerun}
            />
          )}
          {inspectResult && <InspectResultCard result={inspectResult} onClose={() => setInspectResult(null)} />}
          <MultimodalStage task={task} live={live} busy={busy} onCancel={onCancel} />
          {task && !live && (
            <EvidenceGallery evidence={evidence} report={task.verification_report} onReverify={onReverify} reverifying={reverifying} />
          )}
        </div>
      </main>
      )}

      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} notify={toast} />

      <McpInspectorDrawer
        open={inspectorOpen}
        onClose={() => setInspectorOpen(false)}
        logs={logs}
        clientFrames={clientFrames}
        tools={tools}
        sessionId={sessionId}
        protocolVersion={protocolVersion}
        serverInfo={serverInfo}
        autoRefresh={autoRefresh}
        onToggleAutoRefresh={() => setAutoRefresh(!autoRefresh)}
        onRefresh={refreshLogs}
      />

      <div className="pointer-events-none fixed bottom-4 left-4 z-50 flex w-[min(420px,calc(100vw-2rem))] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              "pointer-events-auto flex items-start gap-2 rounded-xl px-3 py-2.5 text-sm shadow-2xl ring-1 backdrop-blur-xl animate-fade-up",
              t.tone === "success" && "bg-emerald-950/90 text-emerald-100 ring-emerald-500/50",
              t.tone === "error" && "bg-rose-950/90 text-rose-100 ring-rose-500/50",
              t.tone === "info" && "bg-slate-900/95 text-slate-100 ring-cyan-500/40",
            )}
          >
            {t.tone === "success" ? <CircleCheck className="mt-0.5 h-4 w-4 shrink-0" /> : t.tone === "error" ? <ShieldX className="mt-0.5 h-4 w-4 shrink-0" /> : <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />}
            <div className="min-w-0 flex-1">
              <p className="font-semibold">{t.title}</p>
              {t.body && <p className="mt-0.5 break-words text-xs opacity-80">{t.body}</p>}
            </div>
            <button onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))} className="opacity-60 hover:opacity-100" aria-label="Dismiss">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
