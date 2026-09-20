import { TrendingDown, TrendingUp } from "lucide-react";
import { date } from "../lib/format";
import type { ReceiptOutlook as Outlook } from "../lib/types";

function unavailable(reasons: string[]) {
  if (reasons.includes("original_benchmark_excluded")) return "This company was excluded from the predictive model. Its financial records remain available.";
  if (reasons.includes("before_selection_cutoff_no_backcast")) return "Predictions begin in January 2026. The model does not estimate earlier months.";
  if (reasons.includes("month_not_closed")) return "An outlook becomes available after the month closes.";
  if (reasons.includes("eur_coverage_incomplete")) return "Some amounts lack a supported currency conversion.";
  if (reasons.includes("positive_observed_receipt_reference_missing")) return "There is no positive receipt baseline to compare against.";
  if (reasons.includes("unverified_input_availability")) return "The dates when the supporting data became available could not be verified.";
  return "There isn’t enough comparable receipt history for this month.";
}

function checkpoint(asOf: string, days: number) {
  const value = new Date(`${asOf}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return date(value.toISOString().slice(0, 10), true);
}

export function ReceiptOutlook({ outlook }: { outlook?: Outlook }) {
  const targets = [
    { key: "receipt_contraction_3m", title: "Lower receipts", detail: "20% or more below the recent average", Icon: TrendingDown },
    { key: "receipt_expansion_3m", title: "Higher receipts", detail: "20% or more above the recent average", Icon: TrendingUp },
  ] as const;
  return <section className="card receipt-outlook" aria-labelledby="receipt-outlook-title">
    <header className="card-heading"><div><h2 id="receipt-outlook-title">Receipt outlook</h2><p className="small muted">{outlook ? `${date(outlook.horizon_start, true)} – ${date(outlook.horizon_end, true)}` : "The next three months"}</p></div><span className="badge">Experimental</span></header>
    <div className="receipt-checkpoints" aria-label="30, 60 and 90-day checkpoints">
      {[30, 60, 90].map(days => <div key={days}><strong>{days}<span> days</span></strong><time>{outlook ? checkpoint(outlook.as_of, days) : "Date unavailable"}</time><p>No separate forecast</p></div>)}
    </div>
    <p className="receipt-window-label">Estimate for the full three-month window</p>
    <div className="receipt-estimates">
      {targets.map(({ key, title, detail, Icon }) => {
        const prediction = outlook?.predictions[key];
        const probability = prediction?.probabilities?.["1"];
        return <article className="receipt-estimate" key={key}>
          <h3><Icon size={18} strokeWidth={1.5} aria-hidden/>{title}</h3>
          {probability != null ? <>
            <p className="receipt-probability num">{(probability * 100).toFixed(1)}<span>%</span></p>
            <p className="small muted">Estimated chance · {detail}, in at least two of the next three months.</p>
            <div className="receipt-track" aria-hidden><span style={{ width: `${probability * 100}%` }}/></div>
          </> : <><p className="receipt-unavailable">Not available</p><p className="small muted">{unavailable(prediction?.reasons ?? [])}</p></>}
        </article>;
      })}
    </div>
    <footer className="receipt-context">
      <p>Based on the selected month’s recorded receipts. Accuracy is not yet validated. These probabilities do not project cash balances.</p>
      <details><summary>How to read this outlook</summary><div>
        <p>The model compares the month’s operating receipts with their three-month average, then uses the outcomes of similar historical patterns. The two percentages describe separate events; they are not complements.</p>
        <p>These estimates use retrospectively reconstructed data. They were not issued at the historical cutoff. The probabilities are uncalibrated and predictive usefulness has not passed validation; automatic alerts are disabled.</p>
        <p>Temporary-dip predictions are not available because there is insufficient training support. Missing estimates do not mean zero risk.</p>
        {outlook && <p className="receipt-version">Model {outlook.model_version} · Data through {date(outlook.as_of, true)}</p>}
      </div></details>
    </footer>
  </section>;
}
