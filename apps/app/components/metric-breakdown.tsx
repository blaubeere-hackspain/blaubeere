"use client";
import { ArrowDownLeft, ArrowUpRight, CircleHelp, ChevronDown, FileText, Wallet, X } from "lucide-react";
import { Dialog } from "./dialog";
import { addDays, date, money } from "../lib/format";
import type { Company, Flow, Forecast } from "../lib/types";

export type ExplainedMetric = "cash" | "funding" | "shortfall" | "minimum" | "health";
const titles: Record<ExplainedMetric, string> = {
  cash: "Usable cash today", funding: "Funding needed", shortfall: "First cash floor breach",
  minimum: "Minimum projected cash", health: "Health score",
};

// Attribute the baseline to source records; the returned forecast remains authoritative.
export function cashEvidence(company: Company, through: string, end: string) {
  const tomorrow = addDays(company.assessment_date, 1);
  const records = company.flows.filter(flow => flow.known_on <= company.assessment_date && flow.kind !== "internal_transfer")
    .map(flow => ({ ...flow, projectedDate: flow.date < tomorrow ? tomorrow : flow.date, remaining: Math.sign(flow.amount_cents) * (Math.abs(flow.amount_cents) - flow.settled_cents) }))
    .filter(flow => flow.remaining !== 0).sort((a, b) => a.projectedDate.localeCompare(b.projectedDate) || a.id.localeCompare(b.id));
  const included = records.filter(flow => flow.projectedDate <= through);
  const incoming = included.filter(flow => flow.remaining > 0).reduce((sum, flow) => sum + flow.remaining, 0);
  const outgoing = -included.filter(flow => flow.remaining < 0).reduce((sum, flow) => sum + flow.remaining, 0);
  return { included, incoming, outgoing, closing: company.opening_cash_cents + incoming - outgoing,
    laterReceipts: records.filter(flow => flow.remaining > 0 && flow.projectedDate > through && flow.projectedDate <= end) };
}

export function MetricWhy({ metric, onClick, disabled }: { metric: ExplainedMetric; onClick: () => void; disabled?: boolean }) {
  return <button className="metric-why" type="button" onClick={onClick} disabled={disabled} aria-haspopup="dialog" aria-label={`Why this ${metric === "health" ? "score" : "number"}: ${titles[metric]}`}><CircleHelp size={14} aria-hidden/>Why this {metric === "health" ? "score" : "number"}<ArrowUpRight size={13} aria-hidden/></button>;
}

function Source({ flow, currency }: { flow: Flow; currency: string }) {
  return <div className="metric-source"><span><FileText size={14} aria-hidden/>{flow.id} · {flow.source}</span><dl><div><dt>Due date</dt><dd>{date(flow.date, true)}</dd></div><div><dt>Known since</dt><dd>{date(flow.known_on, true)}</dd></div><div><dt>Original amount</dt><dd>{money(flow.amount_cents, currency, false, true)}</dd></div><div><dt>Already settled</dt><dd>{money(flow.settled_cents, currency, false, true)}</dd></div></dl></div>;
}

