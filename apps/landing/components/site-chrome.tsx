import Image from "next/image";
import { ArrowRight } from "lucide-react";
import logo from "../../brand/blau.svg";
import { appOrigin } from "../../metadata";

function Brand() { return <a className="brand" href="/" aria-label="blau home"><Image className="brand-logo" src={logo} alt="blau"/></a>; }

export function SiteHeader({ pricing = false }: { pricing?: boolean }) {
  return <><a href="#main" className="skip-link">Skip to content</a><header className="site-header site-container"><Brand/><nav aria-label="Main navigation"><a href="/#outlook">The workspace</a><a href="/#planning">Planning</a><a href="/#built-for-finance">Our approach</a><a href="/pricing" aria-current={pricing ? "page" : undefined}>Pricing</a></nav><a className="button secondary" href={`${appOrigin}/login`}>Open workspace<ArrowRight size={16} aria-hidden/></a></header></>;
}

export function SiteFooter() {
  return <footer className="site-footer site-container"><Brand/><span>A little foresight goes a long way. · HackSpain 2026</span><nav className="footer-links" aria-label="Footer navigation"><a className="text-link" href="/pricing">Pricing</a><a className="text-link" href={`${appOrigin}/login`}>Sign in<ArrowRight size={16} aria-hidden/></a></nav></footer>;
}
