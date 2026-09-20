import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { returnPath } from "./api";

export async function redirectSignedIn(returnTo?: string) {
  const session = (await cookies()).get("blaubeere_session");
  if (!session) return;
  const response = await fetch(`${process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8080"}/api/me`, {
    headers: { Cookie: `${session.name}=${session.value}` }, cache: "no-store", signal: AbortSignal.timeout(5000),
  }).catch(() => null);
  if (response?.ok) redirect(returnPath(returnTo ?? null));
}
