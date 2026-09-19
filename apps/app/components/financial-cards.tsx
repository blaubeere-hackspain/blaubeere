import type { ReactNode } from "react";
import { ArrowDownLeft, ArrowUpRight, Landmark } from "lucide-react";
import { date, money } from "../lib/format";
import type { ModelRecord } from "../lib/types";

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
    <footer className="financial-card-note"><p>Share of known overdue euros. Includes invoices due on the assessment date.</p>{missing != null && missing > 0 && <p>{missing} invoice(s) with unknown EUR amounts are excluded.</p>}{unknownDates != null && unknownDates > 0 && <p>{unknownDates} invoice(s) across payments and collections have unknown due dates and cannot be aged.</p>}</footer>
  </section>;
}

export function DebtServiceCard({ row }: { row: ModelRecord }) {
  const expected = knownAmount(row.debt?.servicio_esperado_eur), observed = knownAmount(row.debt?.servicio_observado_eur), shortfall = knownAmount(row.debt?.deficit_servicio_eur);
  const comparable = expected !== null && observed !== null;
  const maximum = Math.max(expected ?? 0, observed ?? 0);
  return <section className="card financial-card debt-service-card" aria-labelledby="debt-service-title">
    <header className="financial-card-heading"><Landmark size={17} aria-hidden/><div><h2 id="debt-service-title">Debt service</h2><p>Monthly comparison · {date(row.as_of, true)}</p></div></header>
    <div className="debt-service-summary"><span>Shortfall against estimate</span><strong className="num">{euros(shortfall)}</strong><p>{shortfall === null ? "More evidence is needed to compare this month." : shortfall > 0 ? "Observed payments are below the historical estimate." : "No shortfall against the historical estimate."}</p></div>
    <dl className="debt-service-bars">{[["Expected · historical median", expected], ["Observed this month", observed]].map(([label, value], index) => <div key={String(label)}><dt>{label}</dt><dd className="num">{euros(value as number | null)}</dd>{comparable && <div className="debt-service-track" aria-hidden><i data-series={index ? "observed" : "expected"} style={{ width: `${maximum > 0 ? Number(value) / maximum * 100 : 0}%` }}/></div>}</div>)}</dl>
    <footer className="financial-card-note"><p>The estimate is the median of at least three earlier months with debt payments, not a contractual amount due.</p>{expected === null && <p>No historical estimate is available at this cutoff.</p>}{observed === null && <p>Debt payments this month are unknown, not zero.</p>}</footer>
  </section>;
}
