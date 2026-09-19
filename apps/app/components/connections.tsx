"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Copy, Plug, X } from "lucide-react";
import { api } from "../lib/api";

export function Connections({ endpoint, open, onClose }: { endpoint: string; open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [clients, setClients] = useState<{ id: string; name: string }[] | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!open) { dialog.current?.close(); return; }
    if (!dialog.current?.open) dialog.current?.showModal();
    const controller = new AbortController(); setError(""); setClients(null);
    api<{ id: string; name: string }[]>("/connections", { signal: controller.signal }).then(setClients).catch(error => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [open, retry]);
  async function disconnect(id: string) {
    setBusy(id); setError("");
    try { await api(`/connections/${encodeURIComponent(id)}/revoke`, { method: "POST", body: "{}" }); setClients(clients?.filter(client => client.id !== id) ?? []); }
    catch (error) { setError(error instanceof Error ? error.message : "Could not disconnect. Please retry."); }
    finally { setBusy(""); }
  }
  return <dialog ref={dialog} className="connection-dialog" aria-labelledby="connections-title" onClose={onClose} onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div className="panel-head"><div><span className="eyebrow">Your tools, connected</span><h2 id="connections-title">Bring your finance assistant</h2></div><button className="icon-button" aria-label="Close connections" onClick={onClose}><X size={20}/></button></div><div className="panel-body">
    <p className="muted">Add this MCP server to a compatible assistant, then sign in to approve access to your authorised companies.</p><label className="field mt-6">MCP endpoint<div className="copy-field"><input value={endpoint} readOnly onFocus={e => e.target.select()}/><button className="icon-button" aria-label="Copy MCP endpoint" onClick={async () => { try { await navigator.clipboard.writeText(endpoint); setCopied(true); } catch { setError("Select and copy the endpoint from the field."); } }}>{copied ? <Check size={18}/> : <Copy size={18}/>}</button></div></label>
    <p className="small muted mt-3">Read cash outlooks and compare conditional plans. No source edits or payment execution.</p><h3 className="mt-8 mb-4">Connected assistants</h3>
    {error && <div className="error mb-4" role="alert">{error}<button className="button ghost" onClick={() => setRetry(retry + 1)}>Retry</button></div>}
    {!clients && !error && <p role="status">Loading connections…</p>}
    {clients?.length === 0 && <div className="empty-connections"><Plug size={24} aria-hidden/><p>No assistants connected yet.</p><span className="small muted">Use the endpoint above to connect your first one.</span></div>}
    {clients?.map(client => <div className="client-row" key={client.id}><span>{client.name}</span><button className="button secondary" disabled={Boolean(busy)} onClick={() => disconnect(client.id)}>{busy === client.id ? "Disconnecting…" : "Disconnect"}</button></div>)}
  </div></dialog>;
}
