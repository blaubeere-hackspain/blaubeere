import { date } from "../lib/format";
import { hasProxyEstimate, type ProxyCompany } from "../lib/types";

const percent = (value: number) => `${(100 * value).toFixed(1)}%`;
const reason = (value: string) => value.replaceAll("_", " ");

export function ProxyAssessment({ company, cashReason }: { company: ProxyCompany; cashReason: string }) {
  const p = company.predictive;
  const available = hasProxyEstimate(p);
  const v = p.validation;
  return <div className="proxy-assessment" aria-label="Predictive assessment">
    <section className="card evidence-card" aria-labelledby="proxy-title">
      <div className="card-heading"><div><span className="eyebrow">Synthetic · reconstructed · retrospective</span><h2 id="proxy-title">Operating deficit proxy</h2></div><span className="badge warn">Not calibrated</span></div>
      <div className="p-4">
        <p className="small muted">Estimated probability</p>
        <p className="stat-value num" role="status" aria-label="Estimated proxy probability">{available ? percent(p.probability_estimate!) : "Unavailable"}</p>
        {!available && <div className="mt-2"><strong>Why unavailable</strong><ul>{(p.unavailable_reasons.length ? p.unavailable_reasons : ["Accepted, eligible evidence with a valid probability is required."]).map(item => <li key={item}>{reason(item)}</li>)}</ul></div>}
        <p className="mt-2"><strong>Fixed horizon: 3 calendar months</strong> · {date(p.horizon_start, true)} – {date(p.horizon_end, true)}</p>
        <p className="small muted">Source as of {date(p.as_of, true)} · Model {p.model_version}</p>
        <p className="mt-2">{p.definition}</p>
        <p className="mt-2"><strong>This is not a calibrated probability, a default prediction, or a health score.</strong> It does not estimate a cash balance or a cash shortfall amount.</p>
        <p className="small muted mt-2">{cashReason}</p>
        <p className="small muted mt-2">{p.currency_basis}</p>
      </div>
    </section>
    <section className="card evidence-card mt-4" id="evidence" aria-labelledby="proxy-evidence-title">
      <div className="card-heading"><div><h2 id="proxy-evidence-title">Coverage and uncertainty</h2><p className="small muted mt-1">Missing evidence is a gap, not poor health.</p></div><span className="badge">{p.eligible ? "Eligible inputs" : "Ineligible inputs"}</span></div>
      <div className="p-4">
        <p>Valid observed months: {p.coverage.valid_months_3m}/3 recent months; {p.coverage.valid_months_6m}/6 longer-window months. Current records without known EUR: {p.coverage.n_sin_eur_current}.</p>
        <p className="small muted mt-2">Acceptance is model-level validation, not company-level eligibility or guaranteed accuracy.</p>
        <details className="source-records mt-4"><summary>Observed input features · not causal drivers</summary><p className="small muted p-4">Values describe observed inputs, not model contributions, feature importance, or causal explanations. No health points are inferred.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Observed proxy input features"><table><caption className="sr-only">Observed model inputs and missing evidence</caption><thead><tr><th>Feature</th><th>Observed value</th><th>Definition / missing reason</th></tr></thead><tbody>{Object.entries(p.observed_features).map(([name, value]) => <tr key={name}><td>{name}</td><td className="num">{value === null ? "Unavailable" : value.toPrecision(5)}</td><td>{value === null ? reason(p.missing_reason[name] ?? "unknown") : p.feature_descriptions[name]}</td></tr>)}</tbody></table></div></details>
      </div>
    </section>
    <section className="card evidence-card mt-4" aria-labelledby="proxy-validation-title">
      <div className="card-heading"><h2 id="proxy-validation-title">Validation scope and limits</h2></div>
      <div className="p-4">
        <p><strong>Conditional on {v.labeled_rows}/{v.prospective_rows} observed labels across {v.groups} groups.</strong> Censored outcomes are not counted as negatives. These aggregate results do not establish reliability for every company.</p>
        <p className="mt-2">Average precision {v.average_precision.toFixed(3)} versus prevalence {v.prevalence.toFixed(3)}. Brier {v.brier.toFixed(3)} versus baseline {v.baseline_brier.toFixed(3)}.</p>
        <p className="mt-2">Relative Brier skill {percent(v.relative_brier_skill)}; grouped 95% bootstrap interval {percent(v.relative_brier_skill_ci95[0])} – {percent(v.relative_brier_skill_ci95[1])}, conditional on the fixed model fit. This is not an individual probability confidence interval.</p>
        <ul className="small muted mt-2">{v.limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
      </div>
    </section>
  </div>;
}
