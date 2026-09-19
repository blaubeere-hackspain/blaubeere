import Image from "next/image";
import { Activity, ArrowDown, ArrowDownRight, ArrowRight, Building2, CalendarDays, Check, FileSearch, LockKeyhole, ShieldCheck, SlidersHorizontal, TriangleAlert, Wallet } from "lucide-react";
import painting from "../public/cash-horizon-oil.png";
import perspective from "../public/perspective-oil.png";
import foundation from "../public/foundation-oil.png";
import possibility from "../public/possibility-oil.png";
import mcpConnection from "../public/mcp-connection-oil.png";

const app = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:3100";
function Brand() { return <a className="brand" href="/" aria-label="Blaubeere home"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</a>; }

export default function Home() {
  return <>
    <a href="#main" className="skip-link">Skip to content</a>
    <header className="site-header site-container"><Brand/><nav aria-label="Main navigation"><a href="#outlook">The workspace</a><a href="#planning">Planning</a><a href="#built-for-finance">Our approach</a></nav><a className="button secondary" href={`${app}/login`}>Open workspace<ArrowRight size={16} aria-hidden/></a></header>
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
          <div className="oil-gallery"><figure><Image src={perspective} alt="An oil painting of a lake winding through a wide mountain valley." sizes="(max-width: 760px) 33vw, (max-width: 1320px) 31vw, 392px" placeholder="blur"/></figure><figure><Image src={foundation} alt="An oil painting of a small farmhouse among sunlit green hills." sizes="(max-width: 760px) 33vw, (max-width: 1320px) 31vw, 392px" placeholder="blur"/></figure><figure><Image src={possibility} alt="An oil painting of a person on a bench looking toward an open horizon." sizes="(max-width: 760px) 33vw, (max-width: 1320px) 31vw, 392px" placeholder="blur"/></figure></div>
          <div className="story-layout"><div className="story-label"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span><span className="eyebrow">What we’re building</span></div><div className="story-copy"><h2 id="story-title">We believe a clearer view of your finances makes room for better decisions.</h2><p>We’re building Blaubeere for the people behind the numbers. A place to bring cash, commitments and possibilities into focus — so you can see a shortfall coming, understand what’s behind it and explore a way forward.</p><p>Your company’s evidence stays in view. Your assumptions stay open to question. And the next move stays yours.</p></div></div>
        </section>
        <section className="planning site-container" id="planning" aria-labelledby="planning-title"><div className="planning-copy"><span className="eyebrow">02 / A little possibility</span><h2 id="planning-title">“What would it take?”<br/><span>A good place to start.</span></h2><p>Protect a cash buffer. Fund the next quarter. Explore a revenue goal. Compare the trade-offs before you choose your next move.</p><a className="button" href={`${app}/dashboard`}>Explore a plan<ArrowRight size={16} aria-hidden/></a></div><div className="mini-plan"><span className="eyebrow">An example, not a promise</span><h3>Keep cash above <span>€100,000.</span></h3><p>Through 29 November · every day of the plan</p><dl><div><dt>Collection timing</dt><dd>Up to 14 days earlier</dd></div><div><dt>Discretionary spend</dt><dd>Up to 10% lower</dd></div><div><dt>Additional funding</dt><dd>Within your limit</dd></div></dl><div className="mini-plan-note"><Check size={18} aria-hidden/><p>Baseline plus two conditional plans. See the cash impact and any gap that remains.</p></div></div></section>
        <section className="assistant-section site-container" id="assistants" aria-labelledby="assistant-title">
          <div className="assistant-painting"><Image src={mcpConnection} alt="An oil painting of an AI chat assistant linked through an MCP connector to financial records, a cash chart and scenario plans." fill sizes="(max-width: 760px) calc(100vw - 40px), (max-width: 1000px) calc((100vw - 104px) / 2), (max-width: 1320px) calc((100vw - 176px) / 2), 572px" placeholder="blur"/></div>
          <div className="assistant-copy">
            <span className="eyebrow">03 / Connect with MCP</span>
            <h2 id="assistant-title">A second perspective.<br/><span>The same clear picture.</span></h2>
            <p>MCP connects Blaubeere to the AI assistant you already use. Bring your company’s financial context into the conversation, and ask questions in your own words.</p>
            <ul className="assistant-features">
              <li><span className="assistant-number" aria-hidden>01</span><div><h3>See what’s ahead.</h3><p>Ask when cash gets tight, what’s driving the gap and how much funding your company may need.</p></div></li>
              <li><span className="assistant-number" aria-hidden>02</span><div><h3>Follow the evidence.</h3><p>Look behind an outlook at its dated sources, assumptions and missing inputs.</p></div></li>
              <li><span className="assistant-number" aria-hidden>03</span><div><h3>Explore a different path.</h3><p>Compare “what if” plans for earlier collections, lower spending or extra funding, within limits you choose.</p></div></li>
            </ul>
            <a className="button" href={`${app}/dashboard`}>Connect your assistant<ArrowRight size={16} aria-hidden/></a>
            <p className="assistant-note">For assistants that support remote MCP and OAuth. You approve access and can revoke it anytime. No source edits or payments.</p>
          </div>
        </section>
        <section className="trust site-container" id="built-for-finance" aria-labelledby="trust-title"><div className="section-intro"><span className="eyebrow">04 / A little confidence</span><h2 id="trust-title">Clear about what’s known.<br/><span>Honest about what isn’t.</span></h2></div><div className="trust-grid"><article><FileSearch size={26} strokeWidth={1.4} aria-hidden/><h3>Evidence you can inspect.</h3><p>Dated sources, visible assumptions and missing inputs. Understand what’s behind every projection.</p></article><article><ShieldCheck size={26} strokeWidth={1.4} aria-hidden/><h3>Access that stays yours.</h3><p>Your companies, your permissions. Connected assistants use the same access and can be disconnected at any time.</p></article><article><SlidersHorizontal size={26} strokeWidth={1.4} aria-hidden/><h3>Decisions that stay human.</h3><p>Test scenarios without changing source data or executing payments. Your team makes the call.</p></article></div></section>
        <section className="closing site-container"><span className="eyebrow">Make room for what’s next</span><h2>Good decisions start<br/><span>with a clearer picture.</span></h2><a className="button" href={`${app}/dashboard`}>Open your workspace<ArrowRight size={16} aria-hidden/></a></section>
      </div>
    </main>
    <footer className="site-footer site-container"><Brand/><span>A little foresight goes a long way. · HackSpain 2026</span><a className="text-link" href={`${app}/login`}>Sign in<ArrowRight size={16} aria-hidden/></a></footer>
  </>;
}

