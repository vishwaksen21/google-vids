import type { Metadata } from "next";
import { NewCampaign } from "./new-campaign";

export const metadata: Metadata = { title: "New campaign" };

export default function Page() {
  return <NewCampaign />;
}
