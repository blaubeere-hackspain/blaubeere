import { Dashboard } from "../../components/dashboard";
import { appOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(appOrigin, "/demo", "Demo workspace | blau", "Explore imported companies, published health scores, cash movements, payments and debt without signing in.");

export default function Demo() { return <Dashboard demo/>; }
