import Image from "next/image";
import { ArrowDown, ArrowRight, Building2, Check, FileSearch, LockKeyhole, ShieldCheck, SlidersHorizontal, TriangleAlert } from "lucide-react";
import painting from "../public/cash-horizon-oil.png";

const app = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3100";
function Brand() { return <a className="brand" href="/" aria-label="Blaubeere home"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</a>; }

export default function Home() {
  return <>
    <a href="#main" className="skip-link">Skip to content</a>
    <header className="site-header site-container"><Brand/><nav aria-label="Main navigation"><a href="#how-it-works">The approach</a><a href="#outlook">Cash outlook</a><a href="#planning">Planning</a></nav><a className="button secondary" href={`${app}/login`}>Open workspace<ArrowRight size={16} aria-hidden/></a></header>
    <main id="main">
      <div className="painted-intro">
        <div className="painting" aria-hidden><Image src={painting} alt="" fill sizes="100vw" preload placeholder="blur"/></div>
        <section className="hero site-container" aria-labelledby="hero-title">
          <span className="hero-eyebrow"><span className="live-dot"/>A little financial foresight</span>
          <h1 id="hero-title">A clearer picture.<br/><span>A calmer next move.</span></h1>
          <p>See your cash ahead. Understand what’s changing.<br className="desktop-break"/> Make a plan for the things that matter.</p>
          <div className="hero-actions"><a className="button" href={`${app}/dashboard`}>See your cash outlook<ArrowRight size={17} aria-hidden/></a><a className="text-link" href="#how-it-works">Take a closer look<ArrowDown size={16} aria-hidden/></a></div>
          <span className="hero-footnote"><LockKeyhole size={13} aria-hidden/>Your companies. Your assumptions. Your decisions.</span>
        </section>
        <section className="approach site-container" id="how-it-works" aria-labelledby="approach-title">
          <div className="approach-heading"><h2 id="approach-title">Room to see the whole picture.</h2><span>From a question to a next step</span></div>
          <div className="approach-grid">
            <a className="paint-card" href="#outlook"><h3><strong>See.</strong> Know where your cash is going, before it gets there.</h3><div className="paint-card-bottom"><span className="card-symbol" aria-hidden><i/><i/><i/><i/><i/><i/><i/><i/></span><span>Explore your outlook<ArrowRight size={18} aria-hidden/></span></div></a>
            <a className="paint-card" href="#built-for-finance"><h3><strong>Understand.</strong> Follow the numbers back to their source.</h3><div className="paint-card-bottom"><FileSearch size={30} strokeWidth={1.25} aria-hidden/><span>Look at the evidence<ArrowRight size={18} aria-hidden/></span></div></a>
            <a className="paint-card" href="#planning"><h3><strong>Plan.</strong> Give your next decision a little more perspective.</h3><div className="paint-card-bottom"><SlidersHorizontal size={30} strokeWidth={1.25} aria-hidden/><span>Consider your options<ArrowRight size={18} aria-hidden/></span></div></a>
          </div>
        </section>
      </div>
      <div className="page-content">
        <section className="outlook site-container" id="outlook" aria-labelledby="outlook-title">
          <div className="section-intro"><span className="eyebrow">01 / A little clarity</span><h2 id="outlook-title">Ahead of the gap.<br/><span>Ready for the conversation.</span></h2><p>Customer receipts. Supplier payments. The space in between. Bring them into one cash outlook, with the assumptions in plain sight.</p></div>
          <div className="preview"><div className="preview-header"><span><Building2 size={17} aria-hidden/><strong>Mediterránea Supply</strong></span><span className="badge">Illustrative demo · EUR</span></div>
            <div className="preview-stats"><div><span>Cash today</span><strong className="num">€2.0m</strong><small>31 August 2026</small></div><div><span>Funding needed</span><strong className="num">€2.1m</strong><small>Above a €100k floor</small></div><div className="preview-horizon"><span>90-day cash outlook</span><small><i/>Baseline forecast</small></div></div>
            <div className="preview-chart"><svg viewBox="0 0 800 260" role="img" aria-label="Illustrative cash outlook: cash falls from two million euros to negative two million on 28 September before recovering in October."><path d="M60 30H775M60 72.5H775M60 115H775M60 157.5H775M60 200H775" stroke="var(--line)"/><text x="0" y="34">€2m</text><text x="0" y="119">€0</text><text x="0" y="204">−€2m</text><line x1="60" x2="775" y1="110.75" y2="110.75" stroke="var(--warn)" strokeDasharray="3 5"/><path d="M60 30H282V200H328V172H383V188H425V37H478V54H553V32H630V50H715V27H775" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeDasharray="5 4"/><circle cx="282" cy="200" r="5" fill="var(--bad)"/><text x="60" y="246">31 Aug</text><text x="282" y="246" textAnchor="middle">28 Sept</text><text x="553" y="246" textAnchor="middle">30 Oct</text><text x="775" y="246" textAnchor="end">29 Nov</text></svg></div>
            <div className="preview-alert"><TriangleAlert size={18} aria-hidden/><p><strong>A cash gap ahead, with time to act.</strong> Supplier payments fall before customer receipts.</p><a href={`${app}/dashboard`} className="text-link">See the outlook<ArrowRight size={16} aria-hidden/></a></div>
          </div>
          <p className="preview-caption">An example using demo data. Your outlook depends on your company’s records and assumptions.</p>
        </section>
        <section className="planning site-container" id="planning" aria-labelledby="planning-title"><div className="planning-copy"><span className="eyebrow">02 / A little possibility</span><h2 id="planning-title">“What would it take?”<br/><span>A good place to start.</span></h2><p>Protect a cash buffer. Fund the next quarter. Explore a revenue goal. Compare the trade-offs before you choose your next move.</p><a className="button" href={`${app}/dashboard`}>Explore a plan<ArrowRight size={16} aria-hidden/></a></div><div className="mini-plan"><span className="eyebrow">An example, not a promise</span><h3>Keep cash above <span>€100,000.</span></h3><p>Through 29 November · every day of the plan</p><dl><div><dt>Collection timing</dt><dd>Up to 14 days earlier</dd></div><div><dt>Discretionary spend</dt><dd>Up to 10% lower</dd></div><div><dt>Additional funding</dt><dd>Within your limit</dd></div></dl><div className="mini-plan-note"><Check size={18} aria-hidden/><p>Baseline plus two conditional plans. See the cash impact and any gap that remains.</p></div></div></section>
        <section className="trust site-container" id="built-for-finance" aria-labelledby="trust-title"><div className="section-intro"><span className="eyebrow">03 / A little confidence</span><h2 id="trust-title">Clear about what’s known.<br/><span>Honest about what isn’t.</span></h2></div><div className="trust-grid"><article><FileSearch size={26} strokeWidth={1.4} aria-hidden/><h3>Evidence you can inspect.</h3><p>Dated sources, visible assumptions and missing inputs. Understand what’s behind every projection.</p></article><article><ShieldCheck size={26} strokeWidth={1.4} aria-hidden/><h3>Access that stays yours.</h3><p>Your companies, your permissions. Connected assistants use the same access and can be disconnected at any time.</p></article><article><SlidersHorizontal size={26} strokeWidth={1.4} aria-hidden/><h3>Decisions that stay human.</h3><p>Test scenarios without changing source data or executing payments. Your team makes the call.</p></article></div></section>
        <section className="closing site-container"><span className="eyebrow">Make room for what’s next</span><h2>Good decisions start<br/><span>with a clearer picture.</span></h2><a className="button" href={`${app}/dashboard`}>Open your workspace<ArrowRight size={16} aria-hidden/></a></section>
      </div>
    </main>
    <footer className="site-footer site-container"><Brand/><span>A little foresight goes a long way. · HackSpain 2026</span><a className="text-link" href={`${app}/login`}>Sign in<ArrowRight size={16} aria-hidden/></a></footer>
  </>;
}
