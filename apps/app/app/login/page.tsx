import { AuthLayout } from "../../components/auth-layout";
import { LoginForm } from "../../components/login-form";

export const dynamic = "force-dynamic";

export default function Login() {
  return <AuthLayout><LoginForm demo={process.env.DEMO_LOGIN === "true"}/></AuthLayout>;
}
