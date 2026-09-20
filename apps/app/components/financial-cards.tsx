"use client";
import type { ReactNode } from "react";
import { ArrowDownLeft, ArrowUpRight, ClockAlert, Coins } from "lucide-react";
import { date, money } from "../lib/format";
import type { ModelRecord } from "../lib/types";
import { GrowingBar } from "./motion";

const buckets = [
  { key: "1_30", label: "0–30 days", color: "#315b4b" },
  { key: "31_60", label: "31–60 days", color: "#719b86" },
  { key: "61_90", label: "61–90 days", color: "#d1a34c" },
  { key: "91_180", label: "91–180 days", color: "#df8268" },
  { key: "180_mas", label: "Over 180 days", color: "#b65f56" },
] as const;
const knownAmount = (value: number | null | undefined) => value != null && Number.isFinite(value) && value >= 0 ? value : null;
const euros = (value: number | null) => value === null ? "Not available" : money(Math.round(value * 100), "EUR", false, true);
const percentage = (value: number) => new Intl.NumberFormat("en-GB", { style: "percent", maximumFractionDigits: 1 }).format(value);
const knownIndex = (value: number | null | undefined) => value != null && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
export function formatIndex(value: number | null | undefined) {
  const index = knownIndex(value);
  return index === null ? "Not available" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits: 3 }).format(index)} / 1`;
}
const formatFxPoints = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "Not available" : `${new Intl.NumberFormat("en-GB", { maximumFractionDigits: 2 }).format(value)} points`;

export function SegmentedGauge({ segments, className, children }: { segments: { fraction: number; color: string }[]; className: string; children: ReactNode }) {
  let end = 0;
  const ranges = segments.map(segment => ({ ...segment, end: end += segment.fraction }));
  return <div className={className}><svg viewBox="0 0 180 105" aria-hidden="true">{Array.from({ length: 36 }, (_, index) => {
    const angle = Math.PI * (1 - index / 35);
    const color = ranges.find(range => (index + .5) / 36 <= range.end)?.color ?? "var(--line)";
    return <line key={index} x1={90 + 61 * Math.cos(angle)} y1={87 - 61 * Math.sin(angle)} x2={90 + 74 * Math.cos(angle)} y2={87 - 74 * Math.sin(angle)} stroke={color} strokeWidth="5" strokeLinecap="round"/>;
  })}</svg>{children}</div>;
}

export function agingAmounts(payment: ModelRecord["payment"], side: "pago" | "cobro") {
  const total = knownAmount(payment?.[`${side}_vencido_eur`]);
  const amounts = buckets.map(bucket => knownAmount(payment?.[`${side}_vencido_${bucket.key}_eur`]));
  const complete = total !== null && amounts.every(value => value !== null) && Math.abs(amounts.reduce<number>((sum, value) => sum + (value ?? 0), 0) - total) < .05;
  return { total, complete, buckets: buckets.map((bucket, index) => ({ ...bucket, amount: amounts[index], fraction: complete && total > 0 ? amounts[index]! / total : null })) };
}

export function AgingCard({ row, side }: { row: ModelRecord; side: "pago" | "cobro" }) {
  const { total, complete, buckets } = agingAmounts(row.payment, side);
  const missing = row.payment?.[`${side}_n_huecos_eur`];
  const invoices = row.payment?.[`${side}_n_facturas`];
  const unknownDates = row.payment?.n_vencimiento_desconocido;
  const title = side === "cobro" ? "Overdue collections" : "Overdue payments";
  const Icon = side === "cobro" ? ArrowDownLeft : ArrowUpRight;
  return <section className="card financial-card" aria-labelledby={`aging-${side}-title`}>
    <header className="financial-card-heading"><Icon size={17} aria-hidden/><div><h2 id={`aging-${side}-title`}>{title}</h2><p>{side === "cobro" ? "Customers" : "Suppliers"} · {date(row.as_of, true)}</p></div></header>
    <SegmentedGauge className="aging-gauge" segments={buckets.flatMap(bucket => bucket.fraction === null ? [] : [{ fraction: bucket.fraction, color: bucket.color }])}><div className="aging-total"><strong className="num">{euros(total)}</strong><span>Known overdue amount</span></div></SegmentedGauge>
    {total === null || !complete ? <p className="financial-card-note">{total === null ? "No payment amounts available for this month." : "The aging breakdown is incomplete. Shares are unavailable."}</p> : total === 0 && <p className="financial-card-note">{invoices === 0 ? "No dated invoices observed at this cutoff." : "No overdue amount recorded among known invoices."}</p>}
    <dl className="aging-buckets">{buckets.map(bucket => <div key={bucket.key}><dt><i style={{ background: bucket.color }} aria-hidden/>{bucket.label}</dt><dd><strong className="num">{euros(bucket.amount)}</strong><span>{bucket.fraction === null ? "—" : percentage(bucket.fraction)}</span></dd></div>)}</dl>
    {((missing ?? 0) > 0 || (unknownDates ?? 0) > 0) && <footer className="financial-card-note">{missing != null && missing > 0 && <p>{missing} invoice(s) with unknown EUR amounts are excluded.</p>}{unknownDates != null && unknownDates > 0 && <p>{unknownDates} invoice(s) across payments and collections have unknown due dates and cannot be aged.</p>}</footer>}
  </section>;
}

export function DefaultingCard({ row }: { row: ModelRecord }) {
  const index = knownIndex(row.mora_indice);
  const suppliers = knownIndex(row.payment?.mora_pago_robusta), customers = knownIndex(row.payment?.mora_cobro_robusta);
  const hasFxLayer = row.indice_fx !== undefined || row.indice_fx_aplicado !== undefined;
  const fxHeadline = knownIndex(row.indice_fx) ?? (row.indice_es_intervalo ? knownIndex(row.indice_fx_aplicado) : null);
  return <section className="card financial-card defaulting-card" aria-labelledby="defaulting-title">
    <header className="financial-card-heading"><ClockAlert size={17} aria-hidden/><div><h2 id="defaulting-title">Defaulting</h2><p>Payment arrears · {date(row.as_of, true)}</p></div></header>
    <div className="defaulting-summary"><span>Arrears index</span><strong className="num">{formatIndex(index)}</strong>{index === null && <p>Insufficient payment evidence.</p>}</div>
    <dl className="defaulting-bars">{([['Supplier payments', suppliers], ['Customer collections', customers]] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="num">{formatIndex(value)}</dd>{value !== null && <div className="defaulting-track" aria-hidden><GrowingBar width={`${value * 100}%`}/></div>}</div>)}</dl>
    <footer className="financial-card-note"><p>Observed arrears, not default probability.</p>{index !== null && (suppliers === null || customers === null) && <p>{suppliers !== null ? "Supplier evidence only." : customers !== null ? "Customer evidence only." : "Breakdown unavailable."}</p>}</footer>
    {hasFxLayer && <section className="defaulting-fx" aria-labelledby="fx-risk-title">
      <header className="financial-card-heading"><Coins size={17} aria-hidden/><div><h3 id="fx-risk-title">Currency risk</h3><p>Foreign-currency receivables</p></div></header>
      <div className="defaulting-summary"><span>FX risk index</span><strong className="num">{formatIndex(fxHeadline)}</strong>{fxHeadline !== null && <div className="fx-track" aria-hidden><GrowingBar width={`${fxHeadline * 100}%`}/></div>}{fxHeadline === null && <p>FX evidence unavailable; no adjustment.</p>}</div>
      <dl className="defaulting-bars"><div><dt>Applied index</dt><dd className="num">{formatIndex(row.indice_fx_aplicado)}</dd></div><div><dt>Score reduction</dt><dd className="num">{formatFxPoints(row.penalizacion_fx_puntos)}</dd></div></dl>
      <footer className="financial-card-note">{row.indice_es_intervalo && <p>Incomplete FX coverage; minimum compatible penalty applied.</p>}{row.beta_fx != null && <p>Maximum reduction: {percentage(row.beta_fx)} of the score.</p>}</footer>
    </section>}
  </section>;
}
