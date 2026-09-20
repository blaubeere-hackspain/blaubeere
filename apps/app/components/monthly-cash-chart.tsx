"use client";
import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { m } from "framer-motion";
import { date, money } from "../lib/format";
import type { CashMonth, CashProjection, ModelRecord } from "../lib/types";
import { cashScale } from "./cash-chart";
import { ChartMotion, enterTransition, pressSpring, useChartMotion } from "./motion";

const monthLabel = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const amount = (value: number | null | undefined, currency: string) => value == null ? "Not available" : money(Math.round(value * 100), currency, false, true);

export function cashForecastAvailable(row: ModelRecord) {
  return row.cash_projection?.as_of === row.as_of && row.cash_projection.currency === "EUR" &&
    row.cash_projection.horizons.some(point => point.saldo_proyectado_eur !== null) &&
    Boolean(row.daily_cash?.some(series => series.currency === "EUR" && series.days.length));
}

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

// El punto inspeccionado equivale al antiguo r = 4 manteniendo r base 1.5:
// el crecimiento se hace con scale, nunca animando el atributo r.
const inspectedScale = 4 / 1.5;

export function DailyCashPlot({ series, height = 290, projection }: { series: CashMonth; height?: number; projection?: CashProjection }) {
  const animate = useChartMotion();
  const [width, setWidth] = useState(1000), [hovered, setHovered] = useState<number | null>(null);
  const plot = useRef<HTMLDivElement>(null), lastHovered = useRef<number | null>(null);
  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    if (plot.current) observer.observe(plot.current);
    return () => observer.disconnect();
  }, []);
  const { days, currency } = series;
  // Forecasts are company-wide EUR amounts, never native USD/GBP amounts.
  const forecast = projection?.currency === currency && projection.as_of === days.at(-1)?.date ? projection : undefined;
  const horizons = forecast?.horizons ?? [];
  const points = [...days, ...horizons.map(point => ({ date: point.date, income: point.entrada_esperada_eur, expense: point.salida_esperada_eur === null ? null : -point.salida_esperada_eur, balance: point.saldo_proyectado_eur }))];
  const offsets = [...days.map((_, index) => index), ...horizons.map(point => days.length - 1 + point.h)];
  const opening = horizons[0]?.saldo_corte_eur ?? null;
  const known = [...days.flatMap(day => [day.income, day.expense, day.balance]), ...horizons.map(point => point.saldo_proyectado_eur), opening].filter((value): value is number => value !== null);
  const scale = cashScale(Math.min(0, ...known) * 100, Math.max(0, ...known) * 100);
  const min = scale.min / 100, max = scale.max / 100, ticks = scale.ticks.map(value => value / 100);
  const left = 68, right = width - 12, slot = (right - left) / Math.max(1, days.length + (horizons.at(-1)?.h ?? 0));
  const xAt = (offset: number) => left + (offset + .5) * slot;
  const x = (index: number) => xAt(offsets[index]);
  const y = (value: number) => height - 40 - (value - min) / (max - min) * (height - 70);
  const barWidth = Math.max(1, Math.min(12, (slot - 3) / 2));
  if (hovered !== null) lastHovered.current = hovered;
  // El resaltado recuerda el ultimo dia inspeccionado para deslizarse al
  // siguiente en vez de desmontarse y volver a aparecer en cada cambio.
  const highlight = hovered ?? lastHovered.current;
  const inspected = hovered === null ? null : points[hovered];
  const inspectedForecast = hovered === null ? undefined : horizons[hovered - days.length];
  const totals = inspected ?? { income: series.income, expense: series.expense, balance: series.closing_balance };
  const path = days.map((day, index) => day.balance === null ? "" : `${index && days[index - 1].balance !== null ? "L" : "M"}${x(index)},${y(day.balance)}`).join(" ");
  const forecastPoints = [{ offset: days.length - 1, balance: opening }, ...horizons.map(point => ({ offset: days.length - 1 + point.h, balance: point.saldo_proyectado_eur }))];
  const forecastPath = forecastPoints.map((point, index) => point.balance === null ? "" : `${index && forecastPoints[index - 1].balance !== null ? "L" : "M"}${xAt(point.offset)},${y(point.balance)}`).join(" ");
  const zeroBalance = days.some(day => day.balance !== null) && days.every(day => day.balance === null || day.balance === 0);
  function inspect(event: React.PointerEvent<SVGSVGElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    const pointer = (event.clientX - bounds.left) / bounds.width * width;
    setHovered(pointer < left || pointer > right ? null : offsets.reduce((nearest, _, index) => Math.abs(x(index) - pointer) < Math.abs(x(nearest) - pointer) ? index : nearest, 0));
  }
  // Animaciones de entrada solo en cliente tras el montaje: sin JS el markup
  // estatico ya es el estado final (I1), y fuera de ChartMotion los elementos
  // son los planos de siempre (LazyMotion strict exigiria un proveedor).
  const content = <>
    <div className="model-chart cash-flow-chart"><div ref={plot} className="cash-flow-plot" role="region" aria-label="Cash flow. Use left and right arrow keys to inspect recorded days and forecast points; Escape returns to month totals." tabIndex={known.length ? 0 : undefined}
      onBlur={() => setHovered(null)} onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) {
          event.preventDefault();
          setHovered(event.key === "Escape" ? null : event.key === "Home" ? 0 : event.key === "End" ? points.length - 1 : Math.max(0, Math.min(points.length - 1, (hovered ?? (event.key === "ArrowRight" ? -1 : points.length)) + (event.key === "ArrowRight" ? 1 : -1))));
        }
      }}>
      {known.length ? <svg style={{ width, height }} viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="daily-chart-title daily-chart-description" onPointerMove={event => { if (event.pointerType === "mouse") inspect(event); }} onPointerDown={inspect} onPointerLeave={() => setHovered(null)}>
        <title id="daily-chart-title">Daily income, expenses and reconstructed cash</title>
        <desc id="daily-chart-description">Two bars per day show recorded inflows and outflows. The line shows reconstructed daily closing cash. All series use the same {currency} axis. Recorded movements cover accounts in that currency.{forecast && " Three dashed forecast points show company-wide EUR cash after expected invoice receipts and payments, at 30, 60 and 90 days. No daily forecasts are inferred."} Missing amounts remain gaps.</desc>
        {forecast && <><rect x={xAt(days.length - .5)} y="24" width={right - xAt(days.length - .5)} height={height - 58} fill="var(--accent-soft)" opacity=".35"/><line x1={xAt(days.length - .5)} x2={xAt(days.length - .5)} y1="24" y2={height - 34} stroke="var(--subtle)" strokeDasharray="3 4"/><text x={xAt(days.length - .5) + 8} y="18">Invoice forecast</text></>}
        {hovered !== null && !animate && <rect x={x(hovered) - slot / 2} y="24" width={slot} height={height - 58} rx="3" fill="var(--accent-soft)"/>}
        {animate && highlight !== null && <m.rect y="24" width={slot} height={height - 58} rx="3" fill="var(--accent-soft)" initial={{ opacity: 0 }} animate={{ opacity: hovered === null ? 0 : 1, x: x(highlight) - slot / 2 }} transition={hovered === null ? { duration: 0.15 } : pressSpring}/>}
        {ticks.map(value => <g key={value}><line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke={value === 0 ? "var(--subtle)" : "var(--line)"}/><text x={left - 10} y={y(value) + 4} textAnchor="end">{money(Math.round(value * 100), currency, true)}</text></g>)}
        {days.map((day, index) => <g key={day.date}>{(["income", "expense"] as const).map((name, offset) => {
          if (day[name] === null) return null;
          const barX = x(index) + (offset ? 1 : -barWidth - 1), barY = y(day[name]), barHeight = Math.max(0, y(0) - barY);
          const label = <title>{`${date(day.date, true)} · ${name === "income" ? "Income" : "Expenses"}: ${amount(day[name], currency)}`}</title>;
          if (!animate) return <rect key={name} data-series={name} x={barX} y={barY} width={barWidth} height={barHeight} rx="1.5" fill={`var(--cash-${name})`}>{label}</rect>;
          // Crecen desde la linea del cero con scaleY (origen en y(0)); los
          // atributos x/y/height quedan literales y el escalonado va de
          // izquierda a derecha (el dia 0 arranca enseguida).
          const Bar = m.rect;
          return <Bar key={name} data-series={name} x={barX} y={barY} width={barWidth} height={barHeight} rx="1.5" fill={`var(--cash-${name})`} style={{ transformBox: "fill-box", originX: 0.5, originY: barY <= y(0) ? 1 : 0 }} initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ ...enterTransition, delay: index * 0.015 }}>{label}</Bar>;
        })}</g>)}
        {animate ? <m.path data-series="balance" d={path} fill="none" stroke="var(--cash-balance)" strokeWidth="2.5" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={enterTransition}/> : <path data-series="balance" d={path} fill="none" stroke="var(--cash-balance)" strokeWidth="2.5" strokeLinejoin="round"/>}
        {days.map((day, index) => {
          if (day.balance === null) return null;
          const isInspected = index === hovered;
          const label = <title>{`${date(day.date, true)} · Reconstructed cash: ${amount(day.balance, currency)}`}</title>;
          const shared = { cx: x(index), cy: y(day.balance), r: 1.5, fill: "var(--surface)", stroke: "var(--cash-balance)", strokeWidth: 2, vectorEffect: "non-scaling-stroke" as const };
          if (!animate) return <circle key={day.date} {...shared} style={{ transformBox: "fill-box", transformOrigin: "center", transform: isInspected ? `scale(${inspectedScale})` : undefined }}>{label}</circle>;
          // El punto aparece cuando el trazo pasa por su posicion y crece con
          // scale (r base constante, el trazo no se engorda al escalar).
          const Point = m.circle;
          return <Point key={day.date} {...shared} style={{ transformBox: "fill-box", transformOrigin: "center" }} initial={{ scale: 0, opacity: 0 }} animate={{ scale: isInspected ? inspectedScale : 1, opacity: 1 }} transition={isInspected ? pressSpring : { ...enterTransition, duration: 0.35, delay: 0.05 + 0.55 * (days.length > 1 ? index / (days.length - 1) : 1) }}>{label}</Point>;
        })}
        {zeroBalance && <text className="cash-zero-label" x={left + 4} y={y(0) - 12}>Reconstructed cash · {amount(0, currency)}</text>}
        {forecast && <path data-series="forecast" d={forecastPath} fill="none" stroke="var(--cash-forecast)" strokeWidth="2.5" strokeDasharray="5 5" strokeLinejoin="round"/>}
        {horizons.map((point, index) => <g key={point.h}>{point.saldo_proyectado_eur !== null && <circle data-forecast-horizon={point.h} cx={x(days.length + index)} cy={y(point.saldo_proyectado_eur)} r={hovered === days.length + index ? 6 : 4} fill="var(--surface)" stroke="var(--cash-forecast)" strokeWidth="2.5"><title>{`${point.h}-day invoice cash forecast · ${date(point.date, true)}: ${amount(point.saldo_proyectado_eur, currency)}`}</title></circle>}<text x={x(days.length + index)} y={height - 11} textAnchor={index === horizons.length - 1 ? "end" : "middle"}>{date(point.date)}</text></g>)}
        {days.map((day, index) => index === 0 || (!forecast && (index === days.length - 1 || (index % 7 === 0 && index < days.length - 4))) ? <text key={day.date} x={x(index)} y={height - 11} textAnchor={index === 0 ? "start" : index === days.length - 1 ? "end" : "middle"}>{forecast ? date(day.date) : Number(day.date.slice(-2))}</text> : null)}
      </svg> : <p className="monthly-empty">Daily amounts are unavailable for these accounts this month.</p>}
    </div><p className="chart-note">Original {currency} accounts. Cash reconstructed from {date(series.anchor_date, true)}, not a bank balance observed each day.{days.some(day => day.unknown_movements > 0) && " Some movements have unknown amounts; incomplete days remain gaps."}</p></div>
    {forecast && <p className="cash-forecast-note">Company-wide EUR cash based on open invoices at {date(forecast.as_of, true)}. Excludes payroll, taxes and other cash movements. Estimates, not guaranteed balances.</p>}
    <div className="cash-inspected-period" aria-live="polite">{inspectedForecast ? `${inspectedForecast.h}-day forecast · through ${date(inspectedForecast.date, true)} · cumulative` : inspected ? date(inspected.date, true) : `Month totals · ${monthLabel.format(new Date(`${days[0].date}T00:00:00Z`))}`}</div>
    <dl className="checkpoints cash-flow-totals" aria-label={inspectedForecast ? `${inspectedForecast.h}-day forecast totals` : inspected ? `Cash flow for ${date(inspected.date, true)}` : "Selected month cash totals"}>{([["income", "Income", totals.income], ["expense", "Expenses", totals.expense], ["balance", inspectedForecast ? "Projected cash" : inspected ? "Closing cash" : "Month-end cash", totals.balance]] as const).map(([name, label, value]) => <div key={name}><dt><i className={`cash-flow-swatch ${name}`}/>{label}</dt><dd className="num">{amount(value, currency)}</dd></div>)}</dl>
  </>;
  // Keep the provider mounted so enabling motion does not detach the observed plot.
  return <ChartMotion>{content}</ChartMotion>;
}

