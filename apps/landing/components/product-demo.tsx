"use client";
import Image from "next/image";
import { useState } from "react";
import { ArrowRight, CalendarDays, TrendingUp } from "lucide-react";
import logo from "../../brand/blau.svg";
import { appOrigin } from "../../metadata";
import { HealthScorePanel, healthForecastAvailable } from "../../app/components/health-score-panel";
import { MonthlyCashChart, cashForecastAvailable } from "../../app/components/monthly-cash-chart";
import { AgingCard, DefaultingCard, formatIndex } from "../../app/components/financial-cards";
import { date, money } from "../../app/lib/format";
import type { ModelRecord } from "../../app/lib/types";
import { ChartMotion } from "../../app/components/motion";
import painting from "../public/cash-horizon-oil.png";
import snapshot from "../data/product-preview.json";

export function ProductDemo() {
  const row = snapshot.record as ModelRecord;
  const [forecast, setForecast] = useState(false);
  const canForecast = cashForecastAvailable(row) || healthForecastAvailable(row);
  const month = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(`${row.as_of}T00:00:00Z`));
  const euro = (value: number | null | undefined) => value == null ? "Not available" : money(Math.round(value * 100), "EUR", false, true);
  const companyUrl = `${appOrigin}/demo?company=${snapshot.company_id}`;
  return <ChartMotion><section className="product-demo site-container" id="outlook" aria-labelledby="demo-title">
    <h2 id="demo-title" className="sr-only">A preview of your finance workspace</h2>
    <div className="demo-window" data-scroll-reveal>
      <div className="demo-topbar"><Image className="brand-logo" src={logo} alt="blau"/><a className="text-link" href={companyUrl}>Explore the dashboard<ArrowRight size={16} aria-hidden/></a></div>
      <div className="dashboard-preview">
        <div className="demo-backdrop" aria-hidden><Image src={painting} alt="" fill sizes="(max-width: 1320px) 100vw, 1200px"/></div>
        <div className="demo-heading"><div><h3>{snapshot.company_name}</h3><p>Financial health and the movements behind it.</p></div><span className="demo-period"><CalendarDays size={15} aria-hidden/>{month}</span></div>
        <section className="demo-stats" aria-label="Source data at the selected cutoff">{[["Monthly net movement", euro(row.cash?.flujo_neto), `Month ending ${date(row.as_of, true)}`], ["Overdue supplier payments", euro(row.payment?.pago_vencido_eur), `Due and unpaid at ${date(row.as_of, true)}`], ["Overdue customer collections", euro(row.payment?.cobro_vencido_eur), `Due and uncollected at ${date(row.as_of, true)}`], ["Defaulting", formatIndex(row.mora_indice), `Arrears index at ${date(row.as_of, true)}`]].map(([label, value, caption]) => <article className="card" key={label}><span>{label}</span><strong className="num">{value}</strong><p>{caption}</p></article>)}</section>
        <div className="dashboard-charts"><HealthScorePanel records={snapshot.history} row={row} forecast={forecast} action={<button type="button" className="button secondary forecast-toggle" aria-pressed={forecast} aria-controls="health cash" disabled={!canForecast && !forecast} title="Show the 30, 60 and 90-day cash and estimated health forecasts" onClick={() => setForecast(!forecast)}><TrendingUp size={16} aria-hidden/>Forecast</button>}/><MonthlyCashChart row={row} forecast={forecast} onCurrencyChange={() => setForecast(false)}/></div>
        <div className="obligation-cards"><AgingCard row={row} side="cobro"/><AgingCard row={row} side="pago"/><DefaultingCard row={row}/></div>
      </div>
    </div>
    <p className="preview-caption">{snapshot.company_name} · {month}. A snapshot from the published demo dataset.</p>
  </section>
  </ChartMotion>;
}
