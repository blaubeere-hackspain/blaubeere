"use client";
import { useEffect, useState } from "react";
import { Activity, ArrowDownRight, ArrowRight, Building2, CalendarDays, ChevronDown, FlaskConical, Layers3, LoaderCircle, PanelLeft, PanelLeftClose, ShieldCheck, TrendingUp, TriangleAlert, Wallet, X } from "lucide-react";
import { ApiError, api } from "../lib/api";
import { date, money } from "../lib/format";
import type { Assessment, Company, CompanySummary, Comparison, Forecast, Identity, Plan } from "../lib/types";
import { MetricBreakdown, MetricWhy, type ExplainedMetric } from "./metric-breakdown";
import { CashChart } from "./cash-chart";
import { Planner } from "./planner";
import { Connections } from "./connections";
import { Sidebar } from "./sidebar";
import { Dialog } from "./dialog";
import { PaintedEmptyState } from "./painted-empty-state";
import garden from "../public/open-path-oil.png";

type Demo = { company: Company; forecasts: Record<number, Forecast>; comparison: Comparison };

export function Dashboard({ demo }: { demo?: Demo }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [companies, setCompanies] = useState<CompanySummary[] | null>(demo ? [demo.company] : null);
  const [selected, setSelected] = useState(demo?.company.id ?? "");
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [days, setDays] = useState(90);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(!demo);
  const [retry, setRetry] = useState(0);
  const [planning, setPlanning] = useState(false);
  const [explainedMetric, setExplainedMetric] = useState<ExplainedMetric>("funding");
  const [breakdownOpen, setBreakdownOpen] = useState(false);
  function explain(metric: ExplainedMetric) { setExplainedMetric(metric); setBreakdownOpen(true); }
  const [connections, setConnections] = useState(false);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [plan, setPlan] = useState<Plan | undefined>();
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileNavigation, setMobileNavigation] = useState(false);

  function handleError(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      window.location.replace(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    } else setError(error instanceof Error ? error.message : "Could not load the workspace. Please try again.");
  }
  useEffect(() => {
    if (demo) return;
    const controller = new AbortController(); setError("");
    Promise.all([api<Identity>("/me", { signal: controller.signal }), api<CompanySummary[]>("/companies", { signal: controller.signal })]).then(([user, list]) => {
      setIdentity(user); setCompanies(list);
      const requested = new URLSearchParams(window.location.search).get("company");
      setSelected(list.some(c => c.id === requested) ? requested! : list[0]?.id ?? "");
      if (!list.length) setLoading(false);
    }).catch(error => { if (!controller.signal.aborted) { handleError(error); setLoading(false); } });
    return () => controller.abort();
  }, [retry, demo]);
  useEffect(() => {
    if (demo || !selected) return;
    const controller = new AbortController(); setLoading(true); setError(""); setComparison(null); setPlan(undefined); setAssessment(current => current?.company.id === selected ? current : null);
    const url = new URL(window.location.href); url.searchParams.set("company", selected); window.history.replaceState(null, "", url);
    api<Assessment>(`/companies/${encodeURIComponent(selected)}/assessment?days=${days}`, { signal: controller.signal }).then(setAssessment).catch(error => { if (!controller.signal.aborted) handleError(error); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected, days, retry, demo]);
  async function logout() {
    if (demo) { window.location.assign("/login"); return; }
    setLogoutBusy(true);
    try { await api("/auth/logout", { method: "POST", body: "{}" }); window.location.assign("/login"); }
    catch (error) { handleError(error); setLogoutBusy(false); }
  }
  const company = demo?.company ?? (assessment?.company.id === selected ? assessment.company : undefined);
  const forecast = comparison?.baseline ?? demo?.forecasts[days] ?? assessment?.forecast;
  const format = (value: number, compact = false) => money(value, company?.currency ?? "EUR", compact);
  const sidebar = <Sidebar offline={Boolean(demo)} companies={companies} selected={selected} identity={identity} canPlan={Boolean(company) && !loading} logoutBusy={logoutBusy}
    onCompany={id => { setSelected(id); setMobileNavigation(false); }}
    onPlan={() => { setMobileNavigation(false); setPlanning(true); }}
    onConnections={() => { setMobileNavigation(false); setConnections(true); }}
    onLogout={logout} onNavigate={() => setMobileNavigation(false)}/>;
  return <div className="app-shell" data-sidebar-collapsed={sidebarCollapsed}><a href="#main" className="skip-link">Skip to cash outlook</a>
    <aside className="sidebar" id="workspace-sidebar" aria-label="Workspace navigation">{sidebar}</aside>
    <div className="app-content"><header className="topbar"><div className="topbar-context">
      <button className="icon-button desktop-sidebar-toggle" aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"} aria-expanded={!sidebarCollapsed} aria-controls="workspace-sidebar" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}><span className="icon-swap" aria-hidden><PanelLeft data-visible={sidebarCollapsed}/><PanelLeftClose data-visible={!sidebarCollapsed}/></span></button>
      <button className="icon-button mobile-sidebar-toggle" aria-label="Open navigation" aria-haspopup="dialog" onClick={() => setMobileNavigation(true)}><PanelLeft/></button>
      <span className="current-view"><Activity size={16} aria-hidden/>Cash outlook</span>
      {company && <a className="topbar-link" href="#evidence">Sources &amp; evidence</a>}
    </div><div className="topbar-actions"><span className="private-label"><ShieldCheck size={14} aria-hidden/>{demo ? "Sample workspace" : "Private workspace"}</span>{demo ? <a className="topbar-link" href="/login">Exit demo<ArrowRight size={14} aria-hidden/></a> : <span className="user-avatar" aria-label="Finance team">FT</span>}</div></header>
      <main id="main" className="dashboard-main" tabIndex={-1}>{demo && <p className="demo-notice"><FlaskConical size={16} aria-hidden/>You’re exploring sample data. Try the charts, sources and example plans.</p>}<div className="page-heading"><div><div className="eyebrow mb-2">A little foresight goes a long way</div><h1>Your cash, in perspective.</h1><p className="muted mt-2">See what’s ahead. Understand why. Choose your next move.</p></div><button className="button" onClick={() => setPlanning(true)} disabled={!company || loading}><TrendingUp size={17} aria-hidden/>Explore a plan<ArrowRight size={16} aria-hidden/></button></div>
        <div className="context-row"><div className="company-selector"><Building2 size={17} aria-hidden/><label className="sr-only" htmlFor="company">Company</label><select id="company" value={selected} onChange={e => setSelected(e.target.value)} disabled={!companies?.length}>{!companies?.length && <option value="">No company selected</option>}{companies?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div><span className="context-date"><CalendarDays size={15} aria-hidden/>{company ? `As of ${date(company.assessment_date, true)}` : loading ? "Loading assessment" : "No assessment available"}</span><span className="badge">{company?.currency ?? "EUR"}</span>{company?.data_mode === "demo" && <span className="badge accent"><FlaskConical size={13} aria-hidden/>Demo data</span>}<div className="horizon-select"><label htmlFor="horizon" className="small muted">Outlook</label><select id="horizon" value={comparison?.baseline.horizon_days ?? days} onChange={e => { setComparison(null); setPlan(undefined); setDays(Number(e.target.value)); }} disabled={!company || loading}>{[30,60,90,180].map(d => <option key={d} value={d}>{d} days</option>)}{forecast && ![30,60,90,180].includes(forecast.horizon_days) && <option value={forecast.horizon_days}>{forecast.horizon_days} days · goal</option>}</select></div></div>
        {error && <div className="error error-retry" role="alert"><span>{error}</span><button className="button secondary" onClick={() => setRetry(retry + 1)}>Try again</button></div>}
        <p className="refresh-status small" role="status">{loading && company && <><LoaderCircle className="spinner" size={14} aria-hidden/>Updating outlook… Showing the previous assessment.</>}{error && company && !loading && `Showing the last available ${forecast?.horizon_days}-day outlook.`}</p>
        {loading && !company && <div className="dashboard-skeleton" role="status"><span className="sr-only">Loading company cash outlook…</span><div className="skeleton-stats">{[1,2,3,4].map(n => <div key={n}/>)}</div><div className="skeleton-chart"/></div>}
        {!loading && companies?.length === 0 && <PaintedEmptyState image={garden} title="Your next chapter starts here."><p>Your workspace is ready. Ask your administrator to grant company access and load your first assessment.</p><button className="button secondary" onClick={() => setRetry(retry + 1)}>Refresh company access<ArrowRight size={16} aria-hidden/></button></PaintedEmptyState>}
        {company && forecast && <div aria-busy={loading}>
          <section className="stats-grid" aria-label="Cash outlook summary"><Stat why={<MetricWhy metric="cash" onClick={() => explain("cash")} disabled={loading}/>} label="Usable cash today" value={format(company.opening_cash_cents)} caption={`Observed at ${date(company.assessment_date)}`} icon={<Wallet size={17} aria-hidden/>}/><Stat why={<MetricWhy metric="funding" onClick={() => explain("funding")} disabled={loading}/>} label="Funding needed" value={format(forecast.funding_needed_cents)} caption={`Above a ${format(forecast.buffer_cents)} cash floor`} tone={forecast.funding_needed_cents > 0 ? "warn" : "good"} icon={<TriangleAlert size={17} aria-hidden/>}/><Stat why={<MetricWhy metric="shortfall" onClick={() => explain("shortfall")} disabled={loading}/>} label="First cash floor breach" value={forecast.first_shortfall ? date(forecast.first_shortfall.date) : "None forecast"} caption={forecast.first_shortfall ? `${format(forecast.first_shortfall.cash_cents)} projected cash` : `Within ${forecast.horizon_days} days`} tone={forecast.first_shortfall ? "bad" : "good"} icon={<CalendarDays size={17} aria-hidden/>}/><Stat why={<MetricWhy metric="minimum" onClick={() => explain("minimum")} disabled={loading}/>} label="Minimum projected cash" value={format(forecast.minimum.cash_cents)} caption={`On ${date(forecast.minimum.date)}`} tone={forecast.minimum.cash_cents < 0 ? "bad" : "neutral"} icon={<ArrowDownRight size={17} aria-hidden/>}/></section>
          <section className="card chart-card"><div className="card-heading"><div><h2>Cash over time</h2><p className="muted small mt-1">Cash history and your {forecast.horizon_days}-day baseline forecast</p></div><span className="badge">{company.history_mode === "reconstructed" ? "Reconstructed history" : "As known at the cutoff"}</span></div>
            {plan && <div className="plan-overlay-banner"><span><Layers3 size={15} aria-hidden/>Comparing: <strong>{plan.title}</strong></span><button className="icon-button" aria-label="Remove plan overlay" onClick={() => setPlan(undefined)}><X size={16}/></button></div>}
            <CashChart company={company} forecast={forecast} plan={plan}/><div className="checkpoints">{forecast.checkpoints.map(point => <div key={point.day}><span className="eyebrow">In {point.day} days</span><strong className={`num ${point.cash_cents < forecast.buffer_cents ? "text-bad" : ""}`}>{format(point.cash_cents)}</strong><span className="small muted">{date(point.date)}</span></div>)}</div>
          </section>
          <div className="insight-grid"><section className="card drivers-card"><div className="card-heading"><div><h2>What’s driving this?</h2><p className="small muted mt-1">Numerical changes, with the evidence behind them</p></div><span className="badge">{company.drivers.length} drivers</span></div><div className="driver-list">{company.drivers.map((driver,i) => <details className="driver" key={driver.label}><summary><span className={`driver-icon ${driver.points < 0 ? "down" : "up"}`}>{driver.points < 0 ? <ArrowDownRight size={18} aria-hidden/> : <TrendingUp size={18} aria-hidden/>}</span><span><strong>{driver.label}</strong><span>{driver.detail}</span></span><span className={`driver-points num ${driver.points < 0 ? "text-bad" : "text-good"}`}>{driver.points > 0 ? "+" : ""}{driver.points}<small>pts</small></span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="driver-source"><span className="eyebrow">Source evidence · driver {i+1}</span>{company.flows.filter(flow => driver.source_ids.includes(flow.id)).map(flow => <div key={flow.id}><strong>{flow.id} · {flow.label}</strong><span>{format(flow.amount_cents)} · {date(flow.date)} · {flow.timing}</span><span className="small muted">{flow.source}. Known on {date(flow.known_on)}.</span></div>)}</div></details>)}</div></section>
            <section className="card health-card"><div className="card-heading"><h2>Health, in context</h2><Activity size={18} className="muted" aria-hidden/></div><div className="health-number"><strong className="num">{company.health.score}</strong><span>/ 100</span><span className={`badge ${company.health.score < company.health.previous_score ? "warn" : company.health.score > company.health.previous_score ? "good" : ""}`}>{company.health.score < company.health.previous_score ? "Deteriorating" : company.health.score > company.health.previous_score ? "Improving" : "Stable"}</span></div><p className="muted"><strong className={company.health.score < company.health.previous_score ? "text-bad" : company.health.score > company.health.previous_score ? "text-good" : "muted"}>{company.health.score-company.health.previous_score} points</strong> over {company.health.period} · previously {company.health.previous_score}</p><div className="health-explain"><MetricWhy metric="health" onClick={() => explain("health")} disabled={loading}/></div><div className="health-scale" aria-hidden><div style={{ width: `${company.health.score}%` }}/></div><p className="small muted">{company.health.note}</p><div className="health-trend"><span className="eyebrow">Observed trajectory</span><div>{company.history.map(point => <span key={point.date}><strong className="num">{point.score}</strong><small>{date(point.date)}</small></span>)}</div></div></section></div>
          <section className="card evidence-card" id="evidence"><div className="card-heading"><div><h2>Know what’s behind the numbers</h2><p className="small muted mt-1">Missing evidence is shown as a gap, never automatically as poor health.</p></div><span className="badge warn">{company.coverage.filter(c => c.status !== "available").length} items to review</span></div><div className="coverage-grid">{company.coverage.map(source => <details key={source.label}><summary><span className={`coverage-dot ${source.status}`}/><span>{source.label}</span><span className={`badge ${source.status === "available" ? "good" : "warn"}`}>{source.status === "available" ? "Available" : source.status === "stale" ? "Stale" : "Missing"}</span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><p>{source.detail}</p>{source.as_of && <p className="small muted">As of {date(source.as_of, true)}</p>}</details>)}</div>
            <details className="source-records"><summary>Inspect dated cash flows and assumptions<span className="badge">{company.flows.length} source records</span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Dated cash flows, scroll to inspect all columns"><table><caption className="sr-only">Dated cash flows known at this assessment</caption><thead><tr><th>Source / flow</th><th>Date</th><th>Remaining amount</th><th>Timing</th><th>Known on</th></tr></thead><tbody>{company.flows.map(flow => <tr key={flow.id}><td><strong>{flow.label}</strong><span>{flow.id} · {flow.source}</span></td><td>{date(flow.date)}</td><td className="num">{format(Math.sign(flow.amount_cents) * (Math.abs(flow.amount_cents) - flow.settled_cents))}</td><td><span className={`badge ${flow.timing === "estimated" ? "warn" : ""}`}>{flow.timing}</span></td><td>{date(flow.known_on)}</td></tr>)}</tbody></table></div><p className="small muted p-4">Amounts are in {company.currency}. Partial settlements are deducted. Internal transfers are excluded. Overdue open items are projected for tomorrow; confirm their timing.</p></details>
          </section><footer className="dashboard-footer"><span><ShieldCheck size={14} aria-hidden/>{demo ? "Sample data · no account connected" : "Only your authorised company data"}</span><span className="mono">{company.model_version} · {company.history_mode}</span></footer>
        </div>}
      </main>
    </div>
    <Dialog open={mobileNavigation} onClose={() => setMobileNavigation(false)} className="navigation-dialog" titleId="navigation-title"><h2 id="navigation-title" className="sr-only">Workspace navigation</h2><button className="icon-button close-navigation" aria-label="Close navigation" onClick={() => setMobileNavigation(false)}><X/></button>{sidebar}</Dialog>
    {company && forecast && <MetricBreakdown key={`${company.id}:${explainedMetric}`} company={company} forecast={forecast} metric={explainedMetric} open={breakdownOpen} onClose={() => setBreakdownOpen(false)}/>}
    {company && <Planner key={company.id} company={company} example={demo?.comparison} open={planning} onClose={() => setPlanning(false)} onCompare={value => { setComparison(value); setPlan(undefined); }} onPreview={setPlan}/>}
    {(identity || demo) && <Connections demo={Boolean(demo)} endpoint={identity?.mcp_resource ?? ""} open={connections} onClose={() => setConnections(false)}/>}
  </div>;
}

function Stat({ label, value, caption, icon, why, tone = "neutral" }: { label: string; value: string; caption: string; icon: React.ReactNode; why: React.ReactNode; tone?: string }) {
  return <article className={`stat-card ${tone}`}><div><span>{label}</span>{icon}</div><strong className="stat-value num">{value}</strong><p>{caption}</p>{why}</article>;
}