export function MetricBreakdown({ company, forecast, metric, open, onClose }: { company: Company; forecast: Forecast; metric: ExplainedMetric; open: boolean; onClose: () => void }) {
  const format = (amount: number) => money(amount, company.currency, false, true);
  const health = metric === "health";
  const observed = health || metric === "cash";
  const point = metric === "shortfall" ? forecast.first_shortfall ?? forecast.minimum : forecast.minimum;
  const end = forecast.points.at(-1)!.date;
  const evidence = cashEvidence(company, point.date, end);
  const difference = point.cash_cents - evidence.closing;
  const driverTotal = company.drivers.reduce((total, driver) => total + driver.points, 0);
  const scoreDifference = company.health.score - company.health.previous_score - driverTotal;
  const available = company.coverage.filter(source => source.status === "available").length;
  const value = health ? `${company.health.score} / 100` : metric === "cash" ? format(company.opening_cash_cents) : metric === "funding" ? format(forecast.funding_needed_cents) : metric === "shortfall" ? forecast.first_shortfall ? date(forecast.first_shortfall.date, true) : "None forecast" : format(point.cash_cents);
  const description = health ? `The assessment reports ${company.health.score} points, compared with ${company.health.previous_score} over ${company.health.period}. Review the reported changes and their source evidence below.`
    : metric === "cash" ? `The assessment supplies ${format(company.opening_cash_cents)} of usable cash at ${date(company.assessment_date, true)}. This is the starting balance for the outlook.`
    : metric === "funding" ? forecast.funding_needed_cents > 0 ? `Cash reaches ${format(forecast.minimum.cash_cents)} on ${date(forecast.minimum.date)}. Keeping the ${format(forecast.buffer_cents)} floor requires an additional ${format(forecast.funding_needed_cents)} at that low point.` : `Every projected daily closing balance stays at or above the ${format(forecast.buffer_cents)} cash floor. No additional funding is needed within this outlook.`
    : metric === "shortfall" ? forecast.first_shortfall ? `This is the first date when projected daily closing cash falls below the ${format(forecast.buffer_cents)} floor. Cash is ${format(point.cash_cents)} that day.` : `No daily closing balance falls below the ${format(forecast.buffer_cents)} floor in this ${forecast.horizon_days}-day outlook. The closest point is ${format(point.cash_cents)} on ${date(point.date)}.`
    : `This is the lowest daily closing balance in the ${forecast.horizon_days}-day outlook, including the assessment date. Later receipts do not erase an earlier cash gap.`;

  function flowRows(flows: typeof evidence.included) {
    return flows.map(flow => <details className="metric-flow" key={flow.id}><summary><span className={`metric-flow-icon ${flow.remaining > 0 ? "receipt" : "payment"}`}>{flow.remaining > 0 ? <ArrowDownLeft size={18} aria-hidden/> : <ArrowUpRight size={18} aria-hidden/>}</span><span className="metric-flow-label"><strong>{flow.label}</strong><span>{date(flow.projectedDate)} · {flow.timing}{flow.projectedDate !== flow.date && " · overdue, assumed tomorrow"}</span></span><strong className={`metric-flow-amount num ${flow.remaining > 0 ? "text-good" : "text-bad"}`}>{flow.remaining > 0 ? "+" : ""}{format(flow.remaining)}</strong><ChevronDown className="disclosure-chevron" aria-hidden/></summary><Source flow={flow} currency={company.currency}/></details>);
  }

  return <Dialog className="planning-dialog metric-dialog" titleId="metric-title" open={open} onClose={onClose}>
    <div className="panel-head"><p className="planner-breadcrumb"><span>Cash outlook</span><span aria-hidden>/</span><strong>{titles[metric]}</strong></p><button className="icon-button" aria-label="Close metric breakdown" onClick={onClose}><X size={20}/></button></div>
    <div className="panel-body">
      <header className="metric-intro"><span className="badge accent">{observed ? "Reported assessment" : `${forecast.horizon_days}-day baseline`}</span><h2 id="metric-title">{titles[metric]}</h2><strong className="metric-headline num">{value}</strong><p>{description}</p><span className="small muted">{company.name} · as of {date(company.assessment_date, true)}</span></header>
      <div className="metric-columns">
        <section className="metric-evidence" aria-labelledby="metric-evidence-title"><div className="metric-section-heading"><h3 id="metric-evidence-title">{health ? "What moved the score" : metric === "cash" ? "Snapshot & history" : "Cash movements behind it"}</h3><span className="badge">{health ? `${company.drivers.length} drivers` : metric === "cash" ? company.currency : `Through ${date(point.date)}`}</span></div>
          {health ? <>
            <div className="metric-opening"><span>Previous score</span><strong className="num">{company.health.previous_score}<small> / 100</small></strong></div>
            {company.drivers.map((driver, index) => <details className="metric-flow" key={driver.label} open={index === 0}><summary><span className={`metric-flow-icon ${driver.points >= 0 ? "receipt" : "payment"}`}>{driver.points >= 0 ? <ArrowUpRight size={18} aria-hidden/> : <ArrowDownLeft size={18} aria-hidden/>}</span><span className="metric-flow-label"><strong>{driver.label}</strong><span>{driver.source_ids.length} referenced sources</span></span><strong className={`metric-flow-amount num ${driver.points >= 0 ? "text-good" : "text-bad"}`}>{driver.points > 0 ? "+" : ""}{driver.points}<small> pts</small></strong><ChevronDown className="disclosure-chevron" aria-hidden/></summary><div className="metric-driver-detail"><p>{driver.detail}</p>{driver.source_ids.map(id => { const flow = company.flows.find(flow => flow.id === id && flow.known_on <= company.assessment_date); return flow ? <Source key={id} flow={flow} currency={company.currency}/> : <p className="metric-caveat" key={id}>Source {id} is not included in this assessment.</p>; })}{!driver.source_ids.length && <p className="metric-caveat">No supporting source records were supplied for this driver.</p>}</div></details>)}
            {!company.drivers.length && <p className="metric-empty">No score drivers were supplied. The score cannot be explained from cash flows alone.</p>}
            {scoreDifference !== 0 && <p className="metric-caveat">{scoreDifference > 0 ? "+" : ""}{scoreDifference} points of change are not attributed by the supplied drivers.</p>}
          </> : metric === "cash" ? <>
            <div className="metric-opening"><span><Wallet size={16} aria-hidden/>Usable cash · {date(company.assessment_date)}</span><strong className="num">{format(company.opening_cash_cents)}</strong></div>
            <p className="metric-list-note">{company.history_mode === "reconstructed" ? "Reconstructed history, supplied with the assessment." : "Historical balances supplied as known at each cutoff."}</p>
            {company.history.map(point => <div className="metric-history-row" key={point.date}><span>{date(point.date, true)}</span><strong className="num">{format(point.cash_cents)}</strong></div>)}
            {!company.history.length && <p className="metric-empty">No historical balances were supplied.</p>}
            <p className="metric-caveat">Account-level balances and a reconciliation to the ledger are not supplied. Future receipts are not part of usable cash today.</p>
          </> : <>
            <div className="metric-opening"><span>Starting cash · {date(company.assessment_date)}</span><strong className="num">{format(company.opening_cash_cents)}</strong></div>
            {flowRows(evidence.included)}
            {!evidence.included.length && <p className="metric-empty">No unsettled cash movements are supplied through this date.</p>}
            <div className="metric-closing"><span>Projected cash · {date(point.date)}</span><strong className="num">{format(point.cash_cents)}</strong></div>
            {evidence.laterReceipts.length > 0 && <details className="metric-later"><summary>Receipts arriving after this date<span className="badge">{evidence.laterReceipts.length}</span></summary><p className="metric-list-note">These receipts fall later in the selected outlook and cannot cover an earlier shortfall.</p>{flowRows(evidence.laterReceipts)}</details>}
            {difference !== 0 && <p className="metric-caveat">The supplied cash flows do not fully reconcile to this forecast. {format(difference)} of other adjustments remain unexplained.</p>}
          </>}
        </section>
        <aside className="metric-explanation" aria-label="Calculation and evidence coverage"><section><span className="eyebrow">How it adds up</span><h3>{health ? "Reported score movement" : metric === "cash" ? "A starting balance" : metric === "funding" ? "The gap to your cash floor" : metric === "shortfall" ? "Check every closing balance" : "Follow the full cash path"}</h3>
          <dl className="metric-equation">{health ? <><div><dt>Previous score</dt><dd>{company.health.previous_score}</dd></div><div><dt>Reported driver effects</dt><dd>{driverTotal > 0 ? "+" : ""}{driverTotal} pts</dd></div>{scoreDifference !== 0 && <div><dt>Unattributed change</dt><dd>{scoreDifference > 0 ? "+" : ""}{scoreDifference} pts</dd></div>}<div className="equation-result"><dt>Current score</dt><dd>{company.health.score} / 100</dd></div></> : metric === "cash" ? <><div><dt>Observed at</dt><dd>{date(company.assessment_date)}</dd></div><div><dt>Reporting currency</dt><dd>{company.currency}</dd></div><div className="equation-result"><dt>Supplied usable cash</dt><dd>{format(company.opening_cash_cents)}</dd></div></> : <>
            <div><dt>Starting cash</dt><dd>{format(company.opening_cash_cents)}</dd></div><div><dt>Incoming through {date(point.date)}</dt><dd>+{format(evidence.incoming)}</dd></div><div><dt>Outgoing through {date(point.date)}</dt><dd>−{format(evidence.outgoing)}</dd></div>{difference !== 0 && <div><dt>Unexplained adjustments</dt><dd>{format(difference)}</dd></div>}<div className="equation-result"><dt>{metric === "shortfall" && forecast.first_shortfall ? "Cash at first breach" : "Lowest projected cash"}</dt><dd>{format(point.cash_cents)}</dd></div><div><dt>Required cash floor</dt><dd>{format(forecast.buffer_cents)}</dd></div>{metric === "funding" && <div className="equation-result"><dt>Additional funding needed</dt><dd>{format(forecast.funding_needed_cents)}</dd></div>}
          </>}</dl>
          <p className="small muted">{health ? company.health.note : metric === "cash" ? "This balance comes from the assessment, rather than being inferred from future cash flows." : "Daily closing cash, including the assessment date. Settlements are deducted; internal transfers and facts learned after the cutoff are excluded. Overdue open items are assumed to settle tomorrow."}</p>
        </section><section><span className="eyebrow">Evidence coverage</span><h3>{available} of {company.coverage.length} sources available</h3><p className="small muted">Coverage describes the supplied inputs, not a confidence score.</p><div className="metric-coverage">{company.coverage.map(source => <details key={source.label}><summary><span>{source.label}</span><span className={`badge ${source.status === "available" ? "good" : "warn"}`}>{source.status === "available" ? "Available" : source.status === "stale" ? "Stale" : "Missing"}</span></summary><p>{source.detail}{source.as_of && ` As of ${date(source.as_of, true)}.`}</p></details>)}{!company.coverage.length && <p className="small muted">No coverage information was supplied.</p>}</div></section></aside>
      </div>
      <p className="metric-footnote">{health ? "Driver effects are reported by the assessment; this view does not calculate a new health score." : observed ? "Observed cash is separate from forecasts and conditional plans." : "This explains the baseline metric. A plan overlay does not change it."}</p>
    </div>
  </Dialog>;
}
