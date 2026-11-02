export default function Loading() {
  return (
    <div className="space-y-6">
      <div className="h-7 w-32 animate-pulse rounded-sm bg-bg-inset" />
      <div className="h-28 animate-pulse rounded-md bg-bg-inset" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="aspect-square animate-pulse rounded-md bg-bg-inset" />
        ))}
      </div>
    </div>
  );
}
