import type { Metadata } from "next";
import { PlanView } from "./plan-view";

export const metadata: Metadata = { title: "Creative plan" };

export default function Page() {
  return <PlanView />;
}
