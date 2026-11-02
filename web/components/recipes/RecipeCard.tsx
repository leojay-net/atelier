import type { Recipe } from "@/lib/types";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { RecipeRunDialog } from "./RecipeRunDialog";
import { DeleteRecipeConfirm } from "./DeleteRecipeConfirm";
import { pluralize } from "@/lib/format";

export function RecipeCard({ recipe }: { recipe: Recipe }) {
  const inputCount = Object.keys(recipe.inputs).length;

  return (
    <Card className="flex items-center justify-between gap-4 px-4 py-3.5">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate text-[14px] font-medium text-fg">{recipe.name}</p>
          <Badge>v{recipe.version}</Badge>
        </div>
        {recipe.description ? (
          <p className="truncate text-[13px] text-fg-secondary">{recipe.description}</p>
        ) : null}
        <p className="text-[12px] text-fg-tertiary">
          {pluralize(inputCount, "input")}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <RecipeRunDialog recipe={recipe} />
        <DeleteRecipeConfirm id={recipe.id} name={recipe.name} />
      </div>
    </Card>
  );
}
