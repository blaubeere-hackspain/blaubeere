"use client";
import Image from "next/image";
import { useEffect, useState } from "react";
import { Check, Copy, LoaderCircle, Plug, X } from "lucide-react";
import { api } from "../lib/api";
import { Dialog } from "./dialog";
import garden from "../public/garden-chairs-oil.png";
import styles from "./welcome-onboarding.module.css";

export function Connections({ endpoint, demo = false, open, onClose }: { endpoint: string; demo?: boolean; open: boolean; onClose: () => void }) {
  const [clients, setClients] = useState<{ id: string; name: string }[] | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setCopied(false); setStatus("");
    if (!open || demo) return;
    const controller = new AbortController(); setError(""); setClients(null);
    api<{ id: string; name: string }[]>("/connections", { signal: controller.signal }).then(setClients).catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [open, retry, demo]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  async function copyEndpoint() {
    setError("");
    try { await navigator.clipboard.writeText(endpoint); setCopied(true); }
    catch { setError("Select and copy the endpoint from the field."); }
  }
  async function disconnect(id: string) {
    setBusy(id); setError(""); setStatus("");
    try {
      await api(`/connections/${encodeURIComponent(id)}/revoke`, { method: "POST", body: "{}" });
      setClients(current => current?.filter(client => client.id !== id) ?? null);
      setStatus("Assistant disconnected. Its access has been revoked.");
    } catch (error) { setError(error instanceof Error ? error.message : "Could not disconnect. Please try the Disconnect button again."); }
    finally { setBusy(""); }
  }
  return <Dialog open={open} onClose={onClose} className={`${styles.dialog} connection-dialog`} titleId="connections-title">
    <div className={styles.painting}>
      <Image src={garden} alt="" fill sizes="(max-width: 540px) 100vw, 512px"/>
      <div className={styles.brand} aria-label="blau"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span></div>
      <button className={`icon-button ${styles.close}`} aria-label="Close connections" onClick={onClose}><X size={18} aria-hidden/></button>
    </div>
    <div className={styles.body}>
      <header className={styles.heading}><h2 id="connections-title">Bring your finance assistant</h2><p>Your tools, connected</p></header>
      {demo ? <><div className={styles.details}><h3>Better with a second perspective.</h3><p>Connect your assistant in a signed-in workspace. This demo uses sample data and has no account or assistants connected.</p></div><footer className={styles.footer}><a className={`button ${styles.continue}`} href="/login">Sign in to connect</a></footer></> : <>
      <div className={styles.details}>
      <ol className={`${styles.steps} connection-steps`}>{["Add a custom MCP server in your assistant’s settings.", "Paste the endpoint below and connect.", "Sign in to blau and review the requested access."].map((step, index) => <li key={step}><span>{index + 1}</span><div>{step}</div></li>)}</ol>
      <div className="field mt-6"><label htmlFor="mcp-endpoint">MCP endpoint</label><div className="copy-field"><input id="mcp-endpoint" value={endpoint} readOnly onFocus={e => e.target.select()} aria-describedby="endpoint-help"/><button className="icon-button" aria-label={copied ? "Endpoint copied" : "Copy MCP endpoint"} onClick={copyEndpoint}><span className="icon-swap" aria-hidden><Copy data-visible={!copied}/><Check data-visible={copied}/></span></button></div></div>
      <p id="endpoint-help" className="small muted mt-3">Requires an assistant that supports remote MCP and OAuth. It can read outlooks and compare plans; it cannot edit source data or execute payments.</p>
      </div>
      <p className="copy-status small" role="status">{copied ? "Endpoint copied to clipboard." : status}</p>
      <section className={styles.details} aria-labelledby="connected-assistants-title">
      <h3 id="connected-assistants-title">Connected assistants</h3>
      {error && <div className="error mb-4" role="alert"><p>{error}</p><button className="button ghost" onClick={() => setRetry(retry + 1)}>Refresh connection list</button></div>}
      {!clients && !error && <p className="status-line" role="status"><LoaderCircle className="spinner" size={16} aria-hidden/>Loading connections…</p>}
      {clients?.length === 0 && <p>No assistants connected yet. Copy the endpoint above to get started. Once you approve an assistant, you can manage its access here.</p>}
      {clients?.map(client => <div className="client-row" key={client.id} aria-busy={busy === client.id}><span><Plug size={18} aria-hidden/><strong>{client.name}</strong></span><button className="button secondary danger" disabled={Boolean(busy)} onClick={() => disconnect(client.id)}>{busy === client.id ? <><LoaderCircle className="spinner" size={16} aria-hidden/>Disconnecting…</> : "Disconnect"}</button></div>)}
      </section>
    </>}</div>
  </Dialog>;
}
