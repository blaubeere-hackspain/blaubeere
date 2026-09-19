"use client";
import { useEffect, useState } from "react";
import { Check, ShieldCheck } from "lucide-react";
import { AuthLayout } from "../../components/auth-layout";
import { ApiError, api } from "../../lib/api";

type Consent = { client_name: string; redirect_uri: string; company_ids: string[]; email: string };
export default function Connect() {
  const [consent, setConsent] = useState<Consent | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    api<Consent>(`/oauth/request${window.location.search}`, { signal: controller.signal }).then(setConsent).catch((error) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) window.location.replace(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
      else setError(error.message ?? "Could not load this connection request.");
    });
    return () => controller.abort();
  }, [retry]);
  async function decide(approve: boolean) {
    setBusy(true); setError("");
    try {
      const request = Object.fromEntries(new URLSearchParams(window.location.search));
      const result = await api<{ redirect: string }>("/oauth/consent", { method: "POST", body: JSON.stringify({ request, approve }) });
      window.location.assign(result.redirect);
    } catch (error) { setError(error instanceof Error ? error.message : "Please retry."); setBusy(false); }
  }
  return <AuthLayout><div className="auth-form">
    <span className="badge accent"><ShieldCheck size={16} aria-hidden/>Connect a finance assistant</span>
    <div><h1>{consent ? `Connect ${consent.client_name}?` : "Review connection"}</h1><p className="muted">You control access to your financial data.</p></div>
    {error && <div className="error" role="alert">{error}<button className="button ghost" onClick={() => setRetry(retry + 1)}>Retry</button></div>}
    {!consent && !error && <p role="status">Loading the connection request…</p>}
    {consent && <><p className="small muted break-word">Signed in as {consent.email}. Return address: <strong>{new URL(consent.redirect_uri).host}</strong>.</p>
      <ul className="consent-list"><li><Check size={18} aria-hidden/><span>Read cash forecasts, source evidence and health context for your {consent.company_ids.length} authorised {consent.company_ids.length === 1 ? "company" : "companies"}.</span></li><li><Check size={18} aria-hidden/><span>Calculate hypothetical plans using explicit assumptions.</span></li></ul>
      <p className="muted small">This connection cannot change source records or move money. You can revoke access in the app at any time. Client names are supplied by the client; only approve clients you recognise.</p>
      <div className="consent-actions"><button className="button secondary" disabled={busy} onClick={() => decide(false)}>Decline</button><button className="button" disabled={busy} onClick={() => decide(true)}>{busy ? "Continuing…" : "Allow access"}</button></div>
    </>}
  </div></AuthLayout>;
}
