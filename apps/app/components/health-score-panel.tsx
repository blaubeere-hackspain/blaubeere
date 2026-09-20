"use client";
import "@fontsource-variable/inter";
import { useEffect, useRef, useState } from "react";
import { date, monthlyTimeline } from "../lib/format";
import type { ModelRecord } from "../lib/types";
import { SegmentedGauge } from "./financial-cards";

type ScorePoint = Pick<ModelRecord, "as_of" | "health_score">;
export const modelNumber = (value: number | null | undefined, suffix = "", maximumFractionDigits = 1) => value == null ? "Not available" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits }).format(value)}${suffix}`;

function ScoreChart({ records, row, height }: { records: ScorePoint[]; row: ModelRecord; height: number }) {
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
  const y = (score: number) => height - 40 - score / 100 * (height - 70);
  const path = records.map((row, index) => row.health_score === null ? "" : `${index && records[index - 1].health_score !== null ? "L" : "M"}${x(index)},${y(row.health_score)}`).join(" ");
  const selected = records.findIndex(record => record.as_of === row.as_of);
  return <div className="model-chart health-history-chart"><svg ref={svg} style={{ height }} viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="model-chart-title model-chart-description"><title id="model-chart-title">Monthly health score history</title><desc id="model-chart-description">Published model ratings from 0 to 100. Gaps mean no score was returned.</desc>{[0,25,50,75,100].map(value => <g key={value}><line x1="42" x2={right} y1={y(value)} y2={y(value)} stroke="var(--line)"/><text x="30" y={y(value)+4} textAnchor="end">{value}</text></g>)}<path d={path} fill="none" stroke="var(--accent)" strokeWidth="2.5"/>{records.map((record,index) => record.health_score === null ? null : <circle key={record.as_of} cx={x(index)} cy={y(record.health_score)} r={record.as_of === row.as_of ? 6 : width < 540 ? 3 : 4} fill={record.as_of === row.as_of ? "var(--accent)" : "var(--surface)"} stroke="var(--accent)" strokeWidth="2"><title>{`${date(record.as_of,true)}: ${modelNumber(record.health_score)} / 100`}</title></circle>)}{selected >= 0 && <line x1={x(selected)} x2={x(selected)} y1="18" y2={height - 34} stroke="var(--accent)" strokeDasharray="4 4"/>}{timeline.map((day,index) => index===0 || index===timeline.length-1 || (width > 540 && index===Math.floor(timeline.length/2)) ? <text key={day} x={x(index)} y={height - 11} textAnchor={index===0?"start":index===timeline.length-1?"end":"middle"}>{timeline.length > records.length && index===timeline.length-1 ? "Today" : date(day,true)}</text> : null)}</svg>
    {!records.some(record => record.health_score !== null) && <p className="chart-note">No score was returned for this company. Its available source data is shown below.</p>}
    <p className="chart-note">Monthly observations{records.length ? ` through ${date(records.at(-1)!.as_of, true)}` : " from 2025 are not available"}. Missing ratings stay empty; no daily scores are interpolated.</p></div>;
}

function HealthScoreGauge({ row }: { row: ModelRecord }) {
  const score = row.excluida ? null : row.health_score;
  return <SegmentedGauge className="health-score-gauge" segments={score === null ? [] : [{ fraction: score / 100, color: "var(--accent)" }]}><div className="health-score-value" data-empty={score === null}><strong className="num">{row.excluida ? "Excluded" : score === null ? "No score" : modelNumber(score)}</strong>{score !== null && <span>/ 100</span>}</div></SegmentedGauge>;
}

export function HealthScorePanel({ records, row, detail = false, chartHeight = 290 }: { records: ScorePoint[]; row: ModelRecord; detail?: boolean; chartHeight?: number }) {
  const previous = records[records.findIndex(record => record.as_of === row.as_of) - 1];
  const delta = row.health_score != null && previous?.health_score != null ? row.health_score - previous.health_score : null;
  return <section className="card health-overview-panel" id="health" aria-labelledby="health-title">
    <div className="health-overview-main"><div className="card-heading"><div><h2 id="health-title">Health score</h2><p className="small muted mt-1">{detail ? "Published rating at this cutoff" : "How the company’s cash-flow health has changed"}</p></div></div>
      <div className="health-selected-score"><HealthScoreGauge row={row}/><div className="health-score-context"><span>{date(row.as_of,true)}</span><span>{delta === null ? "No comparable previous score" : `${delta > 0 ? "+" : ""}${modelNumber(delta)} points since ${date(previous.as_of)}`}</span></div></div>
      {!detail && <ScoreChart records={records.filter(record => record.as_of >= "2025-01-01")} row={row} height={chartHeight}/>}
    </div>
  </section>;
}