export function MonthlyCashChart({ row, chartHeight = 290, forecast = false, onCurrencyChange }: { row: ModelRecord; chartHeight?: number; forecast?: boolean; onCurrencyChange?: () => void }) {
  const [currency, setCurrency] = useState("EUR");
  const currencies = row.daily_cash ?? [];
  const projection = forecast && cashForecastAvailable(row) ? row.cash_projection! : undefined;
  const series = currencies.find(value => value.currency === (projection ? "EUR" : currency)) ?? currencies[0];
  return <section className="card monthly-cash" id="cash" aria-labelledby="monthly-cash-title">
    <div className="card-heading"><div><h2 id="monthly-cash-title">Cash flow</h2><p className="small muted mt-1">Daily movements · {monthLabel.format(new Date(`${row.as_of}T00:00:00Z`))}</p></div>{series && <CurrencyPicker currencies={currencies.map(value => value.currency)} selected={series.currency} onSelect={value => { setCurrency(value); onCurrencyChange?.(); }}/>}</div>
    <div className="cash-flow-legend" aria-label="Chart legend"><span><i className="cash-flow-swatch income"/>Income</span><span><i className="cash-flow-swatch expense"/>Expenses</span><span><i className="cash-flow-swatch balance"/>Reconstructed cash</span>{projection && <span><i className="cash-flow-swatch forecast"/>Invoice forecast · EUR</span>}</div>
    {series ? <DailyCashPlot key={`${row.as_of}:${series.currency}:${Boolean(projection)}`} series={series} height={chartHeight} projection={projection}/> : <p className="monthly-empty">{row.daily_cash == null ? "Daily cash movements have not been imported for this month." : "No cash accounts recorded for this company this month."}</p>}
  </section>;
}
