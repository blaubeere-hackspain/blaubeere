"use client";
import { useEffect, useState } from "react";
import { Check, Copy, LoaderCircle, Plug, X } from "lucide-react";
import { api } from "../lib/api";
import { Dialog } from "./dialog";
import { PaintedEmptyState } from "./painted-empty-state";
import garden from "../public/garden-chairs-oil.png";

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
  return <Dialog open={open} onClose={onClose} className="connection-dialog" titleId="connections-title">
    <div className="panel-head"><div><span className="eyebrow">Your tools, connected</span><h2 id="connections-title">Bring your finance assistant</h2></div><button className="icon-button" aria-label="Close connections" onClick={onClose}><X size={20}/></button></div>
    <div className="panel-body">{demo ? <PaintedEmptyState heading="h3" image={garden} title="Better with a second perspective."><p>Connect your assistant in a signed-in workspace. This demo uses sample data and has no account or assistants connected.</p><a className="button secondary" href="/login">Sign in to connect</a></PaintedEmptyState> : <>
      <ol className="connection-steps"><li>Add a custom MCP server in your assistant’s settings.</li><li>Paste the endpoint below and connect.</li><li>Sign in to blau and review the requested access.</li></ol>
      <div className="field mt-6"><label htmlFor="mcp-endpoint">MCP endpoint</label><div className="copy-field"><input id="mcp-endpoint" value={endpoint} readOnly onFocus={e => e.target.select()} aria-describedby="endpoint-help"/><button className="icon-button" aria-label={copied ? "Endpoint copied" : "Copy MCP endpoint"} onClick={copyEndpoint}><span className="icon-swap" aria-hidden><Copy data-visible={!copied}/><Check data-visible={copied}/></span></button></div></div>
      <p id="endpoint-help" className="small muted mt-3">Requires an assistant that supports remote MCP and OAuth. It can read outlooks and compare plans; it cannot edit source data or execute payments.</p>
      <p className="copy-status small" role="status">{copied ? "Endpoint copied to clipboard." : status}</p>
      <h3 className="mt-6 mb-4">Connected assistants</h3>
      {error && <div className="error mb-4" role="alert"><p>{error}</p><button className="button ghost" onClick={() => setRetry(retry + 1)}>Refresh connection list</button></div>}
      {!clients && !error && <p className="status-line" role="status"><LoaderCircle className="spinner" size={16} aria-hidden/>Loading connections…</p>}
      {clients?.length === 0 && <PaintedEmptyState heading="h3" image={garden} title="Better with a second perspective."><p>No assistants connected yet. Copy the endpoint above to get started. Once you approve an assistant, you can manage its access here.</p><button className="button secondary" onClick={copyEndpoint}><span className="icon-swap" aria-hidden><Copy data-visible={!copied}/><Check data-visible={copied}/></span>{copied ? "Endpoint copied" : "Copy endpoint"}</button></PaintedEmptyState>}
      {clients?.map(client => <div className="client-row" key={client.id} aria-busy={busy === client.id}><span><Plug size={18} aria-hidden/><strong>{client.name}</strong></span><button className="button secondary danger" disabled={Boolean(busy)} onClick={() => disconnect(client.id)}>{busy === client.id ? <><LoaderCircle className="spinner" size={16} aria-hidden/>Disconnecting…</> : "Disconnect"}</button></div>)}
    </>}</div>
  </Dialog>;
}
