import { SegmentedGauge } from "./financial-cards";

export function DashboardSkeleton({ detail = false }: { detail?: boolean }) {
  return <div className="dashboard-skeleton" role="status">
    <span className="sr-only">Loading company health and source records…</span>
    <div className="model-dashboard" aria-hidden="true">
      <header className="page-heading company-heading">
        <div><h1><span className="skeleton-line skeleton-title"/></h1><p className="muted mt-2">Financial health and the movements behind it.</p></div>
        <div className="company-heading-actions"><span className="skeleton-control"/></div>
      </header>
      <div className="stats-grid model-stats">
        {["Monthly net movement", "Overdue supplier payments", "Overdue customer collections", "Defaulting"].map(label => <article className="stat-card" key={label}>
          <div><span>{label}</span></div><strong className="stat-value"><span className="skeleton-line skeleton-value"/></strong><p><span className="skeleton-line"/></p>
        </article>)}
      </div>
      <div className={detail ? undefined : "dashboard-charts"}>
        <section className="card health-overview-panel">
          <div className="card-heading"><div><h2>Health score</h2><p className="small muted mt-1">{detail ? "Published rating at this cutoff" : "How the company’s cash-flow health has changed"}</p></div></div>
          <div className="health-selected-score">
            <SegmentedGauge segments={[]} className="health-score-gauge"><div className="health-score-value"><span className="skeleton-line skeleton-value"/></div></SegmentedGauge>
            <div className="health-score-context"><span className="skeleton-line"/><span className="skeleton-line"/></div>
          </div>
          {!detail && <div className="model-chart health-history-chart"><div className="skeleton-plot"/><p className="chart-note"><span className="skeleton-line"/><span className="skeleton-line"/></p></div>}
        </section>
        {!detail && <section className="card monthly-cash">
          <div className="card-heading"><div><h2>Cash flow</h2><p className="small muted mt-1"><span className="skeleton-line"/></p></div><span className="skeleton-control skeleton-currency"/></div>
          <div className="cash-flow-legend">{[1, 2, 3].map(n => <span className="skeleton-line" key={n}/>)}</div>
          <div className="model-chart cash-flow-chart"><div className="skeleton-plot"/><p className="chart-note"><span className="skeleton-line"/><span className="skeleton-line"/></p></div>
          <div className="cash-inspected-period"><span className="skeleton-line"/></div>
          <dl className="checkpoints cash-flow-totals">{["Income", "Expenses", "Month-end cash"].map(label => <div key={label}><dt>{label}</dt><dd><span className="skeleton-line skeleton-value"/></dd></div>)}</dl>
        </section>}
      </div>
      {detail ? <section className="card health-assessment-card"><div className="metric-columns">
        {[1, 2].map(column => <div className="metric-evidence" key={column}><span className="skeleton-line skeleton-value"/>{[1, 2, 3, 4, 5, 6].map(row => <div className="skeleton-row" key={row}><span className="skeleton-line"/><span className="skeleton-line"/></div>)}</div>)}
      </div></section> : <div className="obligation-cards">
        {["Overdue collections", "Overdue payments", "Defaulting"].map((label, index) => <section className={`card financial-card${index === 2 ? " defaulting-card" : ""}`} key={label}>
          <div className="financial-card-heading"><div><h2>{label}</h2><p><span className="skeleton-line"/></p></div></div>
          {index < 2 ? <SegmentedGauge segments={[]} className="aging-gauge"><div className="aging-total"><span className="skeleton-line skeleton-value"/><span className="skeleton-line"/></div></SegmentedGauge> : <div className="defaulting-summary"><span className="skeleton-line"/><span className="skeleton-line skeleton-value"/></div>}
          <div className="aging-buckets">{(index === 2 ? [1, 2] : [1, 2, 3, 4, 5]).map(row => <div className="skeleton-row" key={row}><span className="skeleton-line"/><span className="skeleton-line"/></div>)}</div>
          {index === 2 && <section className="defaulting-fx"><h3>Currency risk</h3><div className="defaulting-summary"><span className="skeleton-line"/><span className="skeleton-line skeleton-value"/></div>{[1, 2].map(row => <div className="skeleton-row" key={row}><span className="skeleton-line"/><span className="skeleton-line"/></div>)}</section>}
          <footer><span className="skeleton-line"/></footer>
        </section>)}
      </div>}
    </div>
  </div>;
}
