import type { Metadata } from "next";
import { CharactersView } from "@/components/characters/Characters";

export const metadata: Metadata = { title: "Characters · Atelier" };

export default function CharactersPage() {
  return <CharactersView />;
}
