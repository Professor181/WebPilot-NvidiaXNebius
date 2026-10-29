"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getSpeechRecognition, stopSpeaking, type SpeechRecognitionLike } from "@/lib/voice";

/** One-shot speech capture (Web Speech API) that calls `onFinal` with the recognized utterance. */
export function useSpeechCapture(onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(true);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const callbackRef = useRef(onFinal);
  callbackRef.current = onFinal;

  useEffect(() => {
    setSupported(Boolean(getSpeechRecognition()));
    return () => recRef.current?.abort();
  }, []);

  const toggle = useCallback(() => {
    setError(null);
    if (listening) {
      recRef.current?.stop();
      return;
    }
    const Ctor = getSpeechRecognition();
    if (!Ctor) {
      setError("Voice capture needs Chrome or Edge — type instead.");
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
      let partial = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const r = event.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else partial += r[0].transcript;
      }
      setInterim(partial || finalText);
    };
    rec.onerror = (event) => setError(event.error === "not-allowed" ? "Microphone permission denied." : `Speech error: ${event.error}`);
    rec.onend = () => {
      setListening(false);
      setInterim("");
      const spoken = finalText.replace(/^alexa[,\s]+/i, "").trim();
      if (spoken) callbackRef.current(spoken);
    };
    recRef.current = rec;
    try {
      rec.start();
      setListening(true);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [listening]);

  return { listening, interim, error, supported, toggle };
}
