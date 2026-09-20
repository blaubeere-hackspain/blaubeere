"use client";
import Link from "next/link";
import { useState } from "react";
import { ArrowLeft, ArrowRight, TrendingUp } from "lucide-react";
import { date, money } from "../lib/format";
import { healthHref } from "../lib/health";
import { MonthlyCashChart, cashForecastAvailable } from "./monthly-cash-chart";
import { AssessmentCalendar } from "./assessment-calendar";
import { AgingCard, DefaultingCard, formatIndex } from "./financial-cards";
import type { ModelAssessment, ModelRecord } from "../lib/types";
import { ChartMotion } from "./motion";

import { HealthScorePanel, modelNumber } from "./health-score-panel";
export { modelNumber } from "./health-score-panel";

const euro = (value: number | null | undefined) => value == null ? "Not available" : money(Math.round(value * 100), "EUR", false, true);
export function hasMonthlyData(row: ModelRecord) {
  return row.health_score !== null || row.n_meses_con_actividad > 0 ||
    (row.cash?.volumen_conocido ?? 0) > 0 ||
    (row.payment?.pago_n_facturas ?? 0) > 0 || (row.payment?.cobro_n_facturas ?? 0) > 0 ||
    (row.debt?.servicio_observado_eur ?? 0) > 0 || (row.debt?.servicio_esperado_eur ?? 0) > 0;
}
export function modelReason(reason: string) {
  const unknown = reason.match(/^([cpd]_eur|deficit_servicio_eur)_desconocido_en_(\d+)_meses$/);
  if (unknown) return `${({ c_eur: "Collections", p_eur: "Operating payments", d_eur: "Debt service", deficit_servicio_eur: "Debt-service shortfall" } as Record<string, string>)[unknown[1]]} could not be fully determined in ${unknown[2]} month(s) of the window.`;
  const fxIndex = reason.match(/^castigo_fx_indice_(\d+(?:\.\d+)?)$/);
  if (fxIndex) return `Foreign-currency receivables carried an FX risk index of ${formatIndex(Number(fxIndex[1]))}, so the corresponding FX reduction was applied to the score.`;
  return ({
    excluida_nota_cero_persistente: "Excluded by the published v4 rule for persistently near-zero scores. This reflects uneven data coverage, not necessarily poor financial health; the company’s source records remain available.",
    indice_fx_desconocido: "The foreign-currency risk index was unknown, so no FX adjustment was applied.",
    castigo_fx_acotado_por_intervalo: "Part of the receivables portfolio has no reference exchange rate, so the minimum compatible penalty consistent with what is known was applied.",
    sin_caja_reconstruida: "Cash could not be reconstructed; no cash cushion was applied.",
    saldo_reversa_negativo: "Reconstructed cash was negative, so the cash cushion is zero.",
    obligacion_vencida_desconocida: "Overdue obligations were unknown and omitted from the effective payment total.",
    multiplicador_deuda_desconocido: "The debt multiplier was unknown; no multiplier adjustment was applied.",
    pagos_operativos_no_demostrados: "No operating payments or positive overdue obligations were demonstrated, so no score was returned.",
    sin_flujos_observados_en_la_ventana: "No collections or effective payment obligations were observed in the model window.",
    nota_acotada_al_rango_0_100: "The returned score was bounded to the 0–100 range.",
    sin_colchon_estimado: "No cash cushion could be estimated.", sin_mora_observable: "Payment arrears were not observable; no arrears adjustment was applied.", colchon_saturado: "The cash cushion reached the model's cap.", denominador_nulo: "No calculable flow ratio for this window.", sin_actividad_de_caja_observada: "No cash-account activity was observed.", ventana_parcial: "The assessment uses fewer than six months of history.", r_hist_indefinida: "The historical ratio was undefined; the history adjustment was omitted.", sin_actividad_en_ventana: "No cash activity was observed in this window." } as Record<string,string>)[reason] ?? reason;
}
export function ModelDashboard({ data, scoreDate, demo = false }: { data: ModelAssessment; scoreDate?: string; demo?: boolean }) {
  const companyName = data.company.name.replace(/^COMP_0*(\d+)$/, "COMP $1");
  const availableDates = data.records.filter(hasMonthlyData).map(record => record.as_of);
  const [month, setMonth] = useState(availableDates.at(-1) ?? "");
  const [forecastMonth, setForecastMonth] = useState<string | null>(null);
  const row = data.records.find(row => row.as_of === (scoreDate ?? month));
  const forecast = Boolean(row && forecastMonth === row.as_of && cashForecastAvailable(row));
  const scoreHref = (day: string) => healthHref(data.company.id,day,demo);
  const selectedIndex = row ? data.records.indexOf(row) : -1;
  const previous = data.records[selectedIndex - 1];
  const next = data.records[selectedIndex + 1];
  return <ChartMotion><div className="model-dashboard">
    <p className="sr-only" role="status">{row ? `Showing data for ${date(row.as_of, true)}` : "No assessment selected"}</p>
    <header className="page-heading company-heading"><div><h1>{scoreDate ? "The story behind this rating." : companyName}</h1><p className="muted mt-2">{scoreDate ? companyName : "Financial health and the movements behind it."}</p></div><div className="company-heading-actions"><AssessmentCalendar dates={availableDates} selected={row?.as_of ?? ""} onSelect={value => scoreDate ? window.location.assign(scoreHref(value)) : setMonth(value)}/>{scoreDate && <Link className="button secondary" href={`${demo ? "/demo" : "/dashboard"}?company=${encodeURIComponent(data.company.id)}`}><ArrowLeft size={16} aria-hidden/>Overview</Link>}</div></header>
    {!row ? <section className="card metric-intro"><h2>{availableDates.length ? "No assessment for this date" : "No recorded activity yet"}</h2><p>{availableDates.length ? "Choose an available month above." : "No cash movements, invoices or model activity were recorded for this company."}</p></section> : <>
      <section className="stats-grid model-stats" aria-label="Source data at the selected cutoff">{[["Monthly net movement",euro(row.cash?.flujo_neto),`Month ending ${date(row.as_of, true)}`],["Overdue supplier payments",euro(row.payment?.pago_vencido_eur),`Due and unpaid at ${date(row.as_of, true)}`],["Overdue customer collections",euro(row.payment?.cobro_vencido_eur),`Due and uncollected at ${date(row.as_of, true)}`],["Defaulting",formatIndex(row.mora_indice),`Arrears index at ${date(row.as_of, true)}`]].map(([label,value,caption])=><article className="stat-card" key={label}><div><span>{label}</span></div><strong className="stat-value num">{value}</strong><p>{caption}</p></article>)}</section>
      <div className={scoreDate ? undefined : "dashboard-charts"}>
      <HealthScorePanel records={data.records} row={row} detail={Boolean(scoreDate)} action={!scoreDate && <button type="button" className="button secondary forecast-toggle" aria-pressed={forecast} aria-controls="cash" disabled={!cashForecastAvailable(row)} title={cashForecastAvailable(row) ? "Show the 30, 60 and 90-day invoice cash forecast" : "No projected EUR cash balance is available for this month"} onClick={() => setForecastMonth(forecast ? null : row.as_of)}><TrendingUp size={16} aria-hidden/>Forecast</button>}/>
      {!scoreDate && <MonthlyCashChart row={row} forecast={forecast} onCurrencyChange={() => setForecastMonth(null)}/>}
      </div>
      {!scoreDate ? <div className="obligation-cards" id="payments"><AgingCard row={row} side="cobro"/><AgingCard row={row} side="pago"/><DefaultingCard row={row}/></div> : <section className="card health-assessment-card"><div className="metric-columns">
        <section className="metric-evidence"><div className="metric-section-heading"><h2>How the model reached this score</h2><span className="badge">{row.n_meses_ventana} months</span></div><p className="small muted">The v4 model adds observed debt-service shortfalls and overdue obligations to known payments. It uses reconstructed cash for the cushion, then applies the robust arrears index and debt multiplier. Unknown inputs remain omitted and disclosed.</p><dl className="metric-equation">{[["Known collections (C6)",euro(row.c6)],["Operating payments (P6)",euro(row.p6)],["Debt service (D6)",euro(row.d6)],["Debt-service shortfall · window",euro(row.deficit_servicio_6)],["Overdue obligations · cutoff",euro(row.obligacion_vencida_m)],["Effective obligations (T6)",euro(row.t6_efectivo)],["Reconstructed cash cushion",euro(row.colchon_v4)],["Applicable cash cushion",euro(row.colchon_aplicable)],["Own historical collection ratio",modelNumber(row.r_hist===null?null:row.r_hist*100,"%")],["Historical adjustment (k)",row.r_hist===null?"Omitted":euro(row.k)],["Score before adjustments",modelNumber(row.h_antes_de_ajustes)],["Robust arrears index",formatIndex(row.mora_indice)],["Arrears reduction",modelNumber(row.penalizacion_mora_puntos," points")],["Debt multiplier",modelNumber(row.multiplicador_deuda,"×",3)],["Debt multiplier reduction",modelNumber(row.penalizacion_multiplicador_puntos," points")],["FX risk index applied",formatIndex(row.indice_fx_aplicado)],["FX reduction",modelNumber(row.penalizacion_fx_puntos," points",2)],["Returned health score",modelNumber(row.health_score)]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><details className="source-records"><summary>Formula and parameters</summary><div className="model-formula"><p>H = 100 × (C6 + cushion + k × historical ratio) / (C6 + cushion + T6_effective + k)</p><p>T6_effective = P6 + D6 + service shortfall over the window + overdue obligations at the cutoff</p><p>Final score = H × (1 − beta × robust arrears index) × debt multiplier × (1 − beta_fx × FX risk index)</p><p>k = {row.k} EUR · alpha = {row.alpha} · beta = {row.beta}{row.beta_fx != null && ` · beta_fx = ${row.beta_fx}`}. The cash cushion is capped at alpha × T6_effective. Unknown arrears, an unknown FX risk index or debt multipliers apply no adjustment. k is omitted when the historical ratio is undefined. The displayed score comes directly from the saved model output.</p></div></details><h2 className="mt-6">What limits this assessment</h2><ul className="model-reasons">{row.reasons.map(reason=><li key={reason}>{modelReason(reason)}</li>)}{!row.reasons.length && <li>No additional reason codes were returned.</li>}</ul>{row.p6===0 && row.health_score!==null && <p className="model-note">No operating payments were identified, but positive overdue obligations provide evidence for a v4 rating. Missing payments must not be read as perfect financial health.</p>}</section>
        <aside className="metric-explanation" aria-label="Supporting model inputs"><section><span className="eyebrow">Cash movements</span><h2>Reconstructed from the anchor</h2><dl className="metric-equation"><div><dt>Reconstructed cash</dt><dd>{euro(row.cash?.saldo_reversa_eur)}</dd></div><div><dt>This month’s net movement</dt><dd>{euro(row.cash?.flujo_neto)}</dd></div><div><dt>Anchor balance · 1 Sept 2026</dt><dd>{euro(row.cash?.saldo_ancla_eur)}</dd></div><div><dt>Estimated coverage</dt><dd>{modelNumber(row.cash?.meses_de_cobertura_reversa," months")}</dd></div></dl><p className="small muted">Cash is reconstructed backwards from the 1 September 2026 snapshot using subsequent known movements. It is not a bank balance observed at this cutoff or usable cash today. Missing anchors or FX amounts leave gaps.</p></section><section><span className="eyebrow">Payment timing</span><h2>Obligations due at this cutoff</h2><dl className="metric-equation"><div><dt>Known supplier exposure</dt><dd>{euro(row.payment?.pago_exposicion_eur)}</dd></div><div><dt>Overdue at cutoff</dt><dd>{euro(row.payment?.pago_vencido_eur)}</dd></div><div><dt>Robust arrears index</dt><dd>{formatIndex(row.mora_indice)}</dd></div><div><dt>Debt multiplier</dt><dd>{modelNumber(row.multiplicador_deuda,"×",3)}</dd></div></dl><p className="small muted">The index measures weighted delay severity and persistence, not the percentage of invoices overdue. Missing evidence does not imply zero arrears.</p></section></aside>
      </div></section>}
      {scoreDate && <nav className="health-adjacent" aria-label="Other assessments">{previous?<Link className="button secondary" href={scoreHref(previous.as_of)}><ArrowLeft size={16} aria-hidden/>{date(previous.as_of,true)}</Link>:<span/>}{next&&<Link className="button secondary" href={scoreHref(next.as_of)}>{date(next.as_of,true)}<ArrowRight size={16} aria-hidden/></Link>}</nav>}
    </>}
  </div>
  </ChartMotion>;
}
