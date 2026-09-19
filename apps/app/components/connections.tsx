"use client";
import { useEffect, useState } from "react";
import { Check, Copy, LoaderCircle, Plug, X } from "lucide-react";
import { api } from "../lib/api";
import { Dialog } from "./dialog";

export function Connections({ endpoint, open, onClose }: { endpoint: string; open: boolean; onClose: () => void }) {
  const [clients, setClients] = useState<{ id: string; name: string }[] | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setCopied(false); setStatus("");
    if (!open) return;
    const controller = new AbortController(); setError(""); setClients(null);
    api<{ id: string; name: string }[]>("/connections", { signal: controller.signal }).then(setClients).catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [open, retry]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
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
    <div className="panel-body">
      <ol className="connection-steps"><li>Add a custom MCP server in your assistant’s settings.</li><li>Paste the endpoint below and connect.</li><li>Sign in to Blaubeere and review the requested access.</li></ol>
      <div className="field mt-6"><label htmlFor="mcp-endpoint">MCP endpoint</label><div className="copy-field"><input id="mcp-endpoint" value={endpoint} readOnly onFocus={e => e.target.select()} aria-describedby="endpoint-help"/><button className="icon-button" aria-label={copied ? "Endpoint copied" : "Copy MCP endpoint"} onClick={async () => { try { await navigator.clipboard.writeText(endpoint); setCopied(true); } catch { setError("Select and copy the endpoint from the field."); } }}><span className="icon-swap" aria-hidden><Copy data-visible={!copied}/><Check data-visible={copied}/></span></button></div></div>
      <p id="endpoint-help" className="small muted mt-3">Requires an assistant that supports remote MCP and OAuth. It can read outlooks and compare plans; it cannot edit source data or execute payments.</p>
      <p className="copy-status small" role="status">{copied ? "Endpoint copied to clipboard." : status}</p>
      <h3 className="mt-6 mb-4">Connected assistants</h3>
      {error && <div className="error mb-4" role="alert"><p>{error}</p><button className="button ghost" onClick={() => setRetry(retry + 1)}>Refresh connection list</button></div>}
      {!clients && !error && <p className="status-line" role="status"><LoaderCircle className="spinner" size={16} aria-hidden/>Loading connections…</p>}
      {clients?.length === 0 && <div className="empty-connections"><span className="connection-icon"><Plug size={22} aria-hidden/></span><strong>No assistants connected yet</strong><p className="small muted">Once you approve an assistant, it appears here. You can revoke its access at any time.</p></div>}
      {clients?.map(client => <div className="client-row" key={client.id} aria-busy={busy === client.id}><span><Plug size={18} aria-hidden/><strong>{client.name}</strong></span><button className="button secondary danger" disabled={Boolean(busy)} onClick={() => disconnect(client.id)}>{busy === client.id ? <><LoaderCircle className="spinner" size={16} aria-hidden/>Disconnecting…</> : "Disconnect"}</button></div>)}
    </div>
  </Dialog>;
}
