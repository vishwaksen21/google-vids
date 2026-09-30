import type { Metadata } from "next";
import { Delivery } from "./delivery-view";

export const metadata: Metadata = { title: "Export and delivery" };

export default function Page() {
  return <Delivery />;
}
