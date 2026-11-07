import { BookMarked } from "lucide-react";
import { api } from "@/lib/api";
import { RecipeCard } from "@/components/recipes/RecipeCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { NewRecipePanel } from "./NewRecipePanel";

// Always live data from Django, never a build-time static render.
export const dynamic = "force-dynamic";

export default async function RecipesPage() {
  const { items } = await api.listRecipes();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="animate-rise text-[30px] font-semibold leading-tight tracking-[-0.03em] text-fg">Recipes</h1>
        <p className="mt-1 text-[13px] text-fg-secondary">
          Saved pipelines you can run again on new assets.
        </p>
      </div>

      {items.length === 0 ? (
        <EmptyState
          icon={BookMarked}
          title="No recipes yet"
          description='Make something in Create, then "Save as recipe" once it succeeds.'
        />
      ) : (
        <div className="space-y-2">
          {items.map((recipe) => (
            <RecipeCard key={recipe.id} recipe={recipe} />
          ))}
        </div>
      )}

      <NewRecipePanel />
    </div>
  );
}
