"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Check, FlaskConical, X } from "lucide-react";
import { api } from "../lib/api";
import { addDays, cents, date, money } from "../lib/format";
import type { Company, Comparison, Goal, Plan } from "../lib/types";

const amountPattern = "[0-9]+(\\.[0-9]{1,2})?";
function Amount({ label, name, value, hint }: { label: string; name: string; value: string; hint?: string }) {
  const integer = name === "collections" || name === "collectionTiming";
  return <label className="field">{label}<input name={name} defaultValue={value} inputMode={integer ? "numeric" : "decimal"} pattern={integer ? "[0-9]+" : amountPattern} required/>{hint && <small>{hint}</small>}</label>;
}
export function Planner({ company, open, onClose, onCompare, onPreview }: { company: Company; open: boolean; onClose: () => void; onCompare: (c: Comparison) => void; onPreview: (p?: Plan) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [metric, setMetric] = useState("min_cash");
  const [target, setTarget] = useState(String(company.buffer_cents / 100));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const resultsRef = useRef<HTMLElement>(null);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); else if (!open) dialog.current?.close(); }, [open]);
  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      const goal: Goal = { metric, target_cents: cents(form.get("target"), "Target"), deadline: String(form.get("deadline")), cash_floor_cents: cents(form.get("floor"), "Cash floor"), max_collection_days: Number(form.get("collections")), max_spend_reduction_pct: Number(form.get("spend")), max_funding_cents: cents(form.get("funding"), "Funding"), max_growth_pct: metric === "monthly_revenue" ? Number(form.get("growth")) : 0, business: metric === "monthly_revenue" ? { monthly_revenue_cents: cents(form.get("revenue"), "Starting revenue"), gross_margin_pct: Number(form.get("margin")), collection_days: Number(form.get("collectionTiming")) } : null };
      const result = await api<Comparison>(`/companies/${encodeURIComponent(company.id)}/plans`, { method: "POST", body: JSON.stringify(goal) });
      setComparison(result); onCompare(result);
      requestAnimationFrame(() => resultsRef.current?.focus());
    } catch (error) { setError(error instanceof Error ? error.message : "Could not calculate the plans. Please retry."); }
    finally { setBusy(false); }
  }
  const format = (value: number) => money(value, company.currency);
  return <dialog ref={dialog} className="planning-dialog" aria-labelledby="planner-title" onClose={onClose} onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="panel-head"><div><span className="eyebrow">Planning workspace</span><h2 id="planner-title">A goal. A few ways forward.</h2></div><button className="icon-button" aria-label="Close planning panel" onClick={onClose}><X size={20}/></button></div>
    <div className="panel-body"><p className="muted mb-6">Explore conditional changes for {company.name}. Your observed health and source records stay unchanged.</p>
      <form onSubmit={compare} onChange={() => setComparison(null)} aria-busy={busy}>
        <fieldset><legend><span className="step-number">01</span>Define your goal</legend><div className="form-grid">
          <label className="field">Metric<select value={metric} onChange={e => { setMetric(e.target.value); setTarget(e.target.value === "monthly_revenue" ? "" : String(company.buffer_cents / 100)); }}><option value="min_cash">Minimum cash throughout</option><option value="ending_cash">Cash at the deadline</option><option value="monthly_revenue">Monthly revenue</option><option disabled>Retention · cohort inputs needed</option><option disabled>Margin · P&amp;L history needed</option></select></label>
          <label className="field">Target ({company.currency})<input name="target" inputMode="decimal" pattern={amountPattern} value={target} onChange={e => setTarget(e.target.value)} required/></label>
          <label className="field">Deadline<input name="deadline" type="date" min={addDays(company.assessment_date, 7)} max={addDays(company.assessment_date, 180)} defaultValue={addDays(company.assessment_date, 90)} required/></label>
          <Amount label={`Cash floor (${company.currency})`} name="floor" value={String(company.buffer_cents / 100)} hint="Must hold every day, including the assessment date."/>
        </div><p className="field-note">{metric === "min_cash" ? "Definition: the lowest projected daily closing balance through the deadline." : metric === "ending_cash" ? "Definition: the projected closing cash balance on your deadline." : "Definition: monthly revenue in the last completed 30-day period. Starting revenue is entered by the finance team, not inferred from bank receipts."}</p></fieldset>
        {metric === "monthly_revenue" && <fieldset><legend>Business assumptions · entered by you</legend><div className="form-grid"><Amount label={`Starting monthly revenue (${company.currency})`} name="revenue" value=""/><Amount label="Gross margin (%)" name="margin" value=""/><Amount label="Collection delay (days)" name="collectionTiming" value=""/><Amount label="Maximum monthly growth (%)" name="growth" value="10"/></div><p className="field-note">Incremental costs are paid each 30-day period; incremental receipts arrive after the stated delay. Growth compounds monthly; no churn relationship is assumed.</p></fieldset>}
        <fieldset><legend><span className="step-number">02</span>Set the limits you could work within</legend><div className="form-grid">
          <Amount label="Collect earlier · maximum days" name="collections" value="14" hint="0–45 days, subject to customer agreement."/>
          <Amount label="Discretionary spending cut · maximum %" name="spend" value="10" hint="0–30%. Payroll, taxes and debt stay unchanged."/>
          <Amount label={`New funding · maximum ${company.currency}`} name="funding" value="2500000" hint="Set to 0 for no new debt. Uncommitted; assumed day one, 8% annual interest, principal after the horizon."/>
        </div></fieldset>
        {error && <p className="error mb-4" role="alert">{error}</p>}
        <div className="planner-submit"><p className="small muted"><FlaskConical size={16} aria-hidden/>Two candidate combinations. Explicit assumptions.</p><button className="button" disabled={busy}>{busy ? "Comparing plans…" : "Compare plans"}<ArrowRight size={16} aria-hidden/></button></div>
      </form>
      {comparison && <section className="comparison" ref={resultsRef} tabIndex={-1} aria-labelledby="comparison-title"><div className="section-heading"><div><span className="eyebrow">Your options, side by side</span><h2 id="comparison-title">Baseline + two possible plans</h2></div></div><p className={`comparison-summary ${comparison.any_qualifies ? "good" : "warn"}`}>{comparison.explanation}</p>
        <div className="plan-grid"><article className="plan-card baseline-card"><span className="badge">Reference</span><h3>Baseline</h3><p className="muted small">Continue without changes</p><span className="eyebrow">Target result</span><strong className="plan-number num">{format(comparison.baseline_value_cents)}</strong><dl><div><dt>Minimum cash</dt><dd>{format(comparison.baseline.minimum.cash_cents)}</dd></div><div><dt>Funding gap</dt><dd>{format(comparison.baseline.funding_needed_cents)}</dd></div></dl><button className="button secondary" onClick={() => { onPreview(); onClose(); }}>View baseline</button></article>
          {comparison.plans.map(plan => <article key={plan.id} className={`plan-card ${plan.qualifies ? "qualifies" : ""}`}><span className={`badge ${plan.qualifies ? "good" : "warn"}`}>{plan.qualifies ? <><Check size={13} aria-hidden/>Meets all conditions</> : "Conditions unmet"}</span><h3>{plan.title}</h3><p className="muted small">Receipts {plan.changes.collection_days}d earlier · spend −{plan.changes.spend_reduction_pct}%{plan.changes.funding_cents > 0 && ` · ${format(plan.changes.funding_cents)} funding`}{plan.changes.growth_pct > 0 && ` · ${plan.changes.growth_pct}% monthly growth`}</p><span className="eyebrow">Target result</span><strong className="plan-number num">{format(plan.value_cents)}</strong><dl><div><dt>Minimum cash</dt><dd>{format(plan.forecast.minimum.cash_cents)}</dd></div><div><dt>On</dt><dd>{date(plan.forecast.minimum.date)}</dd></div><div><dt>Target gap</dt><dd>{format(plan.remaining_target_cents)}</dd></div><div><dt>Cash floor gap</dt><dd>{format(plan.remaining_cash_cents)}</dd></div></dl><div className="constraint-list"><span>{plan.target_met ? "✓" : "×"} Target reached</span><span>{plan.cash_floor_met ? "✓" : "×"} Cash floor throughout</span></div><p className="small muted">{plan.trade_off}</p><button className={`button ${plan.qualifies ? "" : "secondary"}`} onClick={() => { onPreview(plan); onClose(); }}>View on cash chart<ArrowRight size={14} aria-hidden/></button></article>)}
        </div><details className="assumption-details"><summary>Calculation assumptions</summary><ul>{comparison.assumptions.map(item => <li key={item}>{item}</li>)}</ul></details>
      </section>}
    </div>
  </dialog>;
}
