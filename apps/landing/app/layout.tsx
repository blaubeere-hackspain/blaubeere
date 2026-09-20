import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import type { Metadata } from "next";
import { landingOrigin, pageMetadata } from "../../metadata";
import "./globals.css";
export const metadata: Metadata = {
  ...pageMetadata(landingOrigin, "/", "blau — Cash forecasts and what-if planning", "See your cash ahead, understand the evidence and compare what-if plans. blau gives finance teams a clearer picture of what comes next."),
  robots: { index: true, follow: true },
};
export const viewport = { themeColor: "#f5f5f2" };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
