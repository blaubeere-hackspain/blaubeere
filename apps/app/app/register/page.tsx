import { AuthLayout } from "../../components/auth-layout";
import { LoginForm } from "../../components/login-form";
import { appOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(appOrigin, "/register", "Create an account | blau", "Create your blau account for cash forecasts, source evidence and what-if planning. Your team manages access to your company’s financial data.");

export default async function Register({ searchParams }: { searchParams: Promise<{ returnTo?: string | string[] }> }) {
  const { returnTo } = await searchParams;
  const destination = typeof returnTo === "string" ? returnTo : undefined;
  return <AuthLayout returnTo={destination}><LoginForm register returnTo={destination}/></AuthLayout>;
}
