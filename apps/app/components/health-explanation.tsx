"use client";
import Link from "next/link";
import { ArrowDownLeft, ArrowLeft, ArrowRight, ArrowUpRight, ChevronDown, FileText } from "lucide-react";
import { date, money } from "../lib/format";
import { healthHref, healthTimeline } from "../lib/health";
import type { Company } from "../lib/types";

export function HealthExplanation({ company, scoreDate, demo = false }: { company: Company; scoreDate: string; demo?: boolean }) {
  const timeline = healthTimeline(company);
  const index = timeline.findIndex(point => point.date === scoreDate);
  const point = timeline[index];
  const assessment = point?.assessment;
  const previous = timeline[index - 1];
  const next = timeline[index + 1];
  const back = `${demo ? "/demo" : "/dashboard"}?company=${encodeURIComponent(company.id)}#health`;
  const href = (day: string) => healthHref(company.id, day, demo);
  const driverTotal = assessment?.drivers.reduce((sum, driver) => sum + (driver.points ?? 0), 0) ?? 0;
  const delta = assessment && assessment.health.previous_score !== null ? Math.round((assessment.health.score - assessment.health.previous_score) * 100) / 100 : null;
  const unattributed = delta === null ? null : Math.round((delta - driverTotal) * 100) / 100;
  const signed = (value: number) => `${value > 0 ? "+" : ""}${value}`;
  return <div className="health-page">
    <nav className="health-page-nav" aria-label="Assessment navigation"><Link className="button ghost" href={back}><ArrowLeft size={16} aria-hidden/>Cash outlook</Link><div><label className="sr-only" htmlFor="score-date">Assessment date</label><select id="score-date" value={point?.date ?? ""} onChange={event => window.location.assign(href(event.target.value))}>{!point && <option value="">Choose a saved score</option>}{[...timeline].reverse().map(item => <option value={item.date} key={item.date}>{date(item.date, true)} · {item.score} / 100</option>)}</select></div></nav>
    {!point ? <section className="card metric-intro"><h1>No score recorded for this date</h1><p>There is no saved health rating for {date(scoreDate, true)}. Choose an assessment above to see its explanation.</p></section> : <>
      <section className="card health-assessment-card">
        <header className="metric-intro"><span className="eyebrow">{company.name} · {date(point.date, true)}</span><h1>Your financial health, explained.</h1><div className="health-rating"><strong className="num">{point.score}<span> / 100</span></strong>{assessment && <span className="badge accent">{assessment.health.label}</span>}</div><p>{assessment?.health.note ?? "The score was saved, but its model explanation and evidence were not supplied for this date."}</p>{assessment && <span className="small muted">{assessment.model_version} · {assessment.history_mode === "reconstructed" ? "Reconstructed assessment" : "As known at this date"}</span>}</header>
        {!assessment ? <div className="metric-evidence"><h2>Explanation not available</h2><p className="metric-empty">This historical data point contains only a rating. Its original model response is needed to explain it; today’s drivers do not describe this score.</p></div> : <div className="metric-columns">
          <section className="metric-evidence" aria-labelledby="health-drivers"><div className="metric-section-heading"><h2 id="health-drivers">Behind this rating</h2><span className="badge">{assessment.drivers.length} drivers</span></div>
            {assessment.drivers.map((driver, driverIndex) => <details className="metric-flow" key={`${driver.label}:${driverIndex}`} open={driverIndex === 0}><summary><span className={`metric-flow-icon ${driver.points === null || driver.points === 0 ? "" : driver.points < 0 ? "payment" : "receipt"}`}>{driver.points === null || driver.points === 0 ? <FileText size={18} aria-hidden/> : driver.points < 0 ? <ArrowDownLeft size={18} aria-hidden/> : <ArrowUpRight size={18} aria-hidden/>}</span><span className="metric-flow-label"><strong>{driver.label}</strong><span>{driver.source_ids.length} referenced sources</span></span><strong className={`metric-flow-amount num ${driver.points !== null && driver.points < 0 ? "text-bad" : ""}`}>{driver.points === null ? "—" : `${signed(driver.points)} pts`}</strong><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="metric-driver-detail"><p>{driver.detail}</p>{driver.points === null && <p>No numerical effect was returned for this driver.</p>}{driver.source_ids.map(id => {
              const flow = assessment.flows.find(flow => flow.id === id && flow.known_on <= assessment.date);
              return flow ? <div className="metric-source" key={id}><span><FileText size={14} aria-hidden/>{flow.id} · {flow.label}</span><dl><div><dt>Source</dt><dd>{flow.source}</dd></div><div><dt>Due date</dt><dd>{date(flow.date, true)}</dd></div><div><dt>Known since</dt><dd>{date(flow.known_on, true)}</dd></div><div><dt>Original amount</dt><dd>{money(flow.amount_cents, company.currency, false, true)}</dd></div><div><dt>Settled at this assessment</dt><dd>{money(flow.settled_cents, company.currency, false, true)}</dd></div><div><dt>Timing</dt><dd>{flow.timing}</dd></div></dl></div> : <p key={id}>Source {id} was not included in this day’s assessment.</p>;
            })}{!driver.source_ids.length && <p>No supporting source records were returned for this driver.</p>}</div></details>)}
            {!assessment.drivers.length && <p className="metric-empty">No drivers were returned for this score.</p>}
          </section>
          <aside className="metric-explanation" aria-label="Rating context and evidence"><section><span className="eyebrow">Score movement</span><h2>{delta === null ? "Comparison not supplied" : `${signed(delta)} points${assessment.health.period ? ` over ${assessment.health.period}` : ""}`}</h2><dl className="metric-equation">{assessment.health.previous_score !== null && <><div><dt>Comparison score</dt><dd>{assessment.health.previous_score} / 100</dd></div><div><dt>Reported driver effects</dt><dd>{signed(driverTotal)} pts</dd></div>{unattributed !== 0 && <div><dt>Change without numerical attribution</dt><dd>{signed(unattributed!)} pts</dd></div>}</>}<div className="equation-result"><dt>Returned rating</dt><dd>{point.score} / 100</dd></div></dl><p className="small muted">{delta === null ? "No comparison score was supplied for this assessment." : "Point effects are supplied by the model. Reasons without a numerical effect are shown without an invented weight."}</p></section>
            <section><span className="eyebrow">Evidence at this date</span><h2>{assessment.coverage.filter(source => source.status === "available").length} of {assessment.coverage.length} sources available</h2><div className="metric-coverage">{assessment.coverage.map(source => <details key={source.label}><summary><span>{source.label}</span><span className={`badge ${source.status === "available" ? "good" : "warn"}`}>{source.status === "available" ? "Available" : source.status === "stale" ? "Stale" : "Missing"}</span></summary><p>{source.detail}{source.as_of && ` As of ${date(source.as_of, true)}.`}</p></details>)}{!assessment.coverage.length && <p className="small muted">No evidence coverage was returned for this date.</p>}</div><p className="small muted mt-4">Missing evidence is a gap in the inputs, not automatically poor health.</p></section>
          </aside>
        </div>}
        <p className="metric-footnote">This page shows the saved rating and explanation for {date(point.date, true)}. Cash forecasts and planning scenarios do not recalculate this score.</p>
      </section>
      <nav className="health-adjacent" aria-label="Other daily assessments">{previous ? <Link className="button secondary" href={href(previous.date)}><ArrowLeft size={16} aria-hidden/>{date(previous.date)} · {previous.score} / 100</Link> : <span/>}{next && <Link className="button secondary" href={href(next.date)}>{date(next.date)} · {next.score} / 100<ArrowRight size={16} aria-hidden/></Link>}</nav>
    </>}
  </div>;
}
