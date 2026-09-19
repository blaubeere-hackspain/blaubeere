import "@fontsource-variable/inter";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = { title: "Blaubeere", description: "See your cash ahead. Plan with confidence." };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
