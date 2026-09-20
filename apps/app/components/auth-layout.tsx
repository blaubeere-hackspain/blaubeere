import Image from "next/image";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { returnPath } from "../lib/api";
import garden from "../public/garden-gate-oil.png";
import logo from "../../brand/blau.svg";

export async function AuthLayout({ children, returnTo }: { children: React.ReactNode; returnTo?: string }) {
  const session = (await cookies()).get("blaubeere_session");
  if (session) {
    const response = await fetch(`${process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8080"}/api/me`, {
      headers: { Cookie: `${session.name}=${session.value}` }, cache: "no-store", signal: AbortSignal.timeout(5000),
    }).catch(() => null);
    if (response?.ok) redirect(returnPath(returnTo ?? null));
  }
  return <main className="auth-layout">
    <section className="auth-story" aria-label="blau">
      <Image className="auth-painting" src={garden} alt="An open wooden gate leading into a sunlit garden, painted in oils." draggable={false} fill sizes="(max-width: 760px) 100vw, 50vw" preload placeholder="blur"/>
      <a className="brand" href={process.env.NEXT_PUBLIC_LANDING_URL ?? "http://localhost:3102"} aria-label="blau home"><Image className="brand-logo" src={logo} alt="blau"/></a>
      <div className="auth-story-copy"><p className="auth-story-title">A little foresight.<br/><span>A world of possibility.</span></p><p>Make room for your next decision.<br/>We’ll help you see what’s ahead.</p></div>
    </section>
    <section className="auth-form-area">{children}<p className="auth-signature">For the people behind the numbers.</p></section>
  </main>;
}
