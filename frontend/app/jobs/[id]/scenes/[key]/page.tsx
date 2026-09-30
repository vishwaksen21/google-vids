import type { Metadata } from "next";
import { SceneReview } from "./scene-review";

export const metadata: Metadata = { title: "Scene review" };

export default async function Page({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return <SceneReview sceneKey={key} />;
}
