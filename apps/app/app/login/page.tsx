"use client";
import { useState, type FormEvent } from "react";
import { Eye, EyeOff, ArrowRight, LockKeyhole, LoaderCircle } from "lucide-react";
import { AuthLayout } from "../../components/auth-layout";
import { api, returnPath } from "../../lib/api";

export default function Login() {
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      window.location.assign(returnPath(new URLSearchParams(window.location.search).get("returnTo")));
    } catch (error) { setError(error instanceof Error ? error.message : "Could not sign in. Please retry."); setBusy(false); }
  }
  return <AuthLayout><div className="auth-form">
    <div><span className="badge accent"><LockKeyhole size={14} aria-hidden/>Your finance workspace</span><h1 className="mt-6">Welcome back.</h1><p className="muted">Sign in to see your company’s cash outlook.</p></div>
    <form onSubmit={submit} aria-busy={busy}>
      <label className="field">Work email<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@company.com" spellCheck={false} aria-describedby={error ? "login-error" : undefined}/></label>
      <div className="field"><label htmlFor="password">Password</label><span className="password-input"><input id="password" name="password" type={show ? "text" : "password"} autoComplete="current-password" required maxLength={128} aria-describedby={error ? "login-error" : undefined}/><button type="button" className="icon-button" aria-label={show ? "Hide password" : "Show password"} aria-pressed={show} onClick={() => setShow(!show)}><span className="icon-swap" aria-hidden><Eye data-visible={!show}/><EyeOff data-visible={show}/></span></button></span></div>
      {error && <p className="error" id="login-error" role="alert">{error}</p>}
      <button className="button" disabled={busy}>{busy ? <><LoaderCircle className="spinner" aria-hidden/>Signing in…</> : <>Sign in<ArrowRight size={16} aria-hidden/></>}</button>
    </form>
    <p className="auth-footer">Access is provisioned by your administrator. Your account only shows companies you’re authorised to view.</p>
  </div></AuthLayout>;
}
