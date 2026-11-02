import { Spinner } from "@/components/ui/Spinner";

export default function Loading() {
  return (
    <div className="flex justify-center py-24">
      <Spinner size={22} className="text-fg-tertiary" />
    </div>
  );
}
