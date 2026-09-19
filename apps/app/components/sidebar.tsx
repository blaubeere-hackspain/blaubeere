"use client";
import Image from "next/image";
import { useEffect, useState } from "react";
import logo from "../../brand/blau.svg";
import { Activity, ChartNoAxesCombined, FileText, LayoutGrid, LoaderCircle, LogOut, Plug, ReceiptText, ShieldCheck, Wallet } from "lucide-react";
import type { CompanySummary, Identity } from "../lib/types";

type Props = {
  companies: CompanySummary[] | null; recentCompanies: CompanySummary[]; selected: string; identity: Identity | null;
  canInspect: boolean; logoutBusy: boolean; demo?: boolean; overviewHref?: string; selectorId: string;
  onCompany: (id: string) => void; onConnections: () => void; onLogout: () => void; onNavigate: () => void;
};

export function Sidebar({ overviewHref, demo = false, selectorId, companies, recentCompanies, selected, identity, canInspect, logoutBusy, onCompany, onConnections, onLogout, onNavigate }: Props) {
  const [section, setSection] = useState("");
  useEffect(() => {
    const update = () => setSection(window.location.hash);
    update(); window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const current = overviewHref ? "#health" : section === "#main" ? "" : section;
  const navProps = (hash: string) => ({ className: `nav-link ${current === hash ? "active" : ""}`, "aria-current": current === hash ? "location" as const : undefined });
  return <>
    <a className="brand sidebar-brand" href={demo ? "/demo" : "/dashboard"} aria-label="blau workspace"><Image className="brand-logo" src={logo} alt="blau"/></a>
    <div className="sidebar-company-picker"><label htmlFor={selectorId}>Company<span>{companies?.length.toLocaleString("en-GB") ?? "—"}</span></label><select className="control" id={selectorId} value={selected} disabled={!companies?.length} onChange={event => onCompany(event.target.value)}><option value="" disabled>{companies === null ? "Loading companies…" : companies.length ? "Choose a company" : "No companies available"}</option>{companies?.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}</select></div>
    <div className="sidebar-scroll">
      <nav className="sidebar-section sidebar-primary" aria-label="Financial overview">
        <a {...navProps("")} href={overviewHref ?? "#main"} onClick={onNavigate}><LayoutGrid aria-hidden/>Overview</a>
        <a {...navProps("#health")} href={`${overviewHref ?? ""}#health`} onClick={onNavigate} aria-disabled={!canInspect} tabIndex={canInspect ? undefined : -1}><ChartNoAxesCombined aria-hidden/>Health history</a>
        <a {...navProps("#cash")} href={`${overviewHref ?? ""}#cash`} onClick={onNavigate} aria-disabled={!canInspect} tabIndex={canInspect ? undefined : -1}><Wallet aria-hidden/>Cash movements</a>
        <a {...navProps("#payments")} href={`${overviewHref ?? ""}#payments`} onClick={onNavigate} aria-disabled={!canInspect} tabIndex={canInspect ? undefined : -1}><ReceiptText aria-hidden/>Payments &amp; debt</a>
        <button className="nav-link" onClick={onConnections} disabled={!demo && !identity}><Plug aria-hidden/>Integrations</button>
      </nav>
      <nav className="sidebar-section" aria-label="Workspace">
        <span className="nav-label">Workspace</span>
        <a {...navProps("#history")} href={`${overviewHref ?? ""}#history`} onClick={onNavigate} aria-disabled={!canInspect} tabIndex={canInspect ? undefined : -1}><Activity aria-hidden/>Monthly records</a>
        <a {...navProps("#evidence")} href={`${overviewHref ?? ""}#evidence`} onClick={onNavigate} aria-disabled={!canInspect} tabIndex={canInspect ? undefined : -1}><FileText aria-hidden/>Source evidence</a>
      </nav>
      {recentCompanies.length > 0 && <nav className="sidebar-section sidebar-recents" aria-label="Recent companies"><span className="nav-label">Recents</span>{recentCompanies.map(company => <button key={company.id} className="nav-link" onClick={() => onCompany(company.id)} aria-pressed={company.id === selected}>{company.name}{company.id === selected && <span className="recent-current" aria-hidden/>}</button>)}</nav>}
    </div>
    <div className="sidebar-bottom">

      <div className="sidebar-user"><span className="user-avatar" aria-hidden>{demo ? "DV" : "FT"}</span><div><strong>{demo ? "Demo workspace" : "Finance workspace"}</strong><span title={demo ? undefined : identity?.email}>{demo ? "Published company data" : identity?.email ?? "Signing in…"}</span></div><button className="icon-button" onClick={onLogout} disabled={logoutBusy || (!demo && !identity)} aria-label={demo ? "Exit demo" : "Sign out"}>{logoutBusy ? <LoaderCircle className="spinner"/> : <LogOut/>}</button></div>
      <span className="sidebar-privacy"><ShieldCheck aria-hidden/>{demo ? "Read-only · no account connected" : "Private company access"}</span>
    </div>
  </>;
}
