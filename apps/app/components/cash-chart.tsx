"use client";
import { useEffect, useRef, useState } from "react";
import { date, money } from "../lib/format";
import type { Company, Forecast, Plan, Point } from "../lib/types";

export function cashScale(low: number, high: number) {
  const rough = Math.max((high - low) / 4, 100_000);
  const unit = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].find(n => n * unit >= rough)! * unit;
  const min = Math.floor(low / step) * step;
  const max = Math.max(min + step, Math.ceil(high / step) * step);
  return { min, max, ticks: Array.from({ length: Math.round((max - min) / step) + 1 }, (_, i) => min + i * step) };
}

export function CashChart({ company, forecast, plan }: { company: Company; forecast: Forecast; plan?: Plan }) {
  const [day, setDay] = useState(0);
  const [width, setWidth] = useState(1000);
  const svg = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(300, entry.contentRect.width)));
    if (svg.current) observer.observe(svg.current);
    return () => observer.disconnect();
  }, []);
  const right = width - 24;
  const selected = Math.min(day, forecast.horizon_days);
  const all = [...company.history, ...forecast.points, ...(plan?.forecast.points ?? [])];
  const low = Math.min(0, ...all.map(p => p.cash_cents));
  const high = Math.max(forecast.buffer_cents, ...all.map(p => p.cash_cents));
  const { min, max, ticks } = cashScale(low, high);
  const start = Date.parse(company.history[0]?.date ?? company.assessment_date);
  const end = Date.parse(forecast.points.at(-1)!.date);
  const x = (d: string) => 76 + ((Date.parse(d) - start) / (end - start)) * (right - 76);
  const y = (v: number) => 250 - ((v - min) / (max - min)) * 220;
  const path = (points: Point[], step: boolean) => points.map((p, i) => `${i ? (step ? `H${x(p.date)}V` : `L${x(p.date)},`) : `M${x(p.date)},`}${y(p.cash_cents)}`).join(" ");
  const point = forecast.points[selected];
  return <div className="cash-chart">
    <div className="chart-legend"><span><i className="legend-line history"/>History · {company.history_mode.replaceAll("_", " ")}</span><span><i className="legend-line baseline"/>Baseline forecast</span><span><i className="legend-line floor"/>Cash floor</span>{plan && <span><i className="legend-line plan"/>{plan.title}</span>}</div>
    <svg ref={svg} viewBox={`0 0 ${width} 290`} role="img" aria-labelledby="cash-chart-title cash-chart-desc" onPointerMove={event => {
      if (event.pointerType !== "mouse") return;
      const box = event.currentTarget.getBoundingClientRect();
      const pos = (event.clientX - box.left) / box.width * width;
      if (pos < x(company.assessment_date) || pos > right) return;
      setDay(Math.max(0, Math.min(forecast.horizon_days, Math.round((pos - x(company.assessment_date)) / (right - x(company.assessment_date)) * forecast.horizon_days))));
    }}>
      <title id="cash-chart-title">{`Daily closing cash, history and ${forecast.horizon_days}-day outlook`}</title>
      <desc id="cash-chart-desc">Lowest baseline cash is {money(forecast.minimum.cash_cents, company.currency)} on {date(forecast.minimum.date)}. Required funding above the floor is {money(forecast.funding_needed_cents, company.currency)}. Use the forecast day control below to inspect values.</desc>
      <rect x={x(company.assessment_date)} y="18" width={right - x(company.assessment_date)} height="232" fill="var(--accent-soft)" opacity=".25"/>
      {ticks.map(v => <g key={v}><line x1="76" x2={right} y1={y(v)} y2={y(v)} stroke="var(--line)"/><text x="62" y={y(v) + 4} textAnchor="end">{money(v, company.currency, true)}</text></g>)}
      <line x1="76" x2={right} y1={y(forecast.buffer_cents)} y2={y(forecast.buffer_cents)} stroke="var(--warn)" strokeDasharray="4 6"/>
      <line x1={x(company.assessment_date)} x2={x(company.assessment_date)} y1="18" y2="250" stroke="var(--subtle)" strokeDasharray="3 5"/>
      <text x={x(company.assessment_date) + 10} y="14">Forecast →</text>
      <path d={path(company.history, false)} fill="none" stroke="var(--ink)" strokeWidth="2.5"/>
      <path d={path(forecast.points, true)} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeDasharray="5 4"/>
      {plan && <path d={path(plan.forecast.points, true)} fill="none" stroke="var(--good)" strokeWidth="3"/>}
      {[...new Set([company.history[0]?.date, company.assessment_date, ...(width > 540 ? [forecast.points[Math.floor(forecast.horizon_days / 2)].date] : []), forecast.points.at(-1)!.date].filter(Boolean))].map(d => <text key={d} x={x(d!)} y="278" textAnchor={d === forecast.points.at(-1)!.date ? "end" : "middle"}>{date(d!)}</text>)}
      <line x1={x(point.date)} x2={x(point.date)} y1="24" y2="250" stroke="var(--accent)" opacity=".25"/>
      <circle cx={x(point.date)} cy={y(point.cash_cents)} r="5" fill="var(--surface)" stroke="var(--accent)" strokeWidth="2"/>
    </svg>
    <div className="chart-inspector"><div className="chart-controls"><label htmlFor="forecast-day">Inspect day <span className="mono">{selected}</span><input id="forecast-day" type="range" min="0" max={forecast.horizon_days} value={selected} aria-valuetext={`${date(point.date, true)}: baseline ${money(point.cash_cents, company.currency)}${plan ? `; plan ${money(plan.forecast.points[selected].cash_cents, company.currency)}` : ""}`} onChange={e => setDay(Number(e.target.value))}/></label><button className="button ghost" onClick={() => setDay(forecast.points.findIndex(p => p.date === forecast.minimum.date))}>Lowest cash</button></div><output htmlFor="forecast-day" aria-live="off"><span>{date(point.date)}</span><span>Baseline <strong className="num">{money(point.cash_cents, company.currency)}</strong></span>{plan && <span className="plan-value">Plan <strong className="num">{money(plan.forecast.points[selected].cash_cents, company.currency)}</strong></span>}</output></div>
    <p className="chart-note">Daily closing balances. Forecast combines contractual obligations and estimated receipts; inspect each source below.</p>
  </div>;
}
