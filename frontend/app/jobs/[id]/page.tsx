import type { Metadata } from "next";
import { Monitor } from "./monitor";

export const metadata: Metadata = { title: "Production monitor" };

export default function Page() {
  return <Monitor />;
}
