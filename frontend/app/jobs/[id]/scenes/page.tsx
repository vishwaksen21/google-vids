import type { Metadata } from "next";
import { SceneManager } from "./scene-manager";

export const metadata: Metadata = { title: "Scenes" };

export default function Page() {
  return <SceneManager />;
}
