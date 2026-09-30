import type { Metadata } from "next";
import { QaReport } from "./qa-report";

export const metadata: Metadata = { title: "Validation report" };

export default function Page() {
  return <QaReport />;
}
