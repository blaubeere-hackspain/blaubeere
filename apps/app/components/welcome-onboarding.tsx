"use client";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ChartNoAxesCombined, CircleHelp, FileText, Wallet, X } from "lucide-react";
import { Dialog } from "./dialog";
import styles from "./welcome-onboarding.module.css";

export function welcomeProgress(value: string | null) {
  return value === "guide" || value === "done" ? value : "welcome";
}

export function WelcomeOnboarding({ scope, hasCompanies, demo = false, autoOpen = true }: { scope: string; hasCompanies: boolean; demo?: boolean; autoOpen?: boolean }) {
  const key = `blau:welcome:v1:${scope}`;
  const [progress, setProgress] = useState<ReturnType<typeof welcomeProgress>>("done");
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!autoOpen) return;
    try { setProgress(welcomeProgress(localStorage.getItem(key))); }
    catch { setProgress("welcome"); }
  }, [key, autoOpen]);
  useEffect(() => { if (progress !== "done") title.current?.focus(); }, [progress]);
  function advance(next: ReturnType<typeof welcomeProgress>) {
    setProgress(next);
    try { localStorage.setItem(key, next); } catch { /* The guide still works when browser storage is unavailable. */ }
  }
  const guide = progress === "guide";
  return <>
    <button className="icon-button" aria-label="Getting started" title="Getting started" aria-haspopup="dialog" onClick={() => advance("welcome")}><CircleHelp size={18} aria-hidden/></button>
    <Dialog open={progress !== "done"} onClose={() => advance("done")} titleId="welcome-title" className={styles.dialog}>
      <div className={styles.painting}>
        <Image src="/welcome-garden-oil.png" alt="" fill sizes="(max-width: 540px) 100vw, 512px" loading="eager"/>
        <div className={styles.brand} aria-label="blau"><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span></div>
        {guide && <button className={`icon-button ${styles.back}`} aria-label="Back" onClick={() => advance("welcome")}><ArrowLeft size={18} aria-hidden/></button>}
        <button className={`icon-button ${styles.close}`} aria-label="Close welcome" onClick={() => advance("done")}><X size={18} aria-hidden/></button>
      </div>
      <div className={styles.body}>
        <header className={styles.heading}>
          <h2 id="welcome-title" ref={title} tabIndex={-1}>{guide ? "A clearer view starts here." : "Welcome to blau"}</h2>
          <p>{guide ? "A few small steps. A little more perspective." : "A little foresight. A world of possibility."}</p>
        </header>
        {guide ? <div className={styles.details}>
          {!hasCompanies && <p className={styles.accessNote}>{demo ? "Company data isn’t available just yet. You can return to the workspace and try again." : "Your team still needs to grant access to your company. Explore the demo while you wait."}</p>}
          <ol className={styles.steps}>
            <li><span>1</span><div><h3>Choose a company</h3><p>Use the company menu in the sidebar to find your workspace.</p></div></li>
            <li><span>2</span><div><h3>See the bigger picture</h3><p>Pick a month to explore its cash movements, payments and health score.</p></div></li>
            <li><span>3</span><div><h3>Look behind the numbers</h3><p>Open a score to see the source evidence and what’s missing.</p></div></li>
          </ol>
        </div> : <div className={styles.details}>
          <div className={styles.detailHeading}>Your financial workspace</div>
          <h3>Make room for your next decision.</h3>
          <p>Bring cash, commitments and possibilities into focus. See the story behind your numbers, with the evidence always in view.</p>
          <div className={styles.capabilities} aria-label="Explore with blau"><span><Wallet aria-hidden/>Cash flow</span><span><ChartNoAxesCombined aria-hidden/>Health history</span><span><FileText aria-hidden/>Source evidence</span></div>
        </div>}
        <footer className={styles.footer}>
          {!guide ? <button className={`button ${styles.continue}`} onClick={() => advance("guide")}>Continue onboarding<ArrowRight size={16} aria-hidden/></button> : <>
            {!hasCompanies && !demo ? <a className={`button ${styles.continue}`} href="/demo" onClick={() => advance("done")}>Explore the demo<ArrowRight size={16} aria-hidden/></a> : <button className={`button ${styles.continue}`} onClick={() => advance("done")}>Open workspace<ArrowRight size={16} aria-hidden/></button>}
          </>}
          <span className={styles.progress} aria-label={`Step ${guide ? 2 : 1} of 2`}><i data-active={!guide}/><i data-active={guide}/></span>
        </footer>
      </div>
    </Dialog>
  </>;
}
