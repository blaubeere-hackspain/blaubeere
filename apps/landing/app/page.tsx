import Image from "next/image";
import { ArrowDown, ArrowRight, Check, FileSearch, LockKeyhole, ShieldCheck, SlidersHorizontal } from "lucide-react";
import painting from "../public/cash-horizon-oil.png";
import { ProductDemo } from "../components/product-demo";
import { AssistantSection } from "../components/assistant-section";
import { ScrollReveals } from "../components/scroll-reveals";
import { PricingCards } from "../components/pricing-cards";
import { SiteHeader, SiteFooter } from "../components/site-chrome";

const app = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3100";

export default function Home() {
  return <>
    <ScrollReveals/>
    <SiteHeader/>
    <main id="main">
      <div className="painted-intro">
        <div className="painting" aria-hidden><Image src={painting} alt="" fill sizes="100vw" preload placeholder="blur"/></div>
        <section className="hero site-container" aria-labelledby="hero-title">
          <span className="hero-eyebrow"><span className="live-dot"/>A little financial foresight</span>
          <h1 id="hero-title">A clearer picture.<br/><span>A calmer next move.</span></h1>
          <p>See your cash ahead. Understand what’s changing.<br className="desktop-break"/> Make a plan for the things that matter.</p>
          <div className="hero-actions"><a className="button" href={`${app}/dashboard`}>See your cash outlook<ArrowRight size={17} aria-hidden/></a><a className="text-link" href="#outlook">Take a closer look<ArrowDown size={16} aria-hidden/></a></div>
          <span className="hero-footnote"><LockKeyhole size={13} aria-hidden/>Your companies. Your assumptions. Your decisions.</span>
        </section>
        <ProductDemo/>
      </div>
      <div className="page-content">
        <section className="our-story site-container" aria-labelledby="story-title">
          <div className="story-copy" data-scroll-reveal><h2 id="story-title">We believe a clearer view of your finances makes room for better decisions.</h2><p>We’re building blau for the people behind the numbers. A place to bring cash, commitments and possibilities into focus — so you can see a shortfall coming, understand what’s behind it and explore a way forward.</p><p>Your company’s evidence stays in view. Your assumptions stay open to question. And the next move stays yours.</p></div>
        </section>
        <section className="planning site-container" id="planning" aria-labelledby="planning-title"><div className="planning-copy" data-scroll-reveal><h2 id="planning-title">“What would it take?”<br/><span>A good place to start.</span></h2><p>Protect a cash buffer. Fund the next quarter. Explore a revenue goal. Compare the trade-offs before you choose your next move.</p><a className="button" href={`${app}/dashboard`}>Explore a plan<ArrowRight size={16} aria-hidden/></a></div><div className="mini-plan" data-scroll-reveal="1"><span className="eyebrow">An example, not a promise</span><h3>Keep cash above <span>€100,000.</span></h3><p>Through 29 November · every day of the plan</p><dl><div><dt>Collection timing</dt><dd>Up to 14 days earlier</dd></div><div><dt>Discretionary spend</dt><dd>Up to 10% lower</dd></div><div><dt>Additional funding</dt><dd>Within your limit</dd></div></dl><div className="mini-plan-note"><Check size={18} aria-hidden/><p>Baseline plus two conditional plans. See the cash impact and any gap that remains.</p></div></div></section>
        <AssistantSection/>
        <section className="trust site-container" id="built-for-finance" aria-labelledby="trust-title"><div className="section-intro" data-scroll-reveal><h2 id="trust-title">Clear about what’s known.<br/><span>Honest about what isn’t.</span></h2></div><div className="trust-grid"><article data-scroll-reveal><FileSearch size={26} strokeWidth={1.4} aria-hidden/><h3>Evidence you can inspect.</h3><p>Dated sources, visible assumptions and missing inputs. Understand what’s behind every projection.</p></article><article data-scroll-reveal="1"><ShieldCheck size={26} strokeWidth={1.4} aria-hidden/><h3>Access that stays yours.</h3><p>Your companies, your permissions. Connected assistants use the same access and can be disconnected at any time.</p></article><article data-scroll-reveal="2"><SlidersHorizontal size={26} strokeWidth={1.4} aria-hidden/><h3>Decisions that stay human.</h3><p>Test scenarios without changing source data or executing payments. Your team makes the call.</p></article></div></section>
        <section className="landing-pricing" id="pricing" aria-labelledby="pricing-title">
          <div className="section-intro site-container" data-scroll-reveal><h2 id="pricing-title">A clearer picture.<br/><span>One simple starting point.</span></h2><p>One fixed price. Or a conversation about what your team needs.</p></div>
          <PricingCards heading="h3"/>
          <p className="pricing-details"><a className="text-link" href="/pricing">Compare the plans<ArrowRight size={16} aria-hidden/></a></p>
        </section>
        <section className="closing site-container" data-scroll-reveal><span className="eyebrow">Make room for what’s next</span><h2>Good decisions start<br/><span>with a clearer picture.</span></h2><a className="button" href={`${app}/dashboard`}>Open your workspace<ArrowRight size={16} aria-hidden/></a></section>
      </div>
    </main>
    <SiteFooter/>
  </>;
}
