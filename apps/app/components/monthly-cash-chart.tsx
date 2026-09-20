"use client";
import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { date, money } from "../lib/format";
import type { CashMonth, ModelRecord } from "../lib/types";
import { cashScale } from "./cash-chart";

const monthLabel = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const amount = (value: number | null | undefined, currency: string) => value == null ? "Not available" : money(Math.round(value * 100), currency, false, true);

function CurrencyPicker({ currencies, selected, onSelect }: { currencies: string[]; selected: string; onSelect: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')?.focus();
    const dismiss = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);
  const close = () => { setOpen(false); trigger.current?.focus(); };
  return <div ref={root} className="cash-currency" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }} onKeyDown={event => { if (event.key === "Escape" && open) { event.preventDefault(); event.stopPropagation(); close(); } }}>
    <button ref={trigger} type="button" className="button secondary" aria-label={`Cash currency: ${selected}`} aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen(!open)}>{selected}<ChevronsUpDown size={14} aria-hidden/></button>
    {open && <div className="cash-currency-options" role="dialog" aria-label="Choose cash currency"><p>Original account currency</p>{currencies.map(currency => <button type="button" key={currency} aria-pressed={currency === selected} onClick={() => { onSelect(currency); close(); }}>{currency}{currency === selected && <Check size={15} aria-hidden/>}</button>)}</div>}
  </div>;
}

