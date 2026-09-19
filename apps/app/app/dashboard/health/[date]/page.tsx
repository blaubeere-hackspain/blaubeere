import { notFound } from "next/navigation";
import { Dashboard } from "../../../../components/dashboard";
import { validHealthDate } from "../../../../lib/health";
import { appOrigin, pageMetadata } from "../../../../../metadata";

type Props = { params: Promise<{ date: string }> };
export async function generateMetadata({ params }: Props) {
  const { date } = await params;
  return pageMetadata(appOrigin, "/dashboard/health/" + date, `Health assessment · ${date} | blau`, "Review the saved financial health rating, model explanation and source evidence for this assessment date.");
}
export default async function Page({ params }: Props) {
  const { date } = await params;
  if (!validHealthDate(date)) notFound();
  return <Dashboard scoreDate={date}/>;
}
