import { Dashboard } from "../../components/dashboard";
import demo from "../../lib/demo.json";
import { appOrigin, pageMetadata } from "../../../metadata";

export const metadata = pageMetadata(appOrigin, "/demo", "Demo workspace | blau", "Explore the blau cash outlook with sample data. Inspect the charts, source evidence and example plans without signing in.");

// ponytail: fixed sample plans; custom calculations stay in the authenticated workspace.
export default function Demo() { return <Dashboard demo={demo}/>; }
