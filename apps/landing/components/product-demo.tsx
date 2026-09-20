import Image from "next/image";
import { ArrowRight, CalendarDays } from "lucide-react";
import logo from "../../brand/blau.svg";
import { appOrigin } from "../../metadata";
import { HealthScorePanel } from "../../app/components/health-score-panel";
import { MonthlyCashChart } from "../../app/components/monthly-cash-chart";
import { AgingCard, DefaultingCard } from "../../app/components/financial-cards";
import type { ModelRecord } from "../../app/lib/types";
import snapshot from "../data/product-preview.json";

export function ProductDemo() {
  const row: ModelRecord = snapshot.record;
  const companyUrl = `${appOrigin}/demo?company=${snapshot.company_id}`;
  return <section className="product-demo site-container" id="outlook" aria-labelledby="demo-title">
    <h2 id="demo-title" className="sr-only">A preview of your finance workspace</h2>
    <div className="demo-window" data-scroll-reveal>
      <div className="demo-topbar"><Image className="brand-logo" src={logo} alt="blau"/><a className="text-link" href={companyUrl}>Explore the dashboard<ArrowRight size={16} aria-hidden/></a></div>
      <div className="dashboard-preview">
        <div className="demo-heading"><div><h3>COMP 6</h3><p>Financial health and the movements behind it.</p></div><span className="demo-period"><CalendarDays size={15} aria-hidden/>August 2026</span></div>
        <div className="dashboard-charts"><HealthScorePanel records={snapshot.history} row={row} chartHeight={220}/><MonthlyCashChart row={row} chartHeight={220}/></div>
        <div className="obligation-cards"><AgingCard row={row} side="cobro"/><AgingCard row={row} side="pago"/><DefaultingCard row={row}/></div>
      </div>
    </div>
    <p className="preview-caption">COMP 6 · August 2026. A snapshot from the published demo dataset.</p>
  </section>;
}
