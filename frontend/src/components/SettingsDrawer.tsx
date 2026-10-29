"use client";

import { useCallback, useEffect, useState } from "react";
import { X, Trash2, KeyRound, Mail, MonitorSmartphone, LoaderCircle, Plus, ShieldCheck } from "lucide-react";
import {
  deleteSavedLogin,
  fetchBrowserStatus,
  fetchSavedLogins,
  fetchSettings,
  launchWebPilotChrome,
  saveLogin,
  saveSettings,
} from "@/lib/mcpClient";
import type { BrowserStatus, SavedAccount } from "@/types/webpilot";

interface Props {
  open: boolean;
  onClose: () => void;
  notify: (tone: "success" | "error" | "info", title: string, body?: string) => void;
}

export default function SettingsDrawer({ open, onClose, notify }: Props) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [accounts, setAccounts] = useState<SavedAccount[]>([]);
  const [vaultOk, setVaultOk] = useState(true);
  const [browser, setBrowser] = useState<BrowserStatus | null>(null);
  const [site, setSite] = useState("");
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, l, b] = await Promise.all([fetchSettings(), fetchSavedLogins(), fetchBrowserStatus()]);
      setEmail(s.email ?? "");
      setFullName(s.full_name ?? "");
      setAccounts(l.accounts);
      setVaultOk(l.vault_available);
      setBrowser(b);
    } catch (err) {
      notify("error", "Couldn't load settings", (err as Error).message);
    }
  }, [notify]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  if (!open) return null;

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } catch (err) {
      notify("error", "Something went wrong", (err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <aside className="h-full w-full max-w-md overflow-y-auto bg-obsidian-950 p-5 ring-1 ring-slate-800" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">Settings &amp; logins</h2>
          <button onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-white">
            <X className="h-5 w-5" />
          </button>
        </div>

        <section className="mb-6 space-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-200">
            <MonitorSmartphone className="h-4 w-4 text-cyan-300" /> WebPilot Chrome
          </h3>
          <p className="text-xs text-slate-400">
            A real Chrome window with its own profile, started automatically when a task needs it. Sign in to a site there once and every later task is already logged in. Chrome won&apos;t let automation use your everyday profile; that&apos;s a Google security rule.
          </p>
          <div className="rounded-xl bg-slate-900/80 p-3 text-xs text-slate-300 ring-1 ring-slate-800">
            <p>
              Status: <b className={browser?.running ? "text-emerald-300" : "text-amber-300"}>{browser?.running ? "running" : "not running"}</b>
              {browser && !browser.chrome_path && <span className="text-rose-300"> · Chrome not found</span>}
            </p>
            {browser && <p className="mt-1 break-all text-slate-500">Profile: {browser.profile_dir}</p>}
          </div>
          <button
            className="chip-btn"
            disabled={busy === "launch"}
            onClick={() =>
              run("launch", async () => {
                const r = await launchWebPilotChrome();
                notify(r.running ? "success" : "error", r.running ? "WebPilot Chrome is open" : "Couldn't open Chrome", r.detail);
                await load();
              })
            }
          >
            {busy === "launch" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <MonitorSmartphone className="h-3.5 w-3.5" />} Open the WebPilot Chrome (to sign in to your sites)
          </button>
        </section>

        <section className="mb-6 space-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-200">
            <Mail className="h-4 w-4 text-cyan-300" /> Details for new accounts
          </h3>
          <p className="text-xs text-slate-400">I only use these when you tell me to create an account on a site I haven&apos;t seen before. I always ask first.</p>
          <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Your email" type="email" className="chat-input-sm w-full" />
          <input value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder="Your full name (optional)" className="chat-input-sm w-full" />
          <button
            className="chip-btn"
            disabled={busy === "settings"}
            onClick={() =>
              run("settings", async () => {
                await saveSettings({ email, full_name: fullName });
                notify("success", "Saved", "Used only for sign-ups you approve.");
              })
            }
          >
            Save
          </button>
        </section>

        <section className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-slate-200">
            <KeyRound className="h-4 w-4 text-cyan-300" /> Saved logins
          </h3>
          <p className="flex items-start gap-1.5 text-xs text-slate-400">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
            Passwords are kept in Windows Credential Manager, never in WebPilot&apos;s database or logs, and never shown to the AI model.
          </p>
          {!vaultOk && <p className="text-xs text-rose-300">No OS credential store is available on this machine. Saving logins is disabled.</p>}
          <ul className="space-y-1.5">
            {accounts.length === 0 && <li className="text-xs text-slate-500">None yet.</li>}
            {accounts.map((a) => (
              <li key={a.site} className="flex items-center gap-2 rounded-lg bg-slate-900/80 px-3 py-2 text-xs ring-1 ring-slate-800">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold text-slate-100">{a.site}</p>
                  <p className="truncate text-slate-400">
                    {a.username} · {a.source === "created" ? "created by WebPilot" : "added by you"}
                    {!a.verified && " · verification pending"}
                  </p>
                </div>
                <button
                  aria-label={`Delete login for ${a.site}`}
                  className="text-slate-500 hover:text-rose-300"
                  onClick={() =>
                    run(`del-${a.site}`, async () => {
                      await deleteSavedLogin(a.site);
                      await load();
                    })
                  }
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
          <form
            className="grid gap-2 rounded-xl bg-slate-900/50 p-3 ring-1 ring-slate-800"
            onSubmit={(e) => {
              e.preventDefault();
              run("add", async () => {
                await saveLogin(site, user, pass);
                setSite("");
                setUser("");
                setPass("");
                notify("success", "Login saved", "Stored in Windows Credential Manager.");
                await load();
              });
            }}
          >
            <input value={site} onChange={(e) => setSite(e.target.value)} placeholder="Website, e.g. amazon.in" className="chat-input-sm" />
            <input value={user} onChange={(e) => setUser(e.target.value)} placeholder="Email / username" autoComplete="off" className="chat-input-sm" />
            <input value={pass} onChange={(e) => setPass(e.target.value)} placeholder="Password" type="password" autoComplete="new-password" className="chat-input-sm" />
            <button type="submit" disabled={!vaultOk || !site || !user || !pass || busy === "add"} className="chip-btn justify-center">
              <Plus className="h-3.5 w-3.5" /> Add login
            </button>
          </form>
        </section>
      </aside>
    </div>
  );
}
