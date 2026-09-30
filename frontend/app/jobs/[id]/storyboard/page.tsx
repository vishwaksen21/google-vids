import type { Metadata } from "next";
import { Storyboard } from "./storyboard-view";

export const metadata: Metadata = { title: "Script and storyboard" };

export default function Page() {
  return <Storyboard />;
}
