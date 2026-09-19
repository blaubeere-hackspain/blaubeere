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
      <p className="pricing-demo site-container">A little look before your next move.<a className="text-link" href={`${appOrigin}/login`}>Explore the demo<ArrowRight size={16} aria-hidden/></a></p>
    </main>
    <SiteFooter/>
  </>;
}
