"use client";
import Image from "next/image";
import painting from "../../landing/public/cash-horizon-oil.png";
import { useEffect, useState } from "react";
import { Activity, ArrowRight, LoaderCircle, PanelLeft, PanelLeftClose, ShieldCheck, X } from "lucide-react";
import { ApiError, api } from "../lib/api";
import type { CompanySummary, Identity, ModelAssessment } from "../lib/types";
import { ModelDashboard } from "./model-dashboard";
import { Connections } from "./connections";
import { WelcomeOnboarding } from "./welcome-onboarding";
import { Sidebar } from "./sidebar";
import { Dialog } from "./dialog";
import { PaintedEmptyState } from "./painted-empty-state";
import garden from "../public/open-path-oil.png";

export function Dashboard({ demo = false, scoreDate }: { demo?: boolean; scoreDate?: string }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [companies, setCompanies] = useState<CompanySummary[] | null>(null);
  const [selected, setSelected] = useState("");
  const [recent, setRecent] = useState<string[]>([]);
  const [assessment, setAssessment] = useState<ModelAssessment | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [connections, setConnections] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(false);
  const prefix = demo ? "/demo" : "";

  function handleError(error: unknown) {
    if (!demo && error instanceof ApiError && error.status === 401) {
      window.location.replace(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    } else setError(error instanceof Error ? error.message : "Could not load the workspace. Please try again.");
  }
  useEffect(() => {
    const controller = new AbortController(); setError(""); setLoading(true);
    Promise.all([demo ? Promise.resolve(null) : api<Identity>("/me", { signal: controller.signal }), api<CompanySummary[]>(`${prefix}/companies`, { signal: controller.signal })]).then(([user, list]) => {
      const imported = list.filter(company => company.data_mode === "challenge");
      setIdentity(user); setCompanies(imported);
      const requested = new URLSearchParams(window.location.search).get("company");
      if (requested && requested !== "DEMO_001" && !imported.some(company => company.id === requested)) {
        setSelected(""); setError("This company is not available. Choose a company from the sidebar."); setLoading(false); return;
      }
      setSelected(requested && requested !== "DEMO_001" ? requested : imported[0]?.id ?? "");
      if (!imported.length) setLoading(false);
    }).catch(error => { if (!controller.signal.aborted) { handleError(error); setLoading(false); } });
    return () => controller.abort();
  }, [retry, demo, prefix]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); setLoading(true); setError(""); setAssessment(current => current?.company.id === selected ? current : null);
    const url = new URL(window.location.href); url.searchParams.set("company", selected); window.history.replaceState(null, "", url);
    api<ModelAssessment>(`${prefix}/companies/${encodeURIComponent(selected)}/assessment`, { signal: controller.signal }).then(value => {
      if (value.kind !== "model") throw new Error("No published model assessment is available for this company.");
      setAssessment(value);
    }).catch(error => { if (!controller.signal.aborted) handleError(error); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected, retry, prefix]);
  useEffect(() => { if (selected) setRecent(current => [selected, ...current.filter(id => id !== selected)].slice(0, 5)); }, [selected]);
  async function logout() {
    if (demo) { window.location.assign("/login"); return; }
    setLogoutBusy(true);
    try { await api("/auth/logout", { method: "POST", body: "{}" }); window.location.assign("/login"); }
    catch (error) { handleError(error); setLogoutBusy(false); }
  }
  const model = assessment?.company.id === selected ? assessment : null;
  const overviewHref = `${demo ? "/demo" : "/dashboard"}${selected ? `?company=${encodeURIComponent(selected)}` : ""}`;
  const sidebar = (mobile = false) => <Sidebar selectorId={mobile ? "mobile-company" : "sidebar-company"} canInspect={Boolean(model)} overviewHref={scoreDate ? overviewHref : undefined} demo={demo} companies={companies} recentCompanies={recent.flatMap(id => companies?.find(company => company.id === id) ?? [])} selected={selected} identity={identity} logoutBusy={logoutBusy}
    onCompany={id => { setSelected(id); setMobileNavigation(false); }} onConnections={() => { setMobileNavigation(false); setConnections(true); }} onLogout={logout} onNavigate={() => setMobileNavigation(false)}/>;
  return <div className="app-shell" data-sidebar-collapsed={sidebarCollapsed}><div className="dashboard-painting" aria-hidden="true"><Image src={painting} alt="" fill sizes="100vw" priority/></div><a href="#main" className="skip-link">Skip to financial health</a>
    <aside className="sidebar" id="workspace-sidebar" aria-label="Workspace navigation">{sidebar()}</aside>
    <div className="app-content"><header className="topbar"><div className="topbar-context">
      <button className="icon-button desktop-sidebar-toggle" aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"} aria-expanded={!sidebarCollapsed} aria-controls="workspace-sidebar" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}><span className="icon-swap" aria-hidden><PanelLeft data-visible={sidebarCollapsed}/><PanelLeftClose data-visible={!sidebarCollapsed}/></span></button>
      <button className="icon-button mobile-sidebar-toggle" aria-label="Open navigation" aria-haspopup="dialog" onClick={() => setMobileNavigation(true)}><PanelLeft/></button>
      <span className="current-view"><Activity size={16} aria-hidden/>{scoreDate ? "Health assessment" : "Financial health"}</span>
    </div><div className="topbar-actions">{companies !== null && (identity || demo) && <WelcomeOnboarding scope={demo ? "demo" : identity!.email} hasCompanies={companies.length > 0} demo={demo} autoOpen={!scoreDate}/>}{!demo && <span className="private-label"><ShieldCheck size={14} aria-hidden/>Private workspace</span>}{demo ? <a className="topbar-link" href="/login">Sign in<ArrowRight size={14} aria-hidden/></a> : <span className="user-avatar" aria-label="Finance team">FT</span>}</div></header>
      <main id="main" className="dashboard-main" tabIndex={-1}>
        {error && <div className="error error-retry" role="alert"><span>{error}</span><button className="button secondary" onClick={() => setRetry(retry + 1)}>Try again</button></div>}
        <p className="refresh-status small" role="status">{loading && model && <><LoaderCircle className="spinner" size={14} aria-hidden/>Updating… Showing the previous assessment.</>}</p>
        {loading && !model && <div className="dashboard-skeleton" role="status"><span className="sr-only">Loading company health and source records…</span><div className="skeleton-chart"/><div className="skeleton-stats">{[1,2,3,4].map(n => <div key={n}/>)}</div></div>}
        {!loading && companies?.length === 0 && <PaintedEmptyState image={garden} title="No imported companies available."><p>{demo ? "The dataset has no companies to display yet." : "Your account does not have access to any imported companies yet. You can explore the published dataset in the demo."}</p>{!demo && <a className="button secondary" href="/demo">Explore company data<ArrowRight size={16} aria-hidden/></a>}</PaintedEmptyState>}
        {model && <div aria-busy={loading}><ModelDashboard key={model.company.id} data={model} scoreDate={scoreDate} demo={demo}/></div>}
      </main>
    </div>
    <Dialog open={mobileNavigation} onClose={() => setMobileNavigation(false)} className="navigation-dialog" titleId="navigation-title"><h2 id="navigation-title" className="sr-only">Workspace navigation</h2><button className="icon-button close-navigation" aria-label="Close navigation" onClick={() => setMobileNavigation(false)}><X/></button>{mobileNavigation && sidebar(true)}</Dialog>
    {(identity || demo) && <Connections demo={demo} endpoint={identity?.mcp_resource ?? ""} open={connections} onClose={() => setConnections(false)}/>}
  </div>;
}
