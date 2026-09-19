import { AuthLayout } from "../../components/auth-layout";
import { LoginForm } from "../../components/login-form";
import { appOrigin, pageMetadata } from "../../../metadata";

export const dynamic = "force-dynamic";
export const metadata = pageMetadata(appOrigin, "/login", "Sign in | blau", "Open your blau finance workspace or explore the demo. See your cash outlook, inspect evidence and compare what-if plans.");

export default function Login() {
  return <AuthLayout><LoginForm demo={process.env.DEMO_LOGIN === "true"}/></AuthLayout>;
}
