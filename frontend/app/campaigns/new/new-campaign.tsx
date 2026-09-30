"use client";

import { useRouter } from "next/navigation";
import { BriefForm } from "@/components/brief-form";
import { PageHeader } from "@/components/ui/section";
import { apiRequest } from "@/lib/api";
import { emptyBriefForm } from "@/lib/brief";
import type { Campaign } from "@/lib/types";

export function NewCampaign() {
  const router = useRouter();
  return (
    <>
      <PageHeader
        title="New campaign"
        lead="Describe the video you need. Nothing is made until you start it and approve the plan."
      />
      <BriefForm
        initial={emptyBriefForm()}
        withName
        submitLabel="Create campaign"
        pendingLabel="Creating campaign"
        onSubmit={async (name, brief) => {
          const campaign = await apiRequest<Campaign>("campaigns", { method: "POST", body: { name, brief } });
          router.push(`/campaigns/${campaign.id}`);
          return campaign;
        }}
        onCancel={() => router.back()}
      />
    </>
  );
}
