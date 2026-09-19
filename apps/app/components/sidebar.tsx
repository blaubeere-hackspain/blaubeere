"use client";
import Image from "next/image";
import logo from "../../brand/blau.svg";
import { Activity, ArrowUpRight, Building2, FileText, LoaderCircle, LogOut, Plug, Plus, ShieldCheck } from "lucide-react";
import type { CompanySummary, Identity } from "../lib/types";

type Props = {
  companies: CompanySummary[] | null; selected: string; identity: Identity | null;
  canPlan: boolean; logoutBusy: boolean;
  onCompany: (id: string) => void; onPlan: () => void; onConnections: () => void;
  onLogout: () => void; onNavigate: () => void;
};

export function Sidebar({ companies, selected, identity, canPlan, logoutBusy, onCompany, onPlan, onConnections, onLogout, onNavigate }: Props) {
  const demo = identity?.email.endsWith("@demo.blaubeere.local");
  return <>
    <a className="brand sidebar-brand" href="/dashboard" aria-label="blau workspace"><Image className="brand-logo" src={logo} alt="blau"/></a>
    <div className="sidebar-scroll">
      <nav className="sidebar-section" aria-label="Workspace">
        <span className="nav-label">Workspace</span>
        <a className="nav-link active" href="#main" aria-current="page" onClick={onNavigate}><Activity aria-hidden/>Cash outlook</a>
        <button className="nav-link" onClick={onPlan} disabled={!canPlan}><Plus aria-hidden/>Explore a plan</button>
        <a className="nav-link" href="#evidence" onClick={onNavigate} aria-disabled={!canPlan} tabIndex={canPlan ? undefined : -1}><FileText aria-hidden/>Sources &amp; evidence</a>
        <button className="nav-link" onClick={onConnections} disabled={!identity}><Plug aria-hidden/>Connected assistants</button>
      </nav>
      <nav className="sidebar-section" aria-label="Companies">
        <span className="nav-label">Your companies<span>{companies?.length ?? "—"}</span></span>
        {companies?.map(company => <button className="nav-link company-link" key={company.id} onClick={() => onCompany(company.id)} aria-pressed={selected === company.id} title={company.name}><Building2 aria-hidden/><span>{company.name}</span>{selected === company.id && <span className="company-dot" aria-hidden/>}</button>)}
        {!companies && <p className="sidebar-note">Loading your companies…</p>}
        {companies?.length === 0 && <p className="sidebar-note">Companies appear here when your administrator grants access.</p>}
      </nav>
    </div>
    <div className="sidebar-bottom">
      <button className="assistant-shortcut" onClick={onConnections} disabled={!identity}><span className="assistant-icon"><Plug aria-hidden/></span><span><strong>A second pair of eyes</strong><span>Connect your assistant</span></span><ArrowUpRight aria-hidden/></button>
      <div className="sidebar-user"><span className="user-avatar" aria-hidden>{demo ? "DV" : "FT"}</span><div><strong>{demo ? "Demo workspace" : "Finance workspace"}</strong><span title={demo ? undefined : identity?.email}>{demo ? "Demo visitor" : identity?.email ?? "Signing in…"}</span></div><button className="icon-button" onClick={onLogout} disabled={logoutBusy || !identity} aria-label="Sign out">{logoutBusy ? <LoaderCircle className="spinner"/> : <LogOut/>}</button></div>
      <span className="sidebar-privacy"><ShieldCheck aria-hidden/>Private company access</span>
    </div>
  </>;
}
