import type { Metadata } from "next";
import { CampaignDetail } from "./campaign-detail";

export const metadata: Metadata = { title: "Campaign" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CampaignDetail id={id} />;
}
