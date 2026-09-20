import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  const api = process.env.API_INTERNAL_URL;
  if (process.env.NODE_ENV !== "development" || !api?.startsWith("https://") || request.method !== "POST") return NextResponse.next();

  const local = new URL(process.env.APP_ORIGIN ?? "http://localhost:3100");
  // Authenticate the browser origin locally before forwarding to the shared backend.
  // Never trust a forwarded host, or turn arbitrary cross-site requests into trusted ones.
  if (!["localhost", "127.0.0.1", "[::1]"].includes(local.hostname)
    || request.headers.get("host") !== local.host
    || request.headers.get("origin") !== local.origin
    || request.headers.get("sec-fetch-site") === "cross-site") {
    return NextResponse.json({ error: "This request must come from the local app." }, { status: 403 });
  }
  const headers = new Headers(request.headers);
  headers.set("origin", new URL(api).origin);
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: "/api/:path*" };
