/** Web Speech API helpers: SpeechSynthesis readback and SpeechRecognition capture. */
import type { AttendanceData, Task } from "@/types/webpilot";

// Minimal typings for the (prefixed) Web Speech recognition API, which lib.dom does not ship.
export interface SpeechRecognitionAlternativeLike {
  transcript: string;
  confidence: number;
}
export interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}
export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: { readonly length: number; [index: number]: SpeechRecognitionResultLike };
}
export interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

export function getSpeechRecognition(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function ttsSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** Make backend summaries pleasant to hear (rupee symbol, arrows, Indian grouping). */
export function normalizeForSpeech(text: string): string {
  return text
    .replace(/₹\s?([\d,]+)/g, (_, n: string) => `${n.replace(/,/g, "")} rupees`)
    .replace(/→/g, " to ")
    .replace(/—/g, ", ")
    .replace(/\s+/g, " ")
    .trim();
}

/** A concise natural readback for the states Alexa+ should announce. */
export function spokenSummary(task: Task): string | null {
  if (!["AWAITING_APPROVAL", "COMPLETED", "BLOCKED", "AWAITING_USER_INPUT", "FAILED"].includes(task.status)) return null;
  const scenario = task.interpreted_goal?.scenario;
  if (scenario === "attendance_audit" && task.status === "COMPLETED" && task.extracted_data) {
    const data = task.extracted_data as unknown as AttendanceData;
    const below = data.below_threshold ?? [];
    if (below.length === 0) return `I checked your college portal. All subjects meet the ${data.threshold_pct} percent requirement.`;
    const words = ["Zero", "One", "Two", "Three", "Four", "Five", "Six"];
    const count = words[below.length] ?? String(below.length);
    const parts = below.map((s) => `${s.subject} at ${s.percentage} percent, needing ${s.classes_needed} more classes`);
    return `I checked your college portal. ${count} subject${below.length > 1 ? "s are" : " is"} below ${data.threshold_pct} percent: ${parts.join(", and ")}.`;
  }
  return task.summary ? normalizeForSpeech(task.summary) : null;
}

export function speak(text: string, opts: { rate?: number; onEnd?: () => void } = {}): void {
  if (!ttsSupported() || !text) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(normalizeForSpeech(text));
  utterance.rate = opts.rate ?? 1.03;
  utterance.pitch = 1;
  const voices = synth.getVoices();
  const preferred =
    voices.find((v) => /en-(US|IN)/i.test(v.lang) && /female|aria|jenny|samantha|zira|neural/i.test(v.name)) ??
    voices.find((v) => /^en/i.test(v.lang));
  if (preferred) utterance.voice = preferred;
  if (opts.onEnd) utterance.onend = opts.onEnd;
  synth.speak(utterance);
}

export function stopSpeaking(): void {
  if (ttsSupported()) window.speechSynthesis.cancel();
}
