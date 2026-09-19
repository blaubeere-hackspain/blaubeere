import Image from "next/image";
import { ArrowRight, Check } from "lucide-react";
import painting from "../../public/cash-horizon-oil.png";
import { SiteHeader, SiteFooter } from "../../components/site-chrome";
import { appOrigin, landingOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(landingOrigin, "/pricing", "Pricing | blau", "A $99 fixed-price plan or a conversation about Enterprise. Explore blau pricing for cash forecasts, source evidence and what-if planning.");

export default function Pricing() {
  return <>
    <SiteHeader pricing/>
    <main id="main" className="pricing-main">
      <div className="pricing-painting" aria-hidden><Image src={painting} alt="" fill sizes="100vw" preload placeholder="blur"/></div>
      <div className="pricing-intro site-container"><span className="eyebrow">A little clarity, from the start</span><h1>A clearer picture.<br/><span>A plan that fits.</span></h1><p>One fixed price. Or a conversation about what your team needs.</p></div>
      <section className="pricing-grid site-container" aria-label="Plans and pricing">
        <article className="pricing-card" aria-labelledby="fixed-plan"><span className="eyebrow">For your next move</span><h2 id="fixed-plan">Fixed</h2><p className="pricing-price"><strong>$99</strong><span>Fixed price · USD</span></p><p className="pricing-description">Bring your cash, commitments and possibilities into focus.</p><ul className="pricing-features"><li><Check size={17} aria-hidden/>Cash outlooks and funding gaps</li><li><Check size={17} aria-hidden/>Dated sources and visible assumptions</li><li><Check size={17} aria-hidden/>Conditional what-if plans</li><li><Check size={17} aria-hidden/>Connect your AI assistant with MCP</li></ul><button className="button" type="button" disabled>Contact sales<ArrowRight size={16} aria-hidden/></button></article>
        <article className="pricing-card enterprise-card" aria-labelledby="enterprise-plan"><span className="eyebrow">For the bigger picture</span><h2 id="enterprise-plan">Enterprise</h2><p className="pricing-price"><strong>Custom</strong></p><p className="pricing-description">Tell us how your team works. Let’s discuss a plan around it.</p><ul className="pricing-features"><li><Check size={17} aria-hidden/>Your companies and team requirements</li><li><Check size={17} aria-hidden/>Your data and planning workflow</li><li><Check size={17} aria-hidden/>Your assistant and access needs</li></ul><button className="button secondary" type="button" disabled>Contact sales<ArrowRight size={16} aria-hidden/></button></article>
      </section>
      <p className="pricing-demo site-container">A little look before your next move.<a className="text-link" href={`${appOrigin}/login`}>Explore the demo<ArrowRight size={16} aria-hidden/></a></p>
    </main>
    <SiteFooter/>
  </>;
}
