"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { m } from "framer-motion";
import { date, monthlyTimeline } from "../lib/format";
import type { ModelRecord } from "../lib/types";
import { SegmentedGauge } from "./financial-cards";
import { ChartMotion, enterTransition, useChartMotion } from "./motion";

type ScorePoint = Pick<ModelRecord, "as_of" | "health_score">;
export const modelNumber = (value: number | null | undefined, suffix = "", maximumFractionDigits = 1) => value == null ? "Not available" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits }).format(value)}${suffix}`;

export function healthForecastAvailable(row: ModelRecord) {
  return row.health_projection?.as_of === row.as_of && row.health_projection.points.some(point => point.health_score !== null);
}

function ScoreChart({ records, row, height, forecast }: { records: ScorePoint[]; row: ModelRecord; height: number; forecast: boolean }) {
  // Las animaciones de entrada solo corren en cliente tras el montaje: sin JS
  // el markup estatico ya es el estado final (I1), y fuera de ChartMotion los
  // elementos son los planos de siempre (LazyMotion strict exigiria proveedor).
  const animate = useChartMotion();
  const [width, setWidth] = useState(1000);
  const svg = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    if (svg.current) observer.observe(svg.current);
    return () => observer.disconnect();
  }, []);
  const right = width - 20;
  const projection = forecast && healthForecastAvailable(row) ? row.health_projection! : undefined;
  const estimates = projection?.points.filter(point => point.date >= "2025-01-01") ?? [];
  const observedTimeline = monthlyTimeline(records.map(record => record.as_of));
  const today = observedTimeline.length > records.length ? observedTimeline.at(-1) : undefined;
  const timeline = [...new Set([...observedTimeline, ...estimates.map(point => point.date)])].sort();
  const start = Date.parse(timeline[0] ?? row.as_of);
  const end = Date.parse(timeline.at(-1) ?? row.as_of);
  const xDate = (day: string) => 42 + (Date.parse(day) - start) * (right - 42) / Math.max(1, end - start);
  const x = (index: number) => xDate(records[index].as_of);
  const y = (score: number) => height - 40 - score / 100 * (height - 70);
  const path = records.map((row, index) => row.health_score === null ? "" : `${index && records[index - 1].health_score !== null ? "L" : "M"}${x(index)},${y(row.health_score)}`).join(" ");
  const selected = records.findIndex(record => record.as_of === row.as_of);
  const projectedPoints = [{ date: row.as_of, health_score: row.health_score }, ...estimates].filter(point => point.date >= (timeline[0] ?? row.as_of));
  const projectedPath = projectedPoints.map((point, index) => point.health_score === null ? "" : `${index && projectedPoints[index - 1].health_score !== null ? "L" : "M"}${xDate(point.date)},${y(point.health_score)}`).join(" ");
  // La nota se dibuja de izquierda a derecha con pathLength (framer-motion lo
  // implementa con strokeDasharray/strokeDashoffset, que solo existen en
  // cliente despues del montaje; el path estatico no lleva guiones). Los
  // puntos aparecen escalonados a medida que el trazo los alcanza, creciendo
  // con scale: el atributo r del mes seleccionado nunca se anima (I2).
  const line = animate ? <m.path d={path} fill="none" stroke="var(--accent)" strokeWidth="2.5" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={enterTransition}/> : <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2.5"/>;
  const content = <div className="model-chart health-history-chart">{projection && <p className="health-forecast-legend" title={projection.assumptions}><i aria-hidden/>Estimated health · cash only</p>}<svg ref={svg} style={{ height }} viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="model-chart-title model-chart-description"><title id="model-chart-title">Monthly health score history</title><desc id="model-chart-description">Published model ratings from 0 to 100. Gaps mean no score was returned.{projection && ` The dashed overlay estimates health from forecast cash at the selected cutoff. ${projection.assumptions}`}</desc>{[0,25,50,75,100].map(value => <g key={value}><line x1="42" x2={right} y1={y(value)} y2={y(value)} stroke="var(--line)"/><text x="30" y={y(value)+4} textAnchor="end">{value}</text></g>)}{line}{records.map((record,index) => {
    if (record.health_score === null) return null;
    const isSelected = record.as_of === row.as_of;
    const attrs = { cx: x(index), cy: y(record.health_score), r: isSelected ? 6 : width < 540 ? 3 : 4, fill: isSelected ? "var(--accent)" : "var(--surface)", stroke: "var(--accent)", strokeWidth: 2 };
    const label = <title>{`${date(record.as_of,true)}: ${modelNumber(record.health_score)} / 100`}</title>;
    if (!animate) return <circle key={record.as_of} {...attrs}>{label}</circle>;
    const Point = m.circle;
    return <Point key={record.as_of} {...attrs} style={{ transformBox: "fill-box", transformOrigin: "center" }} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ ...enterTransition, duration: 0.35, delay: 0.05 + 0.55 * (records.length > 1 ? index / (records.length - 1) : 1) }}>{label}</Point>;
  })}{selected >= 0 && (animate
    // El guion del mes seleccionado ya es diseno (4 4): no se convierte en
    // animacion de guiones. Al cambiar la seleccion reaparece con opacidad.
    ? <m.line key={selected} x1={x(selected)} x2={x(selected)} y1="18" y2={height - 34} stroke="var(--accent)" strokeDasharray="4 4" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.25 }}/>
    : <line x1={x(selected)} x2={x(selected)} y1="18" y2={height - 34} stroke="var(--accent)" strokeDasharray="4 4"/>)}{projection && <path data-series="health-forecast" d={projectedPath} fill="none" stroke="var(--health-forecast)" strokeWidth="2.5" strokeDasharray="5 5" strokeLinejoin="round"/>}{estimates.map(point => {
      if (point.health_score === null) return null;
      const label = `${date(point.date, true)} · Estimated health: ${modelNumber(point.health_score)} / 100 · ${point.h} days from ${date(row.as_of, true)}`;
      return <circle key={point.h} className="health-forecast-point" data-health-forecast-horizon={point.h} cx={xDate(point.date)} cy={y(point.health_score)} r="4" fill="var(--surface)" stroke="var(--health-forecast)" strokeWidth="2.5" tabIndex={0} role="img" aria-label={label}><title>{label}</title></circle>;
    })}{timeline.map((day,index) => index===0 || index===timeline.length-1 || (width > 540 && index===Math.floor(timeline.length/2)) ? <text key={day} x={xDate(day)} y={height - 11} textAnchor={index===0?"start":index===timeline.length-1?"end":"middle"}>{day === today ? "Today" : date(day,true)}</text> : null)}</svg>
    {!records.some(record => record.health_score !== null) && <p className="chart-note">No score was returned for this company. Its available source data is shown below.</p>}
    <p className="chart-note">Monthly observations{records.length ? ` through ${date(records.at(-1)!.as_of, true)}` : " from 2025 are not available"}. Missing ratings stay empty; no daily scores are interpolated.</p></div>;
  // Keep the provider mounted so enabling motion does not detach the observed SVG.
  return <ChartMotion>{content}</ChartMotion>;
}

function HealthScoreGauge({ row }: { row: ModelRecord }) {
  const score = row.excluida ? null : row.health_score;
  return <SegmentedGauge className="health-score-gauge" segments={score === null ? [] : [{ fraction: score / 100, color: "var(--accent)" }]}><div className="health-score-value" data-empty={score === null}><strong className="num">{row.excluida ? "Excluded" : score === null ? "No score" : modelNumber(score)}</strong>{score !== null && <span>/ 100</span>}</div></SegmentedGauge>;
}

export function HealthScorePanel({ records, row, detail = false, chartHeight = 290, action, forecast = false }: { records: ScorePoint[]; row: ModelRecord; detail?: boolean; chartHeight?: number; action?: ReactNode; forecast?: boolean }) {
  const previous = records[records.findIndex(record => record.as_of === row.as_of) - 1];
  const delta = row.health_score != null && previous?.health_score != null ? row.health_score - previous.health_score : null;
  return <section className="card health-overview-panel" id="health" aria-labelledby="health-title">
    <div className="health-overview-main"><div className="card-heading"><div><h2 id="health-title">Health score</h2><p className="small muted mt-1">{detail ? "Published rating at this cutoff" : "How the company’s cash-flow health has changed"}</p></div>{action}</div>
      <div className="health-selected-score"><HealthScoreGauge row={row}/><div className="health-score-context"><span>{date(row.as_of,true)}</span><span>{delta === null ? "No comparable previous score" : `${delta > 0 ? "+" : ""}${modelNumber(delta)} points since ${date(previous.as_of)}`}</span></div></div>
      {!detail && <ScoreChart records={records.filter(record => record.as_of >= "2025-01-01")} row={row} height={chartHeight} forecast={forecast}/>}
    </div>
  </section>;
}
