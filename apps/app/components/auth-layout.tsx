export function AuthLayout({ children }: { children: React.ReactNode }) {
  return <main className="auth-layout">
    <section className="auth-story" aria-label="Blaubeere">
      <div><a className="brand" href={process.env.NEXT_PUBLIC_LANDING_URL ?? "http://localhost:3102"}><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</a></div>
      <div><span className="eyebrow">A clearer view of what’s ahead</span><h1>Tomorrow’s cash.<br/>Today’s decisions.</h1><p>Understand the gap. Explore your options. Give your next decision a little more foresight.</p><div className="auth-art" aria-hidden><svg viewBox="0 0 500 180" fill="none"><path d="M0 90H500M0 45H500M0 135H500" stroke="currentColor" opacity=".12"/><path d="M0 95L70 65L130 82L210 50L270 115" stroke="currentColor" strokeWidth="3"/><path d="M270 115L340 88L410 38L500 20" stroke="currentColor" strokeWidth="3" strokeDasharray="7 6"/><path d="M270 0V180" stroke="currentColor" opacity=".25" strokeDasharray="3 5"/></svg></div></div>
      <p className="small">For the people behind the numbers.</p>
    </section>
    <section className="auth-form-area">{children}</section>
  </main>;
}
