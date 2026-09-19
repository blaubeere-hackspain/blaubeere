"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { date, money, monthlyTimeline } from "../lib/format";
import { healthHref } from "../lib/health";
import { MonthlyCashChart } from "./monthly-cash-chart";
import { AssessmentCalendar } from "./assessment-calendar";
import { AgingCard, DebtServiceCard, SegmentedGauge } from "./financial-cards";
import type { ModelAssessment, ModelRecord } from "../lib/types";

export const modelNumber = (value: number | null | undefined, suffix = "", maximumFractionDigits = 1) => value == null ? "Not available" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits }).format(value)}${suffix}`;
const euro = (value: number | null | undefined) => value == null ? "Not available" : money(Math.round(value * 100), "EUR", false, true);
const confidence: Record<string, string> = { alta: "High", media: "Medium", baja: "Low", ninguna: "None", excluida: "Excluded" };
export function hasMonthlyData(row: ModelRecord) {
  return row.health_score !== null || row.n_meses_con_actividad > 0 ||
    (row.cash?.volumen_conocido ?? 0) > 0 ||
    (row.payment?.pago_n_facturas ?? 0) > 0 || (row.payment?.cobro_n_facturas ?? 0) > 0 ||
    (row.debt?.servicio_observado_eur ?? 0) > 0 || (row.debt?.servicio_esperado_eur ?? 0) > 0;
}
export function modelReason(reason: string) {
  const unknown = reason.match(/^([cpd]_eur|deficit_servicio_eur)_desconocido_en_(\d+)_meses$/);
  if (unknown) return `${({ c_eur: "Collections", p_eur: "Operating payments", d_eur: "Debt service", deficit_servicio_eur: "Debt-service shortfall" } as Record<string, string>)[unknown[1]]} could not be fully determined in ${unknown[2]} month(s) of the window.`;
  return ({
    excluida_nota_cero_persistente: "Excluded by the published v4 rule for persistently near-zero scores. This reflects uneven data coverage, not necessarily poor financial health; the company’s source records remain available.",
    sin_caja_reconstruida: "Cash could not be reconstructed; no cash cushion was applied.",
    saldo_reversa_negativo: "Reconstructed cash was negative, so the cash cushion is zero.",
    obligacion_vencida_desconocida: "Overdue obligations were unknown and omitted from the effective payment total.",
    multiplicador_deuda_desconocido: "The debt multiplier was unknown; no multiplier adjustment was applied.",
    pagos_operativos_no_demostrados: "No operating payments or positive overdue obligations were demonstrated, so no score was returned.",
    sin_flujos_observados_en_la_ventana: "No collections or effective payment obligations were observed in the model window.",
    nota_acotada_al_rango_0_100: "The returned score was bounded to the 0–100 range.",
    sin_colchon_estimado: "No cash cushion could be estimated.", sin_mora_observable: "Payment arrears were not observable; no arrears adjustment was applied.", colchon_saturado: "The cash cushion reached the model's cap.", denominador_nulo: "No calculable flow ratio for this window.", sin_actividad_de_caja_observada: "No cash-account activity was observed.", ventana_parcial: "The assessment uses fewer than six months of history.", r_hist_indefinida: "The historical ratio was undefined; the history adjustment was omitted.", sin_actividad_en_ventana: "No cash activity was observed in this window." } as Record<string,string>)[reason] ?? reason;
}
function ScoreChart({ records, row }: { records: ModelRecord[]; row: ModelRecord }) {
  const [width, setWidth] = useState(1000);
  const svg = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    if (svg.current) observer.observe(svg.current);
    return () => observer.disconnect();
  }, []);
  const right = width - 20;
  const timeline = monthlyTimeline(records.map(record => record.as_of));
  const x = (index: number) => 42 + index * (right - 42) / Math.max(1, timeline.length - 1);
  const y = (score: number) => 250 - score * 2.2;
  const path = records.map((row, index) => row.health_score === null ? "" : `${index && records[index - 1].health_score !== null ? "L" : "M"}${x(index)},${y(row.health_score)}`).join(" ");
  const selected = records.indexOf(row);
  return <div className="model-chart health-history-chart"><svg ref={svg} viewBox={`0 0 ${width} 290`} role="img" aria-labelledby="model-chart-title model-chart-description"><title id="model-chart-title">Monthly health score history</title><desc id="model-chart-description">Published model ratings from 0 to 100. Gaps mean no score was returned. Use the assessment date selector above to inspect a rating and its source data.</desc>{[0,25,50,75,100].map(value => <g key={value}><line x1="42" x2={right} y1={y(value)} y2={y(value)} stroke="var(--line)"/><text x="30" y={y(value)+4} textAnchor="end">{value}</text></g>)}<path d={path} fill="none" stroke="var(--accent)" strokeWidth="2.5"/>{records.map((record,index) => record.health_score === null ? null : <circle key={record.as_of} cx={x(index)} cy={y(record.health_score)} r={record.as_of === row.as_of ? 6 : width < 540 ? 3 : 4} fill={record.as_of === row.as_of ? "var(--accent)" : "var(--surface)"} stroke="var(--accent)" strokeWidth="2"><title>{`${date(record.as_of,true)}: ${modelNumber(record.health_score)} / 100`}</title></circle>)}{selected >= 0 && <line x1={x(selected)} x2={x(selected)} y1="18" y2="256" stroke="var(--accent)" strokeDasharray="4 4"/>}{timeline.map((day,index) => index===0 || index===timeline.length-1 || (width > 540 && index===Math.floor(timeline.length/2)) ? <text key={day} x={x(index)} y="279" textAnchor={index===0?"start":index===timeline.length-1?"end":"middle"}>{timeline.length > records.length && index===timeline.length-1 ? "Today" : date(day,true)}</text> : null)}</svg>
    {!records.some(record => record.health_score !== null) && <p className="chart-note">No score was returned for this company. Its available source data is shown below.</p>}
    <p className="chart-note">Monthly observations{records.length ? ` through ${date(records.at(-1)!.as_of, true)}` : " from 2025 are not available"}. Missing ratings stay empty; no daily scores are interpolated.</p></div>;
}

function HealthScoreGauge({ row }: { row: ModelRecord }) {
  const score = row.excluida ? null : row.health_score;
  return <SegmentedGauge className="health-score-gauge" segments={score === null ? [] : [{ fraction: score / 100, color: "var(--accent)" }]}><div className="health-score-value" data-empty={score === null}><strong className="num">{row.excluida ? "Excluded" : score === null ? "No score" : modelNumber(score)}</strong>{score !== null && <span>/ 100</span>}</div></SegmentedGauge>;
}

function MonthlyBreakdown({ row }: { row: ModelRecord }) {
  const cash = row.cash;
  return <><div className="obligation-cards" id="payments"><AgingCard row={row} side="cobro"/><AgingCard row={row} side="pago"/><DebtServiceCard row={row}/></div><div className="monthly-breakdown">
    <section className="card monthly-detail"><h2>Cash movements · {date(row.as_of)}</h2><p className="small muted">Signed amounts in the published cash categories.</p><dl className="metric-equation">{[
      ["Operating inflows", cash?.flujo_operating_in], ["Operating outflows", cash?.flujo_operating_out],
      ["Financing inflows", cash?.flujo_financing_in], ["Financing outflows", cash?.flujo_financing_out],
      ["Investment inflows", cash?.flujo_investment_in], ["Investment outflows", cash?.flujo_investment_out],
      ["Transfers", cash?.flujo_transfer], ["Non-economic movements", cash?.flujo_non_economic], ["Unclassified movements", cash?.flujo_unknown],
    ].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd className="num">{euro(value as number | null | undefined)}</dd></div>)}</dl><p className="small muted">Cash confidence: {cash ? confidence[cash.confidence] ?? cash.confidence : "Not available"}. These are the published cash-backfill categories; the model’s rolling totals also use transfer-resolution adjustments.</p>{cash?.flags.includes("agujeros_en_tramo") && <p className="model-note mt-4">Some movements have unknown EUR amounts. The reported totals cover known amounts only.</p>}</section>
    <section className="card monthly-detail monthly-debt"><div><h2>Debt and overdue obligations</h2><p className="small muted">Observed debt service and service shortfalls span {row.n_meses_ventana} months ending {date(row.as_of, true)}. Overdue obligations are a stock at this cutoff, counted once. D6 is not the outstanding debt balance.</p></div><dl className="metric-equation"><div><dt>Debt service (D6)</dt><dd className="num">{euro(row.d6)}</dd></div><div><dt>Operating payments (P6)</dt><dd className="num">{euro(row.p6)}</dd></div><div><dt>Service shortfall · window</dt><dd className="num">{euro(row.deficit_servicio_6)}</dd></div><div><dt>Overdue obligations · cutoff</dt><dd className="num">{euro(row.obligacion_vencida_m)}</dd></div><div className="equation-result"><dt>Effective obligations (T6)</dt><dd className="num">{euro(row.t6_efectivo)}</dd></div></dl></section>
  </div></>;
}

function MonthlyRecords({ records, company, selected, demo }: { records: ModelRecord[]; company: string; selected: string; demo: boolean }) {
  return <section className="card monthly-records" id="history"><div className="card-heading"><div><h2>Monthly source records</h2><p className="small muted mt-1">Past month-end records and their score explanations.</p></div><span className="badge">{records.length} months</span></div><div className="table-scroll" role="region" aria-label="Monthly source records, scroll to inspect all columns" tabIndex={0}><table><caption className="sr-only">Published month-end scores, cash movements, payment arrears and rolling debt service in EUR</caption><thead><tr><th scope="col">Month end</th><th scope="col">Health score</th><th scope="col">Net movement</th><th scope="col">Reconstructed cash</th><th scope="col">Overdue payments</th><th scope="col">Overdue collections</th><th scope="col">Debt service · window</th></tr></thead><tbody>{[...records].reverse().map(row => <tr key={row.as_of} data-selected={row.as_of === selected}><th scope="row">{date(row.as_of, true)}</th><td><Link className="monthly-rating-link" href={healthHref(company, row.as_of, demo)} aria-label={`Explain ${date(row.as_of, true)} rating: ${modelNumber(row.health_score)}`}>{modelNumber(row.health_score)}<ArrowRight size={14} aria-hidden/></Link></td><td className="num">{euro(row.cash?.flujo_neto)}</td><td className="num">{euro(row.cash?.saldo_reversa_eur)}</td><td className="num">{euro(row.payment?.pago_vencido_eur)}</td><td className="num">{euro(row.payment?.cobro_vencido_eur)}</td><td className="num">{euro(row.d6)}<span>{row.n_meses_ventana} months</span></td></tr>)}</tbody></table></div></section>;
}

export function ModelDashboard({ data, scoreDate, demo = false }: { data: ModelAssessment; scoreDate?: string; demo?: boolean }) {
  const companyName = data.company.name.replace(/^COMP_0*(\d+)$/, "COMP $1");
  const availableDates = data.records.filter(hasMonthlyData).map(record => record.as_of);
  const [month, setMonth] = useState(availableDates.at(-1) ?? "");
  const row = data.records.find(row => row.as_of === (scoreDate ?? month));
  const scoreHref = (day: string) => healthHref(data.company.id,day,demo);
  const chartRecords = data.records.filter(record => record.as_of >= "2025-01-01");
  const selectedIndex = row ? data.records.indexOf(row) : -1;
  const previous = data.records[selectedIndex - 1];
  const next = data.records[selectedIndex + 1];
  const delta = row?.health_score != null && previous?.health_score != null ? row.health_score - previous.health_score : null;
  return <div className="model-dashboard">
    <p className="sr-only" role="status">{row ? `Showing data for ${date(row.as_of, true)}` : "No assessment selected"}</p>
    <header className="page-heading company-heading"><div><h1>{scoreDate ? "The story behind this rating." : companyName}</h1><p className="muted mt-2">{scoreDate ? companyName : "Financial health and the movements behind it."}</p></div><div className="company-heading-actions"><AssessmentCalendar dates={availableDates} selected={row?.as_of ?? ""} onSelect={value => scoreDate ? window.location.assign(scoreHref(value)) : setMonth(value)}/>{scoreDate && <Link className="button secondary" href={`${demo ? "/demo" : "/dashboard"}?company=${encodeURIComponent(data.company.id)}`}><ArrowLeft size={16} aria-hidden/>Overview</Link>}</div></header>
    {!row ? <section className="card metric-intro"><h2>{availableDates.length ? "No assessment for this date" : "No recorded activity yet"}</h2><p>{availableDates.length ? "Choose an available month above." : "No cash movements, invoices or model activity were recorded for this company."}</p></section> : <>
      <section className="stats-grid model-stats" aria-label="Source data at the selected cutoff">{[["Monthly net movement",euro(row.cash?.flujo_neto),`Month ending ${date(row.as_of, true)}`],["Overdue supplier payments",euro(row.payment?.pago_vencido_eur),`Due and unpaid at ${date(row.as_of, true)}`],["Overdue customer collections",euro(row.payment?.cobro_vencido_eur),`Due and uncollected at ${date(row.as_of, true)}`],["Observed debt service",euro(row.d6),`${row.n_meses_ventana} months ending ${date(row.as_of, true)}`]].map(([label,value,caption])=><article className="stat-card" key={label}><div><span>{label}</span></div><strong className="stat-value num">{value}</strong><p>{caption}</p></article>)}</section>
      <div className={scoreDate ? undefined : "dashboard-charts"}>
      <section className="card health-overview-panel" id="health" aria-labelledby="health-title">
        <div className="health-overview-main"><div className="card-heading"><div><h2 id="health-title">Health score</h2><p className="small muted mt-1">{scoreDate ? "Published rating at this cutoff" : "How the company’s cash-flow health has changed"}</p></div></div>
          <div className="health-selected-score"><HealthScoreGauge row={row}/><div className="health-score-context"><span>{date(row.as_of,true)}</span><span>{delta === null ? "No comparable previous score" : `${delta > 0 ? "+" : ""}${modelNumber(delta)} points since ${date(previous.as_of)}`}</span></div></div>
          {!scoreDate && <ScoreChart records={chartRecords} row={row}/>}
        </div>
        {!scoreDate && <footer className="health-chart-footer"><Link className="button secondary" href={scoreHref(row.as_of)}>{row.health_score === null ? "Why no score?" : "Explain this score"}<ArrowRight size={16} aria-hidden/></Link></footer>}
      </section>
      {!scoreDate && <MonthlyCashChart row={row}/>}
      </div>
      {!scoreDate ? <><MonthlyBreakdown row={row}/><MonthlyRecords records={data.records} company={data.company.id} selected={row.as_of} demo={demo}/></> : <section className="card health-assessment-card"><div className="metric-columns">
        <section className="metric-evidence"><div className="metric-section-heading"><h2>How the model reached this score</h2><span className="badge">{row.n_meses_ventana} months</span></div><p className="small muted">The v4 model adds observed debt-service shortfalls and overdue obligations to known payments. It uses reconstructed cash for the cushion, then applies the robust arrears index and debt multiplier. Unknown inputs remain omitted and disclosed.</p><dl className="metric-equation">{[["Known collections (C6)",euro(row.c6)],["Operating payments (P6)",euro(row.p6)],["Debt service (D6)",euro(row.d6)],["Debt-service shortfall · window",euro(row.deficit_servicio_6)],["Overdue obligations · cutoff",euro(row.obligacion_vencida_m)],["Effective obligations (T6)",euro(row.t6_efectivo)],["Reconstructed cash cushion",euro(row.colchon_v4)],["Applicable cash cushion",euro(row.colchon_aplicable)],["Own historical collection ratio",modelNumber(row.r_hist===null?null:row.r_hist*100,"%")],["Historical adjustment (k)",row.r_hist===null?"Omitted":euro(row.k)],["Score before adjustments",modelNumber(row.h_antes_de_ajustes)],["Robust arrears index",modelNumber(row.mora_indice," / 1",3)],["Arrears reduction",modelNumber(row.penalizacion_mora_puntos," points")],["Debt multiplier",modelNumber(row.multiplicador_deuda,"×",3)],["Debt multiplier reduction",modelNumber(row.penalizacion_multiplicador_puntos," points")],["Returned health score",modelNumber(row.health_score)]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><details className="source-records"><summary>Formula and parameters</summary><div className="model-formula"><p>H = 100 × (C6 + cushion + k × historical ratio) / (C6 + cushion + T6_effective + k)</p><p>T6_effective = P6 + D6 + service shortfall over the window + overdue obligations at the cutoff</p><p>Final score = H × (1 − beta × robust arrears index) × debt multiplier</p><p>k = {row.k} EUR · alpha = {row.alpha} · beta = {row.beta}. The cash cushion is capped at alpha × T6_effective. Unknown arrears or debt multipliers apply no adjustment. k is omitted when the historical ratio is undefined. The displayed score comes directly from the saved model output.</p></div></details><h2 className="mt-6">What limits this assessment</h2><ul className="model-reasons">{row.reasons.map(reason=><li key={reason}>{modelReason(reason)}</li>)}{!row.reasons.length && <li>No additional reason codes were returned.</li>}</ul>{row.p6===0 && row.health_score!==null && <p className="model-note">No operating payments were identified, but positive overdue obligations provide evidence for a v4 rating. Missing payments must not be read as perfect financial health.</p>}</section>
        <aside className="metric-explanation" aria-label="Supporting model inputs"><section><span className="eyebrow">Cash movements</span><h2>Reconstructed from the anchor</h2><dl className="metric-equation"><div><dt>Reconstructed cash</dt><dd>{euro(row.cash?.saldo_reversa_eur)}</dd></div><div><dt>This month’s net movement</dt><dd>{euro(row.cash?.flujo_neto)}</dd></div><div><dt>Anchor balance · 1 Sept 2026</dt><dd>{euro(row.cash?.saldo_ancla_eur)}</dd></div><div><dt>Estimated coverage</dt><dd>{modelNumber(row.cash?.meses_de_cobertura_reversa," months")}</dd></div></dl><p className="small muted">Cash is reconstructed backwards from the 1 September 2026 snapshot using subsequent known movements. It is not a bank balance observed at this cutoff or usable cash today. Missing anchors or FX amounts leave gaps.</p></section><section><span className="eyebrow">Payment timing</span><h2>Obligations due at this cutoff</h2><dl className="metric-equation"><div><dt>Known supplier exposure</dt><dd>{euro(row.payment?.pago_exposicion_eur)}</dd></div><div><dt>Overdue at cutoff</dt><dd>{euro(row.payment?.pago_vencido_eur)}</dd></div><div><dt>Robust arrears index</dt><dd>{modelNumber(row.mora_indice," / 1",3)}</dd></div><div><dt>Debt multiplier</dt><dd>{modelNumber(row.multiplicador_deuda,"×",3)}</dd></div></dl><p className="small muted">The index measures weighted delay severity and persistence, not the percentage of invoices overdue. Missing evidence does not imply zero arrears.</p></section></aside>
      </div></section>}
      {scoreDate && <nav className="health-adjacent" aria-label="Other assessments">{previous?<Link className="button secondary" href={scoreHref(previous.as_of)}><ArrowLeft size={16} aria-hidden/>{date(previous.as_of,true)}</Link>:<span/>}{next&&<Link className="button secondary" href={scoreHref(next.as_of)}>{date(next.as_of,true)}<ArrowRight size={16} aria-hidden/></Link>}</nav>}
    </>}
  </div>;
}
