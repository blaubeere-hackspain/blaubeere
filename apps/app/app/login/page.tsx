import { AuthLayout } from "../../components/auth-layout";
import { LoginForm } from "../../components/login-form";
import { appOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(appOrigin, "/login", "Sign in | blau", "Open your blau finance workspace or explore the demo. See your cash outlook, inspect evidence and compare what-if plans.");

export default async function Login({ searchParams }: { searchParams: Promise<{ returnTo?: string | string[] }> }) {
  const { returnTo } = await searchParams;
  return <AuthLayout><LoginForm returnTo={typeof returnTo === "string" ? returnTo : undefined}/></AuthLayout>;
}
