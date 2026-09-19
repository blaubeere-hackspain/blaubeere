"use client";
import { useState, type FormEvent } from "react";
import { Eye, EyeOff, ArrowRight, LockKeyhole, LoaderCircle } from "lucide-react";
import { api, returnPath } from "../lib/api";

export function LoginForm({ demo }: { demo: boolean }) {
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<"team" | "demo" | null>(null);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const demoEntry = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("name") === "demo";
    setError(""); setBusy(demoEntry ? "demo" : "team");
    const form = new FormData(event.currentTarget);
    try {
      await api(demoEntry ? "/auth/demo" : "/auth/login", { method: "POST", body: JSON.stringify(demoEntry ? {} : { email: form.get("email"), password: form.get("password") }) });
      window.location.assign(returnPath(new URLSearchParams(window.location.search).get("returnTo")));
    } catch (error) { setError(error instanceof Error ? error.message : "Could not sign in. Please retry."); setBusy(null); }
  }
  return <div className="auth-form">
    <div><span className="badge accent"><LockKeyhole size={14} aria-hidden/>Your finance workspace</span><h1 className="mt-6">Welcome back.</h1><p className="muted">Sign in to see your company’s cash outlook.</p></div>
    <form onSubmit={submit} aria-busy={Boolean(busy)}>
      <label className="field">Work email<input name="email" type="email" autoComplete="email" required maxLength={254} placeholder="you@company.com" spellCheck={false} aria-describedby={error ? "login-error" : undefined}/></label>
      <div className="field"><label htmlFor="password">Password</label><span className="password-input"><input id="password" name="password" type={show ? "text" : "password"} autoComplete="current-password" required maxLength={128} aria-describedby={error ? "login-error" : undefined}/><button type="button" className="icon-button" aria-label={show ? "Hide password" : "Show password"} aria-pressed={show} onClick={() => setShow(!show)}><span className="icon-swap" aria-hidden><Eye data-visible={!show}/><EyeOff data-visible={show}/></span></button></span></div>
      {error && <p className="error" id="login-error" role="alert">{error}</p>}
      <button type="submit" className="button" disabled={Boolean(busy)}>{busy === "team" ? <><LoaderCircle className="spinner" aria-hidden/>Signing in…</> : <>Sign in<ArrowRight size={16} aria-hidden/></>}</button>
      {demo && <button type="submit" name="demo" formNoValidate className="button secondary" disabled={Boolean(busy)}>{busy === "demo" ? <><LoaderCircle className="spinner" aria-hidden/>Opening demo…</> : <>Enter demo workspace<ArrowRight size={16} aria-hidden/></>}</button>}
    </form>
    <p className="auth-footer">{demo ? "Try the demo with sample data and your own session. No email or password needed." : "Access is provisioned by your administrator. Your account only shows companies you’re authorised to view."}</p>
  </div>;
}
