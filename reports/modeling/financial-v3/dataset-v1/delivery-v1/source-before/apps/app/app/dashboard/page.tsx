"use client";
import { useEffect, useState } from "react";
import Image from "next/image";
import openPath from "../../public/open-path-oil.png";
import { Activity, ArrowDownRight, ArrowRight, Building2, CalendarDays, ChevronDown, CircleHelp, FlaskConical, Layers3, LoaderCircle, LogOut, PanelLeft, Plug, ShieldCheck, TrendingUp, TriangleAlert, Wallet, X } from "lucide-react";
import { ApiError, api } from "../../lib/api";
import { date, money } from "../../lib/format";
import type { Assessment, CompanySummary, Comparison, Identity, Plan } from "../../lib/types";
import { CashChart } from "../../components/cash-chart";
import { Planner } from "../../components/planner";
import { Connections } from "../../components/connections";
import { ProxyAssessment } from "../../components/proxy-assessment";
import { TrajectoryAssessment } from "../../components/trajectory-assessment";

export default function Dashboard() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [companies, setCompanies] = useState<CompanySummary[] | null>(null);
  const [selected, setSelected] = useState("");
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [days, setDays] = useState(90);
  const [asOf, setAsOf] = useState<string | null>(null);
  function selectCompany(id: string) { setAssessment(null); setAsOf(null); setSelected(id); }
  function selectMonth(date: string) { if (date !== asOf) { setAssessment(null); setAsOf(date); } }
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [planning, setPlanning] = useState(false);
  const [connections, setConnections] = useState(false);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [plan, setPlan] = useState<Plan | undefined>();
  const [logoutBusy, setLogoutBusy] = useState(false);

  function handleError(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      window.location.replace(`/login?returnTo=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    } else setError(error instanceof Error ? error.message : "Could not load the workspace. Please try again.");
  }
  useEffect(() => {
    const controller = new AbortController(); setError("");
    Promise.all([api<Identity>("/me", { signal: controller.signal }), api<CompanySummary[]>("/companies", { signal: controller.signal })]).then(([user, list]) => {
      setIdentity(user); setCompanies(list);
      const requested = new URLSearchParams(window.location.search).get("company");
      setSelected(list.some(c => c.id === requested) ? requested! : list[0]?.id ?? "");
      if (!list.length) setLoading(false);
    }).catch(error => { if (!controller.signal.aborted) { handleError(error); setLoading(false); } });
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); setLoading(true); setError(""); setComparison(null); setPlan(undefined); setAssessment(current => current?.company.id === selected ? current : null);
    const url = new URL(window.location.href); url.searchParams.set("company", selected); window.history.replaceState(null, "", url);
    api<Assessment>(`/companies/${encodeURIComponent(selected)}/assessment?days=${days}${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`, { signal: controller.signal }).then(setAssessment).catch(error => { if (!controller.signal.aborted) handleError(error); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [selected, days, retry, asOf]);
  async function logout() {
    setLogoutBusy(true);
    try { await api("/auth/logout", { method: "POST", body: "{}" }); window.location.assign("/login"); }
    catch (error) { handleError(error); setLogoutBusy(false); }
  }
  const company = assessment?.company.id === selected ? assessment.company : undefined;
  const proxyOnly = company?.kind === "proxy_only";
  const trajectoryMode = company?.kind === "operating_trajectory";
  const nonCash = proxyOnly || trajectoryMode;
  const forecast = proxyOnly ? null : comparison?.baseline ?? assessment?.forecast;
  const format = (value: number, compact = false) => money(value, company?.currency ?? "EUR", compact);
  return <div className="app-shell"><a href="#main" className="skip-link">Skip to cash outlook</a>
    <aside className="sidebar"><a className="brand" href="/dashboard"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</a><div className="workspace-label"><span className="workspace-avatar"><Building2 size={18} aria-hidden/></span><div><strong>Finance workspace</strong><span>Private company access</span></div><ShieldCheck size={14} aria-hidden/></div>
      <span className="eyebrow nav-label">Workspace</span><nav aria-label="Workspace"><a className="nav-link active" href="/dashboard" aria-current="page"><Activity size={18} aria-hidden/>Cash outlook<span className="nav-indicator"/></a><button className="nav-link" onClick={() => setPlanning(true)} disabled={!company || company.kind !== "cash" || loading}><TrendingUp size={18} aria-hidden/>Explore a plan</button></nav>
      <div className="sidebar-bottom"><div className="assistant-callout"><span className="assistant-icon"><Plug size={18} aria-hidden/></span><strong>A second pair of eyes.</strong><p>Bring your cash outlook into your finance assistant.</p><button onClick={() => setConnections(true)} disabled={!identity}>Connect assistant<ArrowRight size={14} aria-hidden/></button></div><div className="sidebar-user"><span className="user-avatar">FT</span><div><strong>Finance team</strong><span title={identity?.email}>{identity?.email ?? "Signing in…"}</span></div><button className="icon-button" onClick={logout} disabled={logoutBusy || !identity} aria-label="Sign out"><LogOut size={17}/></button></div></div>
    </aside>
    <div className="app-content"><header className="topbar"><div className="topbar-context"><span className="desktop-label"><PanelLeft size={17} aria-hidden/></span><span className="muted">Workspace</span><span className="breadcrumb-slash">/</span><strong>Cash outlook</strong></div><div className="topbar-actions"><span className="private-label"><ShieldCheck size={14} aria-hidden/>Private workspace</span><button className="icon-button mobile-control" onClick={() => setConnections(true)} disabled={!identity} aria-label="Connect assistant"><Plug size={18}/></button><button className="icon-button mobile-control" onClick={logout} disabled={logoutBusy || !identity} aria-label="Sign out"><LogOut size={18}/></button><a href="#evidence" className="icon-button" aria-label="Understand sources and assumptions"><CircleHelp size={19}/></a><span className="user-avatar desktop-label">FT</span></div></header>
      <main id="main" className="dashboard-main"><div className="page-heading"><div><div className="eyebrow mb-2">A little foresight goes a long way</div><h1>{trajectoryMode ? "Your operating trajectory." : proxyOnly ? "Your operating deficit proxy." : "Your cash, in perspective."}</h1><p className="muted mt-2">{nonCash ? "Synthetic retrospective evidence, with explicit limitations." : "See what’s ahead. Understand why. Choose your next move."}</p></div><button className="button" onClick={() => setPlanning(true)} disabled={!company || company.kind !== "cash" || loading}><TrendingUp size={17} aria-hidden/>Explore a plan<ArrowRight size={16} aria-hidden/></button></div>
        <div className="context-row"><div className="company-selector"><Building2 size={17} aria-hidden/><label className="sr-only" htmlFor="company">Company</label><select id="company" value={selected} onChange={e => selectCompany(e.target.value)} disabled={!companies?.length}>{companies?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div><span className="context-date"><CalendarDays size={15} aria-hidden/>{company ? `As of ${date(company.assessment_date, true)}` : loading ? "Loading assessment" : "No assessment available"}</span><span className="badge">{proxyOnly ? "Analytical EUR" : company?.currency ?? "EUR"}</span>{company?.data_mode === "demo" && <span className="badge accent"><FlaskConical size={13} aria-hidden/>Demo data</span>}{company?.kind === "cash" && <div className="horizon-select"><label htmlFor="horizon" className="small muted">Outlook</label><select id="horizon" value={comparison?.baseline.horizon_days ?? days} onChange={e => { setComparison(null); setPlan(undefined); setDays(Number(e.target.value)); }} disabled={!company || loading}>{[30,60,90,180].map(d => <option key={d} value={d}>{d} days</option>)}{forecast && ![30,60,90,180].includes(forecast.horizon_days) && <option value={forecast.horizon_days}>{forecast.horizon_days} days · goal</option>}</select></div>}</div>
        {error && <div className="error error-retry" role="alert"><span>{error}</span><button className="button secondary" onClick={() => setRetry(retry + 1)}>Try again</button></div>}
        <p className="refresh-status small" role="status">{loading && company && <><LoaderCircle className="spinner" size={14} aria-hidden/>Updating outlook… Showing the previous assessment.</>}{error && company && !loading && (proxyOnly ? "Showing the last available proxy assessment." : `Showing the last available ${forecast?.horizon_days}-day outlook.`)}</p>
        {loading && !company && <div className="dashboard-skeleton" role="status"><span className="sr-only">Loading company cash outlook…</span><div className="skeleton-stats">{[1,2,3,4].map(n => <div key={n}/>)}</div><div className="skeleton-chart"/></div>}
        {!loading && companies?.length === 0 && <div className="empty-workspace"><div className="empty-painting"><Image src={openPath} alt="" fill sizes="(max-width: 700px) 90vw, 760px" placeholder="blur"/></div><div className="empty-copy"><span className="eyebrow">Your workspace is ready</span><h2>A clearer picture starts here.</h2><p className="muted">No company assessments yet. Ask your administrator to grant company access and load an assessment, then refresh to see your outlook.</p><button className="button secondary" onClick={() => setRetry(retry + 1)}>Refresh access</button></div></div>}
        {company?.kind === "proxy_only" && <div aria-busy={loading}><ProxyAssessment company={company} cashReason={assessment?.cash_planning_reason ?? "Cash planning unavailable: verified cash inputs are missing."}/></div>}
        {trajectoryMode && assessment && "trajectory_metadata" in assessment && <TrajectoryAssessment data={assessment} companies={companies ?? []} onCompany={selectCompany} onDate={selectMonth}/>}
        {company?.kind === "cash" && forecast && <div aria-busy={loading}>
          <section className="stats-grid" aria-label="Cash outlook summary"><Stat label="Usable cash today" value={format(company.opening_cash_cents)} caption={`Observed at ${date(company.assessment_date)}`} icon={<Wallet size={17} aria-hidden/>}/><Stat label="Funding needed" value={format(forecast.funding_needed_cents)} caption={`Above a ${format(forecast.buffer_cents)} cash floor`} tone={forecast.funding_needed_cents > 0 ? "warn" : "good"} icon={<TriangleAlert size={17} aria-hidden/>}/><Stat label="First cash floor breach" value={forecast.first_shortfall ? date(forecast.first_shortfall.date) : "None forecast"} caption={forecast.first_shortfall ? `${format(forecast.first_shortfall.cash_cents)} projected cash` : `Within ${forecast.horizon_days} days`} tone={forecast.first_shortfall ? "bad" : "good"} icon={<CalendarDays size={17} aria-hidden/>}/><Stat label="Minimum projected cash" value={format(forecast.minimum.cash_cents)} caption={`On ${date(forecast.minimum.date)}`} tone={forecast.minimum.cash_cents < 0 ? "bad" : "neutral"} icon={<ArrowDownRight size={17} aria-hidden/>}/></section>
          <section className="card chart-card"><div className="card-heading"><div><h2>Cash over time</h2><p className="muted small mt-1">Cash history and your {forecast.horizon_days}-day baseline forecast</p></div><span className="badge">{company.history_mode === "reconstructed" ? "Reconstructed history" : "As known at the cutoff"}</span></div>
            {plan && <div className="plan-overlay-banner"><span><Layers3 size={15} aria-hidden/>Comparing: <strong>{plan.title}</strong></span><button className="icon-button" aria-label="Remove plan overlay" onClick={() => setPlan(undefined)}><X size={16}/></button></div>}
            <CashChart company={company} forecast={forecast} plan={plan}/><div className="checkpoints">{forecast.checkpoints.map(point => <div key={point.day}><span className="eyebrow">In {point.day} days</span><strong className={`num ${point.cash_cents < forecast.buffer_cents ? "text-bad" : ""}`}>{format(point.cash_cents)}</strong><span className="small muted">{date(point.date)}</span></div>)}</div>
          </section>
          <div className="insight-grid"><section className="card drivers-card"><div className="card-heading"><div><h2>What’s driving this?</h2><p className="small muted mt-1">Numerical changes, with the evidence behind them</p></div><span className="badge">{company.drivers.length} drivers</span></div><div className="driver-list">{company.drivers.map((driver,i) => <details className="driver" key={driver.label}><summary><span className={`driver-icon ${driver.points < 0 ? "down" : "up"}`}>{driver.points < 0 ? <ArrowDownRight size={18} aria-hidden/> : <TrendingUp size={18} aria-hidden/>}</span><span><strong>{driver.label}</strong><span>{driver.detail}</span></span><span className={`driver-points num ${driver.points < 0 ? "text-bad" : "text-good"}`}>{driver.points > 0 ? "+" : ""}{driver.points}<small>pts</small></span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="driver-source"><span className="eyebrow">Source evidence · driver {i+1}</span>{company.flows.filter(flow => driver.source_ids.includes(flow.id)).map(flow => <div key={flow.id}><strong>{flow.id} · {flow.label}</strong><span>{format(flow.amount_cents)} · {date(flow.date)} · {flow.timing}</span><span className="small muted">{flow.source}. Known on {date(flow.known_on)}.</span></div>)}</div></details>)}</div></section>
            <section className="card health-card"><div className="card-heading"><h2>Health, in context</h2><Activity size={18} className="muted" aria-hidden/></div><div className="health-number"><strong className="num">{company.health.score}</strong><span>/ 100</span><span className={`badge ${company.health.score < company.health.previous_score ? "warn" : company.health.score > company.health.previous_score ? "good" : ""}`}>{company.health.score < company.health.previous_score ? "Deteriorating" : company.health.score > company.health.previous_score ? "Improving" : "Stable"}</span></div><p className="muted"><strong className={company.health.score < company.health.previous_score ? "text-bad" : company.health.score > company.health.previous_score ? "text-good" : "muted"}>{company.health.score-company.health.previous_score} points</strong> over {company.health.period} · previously {company.health.previous_score}</p><div className="health-scale" aria-hidden><div style={{ width: `${company.health.score}%` }}/></div><p className="small muted">{company.health.note}</p><div className="health-trend"><span className="eyebrow">Observed trajectory</span><div>{company.history.map(point => <span key={point.date}><strong className="num">{point.score}</strong><small>{date(point.date)}</small></span>)}</div></div></section></div>
          <section className="card evidence-card" id="evidence"><div className="card-heading"><div><h2>Know what’s behind the numbers</h2><p className="small muted mt-1">Missing evidence is shown as a gap, never automatically as poor health.</p></div><span className="badge warn">{company.coverage.filter(c => c.status !== "available").length} items to review</span></div><div className="coverage-grid">{company.coverage.map(source => <details key={source.label}><summary><span className={`coverage-dot ${source.status}`}/><span>{source.label}</span><span className={`badge ${source.status === "available" ? "good" : "warn"}`}>{source.status === "available" ? "Available" : source.status === "stale" ? "Stale" : "Missing"}</span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><p>{source.detail}</p>{source.as_of && <p className="small muted">As of {date(source.as_of, true)}</p>}</details>)}</div>
            <details className="source-records"><summary>Inspect dated cash flows and assumptions<span className="badge">{company.flows.length} source records</span><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="table-scroll" tabIndex={0} role="region" aria-label="Dated cash flows, scroll to inspect all columns"><table><caption className="sr-only">Dated cash flows known at this assessment</caption><thead><tr><th>Source / flow</th><th>Date</th><th>Remaining amount</th><th>Timing</th><th>Known on</th></tr></thead><tbody>{company.flows.map(flow => <tr key={flow.id}><td><strong>{flow.label}</strong><span>{flow.id} · {flow.source}</span></td><td>{date(flow.date)}</td><td className="num">{format(Math.sign(flow.amount_cents) * (Math.abs(flow.amount_cents) - flow.settled_cents))}</td><td><span className={`badge ${flow.timing === "estimated" ? "warn" : ""}`}>{flow.timing}</span></td><td>{date(flow.known_on)}</td></tr>)}</tbody></table></div><p className="small muted p-4">Amounts are in {company.currency}. Partial settlements are deducted. Internal transfers are excluded. Overdue open items are projected for tomorrow; confirm their timing.</p></details>
          </section><footer className="dashboard-footer"><span><ShieldCheck size={14} aria-hidden/>Only your authorised company data</span><span className="mono">{company.model_version} · {company.history_mode}</span></footer>
        </div>}
      </main>
    </div>
    {company?.kind === "cash" && <Planner key={company.id} company={company} open={planning} onClose={() => setPlanning(false)} onCompare={value => { setComparison(value); setPlan(undefined); }} onPreview={setPlan}/>}
    {identity && <Connections endpoint={identity.mcp_resource} open={connections} onClose={() => setConnections(false)}/>}
  </div>;
}

function Stat({ label, value, caption, icon, tone = "neutral" }: { label: string; value: string; caption: string; icon: React.ReactNode; tone?: string }) {
  return <article className={`stat-card ${tone}`}><div><span>{label}</span>{icon}</div><strong className="stat-value num">{value}</strong><p>{caption}</p></article>;
}
