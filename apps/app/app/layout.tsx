import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import type { Metadata } from "next";
import { appOrigin, pageMetadata } from "../../metadata";
import "./globals.css";
export const metadata: Metadata = {
  ...pageMetadata(appOrigin, "/", "Finance workspace | blau", "Your private cash outlook, source evidence and conditional plans, together in one finance workspace."),
  robots: { index: false, follow: false },
};
export const viewport = { themeColor: "#f5f5f2" };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
