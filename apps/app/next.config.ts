import type { NextConfig } from "next";
const config: NextConfig = {
  agentRules: false,
  async rewrites() { return [{ source: "/api/:path*", destination: `${process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8080"}/api/:path*` }]; },
  async headers() { return [{ source: "/:path*", headers: [{ key: "X-Frame-Options", value: "DENY" }, { key: "Referrer-Policy", value: "no-referrer" }, { key: "X-Content-Type-Options", value: "nosniff" }] }]; },
};
export default config;