export function DailyCashPlot({ series, height = 290 }: { series: CashMonth; height?: number }) {
  const [width, setWidth] = useState(1000), [hovered, setHovered] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    if (plot.current) observer.observe(plot.current);
    return () => observer.disconnect();
  }, []);
  const { days, currency } = series;
  const known = days.flatMap(day => [day.income, day.expense, day.balance].filter((value): value is number => value !== null));
  const scale = cashScale(Math.min(0, ...known) * 100, Math.max(0, ...known) * 100);
  const min = scale.min / 100, max = scale.max / 100, ticks = scale.ticks.map(value => value / 100);
  const left = 68, right = width - 12, slot = (right - left) / Math.max(1, days.length);
  const x = (index: number) => left + (index + .5) * slot;
  const y = (value: number) => height - 40 - (value - min) / (max - min) * (height - 70);
  const barWidth = Math.max(1, Math.min(12, (slot - 3) / 2));
  const inspected = hovered === null ? null : days[hovered];
  const totals = inspected ?? { income: series.income, expense: series.expense, balance: series.closing_balance };
  const path = days.map((day, index) => day.balance === null ? "" : `${index && days[index - 1].balance !== null ? "L" : "M"}${x(index)},${y(day.balance)}`).join(" ");
  const zeroBalance = days.some(day => day.balance !== null) && days.every(day => day.balance === null || day.balance === 0);
  function inspect(event: React.PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const index = Math.floor(((event.clientX - bounds.left) / bounds.width * width - left) / slot);
    setHovered(index < 0 || index >= days.length ? null : index);
  }
  return <>
    <div className="model-chart cash-flow-chart"><div ref={plot} className="cash-flow-plot" role="region" aria-label="Daily cash flow. Use left and right arrow keys to inspect a day; Escape returns to month totals." tabIndex={known.length ? 0 : undefined}
      onBlur={() => setHovered(null)} onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) {
          event.preventDefault();
          setHovered(event.key === "Escape" ? null : event.key === "Home" ? 0 : event.key === "End" ? days.length - 1 : Math.max(0, Math.min(days.length - 1, (hovered ?? (event.key === "ArrowRight" ? -1 : days.length)) + (event.key === "ArrowRight" ? 1 : -1))));
        }
      }}>
      {known.length ? <svg style={{ width, height }} viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="daily-chart-title daily-chart-description" onPointerMove={event => { if (event.pointerType === "mouse") inspect(event); }} onPointerDown={inspect} onPointerLeave={() => setHovered(null)}>
        <title id="daily-chart-title">Daily income, expenses and reconstructed cash</title>
        <desc id="daily-chart-description">Two bars per day show recorded inflows and outflows. The line shows reconstructed daily closing cash. All three series use the same {currency} axis, only for accounts in that currency. Missing amounts remain gaps.</desc>
        {hovered !== null && <rect x={left + hovered * slot} y="24" width={slot} height={height - 58} rx="3" fill="var(--accent-soft)"/>}
        {ticks.map(value => <g key={value}><line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke={value === 0 ? "var(--subtle)" : "var(--line)"}/><text x={left - 10} y={y(value) + 4} textAnchor="end">{money(Math.round(value * 100), currency, true)}</text></g>)}
        {days.map((day, index) => <g key={day.date}>{(["income", "expense"] as const).map((name, offset) => day[name] === null ? null : <rect key={name} data-series={name} x={x(index) + (offset ? 1 : -barWidth - 1)} y={y(day[name])} width={barWidth} height={Math.max(0, y(0) - y(day[name]))} rx="1.5" fill={`var(--cash-${name})`}><title>{`${date(day.date, true)} · ${name === "income" ? "Income" : "Expenses"}: ${amount(day[name], currency)}`}</title></rect>)}</g>)}
        <path data-series="balance" d={path} fill="none" stroke="var(--cash-balance)" strokeWidth="2.5" strokeLinejoin="round"/>
        {days.map((day, index) => day.balance === null ? null : <circle key={day.date} cx={x(index)} cy={y(day.balance)} r={index === hovered ? 4 : 1.5} fill="var(--surface)" stroke="var(--cash-balance)" strokeWidth="2"><title>{`${date(day.date, true)} · Reconstructed cash: ${amount(day.balance, currency)}`}</title></circle>)}
        {zeroBalance && <text className="cash-zero-label" x={left + 4} y={y(0) - 12}>Reconstructed cash · {amount(0, currency)}</text>}
        {days.map((day, index) => index === 0 || index === days.length - 1 || (index % 7 === 0 && index < days.length - 4) ? <text key={day.date} x={x(index)} y={height - 11} textAnchor={index === 0 ? "start" : index === days.length - 1 ? "end" : "middle"}>{Number(day.date.slice(-2))}</text> : null)}
      </svg> : <p className="monthly-empty">Daily amounts are unavailable for these accounts this month.</p>}
    </div><p className="chart-note">Original {currency} accounts. Cash reconstructed from {date(series.anchor_date, true)}, not a bank balance observed each day.{days.some(day => day.unknown_movements > 0) && " Some movements have unknown amounts; incomplete days remain gaps."}</p></div>
    <div className="cash-inspected-period" aria-live="polite">{inspected ? date(inspected.date, true) : `Month totals · ${monthLabel.format(new Date(`${days[0].date}T00:00:00Z`))}`}</div>
    <dl className="checkpoints cash-flow-totals" aria-label={inspected ? `Cash flow for ${date(inspected.date, true)}` : "Selected month cash totals"}>{([["income", "Income", totals.income], ["expense", "Expenses", totals.expense], ["balance", inspected ? "Closing cash" : "Month-end cash", totals.balance]] as const).map(([name, label, value]) => <div key={name}><dt><i className={`cash-flow-swatch ${name}`}/>{label}</dt><dd className="num">{amount(value, currency)}</dd></div>)}</dl>
  </>;
}

export function MonthlyCashChart({ row, chartHeight = 290 }: { row: ModelRecord; chartHeight?: number }) {
  const [currency, setCurrency] = useState("EUR");
  const currencies = row.daily_cash ?? [];
  const series = currencies.find(value => value.currency === currency) ?? currencies[0];
  return <section className="card monthly-cash" id="cash" aria-labelledby="monthly-cash-title">
    <div className="card-heading"><div><h2 id="monthly-cash-title">Cash flow</h2><p className="small muted mt-1">Daily movements · {monthLabel.format(new Date(`${row.as_of}T00:00:00Z`))}</p></div>{series && <CurrencyPicker currencies={currencies.map(value => value.currency)} selected={series.currency} onSelect={setCurrency}/>}</div>
    <div className="cash-flow-legend" aria-label="Chart legend"><span><i className="cash-flow-swatch income"/>Income</span><span><i className="cash-flow-swatch expense"/>Expenses</span><span><i className="cash-flow-swatch balance"/>Reconstructed cash</span></div>
    {series ? <DailyCashPlot key={`${row.as_of}:${series.currency}`} series={series} height={chartHeight}/> : <p className="monthly-empty">{row.daily_cash == null ? "Daily cash movements have not been imported for this month." : "No cash accounts recorded for this company this month."}</p>}
  </section>;
}
