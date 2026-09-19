import type { NextConfig } from "next";
const config: NextConfig = {
  agentRules: false,
  async rewrites() {
    const api = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8080";
    const mcp = process.env.MCP_INTERNAL_URL ?? "http://127.0.0.1:8081";
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
      { source: "/oauth/:path*", destination: `${api}/oauth/:path*` },
      { source: "/mcp", destination: `${mcp}/mcp` },
      { source: "/.well-known/oauth-protected-resource", destination: `${mcp}/.well-known/oauth-protected-resource` },
      { source: "/.well-known/oauth-protected-resource/mcp", destination: `${mcp}/.well-known/oauth-protected-resource/mcp` },
      { source: "/.well-known/:path*", destination: `${api}/.well-known/:path*` },
    ];
  },
  async headers() { return [{ source: "/:path*", headers: [{ key: "X-Frame-Options", value: "DENY" }, { key: "Referrer-Policy", value: "no-referrer" }, { key: "X-Content-Type-Options", value: "nosniff" }] }]; },
};
export default config;
