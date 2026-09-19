import Image from "next/image";
import { ArrowRight } from "lucide-react";
import painting from "../../public/cash-horizon-oil.png";
import { PricingCards } from "../../components/pricing-cards";
import { SiteHeader, SiteFooter } from "../../components/site-chrome";
import { appOrigin, landingOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(landingOrigin, "/pricing", "Pricing | blau", "A $99 fixed-price plan or a conversation about Enterprise. Explore blau pricing for cash forecasts, source evidence and what-if planning.");

export default function Pricing() {
  return <>
    <SiteHeader pricing/>
    <main id="main" className="pricing-main">
      <div className="pricing-painting" aria-hidden><Image src={painting} alt="" fill sizes="100vw" preload placeholder="blur"/></div>
      <div className="pricing-intro site-container"><span className="eyebrow">A little clarity, from the start</span><h1>A clearer picture.<br/><span>A plan that fits.</span></h1><p>One fixed price. Or a conversation about what your team needs.</p></div>
      <PricingCards/>
      <section className="pricing-comparison site-container" id="features" aria-labelledby="comparison-title">
        <div className="section-intro"><span className="eyebrow">What’s in your plan</span><h2 id="comparison-title">The details, side by side.</h2><p>The essentials in both plans. Enterprise starts with a conversation about how your team works.</p></div>
        <table className="pricing-table">
          <caption className="sr-only">Compare Fixed and Enterprise features</caption>
          <thead><tr><th scope="col">Your workspace</th><th scope="col">Fixed<span>$99 fixed</span></th><th scope="col">Enterprise<span>Custom</span></th></tr></thead>
          <tbody>
            <tr><th scope="row">Cash forecasts</th><td>Included</td><td>Included</td></tr>
            <tr><th scope="row">Funding gaps and cash floor breaches</th><td>Included</td><td>Included</td></tr>
            <tr><th scope="row">Dated sources and visible assumptions</th><td>Included</td><td>Included</td></tr>
            <tr><th scope="row">Conditional what-if plans</th><td>Included</td><td>Included</td></tr>
            <tr><th scope="row">AI assistant connections with MCP</th><td>Included</td><td>Included</td></tr>
            <tr><th scope="row">Data and planning workflow</th><td>Standard workspace</td><td>Discuss your needs</td></tr>
            <tr><th scope="row">Company, team and access requirements</th><td>Standard workspace</td><td>Discuss your needs</td></tr>
          </tbody>
        </table>
      </section>
      <p className="pricing-demo site-container">A little look before your next move.<a className="text-link" href={`${appOrigin}/login`}>Explore the demo<ArrowRight size={16} aria-hidden/></a></p>
    </main>
    <SiteFooter/>
  </>;
}
