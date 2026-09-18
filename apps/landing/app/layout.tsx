import type { Metadata } from "next";
export const metadata: Metadata = { title: "Blaubeere", description: "See your cash ahead. Plan with confidence." };
export default function Layout({ children }: { children: React.ReactNode }) { return <html lang="en"><body>{children}</body></html>; }
