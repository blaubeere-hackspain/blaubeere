import Image from "next/image";
import garden from "../public/garden-gate-oil.png";

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return <main className="auth-layout">
    <section className="auth-story" aria-label="Blaubeere">
      <Image className="auth-painting" src={garden} alt="An open wooden gate leading into a sunlit garden, painted in oils." fill sizes="(max-width: 760px) 100vw, 50vw" preload placeholder="blur"/>
      <a className="brand" href={process.env.NEXT_PUBLIC_LANDING_URL ?? "http://localhost:3102"}><span className="brand-mark" aria-hidden><i/><i/><i/><i/></span>blaubeere</a>
      <div className="auth-story-card"><p className="auth-story-title">A little foresight.<br/><span>A world of possibility.</span></p><p>Make room for your next decision.<br/>We’ll help you see what’s ahead.</p></div>
    </section>
    <section className="auth-form-area">{children}<p className="auth-signature">For the people behind the numbers.</p></section>
  </main>;
}