function ProductDemo() {
  return <section className="product-demo site-container" id="outlook" aria-labelledby="demo-title">
    <h2 id="demo-title" className="sr-only">A preview of your finance workspace</h2>
    <div className="demo-window">
      <div className="demo-topbar"><span className="demo-wordmark"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</span><span className="demo-label"><span className="live-dot"/>Product preview · demo data</span></div>
      <div className="demo-layout">
        <aside className="demo-sidebar" aria-hidden><span className="demo-workspace"><Building2 size={18}/>Finance workspace</span><span className="eyebrow">Workspace</span><span className="demo-nav active"><Activity size={17}/>Cash outlook</span><span className="demo-nav"><ArrowDownRight size={17}/>Explore a plan</span><div className="demo-sidebar-note"><ShieldCheck size={20}/><p>Your companies.<br/>A clearer perspective.</p></div></aside>
        <div className="demo-main">
          <div className="demo-heading"><div><h3>Your cash, in perspective.</h3><p>See what’s ahead. Understand why.</p></div><span className="demo-period">90-day outlook</span></div>
          <div className="demo-context"><span><Building2 size={15} aria-hidden/>Mediterránea Supply</span><span><CalendarDays size={14} aria-hidden/>31 August 2026</span><span>EUR</span></div>
          <div className="demo-metrics"><div><span>Cash today<Wallet size={15} aria-hidden/></span><strong className="num">€2,000,000</strong><small>Usable cash at the cutoff</small></div><div className="needs-attention"><span>Funding needed<TriangleAlert size={15} aria-hidden/></span><strong className="num">€2,100,000</strong><small>Above a €100,000 cash floor</small></div><div><span>First cash floor breach<CalendarDays size={15} aria-hidden/></span><strong className="num">28 Sept</strong><small>With time to consider your options</small></div></div>
          <div className="demo-chart"><div className="demo-chart-heading"><h4>Cash over time</h4><span>History + 90-day forecast</span></div><div className="demo-legend"><span><i/>Reconstructed history</span><span><i className="forecast-line"/>Baseline forecast</span><span><i className="floor-line"/>Cash floor</span></div>
            <DemoChart/><DemoChart compact/>
            <div className="demo-chart-footer"><span><TriangleAlert size={16} aria-hidden/>Supplier payments arrive before customer receipts.</span><strong>€2.1m funding gap</strong></div>
          </div>
          <div className="demo-bottom"><span><ShieldCheck size={14} aria-hidden/>Every assumption stays visible.</span><a className="text-link" href={`${app}/dashboard`}>Explore the workspace<ArrowRight size={16} aria-hidden/></a></div>
        </div>
      </div>
    </div>
    <p className="preview-caption">An illustrative preview using demo data. Your outlook depends on your company’s records and assumptions.</p>
  </section>;
}

function DemoChart({ compact = false }: { compact?: boolean }) {
  const left = compact ? 50 : 60, right = compact ? 330 : 775;
  const x = (position: number) => left + (position - 60) * (right - left) / 715;
  return <svg className={compact ? "demo-chart-compact" : "demo-chart-wide"} viewBox={`0 0 ${compact ? 340 : 800} 228`} role="img" aria-label="Illustrative cash outlook: history falls to two million euros on 31 August, followed by a projected cash shortfall of negative two million on 28 September, before recovering in October.">
    <g transform={`translate(${left} 0) scale(${(right - left) / 715} 1) translate(-60 0)`}>
      <rect x="280" y="24" width="495" height="160" fill="var(--accent-soft)" opacity=".5"/><path d="M60 30H775M60 80H775M60 130H775M60 180H775" stroke="var(--line)"/><line x1="60" x2="775" y1="127.5" y2="127.5" stroke="var(--warn)" strokeDasharray="3 5"/><line x1="280" x2="280" y1="22" y2="184" stroke="var(--subtle)" strokeDasharray="3 5"/><path d="M60 52.5L134 57.5L207 67.5L280 80" fill="none" stroke="var(--ink)" strokeWidth="2.5"/><path d="M280 80H434V180H445V161.25H457V175H516V179H528V174H588V94H660V85H666V97H720V100H764V78.75H775" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeDasharray="5 4"/>
    </g>
    <circle cx={x(434)} cy="180" r="4" fill="var(--bad)"/><text x="0" y="34">€4m</text><text x="0" y="84">€2m</text><text x="0" y="134">€0</text><text x="0" y="184">−€2m</text><text x={x(280) + 8} y="16">Forecast →</text><text x={left} y="218">2 Jun</text><text x={x(280)} y="218" textAnchor="middle">31 Aug</text>{!compact && <text x={x(528)} y="218" textAnchor="middle">15 Oct</text>}<text x={right} y="218" textAnchor="end">29 Nov</text>
  </svg>;
}
