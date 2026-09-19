export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, credentials: "same-origin", cache: "no-store", headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(body?.error ?? "The service is unavailable. Please try again.", response.status);
  return body as T;
}
export function returnPath(value: string | null): string {
  if (!value) return "/dashboard";
  try {
    const url = new URL(value, "https://app.local");
    return url.origin === "https://app.local" && ["/connect", "/dashboard"].includes(url.pathname) ? url.pathname + url.search : "/dashboard";
  } catch { return "/dashboard"; }
}
