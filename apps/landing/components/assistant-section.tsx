"use client";

import { useState } from "react";
import Image from "next/image";
import { ArrowRight } from "lucide-react";
import perspective from "../public/perspective-oil.png";
import foundation from "../public/foundation-oil.png";
import possibility from "../public/possibility-oil.png";
import { appOrigin } from "../../metadata";

const features = [
  { title: "See what’s ahead.", description: "Ask when cash gets tight, what’s driving the gap and how much funding your company may need.", image: perspective, alt: "An oil painting of a lake winding through a wide mountain valley." },
  { title: "Follow the evidence.", description: "Look behind an outlook at its dated sources, assumptions and missing inputs.", image: foundation, alt: "An oil painting of a small farmhouse among sunlit green hills." },
  { title: "Explore a different path.", description: "Compare “what if” plans for earlier collections, lower spending or extra funding, within limits you choose.", image: possibility, alt: "An oil painting of a person on a bench looking toward an open horizon." },
];

export function AssistantSection() {
  const [active, setActive] = useState(0);
  return <section className="assistant-section site-container" id="assistants" aria-labelledby="assistant-title">
    <div className="assistant-painting" id="assistant-artwork">
      {features.map((feature, index) => <Image key={feature.title} src={feature.image} alt={active === index ? feature.alt : ""} aria-hidden={active !== index} data-active={active === index} fill sizes="(max-width: 760px) calc(100vw - 40px), (max-width: 1000px) calc((100vw - 104px) / 2), (max-width: 1320px) calc((100vw - 176px) / 2), 572px" loading="eager" placeholder="blur"/>)}
    </div>
    <div className="assistant-copy">
      <div className="assistant-intro"><span className="eyebrow">03 / Connect with MCP</span><h2 id="assistant-title">A second perspective.<br/><span>The same clear picture.</span></h2><p>MCP connects blau to the AI assistant you already use. Bring your company’s financial context into the conversation, and ask questions in your own words.</p></div>
      <ul className="assistant-features" aria-label="Explore assistant features">
        {features.map((feature, index) => <li key={feature.title}><button className="assistant-feature" type="button" aria-pressed={active === index} aria-controls="assistant-artwork" onMouseEnter={() => setActive(index)} onFocus={() => setActive(index)} onClick={() => setActive(index)}><span className="assistant-number" aria-hidden>0{index + 1}</span><span><strong className="assistant-feature-title">{feature.title}</strong><span className="assistant-feature-description">{feature.description}</span></span></button></li>)}
      </ul>
      <div className="assistant-actions"><a className="button" href={`${appOrigin}/dashboard`}>Connect your assistant<ArrowRight size={16} aria-hidden/></a><p className="assistant-note">For assistants that support remote MCP and OAuth. You approve access and can revoke it anytime. No source edits or payments.</p></div>
    </div>
  </section>;
}
