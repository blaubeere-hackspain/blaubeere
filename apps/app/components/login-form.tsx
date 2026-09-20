"use client";
import { useState, type FormEvent } from "react";
import { Eye, EyeOff, ArrowRight, LockKeyhole, LoaderCircle } from "lucide-react";
import { api, returnPath } from "../lib/api";

export function LoginForm({ register = false, returnTo }: { register?: boolean; returnTo?: string }) {
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const destination = returnPath(returnTo ?? null);
  const errorId = register ? "register-error" : "login-error";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(""); setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await api(register ? "/auth/register" : "/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      window.location.assign(destination);
    } catch (error) { setError(error instanceof Error ? error.message : "Could not complete your request. Please retry."); setBusy(false); }
  }
  return <div className="auth-form">
    <div><span className="badge accent"><LockKeyhole size={14} aria-hidden/>Your finance workspace</span><h1 className="mt-4">{register ? "A clearer start." : "Welcome back."}</h1><p className="muted">{register ? "Create your account to explore the shared companies and their cash outlook." : "Sign in to see your company’s cash outlook."}</p></div>
    <form method="post" onSubmit={submit} aria-busy={busy}>
      <label className="field">Work email<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@company.com" spellCheck={false} autoCapitalize="none" aria-describedby={error ? errorId : undefined}/></label>
      <div className="field"><label htmlFor="password">Password</label><span className="password-input"><input id="password" name="password" type={show ? "text" : "password"} autoComplete={register ? "new-password" : "current-password"} required minLength={register ? 12 : undefined} maxLength={128} aria-describedby={[register && "password-hint", error && errorId].filter(Boolean).join(" ") || undefined}/><button type="button" className="icon-button" aria-label={show ? "Hide password" : "Show password"} aria-pressed={show} onClick={() => setShow(!show)}><span className="icon-swap" aria-hidden><Eye data-visible={!show}/><EyeOff data-visible={show}/></span></button></span>{register && <small id="password-hint">Use 12–128 characters. A longer passphrase works well.</small>}</div>
      {error && <p className="error" id={errorId} role="alert">{error}</p>}
      <button type="submit" className="button" disabled={busy}>{busy ? <><LoaderCircle className="spinner" aria-hidden/>{register ? "Creating account…" : "Signing in…"}</> : <>{register ? "Create account" : "Sign in"}<ArrowRight size={16} aria-hidden/></>}</button>
    </form>
    <p className="auth-switch">{register ? "Already have an account?" : "New to blau?"}<a className="auth-demo-link" href={`/${register ? "login" : "register"}?returnTo=${encodeURIComponent(destination)}`}>{register ? "Sign in" : "Create an account"}</a></p>
    <div className="auth-demo"><a href="/demo" className="auth-demo-link">Access demo<ArrowRight size={16} aria-hidden/></a><p className="small muted">Explore the imported companies and their health scores. No sign-in needed.</p></div>
  </div>;
}
